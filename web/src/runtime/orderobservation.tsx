import {useEffect,useState} from 'react';
import {RuntimeApi,errorMessage} from './api';
import {Alert,Badge,Empty,Panel} from './ui';
import {RecordPagination} from './decisionhistory';
import type {ProgramView,ProgramPage,CapabilityView,ArchiveRecord} from './orderobservationtypes';
import { live, t } from './i18n';

const fieldNames:Record<string,string>=live(() => ({side:t("方向"),quantity:t("数量口径"),price:t("价格"),trigger:t("触发"),tif:t("在场期限"),post_only:t("只挂单"),iceberg_quantity:t("冰山展示量"),stp:t("自成交防护"),routing:t("路由"),position_side:t("持仓腿"),reduce_only:t("仅减仓"),trading_session:t("交易会话"),tokenize:t("代币化"),asset:t("资产"),held:t("保留金额（记录值）"),reflected:t("已反映金额（保留的子集）"),release_pending:t("待释放金额（保留的子集）"),sequence:t("记录序号"),kind:t("模式"),value:t("数值"),source:t("来源"),purpose:t("用途"),offset:t("偏移"),offset_unit:t("偏移单位"),activation_price:t("激活价格"),price_protect:t("价格保护"),expires_at:t("场所到期"),peg_type:t("挂钩类型"),offset_levels:t("偏移档数"),price_match:t("价格匹配"),source_node:t("到账来源节点"),submit_valid_until:t("首次提交期限"),management_policy:t("持续管理责任"),algorithm:t("执行算法"),execution_state:t("计划生命周期"),state_origin:t("状态来源"),parent_observation:t("场所母单回报"),protection_status:t("保护观察状态"),children_complete:t("子单清单完整性"),archived_snapshot:t("历史能力快照"),current_qualification:t("当前资格"),command_id:t("命令身份"),venue_client_id:t("原场所客户身份"),service_namespace:t("服务命名空间"),parent_command_id:t("应用母命令")}));
const value=(item:unknown):string=>item===null||item===undefined?t("未记录"):Array.isArray(item)?item.map(value).join(' / '):typeof item==='object'?Object.entries(item).filter(([,v])=>v!==null).map(([k,v])=>`${fieldNames[k]??k}: ${value(v)}`).join(' · '):typeof item==='boolean'?(item?t("是"):t("否")):String(item);
const fieldOrder=['side','quantity','price','trigger','tif'];
function Fields({fields}:{fields:Record<string,unknown>}) {const entries=Object.entries(fields).sort(([a],[b])=>(fieldOrder.includes(a)?fieldOrder.indexOf(a):99)-(fieldOrder.includes(b)?fieldOrder.indexOf(b):99));return <dl className="program-fields">{entries.map(([key,item])=><div key={key}><dt>{fieldNames[key]??key}</dt><dd>{value(item)}</dd></div>)}</dl>;}
function Archive({records,title}:{records:ArchiveRecord[];title:string}) {return <details className="program-archive"><summary>{title} · {records.length}</summary>{records.map(record=><div key={record.record_id}><p>{record.record_id}</p><pre>{JSON.stringify(record.payload,null,2)}</pre></div>)}</details>;}

