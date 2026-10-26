"""IP-22 offline logic probes; execution/review DEFERRED_TO_BATCH.

These tests do not register or qualify F03, T04/T24/T28/T47-T51/T59 or Q gates.
Memory-ledger probes cannot demonstrate native protection continuity.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import localcontext
from itertools import permutations

import pytest

from trading_system.composition.policy import CompositionCompiler, CompositionError, SYSTEM_PROTECTION
from trading_system.composition.portfolio_book import PortfolioBook
from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from unit.support_ip_09 import order
from unit.support_ip_22 import composition_case  # noqa: F401


def test_no_two_exit_owners(composition_case):
    result = composition_case.compile_with_competing_exit_owners()
    assert result.valid is False
    assert result.reason == 'OWNERSHIP_CONFLICT'


def test_contributions_not_venue_positions(composition_case):
    c = composition_case
    before = deepcopy((c.book, c.registry.rows, c.registry.owners))
    proposal = c.compose()
    assert proposal.target.value == '2'  # Two contributions are not summed to four.
    assert c.registry.read(c.scope).contributors == ('ai', 'rules')
    assert len(c.registry.owners) == 1
    assert (c.book, c.registry.rows, c.registry.owners) == before
    for contributor in ('ai', 'rules'):
        verdict = c.registry.check(c.scope, 'book', contributor, 1, 'EXIT')
        assert verdict.allowed is False
        assert verdict.reason == 'OWNERSHIP_CONFLICT'


@pytest.mark.parametrize('research,entry', [('ai', 'rules'), ('rules', 'ai'), ('user-boundary', 'ai')])
def test_ai_and_rules_same_risk_path(composition_case, research, entry):
    c = composition_case
    compiled = CompositionCompiler().compile(c.policy_for(research, entry))
    proposal = c.compose(compiled=compiled)
    intent, allowed = c.through_risk(proposal)
    assert intent.owner_strategy_id == c.owner
    assert intent.proposal_hash == ContractCodec().hash(proposal)
    assert intent.target_quantity == proposal.target.value
    assert intent.ownership_epoch == 1
    assert allowed.verdict == 'ALLOW', allowed.rejection_codes
    assert allowed.permitted_leverage == 1
    _, denied = c.through_risk(proposal, maximum_order_notional='100')
    assert denied.verdict != 'ALLOW'
    assert 'REPLAN:ORDER_NOTIONAL' in denied.rejection_codes


@pytest.mark.parametrize('mode,reason', [
    ('TRANSFER_OWNER', 'TRANSFER_FROZEN'), ('RETAIN_OWNER', 'OWNER_DRAINING'),
    ('EXIT_THEN_SWITCH', 'OWNER_DRAINING'),
])
def test_protection_survives_mode_switch(composition_case, mode, reason):
    """IP-16's system gate/plan persist; no claim of venue protection validation."""
    c = composition_case
    c.seed_position('2')
    before = c.registry.read(c.scope)
    assert c.transfer(mode).state == t.TransferState.REQUESTED
    with pytest.raises(CompositionError, match=reason):
        c.compose()
    after = c.registry.read(c.scope)
    assert after.position.active_exit_plan_id == before.position.active_exit_plan_id
    assert after.protection_responsibility == SYSTEM_PROTECTION
    assert after.position.owner_strategy_id == c.owner
    assert c.registry.check(c.scope, 'book', c.owner, 1, 'PROTECT').allowed
    assert not c.registry.check(c.scope, 'book', 'new-manager', 1, 'PROTECT').allowed


@pytest.mark.parametrize('research,entry', [('ai', 'rules'), ('rules', 'ai'), ('user-boundary', 'ai')])
def test_declared_hybrid_roles(composition_case, research, entry):
    c = composition_case
    compiled = CompositionCompiler().compile(c.policy_for(research, entry))
    signals = [c.signal('selection', value='999'), c.signal('entry', value='2')]
    proposal = c.compose(signals, compiled=compiled)
    assert proposal.target.value == '2'  # Research selects; it does not size orders.
    assert proposal.requested_leverage == c.proposal.requested_leverage
    assert compiled.ordinary_exit_owner == c.owner


