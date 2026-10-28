"""Metadata-only verification for the proposed Binance order refactor.

No product imports, network access, database connections, or trading operations.
Run: python validate_design.py
"""
from pathlib import Path
import hashlib
import json
import re
import sys

ROOT=Path(__file__).resolve().parent
SHA='b168966d350ed60fe8a92792f9519f4227054bc5'
checks=[]
def check(name,condition,detail=None):
    checks.append({'check':name,'passed':bool(condition),'detail':detail})
def read(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

def main():
    cat=read('binance-order-capability-catalog.json')
    findings=read('architecture-findings.json')
    matrix=read('acceptance-cases.json')
    sources=read('sources.json')['sources']
    discrepancies=read('documentation-discrepancies.json')
    features=cat['features']; cases=matrix['cases']
    fids={f['id'] for f in features}; cids={c['id'] for c in cases}
    check('fixed_commit_consistent',cat['audited_commit']==findings['commit']==matrix['audit_commit']==SHA)
    check('46_unique_semantic_features',len(features)==len(fids)==46)
    check('11_unique_code_findings',len(findings['findings'])==len({f['id'] for f in findings['findings']})==11)
    check('56_unique_acceptance_scenarios',len(cases)==len(cids)==matrix['case_count']==56)
    check('all_features_remain_target_required',all(f['target_required'] for f in features))
    check('all_features_have_official_references',all(f['source_refs'] and all(s in sources for s in f['source_refs']) for f in features))
    check('all_case_feature_refs_resolve',all(set(c['feature_ids'])<=fids for c in cases))
    check('all_case_source_refs_resolve',all(set(c['source_refs'])<=set(sources) for c in cases))
    mapped={f for c in cases if c['title']!='完整目录防虚假缩范围' for f in c['feature_ids']}
    check('every_feature_has_specific_test_design',mapped==fids,sorted(fids-mapped))
    check('all_new_tests_not_run',matrix['status']=='NOT_RUN' and all(c['status']=='NOT_RUN' and c['evidence'] is None for c in cases))
    check('no_native_qualification_claimed',all(f['paper_semantic_tests']=='NOT_RUN' and f['venue_tests']=='NOT_RUN' for f in features))
    check('no_live_enabled',cat['live_enabled'] is False and all(f['live_enabled'] is False for f in features))
    check('static_review_not_runtime_reproduction',findings['method']=='STATIC_CODE_REVIEW' and findings['runtime_reproduction'] is False)
    check('4_documentation_discrepancies_explicit',len(discrepancies['items'])==4 and all(d['protocol_test']=='NOT_RUN' for d in discrepancies['items']))
    check('unread_quote_limit_not_invented',any(d['id']=='DOC-04' and '未能读取' in d['observed'] for d in discrepancies['items']))
    check('existing_product_scope_preserved',set(cat['scope'])=={'SPOT','USD_M_PERP','US_EQUITY'})
    required=['AetherTrade_Binance_Order_Architecture_2026-10-28.md','ACCEPTANCE_MATRIX.md','CODEX_HANDOFF.md','README.md']
    check('all_handoff_documents_present',all((ROOT/f).is_file() and (ROOT/f).stat().st_size>500 for f in required))
    missing=[]; refs=[]; fences=[]
    for p in ROOT.glob('*.md'):
        s=p.read_text(encoding='utf-8')
        if s.count('```')%2: fences.append(p.name)
        for label,url in re.findall(r'\[([^\]]+)\]\(([^)]+)\)',s):
            if re.fullmatch(r'(B\d+|C\d+|N\d+)',label) and label not in sources:refs.append((p.name,label))
            if not re.match(r'^[a-z]+://',url) and not url.startswith('#'):
                if not (p.parent/url.split('#')[0]).exists():missing.append((p.name,url))
    check('local_markdown_links_valid',not missing,missing)
    check('inline_source_ids_resolve',not refs,refs)
    check('code_fences_balanced',not fences,fences)
    main_text=(ROOT/required[0]).read_text(encoding='utf-8')
    check('native_child_vs_local_child_distinguished','本地与交易所生成子单的区别' in main_text)
    check('current_code_not_old_direct_fill_assessment','旧版store直接即时成交' in main_text and SHA in main_text)
    check('approved_schema_migration_explicit','旧UNKNOWN保持原ID查询' in main_text)
    check('no_product_source_or_account_config_created',not (ROOT/'src').exists() and not (ROOT/'.env').exists())
    result={'status':'PASS' if all(c['passed'] for c in checks) else 'FAIL',
            'scope':'DOCUMENT_AND_CATALOG_METADATA_ONLY','audited_commit':SHA,
            'feature_count':len(features),'finding_count':len(findings['findings']),
            'acceptance_count':len(cases),'product_tests_run':False,'database_tests_run':False,
            'venue_requests_sent':0,'live_enabled':False,'checks':checks,
            'files_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.iterdir()) if p.is_file() and p.name!='design-validation.json'}}
    (ROOT/'design-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','files_sha256')},ensure_ascii=False,indent=2))
    print('Metadata checks:',sum(c['passed'] for c in checks),'/',len(checks))
    for c in checks:
        if not c['passed']:print('FAILED',c['check'],c['detail'])
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':sys.exit(main())
