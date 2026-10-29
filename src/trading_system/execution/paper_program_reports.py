"""Exact PAPER source economics; no authorization or financial posting.

The complete report verifier is coupled to the independently reviewed matcher
interface. This first unit bridge preserves native facts for existing FillLedger
and carries the separate position-unit bridge for generic ledger accounting.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from hashlib import sha256
import json
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.program_financial import FinancialUnit,require
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,decimal_string
from trading_system.contracts.order_lifecycle_v3 import exact_time


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeFillEconomics:
    native_quantity:t.Quantity
    venue_price:t.Price
    ledger_quantity:t.Quantity
    unit:FinancialUnit
    reported_gross:t.Money
    expected_gross:t.Money
    fees:tuple[t.Money,...]
    discrepancies:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False

    @property
    def source_verified(self)->Literal[False]:return False


def normalize_native_economics(*,quantity,price,fees,reported_gross,unit):
    require(type(quantity) is t.Quantity and type(price) is t.Price
        and type(reported_gross) is t.Money and type(unit) is FinancialUnit,
        'PAPER_TYPED_NATIVE_ECONOMICS_REQUIRED')
    require(quantity.unit==unit.native_unit and quantity.value>0 and price.value>0,
        'PAPER_NATIVE_FILL_UNIT_OR_VALUE_INVALID')
    require(type(fees) is tuple and all(type(f) is t.Money for f in fees),
        'PAPER_EXPLICIT_FEE_FACTS_REQUIRED')
    # Reuse the existing immutable unit contract, including its exact multiplier
    # identity. Native quantity/price/fees are never overwritten or FX converted.
    unit.__post_init__()
    with localcontext() as arithmetic:
        arithmetic.prec=256
        arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        ledger_value=quantity.value*unit.ledger_units_per_native_unit
        native_gross=quantity.value*price.value*unit.native_multiplier
        ledger_gross=ledger_value*price.value*unit.ledger_multiplier
    require(native_gross==ledger_gross,'PAPER_UNIT_GROSS_BRIDGE_MISMATCH')
    discrepancies=()
    if reported_gross.currency!=price.currency:
        discrepancies=('GROSS_CURRENCY_MISMATCH',)
    elif reported_gross.amount!=native_gross:
        discrepancies=('GROSS_NOTIONAL_MISMATCH',)
    return NativeFillEconomics(native_quantity=quantity,venue_price=price,
        ledger_quantity=t.Quantity(value=ledger_value,unit=unit.ledger_unit),unit=unit,
        reported_gross=reported_gross,expected_gross=t.Money(amount=native_gross,currency=price.currency),
        fees=fees,discrepancies=discrepancies)


def digest(value):return canonical_hash(domain_payload(value))


class _SourceError(ValueError):pass


def _check(condition,code):
    if not condition:raise _SourceError(code)


def _plain_object(pairs):
    result={}
    for key,value in pairs:
        _check(key not in result,'PAPER_RAW_DUPLICATE_KEY')
        result[key]=value
    return result


def _nonfinite_source(value):raise _SourceError('PAPER_RAW_NONFINITE_NUMBER')


def _raw_object(text,ref):
    _check(type(text) is str and len(text.encode('utf-8'))<=1048576,'PAPER_RAW_SOURCE_BOUND')
    _check(sha256(text.encode('utf-8')).hexdigest()==ref.hash,'PAPER_RAW_SOURCE_HASH_CHANGED')
    try:value=json.loads(text,parse_float=Decimal,parse_constant=_nonfinite_source,object_pairs_hook=_plain_object)
    except (ValueError,TypeError) as exc:raise _SourceError('PAPER_RAW_SOURCE_INVALID') from exc
    _check(type(value) is dict and value.get('source')=='PAPER_MARKET_REPLAY_SYNTHETIC',
        'PAPER_RAW_SOURCE_CHANGED')
    return value


def _raw_decimal(value):
    _check(type(value) is str,'PAPER_RAW_DECIMAL_REQUIRED')
    try:return decimal_string(value)
    except ValueError as exc:raise _SourceError('PAPER_RAW_DECIMAL_REQUIRED') from exc


@dataclass(frozen=True,slots=True,kw_only=True)
class NormalizedPaperFill:
    fill:t.Fill
    side:Literal['BUY','SELL']
    economics:NativeFillEconomics
    raw:object
    fees_known:bool


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperProgramVerification:
    command_id:str
    status:Literal['BLOCKED','NOT_OBSERVED','VERIFIED','DISCREPANCY']
    source_verified:bool
    fills:tuple[NormalizedPaperFill,...]
    orders:tuple[t.OrderProjection,...]
    raw_refs:tuple[t.EvidenceRef,...]
    discrepancies:tuple[str,...]
    retained_duties:tuple[str,...]
    raw_report:object|None=None
    market_evidence:object|None=None

    @property
    def synthetic(self)->Literal[True]:return True
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


class PaperProgramReportVerifier:
    """Trusted host composition. Only an original command ID is a request input.

    This service reads and archives facts, never posts fills, positions, cash,
    command outcomes, permissions or collateral credit. The integrator supplies
    actual existing UoW/PaperEngine/NativeEventBridge; no credentials are found.
    """
    def __init__(self,uow,scope,*,engine=None,archive_bridge=None):
        require(type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,'PAPER_ONLY')
        self.uow,self.scope,self.engine,self.archive_bridge=uow,scope,engine,archive_bridge

    def _result(self,command_id,status,codes=(),*,report=None,market=None,refs=(),fills=(),orders=(),verified=False):
        duties=('QUERY_ORIGINAL_IDS','RESERVATION_RECONCILIATION_REQUIRED','ACCOUNT_HISTORY_NOT_VERIFIED')
        if fills:duties+=('PROTECTION_RECHECK_REQUIRED','OWNERSHIP_ALLOCATION_RECONCILIATION_REQUIRED')
        if codes:duties+=('SOURCE_OR_ECONOMIC_RECONCILIATION_REQUIRED',)
        return PaperProgramVerification(command_id=command_id,status=status,source_verified=verified,
            fills=fills,orders=orders,raw_refs=refs,discrepancies=tuple(dict.fromkeys(codes)),retained_duties=duties,
            raw_report=report,market_evidence=market)

    def _load(self,command_id):
        from trading_system.recovery.original_creation import read_original_creation
        from trading_system.risk.program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
        from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
        from trading_system.storage.models import scope_id,decode
        with self.uow.transaction(self.scope) as tx:
            original=read_original_creation(tx,command_id)
            _check(original is not None,'PAPER_ORIGINAL_CREATION_NOT_FOUND')
            _check(original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),'PAPER_PROTECTED_CONSUMPTION_REQUIRED')
            row=tx.execute('SELECT c.context,c.token_hash,m.token_hash FROM ledger.program_permit_consumptions c '
                'JOIN ledger.program_dispatch_marks m USING(scope_id,command_id) '
                'WHERE c.scope_id=%s AND c.command_id=%s',(scope_id(self.scope),command_id)).fetchone()
            _check(row is not None and row[1]==row[2],'PAPER_PROTECTED_CONSUMPTION_REQUIRED')
            consumption=row[0]
            stored=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s '
                'AND record_id=%s AND sequence=1',(scope_id(self.scope),BUNDLE_PREFIX+original.initial.intent_id)).fetchone()
            _check(stored is not None,'PAPER_ORIGINAL_BUNDLE_REQUIRED')
            bundle=decode(ProgramFinancialBundle,stored[0])
            assessment=decode(ProgramFinancialAssessmentArchive,consumption['assessment'])
            _check(canonical_hash(consumption['write'])==digest(replace(original.initial,state='MAY_HAVE_SENT'))
                and consumption['request_hash']==original.initial.request_hash
                and consumption['assessment_hash']==digest(assessment)
                and assessment.intent_id==original.initial.intent_id
                and digest(assessment.compilation)==original.initial.compilation_hash,
                'PAPER_PROTECTED_CONTEXT_CHANGED')
        return original,bundle,consumption,assessment

    def _archive(self,payload,clock):
        raw=payload if type(payload) is bytes else canonical_json(payload).encode('utf-8')
        return self.archive_bridge.ingest(raw,source='PAPER_MARKET_REPLAY_SYNTHETIC',
            received_at=clock.utc_now,processed_at=clock.utc_now).raw.raw_payload_ref

    def verify(self,command_id):
        from trading_system.engine.paper import PaperEngine
        from trading_system.engine.nautilus.native_events import NativeEventBridge
        from trading_system.engine.paper_market import PaperCreationReport,PaperMarketEvidence
        require(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        if (self.uow is None or type(self.engine) is not PaperEngine or self.engine.scope!=self.scope
                or type(self.archive_bridge) is not NativeEventBridge or self.archive_bridge.codec.scope!=self.scope):
            return self._result(command_id,'BLOCKED',('PAPER_TRUSTED_READERS_REQUIRED',))
        report=market=None;refs=[]
        try:
            original,bundle,consumption,assessment=self._load(command_id)
            # The business transaction is closed before any venue read/archive IO.
            report=self.engine.query_creation(command_id)
            if report is None:return self._result(command_id,'NOT_OBSERVED',('PAPER_QUERY_ORIGINAL_IDS_REQUIRED',))
            market=self.engine.query_market_evidence(command_id)
            clock=self.engine._clock()
            _check(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY','PAPER_SOURCE_CLOCK_REQUIRED')
            _check(type(report) is PaperCreationReport and type(market) is PaperMarketEvidence,'PAPER_SOURCE_TYPES_REQUIRED')
            refs.append(self._archive(domain_payload(report),clock))
            refs.append(self._archive(domain_payload(market),clock))
            for item in (*report.fills,*report.dispositions):
                refs.append(self._archive(item.raw_payload_json.encode('utf-8'),clock))
            for item in market.inputs:refs.append(self._archive(item.raw_payload_json.encode('utf-8'),clock))
            fills,orders,codes=self._normalize(original,bundle,consumption,assessment,report,market,clock,refs)
            return self._result(command_id,'DISCREPANCY' if codes else 'VERIFIED',codes,
                report=report,market=market,refs=tuple(refs),fills=fills,orders=orders,verified=True)
        except _SourceError as error:
            return self._result(command_id,'BLOCKED',(str(error),),report=report,market=market,refs=tuple(refs))
        except (ValueError,TypeError,KeyError,ArithmeticError,StopIteration):
            return self._result(command_id,'BLOCKED',('PAPER_SOURCE_SHAPE_OR_ECONOMICS_INVALID',),
                report=report,market=market,refs=tuple(refs))

    def _normalize(self,original,bundle,consumption,assessment,report,market,clock,refs):
        from trading_system.engine.paper_market import PaperExecutionTerms,PaperExecutionEnvelope,PaperAcceptedContext,SOURCE
        from trading_system.contracts.order_lifecycle import AssetBudget
        from trading_system.execution.paper_program_liquidity import market_evidence_codes
        from trading_system.storage.models import scope_id
        request=original.request;write=original.initial;plan=assessment.compilation
        _check(report.command_id==market.command_id==write.command_id and digest(report.request)==write.request_hash
            and market.request_hash==write.request_hash,'PAPER_REPORT_REQUEST_CHANGED')
        body=dict(request=domain_payload(report.request),orders=[domain_payload(o) for o in report.orders],
            fills=[domain_payload(f) for f in report.fills],accepted_contexts=[domain_payload(c) for c in report.accepted_contexts],
            dispositions=[domain_payload(d) for d in report.dispositions])
        _check(report.evidence.environment==t.Environment.PAPER and report.evidence.source==SOURCE
            and report.evidence.id=='paper-market-query:'+write.command_id
            and exact_time(report.evidence.visible_at)<=exact_time(clock.utc_now)
            and canonical_hash(body)==report.evidence.hash,'PAPER_REPORT_HASH_CHANGED')
        codes=market_evidence_codes(market,scope=self.scope,command_id=write.command_id,
            request_hash=write.request_hash,now=clock.utc_now)
        if codes:raise _SourceError(codes[0])
        _check(len(report.orders)==len(report.accepted_contexts)==1 and len(plan.nodes)==1
            and plan.original_program.template=='ATOMIC','PAPER_REPORT_PROGRAM_INTERFACE_UNSUPPORTED')
        accepted=report.accepted_contexts[0];order=report.orders[0];node=plan.nodes[0];spec=node.original.order
        _check(node.order_type in ('LIMIT','LIMIT_MAKER') and spec.price.kind=='FIXED_LIMIT'
            and spec.trigger.kind=='NONE','PAPER_REPORT_ORDER_INTERFACE_UNSUPPORTED')
        _check(type(accepted) is PaperAcceptedContext and digest(accepted.original_write)==digest(replace(write,state='MAY_HAVE_SENT'))
            and digest(accepted.request)==write.request_hash and digest(accepted.compilation)==write.compilation_hash
            and digest(accepted.rules)==digest(assessment.rules),'PAPER_ACCEPTED_SOURCE_CHANGED')
        ids=request.assigned_ids
        _check(len(ids.members)==1 and ids.parent_client_id is None,'PAPER_REPORT_MEMBER_INTERFACE_UNSUPPORTED')
        client=ids.members[0].client_id
        expected_terms=PaperExecutionTerms(scope=self.scope,client_id=client,node_id=node.original.node_id,
            namespace=request.namespace.value,quantity=node.quantity.value,quantity_step=assessment.rules.quantity_step,
            side=spec.side,order_type=node.order_type,limit_price=spec.price.value,post_only=spec.post_only,
            tif='GTC' if node.order_type=='LIMIT_MAKER' else spec.tif.kind,multiplier=node.quantity_multiplier,
            quote_asset=assessment.rules.quote_asset,quote_fee_rate=accepted.model.quote_fee_rate,
            accepted_clock=accepted.terms.accepted_clock)
        _check(digest(expected_terms)==digest(accepted.terms),'PAPER_ACCEPTED_TERMS_CHANGED')
        bounds=tuple(b for b in assessment.risk_facts.node_bounds if b.node_id==node.original.node_id)
        _check(len(bounds)==1 and bounds[0].execution_fees is not None,'PAPER_EXECUTION_BOUND_MISSING')
        bound=bounds[0];ceilings={}
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            for fee in bound.execution_fees:
                ceilings[fee.amount.currency]=ceilings.get(fee.amount.currency,Decimal(0))+fee.amount.amount
        envelope=PaperExecutionEnvelope(contract_version='2026-10-29',record_kind='PAPER_EXECUTION_ENVELOPE',scope=self.scope,
            command_id=write.command_id,request_hash=write.request_hash,consumption_context_hash=canonical_hash(consumption),
            assessment_hash=digest(assessment),source_ref='ledger.program_permit_consumptions:'+scope_id(self.scope)+':'+write.command_id,
            node_id=node.original.node_id,minimum_price=None if bound.minimum_price is None else format(bound.minimum_price.value,'f'),
            maximum_price=None if bound.maximum_price is None else format(bound.maximum_price.value,'f'),
            maximum_gross_notional=format(assessment.assessment.metrics.maximum_order_notional.amount,'f'),
            fee_ceilings=tuple(AssetBudget(asset=asset,amount=format(value,'f')) for asset,value in sorted(ceilings.items())))
        _check(digest(envelope)==digest(accepted.execution_envelope),'PAPER_EXECUTION_ENVELOPE_CHANGED')
        accepted_at=exact_time(accepted.terms.accepted_clock.utc_now)
        _check(accepted.terms.accepted_clock.clock_health=='HEALTHY'
            and exact_time(consumption['clock']['utc_now'])<=accepted_at<exact_time(consumption['valid_until'])
            and accepted_at<=exact_time(clock.utc_now),'PAPER_ACCEPTED_CLOCK_CHANGED')
        _check(order.client_id==client and order.node_id==node.original.node_id and order.namespace==request.namespace.value
            and order.quantity_unit==bundle.intent.unit.native_unit and order.venue_order_id,
            'PAPER_ORDER_IDENTITY_CHANGED')
        _check(type(getattr(order,'lifecycle_revision',None)) is int and order.lifecycle_revision>=0,
            'PAPER_ORDER_REVISION_REQUIRED')
        identity=t.OrderIdentity(scope=self.scope,logical_client_order_id=client,native_client_order_id=client,
            venue_client_order_id=client,venue_order_id=order.venue_order_id)
        inputs={(i.event.market_key,i.event.event_id):i for i in market.inputs}
        allocations={(a.scope.venue_account_id,a.client_id,a.fill_id):a for a in market.consumptions
            if a.scope==self.scope and a.client_id==client}
        expected_ids={f.fill_id for f in report.fills}
        _check(len(expected_ids)==len(report.fills) and {key[2] for key in allocations}==expected_ids,
            'PAPER_FILL_ALLOCATION_SET_CHANGED')
        normalized=[];economic=[];total=Decimal(0);gross_total=Decimal(0);fee_totals={}
        for fill in report.fills:
            raw=_raw_object(fill.raw_payload_json,fill.evidence)
            _check(fill.evidence.environment==t.Environment.PAPER and fill.evidence.source==SOURCE
                and fill.evidence.id==fill.fill_id and exact_time(fill.evidence.visible_at)==exact_time(fill.occurred_at),
                'PAPER_FILL_SOURCE_REFERENCE_CHANGED')
            _check(fill.client_id==client and fill.node_id==node.original.node_id and fill.namespace==request.namespace.value
                and raw.get('record_kind')=='PAPER_MARKET_FILL' and raw.get('event_id')==fill.fill_id
                and raw.get('client_id')==client and raw.get('node_id')==fill.node_id
                and raw.get('namespace')==fill.namespace,'PAPER_FILL_IDENTITY_CHANGED')
            source=inputs.get((raw.get('market_key'),fill.market_event_id))
            allocation=allocations.get((self.scope.venue_account_id,client,fill.fill_id))
            _check(source is not None and allocation is not None and allocation.scope==self.scope
                and allocation.side==('ASK' if spec.side=='BUY' else 'BID')
                and allocation.price==fill.price and allocation.quantity==fill.quantity
                and raw.get('market_event_id')==fill.market_event_id and raw.get('market_hash')==fill.market_hash==source.event_hash,
                'PAPER_FILL_MARKET_LINK_CHANGED')
            _check(_raw_decimal(raw['quantity'])==fill.quantity and _raw_decimal(raw['price'])==fill.price
                and _raw_decimal(raw['gross_notional'])==fill.gross_notional and raw['fees']==[domain_payload(f) for f in fill.fees]
                and canonical_hash(raw['model'])==digest(accepted.model)
                and canonical_hash(raw['execution_envelope'])==digest(envelope),'PAPER_FILL_RAW_FIELDS_CHANGED')
            _check(exact_time(raw['execution_at'])==exact_time(fill.occurred_at),'PAPER_FILL_EXECUTION_TIME_CHANGED')
            _check(exact_time(fill.occurred_at)>=max(accepted_at,exact_time(source.event.occurred_at),
                exact_time(source.observed_clock.utc_now)) and exact_time(fill.occurred_at)<=exact_time(clock.utc_now),
                'PAPER_FILL_CLOCK_CHANGED')
            economics=normalize_native_economics(quantity=t.Quantity(value=fill.quantity,unit=fill.quantity_unit),
                price=t.Price(value=fill.price,currency=fill.price_asset),
                fees=tuple(t.Money(amount=Decimal(f.amount),currency=f.asset) for f in fill.fees),
                reported_gross=t.Money(amount=fill.gross_notional,currency=fill.price_asset),unit=bundle.intent.unit)
            economic.extend(economics.discrepancies)
            if fill.price_asset!=assessment.rules.quote_asset:economic.append('FILL_QUOTE_ASSET_MISMATCH')
            if spec.side=='BUY' and fill.price>Decimal(spec.price.value) or spec.side=='SELL' and fill.price<Decimal(spec.price.value):
                economic.append('FILL_OUTSIDE_ORIGINAL_LIMIT')
            if bound.minimum_price is not None and fill.price<bound.minimum_price.value or bound.maximum_price is not None and fill.price>bound.maximum_price.value:
                economic.append('FILL_OUTSIDE_EXECUTION_ENVELOPE')
            with localcontext() as arithmetic:
                arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
                total+=fill.quantity;gross_total+=fill.gross_notional
                for fee in economics.fees:fee_totals[fee.currency]=fee_totals.get(fee.currency,Decimal(0))+fee.amount
                expected_fee=economics.expected_gross.amount*Decimal(accepted.model.quote_fee_rate)
                actual_quote=sum((f.amount for f in economics.fees if f.currency==assessment.rules.quote_asset),Decimal(0))
            if actual_quote!=expected_fee or any(f.currency!=assessment.rules.quote_asset for f in economics.fees):
                economic.append('FILL_FEE_MODEL_DISCREPANCY')
            # Empty explicit PAPER fees plus the proven zero-rate model mean
            # known zero, whereas FillLedger's empty fee tuple means unknown.
            ledger_fees=economics.fees or (t.Money(amount=Decimal(0),currency=assessment.rules.quote_asset),)
            ref=next(r for r in refs if r.hash==fill.evidence.hash)
            fact=t.Fill(fill_id='paper-program:'+canonical_hash(dict(scope=domain_payload(self.scope),native_trade_id=fill.fill_id)),
                venue='BINANCE',scope=self.scope,native_trade_id=fill.fill_id,order=identity,quantity=economics.native_quantity,
                price=economics.venue_price,fees=ledger_fees,occurred_at=fill.occurred_at,correction_of=None,evidence=ref)
            normalized.append(NormalizedPaperFill(fill=fact,side=spec.side,economics=economics,raw=fill,fees_known=True))
        _check(total==order.filled_quantity,'PAPER_CUMULATIVE_FILL_SOURCE_CHANGED')
        _check(order.status!='FILLED' or total>=order.quantity,'PAPER_FILLED_QUANTITY_CHANGED')
        if order.quantity!=Decimal(node.quantity.value):economic.append('ORDER_ORIGINAL_QUANTITY_MISMATCH')
        if total>order.quantity:economic.append('ORDER_OVERFILLED')
        if gross_total>Decimal(envelope.maximum_gross_notional):economic.append('PROGRAM_GROSS_ENVELOPE_EXCEEDED')
        if any(value>ceilings.get(asset,Decimal(0)) for asset,value in fee_totals.items()):economic.append('PROGRAM_FEE_ENVELOPE_EXCEEDED')
        terminal=order.status in ('FILLED','CANCELED','EXPIRED','REJECTED')
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            expected_remaining=Decimal(0) if terminal else order.quantity-order.filled_quantity
        _check(order.remaining_quantity==expected_remaining and expected_remaining>=0,'PAPER_REMAINING_SOURCE_CHANGED')
        _check(len({d.event_id for d in report.dispositions})==len(report.dispositions),'PAPER_DISPOSITION_DUPLICATED')
        for disposition in report.dispositions:
            raw=_raw_object(disposition.raw_payload_json,disposition.evidence)
            source=next((s for s in market.inputs if s.event.event_id==disposition.market_event_id),None)
            _check(source is not None and disposition.client_id==client and raw.get('record_kind')=='PAPER_MARKET_DISPOSITION'
                and raw.get('status')==disposition.status and raw.get('reason')==disposition.reason
                and raw.get('market_event_id')==disposition.market_event_id and raw.get('market_key')==source.event.market_key
                and raw.get('market_hash')==disposition.market_hash==source.event_hash
                and canonical_hash(raw['model'])==digest(accepted.model)
                and canonical_hash(raw['execution_envelope'])==digest(envelope),
                'PAPER_DISPOSITION_SOURCE_CHANGED')
            _check(disposition.evidence.environment==t.Environment.PAPER and disposition.evidence.source==SOURCE
                and disposition.evidence.id==disposition.event_id
                and exact_time(disposition.evidence.visible_at)==exact_time(disposition.occurred_at)
                and exact_time(raw['execution_at'])==exact_time(disposition.occurred_at)
                and max(accepted_at,exact_time(source.event.occurred_at),exact_time(source.observed_clock.utc_now))
                    <=exact_time(disposition.occurred_at)<=exact_time(clock.utc_now),'PAPER_DISPOSITION_CLOCK_CHANGED')
        if order.status in ('EXPIRED','REJECTED'):
            _check(any(d.status==order.status for d in report.dispositions),'PAPER_TERMINAL_DISPOSITION_REQUIRED')
        projection=t.OrderProjection(order=t.OwnedOrderRef(identity=identity,owner_strategy_id=write.owner_strategy_id,
            ownership_epoch=write.ownership_epoch),lifecycle_state=t.OrderLifecycle(order.status),pending_operation='NONE',
            command_certainty='CONFIRMED',outcome_provenance='VENUE_QUERY',
            filled_quantity=t.Quantity(value=order.filled_quantity,unit=order.quantity_unit),
            leaves_quantity=t.Quantity(value=order.remaining_quantity,unit=order.quantity_unit),
            lifecycle_revision=order.lifecycle_revision,sequence=0)
        return tuple(normalized),(projection,),tuple(dict.fromkeys(economic))
