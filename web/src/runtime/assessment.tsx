import type {Decision, ResearchTaskProfile} from './types';
import {Badge} from './ui';
import {time} from './format';
export const researchProfile = (interval = '15m'): ResearchTaskProfile => ({decision_interval:interval, holding_period_seconds:3600, allowed_operations:['SET_TARGET','UPDATE_EXIT','HOLD'], required:['quote',`candles:${interval}`], optional:['book','news'], windows:{[interval]:32}});
export const assessmentLabel = (status?: string) => ({DECIDED:'有效分析', DATA_BLOCKED:'数据不足，未调用模型', MODEL_ERROR:'模型调用失败', REVIEW_REQUIRED:'需要完成证据复核', EXPIRED:'计划已过期', INVALID_PLAN:'计划校验未通过'}[status ?? ''] ?? '历史建议');
export function AssessmentDetails({decision:d}: {decision:Decision}) {
  if (!d.assessment_status) return <p className="field-help">历史建议 · 不含结构化退出计划，不作为新计划重放。</p>;
  return <section className="detail-block" aria-label="证据与交易计划"><h3>证据与交易计划</h3><Badge kind={d.assessment_status === 'DECIDED' ? '' : 'wait'}>{assessmentLabel(d.assessment_status)}</Badge>
    <p>{d.reason_code}</p><p className="field-help">{d.decision_policy === 'evidence_review' ? '交叉证据复核' : '单模型 / 全票政策'} · 分析结论仍须通过执行风控。</p>
    {d.proposal && <><p>目标：{d.proposal.target.side} · {d.proposal.target.value} {d.proposal.target.sizing === 'QUOTE_NOTIONAL' ? '计价币名义金额' : '基础资产数量'}</p><p>止损参考：{d.proposal.exit_plan.stop_price ?? '无'} · 计划有效至 {time(d.proposal.valid_until)}</p><p>退出复查：{time(d.proposal.exit_plan.review_due_at)}</p></>}
    {d.watch_conditions?.length ? <details><summary>复查条件（{d.watch_conditions.length}）</summary><pre>{JSON.stringify(d.watch_conditions,null,2)}</pre></details> : null}
    {Boolean(d.review_report) && <details><summary>复核记录</summary><pre>{JSON.stringify(d.review_report,null,2)}</pre></details>}
    <p className="field-help">快照：{d.snapshot_id ?? '未提供'} · 策略版本：{d.strategy_revision ?? '研究记录'}</p>
  </section>;
}

export function changeResearchInterval(profile: ResearchTaskProfile | undefined, interval:string): ResearchTaskProfile {
  if (!profile) return researchProfile(interval);
  const old = profile.decision_interval;
  const replaceRef = (ref:string) => ref === `candles:${old}` ? `candles:${interval}` : ref;
  const windows = {...profile.windows};
  delete windows[old];
  windows[interval] = profile.windows[old] ?? 32;
  return {...profile,decision_interval:interval,windows,required:[...new Set(profile.required.map(replaceRef))],optional:[...new Set(profile.optional.map(replaceRef))].filter(ref=>!profile.required.map(replaceRef).includes(ref))};
}
