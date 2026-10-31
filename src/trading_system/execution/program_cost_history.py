"""Source-rederived fresh Spot epoch cost/history; never a trading authority.

Only a protected first consumed empty owned book with a complete zero prefix can establish
this version's genesis. Ambiguous/legacy/transfer histories require a separate
future privileged genesis contract and fail closed here.
"""
from dataclasses import dataclass,replace
from fractions import Fraction
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.storage.models import scope_id,decode
from trading_system.storage.events import EventStore
from trading_system.ownership.registry import RegistryTx,OwnerRecord
from .fills import FillLedger
from .program_asset_reader import ProgramAssetPostingReader
from .program_asset_postings import economic_signature
from .program_owned_posting import need,require_owned_program_posting,_history_row
from .program_account_opening import read_program_account_opening
from .program_cost_math import POLICY,SpotCost,apply_spot_cost,exact_decimal


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramCostHistoryRecord:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_COST_HISTORY_V1']
    policy:Literal['SPOT_WAC_RATIONAL_V1']
    genesis_hash:str
    economics_hash:str
    native_identities:tuple[str,...]
    quote_asset:str
    remaining_cost_numerator:int
    remaining_cost_denominator:int
    realized_numerator:int
    realized_denominator:int
    owner_before:OwnerRecord
    owner_after:OwnerRecord
    position_sequence:int
    book_sequence:int
    valuation_missing:tuple[str,...]


def _hash(value):return canonical_hash(domain_payload(value))
def _id(sequence):return 'program-cost-history:'+str(sequence)


def _position(before,state,quote):
    realized=exact_decimal(state.realized)
    return replace(before.position,cost_evidence=t.Coverage(scope=before.position.scope,
        query_succeeded=True,pagination_complete=True,supported=True,missing=()),
        realized_pnl=None if realized is None else t.Money(currency=quote,amount=realized),
        unrealized_valuation=None)


def _values(before,state,facts,genesis,quote):
    return dict(contract_version='2026-10-30',record_kind='PROGRAM_COST_HISTORY_V1',policy=POLICY,
        genesis_hash=genesis,economics_hash=canonical_hash(dict(economics=[
            dict(identity=f.approval.identity_hash,hash=economic_signature(f)) for f in facts])),
        native_identities=tuple(f.approval.identity_hash for f in facts),quote_asset=quote,
        remaining_cost_numerator=state.cost.numerator,remaining_cost_denominator=state.cost.denominator,
        realized_numerator=state.realized.numerator,realized_denominator=state.realized.denominator,
        owner_before=before,owner_after=replace(before,sequence=before.sequence+1,position=_position(before,state,quote)),
        valuation_missing=('CURRENT_MARK_AND_VALUATION_FX_REQUIRED',)+
            (() if exact_decimal(state.realized) is not None else ('REALIZED_DECIMAL_REPRESENTATION_REQUIRED',)))


def _check_revision(tx,proof,values):
    expected=ProgramCostHistoryRecord(**values,position_sequence=proof.position_sequence,book_sequence=proof.book_sequence)
    need(proof==expected,'COST_HISTORY_PROOF_CHANGED')
    row=_history_row(tx,'audit_events',_id(proof.owner_after.sequence),1)
    p=_history_row(tx,'positions',proof.owner_after.position.position_id,proof.position_sequence)
    b=_history_row(tx,'book_snapshots','book',proof.book_sequence)
    o=_history_row(tx,'audit_events','ip16:owner',proof.owner_after.sequence)
    need(row is not None and p is not None and b is not None and o is not None
        and row[0]==domain_payload(proof) and p[0]==domain_payload(proof.owner_after.position)
        and o[0]==domain_payload(proof.owner_after) and all(r[1:]==row[1:] for r in (p,b,o)),
        'COST_HISTORY_TRANSACTION_REQUIRED')
    book=decode(t.BookSnapshot,b[0])
    need(book.scope==tx.scope and book.sequence==proof.book_sequence and book.positions==(proof.owner_after.position,),
        'COST_HISTORY_BOOK_REQUIRED')
    event_id=_id(proof.owner_after.sequence)+':'+scope_id(tx.scope)
    e=tx.execute('SELECT payload,payload_hash,xmin::text,transaction_id FROM ledger.domain_events WHERE event_id=%s',
        (event_id,)).fetchone()
    need(e is not None and e[0].get('scope')==domain_payload(tx.scope)
        and e[0].get('aggregate_id')=='program-cost:'+proof.owner_after.position.position_id
        and e[0].get('payload')==domain_payload(proof.owner_after.position)
        and canonical_hash(e[0])==e[1] and e[2:]==row[1:],'COST_HISTORY_EVENT_REQUIRED')
    out=tx.execute("SELECT xmin::text,transaction_id FROM ledger.outbox WHERE event_id=%s AND kind='DOMAIN_EVENT'",
        (event_id,)).fetchall()
    need(out==[row[1:]],'COST_HISTORY_OUTBOX_REQUIRED')


