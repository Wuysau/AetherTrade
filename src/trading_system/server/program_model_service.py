"""Opt-in full program research using the existing model run journal.

This is model advice only. Current host qualification is absent, so even a valid
candidate reports UNQUALIFIED. Admission remains a separate same-kernel action.
"""
import asyncio,json,time
from dataclasses import dataclass,field
from fractions import Fraction
from uuid import uuid4
from . import model_client as provider,model_repository as repository
from .errors import ApiError
from .store import stamp
from .program_model_context import SCHEMA,PROMPT_REVISION,validate_program_context,parse_program_assessment


@dataclass(frozen=True,slots=True,kw_only=True)
class _ExplicitResearchRequest:
    service:object
    session_token:str=field(repr=False)
    symbol:str
    mode:str
    policy:str
    profiles:tuple[str,...]
    revisions:tuple[tuple[str,int],...]
    key:str
    context_hash:str
    expires_ns:int


def authorize_research(service,request,session_token,key,frame):
    """Bind existing authenticated, cost-acknowledged manual research only.

    This private in-memory witness is not accepted from model/JSON or a strategy
    job. It grants no order action and is never persisted with the cookie.
    """
    from .model_api import ProfileAnalysis
    if (type(request) is not ProfileAnalysis or request.acknowledge_cost is not True
        or request.decision_schema!='assessment_v1' or request.decision_policy not in ('unanimous','evidence_review')):
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','需要已确认费用的人工研究请求。',403)
    service.store.session(session_token)
    validate_program_context(frame);service._request_identity(request.symbol,key)
    expected_profile={k:v for k,v in frame.payload['analysis_snapshot']['task_profile'].items() if k!='strategy_revision'}
    if request.task_profile is None or request.task_profile.model_dump()!=expected_profile:
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','人工研究的数据范围已变化。',403)
    return _ExplicitResearchRequest(service=service,session_token=session_token,symbol=request.symbol,
        mode=request.analysis_mode,policy=request.decision_policy,profiles=tuple(request.model_profile_ids),
        revisions=tuple(sorted(request.expected_revisions.items())),key=key,context_hash=frame.context_hash,
        expires_ns=time.monotonic_ns()+180_000_000_000)


def _research_valid(service,request,symbol,mode,ids,revisions,key,frame,policy):
    if (type(request) is not _ExplicitResearchRequest or request.service is not service
        or (request.symbol,request.mode,request.policy,request.profiles,request.revisions,request.key,request.context_hash)
           !=(symbol,mode,policy,tuple(ids),tuple(sorted(revisions.items())),key,frame.context_hash)
        or time.monotonic_ns()>=request.expires_ns):
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','人工研究请求已变化或失效。',403)
    service.store.session(request.session_token)


def _bounded_adjudication(previous,revised):
    old,new=previous.proposal,revised.proposal
    if new is None:return
    if (old is None or new.requested_leverage>old.requested_leverage or
        new.target_side!='FLAT' and (new.target_side!=old.target_side or new.target_sizing!=old.target_sizing
            or Fraction(new.target_value)>Fraction(old.target_value))):
        raise ValueError('PROGRAM_ADJUDICATION_RISK_INCREASE')


