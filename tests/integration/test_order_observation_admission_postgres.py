"""Observed real committed admission artifacts; synthetic qualification only."""
import pytest
from integration.test_generic_program_admission_postgres import database,isolated_postgres,real_case,public_service
from trading_system.server.order_observation import OrderObservationService
from trading_system.storage.models import scope_id


def test_real_admission_receipt_request_and_reservation_are_separate_from_execution(database):
    c=real_case(database)
    receipt=public_service(c).admit(c.record.proposal,c.record.context,c.c.plan)
    reader=OrderObservationService(c.owner.connection_factory,(c.scope,))
    view=reader.detail(c.record.proposal.program.program_id,scope=scope_id(c.scope))
    assert view['execution_authorized'] is False and view['execution_state']=='NOT_OBSERVED'
    assert view['records']['commands'][0]['payload']['state']=='DURABLE_READY'
    assert view['records']['requests'][0]['payload']['request_hash']==receipt.attachment.request_hashes[0]
    assert len(view['records']['reservations'])==1
    assert any(r['record_id']=='generic-program-admission:'+receipt.intent_id for r in view['records']['financial'])
    assert view['capability']['archived_snapshot']['qualification']=='PAPER_VERIFIED'
    assert view['capability']['current_qualification']=='UNKNOWN'
    assert reader.list()['items'][0]['scope']['venue_account_id']==c.scope.venue_account_id


@pytest.mark.parametrize('template',['TWAP','VP'])
def test_real_local_activation_is_visible_without_a_fictitious_venue_parent(database,template):
    c=real_case(database,template=template,mode='LOCAL_SYNTHETIC')
    receipt=public_service(c).admit(c.record.proposal,c.record.context,c.c.plan)
    view=OrderObservationService(c.owner.connection_factory,(c.scope,)).detail(
        c.record.proposal.program.program_id,scope=scope_id(c.scope))
    assert view['execution_authorized'] is False
    assert view['state_origin']=='LOCAL_ACTIVATION'
    assert view['execution_state']=='ACTIVE'
    assert view['records']['commands']==[]
    assert view['records']['local_states'][0]['payload']['binding']['parent_venue_id'] is None
    assert view['state_observation']['parent_observation'] is None
    assert receipt.attachment.activation_id


def test_rejected_intake_remains_visible_without_a_false_success_receipt(database):
    c=real_case(database,qualified=False)
    with pytest.raises(ValueError):public_service(c).admit(c.record.proposal,c.record.context,c.c.plan)
    reader=OrderObservationService(c.owner.connection_factory,(c.scope,))
    view=reader.detail(c.record.proposal.program.program_id,scope=scope_id(c.scope))
    assert view['source']=='order_program_intakes'
    assert view['records']['commands']==[] and view['records']['financial']==[]
    assert view['execution_state']=='NOT_OBSERVED'
    assert view['payload']==reader.list()['items'][0]['payload']