def test_delivery_order_and_repeated_targets_are_deterministic(composition_case):
    c = composition_case
    outcomes = [c.compose(list(signals)) for signals in permutations(c.entries())]
    assert outcomes[0] == outcomes[1] == c.compose()
    assert outcomes[0].target == c.proposal.target
    assert CompositionCompiler().compile(replace(c.policy, stages=tuple(reversed(c.policy.stages)))) == c.compiled


@pytest.mark.parametrize('revision', [0, -1, True, '1'])
def test_invalid_policy_revision(composition_case, revision):
    with pytest.raises(CompositionError, match='INVALID_COMPOSITION_REVISION'):
        CompositionCompiler().compile(replace(composition_case.policy, revision=revision))


@pytest.mark.parametrize('role', ['ENTRY', 'EXIT'])
def test_missing_responsibility_rejected(composition_case, role):
    c = composition_case
    policy = replace(c.policy, stages=tuple(s for s in c.policy.stages if s.responsibility != role))
    with pytest.raises(CompositionError, match='MISSING_RESPONSIBILITY'):
        CompositionCompiler().compile(policy)


def test_policy_cannot_assign_system_protection_to_strategy(composition_case):
    c = composition_case
    stages = tuple(replace(s, manager_id='ai') if s.responsibility == 'PROTECTION' else s
                   for s in c.policy.stages)
    with pytest.raises(CompositionError, match='SYSTEM_PROTECTION_REQUIRED'):
        CompositionCompiler().compile(replace(c.policy, stages=stages))
    stages = tuple(replace(s, manager_id=SYSTEM_PROTECTION) if s.responsibility == 'ENTRY' else s
                   for s in c.policy.stages)
    with pytest.raises(CompositionError, match='PROTECTION_AUTHORITY_RESERVED'):
        CompositionCompiler().compile(replace(c.policy, stages=stages))


@pytest.mark.parametrize('extra,reason', [
    (t.CompositionStage(stage_id='entry', responsibility='ENTRY', manager_id='rules'), 'DUPLICATE_STAGE'),
    (t.CompositionStage(stage_id='entry-2', responsibility='ENTRY', manager_id='ai'), 'ENTRY_AUTHORITY_CONFLICT'),
    (t.CompositionStage(stage_id='bad', responsibility='EXECUTE', manager_id='ai'), 'UNKNOWN_RESPONSIBILITY'),
    (t.CompositionStage(stage_id=' ', responsibility='ENTRY', manager_id='rules'), 'INVALID_STAGE_ID'),
    (t.CompositionStage(stage_id='protect-2', responsibility='PROTECTION', manager_id=SYSTEM_PROTECTION),
     'DUPLICATE_PROTECTION_STAGE'),
])
def test_invalid_stage_graphs(composition_case, extra, reason):
    with pytest.raises(CompositionError, match=reason):
        CompositionCompiler().compile(replace(composition_case.policy,
            stages=composition_case.policy.stages + (extra,)))


def test_mutable_and_oversized_graphs_rejected(composition_case):
    c = composition_case
    for stages in (list(c.policy.stages), (), tuple(
            t.CompositionStage(stage_id=f'r{i}', responsibility='RESEARCH', manager_id='ai') for i in range(33))):
        with pytest.raises(CompositionError, match='INVALID_STAGES'):
            CompositionCompiler().compile(replace(c.policy, stages=stages))


@pytest.mark.parametrize('field,value', [('hash', 'f' * 64), ('ordinary_exit_owner', 'other')])
def test_compiled_policy_cannot_be_forged(composition_case, field, value):
    with pytest.raises(CompositionError, match='COMPOSITION_HASH_MISMATCH'):
        composition_case.compose(compiled=replace(composition_case.compiled, **{field: value}))


