import base64
import copy
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.engine.binance_demo.attestation import TrustStore, canonical, measure_build


def signed(purpose='deployment', claims=None):
    key = Ed25519PrivateKey.generate()
    payload = dict(version=1, issuer='operator', purpose=purpose, nonce='n'*32,
                   issued_at=90, expires_at=200, bindings={'build':'a'*64}, claims=claims or {})
    envelope = {'payload':payload, 'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    trust = TrustStore({'operator':{'key':key.public_key().public_bytes_raw().hex(),
                                  'purposes':[purpose], 'test_only':True}}, test_only=True)
    return key, trust, envelope


def test_signed_proof_requires_exact_binding_and_purpose():
    _, trust, envelope = signed()
    assert trust.verify(envelope, purpose='deployment', bindings={'build':'a'*64}, now=100)['nonce'] == 'n'*32
    for purpose, binding in [('recovery',{'build':'a'*64}), ('deployment',{'build':'b'*64})]:
        with pytest.raises(DemoError): trust.verify(envelope,purpose=purpose,bindings=binding,now=100)
    altered = copy.deepcopy(envelope); altered['payload']['claims']['enabled'] = True
    with pytest.raises(DemoError): trust.verify(altered,purpose='deployment',bindings={'build':'a'*64},now=100)


@pytest.mark.parametrize('now', [89,200,201,True])
def test_expired_or_future_proof_rejected(now):
    _, trust, envelope = signed()
    with pytest.raises(DemoError): trust.verify(envelope,purpose='deployment',bindings={'build':'a'*64},now=now)


def test_test_trust_cannot_be_used_by_production():
    key, _, envelope = signed()
    with pytest.raises(DemoError):
        TrustStore({'operator':{'key':key.public_key().public_bytes_raw().hex(), 'purposes':['deployment'], 'test_only':True}})


def test_revoked_and_malformed_proofs_rejected():
    key, _, envelope = signed()
    trust = TrustStore({'operator':{'key':key.public_key().public_bytes_raw().hex(), 'purposes':['deployment'], 'test_only':True}},
                       test_only=True, revoked={'n'*32})
    with pytest.raises(DemoError): trust.verify(envelope,purpose='deployment',bindings={'build':'a'*64},now=100)
    for bad in ({}, [], {'payload':None}, {**envelope,'signature':'garbage'}):
        with pytest.raises(DemoError): trust.verify(bad,purpose='deployment',bindings={'build':'a'*64},now=100)


def test_measurement_covers_code_schema_dependencies_and_migrations(tmp_path):
    for name in ('src/trading_system/a.py','src/trading_system/schemas/a.json','migrations/0037.sql','pyproject.toml'):
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('original')
    before=measure_build(tmp_path)
    (tmp_path/'src/trading_system/a.py').write_text('changed')
    assert measure_build(tmp_path) != before
    with pytest.raises(DemoError): measure_build(tmp_path/'missing')


def test_qualification_does_not_promote_ack_to_fill_or_skips_to_pass():
    from trading_system.engine.binance_demo.qualification import QualificationRun
    run=QualificationRun(max_commands=2, max_notional='100', expires_at=200)
    run.reserve('original', '50', now=100)
    with pytest.raises(DemoError): run.reserve('original', '50', now=100)
    run.record('LIMIT_ACK', 'PASS', 'a'*64)
    assert run.summary()['state'] == 'NOT_RUN'
    run.record('LIMIT_FILL','FAIL','b'*64)
    assert run.summary()['state'] == 'FAIL'
    with pytest.raises(DemoError): run.reserve('second','51',now=100)
    with pytest.raises(DemoError): run.reserve('second','1',now=200)
