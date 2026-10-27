"""Real isolated PostgreSQL group-lock probes; synthetic PAPER evidence only."""
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from integration.support_ip_07 import ReservationCase
from integration.support_ip_08 import ip08_postgres
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ContractCodec
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.risk.locked_actions import ActionProof, LockedActionVerifier
from trading_system.risk.locks import RiskLockService
from trading_system.storage.models import MissingEvidence, decode, encode, scope_id
from trading_system.storage.uow import UnitOfWork
from unit.support_ip_06 import NOW, BEFORE, END


def ref(identity='group-review', at=NOW):
    return t.EvidenceRef(id=identity, hash='c' * 64, environment=t.Environment.PAPER,
                         source='synthetic-local-ledger-review', visible_at=at)


def eth_case(dsn, *, sell=False):
    case = ReservationCase(dsn, sell=sell)
    case.btc_scope = case.scope
    case.scope = replace(case.scope, instrument_uid='paper-binance-spot-ethusdt')
    case.ledger.scope = case.service.scope = case.scope
    c = case.case
    c.intent = replace(c.intent, instrument_uid=case.scope.instrument_uid)
    c.book = replace(c.book, scope=case.scope, coverage=replace(c.book.coverage, scope=case.scope),
        positions=tuple(replace(p, scope=case.scope, cost_evidence=replace(p.cost_evidence,
            scope=case.scope)) for p in c.book.positions))
    c.market = replace(c.market, scope=case.scope, coverage=replace(c.market.coverage, scope=case.scope))
    c.market = replace(c.market, ref=replace(c.market.ref, hash=snapshot_content_hash(c.market)))
    c.inputs = replace(c.inputs, scope=case.scope, snapshot_ref=c.market.ref,
        instrument=replace(c.inputs.instrument, instrument_uid=case.scope.instrument_uid),
        session=replace(c.inputs.session, instrument_uid=case.scope.instrument_uid),
        marks=tuple(replace(mark, scope=case.scope) for mark in c.inputs.marks))
    case.command = replace(case.command, instrument_uid=case.scope.instrument_uid)
    case.rehash_command()
    with case.ledger.connect_admin() as conn:
        conn.execute('INSERT INTO ledger.active_owners VALUES (%s,%s,%s,%s,%s,%s,1,1)',
            ('PAPER', case.scope.venue_account_id, str(case.scope.product), case.scope.instrument_uid,
             case.scope.venue_leg, case.owner))
    case.save_book(c.book)
    return case


def local_action_proof(case):
    def read(tx, command, intent, book):
        # The test reads current typed positions/balances from the real local
        # ledger. It makes no claim about real venue evidence or native writes.
        stored = tx.projections.book(case.scope)
        assert stored.positions == book.positions and stored.active_orders == book.active_orders
        assert stored.balances == book.balances and stored.evidence == book.evidence
        report = t.VenueReport(coverage=stored.coverage, positions=stored.positions,
            orders=stored.active_orders, balances=stored.balances, fills=(),
            evidence=stored.evidence, next_cursor=None, as_of=NOW)
        from trading_system.execution.commands import command_hash
        return ActionProof(transaction_id=tx.transaction_id, request_hash=command_hash(command),
            intent_hash=canonical_hash(domain_payload(intent)), book_hash=canonical_hash(domain_payload(book)),
            report=report, instrument=case.case.inputs.instrument, orders=(), valid_until=END)
    return read


def raise_daily(case):
    return RiskLockService(case.ledger.uow, clock=lambda: NOW).raise_lock(
        case.btc_scope, 'DAILY_LOSS', ref('daily-loss'))


def test_btc_daily_lock_blocks_eth_new_risk_same_authoritative_group(ip08_postgres):
    case = eth_case(ip08_postgres)
    lock = raise_daily(case)
    service = RiskLockService(case.ledger.uow, clock=lambda: NOW)
    assert service.active(case.scope) == (lock,)
    assert lock.scope == case.btc_scope  # original identity, no ETH alias
    assert 'NEW_RISK' not in service.allowed_actions(case.scope)
    outcome = case.reserve()
    assert not outcome.accepted
    assert case.count('commands') == case.count('intents') == case.count('reservations') == 0


