"""Explicit synthetic research evidence and model plans for durable PAPER tests."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path

PROFILE=dict(decision_interval='1h',holding_period_seconds=3600,allowed_operations=['SET_TARGET','HOLD'],required=['quote','candles:1h'],optional=['news','book'],windows={'1h':3})
class ResearchFeed:
    def __init__(self, snapshot): self._snapshot=snapshot
    def snapshot(self):
        return dict(self._snapshot(),source_id='fixture',source_generation=1,source_revision=1,venue='BINANCE',product='SPOT')
    async def candles(self,symbol,interval,limit):
        now=int(datetime.now(timezone.utc).timestamp()*1000); start=now//3600000*3600000
        price=next(q['price'] for q in self._snapshot()['quotes'] if q['symbol']==symbol)
        return dict(source_id='fixture',source_generation=1,product='SPOT',candles=[dict(time=start-i*3600000,close_time=start-(i-1)*3600000-1,closed=True,open=price,high=price,low=price,close=price,volume='20') for i in range(limit,0,-1)])

def plan(symbol, action, price, target):
    until=(datetime.now(timezone.utc)+timedelta(seconds=110)).isoformat()
    p=json.loads(Path('tests/fixtures/contracts/examples/decision-proposal.paper.json').read_text(encoding='utf-8'))
    p.update(instrument_ref='console-paper:'+symbol,action='SET_TARGET',valid_until=until,evidence_refs=['quote'])
    p['target']=dict(side='FLAT' if action=='SELL' else 'LONG',sizing='BASE_QUANTITY',value='0' if action=='SELL' else format(target,'f'))
    p['entry'].update(order_preference='MARKET',limit_price=None,maximum_entry_slippage_bps='20',entry_deadline=until,allow_partial=True)
    p['exit_plan'].update(stop_price=format(Decimal(price)*Decimal('.95'),'f'),stop_trigger='LAST',review_due_at=until)
    return dict(schema_revision='assessment_v1',summary='Fixture advice',proposal=None if action=='HOLD' else p,evidence_claims=[],watch_conditions=[],next_review_at=None)

def result(symbol, revision, key, context, action='BUY'):
    snap=context['analysis_snapshot'];price=Decimal(snap['market']['quote']['price'])
    target=(Decimal(snap['constraints'].get('maximum_target_notional','10000'))/(price*Decimal('1.002'))).quantize(Decimal('.000000000001'),rounding=ROUND_DOWN)
    # Repeated explicit target remains the same once held; no fee-based creep.
    position=snap['portfolio'].get('position')
    if position and action=='BUY': target=Decimal(position['quantity'])
    value=plan(symbol,action,price,target)
    return {'decision':dict(value,id=key,symbol=symbol,action=value['proposal']['action'] if value['proposal'] else 'HOLD',assessment_status='DECIDED',reason_code='FIXTURE',created_at=datetime.now(timezone.utc).isoformat(),data_as_of=context['quote']['as_of'],risk_status='NOT_EVALUATED',snapshot_id=snap['snapshot_id'],snapshot_hash=snap['snapshot_hash'],strategy_revision=snap['task_profile']['strategy_revision'],required_profile_ids=['default'],profile_revisions={'default':revision},decision_policy='unanimous',component_results=[dict(profile_id='default',config_revision=revision,snapshot_hash=snap['snapshot_hash'],status='SUCCEEDED',response=value)])}
