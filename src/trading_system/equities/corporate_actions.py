"""Atomic offline corporate-action projection; no venue or catalog writes.

State is immutable and exportable for a transactional owner to persist together
with its action journal. This in-memory book is not a durable runtime ledger.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.protection.native import digest, offline, report_errors, settled


@dataclass(frozen=True, kw_only=True)
class ActionProof:
    action_hash: str
    report_hash: str
    before_hash: str
    rule_revision: int
    after_instrument: t.InstrumentSpec
    after_position: t.ManagedPosition
    after_protection: t.ProtectionPlan
    cashflows: tuple[t.Cashflow, ...]


@dataclass(frozen=True, kw_only=True)
class AppliedAction:
    action_id: str
    action_hash: str
    adjustment: t.EquityAdjustment


@dataclass(frozen=True, kw_only=True)
class EquityState:
    instrument: t.InstrumentSpec
    position: t.ManagedPosition
    protection: t.ProtectionPlan
    tradable: bool = True
    applied: tuple[AppliedAction, ...] = ()


class EquityBook:
    def __init__(self, state: EquityState, *, clock: Callable, read_proof: Callable | None = None):
        self.state = state
        self.clock = clock
        self.read_proof = read_proof

    def apply(self, action: t.CorporateAction, evidence: t.VenueReport) -> t.EquityAdjustment:
        before = self.state
        p, plan, instrument = before.position, before.protection, before.instrument
        offline(p.scope)
        # Replay is checked before the current catalog scope: a symbol migration
        # changes that scope, while the original action identity must stay stable.
        for entry in before.applied:
            if entry.action_id == action.action_id:
                if entry.action_hash != digest(action):
                    raise ValueError('ACTION_ID_CONFLICT')
                return entry.adjustment
        if (p.scope.product != t.Product.US_EQUITY or p.scope.venue_leg != 'BOTH'
                or action.scope != p.scope or not action.action_id
                or plan.scope != p.scope or instrument.instrument_uid != p.scope.instrument_uid
                or instrument.product != t.Product.US_EQUITY):
            raise ValueError('ACTION_SCOPE_INVALID')
        if (plan.coverage_quantity != p.actual_quantity or plan.native_order_ids
                or p.active_exit_plan_id is not None or any(not settled(o) for o in evidence.orders)
                or plan.stop_price.currency != instrument.quote_currency):
            raise ValueError('ACTION_PROTECTION_RECONCILIATION_REQUIRED')
        now = self.clock()
        if (utc_timestamp(action.effective_at) > utc_timestamp(now)
                or report_errors(evidence, evidence.coverage.scope, now, 60)
                or utc_timestamp(evidence.as_of) < utc_timestamp(action.effective_at)
                or action.evidence not in evidence.evidence):
            raise ValueError('ACTION_EVIDENCE_INVALID')
        proof = self.read_proof(action, evidence, before) if self.read_proof else None
        if (not isinstance(proof, ActionProof) or proof.action_hash != digest(action)
                or proof.report_hash != digest(evidence) or proof.before_hash != digest(before)
                or proof.rule_revision != instrument.rule_revision):
            raise ValueError('ACTION_PROOF_REQUIRED')
        target = proof.after_instrument
        if (target.product != instrument.product or target.venue != instrument.venue
                or target.rule_revision <= instrument.rule_revision
                or target.quote_currency != instrument.quote_currency
                or target.settlement_currency != instrument.settlement_currency
                or target.quantity_step.unit != p.actual_quantity.unit
                or target.quantity_step.value <= 0 or target.tick_size.value <= 0
                or target.tick_size.currency != plan.stop_price.currency):
            raise ValueError('ACTION_RULES_INVALID')
        quantity, stop, flows = p.actual_quantity, plan.stop_price, ()
        with localcontext() as ctx:
            ctx.prec = 80
            if action.kind == 'SPLIT':
                ratio = action.ratio
                if not isinstance(ratio, Decimal) or not ratio.is_finite() or ratio <= 0 or action.cashflow is not None:
                    raise ValueError('SPLIT_RATIO_INVALID')
                quantity = replace(quantity, value=quantity.value * ratio)
                stop = replace(stop, value=stop.value / ratio)
                if quantity.value / ratio != p.actual_quantity.value or stop.value * ratio != plan.stop_price.value:
                    raise ValueError('INEXACT_ACTION_REQUIRES_RECONCILIATION')
            elif action.kind == 'DIVIDEND':
                flow = action.cashflow
                if (action.ratio is not None or flow is None or flow.classification != 'DIVIDEND'
                        or flow.amount.currency != instrument.settlement_currency or flow.amount.amount < 0
                        or flow.evidence not in evidence.evidence
                        or not utc_timestamp(action.effective_at) <= utc_timestamp(flow.at) <= utc_timestamp(now)):
                    raise ValueError('DIVIDEND_EVIDENCE_INVALID')
                flows = (flow,)
            elif action.kind in ('SYMBOL_CHANGE', 'DELISTING'):
                if action.ratio is not None or action.cashflow is not None:
                    raise ValueError('UNEXPECTED_ACTION_TERMS')
            else:
                raise ValueError('ACTION_KIND_UNSUPPORTED')
            if quantity.value < 0 or quantity.value % target.quantity_step.value or stop.value % target.tick_size.value:
                raise ValueError('ACTION_ROUNDING_UNRESOLVED')
        if action.kind != 'SYMBOL_CHANGE' and (target.instrument_uid != instrument.instrument_uid
                or target.native_symbol != instrument.native_symbol):
            raise ValueError('UNEXPECTED_SYMBOL_CHANGE')
        if (action.kind == 'SYMBOL_CHANGE' and target.instrument_uid == instrument.instrument_uid
                and target.native_symbol == instrument.native_symbol):
            raise ValueError('SYMBOL_CHANGE_TARGET_REQUIRED')
        scope = replace(p.scope, instrument_uid=target.instrument_uid)
        expected_position = replace(p, scope=scope, actual_quantity=quantity,
                                    cost_evidence=replace(p.cost_evidence, scope=scope))
        # Replacements require renewed protection proof; never carry native IDs
        # or a prior VERIFIED state across an economic/catalog adjustment.
        expected_plan = replace(plan, scope=scope, coverage_quantity=quantity, stop_price=stop,
            native_order_ids=(), state=t.ProtectionState.GAP, last_verified_at=None,
            coverage_gap_since=plan.coverage_gap_since or now,
            replacement_revision=plan.replacement_revision + 1)
        if (proof.after_position != expected_position or proof.after_protection != expected_plan
                or proof.cashflows != flows or evidence.coverage.scope != scope
                or [x for x in evidence.positions if x.position_id == p.position_id] != [expected_position]):
            raise ValueError('ACTION_ADJUSTMENT_UNVERIFIED')
        result = t.EquityAdjustment(action_id=action.action_id, quantity_before=p.actual_quantity,
            quantity_after=quantity, cashflows=flows, evidence=evidence.evidence)
        self.state = EquityState(instrument=target, position=expected_position,
            protection=expected_plan, tradable=before.tradable and action.kind != 'DELISTING',
            applied=before.applied + (AppliedAction(action_id=action.action_id,
                action_hash=digest(action), adjustment=result),))
        return result
