"""Explicit legacy-seed attestation never treats audit JSON as capital authority."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from integration.test_program_account_opening import isolated_postgres,command_database,source_database,database as opening_database,provision_uow
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json
from trading_system.server.paper_runtime import scope_for,ACCOUNT,GROUP
from trading_system.storage.uow import UnitOfWork


@pytest.fixture(scope='module')
def database(opening_database):
    import psycopg
    path=Path(__file__).resolve().parents[2]/'migrations/0020_program_legacy_opening.sql'
    if path.exists():
        with psycopg.connect(opening_database) as db:db.execute(path.read_text(encoding='utf-8'))
    return opening_database


@pytest.fixture(scope='module')
def seeded(database):
    import psycopg
    # Exact original provisioned console shape; no current-book funding facts.
    from uuid import uuid4
    from trading_system.storage.models import scope_id
    payload=dict(initial_cash='100000',source='EXPLICIT_NEW_VIRTUAL_CAPITAL')
    with psycopg.connect(database) as db:
        db.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s)',('PAPER',GROUP))
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',('PAPER',ACCOUNT,'SPOT',GROUP))
        db.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
            "VALUES(%s,'console:seed',1,%s::jsonb,%s)",(scope_id(scope_for('BTCUSDT')),canonical_json(payload),uuid4().hex))
    return payload


def owner_uow(database):
    import psycopg
    def connect():
        db=psycopg.connect(database);db.execute('SET ROLE ledger_owner');db.commit();return db
    return UnitOfWork(connect)


def evidence(seed):
    return t.EvidenceRef(id='explicit-legacy-provision-review',hash=canonical_hash(seed),environment=t.Environment.PAPER,
        source='INDEPENDENT_PROVISIONER_REVIEW_OF_ORIGINAL_INITIALIZATION',visible_at='2026-10-28T10:00:00Z')


def test_ordinary_seed_alone_and_foreign_account_never_supply_opening(database,seeded):
    from trading_system.execution.program_legacy_opening import read_program_legacy_opening
    from integration.test_program_financial_admission import real_case
    uow=owner_uow(database)
    with uow.transaction(scope_for('ETHUSDT')) as tx:assert read_program_legacy_opening(tx) is None
    c=real_case(database)
    with c.service.uow.transaction(c.scope) as tx:assert read_program_legacy_opening(tx) is None


def test_owner_cannot_approve_its_seed_and_explicit_provisioner_attests_exact_original(database,seeded):
    import psycopg
    from trading_system.execution.program_legacy_opening import ProgramLegacyOpeningService,read_program_legacy_opening
    from trading_system.execution.program_account_cash import read_program_account_cash
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ProgramLegacyOpeningService(owner_uow(database),scope_for('BTCUSDT')).attest(canonical_hash(seeded),evidence=evidence(seeded))
    service=ProgramLegacyOpeningService(provision_uow(database),scope_for('BTCUSDT'))
    proof=service.attest(canonical_hash(seeded),evidence=evidence(seeded))
    assert service.attest(canonical_hash(seeded),evidence=evidence(seeded))==proof
    with owner_uow(database).transaction(scope_for('ETHUSDT')) as tx:
        actual=read_program_legacy_opening(tx)
        assert actual==proof and actual.balances==(t.Money(amount=D('100000'),currency='USDT'),)
        frame=read_program_account_cash(tx,read_opening=read_program_legacy_opening)
        assert frame.balances==actual.balances and not frame.native_identities
    from trading_system.storage.models import MissingEvidence
    with pytest.raises(MissingEvidence,match='LEGACY_SEED'):
        service.attest('f'*64,evidence=evidence(seeded))


def test_owner_cannot_mint_a_new_initialization_audit_or_direct_protected_opening(database,seeded):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner_uow(database).transaction(scope_for('BTCUSDT')) as tx:
            tx.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
                "VALUES('owner-forged','console:seed',1,%s::jsonb,%s)",(canonical_json(seeded),tx.transaction_id))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with owner_uow(database).transaction(scope_for('BTCUSDT')) as tx:
            tx.execute('INSERT INTO ledger.program_legacy_account_openings(account_key,payload,payload_hash,transaction_id) '
                "VALUES('forged','{}','forged',%s)",(tx.transaction_id,))


def test_original_zero_base_console_book_reconciles_once_to_the_full_account_vector(database,seeded):
    import psycopg
    from trading_system.execution.program_legacy_opening import read_program_local_account_opening
    from trading_system.execution.program_book_cash import bind_program_book_cash,reconcile_program_book_cash,require_current_program_book_cash
    from trading_system.ownership.registry import OwnershipRegistry
    from trading_system.storage.models import MissingEvidence
    scope=scope_for('ETHUSDT');uow=owner_uow(database)
    with psycopg.connect(database) as db:
        db.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,1,1)',
            ('PAPER',ACCOUNT,'SPOT',scope.instrument_uid,'BOTH','console-paper-owner'))
    coverage=t.Coverage(scope=scope,query_succeeded=True,pagination_complete=True,supported=True,missing=())
    initial=t.BookSnapshot(scope=scope,positions=(),active_orders=(),reservations=(),
        balances=(t.Money(currency='USDT',amount=D('100000')),t.Money(currency='ETH',amount=D(0))),
        sequence=1,basis_fingerprint=canonical_hash(dict(legacy_book='zero-ETH')),coverage=coverage,evidence=(evidence(seeded),))
    with uow.transaction(scope) as tx:tx.projections.save_book(initial,0)
    position=t.ManagedPosition(position_id='legacy-zero-ETH',scope=scope,actual_quantity=t.Quantity(unit='BASE',value=D(0)),
        owner_strategy_id='console-paper-owner',owner_book_id='legacy-ETH-book',ownership_epoch=1,cost_evidence=coverage,
        realized_pnl=None,unrealized_valuation=None,active_exit_plan_id=None,risk_locks=())
    OwnershipRegistry(uow).register(position,1)
    with uow.transaction(scope) as tx:
        with pytest.raises(MissingEvidence,match='BOOK_CASH_VECTOR_MISMATCH'):
            bind_program_book_cash(tx,initial,read_opening=read_program_local_account_opening)
        reconciled=reconcile_program_book_cash(tx,read_opening=read_program_local_account_opening)
        assert reconciled.sequence==2 and reconciled.balances==(t.Money(currency='USDT',amount=D('100000')),)
        assert reconciled.positions==()
        assert require_current_program_book_cash(tx,reconciled,read_opening=read_program_local_account_opening).balances==reconciled.balances
    with uow.transaction(scope) as tx:
        assert reconcile_program_book_cash(tx,read_opening=read_program_local_account_opening)==reconciled


def test_duplicate_seed_is_not_ignored_by_protected_reader(database,seeded):
    import psycopg
    from trading_system.execution.program_legacy_opening import read_program_legacy_opening
    from trading_system.storage.models import MissingEvidence
    from uuid import uuid4
    # Deliberate admin corruption in this isolated database; owner has no grant.
    with psycopg.connect(database) as db:
        db.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
            "VALUES(%s,'console:seed',1,%s::jsonb,%s)",('foreign-seed',canonical_json(seeded),uuid4().hex))
    with owner_uow(database).transaction(scope_for('BTCUSDT')) as tx:
        with pytest.raises(MissingEvidence,match='AMBIGUOUS_LEGACY_SEED'):
            read_program_legacy_opening(tx)
