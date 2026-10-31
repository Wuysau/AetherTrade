from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.contracts.wire import ContractCodec
from unit.support_ip_06 import make_case,money,NOW,END
def test_strict_legacy_terms_retain_full_source_and_responsibility():
    from trading_system.risk.terms import legacy_risk_terms
    c=make_case();terms=legacy_risk_terms(c.intent)
    assert terms.source.kind=='TRADE_INTENT' and terms.source.version=='2026-10-20'
    assert terms.source.hash==ContractCodec().hash(c.intent)
    assert terms.decision_id==c.intent.intent_id and terms.scope==c.book.scope
    assert terms.stop_price==D(c.intent.exit_plan.stop_price)
    assert terms.maximum_holding_seconds==c.intent.exit_plan.maximum_holding_seconds
    assert terms.review_due_at==c.intent.exit_plan.review_due_at
    assert not terms.execution_authorized



@pytest.mark.parametrize('mutation',['subclass','version'])
def test_strict_legacy_adapter_does_not_accept_new_version_as_old_terms(mutation):
    from trading_system.risk.terms import legacy_risk_terms
    from dataclasses import fields
    from trading_system.contracts.wire import TradeIntent
    c=make_case()
    if mutation=='version':intent=replace(c.intent,contract_version='2026-10-29')
    else:
        class Unsupported(TradeIntent):pass
        intent=Unsupported(**{f.name:getattr(c.intent,f.name) for f in fields(c.intent)})
    with pytest.raises(ValueError):legacy_risk_terms(intent)



def test_incomplete_metrics_never_acquire_zero_defaults():
    from trading_system.risk.terms import RiskMetrics,metrics_completeness
    metrics=RiskMetrics()
    codes=metrics_completeness(metrics)
    for name in ('NET_EXPOSURE','RELATED_GROUPS','PROTECTION','PLANNED_LOSS','HOLDING_HORIZON','COUNTS','FINANCIAL_COVERAGE'):
        assert 'RISK_METRIC_MISSING:'+name in codes
    assert not metrics.execution_authorized



def test_program_exposure_is_not_a_complete_risk_assessment():
    from trading_system.risk.terms import metrics_completeness
    from types import SimpleNamespace
    partial=SimpleNamespace(gross_after=money('100'),blockers=())
    assert metrics_completeness(partial)==('RISK_METRICS_REQUIRED',)



def complete_metrics():
    from trading_system.risk.terms import RiskMetrics,legacy_risk_terms
    from trading_system.contracts.canonical import canonical_hash
    c=make_case();terms=legacy_risk_terms(c.intent);decision=c.evaluate()
    metrics=RiskMetrics(source=terms.source,allocation_hash='b'*64,facts_hash='c'*64,
        book_hash=canonical_hash(domain_payload(c.book)),book_sequence=c.book.sequence,
        basis_fingerprint=c.book.basis_fingerprint,snapshot_ref=c.market.ref,evaluated_at=NOW,valid_until=END,
        maximum_order_notional=money('200'),planned_loss=decision.estimated_loss,fees=decision.fees_assumption,
        funding=money('1'),capital=money('100'),required_assets=decision.reserved_assets,
        exposure=decision.aggregate_exposure_after,related_groups=decision.aggregate_exposure_after.groups,
        protection_complete=True,holding_horizon_seconds=terms.maximum_holding_seconds,
        open_position_count=1,active_order_count=1,financial_coverage=c.book.coverage,evidence=c.book.evidence)
    return c,terms,metrics



@pytest.mark.parametrize('field',['planned_loss','exposure','related_groups','protection_complete',
    'holding_horizon_seconds','active_order_count','financial_coverage','required_assets'])
def test_an_otherwise_complete_assessment_cannot_hide_missing_metric(field):
    from trading_system.risk.terms import metrics_completeness
    _,_,metrics=complete_metrics()
    assert not metrics_completeness(metrics)
    assert metrics_completeness(replace(metrics,**{field:None}))



@pytest.mark.parametrize('mutation',['source','book','basis','snapshot','expired','horizon','coverage_scope'])
def test_metric_bindings_reject_stale_or_cross_source_facts(mutation):
    from trading_system.risk.terms import metric_binding_codes
    c,terms,metrics=complete_metrics()
    assert not metric_binding_codes(terms,metrics,c.book,c.market.ref,c.inputs.clock)
    if mutation=='source':metrics=replace(metrics,source=replace(metrics.source,hash='d'*64))
    if mutation=='book':metrics=replace(metrics,book_sequence=2)
    if mutation=='basis':metrics=replace(metrics,basis_fingerprint='other')
    if mutation=='snapshot':metrics=replace(metrics,snapshot_ref=replace(c.market.ref,hash='d'*64))
    if mutation=='expired':metrics=replace(metrics,valid_until=NOW)
    if mutation=='horizon':metrics=replace(metrics,holding_horizon_seconds=1)
    if mutation=='coverage_scope':metrics=replace(metrics,financial_coverage=replace(metrics.financial_coverage,
        scope=replace(c.book.scope,venue_account_id='foreign')))
    assert metric_binding_codes(terms,metrics,c.book,c.market.ref,c.inputs.clock)


@pytest.mark.parametrize('field',['planned_loss','fees','funding','capital','maximum_order_notional',
    'gross','net','group_gross','group_net','asset'])
@pytest.mark.parametrize('invalid',['NaN','Infinity','wrong_type','currency'])
def test_malformed_metric_money_is_never_complete_or_an_exception(field,invalid):
    from trading_system.risk.terms import metrics_completeness,metric_binding_codes
    c,terms,metrics=complete_metrics()
    value=money('1')
    # Deliberately malformed deserialization sentinel: completeness must not
    # rely on a previously executed dataclass constructor as evidence.
    if invalid=='currency':object.__setattr__(value,'currency','')
    else:object.__setattr__(value,'amount','1' if invalid=='wrong_type' else D(invalid))
    if field in ('gross','net'):metrics=replace(metrics,exposure=replace(metrics.exposure,**{field:value}))
    elif field in ('group_gross','group_net'):
        group=replace(metrics.related_groups[0],**{field[6:]:value})
        metrics=replace(metrics,related_groups=(group,),exposure=replace(metrics.exposure,groups=(group,)))
    elif field=='asset':metrics=replace(metrics,required_assets=(value,))
    else:metrics=replace(metrics,**{field:value})
    assert metrics_completeness(metrics)
    assert metric_binding_codes(terms,metrics,c.book,c.market.ref,c.inputs.clock)


@pytest.mark.parametrize('mutation',['currency','negative','net_exceeds_gross','duplicate','sum'])
def test_related_group_metrics_must_be_valid_portfolio_bounds(mutation):
    from trading_system.risk.terms import metrics_completeness
    _,_,metrics=complete_metrics();group=metrics.related_groups[0]
    if mutation=='currency':group=replace(group,gross=money('200','USD'))
    if mutation=='negative':group=replace(group,gross=money('-1'))
    if mutation=='net_exceeds_gross':group=replace(group,net=money('201'))
    if mutation=='sum':group=replace(group,gross=money('201'))
    groups=(group,group) if mutation=='duplicate' else (group,)
    metrics=replace(metrics,related_groups=groups,exposure=replace(metrics.exposure,groups=groups))
    assert metrics_completeness(metrics)



