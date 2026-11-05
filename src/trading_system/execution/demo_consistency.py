"""Qualified conservative single-writer policy; NOT a Binance WS sequence barrier.

Only independently attested dedicated accounts may use this policy. It reserves
the full risk of uncertain sends and admits no overlapping risk-increasing order.
"""
import time
from uuid import uuid4
from sqlalchemy import select, update
from trading_system.engine.binance_demo.contracts import DemoError, digest, decimal
from trading_system.engine.binance_demo.account_events import parse_account_position
from trading_system.storage.demo import (accounts,commands,observations,inbox,fills,
    protection,decisions,final_activity_digest,require_physical_generation,unresolved_trade_history)
from .demo_reconcile import DemoReconciler

TERMINAL={'FILLED','CANCELED','EXPIRED','REJECTED','EXPIRED_IN_MATCH'}


class SerializedAccountPolicy:
    def __init__(self,repository,transport,authority,*,clock=time.time):
        self.repository,self.transport,self.authority,self.clock=repository,transport,authority,clock

    def _stream(self,scope,stream):
        account=self.repository.account(scope)
        if (not stream.healthy or stream.scope.account_key!=scope.account_key or
                not getattr(stream,'active_run_nonce',None) or type(stream.physical_generation) is not int or
                stream.physical_generation<1 or stream.credential_ref!=account['credential_ref'] or
                stream.credential_ref!=self.transport.credential_ref or stream.credential_revision!=account['credential_revision']):
            raise DemoError('DEMO_CONSISTENCY_STREAM_INVALID')
        stream.validate_owner()
        self.authority.require(scope,generation=stream.physical_generation)
        return account

    def _activity(self,scope):
        with self.repository.engine.connect() as conn: return final_activity_digest(conn,scope)

    def prepare(self,scope,*,stream,command_id=None):
        with self.repository.safety_guard(scope):
            account=self._stream(scope,stream)
            started=int(self.clock()); before=self._activity(scope)
            self.transport.sync_time()
            raw=self.transport.account()
            opened=self.transport.open_orders(None)
            cursor=self.repository.cursor(scope,'BTCUSDT')
            if not cursor['complete'] or cursor['cursor'].get('started_at_zero') is not True:
                raise DemoError('DEMO_HISTORY_CURSOR_UNVERIFIED')
            page=self.transport.trades('BTCUSDT',from_id=cursor['cursor']['from_id'],limit=1000)
            self._stream(scope,stream)
            if (type(raw) is not dict or type(raw.get('uid')) is not int or str(raw['uid'])!=scope.account_id or
                    raw.get('accountType')!='SPOT' or raw.get('canTrade') is not True or not isinstance(raw.get('balances'),list)):
                raise DemoError('DEMO_ACCOUNT_BINDING_REJECTED')
            if not isinstance(opened,list) or opened: raise DemoError('DEMO_OPEN_ORDER_RECONCILIATION_REQUIRED')
            if not isinstance(page,list) or len(page)>=1000: raise DemoError('DEMO_BACKFILL_INCOMPLETE')
            if cursor['cursor'].get('overlap_required') and (not page or page[0].get('id')!=cursor['cursor']['from_id']):
                raise DemoError('DEMO_BACKFILL_OVERLAP_LOST')
            balances={}
            for row in raw['balances']:
                if not isinstance(row,dict) or not isinstance(row.get('asset'),str) or row['asset'] in balances:
                    raise DemoError('DEMO_BALANCE_DUPLICATE')
                balances[row['asset']]=(decimal(row.get('free')),decimal(row.get('locked')))
            deltas=self.repository.account_deltas(scope)
            for asset in set(balances)|set(account['balances'])|set(deltas):
                if sum(balances.get(asset,(decimal('0'),))) != decimal(account['balances'].get(asset,'0'))+deltas.get(asset,0):
                    raise DemoError('DEMO_BALANCE_MISMATCH')
            with self.repository.transaction(scope.account_key) as conn:
                if unresolved_trade_history(conn,scope.account_key): raise DemoError('DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED')
                if before!=final_activity_digest(conn,scope) or not 0<=int(self.clock())-started<=5:
                    raise DemoError('DEMO_CONSISTENCY_RACED')
                if self.repository.engine.dialect.name=='postgresql': require_physical_generation(conn,scope,stream.physical_generation)
                # Any new/changed trade requires the normal original-order reducer.
                seen=set()
                for trade in page:
                    if type(trade) is not dict or type(trade.get('id')) is not int or trade['id'] in seen:
                        raise DemoError('DEMO_TRADE_RECONCILIATION_REQUIRED')
                    seen.add(trade['id'])
                    local=conn.execute(select(fills).where(fills.c.account_key==scope.account_key,fills.c.symbol=='BTCUSDT',fills.c.trade_id==str(trade['id']))).mappings().first()
                    if local is None: raise DemoError('DEMO_TRADE_RECONCILIATION_REQUIRED')
                    fact=local['payload']
                    if (str(trade.get('orderId'))!=fact['order_id'] or trade.get('commissionAsset')!=fact['commission_asset'] or
                            type(trade.get('isBuyer')) is not bool or trade['isBuyer']!=(fact['side']=='BUY') or
                            any(decimal(trade.get(a))!=decimal(fact[b]) for a,b in [('qty','quantity'),('price','price'),('quoteQty','quote_quantity'),('commission','commission')])):
                        raise DemoError('DEMO_TRADE_RECONCILIATION_REQUIRED')
                active=[]
                for row in conn.execute(select(commands).where(commands.c.account_key==scope.account_key)).mappings():
                    if row['state']=='ABANDONED': continue
                    if row['state'] in {'MAY_HAVE_SENT','UNKNOWN'} or row['reserve_amount']!='0' or row['order_status'] not in TERMINAL:
                        active.append(row)
                if any(row['command_id']!=command_id for row in active): raise DemoError('DEMO_SERIAL_ENTRY_REQUIRED')
                if command_id is not None:
                    if len(active)!=1 or active[0]['state']!='DURABLE_READY' or active[0]['scope_key']!=scope.key or active[0]['purpose']!='ENTRY':
                        raise DemoError('DEMO_SERIAL_ENTRY_REQUIRED')
                    if decimal(active[0]['reserve_amount'])>min(decimal(account['budget']),balances.get('USDT',(decimal('0'),))[0]):
                        raise DemoError('DEMO_PROJECT_BUDGET_EXCEEDED')
                pending=conn.execute(select(observations).join(inbox,observations.c.observation_id==inbox.c.observation_id).where(
                    observations.c.account_key==scope.account_key,inbox.c.state!='PROCESSED')).mappings().all()
                for row in pending:
                    if row['source']!='WS_ACCOUNT_POSITION' or digest(row['payload'])!=row['payload_hash']:
                        raise DemoError('DEMO_INBOX_RECONCILIATION_REQUIRED')
                    recovery_proof=None
                    if row['credential_revision']!=stream.credential_revision or row['stream_generation']!=stream.physical_generation:
                        recovery_proof=self.authority.historical_approval(scope,row,generation=stream.physical_generation)
                    event=row['payload']; partial=parse_account_position(event)
                    if (type(raw.get('updateTime')) is not int or event['u']>raw['updateTime'] or
                            event['E']>int(self.clock()*1000)+5000):
                        raise DemoError('DEMO_ACCOUNT_POSITION_MISMATCH')
                    supporting=[]
                    if any(balances.get(k)!=v for k,v in partial.items()):
                        from .demo_balance_history import justify_position
                        supporting=justify_position(conn,scope,event,account['balances'])
                    self.repository._event(conn,scope.account_key,'SERIAL_POSITION_REVIEW',row['observation_id'],
                        {'observation_id':row['observation_id'],'supporting_order_observations':supporting,'snapshot_hash':digest(raw),
                         'policy':'serialized-account-v1','atomic_stream_barrier':False,'recovery_proof':recovery_proof})
                health=self.repository.health(scope,now=int(self.clock()))
                if health['unknown'] or health['unprotected'] or health['risk_breaches']: raise DemoError('DEMO_RECOVERY_REQUIRED')
                for row in pending:
                    conn.execute(update(inbox).where(inbox.c.observation_id==row['observation_id']).values(state='PROCESSED',reason='SERIALIZED_ACCOUNT_V1'))
                receipt={'policy':'serialized-account-v1','atomic_stream_barrier':False,'account_key':scope.account_key,
                    'physical_key':scope.physical_account_key,'physical_generation':stream.physical_generation,
                    'credential_revision':stream.credential_revision,'account_revision':account['revision'],
                    'stream_run_nonce':stream.active_run_nonce,'subscription_id':stream.subscription_id,
                    'qualification_hash':digest(account['qualification']),'checked_at':started,
                    'activity_hash':final_activity_digest(conn,scope),'command_id':command_id}
                self.repository._event(conn,scope.account_key,'SERIAL_ACCOUNT_CHECK',uuid4().hex,receipt)
            return receipt

    def validate(self,receipt,scope,*,stream):
        account=self._stream(scope,stream)
        if (receipt['account_key']!=scope.account_key or receipt['physical_generation']!=stream.physical_generation or
                receipt['account_revision']!=account['revision'] or receipt['stream_run_nonce']!=stream.active_run_nonce or
                receipt['qualification_hash']!=digest(account['qualification']) or not 0<=int(self.clock())-receipt['checked_at']<=5 or
                receipt['activity_hash']!=self._activity(scope)):
            raise DemoError('DEMO_CONSISTENCY_RECEIPT_STALE')

    def resume(self,scope,*,stream):
        self._stream(scope,stream)
        self.authority.revalidate_observations(scope,generation=stream.physical_generation)
        progress=DemoReconciler(self.repository,self.transport,clock=self.clock).reconcile(scope,symbol='BTCUSDT',stream=stream,review_positions=False)
        if not progress['pages_complete']: raise DemoError('DEMO_BACKFILL_INCOMPLETE')
        receipt=self.prepare(scope,stream=stream)
        with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
            self.validate(receipt,scope,stream=stream)
            conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key,accounts.c.revision==receipt['account_revision']).values(
                state='READY',revision=receipt['account_revision']+1))
            self.repository._event(conn,scope.account_key,'SERIAL_ACCOUNT_READY',uuid4().hex,receipt)
        return {'state':'READY','execution_authorized':False,'receipt':receipt}


