"""OP-A read-only audit. Metadata is never an execution permission or qualification.

No account/network calls, provider calls, native imports, migrations or runtime
configuration writes. The output path is explicit and outside the input package.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

BASELINE='b168966d350ed60fe8a92792f9519f4227054bc5'
SCHEMAS=('decision-proposal','trade-intent','risk-policy','execution-command')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def assess_catalog(catalog, acceptance):
    features=catalog['features'];cases=acceptance['cases']
    ids={f['id'] for f in features};case_ids={c['id'] for c in cases}
    if (len(features)!=46 or len(ids)!=46 or len(cases)!=56
        or case_ids!={f'ORD-{i:02d}' for i in range(1,57)}
        or set(catalog['scope'])!={'SPOT','USD_M_PERP','US_EQUITY'}
        or not all(f['target_required'] is True and f['live_enabled'] is False for f in features)
        or not all(set(c['feature_ids'])<=ids for c in cases)
        or set().union(*(set(c['feature_ids']) for c in cases if c['id']!='ORD-55'))!=ids):
        raise ValueError('Catalog coverage or qualification boundary changed')
    return dict(execution_authority=False,live_enabled=False,
        capabilities=[dict(id=f['id'],product=f['product'],family=f['family'],route=f['route'],
            target_required=True,official_claim=f['official_documented'],source_refs=f['source_refs'],
            implementation='NOT_ESTABLISHED_FOR_FULL_LIFECYCLE',
            paper_qualification='NOT_RUN',testnet_qualification='NOT_RUN',live_qualification='NOT_RUN',
            account_eligibility='NOT_QUERIED',symbol_eligibility='NOT_QUERIED',
            acceptance_ids=[c['id'] for c in cases if f['id'] in c['feature_ids']]) for f in features],
        acceptance=[dict(id=c['id'],work_package=c['work_package'],feature_ids=c['feature_ids'],
            status='NOT_RUN',evidence=[],reason='Scenario not executed by metadata audit') for c in cases])

def summarize_acceptance(catalog, acceptance, recorded, *, head):
    """Report recorded claims, never authenticate evidence or qualify HEAD."""
    result=assess_catalog(catalog,acceptance)
    expected={c['id']:c for c in acceptance['cases']}
    rows=recorded.get('cases',[])
    if (recorded.get('case_count')!=56 or len(rows)!=56
            or len({r['id'] for r in rows})!=56 or {r['id'] for r in rows}!=set(expected)):
        raise ValueError('Acceptance case coverage changed')
    counts=dict.fromkeys(('PASS','FAIL','BLOCKED','NOT_RUN'),0)
    report=[]
    for row in rows:
        reference=expected[row['id']]
        if any(row.get(k)!=reference.get(k) for k in
               ('title','input_or_fault','expected','feature_ids','source_refs','work_package')):
            raise ValueError('Acceptance requirement changed')
        status=row.get('status');evidence=row.get('evidence');source=row.get('source_commit')
        if status not in counts:raise ValueError('Unknown acceptance status')
        if status=='PASS' and (not isinstance(evidence,list) or not evidence
                or not all(type(e) is str and e for e in evidence)
                or type(source) is not str or not source):
            raise ValueError('Recorded PASS lacks source/evidence attribution')
        counts[status]+=1
        report.append(dict(id=row['id'],feature_ids=row['feature_ids'],
            recorded_status=status,recorded_source_commit=source,recorded_evidence=evidence,
            recorded_qualification=row.get('qualification'),current_status='NOT_RUN',
            reason='Historical claims are not current-source execution evidence'))
    result.update(scope='OFFLINE_ACCEPTANCE_INVENTORY_NOT_QUALIFICATION',head=head,
        recorded_counts=counts,acceptance=report,evidence_authenticated=False)
    return result


def audit_checkout(root):
    root=Path(root).resolve()
    def git(*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    reference=root/'qualification/binance_orders/reference'
    cat=json.loads((reference/'binance-order-capability-catalog.json').read_text(encoding='utf-8'))
    cases=json.loads((reference/'acceptance-cases.json').read_text(encoding='utf-8'))
    result=assess_catalog(cat,cases)
    try: installed=importlib.metadata.version('nautilus_trader')
    except importlib.metadata.PackageNotFoundError: installed=None
    mapping=json.loads((root/'qualification/nautilus/compatibility-map.json').read_text(encoding='utf-8'))
    schemas={name:dict(public_sha256=digest(root/'contracts'/f'{name}.schema.json'),
        packaged_sha256=digest(root/'src/trading_system/schemas'/f'{name}.schema.json')) for name in SCHEMAS}
    result.update(scope='OP_A_METADATA_AND_CHECKOUT_AUDIT_ONLY',baseline=BASELINE,
        head=git('rev-parse','HEAD'),branch=git('branch','--show-current'),
        baseline_diff=git('diff','--name-only',BASELINE,'HEAD').splitlines(),
        worktree_status=git('status','--short').splitlines(),
        input_files={p.name:digest(p) for p in sorted(reference.iterdir()) if p.is_file()},
        schemas=schemas,schema_mirrors_match=all(v['public_sha256']==v['packaged_sha256'] for v in schemas.values()),
        original_schema_mutation_authorized=False,
        native=dict(installed_version=installed,interpreter=sys.version,executable=sys.executable,
            platform=platform.platform(),review_ref=mapping['review_ref'],deployment_ref=mapping['deployment_ref'],
            lock_sha256=digest(root/'qualification/nautilus/requirements.lock'),status='BLOCKED',
            reasons=['EXACT_DEPLOYMENT_ARTIFACT_UNRESOLVED','NATIVE_RUNTIME_NOT_QUALIFIED'],
            latest_documentation_is_not_installed_version=True))
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--acceptance-inventory',action='store_true',help='Report historical case claims without qualifying current source')
    args=parser.parse_args();result=audit_checkout(args.root)
    if args.acceptance_inventory:
        reference=args.root/'qualification/binance_orders/reference'
        read=lambda p:json.loads(p.read_text(encoding='utf-8'))
        result=summarize_acceptance(read(reference/'binance-order-capability-catalog.json'),
            read(reference/'acceptance-cases.json'),read(args.root/'qualification/binance_orders/acceptance-status.json'),
            head=result['head'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(capabilities=len(result['capabilities']),cases=len(result['acceptance']),
        native=result.get('native',{}).get('status','NOT_RUN'),scope=result['scope'],execution_authority=False)))
    return 0  # Audit succeeded; explicitly NOT a native qualification exit status.
if __name__=='__main__':raise SystemExit(main())
