"""Full typed program intake through the existing restricted intake role.

No legacy proposal/intent projection, financial write, command or permit exists
here. The immutable row preserves the original semantics and SQL transaction
identity; the owner must reload it together with fresh financial facts later.
"""
from dataclasses import dataclass,replace
from typing import Literal

from trading_system.accounts.authorization import AuthorizationBinding,AuthorizationService
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import OrderProposal,identifier
from trading_system.contracts.order_program_v3 import OrderProposalV3
from trading_system.contracts.quote_position_v1 import QuotePositionProposalV1
from trading_system.contracts.quote_financial import QuoteFinancialSourceV1
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.contracts.program_financial import FinancialSource,require
from trading_system.storage.models import account_key,decode,scope_id
from .ingress import IngressRevision,revision_key
from .product_rules import TrustedProductRules
from .program_compilation import ProgramCompilation,compile_order_program
from .quote_program_compilation import compile_quote_program


def digest(value):return canonical_hash(domain_payload(value))


def intake_key(proposal,context):
    return 'generic-program:'+canonical_hash(dict(identity=context.identity_id,
        deployment=context.deployment_id,proposal_id=proposal.proposal_id))


def financial_intent_id(scope,key):
    # ledger.intents.record_id is globally unique; intake keys are scope-local.
    return 'program-financial:'+canonical_hash(dict(scope=scope_id(scope),intake_record_id=key))


@dataclass(frozen=True,slots=True,kw_only=True)
class GenericProgramIntake:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['GENERIC_PROGRAM_INTAKE']
    intent_id:str
    proposal:OrderProposal|OrderProposalV3
    context:t.DecisionContext
    compilation_hash:str
    book_sequence:int
    basis_fingerprint:str

    def __post_init__(self):
        require(self.contract_version=='2026-10-29' and self.record_kind=='GENERIC_PROGRAM_INTAKE',
            'PROGRAM_INTAKE_VERSION_UNSUPPORTED')
        VersionedOrderCodec().encode('order-proposal',self.proposal)
        require(self.proposal.action=='SET_TARGET' and self.proposal.program is not None,'PROGRAM_INTAKE_TARGET_REQUIRED')
        require(type(self.book_sequence) is int and self.book_sequence>0,'PROGRAM_INTAKE_BOOK_REQUIRED')
        require(type(self.compilation_hash) is str and len(self.compilation_hash)==64
            and all(c in '0123456789abcdef' for c in self.compilation_hash),'PROGRAM_INTAKE_COMPILATION_REQUIRED')
        identifier(self.intent_id);identifier(self.basis_fingerprint)

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteProgramIntakeV1(GenericProgramIntake):
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_PROGRAM_INTAKE_V1']
    proposal:QuotePositionProposalV1

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='QUOTE_PROGRAM_INTAKE_V1'
            and type(self.proposal) is QuotePositionProposalV1,'QUOTE_PROGRAM_INTAKE_VERSION_REQUIRED')
        VersionedOrderCodec().encode('order-proposal',self.proposal)
        require(type(self.book_sequence) is int and self.book_sequence>0,'PROGRAM_INTAKE_BOOK_REQUIRED')
        require(type(self.compilation_hash) is str and len(self.compilation_hash)==64
            and all(c in '0123456789abcdef' for c in self.compilation_hash),'PROGRAM_INTAKE_COMPILATION_REQUIRED')
        identifier(self.intent_id);identifier(self.basis_fingerprint)


def read_program_source(tx,key):
    """Only this access-controlled table establishes intake provenance."""
    row=tx.execute('SELECT scope_id,record_id,sequence,payload,transaction_id '
        'FROM ledger.order_program_intakes WHERE scope_id=%s AND record_id=%s',
        (scope_id(tx.scope),key)).fetchone()
    require(row is not None and row[0]==scope_id(tx.scope) and row[1]==key
        and type(row[2]) is int and row[2]==1 and type(row[4]) is str and bool(row[4]),
        'PROGRAM_INTAKE_REQUIRED')
    quote=row[3].get('record_kind')=='QUOTE_PROGRAM_INTAKE_V1'
    record=decode(QuoteProgramIntakeV1 if quote else GenericProgramIntake,row[3])
    context=record.context
    require(tx.scope in context.scopes and context.environment==tx.scope.environment
        and key==intake_key(record.proposal,context),'PROGRAM_INTAKE_SCOPE_OR_ID_MISMATCH')
    source_type=QuoteFinancialSourceV1 if quote else FinancialSource
    source=source_type(source_kind='POSITION_VALUE_BAND_PROPOSAL' if quote else 'ORDER_PROPOSAL',source_version=record.proposal.contract_version,
        source_hash=VersionedOrderCodec().hash('order-proposal',record.proposal),
        intake_record_id=key,intake_hash=canonical_hash(row[3]),intake_transaction_id=row[4],
        context=context,context_hash=digest(context))
    return source,record