@pytest.mark.parametrize('signals', ['empty', 'research-only', 'entry-only', 'research-hold', 'entry-hold'])
def test_missing_or_hold_gate_produces_no_target(composition_case, signals):
    c = composition_case
    cases = {
        'empty': [], 'research-only': [c.signal('selection')], 'entry-only': [c.signal('entry')],
        'research-hold': [c.signal('selection', action='HOLD'), c.signal('entry')],
        'entry-hold': [c.signal('selection'), c.signal('entry', action='HOLD')],
    }
    assert c.compose(cases[signals]) is None


def test_no_implicit_disagreement_resolution(composition_case):
    c = composition_case
    stage = t.CompositionStage(stage_id='second-entry', responsibility='ENTRY', manager_id='rules')
    compiled = CompositionCompiler().compile(replace(c.policy, stages=c.policy.stages + (stage,)))
    signals = c.entries() + [c.signal('second-entry', value='3')]
    for sequence in (signals, list(reversed(signals))):
        with pytest.raises(CompositionError, match='SIGNAL_CONFLICT'):
            c.compose(sequence, compiled=compiled)
    assert c.compose(c.entries(), compiled=compiled) is None
    signals[-1] = c.signal('second-entry')
    assert c.compose(signals, compiled=compiled).target.value == '2'


def test_deadlines_only_shorten_and_evidence_is_retained(composition_case):
    c = composition_case
    expiry = '2026-10-22T12:00:30Z'
    research = c.signal('selection', proposal=replace(c.proposal, valid_until=expiry,
        entry=replace(c.proposal.entry, entry_deadline=expiry),
        rationale=replace(c.proposal.rationale, counter_evidence=('selection concern',))))
    result = c.compose([research, c.signal('entry')])
    assert result.valid_until == result.entry.entry_deadline == expiry
    assert set(result.evidence_refs) == set(c.proposal.evidence_refs)
    assert 'selection concern' in result.rationale.counter_evidence
    assert result.target == c.proposal.target


def test_conflicting_research_direction_rejected(composition_case):
    c = composition_case
    with pytest.raises(CompositionError, match='RESEARCH_DIRECTION_CONFLICT'):
        c.compose([c.signal('selection', side='SHORT'), c.signal('entry')])


def test_ordinary_exit_has_priority_without_research(composition_case):
    c = composition_case
    c.seed_position('2')
    exit_signal = c.signal('exit', side='FLAT', value='0')
    for signals in ([exit_signal], c.entries() + [exit_signal], [exit_signal] + c.entries()):
        result = c.compose(signals)
        assert result.target.side == 'FLAT'
        assert result.target.value == '0'
        assert 'selection:' not in result.rationale.summary


@pytest.mark.parametrize('mode', ['RETAIN_OWNER', 'EXIT_THEN_SWITCH'])
def test_old_owner_exit_remains_possible_while_draining(composition_case, mode):
    c = composition_case
    c.seed_position('2')
    c.transfer(mode)
    result = c.compose([c.signal('exit', side='FLAT', value='0')])
    assert result.target.side == 'FLAT'
    assert c.registry.read(c.scope).position.actual_quantity.value == 2  # A proposal is not a fill.


def test_exit_updates_preserve_target_and_execution(composition_case):
    c = composition_case
    c.seed_position('2')
    changed = replace(c.proposal, action='UPDATE_EXIT', exit_plan=replace(c.proposal.exit_plan, stop_price='95'))
    result = c.compose([c.signal('exit', proposal=changed)])
    assert result.action == 'UPDATE_EXIT'
    assert result.target == c.proposal.target
    assert result.entry == c.proposal.entry
    assert result.exit_plan.stop_price == '95'
    for invalid in (replace(changed, target=replace(changed.target, value='3')),
                    replace(changed, requested_leverage=2),
                    replace(changed, entry=replace(changed.entry, maximum_entry_slippage_bps='30'))):
        with pytest.raises(CompositionError, match='UPDATE_EXIT_RESPONSIBILITY_CHANGED'):
            c.compose([c.signal('exit', proposal=invalid)])


