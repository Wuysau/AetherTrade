"""PAPER submission adapter for an independently admitted local child.

This is deliberately separate from the V3 `paper_market.submit_creation` path:
the local child has its own command/permit tables and must not be recast as an
`ApplicationWriteV3`. It reuses the existing paper engine order/terms/command
storage and the public PAPER matcher. PostgreSQL execution remains NOT_RUN.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from psycopg.types.json import Jsonb

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.order_program import amount
from trading_system.execution.creation_requests import digest, require
from trading_system.execution.local_child_admission import (
    LocalChildCommand, LocalChildCommandBoundary, LocalChildPermit, read_local_child_command,
)
from trading_system.execution.local_child_original import read_local_child_original
from trading_system.execution.local_child_requests import LocalChildRequest
from trading_system.runtime.execution_owner import ExecutionOwner
from trading_system.storage.models import scope_id

from .paper import PaperEngine
from .paper_market import market_key


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildSubmissionReceipt:
    command_id: str
    request_hash: str
    status: str
    evidence: tuple[t.EvidenceRef, ...] = ()

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


def _terms(engine, command, request, original, *, client, key):
    compilation = original.compilation
    node = compilation.node
    order = node.original.order
    rules = None
    return {
        'contract_version': '2026-10-31',
        'record_kind': 'PAPER_LOCAL_CHILD_TERMS_V1',
        'scope': domain_payload(engine.scope), 'client_id': client,
        'node_id': node.original.node_id, 'namespace': node.namespace.value,
        'quantity': node.quantity.value, 'quantity_step': None,
        'side': order.side, 'order_type': node.order_type,
        'limit_price': order.price.value if order.price.kind == 'FIXED_LIMIT' else None,
        'post_only': order.post_only, 'tif': order.tif.kind,
        'multiplier': node.quantity_multiplier, 'quote_asset': node.notional_asset,
        'quote_fee_rate': engine._matching_model.quote_fee_rate,
        'model': domain_payload(engine._matching_model),
        'request': domain_payload(request.request),
        'write': domain_payload(command),
        'rules': rules,
        'compilation': domain_payload(compilation.parent),
        'execution_envelope': None,
        'local_child': True,
    }


def submit_local_child(engine: PaperEngine, command: LocalChildCommand,
                       request: LocalChildRequest, permit: LocalChildPermit, *,
                       boundary: LocalChildCommandBoundary, lease: t.OwnerLease,
                       owner: ExecutionOwner) -> LocalChildSubmissionReceipt:
    require(type(engine) is PaperEngine and type(command) is LocalChildCommand
            and type(request) is LocalChildRequest and type(permit) is LocalChildPermit
            and type(boundary) is LocalChildCommandBoundary
            and type(owner) is ExecutionOwner and owner.engine is engine,
            'PAPER_LOCAL_CHILD_COMPOSITION_REQUIRED')
    require(command.scope == request.request.scope == engine.scope == boundary.scope
            and command.command_id == request.request.command_id == permit.command_id
            and command.request_hash == permit.request_hash == digest(request),
            'PAPER_LOCAL_CHILD_BINDING_REQUIRED')
    with owner.guard(lease):
        consumed = boundary.consume(permit, lease)
        require(type(consumed) is LocalChildCommand and digest(consumed) == digest(command),
                'PAPER_LOCAL_CHILD_CONSUMED_COMMAND_CHANGED')
    with boundary.uow.transaction(engine.scope) as tx:
        original = read_local_child_original(tx, command.command_id)
        require(original is not None and original.current_state == 'MAY_HAVE_SENT'
                and digest(original.command) == digest(command)
                and digest(original.request) == digest(request),
                'PAPER_LOCAL_CHILD_ORIGINAL_CHANGED')
        stored = read_local_child_command(tx, command.command_id)
        require(stored is not None and stored.state == 'MAY_HAVE_SENT',
                'PAPER_LOCAL_CHILD_COMMITTED_MAY_REQUIRED')
    node = original.compilation.node
    order = node.original.order
    require(order.quantity.kind == 'BASE_QUANTITY'
            and amount(order.quantity.value) == amount(node.quantity.value),
            'PAPER_LOCAL_CHILD_QUANTITY_CHANGED')
    ids = request.request.assigned_ids
    client_ids = tuple(m.client_id for m in ids.members) + (
        (ids.parent_client_id,) if ids.parent_client_id else ())
    require(len(client_ids) == 1, 'PAPER_LOCAL_CHILD_CLIENT_REQUIRED')
    client = client_ids[0]
    terms = _terms(engine, command, request, original, client=client,
                   key=market_key(engine.scope.product, engine.scope.instrument_uid))
    with owner.guard(lease):
        with engine._connect() as db:
            key = market_key(engine.scope.product, engine.scope.instrument_uid)
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))
            with owner.guard(lease):
                require(db.execute('SELECT 1 FROM public.paper_engine_commands '
                                   'WHERE scope_key=%s AND command_id=%s',
                                   (engine._scope_key, command.command_id)).fetchone() is None,
                        'PAPER_LOCAL_CHILD_ORIGINAL_ID_ALREADY_OBSERVED')
            db.execute('INSERT INTO public.paper_engine_orders(environment,scope_key,client_id,order_id,'
                       'logical_id,owner_strategy_id,ownership_epoch,quantity_unit,price_currency,quantity) '
                       'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       ('PAPER', engine._scope_key, client, 'paper-local-child:' + client, client,
                        command.owner_strategy_id, command.ownership_epoch, node.quantity_unit,
                        node.notional_asset, amount(node.quantity.value)))
            db.execute('INSERT INTO public.paper_order_terms(scope_key,client_id,market_key,terms) '
                       'VALUES(%s,%s,%s,%s)',
                       (engine._scope_key, client, key, Jsonb(terms)))
            db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                       ('PAPER', engine._scope_key, command.command_id, client, client,
                        'CREATE_REQUEST', digest(request.request), Jsonb(domain_payload(request.request))))
            db.execute('SELECT public.paper_match_order(%s,%s)', (engine._scope_key, client))
    report = engine.query_creation(command.command_id)
    return LocalChildSubmissionReceipt(command_id=command.command_id,
                                       request_hash=digest(request),
                                       status=report.status, evidence=report.evidence)
