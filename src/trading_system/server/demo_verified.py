"""Opt-in verified Demo composition. Never generates qualification evidence."""
import json
import re
import time
from pathlib import Path
from trading_system.engine.binance_demo.attestation import TrustStore,measure_build
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.runtime.demo_service import DemoRuntimeService
from trading_system.storage.demo_authority import DemoAuthority
from trading_system.execution.demo_consistency import SerializedAccountPolicy,QualifiedReconciler


def read_document(path):
    try:
        raw=Path(path).read_bytes()
        if len(raw)>262144: raise ValueError()
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value: raise ValueError()
                value[key]=item
            return value
        value=json.loads(raw,object_pairs_hook=unique)
        if type(value) is not dict: raise ValueError()
        return value
    except (OSError,ValueError,TypeError): raise DemoError('DEMO_TRUST_DOCUMENT_INVALID') from None


class ReloadingTrust:
    test_only=False
    def __init__(self,path): self.path=Path(path).resolve()
    def verify(self,*args,**kwargs):
        config=read_document(self.path)
        if set(config)!={'issuers','revoked'} or not isinstance(config['revoked'],list): raise DemoError('DEMO_TRUST_CONFIGURATION_INVALID')
        return TrustStore(config['issuers'],revoked=config['revoked']).verify(*args,**kwargs)


class DeploymentEvidence:
    def __init__(self,*,root,trust_path,admission_file,scope,code_sha):
        self.root,self.file,self.scope,self.code_sha=Path(root),Path(admission_file),scope,code_sha
        self.trust=ReloadingTrust(trust_path)
        self.build_digest=measure_build(root)
        self.verify()

    def verify(self):
        if measure_build(self.root)!=self.build_digest: raise DemoError('DEMO_RUNNING_BUILD_CHANGED')
        bundle=read_document(self.file)
        if set(bundle) not in ({'deployment','qualification'},{'deployment','qualification','recovery'}):
            raise DemoError('DEMO_TRUST_DOCUMENT_INVALID')
        payload=self.trust.verify(bundle['deployment'],purpose='deployment',bindings={
            'build':self.build_digest,'code_sha':self.code_sha,'account_key':self.scope.account_key,'product':'SPOT'},now=int(time.time()))
        claims=payload['claims']; checks=claims.get('checks')
        if (claims.get('policy')!='serialized-account-v1' or not isinstance(checks,dict) or
                set(checks)!={'PG_FENCING','PG_MIGRATIONS','PROCESS_ISOLATION','IMMUTABLE_BUILD'} or
                any(not isinstance(v,dict) or v.get('state')!='PASS' or not isinstance(v.get('evidence_sha256'),str) or
                    not re.fullmatch('[0-9a-f]{64}',v['evidence_sha256']) for v in checks.values())):
            raise DemoError('DEMO_DEPLOYMENT_QUALIFICATION_REQUIRED')
        return bundle


class VerifiedDemoRuntime(DemoRuntimeService):
    entry_reserve_seconds = 30
    def __init__(self,*,deployment,**kwargs):
        super().__init__(**kwargs)
        self.deployment=deployment
        self.authority=DemoAuthority(self.repository,deployment.trust,build_digest=deployment.build_digest,code_sha=self.current_code_sha)

    def _bind_session(self,session_key):
        bundle=self.deployment.verify()
        if bundle['qualification'] is None: raise DemoError('DEMO_QUALIFICATION_REQUIRED')
        super()._bind_session(session_key)
        runtime=self._runtime(session_key)
        try:
            scope=runtime.host.scope
            if runtime.physical_owner is None: raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
            generation=runtime.physical_owner.generation
            existing=self.repository.account(scope).get('qualification') or {}
            if existing.get('proof_nonce')!=bundle['qualification'].get('payload',{}).get('nonce'):
                self.authority.install(scope,bundle['qualification'],generation=generation)
            self.authority.require(scope,generation=generation)
            policy=SerializedAccountPolicy(self.repository,runtime.transport,self.authority)
            runtime.reconciler=QualifiedReconciler(policy)
            runtime.host.owner.consistency_policy=policy
            runtime.host.owner.consistency_stream=runtime.stream
            runtime.host.owner.entry_preflight=lambda: self._require_entry_window(runtime)
            runtime.host.owner.capability_preflight=lambda method: self.authority.require(scope,generation=generation,cancel=method=='DELETE')
            def preflight():
                self.deployment.verify()
                self._sender_preflight(runtime)
            runtime.host.owner.preflight=preflight
            return self.status(session_key)
        except BaseException:
            self._close_session(session_key)
            raise

    def _require_execution_qualification(self,runtime,account):
        self.deployment.verify()
        if runtime.physical_owner is None: raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
        self.authority.require(runtime.host.scope,generation=runtime.physical_owner.generation)

    def _recover_physical_owner(self,scope,lease):
        from trading_system.storage.demo_physical import PhysicalOwner
        from trading_system.storage.demo import physical_owners,accounts
        bundle=self.deployment.verify()
        envelope=bundle.get('recovery')
        if envelope is None: return None
        fingerprint=self.connections.provider.describe(lease.credential_ref)['fingerprint']
        def recover(owner):
            lease.validate()
            return self.authority.consume_recovery(scope,envelope,credential_ref=lease.credential_ref,
                fingerprint=fingerprint,physical_owner=owner)
        return PhysicalOwner.acquire(self.repository.engine,physical_owners,accounts,scope,
            authority=self._handoff_authority,recovery=recover)

    async def run_window(self,session_key,*,duration_seconds,execute=False,**kwargs):
        runtime=self._runtime(session_key)
        self._require_execution_qualification(runtime,self.repository.account(runtime.host.scope))
        try:
            return await super().run_window(session_key,duration_seconds=duration_seconds,execute=execute,**kwargs)
        finally:
            if not runtime.running:
                from trading_system.engine.binance_demo.async_safety import settled_thread
                from uuid import uuid4
                def record():
                    with self.repository.transaction(runtime.host.scope.account_key) as conn:
                        receipt=self.authority.responsibilities(runtime.host.scope,conn=conn)
                        self.repository._event(conn,runtime.host.scope.account_key,'NEEDS_OPERATOR',uuid4().hex,receipt)
                await settled_thread(record)

    def takeover_status(self,session_key):
        # Authenticated API supplies the operator session. Scope is fixed by
        # trusted deployment, never supplied by the browser or an expired key.
        return self.authority.responsibilities(self.deployment.scope)

    def acknowledge_takeover(self,session_key,expected_digest):
        with self._lifecycle_lock:
            scope=self.deployment.scope
            active=[r for r in self._sessions.values() if self._same_physical_account(r.host.scope,scope)]
            if self._account_has_active_window(scope) or any(r.running or r.drain_failed for r in active):
                raise DemoError('DEMO_RUNTIME_DRAIN_REQUIRED')
            receipt=self.authority.acknowledge_takeover(scope,expected_digest)
            for runtime in active: runtime.new_risk_paused=True
            return receipt

    def status(self,session_key):
        result=super().status(session_key)
        try: self.deployment.verify()
        except DemoError as exc:
            return {**result,'build_attestation':'INVALID','deployment_execution_enabled':False,'binding_blocker':exc.code}
        return {**result,'build_attestation':'VERIFIED_SIGNED_MEASUREMENT','deployment_execution_enabled':False,
                'admission_policy':'serialized-account-v1','execution_authorized':False}
