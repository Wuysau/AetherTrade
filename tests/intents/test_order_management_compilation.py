from dataclasses import replace

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.order_lifecycle import ApplicationWrite
from trading_system.intents.program_compilation import Namespace, compile_order_program
from trading_system.intents.order_management_compilation import (
    ManagementInstruction, ManagementKind, OwnedTargetSnapshot, compile_order_management,
)
from trading_system.intents.semantic_validation import ProposalRejected
from test_program_compilation import inputs


def fixture(namespace=Namespace.SPOT_ORDINARY):
    product = t.Product.USD_M_PERP if namespace in (Namespace.USD_M_ORDINARY,Namespace.USD_M_CONDITIONAL,Namespace.USD_M_EXECUTION) else t.Product.US_EQUITY if namespace==Namespace.EQUITY else t.Product.SPOT
    p,r,c,k=inputs(product)
    target=OwnedTargetSnapshot(scope=k['scope'],namespace=namespace,program_id='original-program',
        program_hash='d'*64,venue_id='123',client_id='original-client',owner_strategy_id='owner',
        ownership_epoch=1,execution_epoch=2,policy_revision=3,status='NEW',
        order=p.program.nodes[0].order,original_quantity='1',cumulative_quantity='0.25',
        parent_list_id=None,evidence=r.evidence[0],as_of=r.as_of,valid_until=r.valid_until)
    service='ORDER_LIST' if namespace==Namespace.SPOT_LIST else 'CONDITIONAL_ALGO' if namespace==Namespace.USD_M_CONDITIONAL else 'EXECUTION_ALGO' if namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION) else 'ORDINARY'
    command=ApplicationWrite(contract_version='2026-10-28',venue_client_id='management-client',service_namespace=service,
        owner_strategy_id='owner',command_id='amend-command',program_id=target.program_id,scope=k['scope'],
        origin='APPLICATION_ACTION',operation='AMEND',parent_command_id=None,target_venue_id=target.venue_id,
        canonical_program_hash=target.program_hash,ownership_epoch=1,execution_epoch=2,policy_revision=3,
        durable_record_ref='durable:management',state='DURABLE_READY',created_at=k['now'])
    kwargs=dict(scope=k['scope'],now=k['now'],expected_owner_strategy_id='owner',expected_ownership_epoch=1,
        expected_execution_epoch=2,expected_policy_revision=3,rules=r,capabilities=replace(c,capability_ids=(*c.capability_ids,
            'SPOT-AMEND','SPOT-CANCEL_REPLACE','SPOT-CANCEL_QUERY','USDM-AMEND','USDM-CANCEL_QUERY','EQUITY-CANCEL_QUERY')),
        profile=k['profile'],expected_build_digest=k['expected_build_digest'],expected_rule_revision=1)
    kwargs['expected_capabilities_hash']=canonical_hash(domain_payload(kwargs['capabilities']))
    return p,r,c,k,target,command,kwargs


def test_spot_keep_priority_is_explicit_reduction_only():
    *_,target,command,k=fixture()
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),target,command=command,**k)
    request=result.requests[0]
    assert request.method=='PUT' and request.endpoint.value=='/api/v3/order/amend/keepPriority'
    assert {p.name:p.value for p in request.parameters}['newQty']=='0.5'
    assert request.requires_independent_command and result.priority_effect=='RETAINED'
    assert not result.execution_authorized


@pytest.mark.parametrize('value',['1','2','0.1'])
def test_keep_priority_never_increases_or_erases_already_filled_quantity(value):
    *_,target,command,k=fixture()
    with pytest.raises(ProposalRejected,match='KEEP_PRIORITY_REDUCTION_REQUIRED'):
        compile_order_management(ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity=value),target,command=command,**k)


@pytest.mark.parametrize('namespace,path,method',[(Namespace.SPOT_ORDINARY,'/api/v3/order','DELETE'),
    (Namespace.SPOT_LIST,'/api/v3/orderList','DELETE'),(Namespace.USD_M_ORDINARY,'/fapi/v1/order','DELETE'),
    (Namespace.USD_M_CONDITIONAL,'/fapi/v1/algoOrder','DELETE'),(Namespace.SPOT_EXECUTION,'/sapi/v1/algo/spot/order','DELETE'),
    (Namespace.USD_M_EXECUTION,'/sapi/v1/algo/futures/order','DELETE'),(Namespace.EQUITY,'/sapi/v1/equity/order/cancel','POST')])
def test_cancel_uses_exact_owned_service_namespace(namespace,path,method):
    *_,target,command,k=fixture(namespace)
    command=replace(command,operation='CANCEL_PROGRAM' if command.service_namespace!='ORDINARY' else 'CANCEL')
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.CANCEL),target,command=command,**k)
    request=result.requests[0]
    assert request.endpoint.value==path and request.method==method
    assert request.requires_independent_command and result.funds_released is False


def test_unknown_query_preserves_original_client_id_without_write_permit():
    *_,target,command,k=fixture(Namespace.USD_M_CONDITIONAL)
    target=replace(target,venue_id=None,status='UNKNOWN')
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.QUERY),target,command=None,**k)
    assert result.requests[0].method=='GET' and not result.requests[0].requires_independent_command
    assert {p.name:p.value for p in result.requests[0].parameters}['clientAlgoId']=='original-client'