def test_btc_daily_lock_allows_proven_eth_reduction_and_other_group_new_risk(ip08_postgres):
    case = eth_case(ip08_postgres, sell=True)
    raise_daily(case)
    case.service.load_action_proof = local_action_proof(case)
    outcome = case.reserve()
    assert outcome.accepted, outcome.rejection_codes
    assert [(p.reservation.asset, p.held) for p in outcome.reservations] == [('ASSET', 2)]
    other = ReservationCase(ip08_postgres)
    assert other.group != case.group
    assert 'NEW_RISK' in RiskLockService(other.ledger.uow).allowed_actions(other.scope)
    assert other.reserve().accepted


def test_instrument_protection_gap_does_not_globalize_to_eth(ip08_postgres):
    case = eth_case(ip08_postgres)
    service = RiskLockService(case.ledger.uow, clock=lambda: NOW)
    service.raise_lock(case.btc_scope, 'PROTECTION_GAP', ref('btc-gap'))
    assert service.active(case.scope) == ()
    assert 'NEW_RISK' in service.allowed_actions(case.scope)
    assert case.reserve().accepted


@dataclass(frozen=True)
class DailyReview:
    lock: t.RiskLock
    authorization_id: str
    evidence: t.EvidenceRef


def resolver_uow(case):
    def connect():
        conn = case.ledger.connect_admin()
        conn.execute('SET ROLE ledger_risk_resolver')
        conn.commit()
        return conn
    return UnitOfWork(connect)


@pytest.fixture(scope='module')
def private_daily_review_archive(ip08_postgres):
    import psycopg
    # The execution owner can insert ordinary audit rows, so those rows are not
    # reviewer authority. This fixture's review archive is explicitly private
    # to the separately provisioned resolver identity.
    with psycopg.connect(ip08_postgres) as conn:
        conn.execute('CREATE TABLE ledger.test_daily_reviews '
            '(scope_id text NOT NULL,lock_id text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(scope_id,lock_id))')
        conn.execute('REVOKE ALL ON ledger.test_daily_reviews FROM PUBLIC,ledger_owner,ledger_control,ledger_decision')
        conn.execute('GRANT SELECT,INSERT ON ledger.test_daily_reviews TO ledger_risk_resolver')
        conn.execute('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE '
            'ON ledger.test_daily_reviews FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()')


def verify_daily_review(tx, original, requirement, evidence):
    row = tx.execute('SELECT payload FROM ledger.test_daily_reviews WHERE scope_id=%s AND lock_id=%s',
                     (scope_id(original.scope), original.lock_id)).fetchone()
    review = None if row is None else decode(DailyReview, row[0])
    return (requirement == 'DAILY_LOSS_REVIEW' and review is not None
        and review.lock == original and review.authorization_id == evidence.authorization_id
        and review.evidence in evidence.evidence and evidence.scope == original.scope)


def test_only_source_lock_resolves_with_durable_review_and_distinct_authority(ip08_postgres, private_daily_review_archive):
    import psycopg
    case = eth_case(ip08_postgres)
    lock = raise_daily(case)
    supplied = t.ResolutionEvidence(lock_id=lock.lock_id, scope=lock.scope,
        evidence=(ref('approved-review'),), authorization_id='review-authorization')
    resolver = resolver_uow(case)
    def verify(tx, original, requirement, evidence):
        return verify_daily_review(tx, original, requirement, evidence)
    service = RiskLockService(case.ledger.uow, clock=lambda: NOW, verify=verify, resolver_uow=resolver)
    assert not service.resolve(lock.lock_id, supplied).resolved  # no ledger review
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with case.ledger.uow.transaction(lock.scope) as tx:
            tx.execute('INSERT INTO ledger.test_daily_reviews VALUES (%s,%s,%s::jsonb)',
                (scope_id(lock.scope), lock.lock_id, encode(DailyReview(lock,
                    supplied.authorization_id, supplied.evidence[0]))))
    with resolver.transaction(lock.scope) as tx:
        tx.execute('INSERT INTO ledger.test_daily_reviews VALUES (%s,%s,%s::jsonb)',
            (scope_id(lock.scope), lock.lock_id, encode(DailyReview(lock,
                supplied.authorization_id, supplied.evidence[0]))))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with case.ledger.uow.transaction(lock.scope) as tx:
            tx.execute('SELECT ledger.resolve_risk_lock(%s,%s,1,%s::jsonb,%s)',
                (scope_id(lock.scope), lock.lock_id, encode(replace(lock, state='RESOLVED')), tx.transaction_id))
    assert not RiskLockService(case.ledger.uow, clock=lambda: NOW, verify=lambda *args: True).resolve(
        lock.lock_id, supplied).resolved  # evidence alone is no resolver identity
    assert not service.resolve(lock.lock_id, replace(supplied, scope=case.scope)).resolved
    assert 'NEW_RISK' not in service.allowed_actions(case.scope)
    assert service.resolve(lock.lock_id, supplied).resolved
    assert service.active(case.scope) == ()
    assert 'NEW_RISK' in service.allowed_actions(case.scope)
    with case.ledger.connect_admin() as conn:
        rows = conn.execute('SELECT scope_id,payload FROM ledger.risk_locks WHERE record_id=%s ORDER BY sequence',
                            (lock.lock_id,)).fetchall()
    assert len(rows) == 2 and {row[0] for row in rows} == {scope_id(case.btc_scope)}
    assert rows[-1][1]['state'] == 'RESOLVED'


