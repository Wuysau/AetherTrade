"""Opt-in Spot owner posting in the caller's existing ledger transaction.

No caller DTO supplies capital, owner allocation or fill quantity. Current
RegistryTx, active owner, original consumed command, protected source and real
FillLedger are reread. Cost/history/protection uncertainty remains explicit.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.events import EventStore
from trading_system.storage.models import MissingEvidence,scope_id,decode
from trading_system.ownership.registry import RegistryTx,OwnerRecord
from .program_asset_reader import ProgramAssetPostingReader
from .program_asset_postings import ProgramAssetPostingRecord,make_program_asset_posting,economic_signature
from .program_account_cash import read_program_account_cash,observe_program_account_cash
from .program_account_opening import read_program_account_opening
from .program_legacy_opening import read_program_local_account_opening,read_program_legacy_opening
from .program_book_cash import bind_program_book_cash,require_current_program_book_cash
from .fills import FillLedger


def need(condition,code):
    if not condition:raise MissingEvidence(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramOwnedPostingRecord:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_OWNED_POSTING_V1','PROGRAM_OWNED_POSTING_V2']
    identity_hash:str
    source_economic_hash:str
    owner_after:OwnerRecord
    position_sequence:int
    book_sequence:int
    owner_before:OwnerRecord|None=None
    source_approval_ids:tuple[str,...]=()


def _history_row(tx,table,record_id,sequence):
    return tx.execute('SELECT payload,xmin::text,transaction_id FROM ledger.'+table+
        ' WHERE scope_id=%s AND record_id=%s AND sequence=%s',
        (scope_id(tx.scope),record_id,sequence)).fetchone()


def fee_cashflow(fact,index,fee):
    ref=fact.fill.evidence
    leg=t.EvidenceRef(id=ref.id+'#program-fee:'+fact.approval.identity_hash+':'+str(index),
        hash=canonical_hash(dict(source=domain_payload(ref),index=index,fee=domain_payload(fee))),
        source='PROGRAM_NATIVE_FEE_LEG',environment=ref.environment,visible_at=ref.visible_at)
    return t.Cashflow(at=fact.fill.occurred_at,amount=replace(fee,amount=fee.amount.copy_negate()),
        classification='FEE',internal_pair_id=None,evidence=leg)


def require_owned_program_posting(tx,fact):
    """Rederive a committed native-to-owner transition, never trust its marker."""
    identity=fact.approval.identity_hash
    row=_history_row(tx,'audit_events','program-owned-posting:'+identity,1)
    need(row is not None,'UNPOSTED_PROGRAM_OWNED_EFFECTS')
    try:posted=decode(ProgramOwnedPostingRecord,row[0])
    except (ValueError,TypeError,KeyError) as error:raise MissingEvidence('OWNED_POSTING_HISTORY_REQUIRED') from error
    before,after=posted.owner_before,posted.owner_after
    need(posted.record_kind=='PROGRAM_OWNED_POSTING_V2' and before is not None
        and posted.position_sequence>0 and posted.book_sequence>0 and bool(posted.source_approval_ids)
        and posted.identity_hash==identity and posted.source_economic_hash==economic_signature(fact)
        and after.sequence==before.sequence+1
        and replace(after,position=before.position,sequence=before.sequence)==before,
        'OWNED_POSTING_HISTORY_REQUIRED')
    oldrow=_history_row(tx,'audit_events','ip16:owner',before.sequence)
    newrow=_history_row(tx,'audit_events','ip16:owner',after.sequence)
    prow=_history_row(tx,'positions',after.position.position_id,posted.position_sequence)
    brow=_history_row(tx,'book_snapshots','book',posted.book_sequence)
    need(oldrow is not None and oldrow[0]==domain_payload(before) and newrow is not None
        and newrow[0]==domain_payload(after) and prow is not None and prow[0]==domain_payload(after.position)
        and brow is not None and all(v[1:]==row[1:] for v in (newrow,prow,brow)),
        'OWNED_POSTING_HISTORY_REQUIRED')
    book=decode(t.BookSnapshot,brow[0])
    need(book.scope==after.position.scope==before.position.scope==tx.scope and book.sequence==posted.book_sequence
        and book.positions==(after.position,), 'OWNED_POSTING_HISTORY_REQUIRED')
    facts=[];ids=set();reader=ProgramAssetPostingReader(tx,fill_ledger=FillLedger(None,tx.scope,trade_id_scope='INSTRUMENT'))
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        quantity=before.position.actual_quantity.value
        for approval in posted.source_approval_ids:
            member=reader.read(approval);key=member.approval.identity_hash
            need(key not in ids and (member.original_owner_strategy_id,member.original_ownership_epoch)==(
                before.position.owner_strategy_id,before.position.ownership_epoch)
                and member.effects.position_delta.unit==before.position.actual_quantity.unit,'OWNED_POSTING_HISTORY_REQUIRED')
            ids.add(key);facts.append(member);quantity+=member.effects.position_delta.value
        need(identity in ids and after.position.actual_quantity==replace(before.position.actual_quantity,value=quantity)
            and after.position.owner_strategy_id==before.position.owner_strategy_id
            and after.position.owner_book_id==before.position.owner_book_id
            and after.position.ownership_epoch==before.position.ownership_epoch,'OWNED_POSTING_HISTORY_REQUIRED')
    for member in facts:
        for index,fee in enumerate(member.effects.fees):
            cash=_history_row(tx,'cashflows','program-fee:'+member.approval.identity_hash+':'+str(index),1)
            need(cash is not None and cash[0]==domain_payload(fee_cashflow(member,index,fee)) and cash[1:]==row[1:],
                'OWNED_POSTING_HISTORY_REQUIRED')
    event_id='program-position:'+canonical_hash(dict(scope=domain_payload(tx.scope),identities=sorted(ids)))
    event=tx.execute('SELECT payload,payload_hash,xmin::text,transaction_id FROM ledger.domain_events WHERE event_id=%s',
        (event_id,)).fetchone()
    need(event is not None and event[0].get('scope')==domain_payload(tx.scope)
        and event[0].get('aggregate_id')=='program-position:'+after.position.position_id
        and event[0].get('payload')==domain_payload(after.position)
        and event[1]==canonical_hash(event[0]) and event[2:]==row[1:], 'OWNED_POSTING_HISTORY_REQUIRED')
    outbox=tx.execute("SELECT xmin::text,transaction_id FROM ledger.outbox WHERE event_id=%s AND kind='DOMAIN_EVENT'",
        (event_id,)).fetchall()
    need(len(outbox)==1 and outbox[0]==row[1:],'OWNED_POSTING_HISTORY_REQUIRED')
    return posted


class ProgramOwnedSpotPosting:
    def __init__(self,*,read_opening):
        need(any(read_opening is r for r in (read_program_account_opening,read_program_legacy_opening,
            read_program_local_account_opening)),'TRUSTED_ACCOUNT_OPENING_READER_REQUIRED')
        self.read_opening=read_opening

    def post(self,tx,approvals,fill_ledger):
        need(tx.scope.product==t.Product.SPOT,'PRODUCT_SETTLEMENT_FACTS_REQUIRED')
        opening=self.read_opening(tx)
        need(opening is not None,'ACCOUNT_OPENING_REQUIRED')
        registry=RegistryTx(tx);owner=registry.owner()
        need(owner is not None,'CURRENT_OWNED_POSITION_REQUIRED')
        p=owner.position;book=tx.projections.book(tx.scope)
        need(p.scope==tx.scope and owner.mode=='ACTIVE' and not owner.intervention_ids
            and registry.active_owner()==(p.owner_strategy_id,p.ownership_epoch,owner.policy_revision),
            'CURRENT_OWNED_POSITION_REQUIRED')
        need(book.positions==(p,) or (not book.positions and p.actual_quantity.value==0),'OWNED_POSITION_BOOK_MISMATCH')
        reader=ProgramAssetPostingReader(tx,fill_ledger=fill_ledger)
        facts={}
        for approval in approvals:
            fact=reader.read(approval.approval_id)
            need((fact.original_owner_strategy_id,fact.original_ownership_epoch)==(p.owner_strategy_id,p.ownership_epoch),
                'NATIVE_FILL_CURRENT_OWNER_MISMATCH')
            identity=fact.approval.identity_hash
            need(identity not in facts or economic_signature(facts[identity])==economic_signature(fact),
                'PROGRAM_NATIVE_ECONOMICS_CONFLICT')
            facts[identity]=fact
        pending=[]
        for identity,fact in facts.items():
            posted=tx._latest('audit_events','program-owned-posting:'+identity,ProgramOwnedPostingRecord)
            if posted is None:pending.append(fact);continue
            posted=require_owned_program_posting(tx,fact)
            need(posted.identity_hash==identity and posted.source_economic_hash==economic_signature(fact)
                and posted.owner_after.position.owner_book_id==p.owner_book_id
                and posted.owner_after.position.ownership_epoch==p.ownership_epoch,'OWNED_POSTING_NATIVE_CONFLICT')
            row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=%s',
                (scope_id(tx.scope),'ip16:owner',posted.owner_after.sequence)).fetchone()
            need(row is not None and row[0]==domain_payload(posted.owner_after),'OWNED_POSTING_HISTORY_REQUIRED')
        if not pending:
            require_current_program_book_cash(tx,book,read_opening=self.read_opening)
            return ('COST_HISTORY_RECONCILIATION_REQUIRED','PROTECTION_RECHECK_REQUIRED'),True
        # Exact native signed delta only. Initial account assets and targets do
        # not enter allocation. Overshoots remain raw and are never clipped.
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            quantity=p.actual_quantity.value
            for fact in pending:
                need(fact.effects.position_delta.unit==p.actual_quantity.unit,'OWNED_POSITION_UNIT_CHANGED')
                quantity+=fact.effects.position_delta.value
        need(quantity>=0,'SPOT_OWNED_POSITION_OVERSELL')
        for fact in pending:
            record=make_program_asset_posting(fact)
            old=tx._latest('audit_events',record.record_id,ProgramAssetPostingRecord)
            if old is None:tx._append('audit_events',record.record_id,record,0)
            else:need(old==record or old.source_economic_hash==record.source_economic_hash,'PROGRAM_ASSET_POSTING_MISMATCH')
        frame=observe_program_account_cash(tx,read_opening=self.read_opening)
        need(frame is not None,'ACCOUNT_OPENING_REQUIRED')
        cost=replace(p.cost_evidence,query_succeeded=False,pagination_complete=False,
            missing=tuple(dict.fromkeys((*p.cost_evidence.missing,'PROGRAM_COST_HISTORY_RECONCILIATION_REQUIRED'))))
        updated=replace(p,actual_quantity=replace(p.actual_quantity,value=quantity),cost_evidence=cost,
            realized_pnl=None,unrealized_valuation=None,risk_locks=tx.risk_locks.active(tx.scope))
        new_owner=replace(owner,position=updated,sequence=owner.sequence+1)
        registry.save_owner(owner,new_owner)
        row=tx.execute('SELECT COALESCE(max(sequence),0) FROM ledger.positions WHERE scope_id=%s AND record_id=%s',
            (scope_id(tx.scope),p.position_id)).fetchone()
        position_sequence=tx._append('positions',p.position_id,updated,row[0])
        latest=max((f.fill.evidence for f in pending),key=lambda r:r.visible_at)
        aggregate='program-position:'+p.position_id
        head=tx.execute('SELECT sequence FROM ledger.aggregate_heads WHERE scope_id=%s AND aggregate_id=%s',
            (scope_id(tx.scope),'events:'+aggregate)).fetchone()
        sequence=0 if head is None else head[0]
        EventStore(received_at=latest.visible_at).append(tx,t.DomainEvent(event_id='program-position:'+canonical_hash(
            dict(scope=domain_payload(tx.scope),identities=sorted(f.approval.identity_hash for f in pending))),
            scope=tx.scope,aggregate_id=aggregate,revision=sequence+1,occurred_at=latest.visible_at,
            evidence=tuple(f.fill.evidence for f in pending),payload=updated),sequence)
        updated_book=replace(book,sequence=book.sequence+1,positions=(updated,),balances=frame.balances,
            basis_fingerprint=canonical_hash(dict(prior=canonical_hash(domain_payload(book)),
                account_revision=frame.economic_revision,owner=domain_payload(new_owner))))
        tx.projections.save_book(updated_book,book.sequence)
        for fact in pending:
            identity=fact.approval.identity_hash
            for index,fee in enumerate(fact.effects.fees):
                cashflow=fee_cashflow(fact,index,fee)
                tx._append('cashflows','program-fee:'+identity+':'+str(index),cashflow,0)
            posting=ProgramOwnedPostingRecord(contract_version='2026-10-30',record_kind='PROGRAM_OWNED_POSTING_V2',
                identity_hash=identity,source_economic_hash=economic_signature(fact),owner_after=new_owner,
                position_sequence=position_sequence,book_sequence=updated_book.sequence,owner_before=owner,
                source_approval_ids=tuple(f.approval.approval_id for f in pending))
            tx._append('audit_events','program-owned-posting:'+identity,posting,0)
        # A different scope may still own an unprojected native execution. Our
        # actual financial transition commits without touching it. No cash
        # binding is created until the STRICT account read proves every owner
        # transition; that scope must later reconcile its own book and replan.
        try:read_program_account_cash(tx,read_opening=self.read_opening)
        except MissingEvidence as error:
            if str(error) not in ('UNPOSTED_PROGRAM_ASSET_EFFECTS','UNPOSTED_PROGRAM_OWNED_EFFECTS'):raise
            return ('COST_HISTORY_RECONCILIATION_REQUIRED','PROTECTION_RECHECK_REQUIRED',
                'ACCOUNT_OTHER_OWNER_RECONCILIATION_REQUIRED'),False
        bind_program_book_cash(tx,updated_book,read_opening=self.read_opening)
        return ('COST_HISTORY_RECONCILIATION_REQUIRED','PROTECTION_RECHECK_REQUIRED'),True
