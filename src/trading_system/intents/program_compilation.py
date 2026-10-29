"""Lossless offline program compilation for the single execution owner.

This is a request *plan*, not a delta, funding approval, command or permit.
Every original typed field and hash remains available. Deferred facts are
explicit; native generated children are observations and are never resubmitted.
There is no network transport, credential, signer or arbitrary route here.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import OrderNode, OrderProgram, OrderProposal, QuantitySpec
from trading_system.contracts.order_program_v3 import OrderProgramV3, OrderProposalV3
from trading_system.contracts.order_snapshots import OrderCapabilitySnapshot, OrderRuleSnapshot
from .order_features import requested_order_features
from .product_rules import PositionMode, TrustedProductRules
from .semantic_validation import quantity_from_notional, quantity_on_grid, require


class Namespace(StrEnum):
    SPOT_ORDINARY = 'BINANCE_SPOT_ORDINARY'
    SPOT_LIST = 'BINANCE_SPOT_ORDER_LIST'
    USD_M_ORDINARY = 'BINANCE_USD_M_ORDINARY'
    USD_M_CONDITIONAL = 'BINANCE_USD_M_CONDITIONAL_ALGO'
    SPOT_EXECUTION = 'BINANCE_SPOT_EXECUTION_ALGO'
    USD_M_EXECUTION = 'BINANCE_USD_M_EXECUTION_ALGO'
    EQUITY = 'BINANCE_REAL_EQUITY'


class Operation(StrEnum):
    ORDER = 'SUBMIT_ORDER'
    SOR = 'SUBMIT_SOR_ORDER'
    CONDITIONAL = 'SUBMIT_CONDITIONAL'
    OCO = 'SUBMIT_OCO'
    OTO = 'SUBMIT_OTO'
    OTOCO = 'SUBMIT_OTOCO'
    OPO = 'SUBMIT_OPO'
    OPOCO = 'SUBMIT_OPOCO'
    BATCH = 'SUBMIT_BATCH'
    TWAP = 'SUBMIT_TWAP'
    VP = 'SUBMIT_VP'
    LOCAL_ALGORITHM = 'SCHEDULE_LOCAL_CHILDREN'


class Endpoint(StrEnum):
    """Static owner-client API identifiers, never caller supplied URLs."""
    SPOT_ORDER = '/api/v3/order'
    SPOT_SOR = '/api/v3/sor/order'
    SPOT_OCO = '/api/v3/orderList/oco'
    SPOT_OTO = '/api/v3/orderList/oto'
    SPOT_OTOCO = '/api/v3/orderList/otoco'
    SPOT_OPO = '/api/v3/orderList/opo'
    SPOT_OPOCO = '/api/v3/orderList/opoco'
    USD_M_ORDER = '/fapi/v1/order'
    USD_M_CONDITIONAL = '/fapi/v1/algoOrder'
    USD_M_BATCH = '/fapi/v1/batchOrders'
    SPOT_TWAP = '/sapi/v1/algo/spot/newOrderTwap'
    SPOT_VP = '/sapi/v1/algo/spot/newOrderVp'
    USD_M_TWAP = '/sapi/v1/algo/futures/newOrderTwap'
    USD_M_VP = '/sapi/v1/algo/futures/newOrderVp'
    EQUITY_ORDER = '/sapi/v1/equity/order/place'


class Timing(StrEnum):
    INITIAL = 'INITIAL_SUBMISSION'
    AFTER_FULL_FILL = 'AFTER_FULL_FILL'
    AFTER_NET_RECEIPT = 'AFTER_FULL_FILL_AND_NET_RECEIPT'
    ALGORITHM_CHILD = 'STAGED_ALGORITHM_CHILD'


class FieldName(StrEnum):
    SYMBOL = 'symbol'
    SIDE = 'side'
    TYPE = 'type'
    QUANTITY = 'quantity'
    QUOTE_ORDER_QTY = 'quoteOrderQty'
    NOTIONAL = 'notional'
    PRICE = 'price'
    STOP_PRICE = 'stopPrice'
    TRIGGER_PRICE = 'triggerPrice'
    ACTIVATE_PRICE = 'activatePrice'
    TRAILING_DELTA = 'trailingDelta'
    CALLBACK_RATE = 'callbackRate'
    WORKING_TYPE = 'workingType'
    PRICE_PROTECT = 'priceProtect'
    ALGO_TYPE = 'algoType'
    PEG_PRICE_TYPE = 'pegPriceType'
    PEG_OFFSET_VALUE = 'pegOffsetValue'
    PEG_OFFSET_TYPE = 'pegOffsetType'
    PRICE_MATCH = 'priceMatch'
    TIME_IN_FORCE = 'timeInForce'
    GOOD_TILL_DATE = 'goodTillDate'
    ICEBERG_QTY = 'icebergQty'
    STP = 'selfTradePreventionMode'
    POSITION_SIDE = 'positionSide'
    REDUCE_ONLY = 'reduceOnly'
    CLOSE_POSITION = 'closePosition'
    TRADING_SESSION = 'tradingSession'
    TOKENIZE = 'tokenize'
    DURATION = 'duration'
    URGENCY = 'urgency'
    VP_TIME_LIMIT = 'vpTimeLimit'
    LIMIT_PRICE = 'limitPrice'


class Leg(StrEnum):
    NONE = 'NONE'
    WORKING = 'WORKING'
    PENDING = 'PENDING'
    ABOVE = 'ABOVE'
    BELOW = 'BELOW'
    PENDING_ABOVE = 'PENDING_ABOVE'
    PENDING_BELOW = 'PENDING_BELOW'


class RequiredCheck(StrEnum):
    LOCKED_TARGET_DELTA = 'LOCKED_TARGET_DELTA'
    FINANCIAL_ADMISSION = 'FINANCIAL_ADMISSION'
    OWNER_COMMAND_PERMIT = 'OWNER_COMMAND_PERMIT'
    ADAPTER_QUALIFICATION = 'ADAPTER_QUALIFICATION'
    CURRENT_MARKET_RULES = 'CURRENT_MARKET_RULES'
    REAL_POSITION_REDUCTION = 'REAL_POSITION_REDUCTION'
    FULL_FILL_NET_RECEIPT = 'FULL_FILL_NET_RECEIPT'
    PARTIAL_FILL_PROTECTION = 'PARTIAL_FILL_PROTECTION'
    LOCAL_MUTUAL_EXCLUSION_RACE = 'LOCAL_MUTUAL_EXCLUSION_RACE'
    LOCAL_ALGORITHM_CHILD_ADMISSION = 'LOCAL_ALGORITHM_CHILD_ADMISSION'
    ALGORITHM_NOTIONAL = 'ALGORITHM_NOTIONAL'
    EQUITY_MARKET_CALENDAR = 'EQUITY_MARKET_CALENDAR'
    TRIGGER_PROTECT_SPREAD = 'TRIGGER_PROTECT_SPREAD'
    EXECUTION_PRICE_ENVELOPE = 'EXECUTION_PRICE_ENVELOPE_ENFORCEMENT'


@dataclass(frozen=True, slots=True, kw_only=True)
class Parameter:
    name: FieldName
    value: str | int | bool
    leg: Leg = Leg.NONE

    @property
    def wire_name(self) -> str:
        """Closed field-name mapping; no proposal can provide a field/path."""
        if self.leg == Leg.NONE:
            return self.name.value
        require(self.name in _LIST_FIELDS, 'LIST_FIELD_UNMAPPABLE')
        prefix = {Leg.WORKING: 'working', Leg.PENDING: 'pending', Leg.ABOVE: 'above',
            Leg.BELOW: 'below', Leg.PENDING_ABOVE: 'pendingAbove', Leg.PENDING_BELOW: 'pendingBelow'}[self.leg]
        suffix = self.name.value[0].upper() + self.name.value[1:]
        return prefix + suffix


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledNode:
    original: OrderNode
    order_type: str
    quantity: QuantitySpec
    parameters: tuple[Parameter, ...]
    namespace: Namespace
    trusted_origin: Literal['APPLICATION_GENERATED_CHILD', 'VENUE_GENERATED_CHILD']
    worst_case_quote_notional: str | None
    sizing_price_bound: str | None
    leg: Leg = Leg.NONE
    activation_requires_full_fill: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestPlan:
    namespace: Namespace
    operation: Operation
    node_ids: tuple[str, ...]
    parameters: tuple[Parameter, ...] = ()
    timing: Timing = Timing.INITIAL
    native_mother: bool = False
    generated_child_origin: Literal['APPLICATION_GENERATED_CHILD', 'VENUE_GENERATED_CHILD'] | None = None
    requires_independent_command: bool = True

    @property
    def endpoint(self) -> Endpoint | None:
        if self.operation == Operation.LOCAL_ALGORITHM:
            return None
        routes = {
            (Namespace.SPOT_ORDINARY, Operation.ORDER): Endpoint.SPOT_ORDER,
            (Namespace.SPOT_ORDINARY, Operation.SOR): Endpoint.SPOT_SOR,
            (Namespace.USD_M_ORDINARY, Operation.ORDER): Endpoint.USD_M_ORDER,
            (Namespace.USD_M_ORDINARY, Operation.BATCH): Endpoint.USD_M_BATCH,
            (Namespace.USD_M_CONDITIONAL, Operation.CONDITIONAL): Endpoint.USD_M_CONDITIONAL,
            (Namespace.EQUITY, Operation.ORDER): Endpoint.EQUITY_ORDER,
            (Namespace.SPOT_EXECUTION, Operation.TWAP): Endpoint.SPOT_TWAP,
            (Namespace.SPOT_EXECUTION, Operation.VP): Endpoint.SPOT_VP,
            (Namespace.USD_M_EXECUTION, Operation.TWAP): Endpoint.USD_M_TWAP,
            (Namespace.USD_M_EXECUTION, Operation.VP): Endpoint.USD_M_VP,
            (Namespace.SPOT_LIST, Operation.OCO): Endpoint.SPOT_OCO,
            (Namespace.SPOT_LIST, Operation.OTO): Endpoint.SPOT_OTO,
            (Namespace.SPOT_LIST, Operation.OTOCO): Endpoint.SPOT_OTOCO,
            (Namespace.SPOT_LIST, Operation.OPO): Endpoint.SPOT_OPO,
            (Namespace.SPOT_LIST, Operation.OPOCO): Endpoint.SPOT_OPOCO,
        }
        require((self.namespace, self.operation) in routes, 'NAMESPACE_OPERATION_UNMAPPABLE')
        return routes[self.namespace, self.operation]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramCompilation:
    original_proposal: OrderProposal | OrderProposalV3
    original_program: OrderProgram | OrderProgramV3
    scope: t.ScopeKey
    nodes: tuple[CompiledNode, ...]
    requests: tuple[RequestPlan, ...]
    requested_features: tuple[str, ...]
    required_checks: tuple[RequiredCheck, ...]
    proposal_hash: str
    program_hash: str
    rules_hash: str
    capabilities_hash: str
    profile_hash: str
    rule_revision: int
    adapter_build_digest: str
    capability_qualification: str
    @property
    def execution_authorized(self) -> Literal[False]:
        return False


LISTS = frozenset(('OCO', 'OTO', 'OTOCO', 'OPO', 'OPOCO'))
ALGORITHMS = frozenset(('TWAP', 'VP'))
_LIST_FIELDS = frozenset((FieldName.SIDE, FieldName.TYPE, FieldName.QUANTITY,
    FieldName.PRICE, FieldName.STOP_PRICE, FieldName.TRAILING_DELTA,
    FieldName.TIME_IN_FORCE, FieldName.ICEBERG_QTY, FieldName.PEG_PRICE_TYPE,
    FieldName.PEG_OFFSET_TYPE, FieldName.PEG_OFFSET_VALUE))


def _p(name: FieldName, value: str | int | bool) -> Parameter:
    return Parameter(name=name, value=value)


def _order_type(node: OrderNode, product: t.Product) -> str:
    o = node.order
    limit = o.price.kind != 'MARKET'
    if product == t.Product.USD_M_PERP:
        if o.trigger.kind == 'TRAILING':
            return 'TRAILING_STOP_MARKET'
        if o.trigger.kind == 'STATIC':
            return ('STOP' if o.trigger.purpose == 'STOP_LOSS' else 'TAKE_PROFIT') + ('' if limit else '_MARKET')
    elif product == t.Product.SPOT:
        if o.trigger.kind != 'NONE':
            return o.trigger.purpose + ('_LIMIT' if limit else '')
        if o.post_only:
            return 'LIMIT_MAKER'
    return 'LIMIT' if limit else 'MARKET'


def _namespace(product: t.Product, triggered: bool) -> Namespace:
    if product == t.Product.SPOT:
        return Namespace.SPOT_ORDINARY
    if product == t.Product.US_EQUITY:
        return Namespace.EQUITY
    return Namespace.USD_M_CONDITIONAL if triggered else Namespace.USD_M_ORDINARY


def _validate_bindings(proposal, rules, capabilities, profile, scope, now,
                       expected_build_digest, expected_rule_revision, expected_capabilities_hash,
                       expected_instrument_ref):
    codec = VersionedOrderCodec()
    codec.encode('order-proposal', proposal)
    codec.encode('order-rule-snapshot', rules)
    codec.encode('order-capability-snapshot', capabilities)
    require(scope.environment in (t.Environment.PAPER, t.Environment.RESEARCH), 'ENVIRONMENT_DISABLED')
    require(proposal.instrument_ref == expected_instrument_ref == scope.instrument_uid, 'INSTRUMENT_REFERENCE_MISMATCH')
    require(rules.scope == capabilities.scope == profile.scope == scope, 'SCOPE_MISMATCH')
    require(capabilities.adapter_build_digest == expected_build_digest, 'ADAPTER_BUILD_CHANGED')
    require(profile.adapter_build_digest == expected_build_digest, 'PRODUCT_RULE_BUILD_CHANGED')
    require(rules.revision == expected_rule_revision == capabilities.rule_revision, 'RULE_REVISION_CHANGED')
    require(profile.rule_revision == rules.revision, 'PRODUCT_RULE_REVISION_CHANGED')
    require(canonical_hash(domain_payload(capabilities)) == expected_capabilities_hash, 'CAPABILITY_SNAPSHOT_CHANGED')
    for snapshot, code in ((rules, 'RULES_EXPIRED'), (capabilities, 'CAPABILITIES_EXPIRED'), (profile, 'PROFILE_EXPIRED')):
        require(utc_timestamp(snapshot.as_of) <= utc_timestamp(now) < utc_timestamp(snapshot.valid_until), code)
    require(utc_timestamp(now) < utc_timestamp(proposal.valid_until), 'PROPOSAL_EXPIRED')
    program = proposal.program
    require(program is not None and proposal.action == 'SET_TARGET', 'EXECUTABLE_TARGET_REQUIRED')
    require(program.rule_snapshot_ref == rules.snapshot_id == profile.rule_snapshot_ref
            and program.capability_snapshot_ref == capabilities.snapshot_id, 'SNAPSHOT_REFERENCE_MISMATCH')
    require(utc_timestamp(now) < utc_timestamp(program.submit_valid_until), 'SUBMISSION_EXPIRED')
    require(utc_timestamp(now) < utc_timestamp(proposal.exit_plan.review_due_at), 'EXIT_REVIEW_EXPIRED')
    require(proposal.exit_plan.maximum_holding_seconds > 0, 'EXPLICIT_HOLDING_LIMIT_REQUIRED')
    return codec, program


def _quantity(node, rules, profile, allow_round_down):
    o = node.order
    q = o.quantity
    if q.kind in ('CLOSE_ALL', 'DERIVED_RECEIPT'):
        return q, ()
    value = Decimal(q.value)
    if q.kind == 'QUOTE_NOTIONAL' and o.price.kind == 'MARKET':
        if profile.scope.product == t.Product.SPOT:
            require(o.trigger.kind == 'NONE' and profile.spot.quote_market_quantity, 'QUOTE_ORDER_QTY_UNSUPPORTED')
            param = FieldName.QUOTE_ORDER_QTY
        elif profile.scope.product == t.Product.US_EQUITY:
            require(o.side == 'BUY', 'EQUITY_MARKET_QUANTITY_COMBINATION')
            param = FieldName.NOTIONAL
        else:
            require(False, 'QUOTE_SIZING_REQUIRES_FIXED_LIMIT')
        require(profile.notional.contains(value) and value >= Decimal(rules.minimum_notional), 'NOTIONAL_OUT_OF_RANGE')
        return q, (_p(param, q.value),)
    is_market = o.price.kind == 'MARKET'
    step = Decimal(profile.market_quantity_step if is_market else rules.quantity_step)
    if q.kind == 'QUOTE_NOTIONAL':
        require(o.price.kind == 'FIXED_LIMIT', 'QUOTE_SIZING_REQUIRES_FIXED_LIMIT')
        value = quantity_from_notional(value, Decimal(o.price.value), step, allow_round_down)
    else:
        value = quantity_on_grid(value, step, allow_round_down)
    bounds = profile.market_quantity if is_market else profile.quantity
    require(bounds.contains(value) and value >= Decimal(rules.minimum_quantity), 'QUANTITY_OUT_OF_RANGE')
    if o.price.kind == 'FIXED_LIMIT':
        with localcontext() as ctx:
            ctx.prec = 256
            notional = value * Decimal(o.price.value)
        require(profile.notional.contains(notional) and notional >= Decimal(rules.minimum_notional), 'NOTIONAL_OUT_OF_RANGE')
    return QuantitySpec(kind='BASE_QUANTITY', value=format(value, 'f'), source_node=None), (_p(FieldName.QUANTITY, format(value, 'f')),)


def _validate_price(value, rules, profile):
    if value is not None:
        quantity_on_grid(Decimal(value), Decimal(rules.tick_size), False)
        require(profile.price.contains(Decimal(value)), 'PRICE_OUT_OF_RANGE')


def _compile_node(node, program, rules, profile, allow_round_down, now):
    o = node.order
    product = profile.scope.product
    order_type = _order_type(node, product)
    require(order_type in profile.allowed_order_types, 'SYMBOL_ORDER_TYPE_UNSUPPORTED')
    quantity, quantity_fields = _quantity(node, rules, profile, allow_round_down)
    parameters = [_p(FieldName.SIDE, o.side), _p(FieldName.TYPE, order_type), *quantity_fields]
    for value in (o.price.value, o.trigger.price, o.trigger.activation_price):
        _validate_price(value, rules, profile)
    if o.price.kind == 'FIXED_LIMIT':
        parameters.append(_p(FieldName.PRICE, o.price.value))
    if o.tif.kind != 'NONE':
        require(o.tif.kind in rules.allowed_tifs, 'INVALID_TIF')
        parameters.append(_p(FieldName.TIME_IN_FORCE, o.tif.kind))
    if product == t.Product.SPOT:
        _spot_fields(o, quantity, order_type, profile, rules, parameters)
    elif product == t.Product.USD_M_PERP:
        _usdm_fields(o, order_type, profile, rules, parameters, now)
    else:
        _equity_fields(o, quantity, profile, parameters)
    worst_notional = sizing_bound = None
    if quantity.kind == 'BASE_QUANTITY':
        if o.price.kind == 'FIXED_LIMIT':
            bound = o.price.value
        else:
            require(profile.execution_price_envelope is not None, 'BOUNDED_EXECUTION_PRICE_REQUIRED')
            bound = profile.execution_price_envelope.maximum
        sizing_bound = bound
        with localcontext() as ctx:
            ctx.prec = 256
            exposure = Decimal(quantity.value) * Decimal(bound)
        require(profile.notional.contains(exposure), 'WORST_CASE_NOTIONAL_OUT_OF_RANGE')
        worst_notional = format(exposure, 'f')
    elif quantity.kind == 'QUOTE_NOTIONAL':
        # quoteOrderQty BUY and equity notional BUY have an explicit quote
        # principal budget; fees remain a separate financial-admission input.
        # SELL quoteOrderQty still needs a bounded price to budget base assets.
        if o.side == 'SELL':
            require(profile.execution_price_envelope is not None, 'BOUNDED_EXECUTION_PRICE_REQUIRED')
            sizing_bound = profile.execution_price_envelope.minimum
        worst_notional = quantity.value
    native_child = program.execution_mode == 'VENUE_NATIVE' and program.template in LISTS
    # Model hints are deliberately not consulted when deriving trusted origin.
    return CompiledNode(original=node, order_type=order_type, quantity=quantity,
        parameters=tuple(parameters), namespace=_namespace(product, o.trigger.kind != 'NONE'),
        trusted_origin='VENUE_GENERATED_CHILD' if native_child else 'APPLICATION_GENERATED_CHILD',
        worst_case_quote_notional=worst_notional, sizing_price_bound=sizing_bound,
        activation_requires_full_fill=any(link.target == node.node_id for link in program.links))


def _spot_fields(o, quantity, order_type, profile, rules, parameters):
    s = profile.spot
    require(o.position_side == 'BOTH' and profile.scope.venue_leg == 'BOTH'
            and not o.reduce_only and o.quantity.kind != 'CLOSE_ALL', 'CASH_POSITION_SEMANTICS_REQUIRED')
    require(o.trading_session is None and o.tokenize is None, 'PRODUCT_MODIFIER_UNSUPPORTED')
    require(o.stp in s.allowed_stp, 'STP_MODE_UNAVAILABLE')
    require(o.price.kind != 'PRICE_MATCH', 'PRICE_MATCH_PRODUCT_UNSUPPORTED')
    if o.trigger.kind != 'NONE':
        require(o.trigger.source == 'LAST', 'SPOT_TRIGGER_SOURCE_UNSUPPORTED')
        require(o.trigger.price_protect is False and not o.post_only, 'SPOT_TRIGGER_MODIFIER_UNSUPPORTED')
        if o.trigger.kind == 'STATIC':
            parameters.append(_p(FieldName.STOP_PRICE, o.trigger.price))
        else:
            require(o.trigger.offset_unit == 'BIPS', 'SPOT_TRAILING_REQUIRES_BIPS')
            value = Decimal(o.trigger.offset)
            require(value == value.to_integral_value(), 'TRAILING_BIPS_MUST_BE_INTEGER')
            above = (o.side == 'BUY') == (o.trigger.purpose == 'STOP_LOSS')
            limits = s.trailing_above_bips if above else s.trailing_below_bips
            require(limits is not None and limits.contains(value), 'TRAILING_BIPS_OUT_OF_RANGE')
            parameters.append(_p(FieldName.TRAILING_DELTA, int(value)))
            if o.trigger.activation_price is not None:
                parameters.append(_p(FieldName.STOP_PRICE, o.trigger.activation_price))
    if order_type in ('LIMIT', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT_LIMIT'):
        require(o.tif.kind in ('GTC', 'IOC', 'FOK'), 'SPOT_LIMIT_TIF_REQUIRED')
    elif order_type == 'LIMIT_MAKER':
        require(o.tif.kind == 'NONE', 'LIMIT_MAKER_TIF_FORBIDDEN')
    else:
        require(o.tif.kind == 'NONE', 'MARKET_TIF_FORBIDDEN')
    if o.price.kind == 'PEG':
        require(o.price.offset_levels <= s.maximum_peg_levels, 'PEG_OFFSET_OUT_OF_RANGE')
        require(not o.post_only or o.price.peg_type == 'PRIMARY_PEG', 'LIMIT_MAKER_MARKET_PEG_FORBIDDEN')
        require(order_type != 'LIMIT' or o.price.peg_type != 'PRIMARY_PEG'
                or o.tif.kind == 'GTC', 'PRIMARY_PEG_REQUIRES_GTC')
        parameters.extend((_p(FieldName.PEG_PRICE_TYPE, o.price.peg_type),
            _p(FieldName.PEG_OFFSET_TYPE, 'PRICE_LEVEL'), _p(FieldName.PEG_OFFSET_VALUE, o.price.offset_levels)))
    if o.iceberg_quantity is not None:
        require(order_type in ('LIMIT', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT_LIMIT', 'LIMIT_MAKER')
                and o.tif.kind in ('GTC', 'NONE') and quantity.kind == 'BASE_QUANTITY', 'ICEBERG_COMBINATION_UNSUPPORTED')
        iceberg = quantity_on_grid(Decimal(o.iceberg_quantity), Decimal(rules.quantity_step), False)
        total = Decimal(quantity.value)
        require(iceberg <= total and s.maximum_iceberg_parts is not None, 'ICEBERG_RULES_REQUIRED')
        # Compare exact integer ratios, independent of ambient Decimal precision.
        tn, td = total.as_integer_ratio(); inn, ind = iceberg.as_integer_ratio()
        require(tn * ind <= s.maximum_iceberg_parts * inn * td, 'ICEBERG_PARTS_EXCEEDED')
        parameters.append(_p(FieldName.ICEBERG_QTY, o.iceberg_quantity))
    if o.routing == 'SOR':
        require(s.sor_enabled and order_type in ('LIMIT', 'MARKET') and o.quantity.kind == 'BASE_QUANTITY'
                and o.price.kind != 'PEG', 'SOR_COMBINATION_UNSUPPORTED')
    parameters.append(_p(FieldName.STP, o.stp))


def _usdm_fields(o, order_type, profile, rules, parameters, now):
    u = profile.usd_m
    require(o.price.kind != 'PEG' and o.iceberg_quantity is None and o.routing == 'STANDARD'
            and o.trading_session is None and o.tokenize is None, 'PRODUCT_MODIFIER_UNSUPPORTED')
    require(o.stp in u.allowed_stp, 'STP_MODE_UNAVAILABLE')
    require(o.stp == 'NONE' or o.tif.kind in ('NONE', 'GTC', 'IOC', 'GTD'), 'STP_TIF_INEFFECTIVE')
    if u.position_mode == PositionMode.HEDGE:
        require(o.position_side in ('LONG', 'SHORT') and o.position_side == profile.scope.venue_leg, 'HEDGE_LEG_REQUIRED')
        require(not o.reduce_only, 'HEDGE_REDUCE_ONLY_FORBIDDEN')
    else:
        require(o.position_side == 'BOTH' and profile.scope.venue_leg == 'BOTH', 'ONE_WAY_LEG_REQUIRED')
    parameters.append(_p(FieldName.POSITION_SIDE, o.position_side))
    close = o.quantity.kind == 'CLOSE_ALL'
    if close:
        require(order_type in ('STOP_MARKET', 'TAKE_PROFIT_MARKET') and not o.reduce_only, 'CLOSE_ALL_COMBINATION_UNSUPPORTED')
        require(not (u.position_mode == PositionMode.HEDGE and ((o.side == 'BUY' and o.position_side == 'LONG')
            or (o.side == 'SELL' and o.position_side == 'SHORT'))), 'CLOSE_ALL_HEDGE_DIRECTION')
        parameters.append(_p(FieldName.CLOSE_POSITION, True))
    elif u.position_mode == PositionMode.ONE_WAY:
        parameters.append(_p(FieldName.REDUCE_ONLY, o.reduce_only))
    require(o.quantity.kind != 'DERIVED_RECEIPT', 'DERIVED_RECEIPT_PRODUCT_UNSUPPORTED')
    if o.price.kind == 'PRICE_MATCH':
        require(o.price.price_match in u.allowed_price_match, 'PRICE_MATCH_UNAVAILABLE')
        parameters.append(_p(FieldName.PRICE_MATCH, o.price.price_match))
    limit = order_type in ('LIMIT', 'STOP', 'TAKE_PROFIT')
    if limit:
        require(o.tif.kind in ('GTC', 'IOC', 'FOK', 'GTX', 'GTD', 'RPI'), 'USDM_LIMIT_TIF_REQUIRED')
        require(not o.post_only or o.tif.kind == 'GTX', 'POST_ONLY_REQUIRES_GTX')
    else:
        require(o.tif.kind == 'NONE' and not o.post_only, 'MARKET_TIF_FORBIDDEN')
    if o.tif.kind == 'RPI':
        require(u.rpi_eligible, 'RPI_ACCOUNT_NOT_ELIGIBLE')
    if o.tif.kind == 'GTD':
        expiry = utc_timestamp(o.tif.expires_at)
        require(expiry.timestamp() == int(expiry.timestamp()), 'GTD_REQUIRES_WHOLE_SECONDS')
        require(expiry.timestamp() > utc_timestamp(now).timestamp() + 600
                and int(expiry.timestamp()) * 1000 < 253402300799000, 'INVALID_GTD_DEADLINE')
        parameters.append(_p(FieldName.GOOD_TILL_DATE, int(expiry.timestamp()) * 1000))
    if o.trigger.kind != 'NONE':
        require(o.trigger.source in ('CONTRACT_PRICE', 'MARK_PRICE'), 'USDM_TRIGGER_SOURCE_UNSUPPORTED')
        parameters.extend((_p(FieldName.ALGO_TYPE, 'CONDITIONAL'), _p(FieldName.WORKING_TYPE, o.trigger.source)))
        if o.trigger.kind == 'STATIC':
            parameters.extend((_p(FieldName.TRIGGER_PRICE, o.trigger.price), _p(FieldName.PRICE_PROTECT, o.trigger.price_protect)))
        else:
            require(o.trigger.offset_unit == 'PERCENT' and o.trigger.purpose == 'STOP_LOSS'
                    and o.price.kind == 'MARKET' and not o.trigger.price_protect, 'USDM_TRAILING_COMBINATION_UNSUPPORTED')
            callback = Decimal(o.trigger.offset)
            require(u.callback_percent is not None and u.callback_percent.contains(callback)
                    and rules.maximum_callback_percent is not None
                    and callback <= Decimal(rules.maximum_callback_percent), 'CALLBACK_RATE_OUT_OF_RANGE')
            parameters.append(_p(FieldName.CALLBACK_RATE, o.trigger.offset))
            if o.trigger.activation_price is not None:
                parameters.append(_p(FieldName.ACTIVATE_PRICE, o.trigger.activation_price))
    parameters.append(_p(FieldName.STP, o.stp))


def _equity_fields(o, quantity, profile, parameters):
    require(o.tokenize is False, 'REAL_EQUITY_REQUIRED')
    require(o.trigger.kind == 'NONE' and o.price.kind in ('MARKET', 'FIXED_LIMIT')
            and not o.post_only and o.iceberg_quantity is None and o.stp == 'NONE' and o.routing == 'STANDARD', 'EQUITY_MODIFIER_UNSUPPORTED')
    require(o.position_side == 'BOTH' and profile.scope.venue_leg == 'BOTH' and not o.reduce_only, 'CASH_POSITION_SEMANTICS_REQUIRED')
    require(o.tif.kind in ('DAY', 'GTC'), 'EQUITY_TIF_REQUIRED')
    if o.price.kind == 'MARKET':
        require(o.trading_session is None and o.tif.kind == 'DAY', 'EQUITY_MARKET_SESSION_FORBIDDEN')
        require((o.side == 'BUY' and quantity.kind == 'QUOTE_NOTIONAL')
            or (o.side == 'SELL' and quantity.kind == 'BASE_QUANTITY'), 'EQUITY_MARKET_QUANTITY_COMBINATION')
    else:
        require(o.trading_session in profile.equity.allowed_sessions, 'EQUITY_SESSION_UNAVAILABLE')
        require(quantity.kind == 'BASE_QUANTITY', 'EQUITY_LIMIT_QUANTITY_REQUIRED')
        if Decimal(quantity.value) != Decimal(quantity.value).to_integral_value() and o.tif.kind == 'GTC':
            require(o.trading_session in ('EXTENDED', '24H'), 'FRACTIONAL_GTC_SESSION')
        parameters.append(_p(FieldName.TRADING_SESSION, o.trading_session))
    parameters.append(_p(FieldName.TOKENIZE, False))


def _list_legs(program, nodes):
    by_id = {node.original.node_id: node for node in nodes}
    for node in nodes:
        require(node.original.order.routing == 'STANDARD', 'SOR_LIST_UNSUPPORTED')
    if program.template == 'OCO':
        pair = nodes
    else:
        working = next(node for node in nodes if node.original.role == 'WORKING')
        require(working.order_type in ('LIMIT', 'LIMIT_MAKER'), 'LIST_WORKING_TYPE_UNSUPPORTED')
        by_id[working.original.node_id] = replace(working, leg=Leg.WORKING)
        pair = tuple(node for node in nodes if node.original.role == 'PENDING')
        if program.template in ('OTO', 'OPO'):
            require(not (pair[0].order_type == 'MARKET' and pair[0].quantity.kind == 'QUOTE_NOTIONAL'), 'LIST_PENDING_QUOTE_MARKET_UNSUPPORTED')
            by_id[pair[0].original.node_id] = replace(pair[0], leg=Leg.PENDING)
            return tuple(by_id[node.original.node_id] for node in nodes)
    require(len(pair) == 2 and pair[0].original.order.side == pair[1].original.order.side, 'OCO_SIDE_MISMATCH')
    require(pair[0].quantity == pair[1].quantity, 'OCO_QUANTITY_MISMATCH')
    stop = tuple(node for node in pair if node.order_type in ('STOP_LOSS', 'STOP_LOSS_LIMIT'))
    other = tuple(node for node in pair if node.order_type in ('LIMIT_MAKER', 'TAKE_PROFIT', 'TAKE_PROFIT_LIMIT'))
    require(len(stop) == len(other) == 1, 'OCO_LEG_TYPES_UNSUPPORTED')
    stop_above = pair[0].original.order.side == 'BUY'
    for node in pair:
        above = (node == stop[0]) == stop_above
        leg = (Leg.ABOVE if above else Leg.BELOW) if program.template == 'OCO' else (Leg.PENDING_ABOVE if above else Leg.PENDING_BELOW)
        by_id[node.original.node_id] = replace(node, leg=leg)
    return tuple(by_id[node.original.node_id] for node in nodes)


def _algorithm_requests(program, nodes, profile):
    algo = program.algorithm
    rules = profile.algorithm
    require(rules is not None, 'ALGORITHM_RULES_REQUIRED')
    duration = algo.duration_seconds
    require(duration is None or rules.minimum_duration_seconds <= duration <= rules.maximum_duration_seconds, 'ALGORITHM_DURATION_OUT_OF_RANGE')
    require(program.template != 'TWAP' or duration is not None, 'TWAP_DURATION_REQUIRED')
    native = program.execution_mode == 'VENUE_NATIVE'
    if not native:
        return (RequestPlan(namespace=nodes[0].namespace, operation=Operation.LOCAL_ALGORITHM,
            node_ids=(nodes[0].original.node_id,), timing=Timing.ALGORITHM_CHILD,
            generated_child_origin='APPLICATION_GENERATED_CHILD'),)
    require(program.template != 'VP' or getattr(algo, 'participation_bps', None) is None, 'NATIVE_VP_PARTICIPATION_UNMAPPABLE')
    o = nodes[0].original.order
    require(nodes[0].quantity.kind == 'BASE_QUANTITY' and o.trigger.kind == 'NONE' and not o.post_only
            and o.iceberg_quantity is None and o.routing == 'STANDARD' and o.stp == 'NONE', 'NATIVE_ALGORITHM_MODIFIER_UNSUPPORTED')
    require(o.tif.kind == 'NONE', 'NATIVE_ALGORITHM_TIF_FORBIDDEN')
    spot = profile.scope.product == t.Product.SPOT
    require(not (spot and program.template == 'VP') or o.price.kind == 'MARKET', 'SPOT_VP_PRICE_FORBIDDEN')
    require(spot or o.price.kind in ('MARKET', 'FIXED_LIMIT'), 'ALGORITHM_PRICE_UNSUPPORTED')
    parameters = [_p(FieldName.SYMBOL, profile.venue_symbol), _p(FieldName.SIDE, o.side),
        _p(FieldName.QUANTITY, nodes[0].quantity.value)]
    if program.template == 'TWAP':
        parameters.append(_p(FieldName.DURATION, duration))
    else:
        require(algo.urgency is not None, 'VP_URGENCY_REQUIRED')
        parameters.append(_p(FieldName.URGENCY, algo.urgency))
        if spot:
            if duration is not None:
                parameters.append(_p(FieldName.VP_TIME_LIMIT, duration))
        else:
            require(duration is None, 'FUTURES_VP_DURATION_UNMAPPABLE')
    if not spot:
        parameters.append(_p(FieldName.POSITION_SIDE, o.position_side))
        if profile.usd_m.position_mode == PositionMode.ONE_WAY:
            parameters.append(_p(FieldName.REDUCE_ONLY, o.reduce_only))
    if o.price.kind == 'FIXED_LIMIT':
        parameters.append(_p(FieldName.LIMIT_PRICE, o.price.value))
        with localcontext() as ctx:
            ctx.prec = 256
            notional = Decimal(nodes[0].quantity.value) * Decimal(o.price.value)
        require(rules.notional.contains(notional), 'ALGORITHM_NOTIONAL_OUT_OF_RANGE')
    return (RequestPlan(namespace=Namespace.SPOT_EXECUTION if spot else Namespace.USD_M_EXECUTION,
        operation=Operation.TWAP if program.template == 'TWAP' else Operation.VP,
        node_ids=(nodes[0].original.node_id,), parameters=tuple(parameters), native_mother=True,
        generated_child_origin='VENUE_GENERATED_CHILD'),)


def _native_list_request(program, nodes, symbol):
    """Merge shared list fields; retain every leg's typed modifiers explicitly."""
    parameters = [_p(FieldName.SYMBOL, symbol)]
    stps = {node.original.order.stp for node in nodes}
    require(len(stps) == 1, 'LIST_STP_MUST_MATCH')
    parameters.append(_p(FieldName.STP, next(iter(stps))))
    for node in nodes:
        for param in node.parameters:
            if param.name == FieldName.STP:
                continue  # Already proven equal and bound once at list level.
            require(param.name in _LIST_FIELDS, 'LIST_FIELD_UNMAPPABLE')
            leg = node.leg
            if leg in (Leg.ABOVE, Leg.BELOW) and param.name in (FieldName.SIDE, FieldName.QUANTITY):
                leg = Leg.NONE
            elif leg in (Leg.PENDING_ABOVE, Leg.PENDING_BELOW) and param.name in (FieldName.SIDE, FieldName.QUANTITY):
                leg = Leg.PENDING
            qualified = replace(param, leg=leg)
            prior = next((p for p in parameters if p.wire_name == qualified.wire_name), None)
            require(prior is None or prior.value == qualified.value, 'LIST_SHARED_FIELD_CONFLICT')
            if prior is None:
                parameters.append(qualified)
    return RequestPlan(namespace=Namespace.SPOT_LIST, operation=Operation[program.template],
        node_ids=tuple(node.original.node_id for node in nodes), parameters=tuple(parameters),
        native_mother=True, generated_child_origin='VENUE_GENERATED_CHILD')


