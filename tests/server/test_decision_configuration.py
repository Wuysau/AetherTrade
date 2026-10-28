import pytest
from pydantic import ValidationError
from trading_system.server.validation import StrategyCreate
from trading_system.server.model_api import ProfileAnalysis
from trading_system.server.store import Store
from trading_system.server.errors import ApiError


def values(**kw):
    return dict(name='Evidence', symbol='BTCUSDT', mode='AI', budget='100', target_pct='10', **kw)


def test_new_contract_fields_are_explicit_and_legacy_default():
    assert StrategyCreate(**values()).decision_schema == 'legacy'
    parsed = StrategyCreate(**values(decision_schema='assessment_v1', decision_policy='evidence_review'))
    assert parsed.decision_policy == 'evidence_review'
    parsed = ProfileAnalysis(symbol='BTCUSDT', analysis_mode='consensus', model_profile_ids=['a', 'b'], expected_revisions={'a':1,'b':1}, acknowledge_cost=True, decision_schema='assessment_v1', decision_policy='evidence_review', task_profile=dict(decision_interval='15m',holding_period_seconds=3600,allowed_operations=['HOLD'],required=['quote','candles:15m'],optional=[],windows={'15m':32}))
    assert parsed.decision_schema == 'assessment_v1'


def test_policy_change_requires_new_authorization(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.initialize()
    store.setup('owner', 'a-long-password', '100000', 24)
    saved = store.create_strategy(values(status='ACTIVE', acknowledge_model_cost=True))
    with pytest.raises(ApiError) as exc:
        store.update_strategy(saved['id'], dict(expected_revision=1, decision_schema='assessment_v1', decision_policy='evidence_review'))
    assert exc.value.code in ('MODEL_COST_ACK_REQUIRED', 'INVALID_DECISION_POLICY', 'TASK_PROFILE_REQUIRED')
    profile = dict(decision_interval='15m', holding_period_seconds=3600, allowed_operations=['SET_TARGET','HOLD'], required=['quote','candles:15m'], optional=['news'], windows={'15m':32})
    saved = store.update_strategy(saved['id'], dict(expected_revision=1, decision_schema='assessment_v1', task_profile=profile, acknowledge_model_cost=True))
    assert saved['revision'] == 2
    assert saved['decision_schema'] == 'assessment_v1'
    store.close()


def test_evidence_policy_cannot_silently_apply_to_legacy(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.initialize()
    store.setup('owner', 'a-long-password', '100000', 24)
    with pytest.raises(ApiError) as exc:
        store.create_strategy(values(decision_policy='evidence_review'))
    assert exc.value.code == 'INVALID_DECISION_POLICY'
    store.close()

def test_manual_evidence_policy_requires_explicit_schema_and_profile():
    with pytest.raises(ValidationError):
        ProfileAnalysis(symbol='BTCUSDT', analysis_mode='consensus', model_profile_ids=['a','b'], expected_revisions={'a':1,'b':1}, acknowledge_cost=True, decision_policy='evidence_review')


def test_task_change_on_active_strategy_requires_ack(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.sqlite'}")
    store.initialize()
    store.setup('owner', 'a-long-password', '100000', 24)
    profile = dict(decision_interval='15m', holding_period_seconds=3600, allowed_operations=['SET_TARGET','HOLD'], required=['quote','candles:15m'], optional=['news'], windows={'15m':32})
    saved = store.create_strategy(values(status='ACTIVE', decision_schema='assessment_v1', task_profile=profile, acknowledge_model_cost=True))
    with pytest.raises(ApiError) as exc:
        store.update_strategy(saved['id'], dict(expected_revision=1, task_profile=dict(profile,holding_period_seconds=7200)))
    assert exc.value.code == 'MODEL_COST_ACK_REQUIRED'
    store.close()
