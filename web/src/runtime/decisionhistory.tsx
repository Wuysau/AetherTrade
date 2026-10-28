import {useEffect,useRef,useState,type ReactNode} from 'react';
import type {Decision} from './types';
import {Empty} from './ui';

export function DecisionHistory({decisions,selectedId,selectionVersion,renderItem}:{decisions:Decision[];selectedId?:string|null;selectionVersion?:number;renderItem:(d:Decision)=>ReactNode}) {
  const [size,setSize]=useState(10),[page,setPage]=useState(0);
  const scroll=useRef<HTMLDivElement>(null);
  const pages=Math.max(1,Math.ceil(decisions.length/size));
  const current=Math.min(page,pages-1);
  useEffect(()=>{
    const index=decisions.findIndex(d=>d.id===selectedId);
    if(index>=0) setPage(Math.floor(index/size));
  },[selectedId,selectionVersion]);
  useEffect(()=>{ if(scroll.current) scroll.current.scrollTop=0; },[current,size]);
  return <><div className="pane-list decision-history-scroll" role="region" aria-label="决策流水记录" tabIndex={0} ref={scroll}>
    {decisions.length ? decisions.slice(current*size,(current+1)*size).map(renderItem) : <Empty title="暂无 AI 决策" text="尚无已保存的模型分析。"/>}
  </div><nav className="decision-pagination" aria-label="决策流水分页">
    <label>每页 <select aria-label="每页记录数" value={size} onChange={e=>{setSize(Number(e.target.value));setPage(0);}}>{[10,20,50].map(n=><option key={n} value={n}>{n}</option>)}</select> 条</label>
    <span aria-live="polite">{current+1} / {pages} 页 · {decisions.length} 条</span>
    <div className="action-strip"><button type="button" className="button small" disabled={current===0} onClick={()=>setPage(current-1)}>上一页</button><button type="button" className="button small" disabled={current===pages-1} onClick={()=>setPage(current+1)}>下一页</button></div>
  </nav></>;
}