def _source_order(reader,fact,payload):
    """Read an actual protected market stream sequence, never lexical trade IDs."""
    if payload.get('record_kind')!='PAPER_PROGRAM_FILL_SOURCE_V2':return None
    snapshot=reader.sources.read_snapshot(fact.command_id,payload['source_snapshot_hash'])
    market=snapshot['verification'].get('market_evidence') or {}
    raw=fact.approval.normalized.raw
    inputs=[item for item in market.get('inputs',()) if item.get('event_hash')==raw.market_hash
        and item.get('event',{}).get('event_id')==raw.market_event_id]
    need(len(inputs)==1,'COST_NATIVE_ORDER_SOURCE_REQUIRED')
    event=inputs[0]['event']
    need(type(event.get('sequence')) is int and event['sequence']>0 and bool(event.get('stream_id'))
        and event.get('product')==str(fact.fill.scope.product)
        and event.get('instrument_uid')==fact.fill.scope.instrument_uid,'COST_NATIVE_ORDER_SOURCE_REQUIRED')
    return event['stream_id'],event['sequence']


def _derive(tx):
    need(tx.scope.product==t.Product.SPOT,'SPOT_COST_HISTORY_REQUIRED')
    registry=RegistryTx(tx);current=registry.owner()
    need(current is not None and current.mode=='ACTIVE' and not current.intervention_ids
        and registry.active_owner()==(current.position.owner_strategy_id,current.position.ownership_epoch,current.policy_revision),
        'CURRENT_OWNED_POSITION_REQUIRED')
    rows=tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id='ip16:owner' ORDER BY sequence",
        (scope_id(tx.scope),)).fetchall()
    owners=[decode(OwnerRecord,r[0]) for r in rows]
    need(owners and owners[-1]==current and [o.sequence for o in owners]==list(range(1,current.sequence+1)),
        'COST_OWNER_HISTORY_INCOMPLETE')
    initial=owners[0]
    need(initial.position.actual_quantity.value==0 and initial.mode=='ACTIVE' and not initial.intervention_ids
        and initial.transfer_id is None and initial.position.scope==tx.scope,'COST_ZERO_GENESIS_REQUIRED')
    reader=ProgramAssetPostingReader(tx,fill_ledger=FillLedger(None,tx.scope,trade_id_scope='INSTRUMENT'))
    sources=tx.execute('SELECT approval_id,payload FROM ledger.program_fill_source_approvals WHERE scope_id=%s ORDER BY approval_id',
        (scope_id(tx.scope),)).fetchall()
    facts={};transitions={};source_order={}
    for approval,payload in sources:
        f=reader.read(approval);key=f.approval.identity_hash
        need((f.original_owner_strategy_id,f.original_ownership_epoch)==
            (initial.position.owner_strategy_id,initial.position.ownership_epoch),'COST_EPOCH_HISTORY_REQUIRED')
        if key in facts:need(economic_signature(f)==economic_signature(facts[key]),'COST_SOURCE_CONFLICT');continue
        posted=require_owned_program_posting(tx,f)
        source_order[key]=_source_order(reader,f,payload)
        facts[key]=f;transitions.setdefault(posted.owner_after.sequence,[]).append((f,posted))
    need(facts and transitions,'COST_NATIVE_HISTORY_REQUIRED')
    # Every consumed creation in this epoch needs a committed query, including
    # commands whose real query returned zero fills. Absence is not empty history.
    from trading_system.recovery.original_creation import read_original_creation
    from .program_submission_observation import read_program_submission_observation
    consumed=tx.execute('SELECT command_id FROM ledger.program_permit_consumptions WHERE scope_id=%s',
        (scope_id(tx.scope),)).fetchall()
    covered_commands={f.command_id for f in facts.values()}
    for (command_id,) in consumed:
        original=read_original_creation(tx,command_id)
        need(original is not None and (original.initial.owner_strategy_id,original.initial.ownership_epoch)==
            (initial.position.owner_strategy_id,initial.position.ownership_epoch),'COST_EPOCH_HISTORY_REQUIRED')
        observation=read_program_submission_observation(tx,original.initial)
        need(observation is None or observation.submission_known,'COST_CURRENT_QUERY_REQUIRED')
        if command_id in covered_commands:
            need(observation is None or observation.state=='ACCEPTED','COST_CURRENT_QUERY_REQUIRED')
            continue
        snapshots=tx.execute('SELECT snapshot_id FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND command_id=%s',
            (scope_id(tx.scope),command_id)).fetchall()
        need(snapshots,'COST_COMMAND_HISTORY_INCOMPLETE')
        for (snapshot_id,) in snapshots:
            source=reader.sources.read_snapshot(command_id,snapshot_id);verification=source['verification']
            need(verification['source_verified'] is True and verification['status']=='VERIFIED'
                and verification['discrepancies']==[] and not source['normalized_fills'],
                'COST_COMMAND_HISTORY_INCOMPLETE')
    ledger=tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s',(scope_id(tx.scope),)).fetchall()
    need({decode(t.Fill,r[0]).fill_id for r in ledger}=={f.fill.fill_id for f in facts.values()},'COST_FILL_HISTORY_INCOMPLETE')
    first=min(transitions)
    need(first==2 and transitions[first][0][1].owner_before==initial,'COST_ZERO_GENESIS_REQUIRED')
    first_fact=transitions[first][0][0]
    protected=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
        (scope_id(tx.scope),first_fact.command_id)).fetchone()
    need(protected is not None,'COST_PROTECTED_GENESIS_REQUIRED')
    context=protected[0];assessment=context.get('assessment',{});book_payload=assessment.get('book',{})
    need(context.get('assessment_hash')==canonical_hash(assessment),'COST_PROTECTED_ZERO_BOOK_REQUIRED')
    # Financial scoped views can differ from the actual source book. Bind the
    # protected source hash when present and inspect its entire immutable prefix.
    wanted=context.get('source_book_hash') or canonical_hash(book_payload)
    book_rows=tx.execute("SELECT sequence,payload FROM ledger.book_snapshots WHERE scope_id=%s AND record_id='book' ORDER BY sequence",
        (scope_id(tx.scope),)).fetchall()
    candidates=[(seq,payload) for seq,payload in book_rows if canonical_hash(payload)==wanted]
    need(len(candidates)==1,'COST_PROTECTED_ZERO_BOOK_REQUIRED')
    genesis_sequence,genesis_book=candidates[0]
    prefix=[(seq,payload) for seq,payload in book_rows if seq<=genesis_sequence]
    need([seq for seq,_ in prefix]==list(range(1,genesis_sequence+1))
        and all(payload.get('sequence')==seq and payload.get('scope')==domain_payload(tx.scope)
            and payload.get('positions')==[] for seq,payload in prefix),'COST_ZERO_BOOK_HISTORY_REQUIRED')
    # Protected consumed context binds the first owner/epoch, not caller zeros.
    write=context.get('write',{})
    need(write.get('owner_strategy_id')==initial.position.owner_strategy_id
        and write.get('ownership_epoch')==initial.position.ownership_epoch,'COST_GENESIS_OWNER_REQUIRED')
    genesis=canonical_hash(dict(book=genesis_book,owner=domain_payload(initial),consumption=canonical_hash(context)))
    groups={}
    for f in facts.values():groups.setdefault(utc_timestamp(f.fill.occurred_at),[]).append(f)
    mixed_times=set()
    for when,group in groups.items():
        if len({f.approval.normalized.side for f in group})==1:continue
        mixed_times.add(when)
        orders=[source_order[f.approval.identity_hash] for f in group]
        need(all(order is not None for order in orders) and len({order[0] for order in orders})==1,
            'COST_NATIVE_ORDER_REQUIRED')
        sides={}
        for f,order in zip(group,orders):sides.setdefault(order[1],set()).add(f.approval.normalized.side)
        need(all(len(values)==1 for values in sides.values()),'COST_NATIVE_ORDER_REQUIRED')
    def ordering(f):
        order=source_order[f.approval.identity_hash]
        return utc_timestamp(f.fill.occurred_at),0 if order is None else order[1],f.approval.identity_hash
    state=SpotCost();applied=[];quote=None;native_base=None;last_time=None;last_rank=None;last_proof=None
    for before,after in zip(owners,owners[1:]):
        need(after.mode=='ACTIVE' and not after.intervention_ids and after.transfer_id is None
            and replace(after,position=before.position,sequence=before.sequence)==before,'COST_OWNER_TRANSITION_REQUIRED')
        batch=transitions.get(after.sequence)
        if batch:
            # Equal-time mixed sides require the protected native stream order.
            # Identity only stabilizes serialization inside a commuting group.
            ordered=sorted(batch,key=lambda pair:ordering(pair[0]))
            for f,posted in ordered:
                need(posted.owner_before==before and posted.owner_after==after,'COST_OWNER_HISTORY_INCOMPLETE')
                now=utc_timestamp(f.fill.occurred_at)
                need(last_time is None or now>=last_time,'COST_NATIVE_ORDER_REQUIRED')
                rank=ordering(f)[1]
                need(now!=last_time or now not in mixed_times or rank>=last_rank,'COST_NATIVE_ORDER_REQUIRED')
                last_time=now;last_rank=rank
                base=f.approval.normalized.economics.unit.native_unit
                currency=f.effects.gross_notional.currency
                need(native_base is None or native_base==base,'COST_BASE_UNIT_CHANGED');native_base=base
                need(quote is None or quote==currency,'COST_QUOTE_UNIT_CHANGED');quote=currency
                delta={m.currency:m.amount for m in f.effects.asset_deltas}
                state=apply_spot_cost(state,side=f.approval.normalized.side,base_delta=delta[base],quote_delta=delta[currency],
                    external_fee=any(m.currency not in (base,currency) and m.amount!=0 for m in f.effects.fees))
                applied.append(f)
            need(state.quantity==Fraction(after.position.actual_quantity.value),'COST_QUANTITY_MISMATCH')
            last_proof=None
        else:
            proof=tx._latest('audit_events',_id(after.sequence),ProgramCostHistoryRecord)
            need(proof is not None and applied,'COST_OWNER_HISTORY_INCOMPLETE')
            _check_revision(tx,proof,_values(before,state,applied,genesis,quote))
            need(proof.owner_after==after,'COST_OWNER_HISTORY_INCOMPLETE');last_proof=proof
    need(len(applied)==len(facts) and state.quantity==Fraction(current.position.actual_quantity.value),
        'COST_CURRENT_HISTORY_INCOMPLETE')
    return current,state,applied,genesis,quote,last_proof


