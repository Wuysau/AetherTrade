import base64
import pytest
from test_authority import authority
from trading_system.engine.binance_demo.attestation import canonical
from trading_system.engine.binance_demo.contracts import DemoError,digest
from trading_system.execution.demo_qualification import QualificationExecutor


def test_probe_budget_is_durable_and_original_id_cannot_replay(authority):
    auth,repo,scope,key=authority
    # Explicitly isolated test issuer; does not qualify any production account.
    from trading_system.engine.binance_demo.attestation import TrustStore
    auth.trust=TrustStore({'acceptance':{'key':key.public_key().public_bytes_raw().hex(),
        'purposes':['qualification_run'],'test_only':True}},test_only=True)
    request={'symbol':'BTCUSDT','type':'LIMIT','side':'BUY','quantity':'0.001','price':'50000','timeInForce':'GTC','newClientOrderId':'probe-original'}
    payload=dict(version=1,issuer='acceptance',purpose='qualification_run',nonce='bounded_probe_nonce',issued_at=90,expires_at=150,
        bindings=auth.bindings(scope,generation=1),claims={'max_commands':2,'max_notional':'100','pg_fencing_evidence':'f'*64,
            'operator_responsibility':True,'requests':{'probe-original':digest(['POST',request])}})
    envelope={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    run=QualificationExecutor(repo,None,auth,scope,envelope,physical_owner=None,stream=None,clock=lambda:100)
    run.reserve('POST',request,generation=1)
    with pytest.raises(DemoError): run.reserve('POST',request,generation=1)
    with pytest.raises(DemoError): run.execute('POST',request)
    assert repo.pending_commands(scope)==[]
