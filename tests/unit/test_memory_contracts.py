"""Strategy memory domain contracts: scope isolation, revisions and authority."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from trading_system.memory.contracts import (
    DecisionContext, DecisionHistoryRef, ExecutionFactRef, KnowledgeStatus, MarketThesis,
    PlanStatus, StrategyMemoryEvent, StrategyMemoryScope, StrategyPlan, StrategyState,
    ThesisStatus, ValidatedKnowledge, checkpoint_from_payload, context_from_payload,
    decision_ref_from_payload, event_from_payload, execution_fact_from_payload,
    knowledge_from_payload, state_from_payload,
)
from trading_system.memory.reducer import MemoryRevisionConflict, MemoryTransitionRejected, StrategyStateReducer


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def scope(**changes):
    value = dict(tenant_id="tenant-a", user_id="user-1", account_scope="paper-local",
                 strategy_id="strategy-1", product="SPOT", instrument="console-paper:BTCUSDT")
    value.update(changes)
    return StrategyMemoryScope(**value)


def event(kind, payload, *, revision=0, source="CONTROL", sequence=1, **changes):
    value = dict(event_id=f"event-{sequence}", scope=scope(), event_type=kind, source=source,
                 occurred_at=stamp(), recorded_at=stamp(), idempotency_key=f"key-{sequence}",
                 state_revision=revision, payload=payload, sequence=sequence)
    value.update(changes)
    return StrategyMemoryEvent(**value)


def thesis_payload(thesis_id="thesis-1"):
    return dict(thesis_id=thesis_id, statement="Breakout above resistance is supported by volume.",
                evidence_refs=("quote", "candles:1h"), invalidation_conditions=("close below support",))


def plan_payload(plan_id="plan-1", status="PROPOSED"):
    return dict(plan_id=plan_id, action="SET_TARGET", target_side="LONG", target_sizing="BASE_QUANTITY",
                target_value="0.01", evidence_refs=("quote",), status=status,
                valid_until=stamp(300), entry={"order_preference": "MARKET"},
                exit_plan={"stop_price": "90"})


def test_scope_isolation_by_account_strategy_and_product():
    assert scope().scope_id != scope(account_scope="other-account").scope_id
    assert scope().scope_id != scope(strategy_id="strategy-2").scope_id
    assert scope().scope_id != scope(product="SPOT", instrument="console-paper:ETHUSDT").scope_id
    with pytest.raises(ValueError):
        scope(environment="LIVE")


def test_scope_serialization_round_trip():
    original = scope()
    assert StrategyMemoryScope.from_payload(json.loads(json.dumps(original.as_payload()))) == original


def test_unknown_event_schema_is_rejected():
    with pytest.raises(ValueError, match="MEMORY_EVENT_SCHEMA_UNSUPPORTED"):
        event("STRATEGY_CREATED", {}, schema_revision="strategy-memory-v99")


def test_event_hash_is_deterministic_and_tamper_evident():
    fixed = stamp()
    first = event("STRATEGY_CREATED", {"strategy_version": 1}, occurred_at=fixed, recorded_at=fixed)
    second = event("STRATEGY_CREATED", {"strategy_version": 1}, occurred_at=fixed, recorded_at=fixed)
    assert first.event_hash == second.event_hash
    from dataclasses import fields
    tampered = {field.name: getattr(first, field.name) for field in fields(first)}
    tampered["event_hash"] = "0" * 64
    with pytest.raises(ValueError, match="MEMORY_EVENT_HASH_MISMATCH"):
        StrategyMemoryEvent(**tampered)


def test_missing_thesis_evidence_is_rejected():
    with pytest.raises(ValueError, match="MEMORY_THESIS_EVIDENCE_REQUIRED"):
        MarketThesis(thesis_id="t", scope=scope(), statement="unsupported", evidence_refs=())


def test_model_cannot_write_authority_fields():
    with pytest.raises(ValueError, match="MODEL_MAY_NOT_WRITE_AUTHORITY_FIELDS"):
        event("THESIS_CREATED", {**thesis_payload(), "risk_limits": {"max_position_pct": "100"}},
              source="MODEL")


def test_model_cannot_accept_a_plan():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    state = reducer.apply(state, event("PLAN_PROPOSED", plan_payload(), revision=1, source="MODEL", sequence=2))
    with pytest.raises(MemoryTransitionRejected, match="MEMORY_PLAN_SOURCE_NOT_AUTHORIZED"):
        reducer.apply(state, event("PLAN_ACCEPTED", {"plan_id": "plan-1"}, revision=2,
                                   source="MODEL", sequence=3))


def test_plan_cannot_skip_proposed_to_active():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    state = reducer.apply(state, event("PLAN_PROPOSED", plan_payload(), revision=1, sequence=2))
    with pytest.raises(MemoryTransitionRejected, match="MEMORY_PLAN_TRANSITION_REJECTED"):
        reducer.apply(state, event("PLAN_ACTIVATED", {"plan_id": "plan-1"}, revision=2, sequence=3))


def test_thesis_lifecycle_and_state_round_trip():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    state = reducer.apply(state, event("THESIS_CREATED", thesis_payload(), revision=1, sequence=2))
    assert state.active_thesis.status == ThesisStatus.ACTIVE
    state = reducer.apply(state, event("THESIS_UPDATED", {"thesis_id": "thesis-1", "status": "WEAKENED",
                                                          "statement": "Volume faded."},
                                       revision=2, sequence=3))
    assert state.active_thesis.status == ThesisStatus.WEAKENED
    state = reducer.apply(state, event("THESIS_INVALIDATED", {"thesis_id": "thesis-1", "reason": "support lost"},
                                       revision=3, sequence=4))
    assert state.active_thesis_id is None
    payload = json.loads(json.dumps(_payload(state)))
    assert state_from_payload(payload).state_hash == state.state_hash


def _payload(state):
    from trading_system.contracts.canonical import domain_payload
    return domain_payload(state)


def test_plan_full_lifecycle_is_recorded():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    state = reducer.apply(state, event("PLAN_PROPOSED", plan_payload(), revision=1, sequence=2))
    state = reducer.apply(state, event("PLAN_ACCEPTED", {"plan_id": "plan-1", "authorization_ref": "auth-1"},
                                       revision=2, sequence=3))
    state = reducer.apply(state, event("PLAN_ACTIVATED", {"plan_id": "plan-1"}, revision=3, sequence=4))
    assert state.active_plan.status == PlanStatus.ACTIVE
    state = reducer.apply(state, event("PLAN_COMPLETED", {"plan_id": "plan-1"}, source="EXECUTION_LEDGER",
                                       revision=4, sequence=5))
    assert state.active_plan_id is None
    assert state.plan("plan-1").status == PlanStatus.COMPLETED


def test_manual_intervention_cancels_model_plan():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    state = reducer.apply(state, event("PLAN_PROPOSED", plan_payload(), revision=1, sequence=2))
    state = reducer.apply(state, event("PLAN_ACCEPTED", {"plan_id": "plan-1"}, revision=2, sequence=3))
    state = reducer.apply(state, event("PLAN_ACTIVATED", {"plan_id": "plan-1"}, revision=3, sequence=4))
    state = reducer.apply(state, event("MANUAL_INTERVENTION", {"action": "REDUCE", "source_id": "manual-1"},
                                       source="MANUAL", revision=4, sequence=5))
    assert state.manual_action == "REDUCE"
    assert state.plan("plan-1").status == PlanStatus.CANCEL_PENDING


def test_revision_conflict_is_explicit():
    reducer = StrategyStateReducer()
    state = reducer.apply(reducer.initial(scope()), event("STRATEGY_CREATED", {"strategy_version": 1}))
    with pytest.raises(MemoryRevisionConflict):
        reducer.apply(state, event("STRATEGY_PAUSED", {}, revision=99, sequence=2))


def test_rebuild_matches_incremental_projection():
    reducer = StrategyStateReducer()
    events = [event("STRATEGY_CREATED", {"strategy_version": 1}, revision=0, sequence=1),
              event("THESIS_CREATED", thesis_payload(), revision=1, sequence=2),
              event("PLAN_PROPOSED", plan_payload(), revision=2, source="MODEL", sequence=3)]
    state = reducer.initial(scope())
    for item in events:
        state = reducer.apply(state, item)
    rebuilt = reducer.rebuild(scope(), events)
    assert rebuilt.verified and rebuilt.state.state_hash == state.state_hash
    assert rebuilt.events_applied == 3


def test_decision_and_execution_refs_round_trip():
    decision = DecisionHistoryRef(run_id="run-1", scope=scope(), created_at=stamp(),
                                  decision_status="DECIDED", action="SET_TARGET",
                                  model_provider="openai", model_name="gpt", prompt_revision="p1",
                                  schema_revision="assessment_v1", policy_revision="1",
                                  snapshot_id="s1", snapshot_hash="h1", memory_state_revision=3,
                                  checkpoint_id="mctx:abc", thesis_id="thesis-1", plan_id="plan-1")
    assert decision_ref_from_payload(json.loads(json.dumps(_payload(decision)))) == decision
    fact = ExecutionFactRef(fact_type="FILL", source_system="EXECUTION_LEDGER", source_id="fill-1",
                            scope=scope(), occurred_at=stamp(), recorded_at=stamp(), status="FILLED",
                            order_id="order-1", fill_id="fill-1", plan_id="plan-1")
    assert execution_fact_from_payload(json.loads(json.dumps(_payload(fact)))) == fact


def test_validated_knowledge_requires_provenance_and_evidence():
    with pytest.raises(ValueError, match="MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED"):
        ValidatedKnowledge(knowledge_id="k1", scope=scope(), statement="Small sample rule",
                           status=KnowledgeStatus.VALIDATED, sample_size=3, evidence_refs=("fill-1",),
                           validated_at=stamp(), provenance="MODEL_PROPOSAL")
    good = ValidatedKnowledge(knowledge_id="k2", scope=scope(), statement="Rule with 30 samples",
                              status=KnowledgeStatus.VALIDATED, sample_size=30, evidence_refs=("fill-1",),
                              validated_at=stamp(), provenance="DETERMINISTIC_STATISTICS")
    assert knowledge_from_payload(json.loads(json.dumps(_payload(good)))) == good


def test_context_and_checkpoint_hash_round_trip():
    from trading_system.memory.contracts import DecisionContextCheckpoint
    context = DecisionContext(run_id="run-1", scope=scope(), as_of=stamp(), state_revision=3,
                              strategy_version=1, snapshot_id="s1", snapshot_hash="h1",
                              account_revision=1, sections={"previous_plan": {"plan_id": "p1"}},
                              token_estimate=120, completeness="COMPLETE")
    checkpoint = DecisionContextCheckpoint(context=context, created_at=stamp())
    payload = json.loads(json.dumps(checkpoint.as_payload()))
    restored = checkpoint_from_payload(payload)
    assert restored.checkpoint_id == checkpoint.checkpoint_id
    assert restored.context.input_hash == context.input_hash
    assert context_from_payload(json.loads(json.dumps(_payload(context)))).context_hash == context.context_hash


def test_decimal_values_are_plain_strings():
    plan = StrategyPlan(plan_id="p", scope=scope(), thesis_id=None, action="SET_TARGET",
                        target_side="LONG", target_sizing="BASE_QUANTITY", target_value=Decimal("0.0100"),
                        evidence_refs=("quote",))
    assert plan.target_value == "0.0100"
