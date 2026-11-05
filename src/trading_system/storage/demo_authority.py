"""Durable proof consumption and operator responsibility receipts.

Signed qualification is installed only by trusted composition, never HTTP.
Database roles/OS separation and actual PG locking require independent testing.
"""
import time
from sqlalchemy import Table, Column, String, JSON, Boolean, Integer, select, insert, update
from trading_system.engine.binance_demo.contracts import DemoError, decimal, digest
from trading_system.engine.binance_demo.qualification import QualificationRun


def define_authority(metadata):
    return Table('demo_authority_proofs',metadata,
        Column('nonce',String(100),primary_key=True),Column('account_key',String(64),nullable=False),
        Column('purpose',String(32),nullable=False),Column('envelope',JSON,nullable=False),
        Column('revoked',Boolean,nullable=False),Column('consumed_at',Integer,nullable=False))


class DemoAuthority:
    def __init__(self, repository, trust, *, build_digest, code_sha, clock=time.time):
        self.repository,self.trust=repository,trust
        self.build_digest,self.code_sha,self.clock=build_digest,code_sha,clock

    def bindings(self,scope,*,generation):
        account=self.repository.account(scope)
        return {'account_key':scope.account_key,'physical_key':scope.physical_account_key,
            'generation':generation,'credential_revision':account['credential_revision'],
            'fingerprint':account['fingerprint'],'build':self.build_digest,'code_sha':self.code_sha,
            'product':'SPOT','symbol':'BTCUSDT'}

    def _claims(self,payload):
        claims=payload['claims']
        checks=claims.get('checks')
        import re
        if (claims.get('policy') != 'serialized-account-v1' or claims.get('dedicated_account') is not True or
                claims.get('external_activity_excluded') is not True or claims.get('protection') != 'HOSTED' or
                not isinstance(claims.get('official_contract_revision'),str) or not claims['official_contract_revision'] or
                not isinstance(checks,dict) or set(checks) != QualificationRun.REQUIRED or
                any(not isinstance(v,dict) or v.get('state') != 'PASS' or
                    not isinstance(v.get('evidence_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',v['evidence_sha256']) for v in checks.values())):
            raise DemoError('DEMO_QUALIFICATION_INCOMPLETE')
        baseline=claims.get('baseline')
        if not isinstance(baseline,dict) or not baseline or len(baseline)>1000:
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        for asset,value in baseline.items():
            if not re.fullmatch('[A-Z0-9]{1,20}',asset): raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
            decimal(value)
        if not decimal(claims.get('budget'),positive=True) <= min(decimal(baseline.get('USDT','0')),decimal('10000')):
            raise DemoError('DEMO_PROJECT_BUDGET_EXCEEDED')
        return claims

    def install(self,scope,envelope,*,generation):
        from .demo import accounts,authority_proofs
        bindings=self.bindings(scope,generation=generation)
        payload=self.trust.verify(envelope,purpose='qualification',bindings=bindings,now=int(self.clock()))
        claims=self._claims(payload)
        with self.repository.safety_guard(scope), self.repository.transaction(scope.account_key) as conn:
            account=conn.execute(select(accounts).where(accounts.c.account_key==scope.account_key)).mappings().one()
            if (account['credential_revision'] != bindings['credential_revision'] or account['fingerprint'] != bindings['fingerprint'] or
                    account['frozen'] or (account['balances'] is not None and account['balances'] != claims['baseline'])):
                raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
            if conn.execute(select(authority_proofs.c.nonce).where(authority_proofs.c.nonce==payload['nonce'])).first():
                raise DemoError('DEMO_PROOF_REPLAY')
            conn.execute(insert(authority_proofs).values(nonce=payload['nonce'],account_key=scope.account_key,
                purpose='qualification',envelope=envelope,revoked=False,consumed_at=int(self.clock())))
            evidence={'state':'DEMO_VERIFIED','code_sha':self.code_sha,'account_key':scope.account_key,
                'credential_revision':account['credential_revision'],'product':'SPOT','protection':'HOSTED',
                'history_coverage':'BOUNDED_DEDICATED_V1','policy':claims['policy'],'proof_nonce':payload['nonce'],
                'baseline_hash':digest(claims['baseline']),'evidence_sha256':digest(envelope),
                'official_contract_revision':claims['official_contract_revision'],
                'capabilities':{kind:{'state':'DEMO_VERIFIED','evidence_sha256':digest(envelope),
                    'official_contract_revision':claims['official_contract_revision']} for kind in ('LIMIT','CANCEL')}}
            conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(
                qualification=evidence,balances=claims['baseline'],budget=str(min(decimal(account['budget']),decimal(claims['budget']))),
                state='RECOVERY_REQUIRED',revision=account['revision']+1))
            self.repository._event(conn,scope.account_key,'AUTHORITY_INSTALLED',payload['nonce'],{'proof_hash':digest(envelope)})
        return evidence

    def require(self,scope,*,generation,cancel=False):
        from .demo import authority_proofs
        account=self.repository.account(scope)
        evidence=account.get('qualification') or {}
        with self.repository.engine.connect() as conn:
            row=conn.execute(select(authority_proofs).where(authority_proofs.c.nonce==evidence.get('proof_nonce'),
                authority_proofs.c.account_key==scope.account_key)).mappings().first()
        allowed = {'DEMO_VERIFIED','INVALIDATED_BALANCE'} if cancel else {'DEMO_VERIFIED'}
        if not row or row['revoked'] or evidence.get('state') not in allowed or digest(row['envelope'])!=evidence.get('evidence_sha256'):
            raise DemoError('DEMO_QUALIFICATION_REQUIRED')
        payload=self.trust.verify(row['envelope'],purpose='qualification',bindings=self.bindings(scope,generation=generation),now=int(self.clock()))
        claims=self._claims(payload)
        if cancel:
            capability=(evidence.get('capabilities') or {}).get('CANCEL') or {}
            if capability.get('state')!='DEMO_VERIFIED' or capability.get('evidence_sha256')!=digest(row['envelope']):
                raise DemoError('DEMO_QUALIFICATION_REQUIRED')
            return claims
        if digest(account['balances']) != digest(claims['baseline']) or decimal(account['budget'])>decimal(claims['budget']):
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        return claims

    def revoke(self,scope,nonce):
        from .demo import authority_proofs,accounts
        with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
            row=conn.execute(select(authority_proofs).where(authority_proofs.c.nonce==nonce,authority_proofs.c.account_key==scope.account_key)).first()
            if row is None: raise DemoError('DEMO_PROOF_NOT_FOUND')
            conn.execute(update(authority_proofs).where(authority_proofs.c.nonce==nonce).values(revoked=True))
            conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(state='PAUSED',revision=accounts.c.revision+1))
            self.repository._event(conn,scope.account_key,'AUTHORITY_REVOKED',nonce,{'nonce':nonce})

    def consume_recovery(self,scope,envelope,*,credential_ref,fingerprint,physical_owner=None):
        """Caller acquired physical lock; consume external termination/revocation proof.

        No pending observation, command or reservation is discarded. Rebinding
        is not reconciliation and never produces READY or ordinary permission.
        """
        import re
        from .demo import accounts,authority_proofs,physical_owners,decisions
        if self.repository.engine.dialect.name=='postgresql':
            if physical_owner is None: raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
            physical_owner.validate(scope)
        account=self.repository.account(scope)
        with self.repository.engine.connect() as conn:
            previous=conn.execute(select(physical_owners).where(physical_owners.c.physical_key==scope.physical_account_key)).mappings().first()
        if previous is None or previous['account_key']!=scope.account_key or fingerprint==account['fingerprint']:
            raise DemoError('DEMO_PHYSICAL_RECOVERY_REQUIRED')
        bindings={**self.bindings(scope,generation=previous['generation']),'new_fingerprint':fingerprint}
        payload=self.trust.verify(envelope,purpose='recovery',bindings=bindings,now=int(self.clock()))
        claims=payload['claims']
        if (claims.get('old_process_terminated') is not True or claims.get('old_credentials_revoked') is not True or
                not isinstance(claims.get('evidence_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',claims['evidence_sha256'])):
            raise DemoError('DEMO_RECOVERY_PROOF_REQUIRED')
        with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
            if physical_owner is not None: physical_owner.validate(scope)
            current=conn.execute(select(accounts).where(accounts.c.account_key==scope.account_key)).mappings().one()
            owner=conn.execute(select(physical_owners).where(physical_owners.c.physical_key==scope.physical_account_key).with_for_update()).mappings().one()
            if (dict(owner)!=dict(previous) or current['revision']!=account['revision'] or current['frozen'] or
                    conn.execute(select(authority_proofs.c.nonce).where(authority_proofs.c.nonce==payload['nonce'])).first()):
                raise DemoError('DEMO_RECOVERY_STATE_CHANGED')
            generation=previous['generation']+1
            conn.execute(insert(authority_proofs).values(nonce=payload['nonce'],account_key=scope.account_key,
                purpose='recovery',envelope=envelope,revoked=False,consumed_at=int(self.clock())))
            conn.execute(update(physical_owners).where(physical_owners.c.physical_key==scope.physical_account_key).values(
                generation=generation,state='DIRTY',clean_evidence=None))
            conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(
                credential_ref=credential_ref,fingerprint=fingerprint,credential_revision=current['credential_revision']+1,
                revision=current['revision']+1,state='RECOVERY_REQUIRED',qualification=None))
            conn.execute(update(decisions).where(decisions.c.account_key==scope.account_key,decisions.c.state=='IN_FLIGHT').values(
                state='FAILED',error='DEMO_RECOVERY_INTERRUPTED'))
            self.repository._event(conn,scope.account_key,'PHYSICAL_RECOVERY',payload['nonce'],
                {'proof_hash':digest(envelope),'previous_generation':previous['generation'],'generation':generation,'execution_authorized':False})
        return generation

    def historical_approval(self,scope,observation,*,generation):
        """Exact facts reviewed by the independent recovery issuer, not a reset."""
        from .demo import authority_proofs
        account=self.repository.account(scope)
        with self.repository.engine.connect() as conn:
            records=conn.execute(select(authority_proofs).where(authority_proofs.c.account_key==scope.account_key,
                authority_proofs.c.purpose=='recovery',authority_proofs.c.revoked.is_(False)).limit(101)).mappings().all()
        if len(records)>100: raise DemoError('DEMO_RECOVERY_PROOF_BUDGET')
        for row in records:
            payload=row['envelope']['payload']; binding=payload['bindings']
            if (binding.get('generation')!=generation-1 or binding.get('credential_revision')!=account['credential_revision']-1 or
                    binding.get('new_fingerprint')!=account['fingerprint'] or binding.get('build')!=self.build_digest or
                    binding.get('account_key')!=scope.account_key or observation['credential_revision']!=binding['credential_revision'] or
                    observation['stream_generation']!=binding['generation']): continue
            verified=self.trust.verify(row['envelope'],purpose='recovery',bindings=binding,now=int(self.clock()))
            reviewed=verified['claims'].get('observation_hashes')
            if (isinstance(reviewed,dict) and reviewed.get(observation['observation_id'])==observation['payload_hash'] and
                    digest(observation['payload'])==observation['payload_hash']): return payload['nonce']
        raise DemoError('DEMO_HISTORICAL_OBSERVATION_REVIEW_REQUIRED')

    def revalidate_observations(self,scope,*,generation):
        from .demo import observations,inbox
        from trading_system.execution.demo_reports import DemoReportVerifier
        from trading_system.engine.binance_demo.contracts import DemoScope
        account=self.repository.account(scope)
        with self.repository.engine.connect() as conn:
            rows=conn.execute(select(observations).join(inbox,observations.c.observation_id==inbox.c.observation_id).where(
                observations.c.account_key==scope.account_key,inbox.c.state!='PROCESSED',
                observations.c.credential_revision!=account['credential_revision']).limit(1001)).mappings().all()
        if len(rows)>1000: raise DemoError('DEMO_INBOX_DRAIN_LIMIT')
        verifier=DemoReportVerifier(self.repository)
        for row in rows:
            proof=self.historical_approval(scope,row,generation=generation)
            if row['source']=='WS_ACCOUNT_POSITION': continue  # Validated against order history/snapshot by the policy.
            if row['source'] not in {'REST_ORDER','REST_TRADES','WS_EXECUTION_REPORT'}:
                raise DemoError('DEMO_HISTORICAL_OBSERVATION_REVIEW_REQUIRED')
            owner_scope=DemoScope(**row['scope'])
            replacement=self.repository.observe(owner_scope,source=row['source'],payload=row['payload'],
                received_at=int(self.clock()),credential_ref=account['credential_ref'],
                credential_revision=account['credential_revision'],stream_generation=generation)
            verifier.process(owner_scope,replacement)
            with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
                state=conn.execute(select(inbox.c.state).where(inbox.c.observation_id==replacement)).scalar_one()
                if state!='PROCESSED': raise DemoError('DEMO_RECOVERY_REVALIDATION_REQUIRED')
                conn.execute(update(inbox).where(inbox.c.observation_id==row['observation_id']).values(state='PROCESSED',reason='SIGNED_RECOVERY_REVALIDATED'))
                self.repository._event(conn,scope.account_key,'RECOVERY_REVALIDATED',row['observation_id'],
                    {'original':row['observation_id'],'replacement':replacement,'proof_nonce':proof})

    def responsibilities(self,scope,*,conn=None):
        from .demo import commands,protection,accounts,strategies,decisions
        if conn is None:
            with self.repository.engine.connect() as connection: return self.responsibilities(scope,conn=connection)
        account=conn.execute(select(accounts).where(accounts.c.account_key==scope.account_key)).mappings().one()
        orders=[dict(r) for r in conn.execute(select(commands).where(commands.c.account_key==scope.account_key).order_by(commands.c.command_id)).mappings()]
        duties=[dict(r) for r in conn.execute(select(protection).where(protection.c.account_key==scope.account_key).order_by(protection.c.fill_key)).mappings()]
        grants=[dict(r) for r in conn.execute(select(strategies).where(strategies.c.account_key==scope.account_key).order_by(strategies.c.scope_key)).mappings()]
        active=conn.execute(select(decisions.c.run_id).where(decisions.c.account_key==scope.account_key,decisions.c.state=='IN_FLIGHT')).first()
        payload={'account_key':scope.account_key,'revision':account['revision'],'orders':orders,'duties':duties,'grants':grants,'model_in_flight':bool(active)}
        return {'state':'NEEDS_OPERATOR','digest':digest(payload),'orders':[{'command_id':r['command_id'],'state':r['state'],'order_status':r['order_status']} for r in orders],
                'duties':[{'fill_key':r['fill_key'],'state':r['state'],'quantity':r['remaining_quantity']} for r in duties],
                'model_in_flight':bool(active),'flat_account_verified':False}

    def acknowledge_takeover(self,scope,expected_digest):
        from .demo import accounts,strategies
        with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
            current=self.responsibilities(scope,conn=conn)
            if current['digest']!=expected_digest or current['model_in_flight']: raise DemoError('DEMO_TAKEOVER_STATE_CHANGED')
            for row in conn.execute(select(strategies).where(strategies.c.account_key==scope.account_key)).mappings():
                config={**row['config'],'revision':row['revision']+1,'authorized':False,'allow_execution':False,'allow_model_calls':False}
                conn.execute(update(strategies).where(strategies.c.scope_key==row['scope_key']).values(config=config,revision=row['revision']+1))
            conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(state='PAUSED',revision=accounts.c.revision+1))
            receipt={**current,'state':'TAKEOVER_ACKNOWLEDGED','acknowledged_at':int(self.clock()),'execution_authorized':False}
            self.repository._event(conn,scope.account_key,'TAKEOVER_ACKNOWLEDGED',expected_digest,receipt)
        return receipt
