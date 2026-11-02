"""New/old immutable intents share real PostgreSQL; no generic approval/send."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from integration.support_ip_03 import isolated_postgres,LedgerScenario,ROOT
from unit.support_program_financial import financial_case
from trading_system.contracts import types as t
from trading_system.contracts.ledger_intents import LedgerIntentCodec
from trading_system.contracts.wire import ContractCodec
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.storage.models import MissingEvidence,ScopeMismatch,SequenceConflict,scope_id
from trading_system.storage.uow import UnitOfWork,IntentRepository
from trading_system.storage.uow import LedgerTx


@pytest.fixture(scope='module')
def financial_database(isolated_postgres):
    import psycopg
    with psycopg.connect(isolated_postgres) as c:
        c.execute((ROOT/'migrations/0006_proposal_intake_role.sql').read_text(encoding='utf-8'))
    return isolated_postgres


@pytest.fixture
def case(financial_database):
    import psycopg
    c=financial_case(intent_id='financial-'+uuid4().hex)
    c.connect_admin=lambda:psycopg.connect(financial_database)
    i=c.intent;s=i.scope
    with c.connect_admin() as db:
        db.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s) ON CONFLICT DO NOTHING',
            (s.environment.value,i.collateral_group_id))
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL) ON CONFLICT DO NOTHING',
            (s.environment.value,s.venue_account_id,s.product.value,i.collateral_group_id))
        db.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (s.environment.value,s.venue_account_id,s.product.value,s.instrument_uid,s.venue_leg,
            i.owner_strategy_id,i.ownership_epoch,i.risk_policy_revision))
    def connect_owner():
        db=c.connect_admin();db.execute('SET ROLE ledger_owner');db.commit();return db
    c.uow=UnitOfWork(connect_owner)
    c.reservation=t.CapitalReservation(reservation_id='reservation-'+uuid4().hex,environment=s.environment,
        collateral_group_id=i.collateral_group_id,venue_account_id=s.venue_account_id,
        owner_strategy_id=i.owner_strategy_id,intent_id=i.intent_id,asset='USDT',amount=Decimal('37.00000001'),
        reflected_amount=Decimal(0),state=t.ReservationState.HELD,created_at=i.created_at,
        expires_at=i.valid_until,command_refs=(),evidence_watermark='uncredited')
    return c


def test_versioned_intent_and_existing_reservation_restart_in_same_ledger(case):
    c=case
    with c.uow.transaction(c.intent.scope) as tx:
        tx.intents.append(c.intent)
        tx.reservations.append(c.reservation,0)
    with c.uow.transaction(c.intent.scope) as tx:
        assert tx.intents.get(c.intent.intent_id)==c.intent
        assert ReservationRepository(tx).existing(c.intent)[0].reservation==c.reservation
        assert ReservationRepository(tx).existing(c.intent)[0].held==Decimal('37.00000001')
        with pytest.raises(ValueError,match='identity reused'):
            ReservationRepository(tx).existing(replace(c.intent,venue='CHANGED'))
    with c.connect_admin() as db:
        assert db.execute('SELECT amount FROM ledger.reservation_assets WHERE reservation_id=%s',
            (c.reservation.reservation_id,)).fetchone()[0]==c.reservation.amount
        assert db.execute('SELECT count(*) FROM ledger.commands WHERE scope_id=%s',
            (scope_id(c.intent.scope),)).fetchone()[0]==0


def test_generic_record_rejects_noncurrent_owner(case):
    with pytest.raises((ScopeMismatch,MissingEvidence),match='owner|OWNER'):
        with case.uow.transaction(case.intent.scope) as tx:
            tx.intents.append(replace(case.intent,owner_strategy_id='foreign'))


def test_generic_reservation_rollback_has_no_partial_financial_rows(case):
    c=case
    with pytest.raises(OSError,match='injected'):
        with c.uow.transaction(c.intent.scope) as tx:
            tx.intents.append(c.intent);tx.reservations.append(c.reservation,0)
            raise OSError('injected after conservative hold')
    with c.uow.transaction(c.intent.scope) as tx:
        assert tx.intents.get(c.intent.intent_id) is None
        assert tx.reservations.for_intent(c.intent.intent_id)==()


@pytest.mark.parametrize('role',['ledger_decision','ledger_intake'])
def test_decision_or_intake_role_cannot_write_generic_financial_intent(case,role):
    import psycopg
    c=case
    def connect_role():
        db=c.connect_admin();db.execute('SET ROLE '+role);db.commit();return db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with UnitOfWork(connect_role).transaction(c.intent.scope) as tx:tx.intents.append(c.intent)
    with c.uow.transaction(c.intent.scope) as tx:assert tx.intents.get(c.intent.intent_id) is None


def test_same_id_cannot_change_kind_or_payload(case, financial_database):
    c=case
    with c.uow.transaction(c.intent.scope) as tx:tx.intents.append(c.intent)
    dsn=financial_database  # ConnectionInfo.dsn deliberately omits the password.
    legacy=LedgerScenario(dsn).intent
    legacy=replace(legacy,intent_id=c.intent.intent_id,venue_account_id=c.intent.scope.venue_account_id,
        collateral_group_id=c.intent.collateral_group_id,instrument_uid=c.intent.scope.instrument_uid)
    with pytest.raises((SequenceConflict,ValueError)):
        with c.uow.transaction(c.intent.scope) as tx:tx.intents.append(legacy)
    with c.uow.transaction(c.intent.scope) as tx:assert tx.intents.get(c.intent.intent_id)==c.intent


def test_generic_intent_cannot_back_legacy_command_or_permit(case, financial_database):
    c=case
    with c.uow.transaction(c.intent.scope) as tx:tx.intents.append(c.intent)
    dsn=financial_database  # Reuse the explicitly provisioned fixture connection.
    legacy=LedgerScenario(dsn)
    command=replace(legacy.command,intent_id=c.intent.intent_id,
        venue_account_id=c.intent.scope.venue_account_id,product=c.intent.scope.product.value,
        instrument_uid=c.intent.scope.instrument_uid)
    with pytest.raises(MissingEvidence,match='LEGACY_TRADE_INTENT_REQUIRED'):
        with c.uow.transaction(c.intent.scope) as tx:tx.commands.append(command,0)
    # Even an imported READY command cannot use the legacy send boundary to
    # promote a generic record. No engine or artificial permit is constructed.
    with c.uow.transaction(c.intent.scope) as tx:tx._append('commands',command.command_id,command,0)
    with pytest.raises(MissingEvidence,match='LEGACY_TRADE_INTENT_REQUIRED'):
        with c.uow.transaction(c.intent.scope) as tx:
            tx.commands.mark_may_have_sent(tx,command.command_id,'DURABLE_READY')
    with c.uow.transaction(c.intent.scope) as tx:assert tx.commands.get(command.command_id).state=='DURABLE_READY'


def test_historical_trade_intent_hash_and_unknown_recovery_remain_original(financial_database):
    s=LedgerScenario(financial_database)
    before=ContractCodec().hash(s.intent)
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
        tx.commands.mark_may_have_sent(tx,s.command.command_id,'DURABLE_READY')
    with s.uow.transaction(s.scope) as tx:
        old=tx.intents.get(s.intent.intent_id)
        assert type(old) is type(s.intent) and LedgerIntentCodec().hash(old)==before
        assert tx.commands.get(s.command.command_id).venue_client_order_id==s.command.venue_client_order_id
        assert tx.commands.get(s.command.command_id).state=='MAY_HAVE_SENT'


def test_unknown_stored_kind_or_version_is_not_silently_decoded_as_legacy(case):
    import json
    c=case;payload=LedgerIntentCodec().encode(c.intent);payload['contract_version']='future'
    with c.connect_admin() as db:
        db.execute('INSERT INTO ledger.intents(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,1,%s::jsonb,%s)',(scope_id(c.intent.scope),c.intent.intent_id,json.dumps(payload),'future-import'))
    with pytest.raises(ValueError,match='LEDGER_INTENT_KIND_OR_VERSION_UNSUPPORTED'):
        with c.uow.transaction(c.intent.scope) as tx:tx.intents.get(c.intent.intent_id)


def test_generic_hold_cannot_be_created_after_owner_epoch_changes(case):
    c=case
    with c.uow.transaction(c.intent.scope) as tx:tx.intents.append(c.intent)
    with c.connect_admin() as db:
        # Explicit uncommitted ownership fault; normal owner CAS paths remain
        # unchanged. The old intent must not create a fresh post-handoff hold.
        db.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
        s=c.intent.scope
        db.execute('UPDATE ledger.active_owners SET owner_strategy_id=%s,ownership_epoch=2 '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s',
            ('new-owner',s.environment.value,s.venue_account_id,s.product.value,s.instrument_uid,s.venue_leg))
        db.execute('SET ROLE ledger_owner')
        tx=LedgerTx(db,s,c.intent.collateral_group_id)
        with pytest.raises(ScopeMismatch,match='owner|OWNER'):
            tx.reservations.append(c.reservation,0)
        db.rollback()
