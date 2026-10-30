"""Explicit isolated PAPER composition; no default host or external activation.

All authority and accounting remain in the existing domain services. This host
never provisions funds, invents an intake, renews a permit or supplies ALLOW.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.engine.paper import PaperEngine
from trading_system.execution.program_commands import ProgramCommandBoundary
from trading_system.execution.program_accounting import ProgramPaperAccountingService,ProgramAccountingResult
from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
from trading_system.execution.program_fill_source import ProgramFillSourceService
from trading_system.execution.program_legacy_opening import read_program_local_account_opening
from trading_system.execution.program_order_projection import (
    ProgramPaperOrderProjectionService,ProgramOrderProjectionResult,read_program_working_inputs,_original_bundle,
)
from trading_system.execution.program_submission_observation import ProgramSubmissionObservationService
from trading_system.execution.program_management_obligations import management_obligations
from trading_system.execution.program_book_cash import reconcile_program_book_cash
from trading_system.recovery.original_creation import read_original_creation
from trading_system.risk.program_financial_admission import ProgramFinancialBundle
from trading_system.storage.uow import UnitOfWork
from .execution_owner import ExecutionOwner


def need(value,code):
    if not value:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramPaperHostObservation:
    accounting:ProgramAccountingResult
    orders:ProgramOrderProjectionResult|None

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


class ProgramPaperHost:
    def __init__(self,*,owner,boundary,owner_uow,source_service,trade_id_scope):
        need(type(owner) is ExecutionOwner and type(owner.engine) is PaperEngine
            and type(boundary) is ProgramCommandBoundary and type(owner_uow) is UnitOfWork
            and type(source_service) is ProgramFillSourceService,'PROGRAM_HOST_ACTUAL_COMPOSITION_REQUIRED')
        engine=owner.engine;scope=engine.scope
        need(scope.environment==t.Environment.PAPER and scope.product==t.Product.SPOT
            and scope==boundary.scope==source_service.scope
            and source_service.verifier.engine is engine,'PROGRAM_HOST_SCOPED_PAPER_SOURCE_REQUIRED')
        # ProgramFillSourceService already enforces exact static V1/V2 verifier
        # types. New quote/native/managed results cannot borrow either route.
        self.owner,self.boundary,self.uow,self.scope=owner,boundary,owner_uow,scope
        self.engine=engine
        self.accounting=ProgramPaperAccountingService(owner_uow,scope,source_service=source_service,
            trade_id_scope=trade_id_scope,financial_posting=ProgramOwnedSpotPosting(read_opening=read_program_local_account_opening))
        self.orders=ProgramPaperOrderProjectionService(owner_uow,scope,
            observer=ProgramSubmissionObservationService(source_service.uow,scope,verifier=source_service.verifier))

    def _original(self,command_id,*,dispatch=False):
        need(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        with self.uow.transaction(self.scope) as tx:
            original=read_original_creation(tx,command_id)
            need(original is not None,'PROGRAM_HOST_ORIGINAL_REQUIRED')
            bundle=_original_bundle(tx,original)
            plan=bundle.archive.compilation
            need(type(bundle) is ProgramFinancialBundle and plan.original_program.template=='ATOMIC'
                and len(plan.nodes)==len(original.request.assigned_ids.members)==1
                and plan.nodes[0].quantity.kind=='BASE_QUANTITY'
                and original.request.assigned_ids.parent_client_id is None,
                'PROGRAM_HOST_EXECUTION_FAMILY_UNQUALIFIED')
            duties=management_obligations(tx)
            need(not any(o.unresolved and (dispatch or (o.original.scope==self.scope
                and o.original.original_command_id==command_id)) for o in duties),
                'PROGRAM_HOST_MANAGED_SOURCE_BRIDGE_REQUIRED')
        return original

    def dispatch(self,command_id,lease):
        need(type(lease) is t.OwnerLease,'PROGRAM_HOST_CURRENT_LEASE_REQUIRED')
        with self.owner.session(lease.shard):
            with self.owner.guard(lease):
                original=self._original(command_id,dispatch=True)
                need(original.current_state=='DURABLE_READY','PROGRAM_HOST_QUERY_ORIGINAL_NO_RETRY')
            permit=self.boundary.mark_may_have_sent(command_id,lease)
            return self.engine.submit_creation(original.initial,original.request,permit,
                boundary=self.boundary,lease=lease,owner=self.owner)

    def observe(self,command_id):
        original=self._original(command_id)
        need(original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),'PROGRAM_HOST_SENT_ORIGINAL_REQUIRED')
        accounting=self.accounting.apply(command_id)
        if accounting.status=='SOURCE_BLOCKED':
            # A failed financial-source query must also become the latest
            # protected submission observation. An older accepted snapshot
            # cannot keep supplying current working inputs after this failure.
            self.orders.observer.observe(command_id)
        orders=None if accounting.status=='SOURCE_BLOCKED' else self.orders.apply(command_id)
        return ProgramPaperHostObservation(accounting=accounting,orders=orders)

    def reconcile_book(self):
        # Explicit owner operation; replay itself never repairs a stale binding.
        with self.uow.transaction(self.scope) as tx:
            return reconcile_program_book_cash(tx,read_opening=read_program_local_account_opening)

    def working_inputs(self):
        with self.uow.transaction(self.scope) as tx:
            return read_program_working_inputs(tx,tx.projections.book(self.scope))