export function OrderProgramDetail({detail}:{detail:ProgramView}) {
  const records=detail.records??{}, receipts=(records.financial??[]).filter(record=>record.record_id.startsWith('generic-program-admission:'));
  const state=detail.state_observation;
  const children=Array.isArray(state?.children)?state.children:[];
  return <article className="program-detail" aria-label={t("订单计划详情")}>
    <div className="action-strip"><Badge>{detail.contract_version}</Badge><Badge kind="wait">{t("只读观察")}</Badge><Badge>{detail.execution_mode??t("模式未解析")}</Badge></div>
    <h3>{detail.program_id}</h3>
    {detail.codec_status!=='DECODED'&&<Alert error>{t("未知或无效版本：保留原始记录，仅供只读核查。")}</Alert>}
    <section><h4>{t("原计划")}</h4><p className="field-help">{detail.template??t("模板未解析")}  {t("· 原参数保留，类型不会自动改为市价。")}</p>
      {detail.nodes.map(node=><section className="program-node" key={node.node_id}><h5>{node.node_id} · {node.role}</h5><p className="field-help">{node.child_origin}</p><Fields fields={node.order}/></section>)}
      <Fields fields={{submit_valid_until:detail.payload.submit_valid_until,management_policy:detail.payload.management_policy,algorithm:detail.payload.algorithm}}/>
    </section>
    <section><h4>{t("持久化接入回执")}</h4><p>{receipts.length?t("已记录接入回执"):t("未观察到接入回执")}</p><p className="field-help">{t("回执仅证明账本接入已提交；每次发送仍需当前授权与单次许可。")}</p>{receipts.map(record=><p className="program-id" key={record.record_id}>{record.record_id}</p>)}</section>
    <section><h4>{t("应用命令与原身份")}</h4>{(records.commands??[]).length?(records.commands??[]).map(record=><div className="program-node" key={record.record_id}><strong>{value(record.payload.state)}</strong><Fields fields={{command_id:record.record_id,venue_client_id:record.payload.venue_client_id,service_namespace:record.payload.service_namespace,parent_command_id:record.payload.parent_command_id}}/></div>):<p>{t("未观察到应用命令；本地算法激活不代表已向场所发送。")}</p>}</section>
    {(records.local_children??[]).length>0&&<section><h4>{t("本地子单承诺")}</h4><p className="field-help">{t("承诺记录不代表已发送、已成交或可释放资金。")}</p><Archive records={records.local_children??[]} title={t("本地子单承诺（非成交）")}/></section>}
    <section><h4>{t("场所母单、子单与保护")}</h4><Fields fields={{execution_state:detail.execution_state,state_origin:detail.state_origin,parent_observation:state?.parent_observation,protection_status:detail.protection_status,children_complete:detail.children_complete}}/>
      {detail.children_complete!=='COMPLETE'&&<Alert>{t("尚未取得完整子单清单；空列表不能证明空仓或责任结束。")}</Alert>}
      {children.map((child,index)=><div className="program-node" key={index}><Fields fields={child as Record<string,unknown>}/></div>)}
      <p className="field-help">{t("交易所生成子单仅观察；每次应用生成的外部写操作仍需独立命令、许可和同一执行所有者校验。")}</p>
    </section>
    <section><h4>{t("能力与准入原因")}</h4><Fields fields={{archived_snapshot:detail.capability?.archived_snapshot,current_qualification:detail.capability?.current_qualification??'UNKNOWN'}}/>
      {(detail.capability?.admission_blockers??[]).map(code=><p key={code} className="field-help">{t("能力 ·")} {code}</p>)}
      {(detail.rejection_reasons??[]).map(reason=><p key={reason.code}>{reason.stage} · {reason.code}</p>)}
    </section>
    {Object.values(detail.truncated??{}).some(Boolean)&&<Alert error>{t("关联记录超出读取上限，当前详情不完整；请通过账本审查继续核查。")}</Alert>}
    <Archive records={detail.intakes??[]} title={t("原始提案与决策上下文")}/>
    <section><h4>{t("最新资金预留投影")}</h4><p className="field-help">{t("记录值不代表当前可花余额；缺失或无效投影显示当前未验证。")}</p>{(detail.reservation_observations??[]).map(item=><div className="program-node" key={item.reservation_id}><strong>{item.reservation_id}</strong><p>{item.current_status==='RECORDED_PROJECTION'?t("已读取最新记录投影"):t("当前未验证 · ")+item.current_status}</p>{item.projection&&<Fields fields={{asset:(item.projection.reservation as {asset?:string}|undefined)?.asset,held:item.projection.held,reflected:item.projection.reflected,release_pending:item.projection.release_pending,sequence:item.projection.sequence}}/>}</div>)}</section>
    <Archive records={records.reservations??[]} title={t("原始资金预留（创建记录）")}/>
    <Archive records={records.reservation_projections??[]} title={t("预留投影原始证据")}/>
    <Archive records={records.financial??[]} title={t("保护责任与接入证据")}/>
    <Archive records={[...(records.bindings??[]),...(records.states??[]),...(records.local_states??[]),...(records.requests??[])]} title={t("母子关联与完整请求")}/>
    <details className="program-archive"><summary>{t("原始计划与内容摘要")}</summary><p>{t("JSONB 规范内容 SHA256：")}<span className="program-id">{detail.canonical_payload_hash}</span></p><p className="field-help">{t("原记录中的 hash 与 ID 保持原值；此摘要不会替代历史 hash。")}</p><pre>{JSON.stringify(detail.payload,null,2)}</pre></details>
  </article>;
}