@pytest.mark.parametrize('stage,action,side,value,reason', [
    ('entry', 'SET_TARGET', 'FLAT', '0', 'STAGE_ACTION_FORBIDDEN'),
    ('selection', 'UPDATE_EXIT', 'LONG', '2', 'STAGE_ACTION_FORBIDDEN'),
    ('entry', 'SET_TARGET', 'LONG', '1', 'EXIT_AUTHORITY_REQUIRED'),
    ('entry', 'SET_TARGET', 'SHORT', '2', 'REVERSAL_REQUIRES_FLAT'),
    ('exit', 'SET_TARGET', 'LONG', '3', 'EXIT_MUST_REDUCE'),
    ('exit', 'SET_TARGET', 'LONG', '2', 'EXIT_MUST_REDUCE'),
    ('exit', 'SET_TARGET', 'SHORT', '1', 'REVERSAL_REQUIRES_FLAT'),
])
def test_role_and_effect_boundaries(composition_case, stage, action, side, value, reason):
    c = composition_case
    c.seed_position('2')
    signals = [c.signal(stage, action=action, side=side, value=value)]
    if stage == 'entry':
        signals.append(c.signal('selection', side=side if side != 'FLAT' else 'LONG'))
    with pytest.raises(CompositionError, match=reason):
        c.compose(signals)


def test_entry_cannot_replace_existing_exit_plan(composition_case):
    c = composition_case
    c.seed_position('2')
    entry = c.signal('entry', proposal=replace(c.proposal,
        exit_plan=replace(c.proposal.exit_plan, stop_price='80')))
    with pytest.raises(CompositionError, match='EXIT_AUTHORITY_REQUIRED'):
        c.compose([c.signal('selection'), entry])


def test_quote_notional_exit_is_not_assumed_to_reduce(composition_case):
    c = composition_case
    c.seed_position('2')
    proposal = replace(c.proposal, target=replace(c.proposal.target, sizing='QUOTE_NOTIONAL', value='1'))
    with pytest.raises(CompositionError, match='EXIT_BASE_QUANTITY_REQUIRED'):
        c.compose([c.signal('exit', proposal=proposal)])


def test_exit_effect_comparison_does_not_round_position(composition_case):
    c = composition_case
    c.seed_position('2.000000000000000000000000000001')
    signals = [c.signal('exit', value='2')]
    with localcontext() as context:
        context.prec = 6
        result = c.compose(signals)
    assert result.target.value == '2'


def test_equal_exit_target_cannot_round_into_a_reduction(composition_case):
    c = composition_case
    value = '2.000009'
    c.seed_position(value)
    with localcontext() as context:
        context.prec = 6
        with pytest.raises(CompositionError, match='EXIT_MUST_REDUCE'):
            c.compose([c.signal('exit', value=value)])


def test_exit_requires_position_and_protection_label_grants_no_authority(composition_case):
    c = composition_case
    with pytest.raises(CompositionError, match='NO_POSITION_TO_EXIT'):
        c.compose([c.signal('exit', side='FLAT', value='0')])
    with pytest.raises(CompositionError, match='PROTECTION_SIGNAL_FORBIDDEN'):
        c.compose([c.signal('protection', side='FLAT', value='0')])


def test_signal_without_host_attestation_rejected(composition_case):
    with pytest.raises(CompositionError, match='SIGNAL_NOT_ATTESTED'):
        composition_case.compose(attestations=())


@pytest.mark.parametrize('field,value', [('manager_id', 'intruder'), ('composition_hash', 'f' * 64),
                                       ('binding_hash', 'f' * 64), ('signal_hash', 'f' * 64)])