def test_same_group_different_account_and_product_are_bound_by_daily_loss(ip08_postgres):
    source = eth_case(ip08_postgres)
    lock = raise_daily(source)
    target = ReservationCase(ip08_postgres, group=source.group, product=t.Product.USD_M_PERP)
    assert target.scope.venue_account_id != source.scope.venue_account_id
    service = RiskLockService(target.ledger.uow)
    assert service.active(target.scope) == (lock,)
    assert 'NEW_RISK' not in service.allowed_actions(target.scope)
    assert not target.reserve().accepted


def test_applicability_reacquires_same_group_mutex(ip08_postgres):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    case = eth_case(ip08_postgres)
    began = Event()
    def source_connection():
        conn = case.ledger.connect_owner()
        conn.execute("SET application_name='group-risk-lock-waiter'")
        conn.commit()
        began.set()
        return conn
    source = RiskLockService(UnitOfWork(source_connection), clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with case.ledger.uow.transaction(case.scope) as tx:
            assert tx.risk_locks.applicable(case.scope) == ()
            future = pool.submit(source.raise_lock, case.btc_scope, 'DAILY_LOSS', ref('concurrent-loss'))
            assert began.wait(5)
            waiting = False
            for _ in range(100):
                with case.ledger.connect_admin() as conn:
                    waiting = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE "
                        "application_name='group-risk-lock-waiter' AND wait_event_type='Lock')").fetchone()[0]
                if waiting:
                    break
                time.sleep(0.01)
            assert waiting and not future.done()
        future.result(timeout=5)
    assert 'NEW_RISK' not in RiskLockService(case.ledger.uow).allowed_actions(case.scope)


@pytest.mark.parametrize('fault', ['scope_identity', 'record_identity', 'history'])
def test_malformed_local_lock_is_not_treated_as_unlocked(ip08_postgres, fault):
    case = eth_case(ip08_postgres)
    lock = t.RiskLock(lock_id='corrupt-' + uuid4().hex, scope=case.scope, cause='MODEL_HEALTH',
        state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
        required_evidence=('VALID_MODEL_OUTPUT',), raised_at=NOW)
    record_id = lock.lock_id
    if fault == 'scope_identity':
        lock = replace(lock, scope=case.btc_scope)
    if fault == 'record_identity':
        record_id += '-mismatch'
    with case.ledger.connect_admin() as conn:
        conn.execute('INSERT INTO ledger.risk_locks VALUES (%s,%s,1,%s::jsonb,%s,clock_timestamp())',
            (scope_id(case.scope), record_id, encode(lock), 'intentional-malformed-fixture'))
        if fault == 'history':
            conn.execute('INSERT INTO ledger.risk_locks VALUES (%s,%s,2,%s::jsonb,%s,clock_timestamp())',
                (scope_id(case.scope), record_id, encode(lock), 'invalid-second-active'))
    with pytest.raises(MissingEvidence):
        RiskLockService(case.ledger.uow).active(case.scope)
    assert not case.reserve().accepted
    assert case.count('commands') == case.count('intents') == case.count('reservations') == 0


