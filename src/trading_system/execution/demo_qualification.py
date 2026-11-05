"""Independent bounded acceptance runner, never invoked by serve or strategy AI.

An external operator signs exact requests and accepts exposure responsibility.
The runner retains original IDs, MAY/UNKNOWN and normal report accounting. It
cannot sign or install ordinary capability qualification. No automatic retries.
"""
import re
import time
from sqlalchemy import select,insert
from trading_system.engine.binance_demo.contracts import DemoError,digest,decimal
from trading_system.storage.demo import authority_proofs,outbox
from .demo_commands import DemoSendPermit,_OWNER


class QualificationExecutor:
    def __init__(self,repository,transport,authority,scope,envelope,*,physical_owner,stream,clock=time.time):
        self.repository,self.transport,self.authority,self.scope,self.envelope=repository,transport,authority,scope,envelope
        self.owner,self.stream,self.clock=physical_owner,stream,clock

    def _verify(self,generation):
        payload=self.authority.trust.verify(self.envelope,purpose='qualification_run',
            bindings=self.authority.bindings(self.scope,generation=generation),now=int(self.clock()))
        claims=payload['claims']
        if (type(claims.get('max_commands')) is not int or not 1<=claims['max_commands']<=4 or
                not isinstance(claims.get('requests'),dict) or not 1<=len(claims['requests'])<=claims['max_commands'] or
                claims.get('operator_responsibility') is not True or
                not isinstance(claims.get('pg_fencing_evidence'),str) or not re.fullmatch('[0-9a-f]{64}',claims['pg_fencing_evidence']) or
                decimal(claims.get('max_notional'),positive=True)>100):
            raise DemoError('DEMO_QUALIFICATION_BUDGET_INVALID')
        return payload

    def reserve(self,method,request,*,generation):
        payload=self._verify(generation); claims=payload['claims']
        if method=='POST':
            if (set(request)!={'symbol','type','side','quantity','price','timeInForce','newClientOrderId'} or
                    request['symbol']!='BTCUSDT' or request['type']!='LIMIT' or request['side']!='BUY' or request['timeInForce']!='GTC'):
                raise DemoError('DEMO_PROBE_REQUEST_INVALID')
            original=request['newClientOrderId']; amount=decimal(request['quantity'],positive=True)*decimal(request['price'],positive=True)*decimal('1.01')
        elif method=='DELETE':
            if set(request)!={'symbol','origClientOrderId'} or request['symbol']!='BTCUSDT': raise DemoError('DEMO_PROBE_REQUEST_INVALID')
            original='cancel:'+request['origClientOrderId']; amount=decimal('0')
        else: raise DemoError('DEMO_PROBE_REQUEST_INVALID')
        if claims['requests'].get(original)!=digest([method,request]): raise DemoError('DEMO_PROBE_REQUEST_NOT_AUTHORIZED')
        with self.repository.safety_guard(self.scope),self.repository.transaction(self.scope.account_key) as conn:
            existing=conn.execute(select(authority_proofs).where(authority_proofs.c.nonce==payload['nonce'])).mappings().first()
            if existing:
                if existing['revoked'] or digest(existing['envelope'])!=digest(self.envelope): raise DemoError('DEMO_PROOF_REPLAY')
            else:
                conn.execute(insert(authority_proofs).values(nonce=payload['nonce'],account_key=self.scope.account_key,
                    purpose='qualification_run',envelope=self.envelope,revoked=False,consumed_at=int(self.clock())))
            rows=conn.execute(select(outbox.c.payload).where(outbox.c.account_key==self.scope.account_key,outbox.c.kind=='QUALIFICATION_PROBE')).scalars().all()
            used=[row for row in rows if row['nonce']==payload['nonce']]
            if (any(row['original']==original for row in used) or len(used)>=claims['max_commands'] or
                    sum(decimal(row['reserved']) for row in used)+amount>decimal(claims['max_notional'])):
                raise DemoError('DEMO_QUALIFICATION_BUDGET_EXHAUSTED')
            receipt={'nonce':payload['nonce'],'original':original,'request_hash':digest([method,request]),'reserved':str(amount)}
            self.repository._event(conn,self.scope.account_key,'QUALIFICATION_PROBE',digest(receipt),receipt)
        return receipt

    def execute(self,method,request):
        if self.repository.engine.dialect.name!='postgresql' or self.owner is None:
            raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        scope=self.scope
        with self.repository.owner_guard(scope) as sender:
            def guard():
                self.owner.validate(scope); sender.validate()
                payload=self._verify(self.owner.generation)
                account=self.repository.account(scope)
                if (self.stream is None or not self.stream.healthy or self.stream.scope.account_key!=scope.account_key or
                        self.stream.physical_generation!=self.owner.generation or self.stream.credential_revision!=account['credential_revision'] or
                        self.transport.credential_ref!=account['credential_ref'] or self.stream.credential_ref!=account['credential_ref'] or account['frozen']):
                    raise DemoError('DEMO_CONNECTED_STREAM_REQUIRED')
                with self.repository.engine.connect() as conn:
                    row=conn.execute(select(authority_proofs.c.revoked).where(authority_proofs.c.nonce==payload['nonce'])).first()
                if row and row[0]: raise DemoError('DEMO_ATTESTATION_INVALID')
            guard()
            if method=='POST':
                if self.repository.pending_commands(scope) or self.repository.health(scope)['pending_inbox']:
                    raise DemoError('DEMO_RECOVERY_REQUIRED')
                from trading_system.engine.binance_demo.orders import compile_spot_order
                info=self.transport.exchange_info('BTCUSDT')
                symbols=info.get('symbols',[])
                if len(symbols)!=1: raise DemoError('DEMO_SYMBOL_UNBOUND')
                rules={**symbols[0],'exchangeFilters':info.get('exchangeFilters',[])}
                compiled=compile_spot_order(rules,side='BUY',order_type='LIMIT',quantity=request.get('quantity'),
                    client_id=request.get('newClientOrderId'),reference_price=request.get('price'),price=request.get('price'))
                if compiled!=request: raise DemoError('DEMO_PROBE_REQUEST_INVALID')
            elif method=='DELETE':
                targets=[row for row in self.repository.pending_commands(scope) if row['scope_key']==scope.key and
                    row['client_id']==request.get('origClientOrderId') and row['authorization_ref']==self.envelope['payload']['nonce']]
                if len(targets)!=1: raise DemoError('DEMO_CANCEL_TARGET_INVALID')
            guard(); receipt=self.reserve(method,request,generation=self.owner.generation)
            command_id='probe:'+digest(receipt)
            account=self.repository.account(scope)
            self.repository.stage(scope,command_id=command_id,request=request,method=method,purpose='ENTRY' if method=='POST' else 'CANCEL',
                reserve_asset='USDT',reserve_amount=receipt['reserved'],available=self.envelope['payload']['claims']['max_notional'],
                authorization_ref=self.envelope['payload']['nonce'],expires_at=min(int(self.clock())+60,self.envelope['payload']['expires_at']),now=int(self.clock()))
            self.transport.sync_time(); guard()
            self.repository.begin_send(scope,command_id,expected_revision=account['revision'],now=int(self.clock()),validator=lambda _:guard())
            permit=DemoSendPermit(_owner=_OWNER,method=method,request=request,credential_ref=account['credential_ref'],preflight=guard)
            try:
                response=(self.transport.send_order if method=='POST' else self.transport.cancel_order)(request,permit=permit)
                guard(); self.repository.acknowledge(scope,command_id,response,validator=guard)
            except Exception:
                self.repository.mark_unknown(scope,command_id,'DEMO_QUALIFICATION_OUTCOME_UNKNOWN')
                raise DemoError('DEMO_QUALIFICATION_OUTCOME_UNKNOWN') from None
            return self.repository.command(scope,command_id)
