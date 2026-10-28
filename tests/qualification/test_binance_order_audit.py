import copy
import json
from pathlib import Path
import pytest
from tools.audit_binance_orders import assess_catalog, audit_checkout
ROOT=Path(__file__).resolve().parents[2]
REF=ROOT/'qualification/binance_orders/reference'

def inputs():
    return tuple(json.loads((REF/n).read_text(encoding='utf-8')) for n in ('binance-order-capability-catalog.json','acceptance-cases.json'))

def test_all_targets_and_cases_survive_without_implied_qualification():
    c,a=inputs();r=assess_catalog(c,a)
    assert len(r['capabilities'])==46 and len(r['acceptance'])==56
    assert all(x['target_required'] for x in r['capabilities'])
    assert all(x['status']=='NOT_RUN' for x in r['acceptance'])
    assert not r['live_enabled'] and not r['execution_authority']

@pytest.mark.parametrize('mutation',['delete','duplicate','downgrade','unmapped'])
def test_catalog_cannot_silently_drop_or_grant_requirements(mutation):
    c,a=inputs()
    if mutation=='delete':c['features'].pop()
    if mutation=='duplicate':c['features'][1]=copy.deepcopy(c['features'][0])
    if mutation=='downgrade':c['features'][0]['target_required']=False
    if mutation=='unmapped':a['cases'][0]['feature_ids']=['invented']
    with pytest.raises(ValueError):assess_catalog(c,a)

def test_checkout_audit_distinguishes_review_ref_from_installed_build():
    r=audit_checkout(ROOT)
    assert r['baseline']=='b168966d350ed60fe8a92792f9519f4227054bc5'
    assert isinstance(r['baseline_diff'],list)
    assert r['native']['deployment_ref'] is None
    assert r['native']['status']=='BLOCKED'
    assert r['schema_mirrors_match']
    assert r['original_schema_mutation_authorized'] is False
