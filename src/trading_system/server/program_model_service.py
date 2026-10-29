"""Opt-in full program research using the existing model run journal.

This is model advice only. Current host qualification is absent, so even a valid
candidate reports UNQUALIFIED. Admission remains a separate same-kernel action.
"""
import asyncio,json,time
from dataclasses import dataclass,field
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
        or request.decision_schema!='assessment_v1' or request.decision_policy!='unanimous'):
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','需要已确认费用的人工研究请求。',403)
    service.store.session(session_token)
    validate_program_context(frame);service._request_identity(request.symbol,key)
    expected_profile={k:v for k,v in frame.payload['analysis_snapshot']['task_profile'].items() if k!='strategy_revision'}
    if request.task_profile is None or request.task_profile.model_dump()!=expected_profile:
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','人工研究的数据范围已变化。',403)
    return _ExplicitResearchRequest(service=service,session_token=session_token,symbol=request.symbol,
        mode=request.analysis_mode,profiles=tuple(request.model_profile_ids),
        revisions=tuple(sorted(request.expected_revisions.items())),key=key,context_hash=frame.context_hash,
        expires_ns=time.monotonic_ns()+180_000_000_000)


def _research_valid(service,request,symbol,mode,ids,revisions,key,frame):
    if (type(request) is not _ExplicitResearchRequest or request.service is not service
        or (request.symbol,request.mode,request.profiles,request.revisions,request.key,request.context_hash)
           !=(symbol,mode,tuple(ids),tuple(sorted(revisions.items())),key,frame.context_hash)
        or time.monotonic_ns()>=request.expires_ns):
        raise ApiError('EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED','人工研究请求已变化或失效。',403)
    service.store.session(request.session_token)


async def assess_program(service,symbol,mode,ids,revisions,key,frame,policy,research_request=None):
    if policy!='unanimous':
        raise ApiError('PROGRAM_REVIEW_PROTOCOL_NOT_IMPLEMENTED','完整订单计划的复核协议尚未接入。',409)
    payload=validate_program_context(frame)
    # Reuse existing market quality/freshness checks, without replacing its hash.
    service._clean_context(symbol,{'analysis_snapshot':payload['analysis_snapshot']})
    clean=dict(payload,context_hash=frame.context_hash)
    if research_request is not None:
        _research_valid(service,research_request,symbol,mode,ids,revisions,key,frame)
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
    outcomes=[];selected=None;status='UNQUALIFIED';reason='HOST_ADMISSION_NOT_REGISTERED'
    usage={'input_tokens':0,'output_tokens':0}
    def persist():
        with service.store.transaction() as connection:repository.write_run(connection,key,record)
    try:
        for identifier in ids:
            _research_valid(service,research_request,symbol,mode,ids,revisions,key,frame)
            config,secret=configs[identifier]
            with service.store.transaction() as connection:service._revision(service._profile(connection,identifier),revisions[identifier])
            path,body=provider.payload(config,clean)
            if len(json.dumps(body,ensure_ascii=False).encode())>120000:raise ApiError('INVALID_MODEL_CONTEXT','Program context exceeds model budget.')
            record['requests'].append(dict(profile_id=identifier,path=path,body=body,input_hash=service._hash(body),
                config_revision=config['revision'],context_hash=frame.context_hash,prompt_revision=PROMPT_REVISION))
            persist() # actual existing durable model journal, before provider IO
            raw=await provider.request(service.client,config,secret,'POST',path,body)
            value,consumed=provider.parse(config,raw,symbol,schema=SCHEMA)
            if any(s and s in json.dumps(value) for _,s in configs.values()):raise provider.invalid()
            assessment=parse_program_assessment(value,frame)
            outcomes.append(dict(profile_id=identifier,status='SUCCEEDED',config_revision=revisions[identifier],
                context_hash=frame.context_hash,response=assessment.payload,usage=consumed))
            for name in usage:usage[name]+=consumed.get(name) or 0
            if selected is None:selected=assessment
            else:
                def terms(p):
                    if p is None:return None
                    return {k:v for k,v in p.items() if k not in ('proposal_id','summary','evidence_refs')}
                if terms(selected.payload['proposal'])!=terms(assessment.payload['proposal']):
                    status,reason='REPLAN_REQUIRED','PROGRAM_MODEL_DISAGREEMENT'
            record['component_results']=outcomes;persist()
        with service.store.transaction() as connection:
            if any(service._profile(connection,p)['revision']!=revisions[p] for p in ids):
                status,reason='EXPIRED','MODEL_REVISION_CHANGED'
    except BaseException as exc:
        record.update(status='FAILED',component_results=outcomes,**service._error(exc));persist();raise
    proposal=selected.payload['proposal'] if selected else None
    snapshot=payload['analysis_snapshot']
    decision=dict(id=str(uuid4()),symbol=symbol,created_at=stamp(),schema_revision=SCHEMA,
        assessment_status=status,reason_code=reason,proposal=proposal,action=None,
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
        admission_blockers=payload['order_context']['blockers'],source='AI · typed program research')
    result={'decision':decision,'usage':usage}
    record.update(status='SUCCEEDED',component_results=outcomes,result=result);persist()
    return result
