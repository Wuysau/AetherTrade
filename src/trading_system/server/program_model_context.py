"""Opt-in program model context; snapshot references are bindings, never authority.

The old MarketAnalysisSnapshot and its hash stay intact. A separately hashed,
server-created envelope binds the existing typed capability/rule/actor records.
No model-authored hash, qualification claim or callback can admit an order.
"""
import copy,json
from dataclasses import dataclass
from datetime import datetime,timezone
from fractions import Fraction
from typing import Literal
from jsonschema import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json,canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program_v3 import OrderProposalV3
from trading_system.contracts.order_snapshots import OrderCapabilitySnapshot,OrderRuleSnapshot
from .analysis_snapshot import validate_snapshot
from .decision_assessment import validate_assessment

SCHEMA='program_assessment_v1'
PROMPT_REVISION='program-evidence-1'


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class FrozenProgramContext:
    _json:str
    decision_context:t.DecisionContext
    capabilities:OrderCapabilitySnapshot
    rules:OrderRuleSnapshot
    expected_build_digest:str
    capability_decision:t.CapabilityDecision|None=None
    financial_market_snapshot:t.SnapshotPacket|None=None
    financial_book:t.BookSnapshot|None=None
    @property
    def payload(self):return json.loads(self._json)
    @property
    def context_hash(self):return canonical_hash(self.payload)
    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramModelAssessment:
    context_hash:str
    proposal:OrderProposalV3|None
    _json:str
    @property
    def payload(self):return json.loads(self._json)
    @property
    def execution_authorized(self)->Literal[False]:return False


def build_program_context(analysis_snapshot,*,decision_context,capabilities,rules,strategy_revision,
                          expected_build_digest,capability_decision=None,financial_market_snapshot=None,financial_book=None):
    require(type(decision_context) is t.DecisionContext,'PROGRAM_DECISION_CONTEXT_REQUIRED')
    require(type(capabilities) is OrderCapabilitySnapshot and type(rules) is OrderRuleSnapshot,
            'PROGRAM_TYPED_SNAPSHOTS_REQUIRED')
    snapshot=copy.deepcopy(validate_snapshot(analysis_snapshot));scope=capabilities.scope
    require(scope==rules.scope and scope in decision_context.scopes
        and scope.environment==decision_context.environment,'PROGRAM_CONTEXT_SCOPE_MISMATCH')
    require(type(strategy_revision) is int and strategy_revision>0
        and strategy_revision==decision_context.strategy_revision==snapshot['task_profile']['strategy_revision'],
        'PROGRAM_STRATEGY_REVISION_MISMATCH')
    require(snapshot['instrument_ref']==scope.instrument_uid and snapshot['product']==str(scope.product),
            'PROGRAM_INSTRUMENT_BINDING_REQUIRED')
    require(decision_context.snapshot_ref.environment==scope.environment
        and utc_timestamp(decision_context.snapshot_ref.visible_at)<=utc_timestamp(snapshot['evidence_cutoff']),
        'PROGRAM_SNAPSHOT_BINDING_REQUIRED')
    require(capabilities.rule_revision==rules.revision and capabilities.adapter_build_digest==expected_build_digest,
            'PROGRAM_RULE_OR_BUILD_CHANGED')
    require(capability_decision is None or type(capability_decision) is t.CapabilityDecision,
            'PROGRAM_CAPABILITY_INTERSECTION_REQUIRED')
    codec=VersionedOrderCodec()
    cap=codec.encode('order-capability-snapshot',capabilities)
    rule=codec.encode('order-rule-snapshot',rules)
    blockers=['HOST_ADMISSION_NOT_REGISTERED','CURRENT_QUALIFICATION_NOT_REGISTERED']
    financial_packet=None;book=None;link_hash=None
    if financial_market_snapshot is None or financial_book is None:
        blockers.append('ANALYSIS_FINANCIAL_LINK_NOT_ESTABLISHED')
    else:
        from trading_system.market.snapshots import snapshot_content_hash
        market=financial_market_snapshot
        require(type(market) is t.SnapshotPacket and type(financial_book) is t.BookSnapshot
            and market.scope==financial_book.scope==scope and market.ref==decision_context.snapshot_ref,
            'PROGRAM_FINANCIAL_SNAPSHOT_SCOPE_MISMATCH')
        require(snapshot_content_hash(market)==market.ref.hash,'PROGRAM_FINANCIAL_SNAPSHOT_HASH_MISMATCH')
        cutoff=utc_timestamp(snapshot['evidence_cutoff'])
        maximum_age=float(snapshot['quality']['quote_check']['maximum_age_seconds'])
        require(all(0<=(cutoff-utc_timestamp(at)).total_seconds()<=maximum_age for at in (
            market.quote.occurred_at,market.quote.received_at,market.published_at,market.revision_visible_at)),
            'PROGRAM_FINANCIAL_SNAPSHOT_CUTOFF_MISMATCH')
        quote=snapshot['market']['quote']
        require(quote is not None and market.quote.last is not None
            and Fraction(quote['price'])==Fraction(market.quote.last.value),'PROGRAM_ANALYSIS_QUOTE_LINK_MISMATCH')
        analysis_position=snapshot['portfolio']['position']
        actual=sum((Fraction(p.actual_quantity.value) for p in financial_book.positions),Fraction(0))
        require(actual==Fraction(analysis_position['quantity']) if analysis_position else actual==0,
                'PROGRAM_ANALYSIS_POSITION_LINK_MISMATCH')
        financial_packet=domain_payload(market);book=domain_payload(financial_book)
        link_hash=canonical_hash(dict(analysis_snapshot_hash=snapshot['snapshot_hash'],
            financial_market_snapshot_ref=domain_payload(market.ref),financial_book_hash=canonical_hash(book),
            analysis_quote=quote,financial_quote=financial_packet['quote']))
    if capability_decision is None:blockers.append('CAPABILITY_INTERSECTION_NOT_REGISTERED')
    elif capability_decision.rejection_codes:blockers.extend(capability_decision.rejection_codes)
    # No production qualified host is registered. These are historical claims,
    # not a new qualification flag or a replacement CapabilityCatalog decision.
    order_context=dict(proposal_version='2026-10-29',scope=domain_payload(scope),
        analysis_snapshot_ref=snapshot['snapshot_id'],analysis_snapshot_hash=snapshot['snapshot_hash'],
        financial_market_snapshot_ref=domain_payload(decision_context.snapshot_ref),
        financial_market_snapshot=financial_packet,financial_book=book,analysis_financial_link_hash=link_hash,
        decision_context=domain_payload(decision_context),strategy_revision=strategy_revision,
        capabilities=cap,rules=rule,capabilities_hash=canonical_hash(cap),rules_hash=canonical_hash(rule),
        expected_build_digest=expected_build_digest,
        capability_intersection=None if capability_decision is None else domain_payload(capability_decision),
        qualification_status='UNQUALIFIED',blockers=list(dict.fromkeys(blockers)),
        pending_source_versions=['2026-10-30'],execution_authorized=False)
    payload=dict(model_schema_revision=SCHEMA,analysis_snapshot=snapshot,order_context=order_context)
    return FrozenProgramContext(_json=canonical_json(payload),decision_context=decision_context,
        capabilities=capabilities,rules=rules,expected_build_digest=expected_build_digest,
        capability_decision=capability_decision,financial_market_snapshot=financial_market_snapshot,
        financial_book=financial_book)


