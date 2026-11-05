"""Explain a historical partial balance frame using already verified venue facts.

No time-window guesses: a transaction at the frame's exact update time must be
present. Ambiguous/missing history stays blocked. This is not a stream barrier.
"""
from decimal import Decimal
from sqlalchemy import select
from trading_system.engine.binance_demo.contracts import DemoError,decimal
from trading_system.engine.binance_demo.account_events import parse_account_position
from trading_system.storage.demo import observations,inbox,commands,fills,postings


def justify_position(conn,scope,event,baseline):
    partial=parse_account_position(event); cutoff=event['u']
    rows=conn.execute(select(observations).join(inbox,observations.c.observation_id==inbox.c.observation_id).where(
        observations.c.account_key==scope.account_key,inbox.c.state=='PROCESSED',
        observations.c.source.in_(['WS_EXECUTION_REPORT','REST_ORDER','REST_TRADES'])).limit(2001)).mappings().all()
    if len(rows)>2000: raise DemoError('DEMO_BALANCE_HISTORY_BUDGET')
    trade_times={}; reports={}; evidence=[]; exact=False
    for row in rows:
        value=row['payload']; source=row['source']
        stamp=value.get('T') if source=='WS_EXECUTION_REPORT' else value.get('updateTime') if source=='REST_ORDER' else value.get('time')
        if type(stamp) is not int or stamp<0: continue
        trade=value.get('t') if source=='WS_EXECUTION_REPORT' else value.get('id') if source=='REST_TRADES' else None
        symbol=value.get('s') if source=='WS_EXECUTION_REPORT' else value.get('symbol')
        if type(trade) is int and trade>=0:
            key=(symbol,str(trade))
            if key in trade_times and trade_times[key]!=stamp: raise DemoError('DEMO_BALANCE_HISTORY_AMBIGUOUS')
            trade_times[key]=stamp
        if source=='REST_TRADES' or stamp>cutoff: continue
        order_id=str(value.get('i') if source=='WS_EXECUTION_REPORT' else value.get('orderId'))
        command=conn.execute(select(commands).where(commands.c.account_key==scope.account_key,
            commands.c.order_id==order_id,commands.c.method=='POST')).mappings().first()
        if command is None: continue
        status=value.get('X') if source=='WS_EXECUTION_REPORT' else value.get('status')
        cumulative=decimal(value.get('z') if source=='WS_EXECUTION_REPORT' else value.get('executedQty'))
        candidate=(stamp,status,cumulative,dict(command),row['observation_id'])
        old=reports.get(order_id)
        if old and old[0]==stamp and old[1:3]!=candidate[1:3]: raise DemoError('DEMO_BALANCE_HISTORY_AMBIGUOUS')
        if old is None or old[0]<stamp: reports[order_id]=candidate
        exact |= stamp==cutoff
    if not exact: raise DemoError('DEMO_ACCOUNT_POSITION_MISMATCH')
    totals={asset:decimal(value) for asset,value in baseline.items()}; filled={}
    all_fills=conn.execute(select(fills).where(fills.c.account_key==scope.account_key).limit(1001)).mappings().all()
    if len(all_fills)>1000: raise DemoError('DEMO_BALANCE_HISTORY_BUDGET')
    for fill in all_fills:
        timestamp=trade_times.get((fill['symbol'],fill['trade_id']))
        if timestamp is None: raise DemoError('DEMO_BALANCE_HISTORY_INCOMPLETE')
        if timestamp>cutoff: continue
        filled[fill['order_id']]=filled.get(fill['order_id'],Decimal(0))+decimal(fill['payload']['quantity'])
        for asset,delta in conn.execute(select(postings.c.asset,postings.c.delta).where(postings.c.fill_key==fill['fill_key'])):
            totals[asset]=totals.get(asset,Decimal(0))+Decimal(delta)
    locks={}
    for order_id,(_,status,cumulative,command,observation_id) in reports.items():
        if cumulative!=filled.get(order_id,Decimal(0)): raise DemoError('DEMO_BALANCE_HISTORY_INCOMPLETE')
        evidence.append(observation_id)
        if status in {'FILLED','CANCELED','REJECTED','EXPIRED','EXPIRED_IN_MATCH'}: continue
        if status not in {'NEW','PARTIALLY_FILLED'}: raise DemoError('DEMO_BALANCE_HISTORY_INCOMPLETE')
        request=command['request']
        if request.get('type')!='LIMIT' or request.get('symbol')!='BTCUSDT': raise DemoError('DEMO_BALANCE_HISTORY_INCOMPLETE')
        remaining=decimal(request['quantity'])-cumulative
        if remaining<0: raise DemoError('DEMO_BALANCE_HISTORY_INCOMPLETE')
        asset='USDT' if request['side']=='BUY' else 'BTC'
        locked=remaining*decimal(request['price']) if asset=='USDT' else remaining
        locks[asset]=locks.get(asset,Decimal(0))+locked
    for asset,(free,locked) in partial.items():
        expected_lock=locks.get(asset,Decimal(0))
        if locked!=expected_lock or free!=totals.get(asset,Decimal(0))-expected_lock:
            raise DemoError('DEMO_ACCOUNT_POSITION_MISMATCH')
    return sorted(evidence)
