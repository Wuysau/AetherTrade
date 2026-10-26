"""Deterministic rule-graph checks for IP-20; offline unit inputs only.

Every assertion here drives the production compiler/evaluator with synthetic
data. Passing these tests would be unit contract evidence only: no venue,
account, native kernel or acceptance scenario (T01-T66) is executed or
registered, and nothing here enables LIVE.
"""
from dataclasses import fields, replace

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.wire import ABSENT
from trading_system.rules.evaluator import RuleEvaluationError, RuleEvaluator
from trading_system.rules.graph import (
    COMPILER_REVISION,
    RuleCompileError,
    graph_hash,
)
from unit.support_ip_20 import PERP_INSTRUMENT, RuleCase, rule_case  # noqa: F401


def test_edge_and_level_trigger(rule_case):
    case = rule_case
    edge = case.compile(trigger='EDGE')
    up = case.snapshot(occurred_offset_s=10, visible_offset_s=12)

    # No baseline observation: an edge cannot be asserted from a first sample.
    first = case.evaluate(edge, up, case.state())
    assert first.action == 'HOLD'
    assert first.proposal is None
    assert 'EDGE_NO_PRIOR_OBSERVATION' in first.reasons
    assert first.state.previous_condition is True
    assert first.state.last_triggered_at is None

    # Same true observation: no new edge.
    second = case.evaluate(edge, up, first.state)
    assert second.action == 'HOLD'
    assert second.proposal is None
    assert second.state.previous_condition is True

    # Falling below the threshold records the false observation.
    down = case.snapshot(occurred_offset_s=20, visible_offset_s=22, last='63999.00')
    third = case.evaluate(edge, down, second.state)
    assert third.action == 'HOLD'
    assert third.state.previous_condition is False

    # Rising edge fires exactly one proposal and stores the decision instant.
    rising = case.snapshot(occurred_offset_s=30, visible_offset_s=32)
    fourth = case.evaluate(edge, rising, third.state)
    assert fourth.action == 'PROPOSAL'
    assert isinstance(fourth.proposal, t.DecisionProposal)
    assert fourth.proposal.action == 'SET_TARGET'
    assert fourth.proposal.target.side == 'LONG'
    assert fourth.proposal.target.sizing == 'BASE_QUANTITY'
    assert fourth.proposal.target.value == '0.001'
    assert fourth.state.last_triggered_at == rising.revision_visible_at

    # Replaying the same snapshot on the fired state does not repeat the edge.
    repeated = case.evaluate(edge, rising, fourth.state)
    assert repeated.action == 'HOLD'
    assert repeated.proposal is None

    # Level (continuous) trigger with an explicit cooldown.
    level = case.compile(trigger='CONTINUOUS', cooldown_seconds=60)
    l1 = case.evaluate(level, case.snapshot(occurred_offset_s=40, visible_offset_s=42),
                       case.state())
    assert l1.action == 'PROPOSAL'
    l2 = case.evaluate(level, case.snapshot(occurred_offset_s=50, visible_offset_s=52),
                       l1.state)
    assert l2.action == 'HOLD'
    assert l2.proposal is None
    assert 'COOLDOWN_ACTIVE' in l2.reasons
    l3 = case.evaluate(level, case.snapshot(occurred_offset_s=110, visible_offset_s=112),
                       l2.state)
    assert l3.action == 'PROPOSAL'

    # Closed-bar-only: an intrabar snapshot is ineligible and records nothing.
    closed = case.compile(trigger='CONTINUOUS', closed_bar_only=True, window_seconds=60)
    intrabar = case.snapshot(occurred_offset_s=30, visible_offset_s=35)
    pending = case.evaluate(closed, intrabar, case.state())
    assert pending.action == 'HOLD'
    assert pending.proposal is None
    assert any(reason.startswith('CLOSED_BAR_PENDING:') for reason in pending.reasons)
    assert pending.state.previous_condition is None
    assert pending.state.last_bar_id is None

    # Once the window closed, the same observation is eligible and fires once.
    after_close = case.snapshot(occurred_offset_s=30, visible_offset_s=65)
    fired = case.evaluate(closed, after_close, pending.state)
    assert fired.action == 'PROPOSAL'
    assert fired.state.last_bar_id is not None

    # A later quote inside the already evaluated bar is not re-triggered.
    same_bar = case.snapshot(occurred_offset_s=45, visible_offset_s=70)
    again = case.evaluate(closed, same_bar, fired.state)
    assert again.action == 'HOLD'
    assert again.proposal is None
    assert any(reason.startswith('BAR_ALREADY_EVALUATED:')
               for reason in again.reasons)

    # Intrabar rules (closed_bar_only=False) do evaluate the same snapshot.
    intrabar_rule = case.compile(trigger='CONTINUOUS', closed_bar_only=False)
    assert case.evaluate(intrabar_rule, intrabar, case.state()).action == 'PROPOSAL'


