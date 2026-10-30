"""Tagged actual SIM effective history; never a financial-source registration."""
from dataclasses import dataclass,fields,replace
from decimal import Decimal
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,canonical_hash,canonical_json
from trading_system.engine.paper_market_v2 import PaperCreationReportV2
from trading_system.execution.program_management_commands import require

SOURCE='PAPER_MANAGED_CREATION_SOURCE_V1'

@dataclass(frozen=True,slots=True,kw_only=True)
class PaperEffectiveOrderV1:
    command_id:str
    client_id:str
    prior_revision:int
    result_revision:int
    original_quantity:Decimal
    prior_quantity:Decimal
    effective_quantity:Decimal
    cumulative_quantity:Decimal
    queue_priority:int
    binding_json:str
    receipt_json:str

@dataclass(frozen=True,slots=True,kw_only=True)
class PaperManagedCreationReportV1(PaperCreationReportV2):
    effective_history:tuple[PaperEffectiveOrderV1,...]=()
    contract_version:Literal['2026-10-30.managed-v1']='2026-10-30.managed-v1'
    record_kind:Literal['PAPER_MANAGED_CREATION_REPORT_V1']='PAPER_MANAGED_CREATION_REPORT_V1'


def decorate_managed_report(engine,db,report):
    if db.execute("SELECT to_regclass('public.paper_effective_orders_v1') AS relation").fetchone()['relation'] is None:return report
    rows=db.execute('SELECT e.*,a.payload AS actual_receipt FROM public.paper_effective_orders_v1 e '
        'JOIN public.paper_management_actions_v4 a USING(scope_key,command_id) '
        'WHERE e.scope_key=%s AND e.client_id=ANY(%s) ORDER BY e.result_revision LIMIT 4097',
        (engine._scope_key,[o.client_id for o in report.orders])).fetchall()
    if not rows:return report
    require(len(rows)<=4096 and type(report) is PaperCreationReportV2 and len(report.orders)==1,'MANAGED_SOURCE_BOUND_REQUIRED')
    history=[];previous=None
    for row in rows:
        require(row['actual_receipt']==row['receipt'],'MANAGED_RECEIPT_CHANGED')
        # Independently protected source checks original authorization + receipt.
        actual=db.execute('SELECT public.paper_effective_quantity_v1(%s,%s,%s) AS quantity',
            (engine._scope_key,row['client_id'],row['original_quantity'])).fetchone()['quantity']
        require(actual==report.orders[0].quantity,'MANAGED_CURRENT_QUANTITY_CHANGED')
        b=row['binding'];w=b['write'];receipt=row['receipt']
        require(w['original_command_id']==report.command_id and w['original_client_id']==row['client_id']
            and w['target_venue_id']==report.orders[0].venue_order_id and w['operation']=='KEEP_PRIORITY'
            and receipt['binding_hash']==w['binding_hash'] and receipt['command_id']==row['command_id']
            and row['original_quantity']==Decimal(report.accepted_contexts[0].terms.quantity), 'MANAGED_ORIGINAL_BINDING_CHANGED')
        require(previous is None or (row['prior_quantity']==previous['effective_quantity']
            and row['prior_revision']>=previous['result_revision']), 'MANAGED_HISTORY_CHANGED')
        history.append(PaperEffectiveOrderV1(**{k:row[k] for k in ('command_id','client_id','prior_revision','result_revision',
            'original_quantity','prior_quantity','effective_quantity','cumulative_quantity','queue_priority')},
            binding_json=canonical_json(b),receipt_json=canonical_json(receipt)))
        previous=row
    values={f.name:getattr(report,f.name) for f in fields(PaperCreationReportV2) if f.name not in ('contract_version','record_kind','evidence')}
    payload={**{k:domain_payload(v) if k=='request' else v for k,v in {}.items()},'base':domain_payload(report),
        'history':[domain_payload(v) for v in history],'record_kind':'PAPER_MANAGED_CREATION_REPORT_V1'}
    return PaperManagedCreationReportV1(**values,effective_history=tuple(history),evidence=t.EvidenceRef(
        id='paper-managed:'+report.command_id,hash=canonical_hash(payload),source=SOURCE,environment=t.Environment.PAPER,
        visible_at=engine._clock().utc_now))
