import type {Decision, ResearchTaskProfile} from './types';
import {Badge} from './ui';
import {time} from './format';
import { live, t } from './i18n';
export const researchProfile = (interval = '15m'): ResearchTaskProfile => ({decision_interval:interval, holding_period_seconds:3600, allowed_operations:['SET_TARGET','UPDATE_EXIT','HOLD'], required:['quote',`candles:${interval}`], optional:['book','news'], windows:{[interval]:32}});
export const assessmentLabel = (status?: string) => ({DECIDED:t("有效分析"), DATA_BLOCKED:t("数据不足，未调用模型"), MODEL_ERROR:t("模型调用失败"), REVIEW_REQUIRED:t("需要完成证据复核"), EXPIRED:t("计划已过期"), INVALID_PLAN:t("计划校验未通过"), CAPABILITY_BLOCKED:t("能力或资格未就绪，未调用模型"), REPLAN_REQUIRED:t("能力、规则或行情已变化，需重新规划")}[status ?? ''] ?? t("历史建议"));
export const proposalTarget = (proposal: NonNullable<Decision['proposal']>) => proposal.target ?? {side:proposal.target_side ?? '—', sizing:proposal.target_sizing ?? '', value:proposal.target_value ?? '—'};
export function AssessmentDetails({decision:d}: {decision:Decision}) {
  if (!d.assessment_status) return <p className="field-help">{t("历史建议 · 不含结构化退出计划，不作为新计划重放。")}</p>;
  return <section className="detail-block" aria-label={t("证据与交易计划")}><h3>{t("证据与交易计划")}</h3><Badge kind={d.assessment_status === 'DECIDED' ? '' : 'wait'}>{assessmentLabel(d.assessment_status)}</Badge>
    <p>{d.reason_code}</p><p className="field-help">{d.decision_policy === 'evidence_review' ? t("交叉证据复核") : t("单模型 / 全票政策")}  {t("· 分析结论仍须通过执行风控。")}</p>
    {d.proposal && (() => { const target = proposalTarget(d.proposal); return <><p>{t("目标：")}{target.side} · {target.value} {target.sizing === 'QUOTE_NOTIONAL' ? t("计价币名义金额") : t("基础资产数量")}</p><p>{t("止损参考：")}{d.proposal.exit_plan.stop_price ?? t("无")}  {t("· 计划有效至")} {time(d.proposal.valid_until)}</p><p>{t("退出复查：")}{time(d.proposal.exit_plan.review_due_at)}</p><p className="field-help">{t("上下文")} {d.context_hash ?? t("未提供")}  {t("· 能力")} {d.capabilities_hash ?? t("未提供")}  {t("· 规则版本")} {d.rule_revision ?? t("未提供")}</p>{d.replan_reasons?.length ? <p className="field-help">{t("需重新规划：")}{d.replan_reasons.join('、')}</p> : null}</>; })()}
    {d.watch_conditions?.length ? <details><summary>{t("复查条件（")}{d.watch_conditions.length}）</summary><pre>{JSON.stringify(d.watch_conditions,null,2)}</pre></details> : null}
    {d.component_results?.length ? <details><summary>{t("模型参与记录（")}{d.component_results.length}  {t("次调用）")}</summary><ul>{d.component_results.map((r,i)=><li key={i}>{r.profile_id} · {r.role ?? t("分析")} · {r.status === 'SUCCEEDED' ? t("已完成") : t("失败")}  {t("· 配置版本")} {r.config_revision}{r.error && <p className="field-help">{r.error}</p>}</li>)}</ul></details> : null}
    {Boolean(d.review_report) && <details><summary>{t("复核记录")}</summary><pre>{JSON.stringify(d.review_report,null,2)}</pre></details>}
    <p className="field-help">{t("快照：")}{d.snapshot_id ?? t("未提供")}  {t("· 策略版本：")}{d.strategy_revision ?? t("研究记录")}</p>
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

const quoteReason:Record<string,string>=live(() => ({QUOTE_MISSING:t("缺少报价"),QUOTE_INVALID:t("报价格式或价格范围无效"),QUOTE_FUTURE:t("报价时间领先本机"),QUOTE_STALE:t("报价已过期或失效"),QUOTE_SOURCE_MISMATCH:t("行情来源发生变化"),QUOTE_SOURCE_UNAVAILABLE:t("行情服务不可用")}));
export function DataBlockedDetails({decision:d}:{decision:Decision}) {
  const check=d.data_quality?.quote_check;
  const reasons=(d.missing_required ?? []).map(ref=>quoteReason[d.data_quality?.sections?.[ref]?.reason ?? ''] ?? (ref==='quote' ? t("报价未通过校验（历史记录未保存具体原因）") : `${t("缺少有效数据：")}${ref}`));
  return <section className="detail-block data-blocked-summary"><h3>{t("数据检查未通过")}</h3><p>{reasons.join('；') || d.summary || t("历史记录未保存具体缺项。")}</p><p className="field-help">{t("未调用模型，未进入交易风控，未提交订单。")}</p>
    {check?.age_seconds && <p className="field-help">{Number(check.age_seconds)<0 ? `${t("报价领先本机 ")}${Math.abs(Number(check.age_seconds)).toFixed(3)}${t(" 秒")}` : `${t("报价年龄 ")}${Number(check.age_seconds).toFixed(3)}${t(" 秒")}`}  {t("· 时效上限")} {check.maximum_age_seconds}  {t("秒")}</p>}
    <p className="field-help">{t("策略版本：")}{d.strategy_revision ?? t("历史记录未保存")} · {d.next_review_at ? `${t("下次检查 ")}${time(d.next_review_at,true)}` : t("复查时间以策略调度为准")}</p>
    {d.snapshot_id && <details><summary>{t("快照信息")}</summary><p className="field-help">{d.snapshot_id}</p>{check && <p className="field-help">{t("报价")} {check.as_of ?? t("未提供")}  {t("· 截止")} {check.cutoff}</p>}</details>}
  </section>;
}