def validate_program_context(frame):
    require(type(frame) is FrozenProgramContext,'SERVER_FROZEN_PROGRAM_CONTEXT_REQUIRED')
    rebuilt=build_program_context(frame.payload['analysis_snapshot'],decision_context=frame.decision_context,
        capabilities=frame.capabilities,rules=frame.rules,
        strategy_revision=frame.decision_context.strategy_revision,expected_build_digest=frame.expected_build_digest,
        capability_decision=frame.capability_decision,financial_market_snapshot=frame.financial_market_snapshot,
        financial_book=frame.financial_book)
    require(rebuilt._json==frame._json,'PROGRAM_CONTEXT_CHANGED')
    return frame.payload


def parse_program_assessment(value,frame,*,now=None):
    frozen=validate_program_context(frame);snapshot=frozen['analysis_snapshot'];binding=frozen['order_context']
    require(type(value) is dict and set(value)=={'schema_revision','context_hash','summary','proposal',
        'evidence_claims','watch_conditions','next_review_at'} and value['schema_revision']==SCHEMA,
        'INVALID_PROGRAM_ASSESSMENT_SCHEMA')
    require(value['context_hash']==frame.context_hash,'PROGRAM_CONTEXT_HASH_MISMATCH')
    checked=copy.deepcopy(value);checked.pop('context_hash');checked['schema_revision']='assessment_v1';checked['proposal']=None
    validate_assessment(checked,snapshot) # same evidence/watch predicate language
    now=utc_timestamp(now) if now else datetime.now(timezone.utc)
    cutoff=utc_timestamp(snapshot['evidence_cutoff'])
    maximum_age=float(snapshot['quality']['quote_check']['maximum_age_seconds'])
    require(0<maximum_age<=3600 and 0<=(now-cutoff).total_seconds()<=maximum_age,'PROGRAM_MODEL_SNAPSHOT_EXPIRED')
    proposal=value['proposal'];typed=None
    if proposal is not None:
        require(type(proposal) is dict and proposal.get('contract_version')=='2026-10-29',
                'PROGRAM_PROPOSAL_VERSION_UNSUPPORTED')
        try:typed=VersionedOrderCodec().decode('order-proposal',proposal)
        except (ValueError,TypeError,KeyError,ValidationError) as exc:raise ValueError('INVALID_ORDER_PROPOSAL_V3') from exc
        require(type(typed) is OrderProposalV3 and typed.instrument_ref==snapshot['instrument_ref'],
                'PROGRAM_PROPOSAL_INSTRUMENT_MISMATCH')
        require(typed.action in snapshot['task_profile']['allowed_operations']
            and all(ref in snapshot['evidence_refs'] for ref in typed.evidence_refs),'PROGRAM_PROPOSAL_SCOPE_OR_EVIDENCE_MISMATCH')
        require(now<utc_timestamp(typed.valid_until) and now<utc_timestamp(typed.exit_plan.review_due_at),
                'PROGRAM_PROPOSAL_EXPIRED')
        horizon=snapshot['task_profile']['holding_period_seconds']
        require(typed.exit_plan.maximum_holding_seconds<=horizon
            and (utc_timestamp(typed.valid_until)-now).total_seconds()<=horizon,'PROGRAM_PROPOSAL_HORIZON_EXCEEDED')
        if typed.program is not None:
            require(typed.program.capability_snapshot_ref==frame.capabilities.snapshot_id
                and typed.program.rule_snapshot_ref==frame.rules.snapshot_id,'PROGRAM_SNAPSHOT_REFERENCE_CHANGED')
            require(now<utc_timestamp(typed.program.submit_valid_until),'PROGRAM_SUBMISSION_EXPIRED')
        if typed.action in ('HOLD','UPDATE_EXIT'):
            previous=snapshot['portfolio'].get('previous_proposal')
            require(type(previous) is dict and previous.get('contract_version')=='2026-10-29',
                    'PROGRAM_PREVIOUS_RESPONSIBILITY_REQUIRED')
            from trading_system.contracts.order_program import validate_program_transition
            validate_program_transition(VersionedOrderCodec().decode('order-proposal',previous),typed)
    return ProgramModelAssessment(context_hash=frame.context_hash,proposal=typed,_json=canonical_json(value))

