from dataclasses import replace
from trading_system.runtime.demo_memory import DemoMemoryProjector
from test_management import buy
from test_lifecycle import repo, scope


def test_demo_projection_is_idempotent_scoped_and_not_accounting_authority(repo, scope):
    buy(repo, scope)
    projector = DemoMemoryProjector(repo)
    first = projector.project(scope)
    assert first['projected'] > 0
    assert projector.project(scope)['projected'] == 0
    context = projector.context(scope, snapshot_hash='a'*64)
    assert context['schema_revision'] == 'demo-strategy-memory-v1'
    assert context['scope']['environment'] == 'BINANCE_DEMO'
    assert context['position']['BTC'] == '0.00999'
    assert any(item['kind'] == 'FILL_ACCOUNTED' for item in context['events'])
    other = replace(scope, strategy_id='other')
    projector.project(other)
    assert not any(item['kind'] == 'FILL_ACCOUNTED' for item in projector.context(other, snapshot_hash='b'*64)['events'])
    assert repo.asset_deltas(scope)['BTC'].is_finite()
