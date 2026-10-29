"""Exact unsigned owned-order control requests; no transport or permission.

Current facts belong to the owner host, not to a trading request. Recompilation
prevents a caller from changing a route, identity or parameter in a saved plan.
The command remains independently durable/permit checked at the write boundary.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.intents.order_management_compilation import (
    ManagementCompilation, ManagementEndpoint, ManagementKind,
    ManagementParameter, compile_order_management,
)
from trading_system.intents.program_compilation import Namespace, compile_order_program


def digest(value):
    return canonical_hash(domain_payload(value))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueManagementRequest:
    scope: t.ScopeKey
    namespace: Namespace
    command_id: str | None
    management_hash: str
    instruction_hash: str
    target_hash: str
    source_request_hash: str
    request_index: int
    method: Literal['GET', 'POST', 'PUT', 'DELETE']
    endpoint: ManagementEndpoint
    parameters: tuple[ManagementParameter, ...]
    target_venue_id: str | None
    original_client_id: str
    venue_symbol: str
    filter_original_identity: bool

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_released(self) -> Literal[False]:
        return False

    @property
    def requires_independent_command(self) -> bool:
        return self.method != 'GET'

    def form_parameters(self):
        return tuple((p.name.value, str(p.value).lower() if type(p.value) is bool
            else str(p.value)) for p in self.parameters)


def materialize_management(plan, *, current_target, rules, capabilities, profile,
        now, scope, expected_owner_strategy_id, expected_ownership_epoch,
        expected_execution_epoch, expected_policy_revision, expected_build_digest,
        expected_rule_revision, expected_capabilities_hash,
        expected_instrument_ref=None, allow_round_down=False):
    require(type(plan) is ManagementCompilation, 'MANAGEMENT_COMPILATION_REQUIRED')
    if plan.instruction.kind == ManagementKind.CANCEL_REPLACE:
        # The management compiler consumes a compiled replacement. Replay its
        # original proposal as well; its supplied parameters are not authority.
        require(type(expected_instrument_ref) is str and bool(expected_instrument_ref),
            'REPLACEMENT_INSTRUMENT_BINDING_REQUIRED')
        replacement = plan.instruction.replacement
        replay = compile_order_program(replacement.original_proposal, rules, capabilities,
            profile=profile, scope=scope, now=now, expected_build_digest=expected_build_digest,
            expected_rule_revision=expected_rule_revision,
            expected_capabilities_hash=expected_capabilities_hash,
            expected_instrument_ref=expected_instrument_ref, allow_round_down=allow_round_down)
        require(digest(replay) == digest(replacement), 'REPLACEMENT_COMPILATION_CHANGED')
    expected = compile_order_management(plan.instruction, current_target, command=plan.command,
        scope=scope, now=now, expected_owner_strategy_id=expected_owner_strategy_id,
        expected_ownership_epoch=expected_ownership_epoch,
        expected_execution_epoch=expected_execution_epoch,
        expected_policy_revision=expected_policy_revision, rules=rules,
        capabilities=capabilities, profile=profile, expected_build_digest=expected_build_digest,
        expected_rule_revision=expected_rule_revision,
        expected_capabilities_hash=expected_capabilities_hash)
    require(digest(expected) == digest(plan), 'MANAGEMENT_COMPILATION_CHANGED')
    return tuple(VenueManagementRequest(scope=scope, namespace=request.namespace,
        command_id=None if plan.command is None else plan.command.command_id,
        management_hash=digest(plan), instruction_hash=plan.instruction_hash,
        target_hash=plan.target_hash, source_request_hash=digest(request), request_index=index,
        method=request.method, endpoint=request.endpoint, parameters=request.parameters,
        target_venue_id=current_target.venue_id, original_client_id=current_target.client_id,
        venue_symbol=profile.venue_symbol, filter_original_identity=request.filter_original_identity)
        for index, request in enumerate(expected.requests))


def verify_management(request, plan, **current_facts):
    require(type(request) is VenueManagementRequest, 'MANAGEMENT_REQUEST_REQUIRED')
    requests = materialize_management(plan, **current_facts)
    require(type(request.request_index) is int and 0 <= request.request_index < len(requests),
        'MANAGEMENT_REQUEST_INDEX_INVALID')
    require(digest(request) == digest(requests[request.request_index]), 'MANAGEMENT_REQUEST_CHANGED')
    return digest(request)