def current_fence(tx,context):
    owner=tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
        'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
        (*account_key(tx.scope),tx.scope.instrument_uid,tx.scope.venue_leg)).fetchone()
    fence=tx._latest('audit_events',revision_key(context),IngressRevision)
    require(fence is not None and fence.enabled is True and type(fence.sequence) is int and fence.sequence>=1,
        'DEPLOYMENT_FENCE_REQUIRED')
    require((fence.identity_id,fence.deployment_id,fence.authorization_id,fence.strategy_revision,
        fence.risk_policy_revision,fence.ownership_epoch)==(context.identity_id,context.deployment_id,
        context.authorization_id,context.strategy_revision,context.policy_revision,context.ownership_epoch),
        'STALE_REVISION')
    require(owner==(fence.owner_strategy_id,fence.ownership_epoch,fence.risk_policy_revision),'STALE_OWNER')
    return fence


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramIntakeInputs:
    compilation:ProgramCompilation
    rules:OrderRuleSnapshot
    capabilities:OrderCapabilitySnapshot
    profile:TrustedProductRules
    authorization:AuthorizationBinding
    clock:t.ClockReading
    expected_build_digest:str
    expected_instrument_ref:str
    allow_round_down:bool


class GenericProgramIngress:
    """Trusted LOCAL readers; no production composition is registered here."""
    def __init__(self,intake_uow,scope,load_inputs,*,clock):
        require(scope.environment==t.Environment.PAPER,'ENVIRONMENT_DISABLED')
        self.uow,self.scope,self.load_inputs,self.clock=intake_uow,scope,load_inputs,clock

    def accept(self,proposal,context):
        codec=VersionedOrderCodec();codec.encode('order-proposal',proposal)
        require(proposal.action=='SET_TARGET' and proposal.program is not None,'PROGRAM_INTAKE_TARGET_REQUIRED')
        require(self.scope in context.scopes and context.environment==self.scope.environment,'CONTEXT_SCOPE_MISMATCH')
        key=intake_key(proposal,context)
        quote=type(proposal) is QuotePositionProposalV1
        record_type=QuoteProgramIntakeV1 if quote else GenericProgramIntake
        with self.uow.transaction(self.scope) as tx:
            fence=current_fence(tx,context)
            previous=tx._latest('order_program_intakes',key,record_type)
            if previous is not None:
                require(digest(previous.proposal)==digest(proposal) and digest(previous.context)==digest(context),
                    'PROGRAM_INTAKE_REPLAY_CONFLICT')
                result=read_program_source(tx,key)
            else:
                book=tx.projections.book(self.scope)
                facts=self.load_inputs(tx,proposal,context,book) if self.load_inputs else None
                require(type(facts) is ProgramIntakeInputs,'PROGRAM_INTAKE_INPUTS_REQUIRED')
                reading=self.clock();now=utc_timestamp(reading)
                auth=AuthorizationService(lambda _:facts.authorization,clock=lambda:now).validate(context)
                require(auth.allowed is True and not auth.rejection_codes
                    and facts.authorization.authorization.owner_strategy_id==fence.owner_strategy_id,
                    'AUTHORIZATION_REQUIRED')
                require(facts.clock.utc_now==reading and now<utc_timestamp(proposal.valid_until)
                    and now<utc_timestamp(proposal.program.submit_valid_until),'PROGRAM_INTAKE_EXPIRED')
                compiler=compile_quote_program if quote else compile_order_program
                plan=compiler(proposal,facts.rules,facts.capabilities,profile=facts.profile,
                    scope=self.scope,now=reading,expected_build_digest=facts.expected_build_digest,
                    expected_rule_revision=facts.rules.revision,
                    expected_capabilities_hash=codec.hash('order-capability-snapshot',facts.capabilities),
                    expected_instrument_ref=facts.expected_instrument_ref,allow_round_down=facts.allow_round_down)
                require(digest(plan)==digest(facts.compilation),'PROGRAM_INTAKE_COMPILATION_CHANGED')
                record=record_type(contract_version='2026-10-30' if quote else '2026-10-29',
                    record_kind='QUOTE_PROGRAM_INTAKE_V1' if quote else 'GENERIC_PROGRAM_INTAKE',
                    intent_id=financial_intent_id(self.scope,key),proposal=proposal,context=context,
                    compilation_hash=digest(plan),book_sequence=book.sequence,basis_fingerprint=book.basis_fingerprint)
                tx._append('order_program_intakes',key,record,0)
                tx._append('audit_events',revision_key(context),replace(fence,sequence=fence.sequence+1),fence.sequence)
                result=read_program_source(tx,key)
        return result  # commit barrier, never a financial ALLOW or permit
