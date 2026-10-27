"""Complete-history accounting across an owner handoff; no database CAS proof."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.intents.target_compiler import TargetCompiler
from trading_system.risk import target_guard
from trading_system.storage.models import encode
from unit.support_ip_09 import NOW, make_case, order, position, price, quantity


@pytest.fixture
def history():
    case = make_case()
    old_intent = case.bind()
    command = SimpleNamespace(command_id='historical-command', intent_id=old_intent.intent_id,
        state='VENUE_CONFIRMED', logical_client_order_id='order',
        venue_client_order_id='venue-order', venue_order_id=None, created_at=old_intent.created_at,
        canonical_payload=SimpleNamespace(quantity='0.010', side='BUY'))
    terminal = order(case.book.scope, '0', lifecycle=t.OrderLifecycle.FILLED)
    terminal = replace(terminal, filled_quantity=quantity('0.010'))
    current_position = replace(position(case.book.scope, '0.010', owner='new-owner'), ownership_epoch=2)
    book = replace(case.book, sequence=2, positions=(current_position,), active_orders=(), reservations=())
    fill = t.Fill(fill_id='historical-fill', venue=old_intent.venue, scope=book.scope,
        native_trade_id='historical-trade', order=terminal.order.identity, quantity=quantity('0.010'),
        price=price('50000'), fees=(), occurred_at=old_intent.created_at,
        correction_of=None, evidence=book.evidence[0])
    report = t.VenueReport(coverage=book.coverage, orders=(terminal,), positions=book.positions,
        balances=book.balances, fills=(fill,), evidence=book.evidence, next_cursor=None, as_of=NOW)
    values = SimpleNamespace(case=case, old_intent=old_intent, command=command, book=book,
        report=report, fills=(fill,), current_owner=('new-owner', 2, old_intent.risk_policy_revision))

    class HistoricalReads:
        scope = book.scope
        group_id = old_intent.collateral_group_id

        def execute(self, query, params=()):
            if 'FROM ledger.active_owners' in query:
                return SimpleNamespace(fetchone=lambda: values.current_owner)
            if 'FROM ledger.fills' in query:
                return SimpleNamespace(fetchall=lambda: [(encode(f),) for f in values.fills])
            raise AssertionError(query)

        def _latest(self, table, key, cls):
            if table == 'intents':
                return values.old_intent
            if cls is target_guard.TargetBasis:
                return target_guard.TargetBasis(command_id=command.command_id, book_sequence=1,
                    actual=quantity('0'), instrument=case.target.instrument)
            if cls is target_guard.TargetReport:
                return target_guard.TargetReport(book_hash=target_guard.digest(values.book),
                    report=values.report, archive_ref=values.book.evidence[0],
                    command_ids=(values.command.command_id,))
            raise AssertionError((table, key, cls))

    values.tx = HistoricalReads()
    values.new_intent = replace(old_intent, intent_id='new-owner-reduction', owner_strategy_id='new-owner',
        ownership_epoch=2, target_quantity='0.004')
    return values


def obligations(history, monkeypatch):
    monkeypatch.setattr(target_guard, 'roots', lambda tx: (history.command,))
    return target_guard.target_obligations(history.tx, history.new_intent, history.book, (), utc_timestamp(NOW))


def test_terminal_history_is_counted_after_handoff_and_allows_new_owner_reduction(history, monkeypatch):
    pending = obligations(history, monkeypatch)
    assert pending == ()
    plan = TargetCompiler(replace(history.case.target, pending=pending)).compile(history.new_intent, history.book)
    assert plan.actual.value == Decimal('0.010')
    assert plan.remaining.value == Decimal('-0.006') and plan.classification == 'REDUCE'


@pytest.mark.parametrize('fault,reason', [
    ('order-owner', 'TARGET_TERMINAL_ORDER_REQUIRED'),
    ('order-epoch', 'TARGET_TERMINAL_ORDER_REQUIRED'),
    ('fill-quantity', 'TARGET_FILLS_INCOMPLETE'),
    ('position-owner', 'TARGET_POSITION_RECONCILIATION_REQUIRED'),
    ('position-quantity', 'TARGET_POSITION_RECONCILIATION_REQUIRED'),
    ('intent-scope', 'PRIOR_COMMAND_SCOPE_MISMATCH'),
])
def test_handoff_does_not_discard_historical_binding_or_quantity_checks(history, monkeypatch, fault, reason):
    report = history.report
    if fault in ('order-owner', 'order-epoch'):
        owned = report.orders[0].order
        owned = replace(owned, **({'owner_strategy_id': 'new-owner'} if fault == 'order-owner' else {'ownership_epoch': 2}))
        report = replace(report, orders=(replace(report.orders[0], order=owned),))
    elif fault == 'fill-quantity':
        history.fills = (replace(history.fills[0], quantity=quantity('0.009')),)
        report = replace(report, fills=history.fills)
    elif fault in ('position-owner', 'position-quantity'):
        changed = replace(history.book.positions[0], **(
            {'owner_strategy_id': history.old_intent.owner_strategy_id} if fault == 'position-owner'
            else {'actual_quantity': quantity('0.009')}))
        history.book = replace(history.book, positions=(changed,))
        report = replace(report, positions=(changed,))
    else:
        history.old_intent = replace(history.old_intent, venue_account_id='another-account')
    history.report = report
    with pytest.raises(ProposalRejected, match=reason):
        obligations(history, monkeypatch)


def test_old_owner_ready_command_remains_an_obligation_and_cannot_be_adopted(history, monkeypatch):
    history.command.state = 'DURABLE_READY'
    with pytest.raises(ProposalRejected, match='PRIOR_COMMAND_NOT_OWNED'):
        obligations(history, monkeypatch)
