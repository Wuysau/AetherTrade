"""Real reserve-path precision regression, isolated PAPER only; no venue proof."""
from dataclasses import replace
from decimal import Decimal

from integration.support_ip_07 import ip07_database, ReservationCase, money
from trading_system.risk.asset_capacity import CapacityInputs, check_capacity
from trading_system.risk.reservation_repository import ReservationRepository


def test_small_cross_account_debit_cannot_disappear_in_large_pool(ip07_database):
    # Imported/unknown local holds may be large. Their full debit must count
    # before another command can be reserved; no order quantity is inferred.
    large='1'+'0'*80
    available='1'+'0'*78+'70'
    c=ReservationCase(ip07_database,available=available)
    c.group_capacity=c.strategy_capacity=(money(available),money('10','ASSET'))
    for amount in ('1',large):
        prior=ReservationCase(ip07_database,group=c.group)
        reservation=replace(prior.ledger.reservation,collateral_group_id=c.group,
            owner_strategy_id=prior.owner,intent_id=prior.case.intent.intent_id,
            amount=Decimal(amount),reflected_amount=Decimal(0),asset=c.currency)
        with prior.ledger.uow.transaction(prior.scope) as tx:
            tx.intents.append(prior.case.intent)
            tx.reservations.append(reservation,0)
    # 10**80+70 free, minus 10**80+1 held, leaves69; this request needs70.
    # Verify the shared capacity guard directly using the actual committed
    # collateral holds. The exact risk evaluator now catches the same shortage
    # earlier in the real reserve path; neither stage may commit a bundle.
    with c.ledger.uow.transaction(c.scope) as tx:
        book=tx.projections.book(c.scope)
        capacity=check_capacity(CapacityInputs(incremental_requirements=(money('70'),),
            venue_free_balances=book.balances,remaining_group_capacity=c.group_capacity,
            remaining_strategy_capacity=c.strategy_capacity,buffers=c.buffer,
            reconciled_group_holds=ReservationRepository(tx).group(),owner_strategy_id=c.owner))
        assert not capacity.sufficient
        assert capacity.rejection_codes==('INSUFFICIENT_CAPACITY:USDT',)
    result=c.reserve()
    assert not result.accepted, result
    assert result.rejection_codes==('INSUFFICIENT_SPENDABLE:USDT',)
    assert c.count('commands')==c.count('intents')==c.count('reservations')==0