def test_missing_feature_policy(rule_case):
    case = rule_case
    state = case.state(previous_condition=False)

    # If a missing bid were treated as 0, "bid <= 0" would fire a proposal.
    zero_rule = case.compile(input_ref='quote.bid', operator='LE', threshold='0')
    missing_bid = case.snapshot(occurred_offset_s=10, visible_offset_s=12, bid=None)
    held = case.evaluate(zero_rule, missing_bid, state)
    assert held.action == 'HOLD'
    assert held.proposal is None
    assert 'MISSING_FEATURE:quote.bid' in held.reasons
    assert held.state.previous_condition is False
    assert held.state.last_triggered_at is None

    # The rule's declared policy selects REJECT instead of HOLD.
    reject_rule = case.compile(input_ref='quote.bid', operator='LE', threshold='0',
                               missing_data='REJECT')
    rejected = case.evaluate(reject_rule, missing_bid, state)
    assert rejected.action == 'REJECT'
    assert rejected.proposal is None
    assert 'MISSING_FEATURE:quote.bid' in rejected.reasons

    # A feature the snapshot explicitly declares missing is not used even if present.
    declared = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                             missing=('quote.last',))
    declared_result = case.evaluate(case.compile(), declared, state)
    assert declared_result.action == 'HOLD'
    assert declared_result.proposal is None
    assert 'MISSING_FEATURE:quote.last' in declared_result.reasons

    # Incomplete coverage is missing data, not a partial evaluation.
    incomplete = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                               pagination_complete=False)
    incomplete_result = case.evaluate(case.compile(), incomplete, state)
    assert incomplete_result.action == 'HOLD'
    assert incomplete_result.proposal is None
    assert 'COVERAGE_INCOMPLETE' in incomplete_result.reasons

    # Derived features inherit their inputs' absence (mid/spread need bid+ask).
    mid_rule = case.compile(input_ref='quote.mid', operator='GT', threshold='1000')
    mid_result = case.evaluate(mid_rule, missing_bid, state)
    assert mid_result.action == 'HOLD'
    assert mid_result.proposal is None
    assert 'MISSING_FEATURE:quote.mid' in mid_result.reasons

    # A snapshot with no price at all never yields a defaulted target.
    empty = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                          bid=None, ask=None, last=None)
    empty_result = case.evaluate(case.compile(), empty, state)
    assert empty_result.proposal is None
    assert any(reason.startswith('MISSING_FEATURE:') for reason in empty_result.reasons)


def test_deterministic_replay(rule_case):
    case = rule_case
    first = case.evaluate_same_input()
    second = case.evaluate_same_input()
    assert first.proposal_hash is not None
    assert first.proposal_hash == second.proposal_hash
    assert first == second
    assert first.proposal.proposal_id == second.proposal.proposal_id

    # A fresh fixture (no shared in-process state) reproduces the same proposal.
    other_process = RuleCase()
    assert other_process.evaluate_same_input().proposal_hash == first.proposal_hash

    # The compiled content hash is stable for identical content, and a revision
    # change produces a different identity.
    stable = case.compile()
    assert stable.hash == case.compile().hash
    assert stable.hash == graph_hash(stable.graph)
    assert stable.compiler_revision == COMPILER_REVISION
    revised = case.compile(revision=2)
    assert revised.hash != stable.hash

    snapshot = case.snapshot(occurred_offset_s=10, visible_offset_s=12)
    original = case.evaluate(case.compile(), snapshot,
                             case.state(previous_condition=False))
    bumped = case.evaluate(revised, snapshot, case.state(previous_condition=False, revision=2))
    assert original.action == 'PROPOSAL'
    assert bumped.action == 'PROPOSAL'
    assert original.proposal_hash != bumped.proposal_hash

    # Different data yields a different proposal identity for the same rule.
    other_data = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                               last='64000.16')
    third = case.evaluate(case.compile(), other_data,
                          case.state(previous_condition=False))
    assert third.proposal_hash != original.proposal_hash


