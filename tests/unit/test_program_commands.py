"""Complete typed request/bundle binding; no venue or qualification claim."""
from dataclasses import replace
import pytest
from unit.test_assessment_archive import actual_archive
from trading_system.intents.program_assessment_archive import financial_intent_from_assessment_archive,financial_risk_basis_from_assessment_archive
from trading_system.risk.program_financial_admission import ProgramFinancialBundle
from trading_system.execution.creation_requests import assign_client_ids,materialize_creation

def creation():
    a=actual_archive();i=financial_intent_from_assessment_archive(a)
    b=ProgramFinancialBundle(intent=i,basis=financial_risk_basis_from_assessment_archive(i,a),archive=a,decision=a.assessment,reservations=())
    request=materialize_creation(a.compilation,0,a.profile,assign_client_ids(a.compilation,0,'command-one'))
    return request,b

def test_creation_binds_entire_typed_bundle_and_unsigned_request():
    from trading_system.execution.program_commands import build_creation_write,validate_creation_binding
    r,b=creation();w,binding=build_creation_write(r,b,execution_epoch=4)
    assert validate_creation_binding(w,r,binding,b)==binding
    assert w.intent_id==b.intent.intent_id and w.execution_epoch==4
    assert not w.execution_authorized and not r.execution_authorized
    assert next(f.value for f in r.fields if f.name=='price')=='81490.00'

@pytest.mark.parametrize('change',['field','route','ids','intent','basis','decision','archive','epoch'])
def test_creation_rejects_mutated_complete_binding(change):
    from trading_system.execution.program_commands import build_creation_write,validate_creation_binding
    r,b=creation();w,binding=build_creation_write(r,b,execution_epoch=4)
    if change=='field':r=replace(r,fields=tuple(replace(f,value='MARKET') if f.name=='type' else f for f in r.fields))
    if change=='route':r=replace(r,namespace='BINANCE_REAL_EQUITY')
    if change=='ids':r=replace(r,assigned_ids=replace(r.assigned_ids,command_id='replacement'))
    if change=='intent':b=replace(b,intent=replace(b.intent,intent_id='replacement'))
    if change=='basis':b=replace(b,basis=replace(b.basis,intent_hash='c'*64))
    if change=='decision':b=replace(b,decision=replace(b.decision,verdict='DENY'))
    if change=='archive':b=replace(b,archive=replace(b.archive,intent_id='replacement'))
    if change=='epoch':w=replace(w,execution_epoch=5)
    with pytest.raises(ValueError):validate_creation_binding(w,r,binding,b)

def test_staged_external_creation_requires_genuine_activation_and_stage_admission():
    from trading_system.execution.program_commands import build_creation_write
    a=actual_archive(template='OTO',mode='LOCAL_SYNTHETIC');i=financial_intent_from_assessment_archive(a)
    b=ProgramFinancialBundle(intent=i,basis=financial_risk_basis_from_assessment_archive(i,a),archive=a,decision=a.assessment,reservations=())
    r=materialize_creation(a.compilation,1,a.profile,assign_client_ids(a.compilation,1,'staged-command'))
    with pytest.raises(ValueError,match='PROGRAM_STAGED_REQUEST_NOT_ADMITTED'):
        build_creation_write(r,b,execution_epoch=1)

@pytest.mark.parametrize('fault,reason',[
    ('missing','PROGRAM_CURRENT_WALLET_REQUIRED'),('mismatch','CREATION_REQUEST_CHANGED'),
    ('stale','EQUITY_PAYMENT_WALLET_STALE'),('quote','EQUITY_PAYMENT_WALLET_BINDING_MISMATCH'),
    ('scope','EQUITY_PAYMENT_WALLET_BINDING_MISMATCH'),('profile','EQUITY_PAYMENT_WALLET_BINDING_MISMATCH'),
    ('exact','PROGRAM_EQUITY_WALLET_FUNDING_LOCATION_UNBOUND')])
def test_equity_current_wallet_is_independent_and_cannot_invent_funding_location(fault,reason):
    from types import SimpleNamespace
    from trading_system.contracts import types as t
    from trading_system.execution.creation_requests import EquityWalletSelection
    from trading_system.execution.program_commands import ProgramCommandRepository
    a=actual_archive(product=t.Product.US_EQUITY);plan=a.compilation;clock=a.risk_facts.inputs.clock
    wallet=EquityWalletSelection(scope=plan.scope,profile_hash=plan.profile_hash,quote_asset='USD',wallet='CARD',
        evidence=a.book.evidence[0],valid_until=a.risk_terms.valid_until)
    r=materialize_creation(plan,0,a.profile,assign_client_ids(plan,0,'equity-wallet-command'),wallet_selection=wallet,now=clock.utc_now)
    current=wallet
    if fault=='mismatch':current=replace(wallet,wallet='MAIN')
    if fault=='stale':current=replace(wallet,valid_until=clock.utc_now)
    if fault=='quote':current=replace(wallet,quote_asset='USDT')
    if fault=='scope':current=replace(wallet,scope=replace(wallet.scope,venue_account_id='other-account'))
    if fault=='profile':current=replace(wallet,profile_hash='e'*64)
    repository=ProgramCommandRepository(SimpleNamespace(scope=plan.scope),
        load_wallet_selection=None if fault=='missing' else lambda *args:current)
    with pytest.raises(ValueError,match=reason):repository._verify_current_request(r,a,clock)
