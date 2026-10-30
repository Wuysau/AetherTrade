"""Continuation metadata shares the actual financial/request transaction."""
import pytest

from integration.test_quote_current_boundary_postgres import database,isolated_postgres
from integration.test_quote_financial_kernel_postgres import quote_case
from integration.test_quote_program_admission_postgres import public
from trading_system.execution.program_commands import ProgramCommandRepository
from trading_system.risk.program_financial_admission import read_program_bundle,BUNDLE_PREFIX
from trading_system.storage.models import scope_id


def test_actual_quote_continuation_and_request_commit_with_one_physical_financial_transaction(database):
    from trading_system.risk.program_continuation import ProgramContinuationRiskV1,CONTINUATION_PREFIX
    c=quote_case(database);receipt=public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    with c.owner.transaction(c.scope) as tx:
        duty=tx._latest('audit_events',CONTINUATION_PREFIX+receipt.intent_id,ProgramContinuationRiskV1)
        assert duty is not None and duty.actual_quantity.value==0 and duty.maximum_possible_quantity.value>0
        assert duty.archive_hash==tx.intents.get(receipt.intent_id).hashes.archive
        rows=tx.execute('SELECT xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id IN (%s,%s) '
            'UNION ALL SELECT xmin::text FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',
            (scope_id(c.scope),CONTINUATION_PREFIX+receipt.intent_id,BUNDLE_PREFIX+receipt.intent_id,scope_id(c.scope))).fetchall()
        assert len(rows)==3 and len({r[0] for r in rows})==1
        assert not duty.execution_authorized and not duty.funds_release_authorized


def test_continuation_rolls_back_with_the_real_financial_outbox_failure(database,monkeypatch):
    from trading_system.risk.program_continuation import CONTINUATION_PREFIX
    c=quote_case(database);original=ProgramCommandRepository.append_creation
    def append(repo,*args,**kwargs):
        original(repo,*args,**kwargs)
        assert repo.tx.execute('SELECT count(*) FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
            (scope_id(c.scope),CONTINUATION_PREFIX+c.qrecord.intent_id)).fetchone()==(1,)
        raise OSError('fixture stops after actual continuation and request append')
    monkeypatch.setattr(ProgramCommandRepository,'append_creation',append)
    with pytest.raises(OSError):public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None
        assert tx.execute('SELECT count(*) FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
            (scope_id(c.scope),CONTINUATION_PREFIX+c.qrecord.intent_id)).fetchone()==(0,)


def test_historical_bundle_or_owner_json_cannot_mint_a_new_live_continuation_record(database):
    from trading_system.risk.program_continuation import persist_program_continuation
    c=quote_case(database);receipt=public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    with c.owner.transaction(c.scope) as tx:
        bundle=read_program_bundle(tx,receipt.intent_id)
        with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):persist_program_continuation(tx,bundle)
