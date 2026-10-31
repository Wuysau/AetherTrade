"""Book cash is a checked projection of authenticated account contributions.

The audit binding cannot mint capital. Every guard rereads protected opening,
source and fills under the caller's existing collateral mutex. Reconciliation
only updates the current instrument; callers must obtain fresh bounded intake
after its book sequence/basis changes.
"""
from dataclasses import dataclass,replace
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.models import MissingEvidence,decode,scope_id
from trading_system.ownership.registry import RegistryTx
from .program_account_cash import read_program_account_cash


def need(condition,code):
    if not condition:raise MissingEvidence(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class BookCashBinding:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_BOOK_CASH_BINDING_V1']
    scope:t.ScopeKey
    book_sequence:int
    book_hash:str
    economic_revision:str
    balances:tuple[t.Money,...]


def _id(book):return 'program-book-cash:'+str(book.sequence)
def _hash(value):return canonical_hash(domain_payload(value))


def _vector(book,frame):
    need(type(book) is t.BookSnapshot and len({m.currency for m in book.balances})==len(book.balances)
        and tuple(sorted(book.balances,key=lambda m:m.currency))==frame.balances,'BOOK_CASH_VECTOR_MISMATCH')


def bind_program_book_cash(tx,book,*,read_opening=None):
    """Bind only the actual current book and the independently rederived vector."""
    tx.require_scope(book.scope)
    frame=read_program_account_cash(tx,read_opening=read_opening)
    if frame is None:return None
    _vector(book,frame)
    need(tx.projections.book(tx.scope)==book,'CURRENT_BOOK_REQUIRED')
    binding=BookCashBinding(contract_version='2026-10-30',record_kind='PROGRAM_BOOK_CASH_BINDING_V1',
        scope=tx.scope,book_sequence=book.sequence,book_hash=_hash(book),
        economic_revision=frame.economic_revision,balances=frame.balances)
    old=tx._latest('audit_events',_id(book),BookCashBinding)
    if old is None:tx._append('audit_events',_id(book),binding,0)
    else:need(old==binding,'BOOK_CASH_BINDING_CONFLICT')
    return frame


def require_current_program_book_cash(tx,book,*,read_opening=None):
    """Neutral reservation/mark/consume guard; no writes, new UoW or permits."""
    tx.require_scope(book.scope)
    frame=read_program_account_cash(tx,read_opening=read_opening)
    if frame is None:return None
    need(tx.projections.book(tx.scope)==book,'CURRENT_BOOK_REQUIRED')
    binding=tx._latest('audit_events',_id(book),BookCashBinding)
    need(binding is not None,'BOOK_CASH_BINDING_REQUIRED')
    need(binding.scope==tx.scope and binding.book_sequence==book.sequence and binding.book_hash==_hash(book),
        'BOOK_CASH_BINDING_MISMATCH')
    need(binding.economic_revision==frame.economic_revision and binding.balances==frame.balances,
        'STALE_BOOK_ACCOUNT_CASH')
    _vector(book,frame)
    return frame


def reconcile_program_book_cash(tx,*,read_opening=None):
    """Current owner reconciles its book without claiming account inventory."""
    registry=RegistryTx(tx);owner=registry.owner()
    need(owner is not None,'CURRENT_OWNED_POSITION_REQUIRED')
    p=owner.position
    need(p.scope==tx.scope and registry.active_owner()==(p.owner_strategy_id,p.ownership_epoch,owner.policy_revision),
        'CURRENT_OWNED_POSITION_REQUIRED')
    book=tx.projections.book(tx.scope)
    need(book.positions==(p,) or (not book.positions and p.actual_quantity.value==0),
        'OWNED_POSITION_BOOK_MISMATCH')
    frame=read_program_account_cash(tx,read_opening=read_opening)
    if frame is None:return book
    binding=tx._latest('audit_events',_id(book),BookCashBinding)
    if binding is not None and binding.economic_revision==frame.economic_revision:
        require_current_program_book_cash(tx,book,read_opening=read_opening)
        return book
    updated=replace(book,sequence=book.sequence+1,balances=frame.balances,
        basis_fingerprint=canonical_hash(dict(prior_book=_hash(book),account_revision=frame.economic_revision)))
    tx.projections.save_book(updated,book.sequence)
    bind_program_book_cash(tx,updated,read_opening=read_opening)
    return updated