def test_attestation_binds_producer_policy_context_and_content(composition_case, field, value):
    c = composition_case
    signals = c.entries()
    attestations = c.attest(signals)
    with pytest.raises(CompositionError, match='SIGNAL_NOT_ATTESTED'):
        c.compose(signals, attestations=(replace(attestations[0], **{field: value}), attestations[1]))


def test_delayed_output_cannot_be_rebound_to_new_run(composition_case):
    c = composition_case
    signals = c.entries()
    old_attestations = c.attest(signals)
    with pytest.raises(CompositionError, match='SIGNAL_NOT_ATTESTED'):
        c.compose(signals, binding=replace(c.binding, decision_run_id='new-run'), attestations=old_attestations)
    changed = [signals[0], replace(signals[1], proposal=replace(signals[1].proposal,
               target=replace(signals[1].proposal.target, value='5')))]
    with pytest.raises(CompositionError, match='SIGNAL_NOT_ATTESTED'):
        c.compose(changed, attestations=old_attestations)


def test_duplicate_or_unknown_stages_rejected(composition_case):
    c = composition_case
    signals = c.entries()
    with pytest.raises(CompositionError, match='DUPLICATE_STAGE_SIGNAL'):
        c.compose(signals + [signals[1]], attestations=c.attest(signals))
    with pytest.raises(CompositionError, match='DUPLICATE_ATTESTATION'):
        c.compose(signals, attestations=c.attest(signals) + c.attest(signals)[:1])
    with pytest.raises(CompositionError, match='UNDECLARED_STAGE'):
        c.compose([replace(signals[0], stage_id='not-declared')], attestations=())


@pytest.mark.parametrize('field,value', [
    ('environment', t.Environment.LIVE), ('venue_account_id', 'other-account'),
    ('product', t.Product.USD_M_PERP), ('instrument_uid', 'other-instrument'), ('venue_leg', 'SHORT'),
])
def test_full_scope_binding_forbids_implicit_hedge_legs(composition_case, field, value):
    c = composition_case
    signal = replace(c.signal(), scope=replace(c.scope, **{field: value}))
    with pytest.raises(CompositionError, match='SIGNAL_SCOPE_MISMATCH'):
        c.compose([signal])
    assert len(c.registry.owners) == 1


def test_live_and_testnet_are_not_enabled(composition_case):
    c = composition_case
    for environment in (t.Environment.LIVE, t.Environment.TESTNET):
        with pytest.raises(CompositionError, match='ENVIRONMENT_NOT_ENABLED'):
            c.compose(binding=replace(c.binding, scope=replace(c.scope, environment=environment)))


@pytest.mark.parametrize('change,reason', [
    ({'sequence': 2}, 'BOOK_DRIFT'), ({'basis_fingerprint': 'changed'}, 'BOOK_DRIFT'),
    ({'evidence': ()}, 'BOOK_INCOMPLETE'),
])
def test_book_basis_is_bound(composition_case, change, reason):
    c = composition_case
    signals = c.entries()
    with pytest.raises(CompositionError, match=reason):
        c.portfolio(signals).compose(signals, replace(c.book, **change))


def test_incomplete_or_multiple_real_positions_rejected(composition_case):
    c = composition_case
    with pytest.raises(CompositionError, match='BOOK_INCOMPLETE'):
        c.portfolio(c.entries()).compose(c.entries(), replace(c.book,
            coverage=replace(c.book.coverage, pagination_complete=False)))
    position = c.seed_position('2')
    with pytest.raises(CompositionError, match='MULTIPLE_REAL_POSITIONS'):
        c.portfolio(c.entries()).compose(c.entries(), replace(c.book,
            positions=(position, replace(position, position_id='virtual-second', owner_strategy_id='ai'))))
    with pytest.raises(CompositionError, match='OWNER_BOOK_MISMATCH'):
        c.portfolio(c.entries()).compose(c.entries(), replace(c.book, positions=()))