def test_rule_output_over_budget_not_privileged(rule_case):
    case = rule_case
    huge = case.compile(target_value='1000000000')
    result = case.evaluate(huge, case.snapshot(occurred_offset_s=10, visible_offset_s=12),
                           case.state(previous_condition=False))
    assert result.action == 'PROPOSAL'
    proposal = result.proposal
    assert isinstance(proposal, t.DecisionProposal)
    assert proposal.target.value == '1000000000'

    # An oversized rule still gets only a proposal: no leverage, permit,
    # command, receipt or platform instruction is minted here.
    assert proposal.requested_leverage is ABSENT
    assert {'permit', 'command', 'receipt', 'engine'} & {
        field.name for field in fields(t.RuleEvaluation)} == set()
    assert {field.name for field in fields(t.RuleEvaluation)} == {
        'proposal', 'action', 'state', 'reasons'}
    assert not any(isinstance(getattr(result, name), (t.ExecutionCommand, t.TradeIntent))
                   for name in ('proposal', 'action', 'state', 'reasons'))
    assert case.engine.calls == []
    assert sorted(name for name in dir(RuleEvaluator) if not name.startswith('_')) == [
        'evaluate']

    # Execution policy stays explicit and conservative: no invented stop and a
    # bounded holding horizon equal to the declared window.
    assert proposal.exit_plan.stop_price is None
    assert proposal.exit_plan.maximum_holding_seconds == 60
    assert proposal.valid_until == proposal.entry.entry_deadline

    # Flat targets are expressed as a flat target, still without authority.
    flat = case.compile(target_value='0')
    flat_result = case.evaluate(flat, case.snapshot(occurred_offset_s=10,
                                                    visible_offset_s=12),
                                case.state(previous_condition=False))
    assert flat_result.action == 'PROPOSAL'
    assert flat_result.proposal.target.side == 'FLAT'
    assert flat_result.proposal.target.value == '0'
    assert flat_result.proposal.requested_leverage is ABSENT
    assert case.engine.calls == []


def test_compile_rejects_ambiguous_or_unsupported_graph(rule_case):
    case = rule_case

    with pytest.raises(RuleCompileError) as unsupported:
        case.compile(input_ref='closed_bar.close')
    assert any('UNSUPPORTED_INPUT:closed_bar.close' in reason
               for reason in unsupported.value.reasons)

    with pytest.raises(RuleCompileError):
        case.compile(closed_bar_only=True, window_seconds=0)
    with pytest.raises(RuleCompileError):
        case.compile(condition=t.RuleCondition(operator='ALL', input_ref=None,
                                               threshold=None, children=()))
    with pytest.raises(RuleCompileError):
        case.compile(exit_priority=())
    with pytest.raises(RuleCompileError):
        case.compile(threshold=1.5)  # a float threshold is not a typed Decimal
    with pytest.raises(RuleCompileError):
        case.compile(condition=t.RuleCondition(
            operator='NOT', input_ref=None, threshold=None,
            children=(case.condition(), case.condition())))
    with pytest.raises(RuleCompileError):
        case.compile(revision=0)


def test_scope_revision_and_timestamp_guards(rule_case):
    case = rule_case
    rule = case.compile()

    other_instrument = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                                     instrument='binance:spot:ETHUSDT')
    mismatch = case.evaluate(rule, other_instrument,
                             case.state(previous_condition=False))
    assert mismatch.action == 'REJECT'
    assert mismatch.proposal is None
    assert 'INSTRUMENT_MISMATCH' in mismatch.reasons

    # State written for another revision cannot supply an edge baseline.
    revised = case.compile(revision=2)
    stale_state = case.state(previous_condition=True, revision=1)
    stale = case.evaluate(revised, case.snapshot(occurred_offset_s=10,
                                                 visible_offset_s=12),
                          stale_state)
    assert stale.action == 'HOLD'
    assert stale.proposal is None
    assert 'STATE_REVISION_MISMATCH' in stale.reasons
    assert stale.state.previous_condition is True

    # Non-UTC timestamps are rejected instead of being reinterpreted locally.
    packet = case.snapshot(occurred_offset_s=10, visible_offset_s=12)
    with pytest.raises(RuleEvaluationError):
        case.evaluate(rule, replace(packet, revision_visible_at='2026-10-22T20:00:12+08:00'))
    with pytest.raises(RuleEvaluationError):
        case.evaluate(rule, replace(packet, published_at='2026-10-22T12:00:59+00:00'))

    # A hand-built or edited CompiledRule is refused.
    with pytest.raises(RuleEvaluationError):
        case.evaluate(replace(rule, hash='0' * 64), packet, case.state())
    with pytest.raises(RuleEvaluationError):
        case.evaluate(replace(rule, compiler_revision='other'), packet, case.state())


def test_cash_market_short_target_rejected(rule_case):
    case = rule_case
    short = case.compile(target_value='-0.5')
    spot = case.snapshot(occurred_offset_s=10, visible_offset_s=12)
    rejected = case.evaluate(short, spot, case.state(previous_condition=False))
    assert rejected.action == 'REJECT'
    assert rejected.proposal is None
    assert 'SHORT_TARGET_UNSUPPORTED:SPOT' in rejected.reasons

    perp_short = case.compile(target_value='-0.5', instrument_ref=PERP_INSTRUMENT)
    perp = case.snapshot(occurred_offset_s=10, visible_offset_s=12,
                         product=t.Product.USD_M_PERP, instrument=PERP_INSTRUMENT)
    accepted = case.evaluate(perp_short, perp, case.state(previous_condition=False))
    assert accepted.action == 'PROPOSAL'
    assert accepted.proposal.target.side == 'SHORT'
    assert accepted.proposal.target.value == '0.5'
    assert accepted.proposal.requested_leverage is ABSENT
    assert case.engine.calls == []
