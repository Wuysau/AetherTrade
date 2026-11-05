import base64,json,time
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trading_system.engine.binance_demo.attestation import canonical,measure_build
from trading_system.engine.binance_demo.contracts import DemoScope,DemoError
from trading_system.server.config import Settings
from trading_system.server.demo_deployment import load_demo_factory
from test_deployment import config
from pathlib import Path


def deployment_files(tmp_path):
    value=config(); key=Ed25519PrivateKey.generate(); now=int(time.time())
    root=Path(__file__).resolve().parents[2]
    scope=DemoScope(account_id=value['account_id'],account_epoch=value['account_epoch'],strategy_id=value['strategy_id'])
    claims={'policy':'serialized-account-v1','checks':{k:{'state':'PASS','evidence_sha256':'c'*64}
        for k in ('PG_FENCING','PG_MIGRATIONS','PROCESS_ISOLATION','IMMUTABLE_BUILD')}}
    payload=dict(version=1,issuer='operator',purpose='deployment',nonce='deployment_nonce_123',issued_at=now-1,expires_at=now+600,
        bindings={'build':measure_build(root),'code_sha':value['build_sha'],'account_key':scope.account_key,'product':'SPOT'},claims=claims)
    proof={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    trust_path=tmp_path/'trust.json'; admission_path=tmp_path/'admission.json'
    trust_path.write_text(json.dumps({'issuers':{'operator':{'key':key.public_key().public_bytes_raw().hex(),
        'purposes':['deployment'],'test_only':False}},'revoked':[]}))
    admission_path.write_text(json.dumps({'deployment':proof,'qualification':None}))
    value['admission_file']=str(admission_path)
    path=tmp_path/'config.json'; path.write_text(json.dumps(value))
    return path,trust_path,admission_path


def test_verified_composition_is_lazy_and_does_not_claim_account_qualification(tmp_path):
    path,trust,admission=deployment_files(tmp_path)
    factory=load_demo_factory(path,Settings(testing=True),trust_path=trust)
    runtime=factory(connections=None,model_service=None)
    status=runtime.status('none')
    assert status['build_attestation']=='VERIFIED_SIGNED_MEASUREMENT'
    assert status['execution_authorized'] is False
    assert status['blocker']=='DEMO_RUNTIME_NOT_BOUND'
    runtime.close()


def test_missing_trust_or_changed_content_rejects_before_any_connection(tmp_path):
    path,trust,admission=deployment_files(tmp_path)
    with pytest.raises((ValueError,DemoError)): load_demo_factory(path,Settings(testing=True))
    raw=json.loads(admission.read_text()); raw['deployment']['payload']['bindings']['build']='f'*64
    admission.write_text(json.dumps(raw))
    with pytest.raises(DemoError): load_demo_factory(path,Settings(testing=True),trust_path=trust)


def test_revocation_rechecked_after_factory_creation(tmp_path):
    path,trust,admission=deployment_files(tmp_path)
    factory=load_demo_factory(path,Settings(testing=True),trust_path=trust)
    runtime=factory(connections=None,model_service=None)
    raw=json.loads(trust.read_text()); raw['revoked']=['deployment_nonce_123']; trust.write_text(json.dumps(raw))
    with pytest.raises(DemoError): runtime.bind_session('none')
    runtime.close()