export function OrderProgramObservation({api}:{api:RuntimeApi}) {
  const [page,setPage]=useState<ProgramPage>(),[capability,setCapability]=useState<CapabilityView>(),[detail,setDetail]=useState<ProgramView>(),[scope,setScope]=useState(''),[size,setSize]=useState(10),[cursors,setCursors]=useState<(string|null)[]>([null]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[detailBusy,setDetailBusy]=useState(false),[selection,setSelection]=useState<{id:string;scope:string}|null>(null);
  const cursor=cursors[cursors.length-1];
  useEffect(()=>{let active=true;api.orderCapabilities().then(result=>{if(active)setCapability(result);}).catch(cause=>{if(active)setError(errorMessage(cause));});return()=>{active=false;};},[api]);
  useEffect(()=>{let active=true;setBusy(true);setError('');setDetail(undefined);setSelection(null);api.orderPrograms({scope:scope||undefined,limit:size,cursor:cursor??undefined}).then(result=>{if(active)setPage(result);}).catch(cause=>{if(active){setPage(undefined);setError(errorMessage(cause));}}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};},[api,scope,size,cursor]);
  useEffect(()=>{if(!selection)return;let active=true;setDetailBusy(true);setDetail(undefined);api.orderProgramDetail(selection.id,selection.scope).then(result=>{if(active)setDetail(result);}).catch(cause=>{if(active)setError(errorMessage(cause));}).finally(()=>{if(active)setDetailBusy(false);});return()=>{active=false;};},[api,selection]);
  return <Panel title={t("订单计划与能力")} caption={t("原计划、账本接入、命令和场所回报分别观察；历史资格不自动延续")} className="order-observation"><div className="panel-body">
    <p className="field-help">{t("此处仅查询授权范围内的账本，不发起计划或恢复发送。PAPER 合成证据与场所资格分别记录。")}</p>
    {error&&<Alert error>{error}</Alert>}
    <div className="program-toolbar"><label>{t("读取范围")} <select aria-label={t("订单计划读取范围")} value={scope} onChange={e=>{setScope(e.target.value);setCursors([null]);}}><option value="">{t("全部已授予范围")}</option>{page?.scopes.map(item=><option key={item.scope_id} value={item.scope_id}>{item.scope.product} · {item.scope.instrument_uid} · {item.scope.venue_account_id}</option>)}</select></label><span aria-live="polite">{busy?t("读取中…"):`${page?.items.length??0}${t(" 条本页记录")}`}</span></div>
    <div className="program-layout"><div><div className="decision-history-scroll" role="region" aria-label={t("订单计划流水")} tabIndex={0}>
      {!busy&&page?.observation_status==='UNCONFIGURED'?<Empty compact title={t("只读订单账本尚未配置")} text={t("当前缺少受信任的账本绑定；不会生成订单或场所状态。")}/>:!busy&&!page?.items.length?<Empty compact title={t("授权范围内未读取到订单计划")} text={t("空记录不能证明没有持仓、在途订单或保护责任。")}/>:page?.items.map(item=><button type="button" className={`program-list-item ${selection?.id===item.program_id&&selection.scope===item.scope_id?'selected':''}`} key={`${item.scope_id}:${item.program_id}`} onClick={()=>setSelection({id:item.program_id,scope:item.scope_id})}><strong>{item.program_id}</strong><span>{item.scope?`${item.scope.product} · ${item.scope.instrument_uid} · ${item.scope.venue_account_id}`:item.scope_id}</span><span>{item.template??t("未知模板")} · {item.execution_mode??t("模式未解析")}</span><span>{item.contract_version} · {item.codec_status}</span></button>)}
    </div><RecordPagination size={size} page={cursors.length-1} hasNext={Boolean(page?.next_cursor)} disabled={busy} label={t("订单计划")} onSize={n=>{setSize(n);setCursors([null]);}} onPrevious={()=>setCursors(v=>v.slice(0,-1))} onNext={()=>{if(page?.next_cursor)setCursors(v=>[...v,page.next_cursor]);}}/></div>
    <div>{detailBusy?<p role="status">{t("读取计划详情…")}</p>:detail?<OrderProgramDetail detail={detail}/>:<Empty compact title={t("选择一个订单计划")} text={t("查看原始参数、命令不确定性、母子关联和持续责任。")}/>}</div></div>
    {capability&&<details className="program-archive"><summary>{t("完整能力目录 ·")} {capability.features.length}  {t("项目标")}</summary><p>{t("表达契约：")}{Object.keys(capability.expression_versions??{}).join(' / ')}{t("；当前资格：")}{capability.current_qualification}。</p><p className="field-help">{t("目录是固定参考。表达词汇、编译实现、工程测试和场所资格独立；不能凭 schema 或历史快照获准执行。")}</p>{capability.admission_blockers.map(code=><p key={code}>{code}</p>)}<div className="table-scroller" role="region" aria-label={t("能力与资格目录")} tabIndex={0}><table className="data-table"><thead><tr><th>{t("产品 / 能力")}</th><th>{t("编译实现")}</th><th>{t("工程测试")}</th><th>{t("场所资格")}</th></tr></thead><tbody>{capability.features.map(item=><tr key={item.id}><td>{item.id}</td><td>{item.compilation_status}</td><td>{item.engineering_test_status}</td><td>{item.venue_qualification}</td></tr>)}</tbody></table></div></details>}
  </div></Panel>;
}
