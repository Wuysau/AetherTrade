"""Offline Binance product checks and owner-bound account projections.

No SDK, credentials, HTTP transport or order sender is installed here. Callbacks
are trusted application composition, never proposal-supplied code. They resolve
already normalized PAPER/RESEARCH facts. A successful product check is necessary,
not sufficient: IP-04 authorization, IP-06 risk, IP-10 durable dispatch, IP-11
qualified binding and IP-15 protection still apply. No result enables trading.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
import os
import re

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp


PRODUCTS = (t.Product.SPOT, t.Product.USD_M_PERP)
OFFLINE = (t.Environment.PAPER, t.Environment.RESEARCH)
EPOCH = '1970-01-01T00:00:00Z'
COMMON_READ_SECTIONS = ('BALANCES', 'OPEN_ORDERS', 'FILLS', 'FEES', 'RESERVATIONS')


def _decimal(value: Decimal, *, minimum: Decimal | None = None) -> None:
    if (not isinstance(value, Decimal) or not value.is_finite()
            or len(value.as_tuple().digits) > 36 or abs(value.as_tuple().exponent) > 18
            or (minimum is not None and value < minimum)):
        raise ValueError('INVALID_DECIMAL')


def _scope(scope: t.ScopeKey) -> None:
    if (scope.product not in PRODUCTS or not scope.venue_account_id or not scope.instrument_uid
            or scope.venue_leg not in ('BOTH', 'LONG', 'SHORT')
            or (scope.product == t.Product.SPOT and scope.venue_leg != 'BOTH')):
        raise ValueError('BINANCE_SCOPE_REQUIRED')


def _complete(coverage: t.Coverage, scope: t.ScopeKey) -> bool:
    return (coverage.scope == scope and coverage.supported is True
            and coverage.query_succeeded is True and coverage.pagination_complete is True
            and not coverage.missing)


def _fresh(start: str, end: str, now: str) -> bool:
    try:
        return utc_timestamp(start) <= utc_timestamp(now) < utc_timestamp(end)
    except (TypeError, ValueError):
        return False


def _refs(refs: tuple[t.EvidenceRef, ...], scope: t.ScopeKey, now: str) -> bool:
    try:
        return bool(refs) and all(
            ref.id and ref.source and re.fullmatch('[0-9a-f]{64}', ref.hash)
            and ref.environment == scope.environment
            and utc_timestamp(ref.visible_at) <= utc_timestamp(now) for ref in refs)
    except (AttributeError, TypeError, ValueError):
        return False


class BinanceProductProfile:
    """Inspect evidence without promoting metadata into native qualification.

    The IP-02 lock has no deployment/artifact binding. Even a supplied PASS is
    not an independently validated native probe. There is deliberately no flag
    or callback that changes this boundary to VERIFIED in this implementation.
    """

    def __init__(self, scope: t.ScopeKey):
        _scope(scope)
        self.scope = scope

    def verify(self, evidence: t.VenueCapabilityEvidence,
               build: t.QualifiedBuild) -> t.CapabilityProfile:
        reasons = ['NATIVE_BINDING_NOT_QUALIFIED', 'PLATFORM_PROBES_NOT_RUN',
                   'ACCOUNT_AUTHORIZATION_NOT_ESTABLISHED']
        if evidence.scope != self.scope or evidence.coverage.scope != self.scope:
            reasons.append('SCOPE_MISMATCH')
        if not _complete(evidence.coverage, self.scope):
            reasons.append('INCOMPLETE_COVERAGE')
        if not evidence.source or not evidence.session_id or not evidence.query_scope:
            reasons.append('PROBE_CONTEXT_MISSING')
        if not _refs(evidence.evidence, self.scope, max(
                (ref.visible_at for ref in evidence.evidence), default=EPOCH)):
            reasons.append('INVALID_EVIDENCE')
        candidate = build.candidate
        env = candidate.build_environment
        if (evidence.build_digest != build.artifact_digest
                or not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', build.artifact_digest)):
            reasons.append('BUILD_MISMATCH_OR_UNRESOLVED')
        if (not all(re.fullmatch('[0-9a-f]{40}', ref or '') for ref in
                    (candidate.review_ref, candidate.deployment_ref))
                or not all(re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', value or '') for value in
                           (candidate.source_digest, env.image_digest, env.dependency_digest))
                or env.os.lower() != 'linux' or not env.arch or not env.python_abi
                or not build.probe_results):
            reasons.append('EXACT_BUILD_EVIDENCE_MISSING')
        if self.scope.environment == t.Environment.TESTNET:
            reasons.append('TESTNET_IS_NOT_LIVE_QUALIFICATION')
        return t.CapabilityProfile(scope=self.scope, build_digest=build.artifact_digest,
            status=t.CapabilityStatus.UNVERIFIED, capabilities=(), limitations=tuple(reasons),
            evidence=evidence.evidence, valid_from=EPOCH, valid_until=EPOCH)


@dataclass(frozen=True, slots=True, kw_only=True)
class BinanceBalance:
    asset: str
    total: Decimal
    available: Decimal

    def __post_init__(self):
        _decimal(self.total, minimum=Decimal(0))
        _decimal(self.available, minimum=Decimal(0))
        if not self.asset or self.available > self.total:
            raise ValueError('INVALID_BALANCE')


@dataclass(frozen=True, slots=True, kw_only=True)
class RateBudget:
    """One current shared-server window, supplied by the owner limiter.

    This is a pure precheck, not token consumption or a per-symbol limiter.
    Dispatcher must acquire shared IP/account tokens atomically before sending.
    """
    key: str
    limit: int
    used: int
    request_cost: int

    def __post_init__(self):
        if (not self.key or any(type(v) is not int or v < 0 for v in
                (self.limit, self.used, self.request_cost)) or self.request_cost == 0):
            raise ValueError('INVALID_RATE_BUDGET')


@dataclass(frozen=True, slots=True, kw_only=True)
class BinanceRules:
    """Normalized current rules; no native-symbol/product inference.

    revision covers filters, fee assumptions and server limit changes together.
    Market bounds must be normalized for MARKET_LOT_SIZE and venue reference
    prices by the reader; unknown filters remain in missing, never ignored.
    """
    scope: t.ScopeKey
    instrument: t.InstrumentSpec
    revision: int
    observed_at: str
    valid_until: str
    evidence: tuple[t.EvidenceRef, ...]
    maximum_quantity: Decimal
    minimum_price: Decimal
    maximum_price: Decimal
    maximum_notional: Decimal
    fee_fraction: Decimal
    fee_asset: str
    maximum_leverage: Decimal
    order_types: tuple[str, ...]
    rate_budgets: tuple[RateBudget, ...]
    missing: tuple[str, ...] = ()
    market_quantity_step: Decimal | None = None
    market_minimum_quantity: Decimal | None = None
    market_maximum_quantity: Decimal | None = None

    def __post_init__(self):
        _scope(self.scope)
        i = self.instrument
        if (i.venue != 'BINANCE' or i.product != self.scope.product
                or i.instrument_uid != self.scope.instrument_uid
                or type(self.revision) is not int or self.revision < 1
                or i.rule_revision != self.revision or not i.native_symbol
                or i.quantity_step.unit != i.minimum_quantity.unit
                or i.tick_size.currency != i.quote_currency
                or i.minimum_notional.currency != i.quote_currency
                or i.settlement_currency != i.quote_currency):
            raise ValueError('RULE_SCOPE_OR_UNIT_MISMATCH')
        for value in (i.tick_size.value, i.quantity_step.value, i.minimum_quantity.value,
                      self.maximum_quantity, self.maximum_price, self.maximum_notional,
                      self.maximum_leverage):
            _decimal(value, minimum=Decimal(0))
            if value == 0:
                raise ValueError('POSITIVE_RULE_REQUIRED')
        for value in (i.minimum_notional.amount, self.minimum_price, self.fee_fraction):
            _decimal(value, minimum=Decimal(0))
        if (self.fee_fraction > 1 or self.maximum_leverage < 1
                or self.maximum_quantity < i.minimum_quantity.value
                or self.maximum_price < self.minimum_price
                or self.maximum_notional < i.minimum_notional.amount
                or (self.scope.product == t.Product.SPOT and self.maximum_leverage != 1)):
            raise ValueError('INCONSISTENT_RULES')
        if len({b.key for b in self.rate_budgets}) != len(self.rate_budgets):
            raise ValueError('DUPLICATE_RATE_WINDOW')
        market = (self.market_quantity_step, self.market_minimum_quantity, self.market_maximum_quantity)
        if any(value is not None for value in market):
            for value in market:
                _decimal(value, minimum=Decimal(0))
                if value == 0:
                    raise ValueError('POSITIVE_MARKET_RULE_REQUIRED')
            if self.market_minimum_quantity > self.market_maximum_quantity:
                raise ValueError('INCONSISTENT_MARKET_RULES')


@dataclass(frozen=True, slots=True, kw_only=True)
class BinanceTradingState:
    """Owner-resolved facts, including allocation and outstanding exit exposure.

    held_* are local reservations NOT yet reflected in available balances;
    pending_exit_quantity includes unknown/cancel-pending exits. SHORT uses a
    signed negative quantity, normalized explicitly by trusted composition.
    """
    scope: t.ScopeKey
    observed_at: str
    valid_until: str
    coverage: t.Coverage
    evidence: tuple[t.EvidenceRef, ...]
    balances: tuple[BinanceBalance, ...]
    actual_quantity: Decimal
    owned_quantity: Decimal
    pending_exit_quantity: Decimal
    held_base: Decimal
    held_quote: Decimal
    position_mode: str
    margin_mode: str
    actual_leverage: Decimal

    def __post_init__(self):
        _scope(self.scope)
        _decimal(self.actual_quantity)
        for value in (self.owned_quantity, self.pending_exit_quantity,
                      self.held_base, self.held_quote, self.actual_leverage):
            _decimal(value, minimum=Decimal(0))
        if len({b.asset for b in self.balances}) != len(self.balances):
            raise ValueError('DUPLICATE_BALANCE')


@dataclass(frozen=True, slots=True, kw_only=True)
class BinanceOrderCheck:
    scope: t.ScopeKey
    side: str
    quantity: t.Quantity
    price: t.Price
    order_type: str
    rule_revision: int
    requested_leverage: Decimal
    authorized_maximum_leverage: Decimal
    authorized_position_mode: str
    authorized_margin_mode: str
    reduce: bool

    def __post_init__(self):
        _scope(self.scope)
        for value in (self.quantity.value, self.price.value, self.requested_leverage,
                      self.authorized_maximum_leverage):
            _decimal(value, minimum=Decimal(0))
        if (self.quantity.value == 0 or self.price.value == 0 or self.requested_leverage < 1
                or self.authorized_maximum_leverage < 1 or type(self.reduce) is not bool
                or self.side not in ('BUY', 'SELL') or self.order_type not in ('LIMIT', 'MARKET')):
            raise ValueError('INVALID_ORDER_CHECK')


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductDecision:
    allow_new_risk: bool
    allow_reduction: bool
    rejection_codes: tuple[str, ...]
    permitted_leverage: Decimal | None

    @property
    def position_mode_writes(self) -> list[str]:
        return []  # This module has no write interface of any kind.

    @property
    def execution_authorized(self) -> bool:
        return False


def check_order(request: BinanceOrderCheck, state: BinanceTradingState,
                rules: BinanceRules, *, now: str) -> ProductDecision:
    """Pure PAPER/RESEARCH validation; never changes quantity, mode or leverage."""
    errors: list[str] = []
    i, q, p = rules.instrument, request.quantity.value, request.price.value
    if request.scope.environment not in OFFLINE:
        errors.append('ENVIRONMENT_NOT_ENABLED')
    if request.scope != state.scope or request.scope != rules.scope:
        errors.append('SCOPE_MISMATCH')
    if not _complete(state.coverage, request.scope):
        errors.append('ACCOUNT_COVERAGE_INCOMPLETE')
    if (not _fresh(state.observed_at, state.valid_until, now)
            or not _refs(state.evidence, request.scope, now)):
        errors.append('ACCOUNT_EVIDENCE_STALE_OR_INVALID')
    if (not _fresh(rules.observed_at, rules.valid_until, now)
            or not _refs(rules.evidence, request.scope, now) or rules.missing):
        errors.append('RULES_STALE_OR_INCOMPLETE')
    if request.rule_revision != rules.revision:
        errors.append('RULE_REVISION_CHANGED')
    if request.quantity.unit != i.quantity_step.unit or request.price.currency != i.quote_currency:
        errors.append('ORDER_UNIT_MISMATCH')
    if request.order_type not in rules.order_types:
        errors.append('ORDER_TYPE_UNSUPPORTED')
    if not rules.rate_budgets or any(b.used + b.request_cost > b.limit for b in rules.rate_budgets):
        errors.append('SERVER_RATE_LIMIT')
    # Decimal bounds above limit the number of significant places; intermediate
    # products must not depend on a caller's ambient Decimal context.
    with localcontext() as ctx:
        ctx.prec = 160
        if q % i.quantity_step.value or not i.minimum_quantity.value <= q <= rules.maximum_quantity:
            errors.append('QUANTITY_RULE')
        if request.order_type == 'MARKET':
            if rules.market_quantity_step is None:
                errors.append('MARKET_RULES_MISSING')
            elif (q % rules.market_quantity_step
                    or not rules.market_minimum_quantity <= q <= rules.market_maximum_quantity):
                errors.append('MARKET_QUANTITY_RULE')
        if p % i.tick_size.value or not rules.minimum_price <= p <= rules.maximum_price:
            errors.append('PRICE_RULE')
        notional = q * p
        if not i.minimum_notional.amount <= notional <= rules.maximum_notional:
            errors.append('NOTIONAL_RULE')
        balances = {b.asset: b for b in state.balances}
        base, quote = balances.get(i.quantity_step.unit), balances.get(i.quote_currency)
        if rules.fee_asset not in (i.quantity_step.unit, i.quote_currency):
            errors.append('FEE_ASSET_NOT_SUPPORTED')
        if request.scope.product == t.Product.SPOT:
            if (state.position_mode != 'CASH' or state.margin_mode != 'CASH'
                    or request.authorized_position_mode != 'CASH'
                    or request.authorized_margin_mode != 'CASH'
                    or state.actual_leverage != 1 or request.requested_leverage != 1):
                errors.append('CASH_ONLY')
            if state.actual_quantity < 0 or (base and state.actual_quantity > base.total):
                errors.append('INVALID_CASH_INVENTORY')
            if request.side == 'SELL':
                if not request.reduce:
                    errors.append('EXPLICIT_REDUCTION_REQUIRED')
                cost = q * (1 + rules.fee_fraction if rules.fee_asset == i.quantity_step.unit else 1)
                available = Decimal(0) if base is None else base.available - state.held_base
                owned = min(state.owned_quantity, state.actual_quantity) - state.pending_exit_quantity
                if cost > available or cost > owned:
                    errors.append('INSUFFICIENT_SELLABLE_BASE')
            elif request.reduce:
                errors.append('CASH_REDUCTION_REQUIRES_SELL')
            else:
                cost = notional * (1 + rules.fee_fraction if rules.fee_asset == i.quote_currency else 1)
                if quote is None or cost > quote.available - state.held_quote:
                    errors.append('INSUFFICIENT_QUOTE')
        else:
            leg = request.scope.venue_leg
            if (state.position_mode not in ('ONE_WAY', 'HEDGE')
                    or state.margin_mode not in ('ISOLATED', 'CROSS')
                    or state.actual_leverage < 1):
                errors.append('ACTUAL_MODE_UNKNOWN')
            if ((state.position_mode == 'ONE_WAY' and leg != 'BOTH')
                    or (state.position_mode == 'HEDGE' and leg == 'BOTH')
                    or (leg == 'LONG' and state.actual_quantity < 0)
                    or (leg == 'SHORT' and state.actual_quantity > 0)):
                errors.append('ACTUAL_LEG_MISMATCH')
            if request.reduce:
                close_side = 'SELL' if state.actual_quantity > 0 else 'BUY'
                reducible = min(abs(state.actual_quantity), state.owned_quantity) - state.pending_exit_quantity
                if request.side != close_side or q > reducible or state.actual_quantity == 0:
                    errors.append('INVALID_REDUCTION')
            else:
                if (request.authorized_position_mode != state.position_mode
                        or request.authorized_margin_mode != state.margin_mode):
                    errors.append('ACTUAL_MODE_NOT_AUTHORIZED')
                if (request.requested_leverage != state.actual_leverage
                        or request.requested_leverage > min(request.authorized_maximum_leverage,
                                                           rules.maximum_leverage)):
                    errors.append('LEVERAGE_REPLAN_REQUIRED')
                if ((leg == 'LONG' and request.side != 'BUY') or (leg == 'SHORT' and request.side != 'SELL')
                        or (leg == 'BOTH' and ((state.actual_quantity > 0 and request.side == 'SELL')
                                             or (state.actual_quantity < 0 and request.side == 'BUY')))):
                    errors.append('EXPLICIT_REDUCTION_REQUIRED')
                # Linear quote-settled only. Full portfolio/maintenance risk is IP-06.
                cost = notional / request.requested_leverage + notional * rules.fee_fraction
                if rules.fee_asset != i.quote_currency or quote is None or cost > quote.available - state.held_quote:
                    errors.append('INSUFFICIENT_MARGIN_OR_FEE_EVIDENCE')
    reasons = tuple(dict.fromkeys(errors))
    return ProductDecision(allow_new_risk=not reasons and not request.reduce,
        allow_reduction=not reasons and request.reduce, rejection_codes=reasons,
        permitted_leverage=request.requested_leverage if not reasons and not request.reduce else None)


def native_protection_missing(profile: t.CapabilityProfile, rules: BinanceRules, *,
                              build_digest: str, quantity: t.Quantity, trigger_price: t.Price,
                              trigger: str, order_type: str, combined_oco: bool,
                              now: str) -> tuple[str, ...]:
    """Necessary capability checks only; not evidence of a live protective order.

    Per-order direction, IDs, active status and remaining coverage stay IP-15.
    A generic CONDITIONAL flag never implies MARK, OCO or algo-ID continuity.
    """
    missing: list[str] = []
    if (profile.scope != rules.scope or profile.build_digest != build_digest
            or not build_digest or profile.status != t.CapabilityStatus.VERIFIED
            or not _fresh(profile.valid_from, profile.valid_until, now)
            or not _refs(profile.evidence, rules.scope, now)):
        missing.append('CAPABILITY_UNVERIFIED')
    if (not _fresh(rules.observed_at, rules.valid_until, now) or rules.missing
            or not _refs(rules.evidence, rules.scope, now)):
        missing.append('RULES_STALE_OR_INCOMPLETE')
    required = ['NATIVE_CONDITIONAL', 'NATIVE_ORDER_ID_QUERY', 'NATIVE_MINIMUM_RULES',
                'ORDER_TYPE:' + order_type, 'TRIGGER:' + trigger]
    if rules.scope.product == t.Product.USD_M_PERP:
        required.extend(('NATIVE_ALGO_ID_CONTINUITY', 'NATIVE_REDUCE_LEG'))
    if combined_oco:
        required.append('NATIVE_OCO_SHARED_ASSET')
        if rules.scope.product != t.Product.SPOT:
            missing.append('OCO_PRODUCT_UNSUPPORTED')
    if trigger not in ('LAST', 'MARK') or (trigger == 'MARK' and rules.scope.product == t.Product.SPOT):
        missing.append('TRIGGER_UNSUPPORTED')
    conditional = (('STOP_LOSS', 'STOP_LOSS_LIMIT') if rules.scope.product == t.Product.SPOT
                   else ('STOP_MARKET', 'STOP'))
    if order_type not in conditional or order_type not in rules.order_types:
        missing.append('CONDITIONAL_ORDER_UNSUPPORTED')
    missing.extend('MISSING:' + capability for capability in required if capability not in profile.capabilities)
    _decimal(quantity.value, minimum=Decimal(0))
    _decimal(trigger_price.value, minimum=Decimal(0))
    i = rules.instrument
    with localcontext() as ctx:
        ctx.prec = 160
        if (quantity.unit != i.quantity_step.unit or trigger_price.currency != i.quote_currency
                or not i.minimum_quantity.value <= quantity.value <= rules.maximum_quantity
                or quantity.value % i.quantity_step.value
                or trigger_price.value % i.tick_size.value
                or not rules.minimum_price <= trigger_price.value <= rules.maximum_price
                or not i.minimum_notional.amount <= quantity.value * trigger_price.value <= rules.maximum_notional):
            missing.append('NATIVE_SIZE_OR_PRICE_RULE')
    return tuple(dict.fromkeys(missing))


@dataclass(frozen=True, slots=True, kw_only=True)
class BinanceAccountRead:
    """Normalized response + ledger join, not a Binance HTTP response schema.

    sections explicitly attest endpoint coverage, including successful empty
    queries. Position lists alone never establish flatness. Spot has cash assets
    here; strategy allocation-to-ManagedPosition joins belong to the ledger.
    """
    scope: t.ScopeKey
    session_id: str
    as_of: str
    coverage: t.Coverage
    evidence: tuple[t.EvidenceRef, ...]
    sections: tuple[str, ...]
    balances: tuple[BinanceBalance, ...]
    positions: tuple[t.ManagedPosition, ...]
    active_orders: tuple[t.OrderProjection, ...]
    reservations: tuple[t.ReservationProjection, ...]
    sequence: int
    position_mode: str | None = None
    margin_mode: str | None = None
    leverage: Decimal | None = None


class BinanceAccountReader:
    """Local owner guard before and after an injected read. No native transport.

    A lease is checked here but is not proof of an external fence. Native owner
    process isolation and platform access remain separately qualified by IP-11.
    """
    def __init__(self, *, shard: t.ExecutionShard, instance_id: str, instrument: t.InstrumentSpec,
                 lease: Callable[[], t.OwnerLease | None],
                 read: Callable[[t.AccountQuery], BinanceAccountRead],
                 clock: Callable[[], t.ClockReading], maximum_age_seconds: int = 30):
        if (shard.product not in PRODUCTS or not instance_id or not shard.venue_account_id
                or not shard.node_id or type(shard.execution_epoch) is not int or shard.execution_epoch < 1
                or type(maximum_age_seconds) is not int or maximum_age_seconds <= 0):
            raise ValueError('INVALID_READER_BINDING')
        self._shard, self._instance, self._lease = shard, instance_id, lease
        self._read, self._clock, self._age = read, clock, maximum_age_seconds
        if instrument.product != shard.product or instrument.venue != 'BINANCE':
            raise ValueError('INSTRUMENT_BINDING_MISMATCH')
        self._instrument = instrument
        self._pid = os.getpid()
        self._watermarks: dict[t.ScopeKey, tuple[int, str]] = {}

    def _owner(self, scope: t.ScopeKey) -> t.ClockReading:
        _scope(scope)
        if scope.environment not in OFFLINE:
            raise PermissionError('NATIVE_ACCOUNT_READ_NOT_QUALIFIED')
        s, lease, now = self._shard, self._lease(), self._clock()
        if (os.getpid() != self._pid or lease is None or lease.shard != s
                or lease.instance_id != self._instance or now.clock_health != 'HEALTHY'
                or (scope.environment, scope.venue_account_id, scope.product) !=
                   (s.environment, s.venue_account_id, s.product)
                or scope.instrument_uid != self._instrument.instrument_uid
                or not now.monotonic_ns < lease.deadline_monotonic_ns
                or not utc_timestamp(now.utc_now) < utc_timestamp(lease.expires_at)):
            raise PermissionError('EXECUTION_OWNER_REQUIRED')
        return now

    def _unavailable(self, scope: t.ScopeKey, reason: str) -> t.BookSnapshot:
        return t.BookSnapshot(scope=scope, positions=(), active_orders=(), reservations=(),
            balances=(), sequence=0, basis_fingerprint=canonical_hash({'missing': reason,
                'scope': domain_payload(scope)}), evidence=(), coverage=t.Coverage(scope=scope,
                query_succeeded=False, pagination_complete=False, supported=False, missing=(reason,)))

    def snapshot(self, query: t.AccountQuery) -> t.BookSnapshot:
        before = self._owner(query.scope)
        try:
            query_fresh = 0 <= (utc_timestamp(before.utc_now) - utc_timestamp(query.as_of)).total_seconds() <= self._age
        except (TypeError, ValueError):
            query_fresh = False
        if not query.session_id or not query_fresh:
            return self._unavailable(query.scope, 'INVALID_QUERY_TIME_OR_SESSION')
        try:
            result = self._read(query)
        except (OSError, TimeoutError):
            self._owner(query.scope)
            return self._unavailable(query.scope, 'ACCOUNT_READ_FAILED')
        now = self._owner(query.scope)
        if not isinstance(result, BinanceAccountRead):
            return self._unavailable(query.scope, 'ACCOUNT_RESPONSE_INVALID')
        if (result.scope != query.scope or result.coverage.scope != query.scope
                or result.session_id != query.session_id):
            return self._unavailable(query.scope, 'ACCOUNT_RESPONSE_SCOPE_MISMATCH')
        try:
            fresh = (utc_timestamp(query.as_of) <= utc_timestamp(result.as_of)
                     <= utc_timestamp(now.utc_now)
                     and (utc_timestamp(now.utc_now) - utc_timestamp(result.as_of)).total_seconds() <= self._age)
        except (TypeError, ValueError):
            fresh = False
        if not fresh or not _refs(result.evidence, query.scope, result.as_of):
            return self._unavailable(query.scope, 'ACCOUNT_RESPONSE_EVIDENCE_INVALID')
        if (type(result.sequence) is not int or result.sequence < 0
                or len({b.asset for b in result.balances}) != len(result.balances)
                or any(p.scope != query.scope for p in result.positions)
                or any(o.order.identity.scope != query.scope for o in result.active_orders)
                or any(r.reservation.environment != query.scope.environment
                       or r.reservation.venue_account_id != query.scope.venue_account_id
                       for r in result.reservations)
                or len({p.position_id for p in result.positions}) != len(result.positions)
                or len({o.order.identity.logical_client_order_id for o in result.active_orders}) != len(result.active_orders)):
            return self._unavailable(query.scope, 'ACCOUNT_ROWS_INVALID')
        if query.scope.product == t.Product.SPOT and result.positions:
            return self._unavailable(query.scope, 'SPOT_HAS_NO_CONTRACT_POSITIONS')
        if query.scope.product == t.Product.USD_M_PERP:
            leg = query.scope.venue_leg
            if (result.position_mode not in ('ONE_WAY', 'HEDGE')
                    or result.margin_mode not in ('ISOLATED', 'CROSS')
                    or not isinstance(result.leverage, Decimal) or not result.leverage.is_finite()
                    or result.leverage < 1 or (result.position_mode == 'ONE_WAY' and leg != 'BOTH')
                    or (result.position_mode == 'HEDGE' and leg == 'BOTH')
                    or any(p.actual_quantity.unit != self._instrument.quantity_step.unit
                           or (leg == 'LONG' and p.actual_quantity.value < 0)
                           or (leg == 'SHORT' and p.actual_quantity.value > 0) for p in result.positions)):
                return self._unavailable(query.scope, 'ACTUAL_MODE_OR_POSITION_INVALID')
        required = COMMON_READ_SECTIONS + (('CASH_ALLOCATIONS',) if query.scope.product == t.Product.SPOT
                                           else ('POSITIONS', 'POSITION_MODE', 'MARGIN_MODE', 'LEVERAGE'))
        missing = tuple(dict.fromkeys((*result.coverage.missing,
            *(section for section in required if section not in result.sections),
            *('BALANCE:' + asset for asset in
              ((self._instrument.quantity_step.unit, self._instrument.quote_currency)
               if query.scope.product == t.Product.SPOT else (self._instrument.settlement_currency,))
              if asset not in {balance.asset for balance in result.balances}))))
        coverage = replace(result.coverage, missing=missing)
        # Totals remain totals: free/locked balances must not be mistaken for
        # sellable inventory. Trading checks consume the typed available fields.
        payload = domain_payload(result)
        payload.pop('as_of')
        payload.pop('evidence')
        payload.pop('session_id')  # A new query session cannot reset account sequence.
        fingerprint = canonical_hash(payload)
        key = query.scope
        previous = self._watermarks.get(key)
        if previous and (result.sequence < previous[0]
                         or (result.sequence == previous[0] and fingerprint != previous[1])):
            return self._unavailable(query.scope, 'ACCOUNT_SEQUENCE_REGRESSION_OR_CONFLICT')
        if _complete(coverage, query.scope):
            self._watermarks[key] = (result.sequence, fingerprint)
        return t.BookSnapshot(scope=query.scope, positions=result.positions,
            active_orders=result.active_orders, reservations=result.reservations,
            balances=tuple(t.Money(amount=b.total, currency=b.asset) for b in result.balances),
            sequence=result.sequence, basis_fingerprint=fingerprint, coverage=coverage,
            evidence=result.evidence)
