"""Owned-identity management request plans, independent of proposal expiry.

All writes require later durable plan-hash binding, locked financial/action
verification and a fresh single-use permit. Query plans are read-only and may
recover UNKNOWN using the original client ID. No plan releases funds.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_lifecycle import ApplicationWrite
from trading_system.contracts.order_program import AtomicOrderSpec, amount, identifier, variant
from trading_system.contracts.order_snapshots import OrderCapabilitySnapshot, OrderRuleSnapshot
from .product_rules import TrustedProductRules
from .program_compilation import FieldName, Namespace, ProgramCompilation, _quantity_terms
from .semantic_validation import quantity_on_grid, require


class ManagementKind(StrEnum):
    QUERY = 'QUERY'
    CANCEL = 'CANCEL'
    KEEP_PRIORITY = 'KEEP_PRIORITY'
    CANCEL_REPLACE = 'CANCEL_REPLACE'
    MODIFY_LIMIT = 'MODIFY_LIMIT'


class ManagementField(StrEnum):
    ORDER_ID = 'orderId'
    ORIGINAL_CLIENT_ID = 'origClientOrderId'
    CLIENT_ORDER_ID = 'clientOrderId'
    ORDER_LIST_ID = 'orderListId'
    ALGO_ID = 'algoId'
    CLIENT_ALGO_ID = 'clientAlgoId'
    NEW_QUANTITY = 'newQty'
    NEW_CLIENT_ID = 'newClientOrderId'
    CANCEL_ORDER_ID = 'cancelOrderId'
    CANCEL_REPLACE_MODE = 'cancelReplaceMode'
    ORDER_RATE_LIMIT_MODE = 'orderRateLimitExceededMode'


class ManagementEndpoint(StrEnum):
    SPOT_ORDER = '/api/v3/order'
    SPOT_LIST = '/api/v3/orderList'
    SPOT_KEEP_PRIORITY = '/api/v3/order/amend/keepPriority'
    SPOT_CANCEL_REPLACE = '/api/v3/order/cancelReplace'
    USD_M_ORDER = '/fapi/v1/order'
    USD_M_CONDITIONAL = '/fapi/v1/algoOrder'
    SPOT_EXECUTION = '/sapi/v1/algo/spot/order'
    USD_M_EXECUTION = '/sapi/v1/algo/futures/order'
    SPOT_EXECUTION_OPEN = '/sapi/v1/algo/spot/openOrders'
    SPOT_EXECUTION_HISTORY = '/sapi/v1/algo/spot/historicalOrders'
    USD_M_EXECUTION_OPEN = '/sapi/v1/algo/futures/openOrders'
    USD_M_EXECUTION_HISTORY = '/sapi/v1/algo/futures/historicalOrders'
    EQUITY_CANCEL = '/sapi/v1/equity/order/cancel'
    EQUITY_QUERY = '/sapi/v1/equity/order/detail'


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnedTargetSnapshot:
    scope: t.ScopeKey
    namespace: Namespace
    program_id: str
    program_hash: str
    venue_id: str | None
    client_id: str
    owner_strategy_id: str
    ownership_epoch: int
    execution_epoch: int
    policy_revision: int
    status: Literal['NEW', 'PARTIALLY_FILLED', 'UNKNOWN', 'CANCEL_PENDING', 'CANCELED', 'FILLED', 'EXPIRED', 'REJECTED']
    order: AtomicOrderSpec | None
    original_quantity: str | None
    cumulative_quantity: str | None
    parent_list_id: str | None
    evidence: t.EvidenceRef
    as_of: str
    valid_until: str

    def __post_init__(self):
        for value in (self.program_id, self.client_id, self.owner_strategy_id):
            identifier(value)
        for value in (self.venue_id, self.parent_list_id):
            if value is not None:
                identifier(value)
        if len(self.program_hash) != 64 or min(self.ownership_epoch, self.execution_epoch, self.policy_revision) < 1:
            raise ValueError('Owned target revisions required')
        if self.evidence.environment != self.scope.environment:
            raise ValueError('Owned target evidence environment mismatch')
        if utc_timestamp(self.as_of) >= utc_timestamp(self.valid_until):
            raise ValueError('Invalid target validity')
        if (self.original_quantity is None) != (self.cumulative_quantity is None):
            raise ValueError('Owned quantities must be paired')
        if self.original_quantity is not None:
            if amount(self.cumulative_quantity, zero=True) > amount(self.original_quantity):
                raise ValueError('Owned cumulative exceeds original quantity')


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementInstruction:
    kind: ManagementKind
    new_quantity: str | None = None
    new_price: str | None = None
    replacement: ProgramCompilation | None = None
    cancel_replace_mode: Literal['STOP_ON_FAILURE', 'ALLOW_FAILURE'] | None = None
    order_rate_limit_mode: Literal['DO_NOTHING', 'CANCEL_ONLY'] | None = None

    def __post_init__(self):
        values = dict(new_quantity=self.new_quantity, new_price=self.new_price, replacement=self.replacement,
            cancel_replace_mode=self.cancel_replace_mode, order_rate_limit_mode=self.order_rate_limit_mode)
        variant(self.kind, {
            ManagementKind.QUERY: (set(), set()), ManagementKind.CANCEL: (set(), set()),
            ManagementKind.KEEP_PRIORITY: ({'new_quantity'}, set()),
            ManagementKind.MODIFY_LIMIT: ({'new_quantity', 'new_price'}, set()),
            ManagementKind.CANCEL_REPLACE: ({'replacement', 'cancel_replace_mode', 'order_rate_limit_mode'}, set()),
        }, values)
        for value in (self.new_quantity, self.new_price):
            if value is not None:
                amount(value)
        if self.cancel_replace_mode is not None and self.cancel_replace_mode not in ('STOP_ON_FAILURE', 'ALLOW_FAILURE'):
            raise ValueError('Unknown cancel-replace mode')
        if self.order_rate_limit_mode is not None and self.order_rate_limit_mode not in ('DO_NOTHING', 'CANCEL_ONLY'):
            raise ValueError('Unknown order-rate-limit mode')


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementParameter:
    name: FieldName | ManagementField
    value: str | int | bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementRequest:
    namespace: Namespace
    endpoint: ManagementEndpoint
    method: Literal['GET', 'POST', 'PUT', 'DELETE']
    parameters: tuple[ManagementParameter, ...]
    requires_independent_command: bool
    # Execution algo services expose collections rather than a single-mother
    # query. The owner must paginate and filter exact original identity locally.
    filter_original_identity: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementCompilation:
    instruction: ManagementInstruction
    target: OwnedTargetSnapshot
    command: ApplicationWrite | None
    requests: tuple[ManagementRequest, ...]
    instruction_hash: str
    target_hash: str
    rules_hash: str
    capabilities_hash: str
    profile_hash: str
    replacement_program_hash: str | None
    required_checks: tuple[str, ...]
    priority_effect: Literal['UNCHANGED', 'RETAINED', 'REQUEUED', 'REPLACED']
    independent_cancel_replace_outcomes: bool

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_released(self) -> Literal[False]:
        return False


_DOMAIN_NAMESPACE = {Namespace.SPOT_ORDINARY: 'ORDINARY', Namespace.SPOT_LIST: 'ORDER_LIST',
    Namespace.USD_M_ORDINARY: 'ORDINARY', Namespace.USD_M_CONDITIONAL: 'CONDITIONAL_ALGO',
    Namespace.SPOT_EXECUTION: 'EXECUTION_ALGO', Namespace.USD_M_EXECUTION: 'EXECUTION_ALGO', Namespace.EQUITY: 'ORDINARY'}
_PRODUCT_NAMESPACES = {
    t.Product.SPOT: (Namespace.SPOT_ORDINARY, Namespace.SPOT_LIST, Namespace.SPOT_EXECUTION),
    t.Product.USD_M_PERP: (Namespace.USD_M_ORDINARY, Namespace.USD_M_CONDITIONAL, Namespace.USD_M_EXECUTION),
    t.Product.US_EQUITY: (Namespace.EQUITY,),
}


def _parameter(name, value):
    return ManagementParameter(name=name, value=value)


def _request(target, endpoint, method, parameters=(), *, filtered=False):
    return ManagementRequest(namespace=target.namespace, endpoint=endpoint, method=method,
        parameters=tuple(parameters), requires_independent_command=method != 'GET', filter_original_identity=filtered)


def _identity(target, *, query=False):
    namespace = target.namespace
    if namespace in (Namespace.SPOT_ORDINARY, Namespace.USD_M_ORDINARY):
        name = ManagementField.ORDER_ID if target.venue_id is not None else ManagementField.ORIGINAL_CLIENT_ID
    elif namespace == Namespace.SPOT_LIST:
        name = ManagementField.ORDER_LIST_ID if target.venue_id is not None else ManagementField.ORIGINAL_CLIENT_ID
    elif namespace in (Namespace.USD_M_CONDITIONAL, Namespace.SPOT_EXECUTION, Namespace.USD_M_EXECUTION):
        name = ManagementField.ALGO_ID if target.venue_id is not None else ManagementField.CLIENT_ALGO_ID
    else:
        name = ManagementField.ORDER_ID if target.venue_id is not None else ManagementField.CLIENT_ORDER_ID
    return _parameter(name, target.venue_id if target.venue_id is not None else target.client_id)


def _query_requests(target, symbol):
    namespace = target.namespace
    symbol_fields = (_parameter(FieldName.SYMBOL, symbol),)
    if namespace in (Namespace.SPOT_EXECUTION, Namespace.USD_M_EXECUTION):
        endpoints = (ManagementEndpoint.SPOT_EXECUTION_OPEN, ManagementEndpoint.SPOT_EXECUTION_HISTORY) if namespace == Namespace.SPOT_EXECUTION else (ManagementEndpoint.USD_M_EXECUTION_OPEN, ManagementEndpoint.USD_M_EXECUTION_HISTORY)
        return tuple(_request(target, endpoint, 'GET', filtered=True) for endpoint in endpoints)
    endpoint = {Namespace.SPOT_ORDINARY: ManagementEndpoint.SPOT_ORDER, Namespace.SPOT_LIST: ManagementEndpoint.SPOT_LIST,
        Namespace.USD_M_ORDINARY: ManagementEndpoint.USD_M_ORDER, Namespace.USD_M_CONDITIONAL: ManagementEndpoint.USD_M_CONDITIONAL,
        Namespace.EQUITY: ManagementEndpoint.EQUITY_QUERY}[namespace]
    params = (*symbol_fields, _identity(target, query=True)) if namespace in (Namespace.SPOT_ORDINARY, Namespace.USD_M_ORDINARY) else (_identity(target, query=True),)
    return (_request(target, endpoint, 'GET', params),)


def _cancel_request(target, symbol):
    namespace = target.namespace
    endpoint = {Namespace.SPOT_ORDINARY: ManagementEndpoint.SPOT_ORDER, Namespace.SPOT_LIST: ManagementEndpoint.SPOT_LIST,
        Namespace.USD_M_ORDINARY: ManagementEndpoint.USD_M_ORDER, Namespace.USD_M_CONDITIONAL: ManagementEndpoint.USD_M_CONDITIONAL,
        Namespace.SPOT_EXECUTION: ManagementEndpoint.SPOT_EXECUTION, Namespace.USD_M_EXECUTION: ManagementEndpoint.USD_M_EXECUTION,
        Namespace.EQUITY: ManagementEndpoint.EQUITY_CANCEL}[namespace]
    params = (_identity(target),)
    if namespace in (Namespace.SPOT_ORDINARY, Namespace.SPOT_LIST, Namespace.USD_M_ORDINARY):
        params = (_parameter(FieldName.SYMBOL, symbol), *params)
    return _request(target, endpoint, 'POST' if namespace == Namespace.EQUITY else 'DELETE', params)


def compile_order_management(instruction: ManagementInstruction, target: OwnedTargetSnapshot, *,
        command: ApplicationWrite | None, scope: t.ScopeKey, now: str, expected_owner_strategy_id: str,
        expected_ownership_epoch: int, expected_execution_epoch: int, expected_policy_revision: int,
        rules: OrderRuleSnapshot, capabilities: OrderCapabilitySnapshot, profile: TrustedProductRules,
        expected_build_digest: str, expected_rule_revision: int, expected_capabilities_hash: str) -> ManagementCompilation:
    require(scope.environment in (t.Environment.PAPER, t.Environment.RESEARCH), 'ENVIRONMENT_DISABLED')
    require(target.scope == rules.scope == capabilities.scope == profile.scope == scope, 'SCOPE_MISMATCH')
    require(target.namespace in _PRODUCT_NAMESPACES[scope.product], 'MANAGEMENT_PRODUCT_NAMESPACE_MISMATCH')
    require(target.owner_strategy_id == expected_owner_strategy_id, 'OWNED_TARGET_OWNER_CHANGED')
    require(target.ownership_epoch == expected_ownership_epoch and target.execution_epoch == expected_execution_epoch
            and target.policy_revision == expected_policy_revision, 'OWNED_TARGET_EPOCH_CHANGED')
    require(rules.revision == capabilities.rule_revision == profile.rule_revision == expected_rule_revision
            and profile.rule_snapshot_ref == rules.snapshot_id, 'RULE_REVISION_CHANGED')
    require(capabilities.adapter_build_digest == profile.adapter_build_digest == expected_build_digest, 'ADAPTER_BUILD_CHANGED')
    require(canonical_hash(domain_payload(capabilities)) == expected_capabilities_hash, 'CAPABILITY_SNAPSHOT_CHANGED')
    for record in (target, rules, capabilities, profile):
        require(utc_timestamp(record.as_of) <= utc_timestamp(now) < utc_timestamp(record.valid_until), 'MANAGEMENT_FACTS_EXPIRED')
    prefix = {t.Product.SPOT: 'SPOT', t.Product.USD_M_PERP: 'USDM', t.Product.US_EQUITY: 'EQUITY'}[scope.product]
    feature = prefix + ('-AMEND' if instruction.kind in (ManagementKind.KEEP_PRIORITY, ManagementKind.MODIFY_LIMIT)
        else '-CANCEL_REPLACE' if instruction.kind == ManagementKind.CANCEL_REPLACE else '-CANCEL_QUERY')
    require(feature in capabilities.capability_ids, 'FEATURE_NOT_AVAILABLE')
    checks = {'EXECUTION_OWNER', 'ADAPTER_QUALIFICATION', 'ORIGINAL_ID_RESPONSE_VERIFICATION'}
    priority = 'UNCHANGED'
    replacement_hash = None
    if instruction.kind == ManagementKind.QUERY:
        require(command is None, 'QUERY_IS_NOT_A_WRITE')
        requests = _query_requests(target, profile.venue_symbol)
        if any(request.filter_original_identity for request in requests):
            checks.add('PAGINATION_AND_EXACT_ORIGINAL_ID_FILTER')
    else:
        require(target.status in ('NEW', 'PARTIALLY_FILLED'), 'TARGET_RECONCILIATION_REQUIRED')
        require(command is not None and target.venue_id is not None, 'INDEPENDENT_MANAGEMENT_COMMAND_REQUIRED')
        require(command.state == 'DURABLE_READY' and command.scope == scope and command.program_id == target.program_id
            and command.canonical_program_hash == target.program_hash and command.target_venue_id == target.venue_id
            and command.owner_strategy_id == expected_owner_strategy_id and command.ownership_epoch == expected_ownership_epoch
            and command.execution_epoch == expected_execution_epoch and command.policy_revision == expected_policy_revision
            and command.service_namespace == _DOMAIN_NAMESPACE[target.namespace], 'MANAGEMENT_COMMAND_BINDING_MISMATCH')
        checks.update(('DURABLE_FULL_MANAGEMENT_PLAN_HASH', 'LOCKED_ACTION_AND_FINANCIAL_VERIFICATION',
            'FRESH_OWNER_EPOCH_SINGLE_USE_PERMIT', 'ACK_IS_NOT_TERMINAL_NO_FUND_RELEASE'))
        if target.parent_list_id is not None:
            checks.add('LINKED_LIST_COLLATERAL_AND_SIBLING_EFFECTS')
        if instruction.kind == ManagementKind.CANCEL:
            require(command.operation == ('CANCEL' if command.service_namespace == 'ORDINARY' else 'CANCEL_PROGRAM'), 'MANAGEMENT_COMMAND_OPERATION_MISMATCH')
            requests = (_cancel_request(target, profile.venue_symbol),)
        else:
            require(command.operation == 'AMEND', 'MANAGEMENT_COMMAND_OPERATION_MISMATCH')
            if instruction.kind == ManagementKind.KEEP_PRIORITY:
                require(target.namespace == Namespace.SPOT_ORDINARY and target.original_quantity is not None,
                    'KEEP_PRIORITY_SPOT_ORDINARY_REQUIRED')
                quantity = quantity_on_grid(Decimal(instruction.new_quantity), Decimal(rules.quantity_step), False)
                require(Decimal(target.cumulative_quantity) < quantity < Decimal(target.original_quantity), 'KEEP_PRIORITY_REDUCTION_REQUIRED')
                requests = (_request(target, ManagementEndpoint.SPOT_KEEP_PRIORITY, 'PUT',
                    (_parameter(FieldName.SYMBOL, profile.venue_symbol), _identity(target),
                     _parameter(ManagementField.NEW_QUANTITY, instruction.new_quantity))),)
                priority = 'RETAINED'
            elif instruction.kind == ManagementKind.MODIFY_LIMIT:
                require(target.namespace == Namespace.USD_M_ORDINARY and target.order is not None
                    and target.order.price.kind == 'FIXED_LIMIT' and target.order.trigger.kind == 'NONE', 'MODIFY_ORDINARY_LIMIT_REQUIRED')
                quantity = quantity_on_grid(Decimal(instruction.new_quantity), Decimal(rules.quantity_step), False)
                price = quantity_on_grid(Decimal(instruction.new_price), Decimal(rules.tick_size), False)
                _, multiplier = _quantity_terms(profile, rules)
                require(profile.quantity.contains(quantity) and profile.price.contains(price), 'MODIFY_RULE_OUT_OF_RANGE')
                with localcontext() as ctx:
                    ctx.prec = 256
                    notional = quantity * price * Decimal(multiplier)
                require(profile.notional.contains(notional), 'MODIFY_NOTIONAL_OUT_OF_RANGE')
                require(target.cumulative_quantity is not None and quantity > Decimal(target.cumulative_quantity), 'MODIFY_WOULD_CANCEL_FILLED_QUANTITY')
                requests = (_request(target, ManagementEndpoint.USD_M_ORDER, 'PUT',
                    (_parameter(FieldName.SYMBOL, profile.venue_symbol), _identity(target),
                     _parameter(FieldName.SIDE, target.order.side), _parameter(FieldName.QUANTITY, instruction.new_quantity),
                     _parameter(FieldName.PRICE, instruction.new_price))),)
                priority = 'REQUEUED'
                checks.add('MODIFY_RACE_AND_QUEUE_REORDER')
            else:
                replacement = instruction.replacement
                require(utc_timestamp(now) < utc_timestamp(replacement.original_proposal.valid_until)
                    and utc_timestamp(now) < utc_timestamp(replacement.original_program.submit_valid_until), 'REPLACEMENT_SUBMISSION_EXPIRED')
                require(target.namespace == Namespace.SPOT_ORDINARY and replacement.scope == scope
                    and replacement.original_program.template == 'ATOMIC' and len(replacement.requests) == 1
                    and replacement.requests[0].namespace == Namespace.SPOT_ORDINARY,
                    'CANCEL_REPLACE_ATOMIC_SPOT_REQUIRED')
                require(replacement.rules_hash == canonical_hash(domain_payload(rules))
                    and replacement.profile_hash == canonical_hash(domain_payload(profile))
                    and replacement.adapter_build_digest == expected_build_digest
                    and replacement.capabilities_hash == expected_capabilities_hash, 'REPLACEMENT_RULES_CHANGED')
                identifier(command.venue_client_id)
                params = tuple(_parameter(param.name, param.value) for param in replacement.requests[0].parameters)
                params += (_parameter(ManagementField.CANCEL_ORDER_ID, target.venue_id),
                    _parameter(ManagementField.NEW_CLIENT_ID, command.venue_client_id),
                    _parameter(ManagementField.CANCEL_REPLACE_MODE, instruction.cancel_replace_mode),
                    _parameter(ManagementField.ORDER_RATE_LIMIT_MODE, instruction.order_rate_limit_mode))
                requests = (_request(target, ManagementEndpoint.SPOT_CANCEL_REPLACE, 'POST', params),)
                replacement_hash = replacement.program_hash
                priority = 'REPLACED'
                checks.update(('INDEPENDENT_CANCEL_AND_NEW_RESULTS', 'NOT_ATTEMPTED_STILL_COUNTS', 'UNKNOWN_QUERY_ORIGINAL_IDS'))
    return ManagementCompilation(instruction=instruction, target=target, command=command, requests=requests,
        instruction_hash=canonical_hash(domain_payload(instruction)), target_hash=canonical_hash(domain_payload(target)),
        rules_hash=canonical_hash(domain_payload(rules)), capabilities_hash=expected_capabilities_hash,
        profile_hash=canonical_hash(domain_payload(profile)), replacement_program_hash=replacement_hash,
        required_checks=tuple(sorted(checks)), priority_effect=priority,
        independent_cancel_replace_outcomes=instruction.kind == ManagementKind.CANCEL_REPLACE)
