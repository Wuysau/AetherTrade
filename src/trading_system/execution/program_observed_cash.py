"""Conservative coverage of private observations; never derives cash credit.

Submission knownness can commit a native fill snapshot before a per-fill
approval or FillLedger row exists. Compact 0018 indices enumerate every account
snapshot/leaf; full proof/global status is loaded and hashed once per snapshot
through the existing transaction-scoped source reader, never once per fill.
"""
from itertools import groupby

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.models import MissingEvidence,decode,scope_id
from .program_fill_source import decode_source_normalized


def need(value,code):
    if not value:raise MissingEvidence(code)


def observed_source_rows(tx,account):
    if tx.execute("SELECT to_regclass('ledger.program_fill_source_snapshots')").fetchone()[0] is None:return ()
    need(tx.execute("SELECT to_regclass('ledger.program_fill_source_headers')").fetchone()[0] is not None,
        'OBSERVED_PROGRAM_SOURCE_INDEX_REQUIRED')
    # Normally only the compact context is read. The parent scope is consulted
    # only if a historical restore lost a header; that row must not disappear.
    return tx.execute('SELECT s.scope_id,s.command_id,s.snapshot_id,h.context,h.leaf_count,'
        's.database_transaction::text,s.xmin::text,pg_current_xact_id()::text,l.normalized_hash,l.payload '
        'FROM ledger.program_fill_source_snapshots s '
        'LEFT JOIN ledger.program_fill_source_headers h USING(scope_id,snapshot_id) '
        'LEFT JOIN ledger.program_fill_source_leaves l ON l.scope_id=s.scope_id AND l.snapshot_id=s.snapshot_id '
        "WHERE COALESCE(h.context#>>'{scope,environment}',s.payload#>>'{scope,environment}')=%s "
        "AND COALESCE(h.context#>>'{scope,venue_account_id}',s.payload#>>'{scope,venue_account_id}')=%s "
        "AND COALESCE(h.context#>>'{scope,product}',s.payload#>>'{scope,product}')=%s "
        'ORDER BY s.scope_id,s.snapshot_id,l.normalized_hash',
        (str(account.environment),account.venue_account_id,str(account.product))).fetchall()


def native_signature(normalized):
    fill=domain_payload(normalized.fill);ref=fill.pop('evidence')
    # Archive location/receipt time can change on a genuine new observation.
    # Immutable native content, units, economic data and raw bytes cannot.
    return canonical_hash(dict(fill=fill,raw={k:ref[k] for k in ('hash','environment','source')},
        economics=domain_payload(normalized.economics),side=normalized.side,fees_known=normalized.fees_known))


def require_observed_cash_coverage(rows,facts,read_scope):
    duties=[]
    for _,members in groupby(rows,key=lambda r:(r[0],r[2])):
        members=tuple(members);sid,command,snapshot_hash,context,count,xid,xmin,current,_,_=members[0]
        need(context is not None and xid!=current and int(xid)%(2**32)==int(xmin),
            'OBSERVED_PROGRAM_SOURCE_INDEX_REQUIRED')
        scope=decode(t.ScopeKey,context['scope'])
        need(scope_id(scope)==sid and context['command_id']==command,'OBSERVED_PROGRAM_SOURCE_BINDING_CHANGED')
        reader=read_scope(scope)
        source=reader.sources.read_snapshot(command,snapshot_hash)
        need(canonical_hash(context)==canonical_hash({k:v for k,v in source.items()
            if k not in ('verification','normalized_fills')}),'OBSERVED_PROGRAM_SOURCE_INDEX_CHANGED')
        leaves=tuple((r[8],r[9]) for r in members if r[8] is not None)
        need(len(leaves)==count==len(source['normalized_fills'])
            and {k for k,_ in leaves}==set(source['normalized_fills']),
            'OBSERVED_PROGRAM_SOURCE_INDEX_INCOMPLETE')
        verification=source['verification']
        need(verification['source_verified'] is True and verification['status']=='VERIFIED'
            and verification['discrepancies']==[],'OBSERVED_PROGRAM_SOURCE_RECONCILIATION_REQUIRED')
        duties.extend(verification['retained_duties'])
        for normalized_hash,payload in leaves:
            need(canonical_hash(payload)==normalized_hash and canonical_hash(source['normalized_fills'][normalized_hash])==normalized_hash,
                'OBSERVED_PROGRAM_SOURCE_INDEX_CHANGED')
            normalized=decode_source_normalized(payload,verification);fill=normalized.fill
            need(fill.scope==fill.order.scope==scope and fill.native_trade_id is not None,
                'OBSERVED_PROGRAM_NATIVE_IDENTITY_CHANGED')
            if hasattr(normalized,'source_v2'):
                need(normalized.source_v2.consumption_context_hash==context['consumption_hash'],
                    'OBSERVED_PROGRAM_SOURCE_BINDING_CHANGED')
            identity=canonical_hash(dict(scope=domain_payload(scope),venue=fill.venue,native_trade_id=fill.native_trade_id))
            need(identity in facts,'OBSERVED_PROGRAM_EXECUTIONS_UNPOSTED')
            need(facts[identity].command_id==command
                and native_signature(normalized)==native_signature(facts[identity].approval.normalized),
                'OBSERVED_PROGRAM_NATIVE_ECONOMICS_CONFLICT')
    return tuple(dict.fromkeys(duties))