def admit_program_assessment(assessment,frame,*,admission_service=None):
    """Trusted host handoff to the SAME GenericProgramAdmissionService.

    The model hash is compared only as a binding. Current inputs are loaded via
    the service's existing restricted ingress reader. That service independently
    reloads intake and financial facts; this compilation supplies no authority.
    This function is not registered as a control/HTTP endpoint.
    """
    from .errors import ApiError
    from trading_system.intents.generic_program_admission import GenericProgramAdmissionService,GenericProgramAdmissionReceipt
    from trading_system.intents.generic_program_intake import ProgramIntakeInputs
    from trading_system.intents.program_compilation import compile_order_program
    if type(admission_service) is not GenericProgramAdmissionService:
        raise ApiError('HOST_ADMISSION_NOT_REGISTERED','完整订单计划接入主机尚未注册。',409)
    require(type(assessment) is ProgramModelAssessment,'TYPED_PROGRAM_ASSESSMENT_REQUIRED')
    validate_program_context(frame)
    require(assessment.context_hash==frame.context_hash,'PROGRAM_CONTEXT_HASH_MISMATCH')
    current_assessment=parse_program_assessment(assessment.payload,frame)
    require(current_assessment.proposal==assessment.proposal and assessment.proposal is not None
        and assessment.proposal.action=='SET_TARGET','PROGRAM_ADMISSION_TARGET_REQUIRED')
    service=admission_service;proposal=assessment.proposal;context=frame.decision_context
    require(service.scope==frame.capabilities.scope,'PROGRAM_ADMISSION_SCOPE_MISMATCH')
    if frame.financial_market_snapshot is None or frame.financial_book is None:
        raise ApiError('MODEL_FINANCIAL_LINK_REQUIRED','分析与资金快照关联尚未建立。',409)
    ingress=service.ingress
    with ingress.uow.transaction(service.scope) as tx:
        require(callable(ingress.load_inputs),'PROGRAM_CURRENT_INPUT_READER_REQUIRED')
        facts=ingress.load_inputs(tx,proposal,context,tx.projections.book(service.scope))
        require(type(facts) is ProgramIntakeInputs,'PROGRAM_INTAKE_INPUTS_REQUIRED')
        codec=VersionedOrderCodec()
        if (codec.hash('order-capability-snapshot',facts.capabilities)!=codec.hash('order-capability-snapshot',frame.capabilities)
            or codec.hash('order-rule-snapshot',facts.rules)!=codec.hash('order-rule-snapshot',frame.rules)
            or facts.expected_build_digest!=frame.expected_build_digest):
            changed=[]
            if codec.hash('order-capability-snapshot',facts.capabilities)!=codec.hash('order-capability-snapshot',frame.capabilities):changed.append('capability')
            if codec.hash('order-rule-snapshot',facts.rules)!=codec.hash('order-rule-snapshot',frame.rules):changed.append('rule')
            if facts.expected_build_digest!=frame.expected_build_digest:changed.append('build')
            raise ApiError('REPLAN_REQUIRED','能力、规则或构建已变化；请重新生成计划。',409,
                           details={'changed_fields':changed})
        compilation=compile_order_program(proposal,facts.rules,facts.capabilities,profile=facts.profile,
            scope=service.scope,now=facts.clock.utc_now,expected_build_digest=facts.expected_build_digest,
            expected_rule_revision=frame.rules.revision,
            expected_capabilities_hash=codec.hash('order-capability-snapshot',frame.capabilities),
            expected_instrument_ref=facts.expected_instrument_ref,allow_round_down=facts.allow_round_down)
        require(canonical_hash(domain_payload(compilation))==canonical_hash(domain_payload(facts.compilation)),
                'PROGRAM_CURRENT_COMPILATION_CHANGED')
    original_load=service.load_inputs
    def guarded_inputs(tx,source,record,book,clock):
        from trading_system.risk.program_financial_admission import ProgramFinancialInputs
        if canonical_hash(domain_payload(book))!=canonical_hash(domain_payload(frame.financial_book)):
            raise ApiError('REPLAN_REQUIRED','模型所见持仓、在途或资金状态已变化。',409,
                           details={'changed_fields':['book']})
        actual=original_load(tx,source,record,book,clock)
        require(type(actual) is ProgramFinancialInputs,'PROGRAM_FINANCIAL_INPUTS_REQUIRED')
        if (canonical_hash(domain_payload(book))!=canonical_hash(domain_payload(frame.financial_book))
            or canonical_hash(domain_payload(actual.archive.risk_facts.market))
               !=canonical_hash(domain_payload(frame.financial_market_snapshot))):
            raise ApiError('REPLAN_REQUIRED','模型所见行情或持仓、在途、资金状态已变化。',409,
                           details={'changed_fields':['market','book']})
        return actual
    # Same service class/kernel/UoW and all independent qualification/wallet/
    # dispatch readers. This adds a stricter frame check INSIDE its actual
    # financial transaction; no shared service mutation or fake success callback.
    guarded=GenericProgramAdmissionService(service.ingress,service.reservations,load_inputs=guarded_inputs,
        load_clock=service.load_clock,load_qualification=service.load_qualification,
        load_action_proof=service.load_action_proof,load_lease=service.load_lease,load_wallet=service.load_wallet)
    receipt=guarded.admit(proposal,context,compilation)
    require(type(receipt) is GenericProgramAdmissionReceipt,'PROGRAM_COMMITTED_RECEIPT_REQUIRED')
    return receipt # committed intake/financial attachment only, never permit/fill

def build_program_context_from_catalog(analysis_snapshot,*,capability_catalog,capability_query,**bindings):
    """Trusted composition invokes the existing four-layer intersection exactly."""
    from trading_system.accounts.capabilities import CapabilityCatalog
    require(type(capability_catalog) is CapabilityCatalog and type(capability_query) is t.CapabilityQuery,
            'PROGRAM_CAPABILITY_CATALOG_REQUIRED')
    require(capability_query.scope==bindings['capabilities'].scope
        and capability_query.authorization.authorization_id==bindings['decision_context'].authorization_id
        and all(p.build_digest==bindings['expected_build_digest'] for p in (
            capability_query.platform,capability_query.adapter,capability_query.account)),
        'PROGRAM_CAPABILITY_QUERY_BINDING_MISMATCH')
    return build_program_context(analysis_snapshot,capability_decision=capability_catalog.intersect(capability_query),**bindings)
