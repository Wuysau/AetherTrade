"""Local PAPER composition. PostgreSQL is the execution source of truth.

There is no exchange or model client here. A request creates an IP-07 atomic
bundle; a distinct owner tick submits it. Synthetic venue events are explicitly
injected and reduced into the business ledger. Legacy console fills are never
imported. The independently restricted PaperEngine database is mandatory.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from time import monotonic_ns
from threading import local
from uuid import uuid4

import psycopg

from trading_system.accounts.authorization import AuthorizationBinding, grant_errors
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec, TradeIntent
from trading_system.engine.nautilus.bridge import BridgeAuthority
from trading_system.engine.paper import PaperEngine, SyntheticOrderEvent, paper_client_order_id
from trading_system.execution.commands import CommandRepository, command_hash
from trading_system.execution.dispatcher import CommandDispatcher, DispatchFacts, REQUIRED_CHECKS, clock_now
from trading_system.intents.ingress import IngressRevision, revision_key
from trading_system.intents.target_compiler import TargetInputs
from trading_system.risk.evaluator import RiskEvaluator, RiskInputs, GroupLimit, EmergencyReduction
from trading_system.risk.exposure import ExposureMark, WorkingOrder
from trading_system.risk.reservations import ReservationService, ReservationInputs
from trading_system.risk.reservations import ReconciliationProof, ReleasedBalanceAttestation
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.risk.target_guard import TargetReport, TargetBasis, validate_report, roots, digest
from trading_system.risk.locked_actions import ActionProof, WorkingTerms
from trading_system.risk.locks import POLICIES
from trading_system.runtime.execution_owner import ExecutionOwner
from trading_system.storage.events import EventStore
from trading_system.storage.models import decode, scope_id
from trading_system.storage.uow import UnitOfWork
from .errors import ApiError
from .validation import amount, number, SYMBOLS

D = Decimal
VERSION = '2026-10-20'
ACCOUNT, GROUP, OWNER = 'paper-local', 'console-paper-collateral', 'console-paper-owner'
NOTICE = '本地虚拟 PAPER；无交易所或模型调用。价格与成交事件为明确注入，不能证明真实滑点、流动性或原生保护。'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def money(value, currency='USDT'):
    return t.Money(amount=D(value), currency=currency)


def scope_for(symbol):
    return t.ScopeKey(environment=t.Environment.PAPER, venue_account_id=ACCOUNT,
        product=t.Product.SPOT, instrument_uid='console-paper:' + symbol, venue_leg='BOTH')


def coverage(scope):
    return t.Coverage(scope=scope, query_succeeded=True, pagination_complete=True, supported=True, missing=())


def authority_deadline(reading, request, *, quote_required=True):
    limits = [request.intent.valid_until, request.authorization.authorization.expires_at]
    if quote_required:
        limits.append((utc_timestamp(request.quote_at) + timedelta(seconds=request.maximum_age)).isoformat())
    delta = min(map(utc_timestamp, limits)) - utc_timestamp(reading.utc_now)
    ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    return reading.monotonic_ns + max(0, ns)


def attach_console_runtime(store, runtime, quote_snapshot):
    """Use the production authority reader in both app and explicit local CLI."""
    if store.sqlite:
        raise ValueError('SQLite console is interaction-only; unified PAPER requires PostgreSQL console storage')
    from sqlalchemy import select
    from .store import state, strategies

    def current_control(request, command):
        # The owner's business-ledger session lock fences every Store control
        # transaction, even when console and business databases are separate.
        with store.engine.connect() as connection:
            data = connection.execute(select(state.c.data).where(state.c.id == 1)).scalar_one()
            if command.canonical_payload.side == 'BUY' and data['account']['paused']:
                raise ApiError('ENTRIES_PAUSED', '新增风险已暂停。', 409)
            if data['risk']['revision'] != request.risk_revision:
                raise ApiError('STALE_RISK_POLICY', '风险政策已更改，请重新评估。', 409)
            market = quote_snapshot()
            price = store._price(market, request.symbol, request.maximum_age)
            quote = next(item for item in market['quotes'] if item['symbol'] == request.symbol)
            if (utc_timestamp(quote['as_of']) < utc_timestamp(request.quote_at)
                    or abs(price / D(request.quote_price) - 1) > D('.002')):
                raise ApiError('MARKET_REPLAN_REQUIRED', '行情乱序或超出原订单价格界限。', 409)
            if request.strategy_id:
                strategy = connection.execute(select(strategies.c.data).where(strategies.c.id == request.strategy_id)).scalar_one_or_none()
                if not strategy or strategy['revision'] != request.strategy_revision or strategy['status'] == 'PAUSED':
                    raise ApiError('STALE_STRATEGY', '策略授权已变更。', 409)
    runtime.current_control = current_control
    runtime.console_store = store
    from .paper_account_risk import account_risk_reader
    runtime.current_account_risk = account_risk_reader(store, runtime, quote_snapshot)
    store.execution = runtime
    return runtime


@dataclass(frozen=True, kw_only=True)
class RuntimeRequest:
    key: str
    fingerprint: str
    symbol: str
    side: str
    quantity: str
    strategy_id: str | None
    mode: str
    strategy_revision: int
    command_id: str
    intent_id: str
    requested_at: str
    quote_at: str
    quote_price: str
    maximum_age: int
    budget: str
    initial_cash: str
    risk_position_pct: str
    risk_revision: int
    stop_loss_pct: str
    rationale: str
    authorization: AuthorizationBinding
    intent: TradeIntent


@dataclass(frozen=True, kw_only=True)
class RuntimeRecoveryArchive:
    scope: t.ScopeKey
    book_hash: str
    normalized_report: t.VenueReport
    queried_commands: tuple[str, ...]
    protection_command_id: str | None
    protection_quantity: t.Quantity | None
    protection_stop_price: t.Price | None
    source_evidence: tuple[t.EvidenceRef, ...]
    recorded_at: str


class UnifiedPaperRuntime:
    def __init__(self, *, ledger_dsn, venue_dsn, event_dsn, journal_directory,
                 current_control=None, clock=clock_now):
        if not all((ledger_dsn, venue_dsn, event_dsn)) or venue_dsn == event_dsn:
            raise ValueError('separate provisioned PostgreSQL PAPER connections required')
        self.ledger_dsn, self.venue_dsn, self.event_dsn = ledger_dsn, venue_dsn, event_dsn
        self.directory = Path(journal_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.current_control, self.clock = current_control, clock
        # Synthetic venue commits deliberately perform repeated independent
        # PostgreSQL authority reads. This bounded PAPER profile has a 5s
        # permit; every use remains capped by current grant/intent/quote TTL.
        # Other domain/native compositions retain the default 1s profile.
        self.uow = UnitOfWork(self.connect, permit_ttl_ms=5000)
        self._control = local()

    def connect(self):
        conn = psycopg.connect(self.ledger_dsn)
        try:
            privileged = conn.execute("SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication "
                "OR r.rolbypassrls OR r.rolinherit OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() "
                "AND datdba=r.oid) OR has_schema_privilege(session_user,'ledger','CREATE') "
                "OR has_schema_privilege(session_user,'public','CREATE') "
                "OR has_database_privilege(session_user,current_database(),'TEMP') "
                "FROM pg_roles r WHERE rolname=session_user").fetchone()[0]
            if privileged:
                raise ValueError('RESTRICTED_PAPER_LEDGER_LOGIN_REQUIRED')
            conn.execute('SET ROLE ledger_owner')
            conn.commit()
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def control_guard(self):
        """Serialize console authority in the BUSINESS ledger lock namespace.

        Console and business databases may differ; acquiring a same-named lock
        in the console database would provide no fencing against this owner.
        """
        if getattr(self._control, 'depth', 0):
            self._control.depth += 1
            try:
                yield
            finally:
                self._control.depth -= 1
            return
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtextextended('console-owner:paper-local:SPOT',0))")
            conn.commit()
            self._control.depth = 1
            try:
                yield
            finally:
                self._control.depth = 0
                conn.execute("SELECT pg_advisory_unlock(hashtextextended('console-owner:paper-local:SPOT',0))")

    @staticmethod
    def provision(admin_dsn, *, initial_cash):
        """Explicit provisioning on an empty dedicated PAPER ledger only.

        This is not called by an API order, and never copies old balances/fills.
        The administrator applies migrations separately before this command.
        """
        cash = amount(initial_cash)
        with psycopg.connect(admin_dsn) as conn:
            name = conn.execute('SELECT current_database()').fetchone()[0]
            if not name.startswith(('paper_runtime_', 'paper_console_')):
                raise ValueError('fresh paper_runtime_* or paper_console_* database required')
            if conn.execute('SELECT 1 FROM ledger.account_bindings LIMIT 1').fetchone():
                raise ValueError('empty dedicated ledger required; existing accounts are never migrated')
            conn.execute('INSERT INTO ledger.collateral_mutex VALUES (%s,%s)', ('PAPER', GROUP))
            conn.execute('INSERT INTO ledger.account_bindings VALUES (%s,%s,%s,%s,NULL)',
                ('PAPER', ACCOUNT, 'SPOT', GROUP))
            for symbol in SYMBOLS:
                s = scope_for(symbol)
                conn.execute('INSERT INTO ledger.active_owners VALUES (%s,%s,%s,%s,%s,%s,1,1)',
                    ('PAPER', ACCOUNT, 'SPOT', s.instrument_uid, 'BOTH', OWNER))
            conn.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES (%s,%s,1,%s::jsonb,%s)', (scope_id(scope_for('BTCUSDT')), 'console:seed',
                    __import__('json').dumps({'initial_cash': number(cash), 'source': 'EXPLICIT_NEW_VIRTUAL_CAPITAL'}), uuid4().hex))

    def _ref(self, identifier, payload, at=None):
        return t.EvidenceRef(id=identifier, hash=canonical_hash(payload), environment=t.Environment.PAPER,
            source='SERVER_HELD_PAPER_LEDGER', visible_at=at or self.clock().utc_now)

    def _request(self, tx, command_id):
        return tx._latest('audit_events', 'console:request:' + command_id, RuntimeRequest)

    def _cash(self, tx):
        seed = tx.execute("SELECT payload->>'initial_cash' FROM ledger.audit_events WHERE record_id='console:seed'").fetchone()
        if seed is None:
            raise ApiError('PAPER_NOT_PROVISIONED', '请先显式初始化独立虚拟资金账本。', 503)
        cash = D(seed[0])
        for payload, side in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side' "
                "FROM ledger.fills f JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                "WHERE c.payload->>'venue_account_id'=%s", (ACCOUNT,)).fetchall():
            fill = decode(t.Fill, payload)
            notional = fill.quantity.value * fill.price.value
            cash += notional * (1 if side == 'SELL' else -1) - notional * D('.001')
        return cash

    def _book(self, tx, scope, *, refresh=False):
        old = tx._latest('book_snapshots', 'book', t.BookSnapshot)
        if old is not None and not refresh:
            return old
        positions = () if old is None else old.positions
        active = () if old is None else old.active_orders
        base = scope.instrument_uid.split(':')[-1].removesuffix('USDT')
        actual = sum((p.actual_quantity.value for p in positions), D(0))
        sequence = 1 if old is None else old.sequence + 1
        balances = (money(self._cash(tx)), money(actual, base))
        if old is not None and balances == old.balances:
            return old
        payload = {'positions': [domain_payload(p) for p in positions], 'cash': number(balances[0].amount),
            'sequence': sequence, 'active_orders': [domain_payload(p) for p in active]}
        ref = self._ref('console:book:' + uuid4().hex, payload)
        book = t.BookSnapshot(scope=scope, positions=positions, active_orders=active, reservations=(),
            balances=balances, sequence=sequence, basis_fingerprint=canonical_hash(payload),
            coverage=coverage(scope), evidence=(ref,))
        tx.projections.save_book(book, sequence - 1)
        self._record_target(tx, book)
        return book

    def _record_target(self, tx, book):
        commands = tuple(c for c in roots(tx) if c.state not in ('DURABLE_READY', 'ABORTED_UNSENT', 'NOT_SENT'))
        if not commands or book.active_orders or any(c.state not in ('VENUE_CONFIRMED', 'VENUE_REJECTED') for c in commands):
            return
        orders = tuple(tx._latest('order_projections', c.logical_client_order_id, t.OrderProjection) for c in commands)
        if any(o is None or o.lifecycle_state not in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED') for o in orders):
            return
        fills = tuple(decode(t.Fill, row[0]) for row in tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s',
            (scope_id(tx.scope),)).fetchall())
        report = t.VenueReport(coverage=book.coverage, orders=orders, positions=book.positions,
            balances=book.balances, fills=fills, evidence=book.evidence, next_cursor=None, as_of=self.clock().utc_now)
        validate_report(tx, book, report, commands, utc_timestamp(self.clock().utc_now))
        # The normalized synthetic account report is itself durably archived in
        # the same immutable audit row, derived exclusively from ledger events.
        ref = self._ref('console:terminal-report:' + uuid4().hex, domain_payload(report))
        record = TargetReport(book_hash=digest(book), report=report, archive_ref=ref,
            command_ids=tuple(c.command_id for c in commands))
        tx._append('audit_events', 'ip07:target-report:' + str(book.sequence), record, 0)

    def _spec(self, symbol):
        return t.InstrumentSpec(instrument_uid=scope_for(symbol).instrument_uid, venue='BINANCE',
            product=t.Product.SPOT, native_symbol=symbol, tick_size=t.Price(value=D('.00000001'), currency='USDT'),
            quantity_step=t.Quantity(value=D('.000000000001'), unit='BASE'),
            minimum_quantity=t.Quantity(value=D('.000000000001'), unit='BASE'),
            minimum_notional=money('0.00000001'), rule_revision=1, quote_currency='USDT', settlement_currency='USDT')

    def _inputs(self, tx, intent, book, request, command):
        reading = replace(self.clock(), utc_now=request.requested_at)
        scope, spec = tx.scope, self._spec(request.symbol)
        price = D(request.quote_price)
        ref = t.SnapshotRef(id=intent.snapshot_id, hash='0' * 64, environment=t.Environment.PAPER,
            source='SERVER_VALIDATED_PUBLIC_QUOTE', visible_at=request.quote_at)
        market = t.SnapshotPacket(ref=ref, scope=scope, quote=t.Quote(
            bid=t.Price(value=price, currency='USDT'), ask=t.Price(value=price, currency='USDT'),
            last=t.Price(value=price, currency='USDT'), occurred_at=request.quote_at, received_at=request.quote_at),
            private_refs=(), economic_refs=(), published_at=request.quote_at,
            revision_visible_at=request.quote_at, coverage=coverage(scope))
        from trading_system.market.snapshots import snapshot_content_hash
        market = replace(market, ref=replace(ref, hash=snapshot_content_hash(market)))
        context = t.DecisionContext(identity_id='console-authenticated-owner', environment=t.Environment.PAPER,
            deployment_id=request.authorization.authorization.deployment_id,
            authorization_id=intent.authorization_id, policy_revision=1, ownership_epoch=1,
            scopes=(scope,), strategy_revision=request.strategy_revision, decision_run_id=intent.decision_run_id,
            snapshot_ref=market.ref)
        session = t.SessionState(instrument_uid=scope.instrument_uid, session_id='PAPER-SYNTHETIC-24H',
            state='OPEN', observed_at=reading.utc_now, next_open_at=None, evidence=book.evidence[0])
        actual = sum((p.actual_quantity.value for p in book.positions), D(0))
        target = D(intent.target_quantity)
        reducing = target < actual
        fee = abs(target - actual) * price * (D('.001') if reducing else D('.002'))
        allocated_entry_fees, fee_quantity = D(0), D(0)
        for raw, side in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side' FROM ledger.fills f "
                "JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                "WHERE f.scope_id=%s ORDER BY f.processed_at,f.record_id", (scope_id(scope),)).fetchall():
            fill = decode(t.Fill, raw)
            if side == 'BUY':
                fee_quantity += fill.quantity.value
                allocated_entry_fees += fill.quantity.value * fill.price.value * D('.001')
            elif fee_quantity:
                allocated_entry_fees *= (fee_quantity - fill.quantity.value) / fee_quantity
                fee_quantity -= fill.quantity.value
        policy = ContractCodec().decode('risk-policy', dict(contract_version=VERSION, policy_id='console-paper-policy',
            revision=1, environment='PAPER', collateral_group_id=GROUP, valuation_currency='USDT',
            maximum_capital=request.budget, cash_buffer='0', maximum_order_notional=request.budget,
            maximum_planned_loss_per_intent=request.budget, maximum_total_planned_loss=request.budget,
            maximum_gross_exposure=request.budget, maximum_net_exposure=request.budget, maximum_open_positions=100,
            maximum_active_orders=100, maximum_leverage=1, daily_loss_fraction='1', maximum_drawdown_fraction='1',
            risk_day_timezone='Asia/Shanghai', maximum_quote_age_ms=request.maximum_age * 1000,
            maximum_entry_slippage_bps='20', maximum_model_cost_per_day={'amount': '0', 'currency': 'USD'},
            protection_policy='NATIVE_REQUIRED', daily_lock_release='REVIEW_REQUIRED', drawdown_lock_release='REVIEW_REQUIRED',
            native_risk_bypass=False, live_enabled=False))
        sides = tuple(WorkingOrder(logical_client_order_id=o.order.identity.logical_client_order_id,
            side=CommandRepository(tx).get(tx.execute("SELECT record_id FROM ledger.commands WHERE scope_id=%s "
                "AND sequence=1 AND payload->>'logical_client_order_id'=%s", (scope_id(scope),
                    o.order.identity.logical_client_order_id)).fetchone()[0]).canonical_payload.side) for o in book.active_orders)
        emergency = EmergencyReduction(maximum_quantity=t.Quantity(value=actual, unit='BASE'),
            allowed_order_types=('MARKET',), reference_price=t.Price(value=price, currency='USDT'),
            maximum_slippage_bps=D('20'), maximum_quote_age_ms=request.maximum_age * 1000) if target < actual else None
        risk = RiskInputs(intent_id=intent.intent_id, authorization_id=intent.authorization_id, owner_strategy_id=OWNER,
            ownership_epoch=1, policy_revision=1, scope=scope, collateral_group_id=GROUP,
            basis_fingerprint=book.basis_fingerprint, book_sequence=book.sequence, snapshot_ref=market.ref,
            clock=reading, expires_at=intent.valid_until, instrument=spec, session=session, multiplier=D(1),
            base_asset=request.symbol.removesuffix('USDT'), fee_reserve=(money(fee),), funding_reserve=(),
            existing_planned_loss=money('0'), capital_in_use=money(actual * price * D('1.001') + allocated_entry_fees), spendable=book.balances,
            fx_rates=(), marks=(ExposureMark(scope=scope, price=t.Price(value=price, currency='USDT'),
                multiplier=D(1), unit='BASE', related_group='console'),), order_sides=sides,
            group_limits=(GroupLimit(group_id='console', maximum_gross=money(request.budget)),),
            authorized_margin_mode='ISOLATED', observed_margin_mode='ISOLATED', authorized_position_mode='ONE_WAY',
            observed_position_mode='ONE_WAY', new_risk_allowed=True, allow_round_down=False, emergency=emergency,
            evidence=book.evidence)
        balance_caps = (money(request.initial_cash), money(actual, request.symbol.removesuffix('USDT')))
        return ReservationInputs(risk=risk, market=market, policy=policy,
            authorization=t.AuthorizationDecision(allowed=not grant_errors(request.authorization, utc_timestamp(reading.utc_now)),
                authorization_id=intent.authorization_id, rejection_codes=grant_errors(request.authorization, utc_timestamp(reading.utc_now)),
                evidence=request.authorization.evidence), command=command, group_capacity=balance_caps,
            strategy_capacity=balance_caps, buffer=tuple(money('0', m.currency) for m in balance_caps),
            target=TargetInputs(instrument=spec, expected_instrument=spec, clock=reading,
                deadline_monotonic_ns=reading.monotonic_ns + 30_000_000_000, session=session,
                order_sides=sides, context=context))

    def accept_proposal(self, proposal, key, market, maximum_age, data, strategy, assessment):
        """Bind an absolute target under the same owner fence as durable execution.

        Pending/unknown orders remain budget consumers. This adapter refuses a
        target change until they reconcile; it never subtracts unknown exposure.
        """
        from .decision_execution import proposal_target
        from trading_system.contracts.wire import ABSENT
        from trading_system.intents.semantic_validation import on_grid
        try:
            candidate = ContractCodec().decode('decision-proposal', proposal)
        except (ValueError, TypeError, KeyError, __import__('jsonschema').ValidationError) as exc:
            raise ApiError('INVALID_PLAN', '提案结构无效。', 409) from exc
        if candidate.action == 'HOLD':
            return {'status': 'NO_ACTION'}
        if candidate.action != 'SET_TARGET':
            raise ApiError('EXIT_UPDATE_UNSUPPORTED', '此执行适配器尚未支持修改退出责任。', 409)
        symbol = strategy['symbol']
        if candidate.instrument_ref not in (symbol, 'console-paper:' + symbol, 'BINANCE:SPOT:' + symbol):
            raise ApiError('INSTRUMENT_BINDING', '提案品种不匹配。', 409)
        if candidate.requested_leverage not in (ABSENT, 1) or candidate.target.side == 'SHORT':
            raise ApiError('CASH_LEVERAGE', '现货不允许杠杆或卖空。', 409)
        if candidate.entry.order_preference != 'MARKET' or not candidate.entry.allow_partial or candidate.exit_plan.stop_trigger != 'LAST':
            raise ApiError('EXECUTION_CAPABILITY_UNSUPPORTED', '提案执行或保护能力未准入。', 409)
        reading = self.clock()
        if not (utc_timestamp(reading.utc_now) < utc_timestamp(candidate.entry.entry_deadline)
                <= utc_timestamp(candidate.valid_until) and utc_timestamp(reading.utc_now) < utc_timestamp(candidate.exit_plan.review_due_at)):
            raise ApiError('STALE_DECISION', '提案或复查期限已过期。', 409)
        quote = next(q for q in market['quotes'] if q['symbol'] == symbol)
        price = D(quote['price'])
        target = proposal_target(proposal['target'], price)
        if target and (candidate.exit_plan.stop_price is None or not 0 < D(candidate.exit_plan.stop_price) < price
                or not on_grid(D(candidate.exit_plan.stop_price), self._spec(symbol).tick_size.value)):
            raise ApiError('INVALID_STOP', '止损方向或价格精度无效。', 409)
        if D(candidate.entry.maximum_entry_slippage_bps) > 20:
            raise ApiError('SLIPPAGE_LIMIT', '滑点超过 PAPER 适配器上限。', 409)
        with self.control_guard():
            projection = self.projection()
            pending = [o for o in projection['commands'] if o['symbol'] == symbol and
                o['order_status'] not in ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'ABORTED_UNSENT')]
            if any(o.get('operation') != 'SET_PROTECTION' for o in pending):
                # Existing verified protective orders also require the explicit
                # protected-reduction protocol; no blind cancellation here.
                raise ApiError('TARGET_RECONCILIATION_REQUIRED', '现有订单或保护仍承担可能成交责任；请先核验并调整。', 409)
            held = next((p for p in self.account_view()['positions'] if p['symbol'] == symbol), None)
            if held and held.get('strategy_id') != strategy['id']:
                raise ApiError('POSITION_OWNERSHIP', '持仓属于其他策略或人工。', 409)
            actual = D(held['quantity']) if held else D(0)
            delta = target - actual
            if not delta:
                return {'status': 'NO_ACTION'}
            if delta > 0 and strategy.get('mode') == 'HYBRID':
                rule = strategy.get('rule')
                if not rule or not (price <= D(rule['price']) if rule['operator'] == 'LTE' else price >= D(rule['price'])):
                    raise ApiError('RULE_NOT_MET', '混合策略的价格规则未满足。', 409)
            if delta > 0 and data['account']['paused']:
                raise ApiError('ENTRIES_PAUSED', '新增风险已暂停。', 409)
            budget = min(D(strategy['budget']), D(data['account']['initial_cash']) * D(strategy['target_pct']) / 100)
            if target * price > budget:
                raise ApiError('STRATEGY_BUDGET', '提案目标超过策略授权上限。', 409)
            payload = dict(symbol=symbol, side='BUY' if delta > 0 else 'SELL', quantity=format(abs(delta), 'f'),
                strategy_id=strategy['id'], decision_run_id=assessment['id'], rationale=candidate.rationale.summary,
                proposal=proposal, assessment_snapshot_id=assessment['snapshot_id'])
            if delta < 0 and pending:
                receipt = self.accept(payload, key, market, maximum_age, data, strategy)
                with self.uow.transaction(scope_for(symbol)) as tx:
                    record_id = 'console:proposal-adjustment:' + receipt['command_id']
                    if tx.execute('SELECT 1 FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
                            (scope_id(tx.scope), record_id)).fetchone() is None:
                        from .paper_maintenance import ReviewArchive
                        tx._append('audit_events', record_id, ReviewArchive(dict(payload=payload, strategy=strategy)), 0)
                return receipt
            if pending:
                raise ApiError('PROTECTION_ADJUSTMENT_REQUIRED', '已有保护责任需显式调整后再增加目标。', 409)
            return self._accept(payload, key, market, maximum_age, data, strategy)

    def analysis_evidence(self, symbol):
        with self.control_guard():
            projection = self.projection()
            terminal = ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'ABORTED_UNSENT')
            orders = [dict(side=o['side'], quantity=o['quantity'], filled_quantity=o['filled_quantity'], status=o['status'])
                      for o in projection['orders'] if o['symbol'] == symbol and o['status'] not in terminal]
            position = next((p for p in self.account_view()['positions'] if p['symbol'] == symbol), None)
            with self.uow.transaction(scope_for(symbol)) as tx:
                reservations = ReservationRepository(tx).group()
                reserved = sum((p.held + p.release_pending for p in reservations if p.reservation.asset == 'USDT'), D(0))
            return dict(portfolio=dict(active_orders=orders, reserved_funds=format(reserved, 'f'),
                protection_status=position['protection'] if position else 'NO_POSITION'),
                constraints=dict(execution_supported=True, protection_supported=True),
                costs=dict(fee_rate='0.001', fee_source='PAPER_POLICY', slippage_bps='20', complete=True))

    def accept(self, payload, key, market, maximum_age, data, strategy=None):
        with self.control_guard():
            if payload.get('side') == 'SELL':
                with self.uow.transaction(scope_for(payload['symbol'])) as tx:
                    retries = tx.execute("SELECT a.payload->>'cancel',a.payload->>'quantity' FROM ledger.audit_events a "
                        "JOIN ledger.audit_events r ON r.scope_id=a.scope_id AND r.record_id='console:request:'||(a.payload->>'cancel') "
                        "WHERE a.scope_id=%s AND a.record_id LIKE 'paper:adjustment:%%' AND r.payload->>'key'=%s",
                        (scope_id(tx.scope), key)).fetchall()
                    if retries:
                        identifier, saved_quantity = retries[0]
                        if D(saved_quantity) != amount(payload['quantity'], scale=18):
                            raise ApiError('IDEMPOTENCY_CONFLICT', '同一调整标识已用于其他数量。', 409)
                        return dict(self._receipt(self._request(tx, identifier)), quantity=saved_quantity,
                            operation='PROTECTED_REDUCTION', phase='CANCEL_CONFIRM_REQUIRED',
                            next_action='QUERY_CANCEL_THEN_REVIEW_AND_CONTINUE',
                            protection_responsibility='RETAINED_UNTIL_EXIT_OR_VERIFIED_REPLACEMENT')
                    book = self._book(tx, tx.scope, refresh=True)
                    plan = tx._latest('protection_plans', 'protection:' + scope_id(tx.scope), t.ProtectionPlan)
                    stop_id = (plan.native_order_ids[0].identity.logical_client_order_id.removeprefix('logical:')
                        if plan and plan.state == 'VERIFIED_NATIVE' and len(plan.native_order_ids) == 1 else None)
                    original = self._request(tx, stop_id) if stop_id else None
                    if strategy and original and original.strategy_id != strategy['id']:
                        raise ApiError('POSITION_OWNERSHIP', '当前保护属于其他策略或人工。', 409)
                if stop_id:
                    receipt = self.begin_reduction(stop_id, payload['quantity'], key)
                    return dict(receipt, quantity=payload['quantity'], operation='PROTECTED_REDUCTION',
                        phase='CANCEL_CONFIRM_REQUIRED', next_action='QUERY_CANCEL_THEN_REVIEW_AND_CONTINUE',
                        protection_responsibility='RETAINED_UNTIL_EXIT_OR_VERIFIED_REPLACEMENT')
            return self._accept(payload, key, market, maximum_age, data, strategy)

    def _accept(self, payload, key, market, maximum_age, data, strategy=None, *, proposal_adjustment=None):
        scope, reading = scope_for(payload['symbol']), self.clock()
        fingerprint = canonical_hash(payload)
        command_id = 'console-command:' + canonical_hash({'key': key, 'account': ACCOUNT})
        with self.uow.transaction(scope) as tx:
            existing = self._request(tx, command_id)
            if existing:
                if existing.fingerprint != fingerprint:
                    raise ApiError('IDEMPOTENCY_CONFLICT', '同一标识已用于其他执行请求。', 409)
                if CommandRepository(tx).get(command_id) is None:
                    raise ApiError('PAPER_REQUEST_NOT_ACCEPTED', '原请求未通过原子风控；没有可执行命令，请使用新的请求标识重新评估。', 409)
                return self._receipt(existing)
            book = self._book(tx, scope, refresh=True)
            seed = tx.execute("SELECT payload->>'initial_cash' FROM ledger.audit_events WHERE record_id='console:seed'").fetchone()
            if seed is None or D(seed[0]) != D(data['account']['initial_cash']):
                raise ApiError('PAPER_CAPITAL_MISMATCH', '控制台初始虚拟资金与独立业务账本不一致；禁止隐式迁移。', 409)
            reading = self.clock()
            actual = sum((p.actual_quantity.value for p in book.positions), D(0))
            if strategy:
                previous_entries = tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND sequence=1 "
                    "AND record_id LIKE 'console:request:%%' AND payload->>'side'='BUY' ORDER BY processed_at DESC LIMIT 1",
                    (scope_id(scope),)).fetchone()
                if actual and previous_entries and decode(RuntimeRequest, previous_entries[0]).strategy_id != strategy['id']:
                    raise ApiError('POSITION_OWNERSHIP', '当前持仓属于其他策略或人工；未创建命令。', 409)
                manual_reduction = tx.execute("SELECT payload->>'requested_at' FROM ledger.audit_events WHERE scope_id=%s "
                    "AND sequence=1 AND record_id LIKE 'console:request:%%' AND payload->>'mode'='MANUAL' "
                    "AND payload->>'side'='SELL' AND record_id<>%s ORDER BY processed_at DESC LIMIT 1", (scope_id(scope), 'console:request:'+proposal_adjustment if proposal_adjustment else '')).fetchone()
                if payload.get('decision_run_id') and manual_reduction and strategy.get('_activated_at', '') <= manual_reduction[0]:
                    raise ApiError('MANUAL_REDUCTION_REAUTH_REQUIRED', '人工减仓后旧目标已停用；需重新明确激活策略。', 409)
            quantity = amount(payload['quantity'])
            target = actual + (quantity if payload['side'] == 'BUY' else -quantity)
            if target < 0:
                raise ApiError('INSUFFICIENT_POSITION', '统一账本可减数量不足；不允许卖空。', 409)
            price = next(q for q in market['quotes'] if q['symbol'] == payload['symbol'])
            quote_at = price['as_of']
            quote_price = amount(price['price'], scale=18)
            if not 0 <= (utc_timestamp(reading.utc_now) - utc_timestamp(quote_at)).total_seconds() <= maximum_age:
                raise ApiError('QUOTE_UNAVAILABLE', '拒绝过期或乱序行情。', 409)
            expiry = min(utc_timestamp(reading.utc_now) + timedelta(seconds=30),
                utc_timestamp(quote_at) + timedelta(seconds=maximum_age)).isoformat()
            budget = min(D(strategy['budget']) if strategy else D(data['account']['initial_cash']),
                D(data['account']['initial_cash']) * D(data['risk']['max_position_pct']) / 100)
            authorization = t.Authorization(authorization_id='grant:' + command_id, owner_strategy_id=OWNER,
                environment=t.Environment.PAPER, deployment_id='console:' + (payload.get('strategy_id') or 'manual'),
                scopes=(scope,), maximum_capital=money(budget), policy_revision=1, protection_options=('NATIVE_REQUIRED',),
                model_cost_budget=money('0', 'USD'), valid_from=reading.utc_now, expires_at=expiry, revoked_at=None)
            ref = self._ref('authenticated-request:' + command_id, {'request': payload, 'key': key}, reading.utc_now)
            binding = AuthorizationBinding(authorization=authorization, identity_id='console-authenticated-owner',
                strategy_revision=strategy['revision'] if strategy else 1, ownership_epoch=1,
                queue_environment=t.Environment.PAPER, capabilities=('PAPER_SYNTHETIC_ONLY',), evidence=(ref,))
            stop_pct = (strategy.get('rule') or {}).get('stop_loss_pct', '5') if strategy else '5'
            stop = quote_price * (1 - D(stop_pct) / 100)
            intent = ContractCodec().decode('trade-intent', dict(contract_version=VERSION, intent_id='intent:' + command_id,
                environment='PAPER', owner_strategy_id=OWNER, strategy_revision=binding.strategy_revision,
                decision_run_id=payload.get('decision_run_id', 'manual:' + key), authorization_id=authorization.authorization_id,
                venue_account_id=ACCOUNT, collateral_group_id=GROUP, venue='BINANCE', product='SPOT',
                instrument_uid=scope.instrument_uid, venue_leg='BOTH', ownership_epoch=1, risk_policy_revision=1,
                basis_fingerprint=book.basis_fingerprint, snapshot_id='quote:' + command_id, as_of=quote_at,
                created_at=reading.utc_now, valid_until=expiry, evidence_refs=[ref.id], action='SET_TARGET',
                target_side='LONG' if target else 'FLAT', target_quantity=format(target, 'f'), quantity_unit='BASE',
                execution=dict(order_preference='MARKET', limit_price=None, maximum_entry_slippage_bps='20',
                    entry_deadline=expiry, allow_partial=True), max_planned_loss=dict(amount=format(budget, 'f'), currency='USDT'),
                requested_leverage=1, exit_plan=dict(protection_policy='NATIVE_REQUIRED', stop_trigger='LAST',
                    stop_price=format(stop, 'f'), review_due_at=expiry, maximum_holding_seconds=3600),
                proposal_hash=canonical_hash(payload), rationale_summary=payload.get('rationale', '受权人工 PAPER 请求'),
                supersedes_intent_id=None))
            if payload.get('proposal'):
                candidate = ContractCodec().decode('decision-proposal', payload['proposal'])
                expiry = min(utc_timestamp(expiry), utc_timestamp(candidate.valid_until),
                             utc_timestamp(candidate.entry.entry_deadline)).isoformat()
                intent = replace(intent, valid_until=expiry, proposal_hash=ContractCodec().hash(candidate),
                    snapshot_id=payload['assessment_snapshot_id'], evidence_refs=candidate.evidence_refs,
                    execution=replace(intent.execution, maximum_entry_slippage_bps=candidate.entry.maximum_entry_slippage_bps,
                        entry_deadline=expiry, allow_partial=candidate.entry.allow_partial),
                    exit_plan=replace(intent.exit_plan, stop_price=candidate.exit_plan.stop_price,
                        review_due_at=candidate.exit_plan.review_due_at,
                        maximum_holding_seconds=candidate.exit_plan.maximum_holding_seconds))
                # Keep the original wire proposal as immutable replay evidence.
                from .paper_maintenance import ReviewArchive
                tx._append('audit_events', 'console:proposal:' + command_id, ReviewArchive(payload['proposal']), 0)
            logical_id = 'logical:' + command_id
            body = {'side': payload['side'], 'order_type': 'MARKET', 'quantity': format(quantity, 'f'),
                'reduce_only': False, 'full_position_exit': False, 'position_side': 'BOTH'}
            command = ContractCodec().decode('execution-command', dict(contract_version=VERSION, command_id=command_id,
                attempt_id='attempt:' + command_id, intent_id=intent.intent_id, parent_command_id=None,
                environment='PAPER', venue_account_id=ACCOUNT, product='SPOT', instrument_uid=scope.instrument_uid,
                operation='CLOSE' if payload['side'] == 'SELL' else 'SUBMIT', logical_client_order_id=logical_id,
                venue_client_order_id=paper_client_order_id(scope, logical_id, 'CLOSE' if payload['side'] == 'SELL' else 'SUBMIT'),
                venue_order_id=None, target_order_id=None, canonical_payload=body, payload_hash=canonical_hash(body),
                ownership_epoch=1, execution_epoch=1, policy_revision=1, state='DURABLE_READY', outcome_provenance='NONE',
                durable_record_ref='pending-atomic-commit', created_at=reading.utc_now))
            request = RuntimeRequest(key=key, fingerprint=fingerprint, symbol=payload['symbol'], side=payload['side'], quantity=payload['quantity'], strategy_id=payload.get('strategy_id'),
                mode=strategy['mode'] if strategy and payload.get('decision_run_id') else 'MANUAL', strategy_revision=binding.strategy_revision,
                command_id=command_id, intent_id=intent.intent_id, requested_at=reading.utc_now, quote_at=quote_at,
                quote_price=str(quote_price), maximum_age=maximum_age, budget=format(budget, 'f'),
                initial_cash=data['account']['initial_cash'], risk_position_pct=data['risk']['max_position_pct'],
                risk_revision=data['risk']['revision'],
                stop_loss_pct=stop_pct, rationale=intent.rationale_summary, authorization=binding, intent=intent)
            inputs = self._inputs(tx, intent, book, request, command)
            context = inputs.target.context
            fence = IngressRevision(sequence=1, identity_id=context.identity_id, deployment_id=context.deployment_id,
                authorization_id=context.authorization_id, owner_strategy_id=OWNER, strategy_revision=context.strategy_revision,
                risk_policy_revision=1, ownership_epoch=1, enabled=True)
            tx._append('audit_events', revision_key(context), fence, 0)
            # The caller's request is immutable, but is not accepted until the
            # following IP-07 transaction commits the full risk/command bundle.
            tx._append('audit_events', 'console:request:' + command_id, request, 0)
        service = ReservationService(self.uow, scope, lambda tx, i, b: self._inputs(tx, i, b, request, command),
            clock=lambda: self.clock().utc_now, load_action_proof=self._action_proof,
            load_released_balances=self._released_balances)
        result = service.reserve(intent, book.sequence)
        if not result.accepted:
            raise ApiError('PAPER_RISK_REJECTED', '；'.join(result.rejection_codes), 409) from ValueError(result.rejection_codes)
        return self._receipt(request)

    def _released_balances(self, tx, book, originals):
        # The shared synthetic account has exactly one quote currency. Its
        # current cash is derived from the provisioned seed plus EVERY group's
        # durable fill and synthetic fee, not copied from a client snapshot.
        balances = {value.currency: value.amount for value in book.balances}
        symbol = tx.scope.instrument_uid.removeprefix('console-paper:')
        actual = D(0)
        for raw, side in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side' FROM ledger.fills f "
                "JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                "WHERE f.scope_id=%s", (scope_id(tx.scope),)).fetchall():
            actual += decode(t.Fill, raw).quantity.value * (1 if side == 'BUY' else -1)
        if (balances.get('USDT') != self._cash(tx) or balances.get(symbol.removesuffix('USDT')) != actual
                or actual != sum((p.actual_quantity.value for p in book.positions), D(0))):
            raise ValueError('CURRENT_SYNTHETIC_BALANCE_NOT_RECONCILED')
        ids = tuple(p.reservation.reservation_id for p in originals
            if p.reservation.state == t.ReservationState.RELEASED and p.held == p.reflected == p.release_pending == 0)
        return ReleasedBalanceAttestation(transaction_id=tx.transaction_id, book_hash=digest(book),
            reservation_ids=ids, evidence=book.evidence)

    def _receipt(self, request):
        return dict(id=request.command_id, command_id=request.command_id, symbol=request.symbol,
            side=request.side, status='PENDING',
            quantity=request.quantity, price=request.quote_price, notional='0', fee='0',
            strategy_id=request.strategy_id, created_at=request.requested_at)

    def latch_daily_lock(self, record):
        scope = scope_for('BTCUSDT')
        lock = t.RiskLock(lock_id=record['lock_id'], scope=scope, cause='DAILY_LOSS', state='ACTIVE',
            permission=t.RiskPermission.NO_NEW_RISK, required_evidence=('DAILY_LOSS_REVIEW',), raised_at=record['raised_at'])
        with self.uow.transaction(scope) as tx:
            if tx._latest('risk_locks', lock.lock_id, t.RiskLock) is None:
                tx.risk_locks.append(lock, 0)
                from .paper_maintenance import ReviewArchive
                tx._append('audit_events', 'console:daily-cause:' + lock.lock_id, ReviewArchive(record), 0)

    def cancel_entries(self):
        count = 0
        for command in self.projection()['commands']:
            if command['order_status'] in ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED'):
                self.cancel(command['command_id'], 'cancel-entry:' + command['command_id'])
                count += 1
        return count

    def cancel(self, command_id, key):
        with self.control_guard():
            return self._cancel(command_id, key)

    def pending_protected_adjustment(self, tx):
        from trading_system.risk.protection_adjustment import invalidate_obsolete
        rows = tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s "
            "AND record_id LIKE 'paper:adjustment:%%'", (scope_id(tx.scope),)).fetchall()
        for record, in rows:
            cancel_id = record['cancel']
            close_id = 'console-command:' + canonical_hash({'key': 'adjustment-close:' + cancel_id, 'account': ACCOUNT})
            close = CommandRepository(tx).get(close_id)
            if close is None and invalidate_obsolete(tx, record, owner=OWNER, now=self.clock().utc_now):
                continue
            order = tx._latest('order_projections', close.logical_client_order_id, t.OrderProjection) if close else None
            if order is None or order.lifecycle_state not in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'):
                return True
        return False

    def begin_reduction(self, command_id, quantity, key):
        """Explicit stop adjustment; caller must confirm cancel, then continue."""
        with self.control_guard():
            return self._cancel(command_id, key, adjustment_quantity=quantity)

    def continue_reduction(self, cancel_id, market, maximum_age, data):
        from trading_system.risk.protection_adjustment import adjustment, invalidation
        with self.control_guard():
            scope, symbol = self._scope_command(cancel_id)
            with self.uow.transaction(scope) as tx:
                record = adjustment(tx, cancel_id)
                if invalidation(tx, cancel_id):
                    raise ApiError('REDUCTION_ADJUSTMENT_INVALIDATED', '原调整数量已过时且已作废；保护责任仍在，请审查当前实际仓位。', 409)
                close_id = 'console-command:' + canonical_hash({'key': 'adjustment-close:' + cancel_id, 'account': ACCOUNT})
                existing = self._request(tx, close_id)
                if existing and CommandRepository(tx).get(close_id):
                    return self._receipt(existing)
                cancel = CommandRepository(tx).get(cancel_id)
                if record is None or cancel is None or cancel.operation != 'CANCEL':
                    raise ApiError('PROTECTION_ADJUSTMENT_REQUIRED', '继续减仓需要原有界保护调整命令。', 409)
                parent = CommandRepository(tx).get(cancel.parent_command_id)
                stop = tx._latest('order_projections', parent.logical_client_order_id, t.OrderProjection)
                book = self._book(tx, scope, refresh=True)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0))
                if (record is None or stop is None or stop.lifecycle_state != 'CANCELED'
                        or stop.command_certainty != 'CONFIRMED' or stop.pending_operation != 'NONE'
                        or actual != D(record['actual']) or book.active_orders
                        or stop.order.owner_strategy_id != OWNER or stop.order.ownership_epoch != cancel.ownership_epoch):
                    raise ApiError('REDUCTION_CANCEL_UNCONFIRMED', '先查询原保护单并确认撤销；数量或归属变化需要重新审查。', 409)
                if any(lock.permission == t.RiskPermission.RECOVERY_REQUIRED for lock in tx.risk_locks.applicable(scope)):
                    raise ApiError('REDUCTION_REVIEW_REQUIRED', '原撤单虽已确认，UNKNOWN 风险锁仍需独立审查后才能继续。', 409)
                raw = tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
                    (scope_id(scope), 'console:proposal-adjustment:' + cancel_id)).fetchone()
                archived = raw[0]['data'] if raw else None
            if archived:
                strategy = archived['strategy']
                if not getattr(self, 'console_store', None):
                    raise ApiError('STRATEGY_AUTHORITY_REQUIRED', '继续提案减仓需要当前策略授权。', 409)
                from sqlalchemy import select
                from .store import strategies
                with self.console_store.engine.connect() as connection:
                    current = connection.execute(select(strategies.c.data).where(strategies.c.id == strategy['id'])).scalar_one_or_none()
                if not current or current['revision'] != strategy['revision'] or current['status'] != 'ACTIVE':
                    raise ApiError('STALE_STRATEGY', '原提案策略已变更。', 409)
                payload = archived['payload']
                candidate = ContractCodec().decode('decision-proposal', payload['proposal'])
                if utc_timestamp(self.clock().utc_now) >= min(utc_timestamp(candidate.valid_until), utc_timestamp(candidate.entry.entry_deadline)):
                    raise ApiError('STALE_DECISION', '原提案已过期；保护责任仍需处理。', 409)
                return self._accept(payload, 'adjustment-close:' + cancel_id, market, maximum_age, data, current, proposal_adjustment=cancel_id)
            return self.accept(dict(symbol=symbol, side='SELL', quantity=record['quantity']),
                'adjustment-close:' + cancel_id, market, maximum_age, data)

    def _cancel(self, command_id, key, *, adjustment_quantity=None):
        scope, symbol = self._scope_command(command_id)
        now = self.clock()
        with self.uow.transaction(scope) as tx:
            parent = CommandRepository(tx).get(command_id)
            previous = self._request(tx, command_id)
            new_id = 'console-cancel:' + canonical_hash({'parent': command_id, 'key': key})
            existing = self._request(tx, new_id)
            if existing:
                from trading_system.risk.protection_adjustment import adjustment
                saved = adjustment(tx, new_id)
                if adjustment_quantity is not None and (saved is None or D(saved['quantity']) != amount(adjustment_quantity, scale=18)):
                    raise ApiError('IDEMPOTENCY_CONFLICT', '同一调整标识已用于其他数量。', 409)
                return self._receipt(existing)
            target = tx._latest('order_projections', parent.logical_client_order_id, t.OrderProjection)
            adjusting = adjustment_quantity is not None
            if ((not adjusting and (parent.operation != 'SUBMIT' or parent.canonical_payload.side != 'BUY')) or target is None
                    or target.lifecycle_state not in ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED')
                    or target.command_certainty != 'CONFIRMED' or target.pending_operation != 'NONE'):
                raise ApiError('CANCEL_TARGET_UNVERIFIED', '只能撤销已确认归属且没有未知操作的入场余单。', 409)
            book = self._book(tx, scope, refresh=True)
            if adjusting:
                from trading_system.risk.protection_adjustment import verified_adjustment, ProtectionAdjustment
                quantity = amount(adjustment_quantity, scale=18)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0))
                record = ProtectionAdjustment(parent=command_id, cancel=new_id, actual=str(actual), quantity=str(quantity))
                tx._append('audit_events', 'paper:adjustment:' + new_id, record, 0)
                candidate = replace(parent, command_id=new_id)
                if quantity % self._spec(symbol).quantity_step.value or not verified_adjustment(tx, candidate, parent, target, book):
                    raise ApiError('PROTECTION_ADJUSTMENT_UNVERIFIED', '仅允许对完整已验证保护责任发起有界减仓调整。', 409)
            expiry = (utc_timestamp(now.utc_now) + timedelta(seconds=30)).isoformat()
            auth = replace(previous.authorization.authorization, authorization_id='grant:' + new_id,
                valid_from=now.utc_now, expires_at=expiry)
            ref = self._ref('authenticated-cancel:' + new_id, {'target': domain_payload(target.order)}, now.utc_now)
            binding = replace(previous.authorization, authorization=auth, evidence=(ref,))
            intent = replace(previous.intent, intent_id='intent:' + new_id, authorization_id=auth.authorization_id,
                basis_fingerprint=book.basis_fingerprint, created_at=now.utc_now, valid_until=expiry,
                execution=replace(previous.intent.execution, entry_deadline=expiry),
                decision_run_id='manual-cancel:' + key, supersedes_intent_id=previous.intent.intent_id)
            logical = 'logical:' + new_id
            command = replace(parent, command_id=new_id, intent_id=intent.intent_id, attempt_id='attempt:' + new_id,
                parent_command_id=command_id, logical_client_order_id=logical,
                venue_client_order_id=paper_client_order_id(scope, logical, 'CANCEL'),
                operation='CANCEL', target_order_id=target.order.identity.venue_order_id, venue_order_id=None,
                state='DURABLE_READY', outcome_provenance='NONE', durable_record_ref='pending-atomic-control', created_at=now.utc_now)
            request = replace(previous, key=key, fingerprint=canonical_hash({'cancel': command_id}), mode='MANUAL',
                command_id=new_id, intent_id=intent.intent_id, requested_at=now.utc_now, intent=intent,
                authorization=binding, rationale=('保护调整：等待原单确认撤销及独立 UNKNOWN 审查，再显式继续减仓；保护责任持续。' if adjusting
                    else '受权撤销原入场余单；不释放未知风险预留。'))
            tx.intents.append(intent)
            tx._append('audit_events', 'console:request:' + new_id, request, 0)
            CommandRepository(tx).append(command)
        return self._receipt(request)

    def exit_owned(self, key, market, maximum_age, data):
        held = self.account_view()['positions']
        # Validate every quote before creating the first durable command.
        for position in held:
            quotes = [q for q in market.get('quotes', []) if q.get('symbol') == position['symbol']]
            if (len(quotes) != 1 or quotes[0].get('stale') is not False
                    or market.get('status') not in ('CONNECTED', 'PARTIAL')):
                raise ApiError('QUOTE_UNAVAILABLE', '批量退出缺少完整的新鲜行情；未创建命令。', 409)
            amount(quotes[0].get('price'), scale=18)
            if not 0 <= (utc_timestamp(self.clock().utc_now) - utc_timestamp(quotes[0]['as_of'])).total_seconds() <= maximum_age:
                raise ApiError('QUOTE_UNAVAILABLE', '批量退出包含过期行情；未创建命令。', 409)
        # One deterministic receipt per scope. A crash resumes original IDs.
        for position in held:
            self.accept(dict(symbol=position['symbol'], side='SELL', quantity=position['quantity'],
                strategy_id=position.get('strategy_id')), key + ':' + position['symbol'], market, maximum_age, data)
        return len(held)

    def protect(self, command_id, key):
        """Compile only the server-held exit plan for already-owned exposure."""
        with self.control_guard():
            scope, symbol = self._scope_command(command_id)
            with self.uow.transaction(scope) as tx:
                parent = CommandRepository(tx).get(command_id)
                original = self._request(tx, command_id)
                if parent is None or parent.operation not in ('SUBMIT', 'CLOSE'):
                    raise ApiError('PROTECTION_PARENT_REQUIRED', '保护需关联原已受权仓位命令。', 409)
                book = self._book(tx, scope, refresh=True)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0))
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
                if actual <= 0 or plan is None or plan.state not in ('GAP', 'REPAIRING'):
                    raise ApiError('PROTECTION_DUTY_REQUIRED', '当前没有已持久化且可修复的保护责任。', 409)
                if any(lock.permission in (t.RiskPermission.EXIT_ONLY, t.RiskPermission.RECOVERY_REQUIRED)
                        for lock in tx.risk_locks.applicable(scope)):
                    raise ApiError('PROTECTION_REPAIR_LOCKED', '当前风险锁仅允许减仓或恢复；未创建新的保护命令。', 409)
                now = self.clock()
                management_until = utc_timestamp(original.intent.created_at) + timedelta(seconds=original.intent.exit_plan.maximum_holding_seconds)
                if utc_timestamp(now.utc_now) >= management_until:
                    raise ApiError('MANAGEMENT_GRANT_EXPIRED', '原保护授权已超出持仓期限，需要独立退出审查。', 409)
                new_id = 'console-protect:' + canonical_hash({'parent': command_id, 'key': key})
                existing = self._request(tx, new_id)
                if existing:
                    return self._receipt(existing)
                expiry = min(utc_timestamp(now.utc_now) + timedelta(seconds=30), management_until).isoformat()
                auth = replace(original.authorization.authorization, authorization_id='grant:' + new_id,
                    valid_from=now.utc_now, expires_at=expiry)
                ref = self._ref('approved-exit-plan:' + new_id, {'plan': domain_payload(plan),
                    'source_request': original.fingerprint, 'actual': number(actual)}, now.utc_now)
                binding = replace(original.authorization, authorization=auth, evidence=(ref,))
                intent = replace(original.intent, intent_id='intent:' + new_id, authorization_id=auth.authorization_id,
                    basis_fingerprint=book.basis_fingerprint, created_at=now.utc_now, valid_until=expiry,
                    target_quantity=format(actual, 'f'), target_side='LONG',
                    execution=replace(original.intent.execution, entry_deadline=expiry),
                    exit_plan=replace(original.intent.exit_plan, stop_price=format(plan.stop_price.value, 'f')),
                    decision_run_id='protection-duty:' + new_id, supersedes_intent_id=original.intent.intent_id)
                logical = 'logical:' + new_id
                body = dict(side='SELL', order_type='STOP_MARKET', quantity=format(actual, 'f'),
                    trigger_price=format(plan.stop_price.value, 'f'), position_side='BOTH', reduce_only=False,
                    full_position_exit=False)
                raw = ContractCodec().encode(parent)
                raw.update(command_id=new_id, intent_id=intent.intent_id, attempt_id='attempt:' + new_id,
                    parent_command_id=command_id, logical_client_order_id=logical,
                    venue_client_order_id=paper_client_order_id(scope, logical, 'SET_PROTECTION'), operation='SET_PROTECTION',
                    target_order_id=None, venue_order_id=None, state='DURABLE_READY', outcome_provenance='NONE',
                    durable_record_ref='pending-atomic-protection', created_at=now.utc_now,
                    canonical_payload=body, payload_hash=canonical_hash(body))
                command = ContractCodec().decode('execution-command', raw)
                request = replace(original, key=key, fingerprint=canonical_hash({'protect': command_id, 'plan': domain_payload(plan)}),
                    mode='MAINTENANCE', side='SELL', quantity=format(actual, 'f'), command_id=new_id,
                    intent_id=intent.intent_id, requested_at=now.utc_now, intent=intent, authorization=binding,
                    rationale='原退出计划的已成交数量保护；提交 ACK 不代表保护已验证。')
                tx.intents.append(intent)
                tx._append('audit_events', 'console:request:' + new_id, request, 0)
                tx._append('audit_events', 'ip07:target-basis:' + new_id, TargetBasis(command_id=new_id,
                    book_sequence=book.sequence, actual=t.Quantity(value=actual, unit='BASE'), instrument=self._spec(symbol)), 0)
                CommandRepository(tx).append(command)
            return self._receipt(request)

    def account_view(self):
        with self.control_guard():
            return self._account_view()

    def _account_view(self):
        positions = []
        cash = None
        seed_cash = None
        for symbol in SYMBOLS:
            scope = scope_for(symbol)
            with self.uow.transaction(scope) as tx:
                if cash is None:
                    cash = self._cash(tx)
                    seed_cash = tx.execute("SELECT payload->>'initial_cash' FROM ledger.audit_events WHERE record_id='console:seed'").fetchone()[0]
                book = tx._latest('book_snapshots', 'book', t.BookSnapshot)
                if book is None or not book.positions:
                    continue
                quantity = sum((p.actual_quantity.value for p in book.positions), D(0))
                cost, running, fees, mark = D(0), D(0), D(0), D(0)
                strategy_id = None
                for raw, side, command_id in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side',c.record_id "
                        "FROM ledger.fills f JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                        "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                        "WHERE f.scope_id=%s ORDER BY f.processed_at,f.record_id", (scope_id(scope),)).fetchall():
                    fill = decode(t.Fill, raw)
                    q, price = fill.quantity.value, fill.price.value
                    mark = price
                    if side == 'BUY':
                        cost += q * price
                        running += q
                        fees += q * price * D('.001')
                        req = self._request(tx, command_id)
                        strategy_id = req.strategy_id if req else None
                    elif running:
                        cost *= (running - q) / running
                        fees *= (running - q) / running
                        running -= q
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
                protection = 'VERIFIED_SYNTHETIC' if plan and plan.state == 'VERIFIED_NATIVE' else 'GAP'
                positions.append(dict(id=book.positions[0].position_id, symbol=symbol, product='SPOT', side='LONG',
                    quantity=number(quantity), average_price=number(cost / quantity if quantity else D(0)),
                    mark_price=number(mark), strategy_id=strategy_id, owner='UNIFIED_PAPER', protection=protection,
                    _entry_fees=number(fees), updated_at=book.evidence[0].visible_at))
        return {'cash': number(cash or D(0)), 'positions': positions, 'initial_cash': seed_cash}

    def _action_proof(self, tx, command, intent, book):
        request = self._request(tx, command.command_id)
        if request is None:
            return None
        if any(o.command_certainty != 'CONFIRMED' or o.pending_operation != 'NONE' for o in book.active_orders):
            return None
        terms = []
        for order in book.active_orders:
            row = tx.execute("SELECT payload->'canonical_payload'->>'side' FROM ledger.commands WHERE scope_id=%s "
                "AND sequence=1 AND payload->>'logical_client_order_id'=%s", (scope_id(tx.scope),
                    order.order.identity.logical_client_order_id)).fetchone()
            if row is None:
                return None
            terms.append(WorkingTerms(order=order.order, side=row[0], evidence=book.evidence[0]))
        report = t.VenueReport(coverage=book.coverage, orders=book.active_orders, positions=book.positions,
            balances=book.balances, fills=(), evidence=book.evidence, next_cursor=None, as_of=book.evidence[0].visible_at)
        return ActionProof(transaction_id=tx.transaction_id, request_hash=command_hash(command), intent_hash=digest(intent),
            book_hash=digest(book), report=report, instrument=self._spec(request.symbol), orders=tuple(terms),
            valid_until=intent.valid_until)

    def _scope_command(self, command_id):
        # Scope is resolved from a server-created request, never a client scope.
        with self.connect() as conn:
            row = conn.execute("SELECT payload->>'symbol' FROM ledger.audit_events WHERE record_id=%s AND sequence=1",
                ('console:request:' + command_id,)).fetchone()
        if row is None or row[0] not in SYMBOLS:
            raise ApiError('COMMAND_NOT_FOUND', '统一 PAPER 命令不存在。', 404)
        return scope_for(row[0]), row[0]

    @contextmanager
    def _claim(self, shard):
        # Session advisory lock is held across both ledger transactions and the
        # separate synthetic venue commit. A crashed connection releases it.
        with self.connect() as conn:
            # Queue briefly behind console reads instead of losing every race
            # to a polling dashboard. The bounded wait preserves single-owner
            # exclusion while allowing a fresh command to reach dispatch.
            try:
                conn.execute("SET LOCAL lock_timeout = '2s'")
                conn.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',
                    ('console-owner:' + shard.venue_account_id + ':' + str(shard.product),))
            except psycopg.errors.LockNotAvailable:
                conn.rollback()
                raise ApiError('OWNER_ALREADY_RUNNING', '另一个 PAPER 执行者正在处理该账户。', 409) from None
            conn.commit()
            try:
                yield
            finally:
                conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',
                    ('console-owner:' + shard.venue_account_id + ':' + str(shard.product),))

    @contextmanager
    def _composition(self, scope, symbol, *, lose_ack=False):
        reading = self.clock()
        lease = t.OwnerLease(shard=t.ExecutionShard(environment=t.Environment.PAPER, venue_account_id=ACCOUNT,
            product=t.Product.SPOT, node_id='local-paper-postgres-owner', execution_epoch=1),
            instance_id=uuid4().hex, expires_at=(utc_timestamp(reading.utc_now) + timedelta(seconds=60)).isoformat(),
            deadline_monotonic_ns=reading.monotonic_ns + 60_000_000_000)
        active_authority = [None]

        def account_check(command_id):
            callback = getattr(self, 'current_account_risk', None)
            if callback:
                with self.uow.transaction(scope) as tx:
                    command = CommandRepository(tx).get(command_id)
                    request = self._request(tx, command_id)
                if command is None or request is None:
                    raise ValueError('SERVER_REQUEST_AUTHORITY_REQUIRED')
                callback(request, command)

        @contextmanager
        def authority(command_id):
            current = active_authority[0]
            if current is None or current.command.command_id != command_id:
                raise ValueError('OWNER_CURRENT_DURABLE_AUTHORITY_REQUIRED')
            with owner.guard(lease):
                account_check(command_id)
                with self.uow.transaction(scope) as tx:
                    dispatcher._check(tx, CommandRepository(tx).get(command_id), lease, current.permit)
                yield current

        @contextmanager
        def event_guard(event):
            # Called only by the authenticated synthetic controller operation.
            # It grants no execution or venue protection authority.
            with owner.guard(lease):
                yield scope

        @contextmanager
        def side_effect(command, permit, current_lease):
            with owner.guard(current_lease):
                active_authority[0] = BridgeAuthority(command=command, permit=permit, lease=lease,
                    ownership_epoch=command.ownership_epoch, policy_revision=command.policy_revision)
                try:
                    yield
                finally:
                    active_authority[0] = None

        def facts(tx, command, intent):
            request = self._request(tx, command.command_id)
            if request is None:
                raise ValueError('SERVER_REQUEST_AUTHORITY_REQUIRED')
            if self.current_control and command.operation not in ('CANCEL', 'SET_PROTECTION'):
                self.current_control(request, command)
            now = self.clock()
            if command.operation == 'SET_PROTECTION':
                book = tx.projections.book(scope)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0))
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
                quantity = D(command.canonical_payload.quantity)
                if (plan is None or plan.state not in ('GAP', 'REPAIRING') or command.canonical_payload.side != 'SELL'
                        or command.canonical_payload.order_type != 'STOP_MARKET' or not 0 < quantity <= actual
                        or quantity != plan.coverage_quantity.value
                        or command.canonical_payload.trigger_price != format(plan.stop_price.value, 'f')
                        or intent.exit_plan.stop_price != command.canonical_payload.trigger_price
                        or quantity % self._spec(symbol).quantity_step.value
                        or plan.stop_price.value % self._spec(symbol).tick_size.value):
                    raise ValueError('CURRENT_BOUNDED_PROTECTION_REQUIRED')
                for working in book.active_orders:
                    raw = tx.execute("SELECT payload->'canonical_payload'->>'side' FROM ledger.commands WHERE scope_id=%s "
                        "AND sequence=1 AND payload->>'logical_client_order_id'=%s", (scope_id(scope),
                            working.order.identity.logical_client_order_id)).fetchone()
                    if (raw is None or raw[0] != 'BUY' or working.order.owner_strategy_id != OWNER
                            or working.order.ownership_epoch != intent.ownership_epoch
                            or working.command_certainty != 'CONFIRMED' or working.pending_operation != 'NONE'):
                        raise ValueError('EXISTING_CLOSE_OR_PROTECTION_REQUIRES_RECONCILIATION')
                return DispatchFacts(binding=request.authorization, lease=lease, request_hash=command_hash(command),
                    valid_until=intent.valid_until, deadline_monotonic_ns=authority_deadline(now, request, quote_required=False),
                    checks=REQUIRED_CHECKS | {'OWNED_TARGET'})
            if command.operation == 'CANCEL':
                # No price is needed to remove an owned opening remainder.
                # Current exact ownership and pending state remain mandatory.
                book = tx.projections.book(scope)
                parent = CommandRepository(tx).get(command.parent_command_id)
                targets = [p for p in book.active_orders if p.order.identity.venue_order_id == command.target_order_id]
                from trading_system.risk.protection_adjustment import verified_adjustment
                adjusting = (len(targets) == 1 and verified_adjustment(tx, command, parent, targets[0], book))
                if (parent is None or (not adjusting and (parent.operation != 'SUBMIT' or parent.canonical_payload.side != 'BUY'))
                        or len(targets) != 1 or targets[0].command_certainty != 'CONFIRMED'
                        or targets[0].pending_operation != 'NONE' or targets[0].order.owner_strategy_id != OWNER
                        or targets[0].order.ownership_epoch != intent.ownership_epoch
                        or parent.logical_client_order_id != targets[0].order.identity.logical_client_order_id
                        or targets[0].leaves_quantity.value <= 0):
                    raise ValueError('CURRENT_CANCEL_TARGET_REQUIRED')
                return DispatchFacts(binding=request.authorization, lease=lease, request_hash=command_hash(command),
                    valid_until=intent.valid_until, deadline_monotonic_ns=authority_deadline(now, request, quote_required=False),
                    checks=REQUIRED_CHECKS | {'OWNED_TARGET'})
            if not 0 <= (utc_timestamp(now.utc_now) - utc_timestamp(request.quote_at)).total_seconds() <= request.maximum_age:
                raise ValueError('QUOTE_STALE')
            holds = tx.reservations.for_intent(intent.intent_id)
            if command.operation == 'SUBMIT' and not holds:
                raise ValueError('ATOMIC_RESERVATION_REQUIRED')
            original_risk = tx._latest('audit_events', 'ip07:risk:' + intent.intent_id, t.RiskDecision)
            if original_risk is None or original_risk.verdict != 'ALLOW':
                raise ValueError('ATOMIC_RISK_REQUIRED')
            book = tx.projections.book(scope)
            if book.active_orders:
                raise ValueError('WORKING_ORDER_REQUIRES_RECONCILIATION')
            actual = sum((p.actual_quantity.value for p in book.positions), D(0))
            signed = D(command.canonical_payload.quantity) * (1 if command.canonical_payload.side == 'BUY' else -1)
            desired = D(intent.target_quantity) * (-1 if intent.target_side == 'SHORT' else 1)
            if actual + signed != desired:
                raise ValueError('CURRENT_DELTA_CHANGED')
            inputs = self._inputs(tx, intent, book, request, command)
            inputs = replace(inputs, risk=replace(inputs.risk, clock=now))
            fresh_risk = RiskEvaluator(inputs.risk).evaluate(intent, book, inputs.market, inputs.policy)
            if fresh_risk.verdict != 'ALLOW' or fresh_risk.permitted_quantity != original_risk.permitted_quantity:
                raise ValueError('CURRENT_RISK_REJECTED')
            projected = ReservationRepository(tx).group()
            owned_holds = [p for p in projected if p.reservation.intent_id == intent.intent_id]
            expected_assets = {m.currency: m.amount for m in fresh_risk.reserved_assets}
            if {p.reservation.asset: p.held for p in owned_holds} != expected_assets:
                raise ValueError('CURRENT_BACKING_CHANGED')
            balances = {m.currency: m.amount for m in book.balances}
            for asset, required in expected_assets.items():
                others = sum((max(p.held - p.reflected, p.release_pending) for p in projected
                    if p.reservation.intent_id != intent.intent_id and p.reservation.asset == asset), D(0))
                if self._cash(tx) < 0 or balances.get(asset, D(0)) - others < required:
                    raise ValueError('CURRENT_CAPACITY_EXCEEDED')
            return DispatchFacts(binding=request.authorization, lease=lease, request_hash=command_hash(command),
                valid_until=intent.valid_until, deadline_monotonic_ns=authority_deadline(now, request),
                checks=REQUIRED_CHECKS | {'OWNED_TARGET'})

        engine = PaperEngine(scope, dsn=self.venue_dsn, authority_guard=authority,
            owner_strategy_id=OWNER, quantity_unit='BASE', price_currency='USDT',
            event_guard=event_guard, event_dsn=self.event_dsn, clock=self.clock)
        owner = ExecutionOwner(lease, journal_path=self.directory / 'paper-emergency.ndjson',
            engine=engine, claim=self._claim, clock=self.clock)
        dispatcher = CommandDispatcher(self.uow, scope, engine, load_facts=facts, owner_guard=owner.guard,
            clock=self.clock, load_action_proof=self._action_proof, side_effect_guard=side_effect,
            before_check=account_check)
        with owner.session(lease.shard):
            yield engine, dispatcher, lease

    def dispatch(self, command_id, *, lose_ack=False):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            # Every resumed command queries its original identity FIRST,
            # including a crash immediately after the READY commit. Only this
            # synthetic venue's successful complete absence permits first send.
            probe = engine.query(self._query_case(scope, command_id))
            if not probe.coverage.query_succeeded:
                return {'command_id': command_id, 'status': 'UNKNOWN', 'reason': 'ORIGINAL_ID_QUERY_UNAVAILABLE'}
            with self.uow.transaction(scope) as tx:
                command = CommandRepository(tx).get(command_id)
                if command is None:
                    raise ApiError('COMMAND_NOT_ACCEPTED', '原请求没有通过原子风控。', 409)
                if command.state == 'ABORTED_UNSENT':
                    return domain_payload(CommandRepository(tx).receipt(command_id))
                ready = command.state == 'DURABLE_READY'
            if probe.orders:
                if ready:
                    raise ApiError('DURABLE_BOUNDARY_INCONSISTENT', '原标识已被平台观察但本地仍未提交；需恢复审查，禁止重发。', 409)
                self._reconcile(engine, scope, command_id)
                with self.uow.transaction(scope) as tx:
                    return domain_payload(CommandRepository(tx).receipt(command_id))
            if not ready:
                return {'command_id': command_id, 'status': 'UNKNOWN', 'reason': 'MAY_HAVE_SENT_QUERY_ORIGINAL_ID_NO_RESEND'}
            if lose_ack:
                # Explicit local fault control; no real transport exists.
                engine._lose_ack_for = frozenset({command_id})
            receipt = dispatcher.dispatch(command_id, lease)
            self._reconcile(engine, scope, command_id)
        return domain_payload(receipt)

    def _query_case(self, scope, command_id):
        return t.ReconciliationCase(case_id='console-query:' + uuid4().hex,
            scope=scope, command_ids=(command_id,), position_ids=(), missing=(),
            query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60, minimum_interval_ms=1000, retry_after=None),
            attempts=0, next_query_at=self.clock().utc_now, state='OPEN')

    def reconcile(self, command_id):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            return self._reconcile(engine, scope, command_id)

    @staticmethod
    def _resolver_connection(dsn):
        conn = psycopg.connect(dsn)
        try:
            unsafe = conn.execute("SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication "
                "OR r.rolbypassrls OR r.rolinherit OR pg_has_role(session_user,'ledger_owner','MEMBER') "
                "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
                "OR has_schema_privilege(session_user,'ledger','CREATE') OR has_schema_privilege(session_user,'public','CREATE') "
                "OR has_database_privilege(session_user,current_database(),'TEMP') FROM pg_roles r WHERE rolname=session_user").fetchone()[0]
            if unsafe:
                raise ValueError('SEPARATE_RESTRICTED_REVIEW_LOGIN_REQUIRED')
            conn.execute('SET ROLE ledger_risk_resolver')
            conn.commit()
            return conn
        except BaseException:
            conn.close()
            raise

    def _ensure_lock_event(self, tx, lock, evidence):
        head = tx.execute('SELECT sequence FROM ledger.aggregate_heads WHERE scope_id=%s AND aggregate_id=%s',
            (scope_id(lock.scope), 'events:risk-lock:' + lock.lock_id)).fetchone()
        if head is None:
            # Earlier command/console paths persisted the cause projection but
            # had no event outbox. Reconstruct only that existing business fact,
            # with explicit current archive provenance, never a fill/venue fact.
            EventStore().append(tx, t.DomainEvent(event_id=uuid4().hex, scope=lock.scope,
                aggregate_id='risk-lock:' + lock.lock_id, revision=1, occurred_at=lock.raised_at,
                evidence=(evidence,), payload=lock), 0)

    def recover(self, command_id, *, resolver_dsn):
        """Explicit trusted review process; ordinary owner cannot clear locks.

        A separate restricted reviewer login is mandatory. The complete virtual
        venue history, account ledger and exact stop terms are queried before
        resolving only fulfilled non-daily causes. No credentials are retained
        on this runtime object or accepted from an HTTP request.
        """
        from trading_system.risk.locks import RiskLockService
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            source_by_scope, protection = {}, None
            for current_symbol in SYMBOLS:
                current_scope = scope_for(current_symbol)
                query_engine = engine if current_scope == scope else PaperEngine(current_scope,
                    dsn=self.venue_dsn, authority_guard=lambda command_id: None,
                    owner_strategy_id=OWNER, quantity_unit='BASE', price_currency='USDT', clock=self.clock)
                with self.uow.transaction(current_scope) as tx:
                    ids = [row[0] for row in tx.execute('SELECT record_id FROM ledger.commands WHERE scope_id=%s AND sequence=1',
                        (scope_id(current_scope),)).fetchall()]
                for identifier in ids:
                    self._reconcile(query_engine, current_scope, identifier)
                source = query_engine.query(replace(self._query_case(current_scope, command_id), command_ids=()))
                if not source.coverage.query_succeeded or not source.coverage.pagination_complete:
                    raise ApiError('RECOVERY_SOURCE_INCOMPLETE', '模拟平台完整查询失败；风险锁继续生效。', 409)
                source_by_scope[current_scope] = source
            with self.uow.transaction(scope) as tx:
                book = self._book(tx, scope, refresh=True)
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
            if plan and plan.native_order_ids:
                protective_id = plan.native_order_ids[0].identity.logical_client_order_id.removeprefix('logical:')
                protection = engine.protection_evidence(protective_id)
            else:
                protective_id = None
            source = source_by_scope[scope]
            source_refs = tuple(ref for report in source_by_scope.values() for ref in report.evidence)
            if protection:
                source_refs += (protection.evidence,)
            normalized = t.VenueReport(coverage=book.coverage, orders=source.orders, positions=book.positions,
                balances=book.balances, fills=source.fills, evidence=book.evidence + source_refs,
                next_cursor=None, as_of=self.clock().utc_now)
            archive = RuntimeRecoveryArchive(scope=scope, book_hash=digest(book), normalized_report=normalized,
                queried_commands=tuple(ids := self._command_ids(scope)), protection_command_id=protective_id,
                protection_quantity=protection.quantity if protection else None,
                protection_stop_price=protection.stop_price if protection else None,
                source_evidence=source_refs, recorded_at=self.clock().utc_now)
            identifier = 'console:recovery:' + uuid4().hex
            ref = self._ref(identifier, domain_payload(archive))
            with self.uow.transaction(scope) as tx:
                tx._append('audit_events', identifier, archive, 0)
                locks = tuple(lock for lock in tx.risk_locks.applicable(scope) if lock.scope == scope and lock.cause != 'DAILY_LOSS')
                for lock in locks:
                    self._ensure_lock_event(tx, lock, ref)

            review_diagnostics = []
            def verify(tx, lock, requirement, evidence):
                def refused(code):
                    review_diagnostics.append(code)
                    return False
                recorded = tx._latest('audit_events', identifier, RuntimeRecoveryArchive)
                current = tx.projections.book(scope)
                if (recorded != archive or digest(recorded) != ref.hash or digest(current) != archive.book_hash
                        or normalized.positions != current.positions or normalized.balances != current.balances
                        or {m.currency: m.amount for m in current.balances}.get('USDT') != self._cash(tx)):
                    return refused('RECOVERY_ARCHIVE_OR_BALANCE_MISMATCH')
                # Independently compare every venue fill and order with current
                # business facts; a submit ACK cannot satisfy this proof.
                ledger_fills = tuple(decode(t.Fill, row[0]) for row in tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s',
                    (scope_id(scope),)).fetchall())
                known_fills = {f.fill_id: f for f in ledger_fills}
                # Query visibility is fresh on each report. Event identity,
                # payload hash and every economic field must remain identical.
                source_fills = tuple(replace(f, evidence=replace(f.evidence,
                    visible_at=known_fills[f.fill_id].evidence.visible_at)) if f.fill_id in known_fills else f
                    for f in source.fills)
                if sorted(ledger_fills, key=lambda f: f.fill_id) != sorted(source_fills, key=lambda f: f.fill_id):
                    return refused('RECOVERY_FILL_HISTORY_MISMATCH')
                for order in source.orders:
                    saved = tx._latest('order_projections', order.order.identity.logical_client_order_id, t.OrderProjection)
                    if saved is None or replace(order, sequence=saved.sequence) != saved:
                        return refused('RECOVERY_ORDER_PROJECTION_MISMATCH')
                actual = sum((p.actual_quantity.value for p in current.positions), D(0))
                flat = actual == 0 and not current.active_orders
                verified_stop = (protection is not None and protection.quantity.value == actual and actual > 0
                    and plan is not None and protection.stop_price == plan.stop_price
                    and protection.order.order.owner_strategy_id == OWNER
                    and protection.order.order.ownership_epoch == 1)
                if requirement in ('PROTECTION_CONFIRMED', 'PROTECTION_OR_EXIT_CONFIRMED'):
                    return True if flat or verified_stop else refused('CURRENT_STOP_OR_FLAT_NOT_VERIFIED')
                if requirement == 'ORIGINAL_COMMAND_RECONCILIATION':
                    original_id = lock.lock_id.removeprefix('ip10:unknown:')
                    original = CommandRepository(tx).get(original_id)
                    return bool(original and original_id in archive.queried_commands
                        and original.state in ('VENUE_CONFIRMED', 'VENUE_REJECTED')
                        and all(o.command_certainty == 'CONFIRMED' and o.pending_operation == 'NONE' for o in source.orders))
                if requirement in ('command_terminal', 'fills_and_balance_reconciled'):
                    reservation_id = lock.lock_id.removeprefix('ip07:unknown:')
                    reservation = ReservationRepository(tx).get(reservation_id)
                    return reservation.reservation.state == t.ReservationState.RELEASED and reservation.held == 0
                return False

            resolver = RiskLockService(self.uow, clock=lambda: self.clock().utc_now, verify=verify,
                resolver_uow=UnitOfWork(lambda: self._resolver_connection(resolver_dsn)))
            results = []
            for lock in locks:
                with dispatcher.owner_guard(lease):
                    result = resolver.resolve(lock.lock_id, t.ResolutionEvidence(lock_id=lock.lock_id, scope=scope,
                        evidence=(ref,), authorization_id='local-trusted-paper-review'))
                results.append(domain_payload(result))
            return {'command_id': command_id, 'resolutions': results, 'review_diagnostics': review_diagnostics, 'simulation_notice': NOTICE}

    def _command_ids(self, scope):
        with self.uow.transaction(scope) as tx:
            return tuple(row[0] for row in tx.execute('SELECT record_id FROM ledger.commands WHERE scope_id=%s AND sequence=1',
                (scope_id(scope),)).fetchall())

    def tick(self):
        """A standalone owner process can outlive the browser/control API.

        The durable outbox is at least once. Each delivery re-reads command
        state, queries its original identity, and sends only an eligible READY
        request. No model calls or automatic synthetic fills occur here.
        """
        with self.connect() as connection:
            commands = connection.execute("SELECT record_id FROM ledger.commands WHERE sequence=1 ORDER BY processed_at").fetchall()
        results = []
        for command_id, in commands:
            try:
                results.append(self.dispatch(command_id))
            except Exception as exc:
                # Preserve durable pending/unknown work; worker error text may
                # contain database secrets, so expose the type only.
                results.append({'command_id': command_id, 'status': 'RECONCILIATION_REQUIRED', 'error_type': type(exc).__name__})
        from .paper_maintenance import maintain
        results.extend(maintain(self))
        return results

    def inject(self, command_id, payload):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            with self.uow.transaction(scope) as tx:
                command = CommandRepository(tx).get(command_id)
                if command is None:
                    raise ApiError('COMMAND_NOT_ACCEPTED', '命令尚未通过原子风控。', 409)
                client_id = (CommandRepository(tx).get(command.parent_command_id).venue_client_order_id
                    if command.operation == 'CANCEL' else command.venue_client_order_id)
            event = SyntheticOrderEvent(event_id=payload['event_id'], scope=scope,
                venue_client_order_id=client_id, status=payload['status'],
                cumulative_quantity=amount(payload['cumulative_quantity'], positive=False),
                price=amount(payload['price'], scale=18) if payload.get('price') is not None else None,
                occurred_at=payload.get('occurred_at') or self.clock().utc_now)
            engine.inject(event)
            return self._reconcile(engine, scope, command_id)

    def _reconcile(self, engine, scope, command_id):
        now = self.clock().utc_now
        report = engine.query(self._query_case(scope, command_id))
        if not report.coverage.query_succeeded or not report.orders:
            # Query absence is never NOT_SENT proof and never permits a resend.
            return {'command_id': command_id, 'status': 'UNKNOWN', 'reason': 'QUERY_ORIGINAL_ID_REQUIRED'}
        protection_proof = engine.protection_evidence(command_id)
        with self.uow.transaction(scope) as tx:
            known_plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
        if protection_proof is None and known_plan and known_plan.native_order_ids:
            known_id = known_plan.native_order_ids[0].identity.logical_client_order_id.removeprefix('logical:')
            protection_proof = engine.protection_evidence(known_id)
        with self.uow.transaction(scope) as tx:
            repo = CommandRepository(tx)
            command = repo.get(command_id)
            request = self._request(tx, command_id)
            order_unknown = report.orders[0].pending_operation != 'NONE' and command.operation == 'CANCEL'
            repo.record_outcome(t.CommandReceipt(receipt_id='query:' + uuid4().hex, command_id=command_id,
                status=t.CommandState.UNKNOWN if order_unknown else t.CommandState.VENUE_CONFIRMED,
                reason='PAPER_ORIGINAL_ID_QUERY', existing_result_ref=report.evidence[0].id))
            for fill in report.fills:
                if tx.execute('SELECT 1 FROM ledger.fills WHERE scope_id=%s AND record_id=%s',
                        (scope_id(scope), fill.fill_id)).fetchone():
                    continue
                EventStore().append(tx, t.DomainEvent(event_id='event:' + fill.fill_id, scope=scope,
                    aggregate_id=fill.fill_id, revision=1, occurred_at=fill.occurred_at,
                    evidence=(fill.evidence,), payload=fill), 0)
            current = report.orders[0]
            if current.lifecycle_state in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'):
                current = replace(current, leaves_quantity=t.Quantity(value=D(0), unit='BASE'))
            logical_id = current.order.identity.logical_client_order_id
            old = tx._latest('order_projections', logical_id, t.OrderProjection)
            if old is None or replace(current, sequence=old.sequence) != old:
                tx._append('order_projections', logical_id,
                    replace(current, sequence=1 if old is None else old.sequence + 1), 0 if old is None else old.sequence)
            # Derive actual holdings solely from durable fills and original sides.
            actual = D(0)
            for raw, side in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side' FROM ledger.fills f "
                    "JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                    "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                    "WHERE f.scope_id=%s", (scope_id(scope),)).fetchall():
                fill = decode(t.Fill, raw)
                actual += fill.quantity.value * (1 if side == 'BUY' else -1)
            book = self._book(tx, scope)
            plan_id = 'protection:' + scope_id(scope)
            plan = tx._latest('protection_plans', plan_id, t.ProtectionPlan)
            position = t.ManagedPosition(position_id='position:' + scope_id(scope), scope=scope,
                actual_quantity=t.Quantity(value=actual, unit='BASE'), owner_strategy_id=OWNER,
                owner_book_id='console-authoritative-paper', ownership_epoch=1, cost_evidence=coverage(scope),
                realized_pnl=None, unrealized_valuation=None, active_exit_plan_id=plan_id, risk_locks=())
            if actual:
                verified = (protection_proof is not None and protection_proof.order.order.owner_strategy_id == OWNER
                    and protection_proof.order.order.ownership_epoch == position.ownership_epoch
                    and protection_proof.order.order.identity.scope == scope
                    and protection_proof.quantity.value == actual
                    and protection_proof.stop_price == (plan.stop_price if plan else t.Price(value=D(request.intent.exit_plan.stop_price), currency='USDT')))
                from trading_system.risk.protection_adjustment import intentional_cancel
                protection_failed = (command.operation == 'SET_PROTECTION'
                    and current.lifecycle_state in ('REJECTED', 'EXPIRED', 'CANCELED')
                    and not intentional_cancel(tx, command, current))
                next_plan = t.ProtectionPlan(plan_id=plan_id, mode='NATIVE', scope=scope,
                    coverage_quantity=t.Quantity(value=actual, unit='BASE'),
                    stop_price=plan.stop_price if plan else t.Price(value=D(request.intent.exit_plan.stop_price), currency='USDT'), stop_trigger='LAST',
                    native_order_ids=(protection_proof.order.order,) if verified else (), monitor_id=None,
                    state=t.ProtectionState.VERIFIED_NATIVE if verified else t.ProtectionState.GAP,
                    last_verified_at=now if verified else None,
                    coverage_gap_since=None if verified else ((plan.coverage_gap_since if plan else None) or now),
                    replacement_revision=1 if plan is None else plan.replacement_revision + 1,
                    on_gap_policy='NO_NEW_RISK_REQUIRE_REPAIR_OR_EXIT')
                if (plan is None or replace(next_plan, replacement_revision=plan.replacement_revision,
                        last_verified_at=plan.last_verified_at) != plan):
                    tx._append('protection_plans', plan_id, next_plan, 0 if plan is None else plan.replacement_revision)
                cause = 'PROTECTION_GAP' if protection_failed else 'PROTECTION_PENDING'
                lock_id = cause.lower() + ':' + scope_id(scope)
                prior_lock = tx._latest('risk_locks', lock_id, t.RiskLock)
                if prior_lock is not None and prior_lock.state == 'RESOLVED':
                    lock_id += ':' + request.intent.intent_id
                if not verified and tx._latest('risk_locks', lock_id, t.RiskLock) is None:
                    permission, required = POLICIES[cause]
                    tx.risk_locks.append(t.RiskLock(lock_id=lock_id, scope=scope, cause=cause, state='ACTIVE',
                        permission=permission, required_evidence=required, raised_at=now), 0)
            elif plan is not None and plan.state != t.ProtectionState.FLAT_VERIFIED:
                tx._append('protection_plans', plan_id, replace(plan, coverage_quantity=t.Quantity(value=D(0), unit='BASE'),
                    state=t.ProtectionState.FLAT_VERIFIED, native_order_ids=(), last_verified_at=now,
                    coverage_gap_since=None, replacement_revision=plan.replacement_revision + 1), plan.replacement_revision)
            position_sequence = tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s AND record_id=%s',
                (scope_id(scope), position.position_id)).fetchone()[0]
            tx._append('positions', position.position_id, position, position_sequence)
            latest_rows = tx.execute('SELECT DISTINCT ON (record_id) payload FROM ledger.order_projections '
                'WHERE scope_id=%s ORDER BY record_id,sequence DESC', (scope_id(scope),)).fetchall()
            active = tuple(o for row in latest_rows if (o := decode(t.OrderProjection, row[0])).lifecycle_state
                not in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'))
            ref = self._ref('console-reduce:' + uuid4().hex, {'report': domain_payload(report), 'cash': number(self._cash(tx))})
            payload = {'actual': number(actual), 'orders': [domain_payload(o) for o in active], 'evidence': ref.id}
            next_book = replace(book, positions=(position,) if actual else (), active_orders=active,
                balances=(money(self._cash(tx)), money(actual, request.symbol.removesuffix('USDT'))),
                sequence=book.sequence + 1, basis_fingerprint=canonical_hash(payload), evidence=(ref,))
            if (next_book.positions == book.positions and next_book.active_orders == book.active_orders
                    and next_book.balances == book.balances):
                if all(0 <= (utc_timestamp(now) - utc_timestamp(e.visible_at)).total_seconds() < 30
                        for e in book.evidence):
                    next_book = book
                else:
                    # A complete new original-ID query renews observation evidence,
                    # while unchanged economics retain their target fingerprint.
                    next_book = replace(next_book, basis_fingerprint=book.basis_fingerprint)
                    tx.projections.save_book(next_book, book.sequence)
                    self._record_target(tx, next_book)
            else:
                tx.projections.save_book(next_book, book.sequence)
                self._record_target(tx, next_book)
            release = []
            if current.lifecycle_state in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED') and current.command_certainty == 'CONFIRMED':
                root_intent_id = CommandRepository(tx).get(command.parent_command_id).intent_id if command.operation == 'CANCEL' else command.intent_id
                release = [r for r in tx.reservations.for_intent(root_intent_id)
                    if ReservationRepository(tx).get(r.reservation_id).held]
            book_sequence = next_book.sequence
        for reservation in release:
            evidence = t.ReservationEvidence(reservation_id=reservation.reservation_id,
                venue_freeze_ref=ref, reflected_amount=D(0), command_certainty='CONFIRMED', terminal_verified=True,
                watermark=ref.id + ':' + ref.hash)
            proof = ReconciliationProof(evidence=evidence, scope=scope, intent_id=reservation.intent_id,
                asset=reservation.asset, command_refs=reservation.command_refs, book_sequence=book_sequence, obligations_cleared=True)
            ReservationService(self.uow, scope, None, clock=lambda: self.clock().utc_now,
                load_proof=lambda tx, ev: proof).reconcile(evidence)
        return {'command_id': command_id, 'status': str(current.lifecycle_state),
            'filled_quantity': number(current.filled_quantity.value), 'leaves_quantity': number(current.leaves_quantity.value),
            'protection_state': 'VERIFIED_SYNTHETIC' if actual and verified else 'GAP' if actual else 'NO_POSITION', 'simulation_notice': NOTICE}

    def projection(self):
        with self.control_guard():
            return self._projection()

    def _projection(self):
        targets, commands, locks, protections, orders_view, adjustments = [], [], [], [], [], []
        for symbol in SYMBOLS:
            scope = scope_for(symbol)
            with self.uow.transaction(scope) as tx:
                from trading_system.risk.protection_adjustment import adjustment_views
                adjustments.extend(dict(view, symbol=symbol) for view in adjustment_views(tx, ACCOUNT))
                records = tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s "
                    "AND record_id LIKE 'console:request:%%' AND sequence=1 ORDER BY processed_at", (scope_id(scope),)).fetchall()
                book = tx._latest('book_snapshots', 'book', t.BookSnapshot)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0)) if book else D(0)
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
                if plan:
                    protections.append({**domain_payload(plan), 'state': 'VERIFIED_SYNTHETIC' if plan.state == 'VERIFIED_NATIVE' else str(plan.state),
                        'provenance': 'UNIFIED_PAPER_SYNTHETIC'})
                latest_target = None
                from .paper_accounting import realized_by_order
                accounting_fills = []
                for fill_raw, fill_side, fill_command_id in tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side',c.record_id "
                        "FROM ledger.fills f JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
                        "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
                        "WHERE f.scope_id=%s ORDER BY f.processed_at,f.record_id", (scope_id(scope),)).fetchall():
                    fill = decode(t.Fill, fill_raw)
                    accounting_fills.append(dict(side=fill_side, quantity=fill.quantity.value, price=fill.price.value,
                        command_id=fill_command_id))
                realized = realized_by_order(accounting_fills)
                for raw, in records:
                    request = decode(RuntimeRequest, raw)
                    command = CommandRepository(tx).get(request.command_id)
                    if command is None:
                        continue
                    target_command = CommandRepository(tx).get(command.parent_command_id) if command.operation == 'CANCEL' else command
                    projection = tx._latest('order_projections', target_command.logical_client_order_id, t.OrderProjection)
                    filled = projection.filled_quantity.value if projection else D(0)
                    leaves = projection.leaves_quantity.value if projection else D(command.canonical_payload.quantity)
                    status = str(projection.lifecycle_state) if projection else ('PENDING' if command.state == 'DURABLE_READY' else 'UNKNOWN')
                    if command.state == 'ABORTED_UNSENT':
                        status, filled, leaves = 'ABORTED_UNSENT', D(0), D(0)
                    if projection and projection.pending_operation == 'CANCEL':
                        status = 'CANCEL_UNKNOWN'
                    commands.append(dict(command_id=command.command_id, symbol=symbol, state=command.state, operation=command.operation,
                        order_status=status, quantity=command.canonical_payload.quantity, filled_quantity=number(filled),
                        leaves_quantity=number(leaves), reason=request.rationale))
                    if command.operation not in ('CANCEL', 'SET_PROTECTION'):
                        latest_target = dict(symbol=symbol, strategy_id=request.strategy_id, target_quantity=request.intent.target_quantity,
                        actual_quantity=number(actual), in_flight_quantity=number(leaves if status not in ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED') else D(0)),
                        protection_state=('VERIFIED_SYNTHETIC' if plan.state == 'VERIFIED_NATIVE' else str(plan.state)) if plan and actual else 'NO_POSITION',
                        protected_quantity=number(plan.coverage_quantity.value) if actual and plan and plan.state == 'VERIFIED_NATIVE' else '0',
                        command_id=command.command_id, reason=request.rationale + ('；人工减仓后需重新激活旧自动目标。' if request.mode == 'MANUAL' and request.side == 'SELL' else ''))
                    # Protective fills are real synthetic cash movements too.
                    # Keep target attribution separate from executed history.
                    if command.operation != 'CANCEL' and (command.operation != 'SET_PROTECTION' or filled > 0):
                        fill_rows = tx.execute("SELECT payload FROM ledger.fills WHERE scope_id=%s "
                            "AND payload->'order'->>'logical_client_order_id'=%s", (scope_id(scope), command.logical_client_order_id)).fetchall()
                        notional = sum((decode(t.Fill, row[0]).quantity.value * decode(t.Fill, row[0]).price.value for row in fill_rows), D(0))
                        orders_view.append(dict(id=command.command_id, command_id=command.command_id, symbol=symbol,
                            provenance='UNIFIED_PAPER_SYNTHETIC',
                            side=command.canonical_payload.side, quantity=command.canonical_payload.quantity,
                            filled_quantity=number(filled), price=number(notional / filled) if filled else request.quote_price,
                            notional=number(notional), fee=number(notional * D('.001')), status=status,
                            realized_pnl=number(realized[command.command_id]) if command.command_id in realized else None,
                            strategy_id=request.strategy_id, created_at=request.requested_at))
                if latest_target:
                    targets.append(latest_target)
                locks.extend(domain_payload(lock) for lock in tx.risk_locks.applicable(scope))
        return dict(mode='UNIFIED_PAPER', simulation_notice=NOTICE, targets=targets, commands=commands,
            orders=orders_view, positions=self.account_view()['positions'], protection_plans=protections,
            protection_adjustments=adjustments,
            risk_locks=list({l['lock_id']: l for l in locks}.values()))