def compile_order_program(proposal: OrderProposal | OrderProposalV3, rules: OrderRuleSnapshot, capabilities: OrderCapabilitySnapshot,
        *, profile: TrustedProductRules, scope: t.ScopeKey, now: str, expected_build_digest: str,
        expected_rule_revision: int, expected_capabilities_hash: str, expected_instrument_ref: str,
        allow_round_down: bool = False) -> ProgramCompilation:
    codec, program = _validate_bindings(proposal, rules, capabilities, profile, scope, now,
        expected_build_digest, expected_rule_revision, expected_capabilities_hash, expected_instrument_ref)
    require(scope.product == t.Product.USD_M_PERP or proposal.requested_leverage == 1, 'CASH_LEVERAGE_FORBIDDEN')
    features = requested_order_features(program, scope.product)
    require(set(features) <= set(capabilities.capability_ids), 'FEATURE_NOT_AVAILABLE')
    # Native algorithm parent fields differ from ordinary order fields (no TIF).
    # Validate its atomic envelope with a temporary GTC only for fixed-limit
    # futures algorithms; the immutable original and request omit that TIF.
    nodes = []
    for node in program.nodes:
        candidate = node
        if program.template in ALGORITHMS and node.order.price.kind == 'FIXED_LIMIT' and node.order.tif.kind == 'NONE':
            candidate = replace(node, order=replace(node.order, tif=replace(node.order.tif, kind='GTC')))
        compiled = _compile_node(candidate, program, rules, profile, allow_round_down, now)
        if candidate is not node:
            compiled = replace(compiled, original=node, parameters=tuple(p for p in compiled.parameters if p.name != FieldName.TIME_IN_FORCE))
        nodes.append(compiled)
    nodes = tuple(nodes)
    checks = {RequiredCheck.LOCKED_TARGET_DELTA, RequiredCheck.FINANCIAL_ADMISSION,
        RequiredCheck.OWNER_COMMAND_PERMIT, RequiredCheck.ADAPTER_QUALIFICATION, RequiredCheck.CURRENT_MARKET_RULES}
    if any(node.original.order.reduce_only or node.quantity.kind == 'CLOSE_ALL'
            or node.original.order.side == 'SELL' for node in nodes):
        checks.add(RequiredCheck.REAL_POSITION_REDUCTION)
    if any(node.quantity.kind == 'DERIVED_RECEIPT' for node in nodes):
        checks.add(RequiredCheck.FULL_FILL_NET_RECEIPT)
    native = program.execution_mode == 'VENUE_NATIVE'
    if program.template in LISTS:
        nodes = _list_legs(program, nodes)
        if program.template != 'OCO':
            checks.add(RequiredCheck.PARTIAL_FILL_PROTECTION)
        if not native and program.exclusive_groups:
            checks.add(RequiredCheck.LOCAL_MUTUAL_EXCLUSION_RACE)
    if program.template in ALGORITHMS:
        requests = _algorithm_requests(program, nodes, profile)
        checks.add(RequiredCheck.ALGORITHM_NOTIONAL)
        if not native:
            checks.add(RequiredCheck.LOCAL_ALGORITHM_CHILD_ADMISSION)
    elif native and program.template in LISTS:
        requests = (_native_list_request(program, nodes, profile.venue_symbol),)
    elif program.template == 'BATCH' and native:
        require(len(nodes) <= profile.usd_m.maximum_batch_orders, 'BATCH_SIZE_EXCEEDED')
        require(all(node.namespace == Namespace.USD_M_ORDINARY for node in nodes), 'BATCH_CONDITIONAL_FORBIDDEN')
        requests = (RequestPlan(namespace=Namespace.USD_M_ORDINARY, operation=Operation.BATCH,
            node_ids=tuple(node.original.node_id for node in nodes)),)
    else:
        requests = tuple(RequestPlan(namespace=node.namespace,
            operation=Operation.CONDITIONAL if node.namespace == Namespace.USD_M_CONDITIONAL else
                Operation.SOR if node.original.order.routing == 'SOR' else Operation.ORDER,
            node_ids=(node.original.node_id,), parameters=(_p(FieldName.SYMBOL, profile.venue_symbol), *node.parameters),
            native_mother=native and node.namespace == Namespace.USD_M_CONDITIONAL,
            generated_child_origin='VENUE_GENERATED_CHILD' if native and node.namespace == Namespace.USD_M_CONDITIONAL else None,
            timing=Timing.AFTER_NET_RECEIPT if node.quantity.kind == 'DERIVED_RECEIPT' else
                Timing.AFTER_FULL_FILL if node.activation_requires_full_fill else Timing.INITIAL) for node in nodes)
    if scope.product == t.Product.US_EQUITY:
        checks.add(RequiredCheck.EQUITY_MARKET_CALENDAR)
    if any(node.original.order.trigger.price_protect for node in nodes):
        checks.add(RequiredCheck.TRIGGER_PROTECT_SPREAD)
    if any(node.original.order.price.kind != 'FIXED_LIMIT' for node in nodes):
        checks.add(RequiredCheck.EXECUTION_PRICE_ENVELOPE)
    return ProgramCompilation(original_proposal=proposal, original_program=program, scope=scope, nodes=nodes,
        requests=requests, requested_features=features, required_checks=tuple(sorted(checks)),
        proposal_hash=codec.hash('order-proposal', proposal), program_hash=codec.hash('order-program', program),
        rules_hash=codec.hash('order-rule-snapshot', rules), capabilities_hash=expected_capabilities_hash,
        profile_hash=canonical_hash(domain_payload(profile)), rule_revision=rules.revision,
        adapter_build_digest=expected_build_digest, capability_qualification=capabilities.qualification)