def test_foreign_orders_not_absorbed(composition_case):
    c = composition_case
    c.book = replace(c.book, active_orders=(order(c.scope, '1', owner='other'),))
    with pytest.raises(CompositionError, match='ORDER_OWNERSHIP_CONFLICT'):
        c.compose()


@pytest.mark.parametrize('change,reason', [
    ({'ownership_epoch': 2}, 'STALE_OWNER_EPOCH'),
    ({'ownership_epoch': True}, 'INVALID_COMPOSITION_BINDING'),
    ({'owner_book_id': 'other-book'}, 'OWNERSHIP_CONFLICT'),
    ({'owner_strategy_id': 'ai'}, 'OWNERSHIP_CONFLICT'),
])
def test_current_ip16_owner_is_authoritative(composition_case, change, reason):
    c = composition_case
    with pytest.raises(CompositionError, match=reason):
        c.compose(binding=replace(c.binding, **change))


def test_compilation_does_not_register_an_owner(composition_case):
    c = composition_case
    c.registry.rows.clear()
    c.registry.owners.clear()
    with pytest.raises(CompositionError, match='EXTERNAL_NOT_OWNED'):
        c.compose()
    assert c.registry.rows == c.registry.owners == {}


@pytest.mark.parametrize('clock_change,reason', [
    ({'clock_health': 'DEGRADED'}, 'DECISION_DEADLINE'),
    ({'monotonic_ns': 1000010000}, 'DECISION_DEADLINE'),
    ({'utc_now': '2026-10-22T12:01:00Z'}, 'COMPOSITION_EXPIRED'),
])
def test_expiration_and_monotonic_deadline(composition_case, clock_change, reason):
    c = composition_case
    c.clock = replace(c.clock, **clock_change)
    with pytest.raises(CompositionError, match=reason):
        c.compose()


def test_expired_signals_and_wrong_evidence_rejected(composition_case):
    c = composition_case
    expired = replace(c.proposal, valid_until=c.clock.utc_now,
                      entry=replace(c.proposal.entry, entry_deadline=c.clock.utc_now))
    with pytest.raises(CompositionError, match='SIGNAL_EXPIRED'):
        c.compose([c.signal(proposal=expired)])
    signal = replace(c.signal(), evidence=(replace(c.refs[0], hash='f' * 64), *c.refs[1:]))
    with pytest.raises(CompositionError, match='SIGNAL_EVIDENCE_BINDING'):
        c.compose([signal])


def test_contract_shape_remains_proposal_only(composition_case):
    payload = ContractCodec().encode(composition_case.compose())
    assert set(payload) <= {'contract_version', 'proposal_id', 'instrument_ref', 'action', 'target',
                           'requested_leverage', 'entry', 'exit_plan', 'evidence_refs', 'rationale', 'valid_until'}
    assert not {'owner_strategy_id', 'venue_account_id', 'ownership_epoch', 'orders', 'atomic'} & set(payload)


def test_oversized_combined_rationale_is_rejected_without_truncation(composition_case):
    c = composition_case
    proposal = replace(c.proposal, rationale=replace(c.proposal.rationale, summary='x' * 1500))
    with pytest.raises(CompositionError, match='INVALID_COMPOSITION_INPUT'):
        c.compose([c.signal('selection', proposal=proposal), c.signal('entry', proposal=proposal)])


def test_mutated_or_malformed_inputs_fail_closed(composition_case):
    c = composition_case
    signals = c.entries()
    with pytest.raises(CompositionError, match='INVALID_SIGNALS'):
        c.portfolio(signals).compose(tuple(signals), c.book)
    bad = replace(c.signal(), proposal=replace(c.proposal, target=replace(c.proposal.target, value='NaN')))
    with pytest.raises(CompositionError, match='INVALID_COMPOSITION_INPUT'):
        c.portfolio(signals).compose([bad], c.book)
    with pytest.raises(CompositionError, match='INVALID_ATTESTATIONS'):
        PortfolioBook(c.compiled, c.binding, c.registry, attestations=[], clock=lambda: c.clock)
