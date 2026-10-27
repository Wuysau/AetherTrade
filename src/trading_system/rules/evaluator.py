"""Restricted rule evaluation: typed AST in, at most one `DecisionProposal` out.

The evaluator is a pure function of (compiled rule, snapshot, rule state). It
reads no wall clock, network, account, credential or venue, and it cannot
submit, amend or cancel anything: the only tradeable output is a
`DecisionProposal` that still has to pass the normal authorization, risk and
execution path. Nothing here interprets natural language or executes strings.

Missing data never becomes zero, `False` or unbounded risk. A feature that the
snapshot does not carry (or that the snapshot declares missing) selects the
rule's own declared `missing_data` behaviour and yields no proposal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
from typing import Mapping

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import (
    ContractCodec, DecisionProposalEntry, DecisionProposalExitPlan,
    DecisionProposalRationale, DecisionProposalTarget,
)

from .graph import (
    COMPILER_REVISION,
    SUPPORTED_INPUT_REFS,
    RuleCompileError,
    RuleCompiler,
    condition_hash,
    condition_input_refs,
    graph_hash,
)
from .triggers import (
    TriggerSemanticsError,
    cooldown_active,
    evaluate_trigger,
    observation_bar,
)

_EVIDENCE_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)


class RuleEvaluationError(ValueError):
    """The inputs violate the rule/domain contract; no proposal is produced."""


@lru_cache(maxsize=1)
def _codec() -> ContractCodec:
    """Approved-schema codec, loaded once; it grants no authority."""
    return ContractCodec()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedFeatures:
    values: Mapping[str, Decimal]
    missing: tuple[str, ...]


def _price_value(price: t.Price | None, label: str) -> Decimal | None:
    if price is None:
        return None
    if type(price) is not t.Price:
        raise RuleEvaluationError(f'{label} is not a typed Price')
    return price.value


def _midpoint(left: Decimal, right: Decimal) -> Decimal:
    """Exact midpoint; precision derives from the operands, never float."""
    digits = max(len(left.as_tuple().digits), len(right.as_tuple().digits))
    exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    precision = max(28, digits + abs(exponent) + 4)
    with localcontext() as context:
        context.prec = precision
        return (left + right) / 2


def resolve_features(snapshot: t.SnapshotPacket) -> ResolvedFeatures:
    quote = snapshot.quote
    if type(quote) is not t.Quote:
        raise RuleEvaluationError('snapshot quote is not typed')
    bid = _price_value(quote.bid, 'quote.bid')
    ask = _price_value(quote.ask, 'quote.ask')
    last = _price_value(quote.last, 'quote.last')

    values: dict[str, Decimal] = {}
    if bid is not None:
        values['quote.bid'] = bid
    if ask is not None:
        values['quote.ask'] = ask
    if last is not None:
        values['quote.last'] = last
    if bid is not None and ask is not None:
        if quote.bid.currency != quote.ask.currency:
            raise RuleEvaluationError('quote mixes currencies')
        values['quote.mid'] = _midpoint(bid, ask)
        values['quote.spread'] = ask - bid

    missing = tuple(name for name in SUPPORTED_INPUT_REFS if name not in values)
    return ResolvedFeatures(values=values, missing=missing)


def _evaluate_condition(node: t.RuleCondition, values: Mapping[str, Decimal]) -> bool:
    operator = node.operator
    if operator == 'ALL':
        return all(_evaluate_condition(child, values) for child in node.children)
    if operator == 'ANY':
        return any(_evaluate_condition(child, values) for child in node.children)
    if operator == 'NOT':
        return not _evaluate_condition(node.children[0], values)
    if operator not in ('GT', 'GE', 'LT', 'LE', 'EQ'):
        raise RuleEvaluationError(f'unsupported condition operator {operator!r}')
    ref = node.input_ref
    if ref not in values:
        # Callers resolve every referenced feature before evaluating.
        raise RuleEvaluationError(f'input {ref!r} is not resolved')
    value, threshold = values[ref], node.threshold
    if operator == 'GT':
        return value > threshold
    if operator == 'GE':
        return value >= threshold
    if operator == 'LT':
        return value < threshold
    if operator == 'LE':
        return value <= threshold
    # EQ is exact decimal equality; the contract defines no tolerance/epsilon.
    return value == threshold


def _utc(value: object, label: str):
    try:
        return utc_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise RuleEvaluationError(f'{label} is not an approved UTC timestamp') from exc


def _optional_utc(value: str | None, label: str):
    return None if value is None else _utc(value, label)


def _reasons(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({reason for group in groups for reason in group}))


class RuleEvaluator:
    """Deterministic, side-effect-free rule evaluation (no order path)."""

    def __init__(self):
        self._compiler = RuleCompiler()

    def evaluate(self, rule: t.CompiledRule, snapshot: t.SnapshotPacket,
                 state: t.RuleState) -> t.RuleEvaluation:
        self._verify_inputs(rule, snapshot, state)
        graph = rule.graph
        if type(snapshot.quote) is not t.Quote:
            # Report a contract error instead of an attribute error: the quote is
            # dereferenced for timestamps before feature resolution runs.
            raise RuleEvaluationError('snapshot quote is not typed')

        decision_text = snapshot.revision_visible_at
        decision_at = _utc(decision_text, 'revision_visible_at')
        occurred_at = _utc(snapshot.quote.occurred_at, 'quote.occurred_at')
        published_at = _utc(snapshot.published_at, 'published_at')
        if occurred_at > published_at or published_at > decision_at:
            # Timezone/precision are explicit: the decision may not precede the
            # data it claims to have seen.
            raise RuleEvaluationError('snapshot timestamps are out of order')
        last_triggered_at = _optional_utc(state.last_triggered_at, 'last_triggered_at')

        carried_reasons: tuple[str, ...] = ()
        previous = state.previous_condition
        last_bar_id = state.last_bar_id
        if state.revision != graph.revision:
            # A baseline written for another revision cannot authorize an edge.
            previous, last_bar_id = None, None
            carried_reasons = ('STATE_REVISION_MISMATCH',)

        def next_state(*, previous_condition: bool | None, bar_id: str | None,
                       triggered_at: str | None) -> t.RuleState:
            return t.RuleState(revision=graph.revision,
                               previous_condition=previous_condition,
                               last_bar_id=bar_id, last_triggered_at=triggered_at)

        def result(*, action: str, reasons: tuple[str, ...],
                   proposal: t.DecisionProposal | None = None,
                   previous_condition: bool | None = None,
                   bar_id: str | None = None,
                   triggered_at: str | None = None) -> t.RuleEvaluation:
            return t.RuleEvaluation(
                proposal=proposal, action=action,
                state=next_state(previous_condition=previous_condition,
                                 bar_id=bar_id, triggered_at=triggered_at),
                reasons=_reasons(carried_reasons, reasons))

        if snapshot.scope.instrument_uid != graph.instrument_ref:
            # Never trade a different instrument than the compiled candidate.
            return result(action='REJECT', reasons=('INSTRUMENT_MISMATCH',),
                          previous_condition=previous, bar_id=last_bar_id,
                          triggered_at=state.last_triggered_at)

        coverage = snapshot.coverage
        if type(coverage) is not t.Coverage:
            raise RuleEvaluationError('snapshot coverage is not typed')
        if not (coverage.query_succeeded and coverage.pagination_complete
                and coverage.supported):
            return self._missing_policy(
                graph, result, ('COVERAGE_INCOMPLETE',),
                previous_condition=previous, bar_id=last_bar_id,
                triggered_at=state.last_triggered_at)

        resolved = resolve_features(snapshot)
        declared_missing = tuple(name for name in coverage.missing
                                 if type(name) is str and name)
        referenced = condition_input_refs(graph.condition)
        missing = tuple(name for name in referenced
                        if name not in resolved.values or name in declared_missing)
        if missing:
            return self._missing_policy(
                graph, result,
                tuple(f'MISSING_FEATURE:{name}' for name in missing),
                previous_condition=previous, bar_id=last_bar_id,
                triggered_at=state.last_triggered_at)

        bar = None
        if graph.closed_bar_only:
            try:
                bar = observation_bar(window_seconds=graph.window_seconds,
                                      occurred_at=occurred_at,
                                      decision_at=decision_at)
            except TriggerSemanticsError as exc:
                raise RuleEvaluationError('closed-bar window is inconsistent') from exc
            if not bar.closed:
                # Intrabar snapshots are not eligible; no observation is recorded.
                return result(action='HOLD',
                              reasons=(f'CLOSED_BAR_PENDING:{bar.bar_id}',),
                              previous_condition=previous, bar_id=last_bar_id,
                              triggered_at=state.last_triggered_at)
            if last_bar_id == bar.bar_id:
                return result(action='HOLD',
                              reasons=(f'BAR_ALREADY_EVALUATED:{bar.bar_id}',),
                              previous_condition=previous, bar_id=last_bar_id,
                              triggered_at=state.last_triggered_at)

        condition = _evaluate_condition(graph.condition, resolved.values)
        observed_bar = bar.bar_id if bar is not None else last_bar_id

        if not condition:
            return result(action='HOLD', reasons=('CONDITION_FALSE',),
                          previous_condition=condition, bar_id=observed_bar,
                          triggered_at=state.last_triggered_at)

        try:
            cooling = cooldown_active(cooldown_seconds=graph.cooldown_seconds,
                                      last_triggered_at=last_triggered_at,
                                      decision_at=decision_at)
            verdict = evaluate_trigger(trigger=graph.trigger,
                                       previous_condition=previous,
                                       condition=condition, cooling_down=cooling)
        except TriggerSemanticsError as exc:
            raise RuleEvaluationError('trigger timing is inconsistent') from exc
        if not verdict.fires:
            return result(action='HOLD', reasons=(verdict.reason,),
                          previous_condition=condition, bar_id=observed_bar,
                          triggered_at=state.last_triggered_at)

        side, magnitude = self._target_direction(graph)
        product = snapshot.scope.product
        if type(product) is not t.Product:
            raise RuleEvaluationError('snapshot scope product is not typed')
        if side == 'SHORT' and product is not t.Product.USD_M_PERP:
            # Cash markets cannot express a short target; refuse instead of
            # silently degrading the rule to the opposite instrument.
            return result(action='REJECT',
                          reasons=(f'SHORT_TARGET_UNSUPPORTED:{product.value}',),
                          previous_condition=condition, bar_id=observed_bar,
                          triggered_at=state.last_triggered_at)

        proposal = self._build_proposal(
            graph=graph, rule_hash=rule.hash, snapshot=snapshot,
            decision_text=decision_text, side=side, magnitude=magnitude,
            bar_id=observed_bar, previous_condition=previous,
            declared_missing=declared_missing)
        return result(action='PROPOSAL', reasons=(verdict.reason,),
                      proposal=proposal, previous_condition=condition,
                      bar_id=observed_bar, triggered_at=decision_text)

    # -- helpers -----------------------------------------------------------

    def _verify_inputs(self, rule: object, snapshot: object, state: object) -> None:
        if type(rule) is not t.CompiledRule:
            raise RuleEvaluationError('expected a compiled rule')
        if type(rule.graph) is not t.RuleGraph:
            raise RuleEvaluationError('compiled rule has no typed graph')
        if rule.compiler_revision != COMPILER_REVISION:
            raise RuleEvaluationError('compiled rule has an unknown compiler revision')
        try:
            self._compiler.compile(rule.graph)
        except RuleCompileError as exc:
            raise RuleEvaluationError('compiled rule graph is not executable') from exc
        if graph_hash(rule.graph) != rule.hash:
            raise RuleEvaluationError('compiled rule hash does not match its graph')
        if type(snapshot) is not t.SnapshotPacket:
            raise RuleEvaluationError('expected a typed SnapshotPacket')
        if type(snapshot.scope) is not t.ScopeKey:
            raise RuleEvaluationError('snapshot scope is not typed')
        if type(snapshot.ref) is not t.SnapshotRef:
            raise RuleEvaluationError('snapshot reference is not typed')
        if type(state) is not t.RuleState:
            raise RuleEvaluationError('expected a typed RuleState')
        if state.previous_condition is not None and type(state.previous_condition) is not bool:
            raise RuleEvaluationError('previous condition must be boolean or absent')
        if state.last_bar_id is not None and type(state.last_bar_id) is not str:
            raise RuleEvaluationError('last bar id must be text or absent')
        if state.last_triggered_at is not None and type(state.last_triggered_at) is not str:
            raise RuleEvaluationError('last trigger must be text or absent')
        if type(state.revision) is not int:
            raise RuleEvaluationError('rule state revision must be an integer')

    @staticmethod
    def _missing_policy(graph: t.RuleGraph, result, reasons: tuple[str, ...], *,
                        previous_condition: bool | None, bar_id: str | None,
                        triggered_at: str | None) -> t.RuleEvaluation:
        action = 'REJECT' if graph.missing_data == 'REJECT' else 'HOLD'
        return result(action=action, reasons=reasons,
                      previous_condition=previous_condition, bar_id=bar_id,
                      triggered_at=triggered_at)

    @staticmethod
    def _target_direction(graph: t.RuleGraph) -> tuple[str, Decimal]:
        """Signed target value encodes direction; the proposal uses side+magnitude."""
        value = graph.target_value
        if value == 0:
            return 'FLAT', Decimal('0')
        if value > 0:
            return 'LONG', value
        return 'SHORT', -value

    @staticmethod
    def _build_proposal(*, graph: t.RuleGraph, rule_hash: str,
                        snapshot: t.SnapshotPacket, decision_text: str,
                        side: str, magnitude: Decimal, bar_id: str | None,
                        previous_condition: bool | None,
                        declared_missing: tuple[str, ...]) -> t.DecisionProposal:
        snapshot_payload = canonical_hash(domain_payload(snapshot))
        proposal_id = 'rule-' + canonical_hash({
            'rule_hash': rule_hash,
            'rule_id': graph.rule_id,
            'revision': graph.revision,
            'snapshot_hash': snapshot_payload,
            'decision_at': decision_text,
            'side': side,
            'sizing': graph.sizing,
            'magnitude': format(magnitude, 'f'),
            'bar_id': bar_id or '',
            'previous_condition': previous_condition,
        })[:32]

        reference = snapshot.ref
        evidence = (reference.id if type(reference.id) is str
                    and _EVIDENCE_ID.fullmatch(reference.id) else snapshot_payload)
        counter_evidence = tuple(f'declared-missing:{name}'[:1000]
                                 for name in sorted(declared_missing)[:20])
        proposal = t.DecisionProposal(
            contract_version='2026-10-20',
            proposal_id=proposal_id,
            instrument_ref=graph.instrument_ref,
            action='SET_TARGET',
            target=DecisionProposalTarget(side=side, sizing=graph.sizing,
                                            value=format(magnitude, 'f')),
            entry=DecisionProposalEntry(order_preference='MARKET',
                                          limit_price=None,
                                          maximum_entry_slippage_bps='0',
                                          entry_deadline=decision_text,
                                          allow_partial=False),
            exit_plan=DecisionProposalExitPlan(
                stop_price=None, stop_trigger='LAST', review_due_at=decision_text,
                # The approved graph carries no holding field; its explicit
                # window is the only horizon, and it is never unbounded.
                maximum_holding_seconds=max(1, graph.window_seconds),
                thesis_invalidation=(
                    f'rule {graph.rule_id} revision {graph.revision}: condition '
                    f'{condition_hash(graph.condition)[:16]} no longer holds')),
            evidence_refs=(evidence,),
            rationale=DecisionProposalRationale(
                summary=(f'rule {graph.rule_id} revision {graph.revision} '
                         f'{graph.trigger} trigger at {decision_text} on snapshot '
                         f'{evidence}'),
                counter_evidence=counter_evidence, missing_information=()),
            valid_until=decision_text,
        )
        try:
            _codec().encode(proposal)
        except Exception as exc:
            raise RuleEvaluationError(
                'rule output violates the approved decision-proposal contract') from exc
        return proposal