class QualifiedReconciler(DemoReconciler):
    def __init__(self,policy):
        super().__init__(policy.repository,policy.transport,clock=policy.clock)
        self.policy=policy

    def reconcile(self,scope,**kwargs):
        self.policy.authority.revalidate_observations(scope,generation=kwargs['stream'].physical_generation)
        kwargs['review_positions']=False
        return super().reconcile(scope,**kwargs)

    def reconcile_and_resume(self,scope,*,stream,current_code_sha):
        if current_code_sha!=self.policy.authority.code_sha: raise DemoError('DEMO_BUILD_ATTESTATION_REQUIRED')
        return self.policy.resume(scope,stream=stream)

    def reconcile_for_clean(self,scope,*,stream,current_code_sha):
        self.reconcile_and_resume(scope,stream=stream,current_code_sha=current_code_sha)
        receipt=self.policy.prepare(scope,stream=stream)
        with self.repository.safety_guard(scope),self.repository.transaction(scope.account_key) as conn:
            self.policy.validate(receipt,scope,stream=stream)
            if (self.repository.account_deltas(scope).get('BTC',0)!=0 or
                    conn.execute(select(protection.c.fill_key).where(protection.c.account_key==scope.account_key,protection.c.state!='SATISFIED')).first() or
                    conn.execute(select(decisions.c.run_id).where(decisions.c.account_key==scope.account_key,decisions.c.state=='IN_FLIGHT')).first()):
                raise DemoError('DEMO_OPEN_EXPOSURE_REVIEW_REQUIRED')
            receipt={**receipt,'nonce':uuid4().hex}
            self.repository._event(conn,scope.account_key,'FINAL_RECONCILIATION',receipt['nonce'],receipt)
        return receipt