def test_shared_daily_lock_allows_bounded_protection_but_local_exit_only_intersects(ip08_postgres):
    from unit.support_ip_06 import price, quantity
    case = eth_case(ip08_postgres, sell=True)
    raise_daily(case)
    protection = t.ProtectionPlan(plan_id='protection', mode='NATIVE', scope=case.scope,
        coverage_quantity=quantity('1', 'BASE'), stop_price=price('90'), stop_trigger='LAST',
        native_order_ids=(), monitor_id=None, state=t.ProtectionState.GAP,
        last_verified_at=None, coverage_gap_since=BEFORE, replacement_revision=0, on_gap_policy='EXIT')
    command = replace(case.command, operation='SET_PROTECTION', canonical_payload=replace(
        case.command.canonical_payload, order_type='STOP_MARKET', quantity='1', trigger_price='90'))
    command = replace(command, payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload']))
    verifier = LockedActionVerifier(local_action_proof(case))
    with case.ledger.uow.transaction(case.scope) as tx:
        tx._append('protection_plans', protection.plan_id, protection, 0)
    with case.ledger.uow.transaction(case.scope) as tx:
        assert verifier.verify(tx, command, case.case.intent, NOW) is not None
        assert tx._latest('protection_plans', 'protection', t.ProtectionPlan).state == t.ProtectionState.GAP
    RiskLockService(case.ledger.uow, clock=lambda: NOW).raise_lock(case.scope, 'PROTECTION_GAP', ref('eth-gap'))
    with case.ledger.uow.transaction(case.scope) as tx:
        assert verifier.verify(tx, command, case.case.intent, NOW) is None
        assert verifier.verify(tx, case.command, case.case.intent, NOW) is not None


def test_unrelated_group_resolution_between_candidate_and_history_reads_does_not_deny(
        ip08_postgres, private_daily_review_archive):
    source = eth_case(ip08_postgres)
    lock = raise_daily(source)
    target = ReservationCase(ip08_postgres)
    assert target.group != source.group
    supplied = t.ResolutionEvidence(lock_id=lock.lock_id, scope=lock.scope,
        evidence=(ref('interleaved-review'),), authorization_id='interleaved-authorization')
    resolver = resolver_uow(source)
    with resolver.transaction(lock.scope) as tx:
        tx.execute('INSERT INTO ledger.test_daily_reviews VALUES (%s,%s,%s::jsonb)',
            (scope_id(lock.scope), lock.lock_id, encode(DailyReview(lock,
                supplied.authorization_id, supplied.evidence[0]))))
    source_service = RiskLockService(source.ledger.uow, clock=lambda: NOW,
        verify=verify_daily_review, resolver_uow=resolver)
    observed = []
    with target.ledger.uow.transaction(target.scope) as tx:
        execute = tx.execute
        def interleave(sql, params=()):
            result = execute(sql, params)
            if 'SELECT DISTINCT ON (l.scope_id,l.record_id)' in sql:
                # Materialize statement-one's real READ COMMITTED snapshot, then
                # commit group B's valid resolution on another role/connection.
                rows = result.fetchall()
                matches = [row for row in rows if row[1] == lock.lock_id]
                assert len(matches) == 1 and matches[0][2] == 1 and matches[0][3]['state'] == 'ACTIVE'
                observed.append((matches[0][2], matches[0][3]['state']))
                assert source_service.resolve(lock.lock_id, supplied).resolved
                from types import SimpleNamespace
                return SimpleNamespace(fetchall=lambda: rows)
            return result
        tx.execute = interleave
        assert tx.risk_locks.applicable(target.scope) == ()
    assert observed == [(1, 'ACTIVE')]
    with source.ledger.connect_admin() as conn:
        assert conn.execute('SELECT sequence,payload->>\'state\' FROM ledger.risk_locks '
            'WHERE scope_id=%s AND record_id=%s ORDER BY sequence',
            (scope_id(lock.scope), lock.lock_id)).fetchall() == [(1, 'ACTIVE'), (2, 'RESOLVED')]
    assert 'NEW_RISK' in RiskLockService(target.ledger.uow).allowed_actions(target.scope)
    assert target.reserve().accepted
