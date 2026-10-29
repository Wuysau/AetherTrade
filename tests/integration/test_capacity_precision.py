"""Real reserve-path precision regression, isolated PAPER only; no venue proof."""
from dataclasses import replace
from decimal import Decimal

from integration.support_ip_07 import ip07_database, ReservationCase, money


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
    result=c.reserve()
    # 10**80+70 free, minus 10**80+1 held, leaves69; this request needs70.
    assert not result.accepted, result
    assert result.rejection_codes==('INSUFFICIENT_CAPACITY:USDT',)
    assert c.count('commands')==c.count('intents')==c.count('reservations')==0
