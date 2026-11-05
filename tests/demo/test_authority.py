import base64
import pytest
from sqlalchemy import create_engine, select
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trading_system.engine.binance_demo.attestation import TrustStore, canonical
from trading_system.engine.binance_demo.contracts import DemoScope, DemoError, digest
from trading_system.engine.binance_demo.qualification import QualificationRun
from trading_system.storage.demo import DemoRepository, metadata
from trading_system.storage.demo_authority import DemoAuthority


@pytest.fixture
def authority(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'authority.sqlite'))
    metadata.create_all(engine)
    repo=DemoRepository(engine)
    scope=DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
    repo.register_account(scope,credential_ref='fixture',fingerprint='key1',budget='1000')
    key=Ed25519PrivateKey.generate()
    trust=TrustStore({'acceptance':{'key':key.public_key().public_bytes_raw().hex(),
        'purposes':['qualification','recovery'],'test_only':True}},test_only=True)
    service=DemoAuthority(repo,trust,build_digest='a'*64,code_sha='b'*40,clock=lambda:100)
    yield service,repo,scope,key
    engine.dispose()


def proof(service,repo,scope,key,*,nonce='n'*32):
    claims={'policy':'serialized-account-v1','budget':'1000','baseline':{'USDT':'1000'},
            'dedicated_account':True,'external_activity_excluded':True,'protection':'HOSTED',
            'official_contract_revision':'binance-spot-20261105',
            'checks':{name:{'state':'PASS','evidence_sha256':'c'*64} for name in QualificationRun.REQUIRED}}
    payload=dict(version=1,issuer='acceptance',purpose='qualification',nonce=nonce,issued_at=90,expires_at=200,
                 bindings=service.bindings(scope,generation=1),claims=claims)
    return {'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}


def test_install_require_revoke_and_replay(authority):
    service,repo,scope,key=authority
    envelope=proof(*authority)
    service.install(scope,envelope,generation=1)
    assert service.require(scope,generation=1)['policy'] == 'serialized-account-v1'
    assert repo.account(scope)['state'] != 'READY'
    with pytest.raises(DemoError): service.install(scope,envelope,generation=1)
    service.revoke(scope,'n'*32)
    with pytest.raises(DemoError): service.require(scope,generation=1)


def test_reject_partial_qualification_and_credential_change(authority):
    service,repo,scope,key=authority
    envelope=proof(*authority)
    envelope['payload']['claims']['checks']['LIMIT_FILL']['state']='NOT_RUN'
    envelope['signature']=base64.b64encode(key.sign(canonical(envelope['payload']))).decode()
    with pytest.raises(DemoError): service.install(scope,envelope,generation=1)
    service.install(scope,proof(*authority),generation=1)
    with pytest.raises(DemoError): service.require(scope,generation=2)
    repo.rotate_credential(scope,credential_ref='new',fingerprint='key2',expected_revision=repo.account(scope)['revision'])
    with pytest.raises(DemoError): service.require(scope,generation=1)


def test_invalid_baseline_never_overwrites_original(authority):
    service,repo,scope,key=authority
    repo.balance_baseline(scope,{'USDT':'999'})
    with pytest.raises(DemoError): service.install(scope,proof(*authority),generation=1)
    assert repo.account(scope)['balances'] == {'USDT':'999'}


def test_takeover_requires_current_digest_and_revokes_strategy(authority):
    service,repo,scope,key=authority
    repo.save_strategy(scope,{'revision':1,'budget':'1000','allow_execution':True,'allow_model_calls':True,'authorized':True},expected_revision=0)
    view=service.responsibilities(scope)
    with pytest.raises(DemoError): service.acknowledge_takeover(scope,'f'*64)
    receipt=service.acknowledge_takeover(scope,view['digest'])
    assert receipt['state']=='TAKEOVER_ACKNOWLEDGED'
    assert repo.strategy(scope)['allow_execution'] is False
    assert repo.account(scope)['state']=='PAUSED'
    assert receipt['flat_account_verified'] is False


def test_recovery_consumes_proof_preserves_unknown_and_never_sets_ready(authority):
    from sqlalchemy import insert
    from trading_system.storage.demo import physical_owners,commands
    from dataclasses import asdict
    service,repo,scope,key=authority
    identity={k:asdict(scope)[k] for k in ('environment','venue','product','account_id')}
    with repo.engine.begin() as conn:
        conn.execute(insert(physical_owners).values(physical_key=scope.physical_account_key,identity=identity,
            account_key=scope.account_key,generation=1,state='DIRTY'))
    repo.stage(scope,command_id='unknown',request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001',
        'price':'50000','timeInForce':'GTC','newClientOrderId':'original'},reserve_asset='USDT',reserve_amount='50',
        available='1000',authorization_ref='old',expires_at=150,now=100)
    repo.begin_send(scope,'unknown',expected_revision=0,now=100)
    repo.mark_unknown(scope,'unknown','lost')
    bindings={**service.bindings(scope,generation=1),'new_fingerprint':'key2'}
    payload=dict(version=1,issuer='acceptance',purpose='recovery',nonce='recovery_nonce_123',issued_at=90,expires_at=200,
        bindings=bindings,claims={'old_process_terminated':True,'old_credentials_revoked':True,'evidence_sha256':'d'*64})
    envelope={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    assert service.consume_recovery(scope,envelope,credential_ref='new',fingerprint='key2')==2
    assert repo.command(scope,'unknown')['state']=='UNKNOWN'
    assert repo.command(scope,'unknown')['reserve_amount']=='50'
    assert repo.account(scope)['state']=='RECOVERY_REQUIRED'
    assert repo.account(scope)['credential_revision']==1
    with pytest.raises(DemoError): service.consume_recovery(scope,envelope,credential_ref='new',fingerprint='key2')


def test_invalidated_balance_preserves_verified_cancel_only(authority):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts,balance_invalidated_evidence
    service,repo,scope,key=authority
    service.install(scope,proof(*authority),generation=1)
    with repo.engine.begin() as conn:
        conn.execute(update(accounts).values(qualification=balance_invalidated_evidence(repo.account(scope)['qualification'])))
    service.require(scope,generation=1,cancel=True)
    with pytest.raises(DemoError): service.require(scope,generation=1)
    service.revoke(scope,'n'*32)
    with pytest.raises(DemoError): service.require(scope,generation=1,cancel=True)


def test_signed_recovery_revalidates_exact_old_fact_without_erasing_original(authority):
    from sqlalchemy import insert,update
    from dataclasses import asdict
    from trading_system.storage.demo import physical_owners,observations,inbox,decisions
    auth,repo,scope,key=authority
    with repo.engine.begin() as conn:
        conn.execute(insert(physical_owners).values(physical_key=scope.physical_account_key,
            identity={k:asdict(scope)[k] for k in ('environment','venue','product','account_id')},
            account_key=scope.account_key,generation=1,state='DIRTY'))
        conn.execute(insert(decisions).values(scope_key=scope.key,account_key=scope.account_key,run_id='interrupted',state='IN_FLIGHT',
            credential_revision=0,stream_generation=1,source='assessment_v1',binding_hash='h',snapshot={},strategy={}))
    request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000','timeInForce':'GTC','newClientOrderId':'original'}
    repo.stage(scope,command_id='cmd',request=request,reserve_asset='USDT',reserve_amount='50',available='1000',authorization_ref='old',expires_at=150,now=100)
    repo.begin_send(scope,'cmd',expected_revision=0,now=100)
    raw={'symbol':'BTCUSDT','clientOrderId':'original','orderId':22,'side':'BUY','type':'LIMIT','origQty':'0.001',
         'price':'50000','status':'NEW','executedQty':'0','cummulativeQuoteQty':'0','updateTime':100000}
    old=repo.observe(scope,source='REST_ORDER',payload=raw,received_at=100,credential_ref='fixture',credential_revision=0,stream_generation=1)
    payload=dict(version=1,issuer='acceptance',purpose='recovery',nonce='reviewed_recovery_123',issued_at=90,expires_at=200,
        bindings={**auth.bindings(scope,generation=1),'new_fingerprint':'key2'},claims={
            'old_process_terminated':True,'old_credentials_revoked':True,'evidence_sha256':'f'*64,'observation_hashes':{old:digest(raw)}})
    signed={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    auth.consume_recovery(scope,signed,credential_ref='new',fingerprint='key2')
    current=repo.observe(scope,source='REST_ORDER',payload=raw,received_at=101,credential_ref='new',credential_revision=1,stream_generation=2)
    assert current!=old
    auth.revalidate_observations(scope,generation=2)
    with repo.engine.connect() as conn:
        assert conn.execute(select(inbox.c.state).where(inbox.c.observation_id==old)).scalar_one()=='PROCESSED'
        original=conn.execute(select(observations).where(observations.c.observation_id==old)).mappings().one()
        assert original['credential_revision']==0 and original['stream_generation']==1 and original['payload']==raw
    assert repo.decision(scope,'interrupted')['state']=='FAILED'
    assert repo.command(scope,'cmd')['client_id']=='original'
