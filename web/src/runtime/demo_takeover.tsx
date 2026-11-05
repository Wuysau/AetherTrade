import { useEffect, useRef, useState } from 'react';
import { RuntimeApi, errorMessage } from './api';
import { useLocale } from './i18n';
import { Alert } from './ui';

interface Responsibilities {
  state: string; digest: string; flat_account_verified: false;
  orders: Array<{command_id:string; state:string; order_status:string | null}>;
  duties: Array<{fill_key:string; state:string; quantity:string | null}>;
}

export function DemoTakeoverControls({api, generation, disabled}: {api:RuntimeApi; generation:string; disabled:boolean}) {
  const {locale}=useLocale();
  const text=(zh:string,en:string)=>locale==='en'?en:zh;
  const [view,setView]=useState<Responsibilities | null>(null), [ack,setAck]=useState(false);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [result,setResult]=useState('');
  const version=useRef(0), inFlight=useRef(false);
  useEffect(()=>{ ++version.current; inFlight.current=false; setBusy(false); setView(null); setAck(false); setResult(''); setError('');
    return ()=>{++version.current;}; },[generation,disabled]);
  async function run(confirm:boolean) {
    if(inFlight.current || disabled || (confirm && (!view || !ack))) return;
    inFlight.current=true; setBusy(true); setError(''); const current=++version.current;
    try {
      if(confirm) {
        const receipt=await api.request<{state:string}>('/demo/runtime/takeover',{method:'POST',
          body:JSON.stringify({expected_digest:view!.digest,acknowledge_responsibility:true})});
        if(current===version.current) {setResult(receipt.state);setView(null);setAck(false);}
      } else {
        const next=await api.request<Responsibilities>('/demo/runtime/takeover');
        if(current===version.current) {setView(next);setAck(false);setResult('');}
      }
    } catch(err) {
      if(current===version.current) {setError(errorMessage(err));setView(null);setAck(false);}
    } finally {if(current===version.current){inFlight.current=false;setBusy(false);}}
  }
  return <section aria-label={text('停止后的责任接管','Responsibility after stopping')}>
    <h3>{text('停止后的责任接管','Responsibility after stopping')}</h3>
    <p>{text('请先停止运行并核对订单和持仓。接管不表示订单已撤销或仓位已平；原策略不会因此恢复交易。',
      'Stop the runtime and review orders and positions first. Takeover does not cancel orders or flatten positions, and does not resume the strategy.')}</p>
    <button type="button" className="button" disabled={disabled || busy} onClick={()=>void run(false)}>{text('读取待接管责任','Read responsibilities')}</button>
    {error && <Alert error>{error}</Alert>}
    {result && <p role="status">{result}</p>}
    {view && <div>
      <ul>{view.orders.map(order=><li key={order.command_id}>{order.command_id} · {order.state} · {order.order_status ?? '—'}</li>)}</ul>
      <ul>{view.duties.map(duty=><li key={duty.fill_key}>{duty.state} · {duty.quantity ?? '—'} BTC</li>)}</ul>
      <label><input type="checkbox" checked={ack} disabled={disabled || busy} onChange={event=>setAck(event.target.checked)}/>{text('我已核对并接管所列未解决订单和持仓责任。','I have reviewed and accept responsibility for the listed unresolved orders and positions.')}</label>
      <button type="button" className="button" disabled={disabled || busy || !ack} onClick={()=>void run(true)}>{text('确认由我接管','Confirm takeover')}</button>
    </div>}
  </section>;
}
