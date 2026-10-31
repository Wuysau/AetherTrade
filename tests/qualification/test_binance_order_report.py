import copy
import json
from pathlib import Path
import pytest
from tools.audit_binance_orders import summarize_acceptance

ROOT=Path(__file__).resolve().parents[2]
def inputs():
    base=ROOT/'qualification/binance_orders'
    return [json.loads((base/p).read_text(encoding='utf-8')) for p in (
        'reference/binance-order-capability-catalog.json','reference/acceptance-cases.json','acceptance-status.json')]

def test_report_preserves_historical_status_without_promoting_current_source():
    c,a,s=inputs();before=copy.deepcopy(s)
    result=summarize_acceptance(c,a,s,head='f'*40)
    assert len(result['capabilities'])==46 and len(result['acceptance'])==56
    assert result['recorded_counts']=={'PASS':1,'FAIL':0,'BLOCKED':0,'NOT_RUN':55}
    assert all(r['current_status']=='NOT_RUN' for r in result['acceptance'])
    assert all(r['paper_qualification']=='NOT_RUN' for r in result['capabilities'])
    assert not result['execution_authority'] and s==before
    old=next(r for r in result['acceptance'] if r['id']=='ORD-47')
    assert old['recorded_source_commit']=='d5074c9' and old['recorded_evidence']

@pytest.mark.parametrize('fault',['missing','duplicate','unknown_status','mapping','expectation','pass_without_evidence','pass_without_source','count'])
def test_invalid_or_rewritten_acceptance_is_rejected(fault):
    c,a,s=inputs()
    if fault=='missing':s['cases'].pop()
    if fault=='duplicate':s['cases'][1]=copy.deepcopy(s['cases'][0])
    if fault=='unknown_status':s['cases'][0]['status']='QUALIFIED'
    if fault=='mapping':s['cases'][0]['feature_ids']=[]
    if fault=='expectation':s['cases'][0]['expected']='weakened'
    if fault=='count':s['case_count']=55
    old=next(r for r in s['cases'] if r['id']=='ORD-47')
    if fault=='pass_without_evidence':old['evidence']=[]
    if fault=='pass_without_source':old.pop('source_commit')
    with pytest.raises(ValueError):summarize_acceptance(c,a,s,head='f'*40)