async def assess_program(service,symbol,mode,ids,revisions,key,frame,policy,research_request=None):
    payload=validate_program_context(frame)
    # Reuse existing market quality/freshness checks, without replacing its hash.
    service._clean_context(symbol,{'analysis_snapshot':payload['analysis_snapshot']})
    clean=dict(payload,context_hash=frame.context_hash)
    if research_request is not None:
        _research_valid(service,research_request,symbol,mode,ids,revisions,key,frame,policy)
    fingerprint=service._hash([symbol,mode,ids,revisions,SCHEMA,frame.context_hash,policy,
                              'research_only' if research_request is not None else 'capability_check'])
    with service.store.transaction() as connection:
        cached=service._cached(repository.read_run(connection,key),[fingerprint])
        if cached is not None:return cached
        if research_request is None:
            # No qualified program host is registered. Strategy/ordinary
            # analysis stops before profiles, decryption or provider IO.
            snapshot=payload['analysis_snapshot']
            decision=dict(id=str(uuid4()),symbol=symbol,created_at=stamp(),schema_revision=SCHEMA,
                assessment_status='CAPABILITY_BLOCKED',qualification='UNQUALIFIED',
                reason_code='HOST_ADMISSION_NOT_REGISTERED',admission_blockers=payload['order_context']['blockers'],
                proposal=None,action=None,summary='当前完整订单上下文未准入，未调用模型。',
                counter_evidence=[],invalidation='',next_review_at=None,source='能力就绪检查',
                risk_status='NOT_EVALUATED',execution_authorized=False,
                snapshot_id=snapshot['snapshot_id'],snapshot_hash=snapshot['snapshot_hash'],context_hash=frame.context_hash,
                strategy_revision=frame.decision_context.strategy_revision,model_profile_ids=ids,
                profile_revisions=revisions,analysis_mode=mode,decision_policy=policy)
            result={'decision':decision,'usage':None}
            repository.write_run(connection,key,dict(fingerprint=fingerprint,status='SUCCEEDED',created_at=stamp(),
                component=False,schema_revision=SCHEMA,program_context=payload,requests=[],result=result),new=True)
            return result
        if policy not in ('unanimous','evidence_review') or policy=='evidence_review' and mode!='consensus':
            raise ApiError('INVALID_MODEL_SELECTION','复核政策须绑定明确的交叉研究请求。',409)
        configs={}
        for identifier in ids:
            config=service._profile(connection,identifier);service._revision(config,revisions[identifier])
            if not config['enabled'] or not config['model']:raise ApiError('MODEL_NOT_CONFIGURED','Model is not enabled.',409)
            secret=service.vault.decrypt(config['encrypted_key'])
            if not secret and not config['local_mode']:raise ApiError('MODEL_KEY_REQUIRED','Model key required.',409)
            if secret and secret in json.dumps(clean,ensure_ascii=False):raise ApiError('INVALID_MODEL_CONTEXT','Unsafe model context.')
            configs[identifier]=(config,secret)
        record=dict(fingerprint=fingerprint,status='IN_FLIGHT',created_at=stamp(),schema_revision=SCHEMA,
            snapshot=payload['analysis_snapshot'],program_context=payload,context_hash=frame.context_hash,
            prompt_revision=PROMPT_REVISION,requests=[],component=False,analysis_mode=mode,
            model_profile_ids=ids,profile_revisions=revisions)
        repository.write_run(connection,key,record,new=True)
    outcomes=[];selected=None;primary=None;report=None;history=[];status='UNQUALIFIED';reason='HOST_ADMISSION_NOT_REGISTERED'
    usage={'input_tokens':0,'output_tokens':0}
    max_calls=2 if mode=='single' else len(ids)+1
    deadline=asyncio.get_running_loop().time()+min(180,sum(c[0]['timeout_seconds'] for c in configs.values())*2)
    def persist():
        with service.store.transaction() as connection:repository.write_run(connection,key,record)
    async def call(identifier,role,extra=None):
        if len(record['requests'])>=max_calls or asyncio.get_running_loop().time()>=deadline:
            raise ApiError('MODEL_BUDGET_EXHAUSTED','Call or elapsed-time budget exhausted.',409)
        _research_valid(service,research_request,symbol,mode,ids,revisions,key,frame,policy)
        config,secret=configs[identifier]
        with service.store.transaction() as connection:service._revision(service._profile(connection,identifier),revisions[identifier])
        path,body=provider.payload(config,dict(clean,role=role,**(extra or {})))
        serialized=json.dumps(body,ensure_ascii=False,allow_nan=False)
        if len(serialized.encode())>120000 or any(s and s in serialized for _,s in configs.values()):
            raise ApiError('INVALID_MODEL_CONTEXT','Unsafe program context.')
        record['requests'].append(dict(profile_id=identifier,role=role,path=path,body=body,input_hash=service._hash(body),
            config_revision=config['revision'],context_hash=frame.context_hash,prompt_revision=PROMPT_REVISION))
        component=dict(profile_id=identifier,role=role,status='FAILED',config_revision=revisions[identifier],context_hash=frame.context_hash)
        outcomes.append(component);record['component_results']=outcomes
        persist() # same exact journal commits before provider IO
        try:
            async with asyncio.timeout(max(.001,deadline-asyncio.get_running_loop().time())):
                raw=await provider.request(service.client,config,secret,'POST',path,body)
            consumed=provider._usage(raw)
            for name in usage:usage[name]+=consumed.get(name) or 0
            component['usage']=consumed
            value,_=provider.parse(config,raw,symbol,schema='review_v1' if role=='review' else SCHEMA)
            if any(s and s in json.dumps(value) for _,s in configs.values()):raise provider.invalid()
            component.update(status='SUCCEEDED',response=value);persist()
            return value
        except Exception as error:
            component.update(**service._error(error));persist();raise
    try:
        primary=selected=parse_program_assessment(await call(ids[0],'primary'),frame)
        if mode=='consensus' and policy=='evidence_review':
            from .decision_assessment import validate_review,combine_review_reports
            reports=[]
            for identifier in ids[1:]:
                try:reports.append(validate_review(await call(identifier,'review',{'candidate':selected.payload}),payload['analysis_snapshot']))
                except Exception:
                    status,reason='REVIEW_REQUIRED','REQUIRED_REVIEW_FAILED';break
            report=combine_review_reports(reports)
            if status=='UNQUALIFIED':
                if report['status']=='HARD':status,reason='INVALID_PLAN','VERIFIED_REVIEW_DEFECT'
                elif report['status']=='UNVERIFIED':status,reason='REVIEW_REQUIRED','UNVERIFIED_CRITICAL_FACT'
                elif report['status']=='SOFT':
                    try:raw=await call(ids[0],'adjudication',{'candidate':selected.payload,'review_report':report})
                    except Exception:status,reason='REVIEW_REQUIRED','PRIMARY_ADJUDICATION_FAILED'
                    else:
                        history.append(dict(outcomes[-1]))
                        try:
                            revised=parse_program_assessment(raw,frame);_bounded_adjudication(selected,revised)
                            selected=revised
                        except ValueError as error:status,reason='INVALID_PLAN',str(error)
        else:
            for identifier in ids[1:]:
                assessment=parse_program_assessment(await call(identifier,'primary'),frame)
                def terms(p):
                    if p is None:return None
                    return {k:v for k,v in p.items() if k not in ('proposal_id','summary','evidence_refs')}
                if terms(selected.payload['proposal'])!=terms(assessment.payload['proposal']):
                    status,reason='REPLAN_REQUIRED','PROGRAM_MODEL_DISAGREEMENT'
        with service.store.transaction() as connection:
            if any(service._profile(connection,p)['revision']!=revisions[p] for p in ids):
                status,reason='EXPIRED','MODEL_REVISION_CHANGED'
    except BaseException as exc:
        record.update(status='FAILED',component_results=outcomes,**service._error(exc));persist();raise
    proposal=selected.payload['proposal'] if selected and status=='UNQUALIFIED' else None
    snapshot=payload['analysis_snapshot']
    decision=dict(id=str(uuid4()),symbol=symbol,created_at=stamp(),schema_revision=SCHEMA,
        assessment_status=status,reason_code=reason,proposal=proposal,action=None,
        candidate_proposal=primary.payload['proposal'] if primary else None,review_report=report,adjudication_history=history,
        summary=selected.payload['summary'] if selected else reason,counter_evidence=[],invalidation='',
        evidence_claims=selected.payload['evidence_claims'] if selected else [],
        watch_conditions=selected.payload['watch_conditions'] if selected else [],
        next_review_at=selected.payload['next_review_at'] if selected else None,
        snapshot_id=snapshot['snapshot_id'],snapshot_hash=snapshot['snapshot_hash'],context_hash=frame.context_hash,
        strategy_revision=frame.decision_context.strategy_revision,profile_revisions=revisions,required_profile_ids=ids,
        model_profile_ids=ids,analysis_mode=mode,decision_policy=policy,component_results=outcomes,
        model_profiles=[service._identity(configs[p][0]) for p in ids],
        prompt_revision=PROMPT_REVISION,risk_status='NOT_EVALUATED',execution_authorized=False,
        qualification=payload['order_context']['qualification_status'],
        budget=dict(max_calls=max_calls,calls=len(record['requests'])),
        admission_blockers=payload['order_context']['blockers'],source='AI · typed program research')
    result={'decision':decision,'usage':usage}
    record.update(status='SUCCEEDED',component_results=outcomes,result=result);persist()
    return result