def require_program_cost_history(tx,*,read_opening=read_program_account_opening):
    """Rederive current source, genesis, math and every owner/proof transition."""
    from .program_account_cash import read_program_account_cash
    need(read_program_account_cash(tx,read_opening=read_opening) is not None,
        'ACCOUNT_OPENING_REQUIRED')
    owner,state,facts,genesis,quote,proof=_derive(tx)
    need(proof is not None and tx.projections.book(tx.scope).positions==(owner.position,),
        'PROGRAM_COST_HISTORY_RECONCILIATION_REQUIRED')
    return proof


def _reconcile_program_cost_history(tx,*,read_opening):
    """Append caller-owned cost revision in its existing UoW; no fees/permits."""
    from .program_account_cash import read_program_account_cash
    from .program_book_cash import bind_program_book_cash
    frame=read_program_account_cash(tx,read_opening=read_opening)
    need(frame is not None,'ACCOUNT_OPENING_REQUIRED')
    owner,state,facts,genesis,quote,old=_derive(tx)
    book=tx.projections.book(tx.scope)
    need(book.positions==(owner.position,) and book.balances==frame.balances,'COST_CURRENT_BOOK_REQUIRED')
    if old is not None:return require_program_cost_history(tx,read_opening=read_opening)
    values=_values(owner,state,facts,genesis,quote);after=values['owner_after']
    RegistryTx(tx).save_owner(owner,after)
    head=tx.execute('SELECT COALESCE(max(sequence),0) FROM ledger.positions WHERE scope_id=%s AND record_id=%s',
        (scope_id(tx.scope),after.position.position_id)).fetchone()[0]
    position_sequence=tx._append('positions',after.position.position_id,after.position,head)
    updated=replace(book,sequence=book.sequence+1,positions=(after.position,),
        basis_fingerprint=canonical_hash(dict(prior=_hash(book),cost=values['economics_hash'],owner=_hash(after))))
    tx.projections.save_book(updated,book.sequence)
    proof=ProgramCostHistoryRecord(**values,position_sequence=position_sequence,book_sequence=updated.sequence)
    tx._append('audit_events',_id(after.sequence),proof,0)
    aggregate='program-cost:'+after.position.position_id
    head=tx.execute('SELECT sequence FROM ledger.aggregate_heads WHERE scope_id=%s AND aggregate_id=%s',
        (scope_id(tx.scope),'events:'+aggregate)).fetchone()
    revision=0 if head is None else head[0]
    ref=max((f.fill.evidence for f in facts),key=lambda r:utc_timestamp(r.visible_at))
    EventStore(received_at=ref.visible_at).append(tx,t.DomainEvent(event_id=_id(after.sequence)+':'+scope_id(tx.scope),
        scope=tx.scope,aggregate_id=aggregate,revision=revision+1,occurred_at=ref.visible_at,
        evidence=tuple(f.fill.evidence for f in facts),payload=after.position),revision)
    bind_program_book_cash(tx,updated,read_opening=read_opening)
    return require_program_cost_history(tx,read_opening=read_opening)


def reconcile_program_cost_history(tx,*,read_opening):
    """One existing owner UoW; even a caught write failure is rollback-only."""
    try:return _reconcile_program_cost_history(tx,read_opening=read_opening)
    except BaseException:
        tx.failed=True
        raise
