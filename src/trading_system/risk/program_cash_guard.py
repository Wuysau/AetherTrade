"""Recheck authenticated account cash under the caller's existing group mutex."""
from trading_system.contracts import types as t
from trading_system.storage.models import MissingEvidence
from trading_system.execution.program_book_cash import require_current_program_book_cash
from trading_system.execution.program_legacy_opening import read_program_local_account_opening


def require_current_account_book(tx,book):
    if type(book) is not t.BookSnapshot:
        raise MissingEvidence('CURRENT_BOOK_REQUIRED')
    # Use the raw book: hold subtraction belongs to collateral preparation.
    # The exact registered local reader rereads protected0019/0020 provenance;
    # no caller DTO, callback, binding marker or preview supplies cash authority.
    return require_current_program_book_cash(tx,book,read_opening=read_program_local_account_opening)


def require_funded_program_account_book(tx,book):
    """Trusted generic path only; absence is not initial virtual capital."""
    frame=require_current_account_book(tx,book)
    if frame is None:
        raise MissingEvidence('ACCOUNT_OPENING_REQUIRED')
    return frame
