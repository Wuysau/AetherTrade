"""Actual SAME-kernel provenance/attachment, not a financial or send permit."""
import pytest

from integration.test_program_financial_admission import isolated_postgres,database,real_case
from trading_system.risk.program_financial_admission import (
    claim_program_attachment,program_bundle_command_ids,ProgramFinancialBundle,BUNDLE_PREFIX)


def test_actual_kernel_witness_binds_whole_external_identity_set_atomically(database):
    c=real_case(database)
    def operation(tx):
        bundle=c.service._reserve_program(tx,c.key,('original-request-id',),load_inputs=c.load,
            load_qualification=c.qualify,load_clock=lambda tx:c.c.facts.inputs.clock)
        assert program_bundle_command_ids(tx,bundle)==('original-request-id',)
        first=claim_program_attachment(tx,bundle,mode='EXTERNAL_REQUESTS',command_ids=('original-request-id',))
        assert claim_program_attachment(tx,bundle,mode='EXTERNAL_REQUESTS',command_ids=('original-request-id',))==first
        assert not first.execution_authorized
        return bundle
    bundle=c.service._transaction(operation)
    with c.owner.transaction(c.scope) as tx:
        restored=tx._latest('audit_events',BUNDLE_PREFIX+c.record.intent_id,ProgramFinancialBundle)
        assert restored==bundle
        # The historical audit record is readable, but cannot mint a new live
        # witness or reopen an attachment in a different transaction.
        with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):
            program_bundle_command_ids(tx,restored)


@pytest.mark.parametrize('fault',['identities','mode'])
def test_bad_attachment_rolls_back_actual_financial_bundle(database,fault):
    c=real_case(database)
    def operation(tx):
        bundle=c.service._reserve_program(tx,c.key,('original-request-id',),load_inputs=c.load,
            load_qualification=c.qualify,load_clock=lambda tx:c.c.facts.inputs.clock)
        return claim_program_attachment(tx,bundle,mode='LOCAL_ACTIVATION' if fault=='mode' else 'EXTERNAL_REQUESTS',
            command_ids=('other-id',) if fault=='identities' else ('original-request-id',))
    with pytest.raises(ValueError):c.service._transaction(operation)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
