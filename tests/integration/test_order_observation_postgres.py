"""Real read-only SQL, original archives and scope isolation; no venue IO."""
from dataclasses import replace
import pytest
from integration.test_order_programs import isolated_postgres, pre_upgrade, order_db, scenario
from trading_system.server.order_observation import OrderObservationService
from trading_system.server.errors import ApiError
from trading_system.storage.models import scope_id
from trading_system.contracts.canonical import canonical_hash


def test_real_reader_retains_uncertainty_and_never_writes(scenario):
    s = scenario
    observer = OrderObservationService(s.connect_owner, (s.scope,))
    with s.connect_admin() as conn:
        before = conn.execute('SELECT count(*) FROM ledger.transaction_observations').fetchone()[0]
    page = observer.list()
    assert page['items'][0]['program_id'] == s.program.program_id
    detail = observer.detail(s.program.program_id, scope=scope_id(s.scope))
    assert detail['execution_authorized'] is False
    assert detail['payload']['contract_version'] == '2026-10-28'
    assert detail['canonical_payload_hash'] == canonical_hash(detail['payload'])
    assert detail['records']['commands'][0]['payload']['state'] == 'MAY_HAVE_SENT'
    assert detail['execution_state'] == 'ACTIVE'
    assert detail['protection_status'] == 'UNVERIFIED_DUTY'
    assert detail['children_complete'] is None
    with s.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.transaction_observations').fetchone()[0] == before
    assert observer.detail(s.program.program_id, scope=scope_id(s.scope)) == detail


def test_real_pagination_is_bounded_stable_and_scope_cannot_be_expanded(scenario):
    s = scenario
    with s.uow.transaction(s.scope) as tx:
        from trading_system.storage.order_programs import OrderProgramRepository
        repo = OrderProgramRepository(tx)
        for i in range(22): repo.save_program(replace(s.program, program_id=f'pagination-{i:02}'))
    reader = OrderObservationService(s.connect_owner, (s.scope,))
    first = reader.list(limit=10)
    second = reader.list(limit=10, cursor=first['next_cursor'])
    assert len(first['items'])==len(second['items'])==10
    assert not {v['program_id'] for v in first['items']} & {v['program_id'] for v in second['items']}
    assert reader.list(limit=10, cursor=first['next_cursor'])==second
    with pytest.raises(ApiError): reader.list(limit=11)
    with pytest.raises(ApiError): reader.list(scope=scope_id(replace(s.scope,venue_account_id='other')))
    with pytest.raises(ApiError): reader.detail(s.program.program_id, scope='other')
    with pytest.raises(ApiError): reader.list(cursor='broken-cursor')


def test_database_itself_enforces_reader_transaction_is_read_only(scenario):
    reader = OrderObservationService(scenario.connect_owner, (scenario.scope,))
    with reader._read() as (conn, _):
        assert conn.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        with pytest.raises(Exception):
            conn.execute("INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) VALUES ('x','x',1,'{}','x')")

def test_actual_latest_reservation_projection_is_observed_beside_initial_creation(scenario):
    from decimal import Decimal
    from trading_system.contracts import types as t
    s=scenario
    with s.uow.transaction(s.scope) as tx:
        for sequence,reflected in ((1,'1'),(2,'2')):
            projection=t.ReservationProjection(reservation=s.reservation,held=s.reservation.amount,
                reflected=Decimal(reflected),release_pending=Decimal('3'),sequence=sequence)
            tx._append('audit_events','ip07:reservation:'+s.reservation.reservation_id,projection,sequence-1)
    view=OrderObservationService(s.connect_owner,(s.scope,)).detail(s.program.program_id,scope=scope_id(s.scope))
    assert view['records']['reservations'][0]['payload']['state']=='HELD'
    current=view['reservation_observations'][0]
    assert current['current_status']=='RECORDED_PROJECTION'
    assert current['projection']['sequence']==2 and current['projection']['reflected']=='2'
    assert current['projection']['release_pending']=='3'
