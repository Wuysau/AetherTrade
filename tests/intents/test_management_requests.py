from dataclasses import replace

import pytest

from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.intents.order_management_compilation import (
    ManagementInstruction, ManagementKind, compile_order_management,
)
from trading_system.intents.program_compilation import Namespace, compile_order_program
from test_order_management_compilation import fixture


def digest(value):
    return canonical_hash(domain_payload(value))


def setup(namespace=Namespace.SPOT_ORDINARY, kind=ManagementKind.QUERY):
    p,r,c,creation,target,command,k=fixture(namespace)
    if kind==ManagementKind.QUERY:
        target=replace(target,venue_id=None,status='UNKNOWN')
        command=None
        instruction=ManagementInstruction(kind=kind)
    elif kind==ManagementKind.CANCEL:
        command=replace(command,operation='CANCEL_PROGRAM' if command.service_namespace!='ORDINARY' else 'CANCEL')
        instruction=ManagementInstruction(kind=kind)
    elif kind==ManagementKind.KEEP_PRIORITY:
        instruction=ManagementInstruction(kind=kind,new_quantity='0.5')
    elif kind==ManagementKind.MODIFY_LIMIT:
        instruction=ManagementInstruction(kind=kind,new_quantity='0.5',new_price='81480.00')
    else:
        creation['expected_capabilities_hash']=k['expected_capabilities_hash']
        replacement=compile_order_program(p,r,k['capabilities'],**creation)
        instruction=ManagementInstruction(kind=kind,replacement=replacement,
            cancel_replace_mode='ALLOW_FAILURE',order_rate_limit_mode='CANCEL_ONLY')
    plan=compile_order_management(instruction,target,command=command,**k)
    facts=dict(current_target=target,**k)
    if kind==ManagementKind.CANCEL_REPLACE:
        facts.update(expected_instrument_ref=creation['expected_instrument_ref'],
            allow_round_down=creation['allow_round_down'])
    return plan,facts


def materialize(plan,**kwargs):
    from trading_system.execution.management_requests import materialize_management
    return materialize_management(plan,**kwargs)


@pytest.mark.parametrize('namespace',list(Namespace))
def test_query_preserves_original_unknown_identity_and_service(namespace):
    plan,k=setup(namespace)
    requests=materialize(plan,**k)
    assert len(requests)==len(plan.requests)
    for index,(request,compiled) in enumerate(zip(requests,plan.requests,strict=True)):
        assert request.request_index==index
        assert request.scope==plan.target.scope and request.namespace==namespace
        assert request.method=='GET' and request.endpoint==compiled.endpoint
        assert request.command_id is None and request.original_client_id=='original-client'
        assert request.management_hash==digest(plan)
        assert request.filter_original_identity==compiled.filter_original_identity
        assert request.form_parameters()==tuple((p.name.value,str(p.value)) for p in compiled.parameters)
        assert request.execution_authorized is False and request.funds_released is False
        assert request.requires_independent_command is False


@pytest.mark.parametrize('namespace',list(Namespace))
def test_cancel_retains_exact_owned_id_without_sending_or_releasing(namespace):
    plan,k=setup(namespace,ManagementKind.CANCEL)
    request,=materialize(plan,**k)
    assert request.command_id==plan.command.command_id
    assert request.target_venue_id=='123' and request.original_client_id=='original-client'
    assert request.method==plan.requests[0].method and request.endpoint==plan.requests[0].endpoint
    assert request.requires_independent_command and request.execution_authorized is False
    assert request.funds_released is False


@pytest.mark.parametrize('namespace,kind',[
    (Namespace.SPOT_ORDINARY,ManagementKind.KEEP_PRIORITY),
    (Namespace.USD_M_ORDINARY,ManagementKind.MODIFY_LIMIT),
    (Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE),
])
def test_every_management_parameter_survives_to_adapter(namespace,kind):
    plan,k=setup(namespace,kind)
    request,=materialize(plan,**k)
    assert request.form_parameters()==tuple((p.name.value,str(p.value)) for p in plan.requests[0].parameters)
    assert request.source_request_hash==digest(plan.requests[0])
    assert request.instruction_hash==digest(plan.instruction)
    assert request.target_hash==digest(plan.target)


@pytest.mark.parametrize('mutation',['method','endpoint','namespace','parameters','instruction_hash','target_hash','priority'])
def test_caller_cannot_substitute_route_parameters_or_compilation_claims(mutation):
    from trading_system.intents.order_management_compilation import ManagementEndpoint,ManagementParameter,ManagementField
    plan,k=setup()
    request=plan.requests[0]
    if mutation=='method':plan=replace(plan,requests=(replace(request,method='DELETE'),))
    if mutation=='endpoint':plan=replace(plan,requests=(replace(request,endpoint=ManagementEndpoint.USD_M_ORDER),))
    if mutation=='namespace':plan=replace(plan,requests=(replace(request,namespace=Namespace.USD_M_ORDINARY),))
    if mutation=='parameters':plan=replace(plan,requests=(replace(request,parameters=(ManagementParameter(
        name=ManagementField.ORIGINAL_CLIENT_ID,value='unrelated-client'),)),))
    if mutation=='instruction_hash':plan=replace(plan,instruction_hash='a'*64)
    if mutation=='target_hash':plan=replace(plan,target_hash='a'*64)
    if mutation=='priority':plan=replace(plan,priority_effect='RETAINED')
    with pytest.raises(ValueError,match='MANAGEMENT_COMPILATION_CHANGED'):
        materialize(plan,**k)


@pytest.mark.parametrize('mutation',['identity','owner','epoch','stale','profile'])
def test_current_independent_facts_are_required(mutation):
    plan,k=setup()
    if mutation=='identity':k['current_target']=replace(k['current_target'],client_id='another-client')
    if mutation=='owner':k['current_target']=replace(k['current_target'],owner_strategy_id='another-owner')
    if mutation=='epoch':k['current_target']=replace(k['current_target'],execution_epoch=99)
    if mutation=='stale':k['now']=k['current_target'].valid_until
    if mutation=='profile':k['profile']=replace(k['profile'],venue_symbol='UNRELATED')
    with pytest.raises(ValueError):materialize(plan,**k)


def test_unknown_command_cannot_be_materialized_as_a_fresh_management_write():
    plan,k=setup(kind=ManagementKind.CANCEL)
    plan=replace(plan,command=replace(plan.command,state='UNKNOWN'))
    with pytest.raises(ValueError,match='MANAGEMENT_COMMAND_BINDING_MISMATCH'):
        materialize(plan,**k)
