"""Restricted management of actual existing PAPER orders; no matcher or fills."""
from dataclasses import replace
from datetime import timedelta
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,canonical_json,utc_timestamp
from trading_system.engine.paper import PaperEngine
from trading_system.execution.program_management_commands import (ManagementBoundary,ManagementPermit,ManagementRepository,digest,require)
from trading_system.intents.order_management_compilation import OwnedTargetSnapshot
from trading_system.runtime.execution_owner import ExecutionOwner
from trading_system.storage.models import scope_id

class PaperManagementTargetReader:
    def __init__(self,engine):
        require(type(engine) is PaperEngine,'MANAGEMENT_ACTUAL_PAPER_ENGINE_REQUIRED')
        self.engine=engine

    def capture(self,original):
        require(original is not None and original.initial.scope==self.engine.scope,'MANAGEMENT_ORIGINAL_SCOPE_REQUIRED')
        report=self.engine.query_creation(original.initial.command_id)
        require(report is not None and digest(report.request)==original.initial.request_hash,
            'MANAGEMENT_ACTUAL_SOURCE_REQUIRED')
        # No parent ACK is evidence that all children share this quantity/state.
        require(len(report.orders)==len(report.accepted_contexts)==1 and report.request.assigned_ids.parent_client_id is None,
            'MANAGEMENT_PARENT_CHILD_CURRENT_PROOF_REQUIRED')
        row=report.orders[0];context=report.accepted_contexts[0];w=original.initial
        require(row.namespace==w.service_namespace and row.client_id==original.request.assigned_ids.members[0].client_id
            and digest(context.original_write)==digest(replace(w,state=context.original_write.state)),
            'MANAGEMENT_SOURCE_IDENTITY_CHANGED')
        now=self.engine._clock()
        require(now.clock_health=='HEALTHY','MANAGEMENT_TARGET_CLOCK_REQUIRED')
        return OwnedTargetSnapshot(scope=w.scope,namespace=original.request.namespace,program_id=w.program_id,
            program_hash=w.canonical_program_hash,venue_id=row.venue_order_id,client_id=row.client_id,
            owner_strategy_id=w.owner_strategy_id,ownership_epoch=w.ownership_epoch,execution_epoch=w.execution_epoch,
            policy_revision=w.policy_revision,status='NEW' if row.status=='SUBMITTED' else row.status,
            order=context.compilation.nodes[0].original.order,original_quantity=format(row.quantity,'f'),
            cumulative_quantity=format(row.filled_quantity,'f'),parent_list_id=None,evidence=report.evidence,
            # Source observation version is the actual immutable accepted clock
            # plus report hash; capture time is independently checked below.
            as_of=context.terms.accepted_clock.utc_now,
            valid_until=(utc_timestamp(context.terms.accepted_clock.utc_now)+timedelta(days=1)).isoformat())

def submit_management(engine,write,permit,*,boundary,lease,owner):
    require(type(boundary) is ManagementBoundary and type(permit) is ManagementPermit,
        'MANAGEMENT_PRIVATE_PERMIT_REQUIRED')
    require(type(owner) is ExecutionOwner and owner.engine is engine and type(engine) is PaperEngine,
        'MANAGEMENT_ACTUAL_OWNER_REQUIRED')
    require(write.scope==engine.scope==boundary.scope and write.command_id==permit.command_id,
        'MANAGEMENT_SCOPE_REQUIRED')
    with owner.guard(lease):
        binding=boundary.consume(permit,lease)
        require(digest(replace(binding.write,state=write.state))==digest(write),'MANAGEMENT_WRITE_CHANGED')
        # Fresh business guards run after consumption and again after connection
        # acquisition. Transactions close before SIM IO; no stale DTO authorizes.
        boundary.recheck_consumed(permit,lease)
        with engine._connect() as db:
            boundary.recheck_consumed(permit,lease)
            row=db.execute('SELECT public.paper_manage_order_v4(%s,%s,%s::jsonb)',
                (engine._scope_key,write.command_id,canonical_json(domain_payload(binding)))).fetchone()
        return row['paper_manage_order_v4']

def provision_paper_management(admin_dsn,roles):
    from pathlib import Path
    import psycopg
    from psycopg import sql
    from trading_system.engine.paper_db_roles import PaperDatabaseRoles
    require(type(roles) is PaperDatabaseRoles,'MANAGEMENT_RESTRICTED_PAPER_ROLES_REQUIRED')
    with psycopg.connect(admin_dsn) as db:
        require(db.execute("SELECT starts_with(current_database(),'paper_engine_')").fetchone()==(True,),
            'MANAGEMENT_ISOLATED_PAPER_DATABASE_REQUIRED')
        db.execute((Path(__file__).resolve().parents[3]/'migrations/0023_paper_management.sql').read_text(encoding='utf-8'))
        db.execute(sql.SQL('GRANT SELECT ON public.paper_management_actions_v4 TO {}').format(sql.Identifier(roles.runtime_role)))
        db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.paper_manage_order_v4(text,text,jsonb) TO {}').format(sql.Identifier(roles.runtime_role)))
