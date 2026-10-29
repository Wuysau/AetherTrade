from dataclasses import replace
from decimal import Decimal as D,localcontext
import json
from pathlib import Path
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from unit.risk_core_cases import CASES,legacy_case
from unit.support_ip_06 import make_case,money,NOW,END
@pytest.mark.parametrize('name',CASES)
def test_full_legacy_decisions_and_hashes_match_unchanged_baseline(name):
    expected=json.loads(Path('tests/fixtures/risk-core/legacy-decisions.json').read_text())
    assert expected['baseline']=='9b28899'
    actual=domain_payload(legacy_case(name).evaluate());old=expected['cases'][name]
    if name=='leverage_limit':
        # Deliberate safety correction: nonterminating margin is an upward
        # monetary bound, not the old downward-rounded value. All other fields,
        # including rejection ordering and risk_decision_id, remain identical.
        assert D(actual['reserved_assets'][0]['amount'])>D(old['reserved_assets'][0]['amount'])
        actual=dict(actual,reserved_assets=old['reserved_assets'])
    assert actual==old



@pytest.mark.parametrize('leverage',[3,6,7])
def test_nonterminating_margin_is_always_an_upper_rational_bound(leverage):
    from fractions import Fraction
    from trading_system.risk.policy_checks import legacy_margin_upper_bound
    bound=legacy_margin_upper_bound(D(200),leverage)
    assert Fraction(bound)>=Fraction(200,leverage)
    with localcontext() as ctx:
        ctx.prec=80
        assert Fraction(bound.next_minus())<Fraction(200,leverage)
    with localcontext() as ctx:
        ctx.prec=3
        assert legacy_margin_upper_bound(D(200),leverage)==bound



def test_evaluator_rejects_schema_version_masquerading_as_legacy_source():
    c=make_case();c.intent=replace(c.intent,contract_version='2026-10-29')
    result=c.evaluate()
    assert result.verdict=='DENY' and 'INVALID_LEGACY_RISK_SOURCE' in result.rejection_codes



@pytest.mark.parametrize('digits,reason',[(80,'INSUFFICIENT_SPENDABLE:USDT'),(300,'ARITHMETIC_PRECISION_UNPROVEN')])
def test_spendable_subtraction_does_not_round_away_small_holds(digits,reason):
    c=make_case();free=D(10**digits+107)
    c.book=replace(c.book,balances=(money(str(free)),))
    c.inputs=replace(c.inputs,spendable=c.book.balances,fee_reserve=(money('6'),))
    def hold(key,amount):
        r=t.CapitalReservation(reservation_id=key,environment=c.book.scope.environment,
            collateral_group_id=c.intent.collateral_group_id,venue_account_id=c.intent.venue_account_id,
            owner_strategy_id=c.intent.owner_strategy_id,intent_id='prior-'+key,asset='USDT',
            amount=amount,reflected_amount=D(0),state=t.ReservationState.HELD,
            created_at=NOW,expires_at=END,command_refs=('prior-'+key,),evidence_watermark='')
        return t.ReservationProjection(reservation=r,held=amount,reflected=D(0),release_pending=D(0),sequence=0)
    c.book=replace(c.book,reservations=(hold('small',D(1)),hold('large',D(10**digits))))
    result=c.evaluate()
    assert result.verdict=='DENY',result
    assert reason in result.rejection_codes
    with localcontext() as ctx:
        ctx.prec=3
        assert c.evaluate()==result