def test_futures_modify_limit_retains_side_quantity_price_and_loses_queue_priority():
    *_,target,command,k=fixture(Namespace.USD_M_ORDINARY)
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.MODIFY_LIMIT,new_quantity='0.5',new_price='81480.00'),target,command=command,**k)
    request=result.requests[0];f={p.name:p.value for p in request.parameters}
    assert request.endpoint.value=='/fapi/v1/order' and request.method=='PUT'
    assert f['quantity']=='0.5' and f['price']=='81480.00' and f['side']=='BUY'
    assert result.priority_effect=='REQUEUED'


@pytest.mark.parametrize('mutation,code',[('owner','OWNED_TARGET_OWNER_CHANGED'),('epoch','OWNED_TARGET_EPOCH_CHANGED'),
    ('hash','MANAGEMENT_COMMAND_BINDING_MISMATCH'),('unknown','TARGET_RECONCILIATION_REQUIRED'),
    ('target','MANAGEMENT_COMMAND_BINDING_MISMATCH'),('namespace','MANAGEMENT_COMMAND_BINDING_MISMATCH')])
def test_management_never_crosses_owner_epoch_original_hash_or_target(mutation,code):
    *_,target,command,k=fixture()
    if mutation=='owner':target=replace(target,owner_strategy_id='other')
    if mutation=='epoch':target=replace(target,ownership_epoch=2)
    if mutation=='hash':command=replace(command,canonical_program_hash='e'*64)
    if mutation=='unknown':target=replace(target,status='UNKNOWN')
    if mutation=='target':command=replace(command,target_venue_id='999')
    if mutation=='namespace':command=replace(command,service_namespace='CONDITIONAL_ALGO')
    with pytest.raises(ProposalRejected,match=code):
        compile_order_management(ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),target,command=command,**k)


def test_cancel_replace_keeps_modes_and_independent_outcomes():
    p,r,c,compile_kwargs,target,command,k=fixture()
    compile_kwargs['expected_capabilities_hash']=k['expected_capabilities_hash']
    replacement=compile_order_program(p,r,k['capabilities'],**compile_kwargs)
    instruction=ManagementInstruction(kind=ManagementKind.CANCEL_REPLACE,replacement=replacement,
        cancel_replace_mode='ALLOW_FAILURE',order_rate_limit_mode='CANCEL_ONLY')
    result=compile_order_management(instruction,target,command=command,**k)
    f={p.name:p.value for p in result.requests[0].parameters}
    assert f['cancelReplaceMode']=='ALLOW_FAILURE' and f['orderRateLimitExceededMode']=='CANCEL_ONLY'
    assert f['cancelOrderId']=='123' and f['newClientOrderId']=='management-client'
    assert result.independent_cancel_replace_outcomes and result.replacement_program_hash==replacement.program_hash
    assert result.funds_released is False


def test_management_plan_roundtrips_without_turning_into_authority():
    from trading_system.intents.order_management_compilation import ManagementCompilation
    from trading_system.storage.models import decode
    *_,target,command,k=fixture()
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),target,command=command,**k)
    assert decode(ManagementCompilation,domain_payload(result))==result


def test_conditional_algo_is_never_modified_through_ordinary_limit_route():
    *_,target,command,k=fixture(Namespace.USD_M_CONDITIONAL)
    with pytest.raises(ProposalRejected,match='MODIFY_ORDINARY_LIMIT_REQUIRED'):
        compile_order_management(ManagementInstruction(kind=ManagementKind.MODIFY_LIMIT,new_quantity='0.5',new_price='81480.00'),target,command=command,**k)


@pytest.mark.parametrize('namespace',list(Namespace))
def test_queries_have_no_write_permit_and_execution_algo_collections_are_filtered(namespace):
    *_,target,command,k=fixture(namespace)
    result=compile_order_management(ManagementInstruction(kind=ManagementKind.QUERY),target,command=None,**k)
    assert all(r.method=='GET' and not r.requires_independent_command for r in result.requests)
    if namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION):
        assert len(result.requests)==2 and all(r.filter_original_identity for r in result.requests)
        assert all(not r.parameters for r in result.requests)


def test_management_is_independent_of_expired_initial_proposal_but_replacement_is_fresh():
    p,r,c,compile_kwargs,target,command,k=fixture()
    compile_kwargs['expected_capabilities_hash']=k['expected_capabilities_hash']
    replacement=compile_order_program(p,r,k['capabilities'],**compile_kwargs)
    k['now']='2026-10-28T10:01:00Z'
    # Existing GTC management continues after original 10:00:30 submission TTL.
    assert compile_order_management(ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),target,command=command,**k)
    with pytest.raises(ProposalRejected,match='REPLACEMENT_SUBMISSION_EXPIRED'):
        compile_order_management(ManagementInstruction(kind=ManagementKind.CANCEL_REPLACE,replacement=replacement,
            cancel_replace_mode='STOP_ON_FAILURE',order_rate_limit_mode='DO_NOTHING'),target,command=command,**k)


def test_futures_modify_notional_limit_includes_contract_multiplier():
    from trading_system.intents.product_rules import DecimalRange
    *_,target,command,k=fixture(Namespace.USD_M_ORDINARY)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,contract_multiplier='10'),
        notional=DecimalRange(minimum='10',maximum='10000'))
    with pytest.raises(ProposalRejected,match='MODIFY_NOTIONAL_OUT_OF_RANGE'):
        compile_order_management(ManagementInstruction(kind=ManagementKind.MODIFY_LIMIT,new_quantity='0.02',new_price='81490.00'),target,command=command,**k)
