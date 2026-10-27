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
from trading_system.risk.reservations import ReconciliationProof
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.risk.target_guard import TargetReport, validate_report, roots, digest
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


class UnifiedPaperRuntime:
    def __init__(self, *, ledger_dsn, venue_dsn, event_dsn, journal_directory,
                 current_control=None, clock=clock_now):
        if not all((ledger_dsn, venue_dsn, event_dsn)) or venue_dsn == event_dsn:
            raise ValueError('separate provisioned PostgreSQL PAPER connections required')
        self.ledger_dsn, self.venue_dsn, self.event_dsn = ledger_dsn, venue_dsn, event_dsn
        self.directory = Path(journal_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.current_control, self.clock = current_control, clock
        self.uow = UnitOfWork(self.connect)

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
        fee = abs(target - actual) * price * D('.002')
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
            existing_planned_loss=money('0'), capital_in_use=money(actual * price), spendable=book.balances,
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

    def accept(self, payload, key, market, maximum_age, data, strategy=None):
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
            reading = self.clock()
            actual = sum((p.actual_quantity.value for p in book.positions), D(0))
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
                mode=strategy['mode'] if strategy else 'MANUAL', strategy_revision=binding.strategy_revision,
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
            clock=lambda: self.clock().utc_now, load_action_proof=self._action_proof)
        result = service.reserve(intent, book.sequence)
        if not result.accepted:
            raise ApiError('PAPER_RISK_REJECTED', '；'.join(result.rejection_codes), 409) from ValueError(result.rejection_codes)
        return self._receipt(request)

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

    def cancel_entries(self):
        raise ApiError('PAPER_CANCEL_NOT_QUALIFIED', '尚未完成统一 PAPER 撤单控制验证；未变更任何订单或预留。', 409)

    def exit_owned(self, key, market, maximum_age, data):
        held = self.account_view()['positions']
        # One deterministic receipt per scope. A crash resumes original IDs.
        for position in held:
            self.accept(dict(symbol=position['symbol'], side='SELL', quantity=position['quantity'],
                strategy_id=position.get('strategy_id')), key + ':' + position['symbol'], market, maximum_age, data)
        return len(held)

    def account_view(self):
        positions = []
        cash = None
        for symbol in SYMBOLS:
            scope = scope_for(symbol)
            with self.uow.transaction(scope) as tx:
                if cash is None:
                    cash = self._cash(tx)
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
                positions.append(dict(id=book.positions[0].position_id, symbol=symbol, product='SPOT', side='LONG',
                    quantity=number(quantity), average_price=number(cost / quantity if quantity else D(0)),
                    mark_price=number(mark), strategy_id=strategy_id, owner='UNIFIED_PAPER', protection='GAP',
                    _entry_fees=number(fees), updated_at=book.evidence[0].visible_at))
        return {'cash': number(cash or D(0)), 'positions': positions}

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
            locked = conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                ('console-owner:' + shard.venue_account_id + ':' + str(shard.product),)).fetchone()[0]
            conn.commit()
            if not locked:
                raise ApiError('OWNER_ALREADY_RUNNING', '另一个 PAPER 执行者正在处理该账户。', 409)
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

        @contextmanager
        def authority(command_id):
            current = active_authority[0]
            if current is None or current.command.command_id != command_id:
                raise ValueError('OWNER_CURRENT_DURABLE_AUTHORITY_REQUIRED')
            with owner.guard(lease):
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
            if self.current_control:
                self.current_control(request, command)
            now = self.clock()
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
                valid_until=intent.valid_until, deadline_monotonic_ns=now.monotonic_ns + 1_000_000_000,
                checks=REQUIRED_CHECKS | {'OWNED_TARGET'})

        engine = PaperEngine(scope, dsn=self.venue_dsn, authority_guard=authority,
            owner_strategy_id=OWNER, quantity_unit='BASE', price_currency='USDT',
            event_guard=event_guard, event_dsn=self.event_dsn, clock=self.clock)
        owner = ExecutionOwner(lease, journal_path=self.directory / 'paper-emergency.ndjson',
            engine=engine, claim=self._claim, clock=self.clock)
        dispatcher = CommandDispatcher(self.uow, scope, engine, load_facts=facts, owner_guard=owner.guard,
            clock=self.clock, load_action_proof=self._action_proof, side_effect_guard=side_effect)
        with owner.session(lease.shard):
            yield engine, dispatcher, lease

    def dispatch(self, command_id, *, lose_ack=False):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            if lose_ack:
                # Explicit local fault control; no real transport exists.
                engine._lose_ack_for = frozenset({command_id})
            receipt = dispatcher.dispatch(command_id, lease)
            self._reconcile(engine, scope, command_id)
        return domain_payload(receipt)

    def reconcile(self, command_id):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            return self._reconcile(engine, scope, command_id)

    def inject(self, command_id, payload):
        scope, symbol = self._scope_command(command_id)
        with self._composition(scope, symbol) as (engine, dispatcher, lease):
            with self.uow.transaction(scope) as tx:
                command = CommandRepository(tx).get(command_id)
                if command is None:
                    raise ApiError('COMMAND_NOT_ACCEPTED', '命令尚未通过原子风控。', 409)
            event = SyntheticOrderEvent(event_id=payload['event_id'], scope=scope,
                venue_client_order_id=command.venue_client_order_id, status=payload['status'],
                cumulative_quantity=amount(payload['cumulative_quantity'], positive=False),
                price=amount(payload['price'], scale=18) if payload.get('price') is not None else None,
                occurred_at=payload.get('occurred_at') or self.clock().utc_now)
            engine.inject(event)
            return self._reconcile(engine, scope, command_id)

    def _reconcile(self, engine, scope, command_id):
        now = self.clock().utc_now
        report = engine.query(t.ReconciliationCase(case_id='console-query:' + uuid4().hex,
            scope=scope, command_ids=(command_id,), position_ids=(), missing=(),
            query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60, minimum_interval_ms=1000, retry_after=None),
            attempts=0, next_query_at=now, state='OPEN'))
        if not report.coverage.query_succeeded or not report.orders:
            # Query absence is never NOT_SENT proof and never permits a resend.
            return {'command_id': command_id, 'status': 'UNKNOWN', 'reason': 'QUERY_ORIGINAL_ID_REQUIRED'}
        with self.uow.transaction(scope) as tx:
            repo = CommandRepository(tx)
            command = repo.get(command_id)
            request = self._request(tx, command_id)
            repo.record_outcome(t.CommandReceipt(receipt_id='query:' + uuid4().hex, command_id=command_id,
                status=t.CommandState.VENUE_CONFIRMED, reason='PAPER_ORIGINAL_ID_QUERY', existing_result_ref=report.evidence[0].id))
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
            old = tx._latest('order_projections', command.logical_client_order_id, t.OrderProjection)
            if old is None or replace(current, sequence=old.sequence) != old:
                tx._append('order_projections', command.logical_client_order_id,
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
                next_plan = t.ProtectionPlan(plan_id=plan_id, mode='NATIVE', scope=scope,
                    coverage_quantity=t.Quantity(value=actual, unit='BASE'),
                    stop_price=t.Price(value=D(request.intent.exit_plan.stop_price), currency='USDT'), stop_trigger='LAST',
                    native_order_ids=(), monitor_id=None, state=t.ProtectionState.GAP,
                    last_verified_at=None, coverage_gap_since=plan.coverage_gap_since if plan else now,
                    replacement_revision=1 if plan is None else plan.replacement_revision + 1,
                    on_gap_policy='NO_NEW_RISK_REQUIRE_REPAIR_OR_EXIT')
                if plan is None or plan.coverage_quantity != next_plan.coverage_quantity:
                    tx._append('protection_plans', plan_id, next_plan, 0 if plan is None else plan.replacement_revision)
                lock_id = 'protection-gap:' + scope_id(scope)
                if tx._latest('risk_locks', lock_id, t.RiskLock) is None:
                    permission, required = POLICIES['PROTECTION_GAP']
                    tx.risk_locks.append(t.RiskLock(lock_id=lock_id, scope=scope, cause='PROTECTION_GAP', state='ACTIVE',
                        permission=permission, required_evidence=required, raised_at=now), 0)
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
            tx.projections.save_book(next_book, book.sequence)
            self._record_target(tx, next_book)
            release = []
            if current.lifecycle_state in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED') and current.command_certainty == 'CONFIRMED':
                release = [r for r in tx.reservations.for_intent(command.intent_id)
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
            'protection_state': 'GAP' if actual else 'NO_POSITION', 'simulation_notice': NOTICE}

    def projection(self):
        targets, commands, locks = [], [], []
        for symbol in SYMBOLS:
            scope = scope_for(symbol)
            with self.uow.transaction(scope) as tx:
                records = tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s "
                    "AND record_id LIKE 'console:request:%%' AND sequence=1 ORDER BY processed_at", (scope_id(scope),)).fetchall()
                book = tx._latest('book_snapshots', 'book', t.BookSnapshot)
                actual = sum((p.actual_quantity.value for p in book.positions), D(0)) if book else D(0)
                for raw, in records:
                    request = decode(RuntimeRequest, raw)
                    command = CommandRepository(tx).get(request.command_id)
                    if command is None:
                        continue
                    projection = tx._latest('order_projections', command.logical_client_order_id, t.OrderProjection)
                    filled = projection.filled_quantity.value if projection else D(0)
                    leaves = projection.leaves_quantity.value if projection else D(command.canonical_payload.quantity)
                    status = str(projection.lifecycle_state) if projection else ('PENDING' if command.state == 'DURABLE_READY' else 'UNKNOWN')
                    commands.append(dict(command_id=command.command_id, symbol=symbol, state=command.state,
                        order_status=status, quantity=command.canonical_payload.quantity, filled_quantity=number(filled),
                        leaves_quantity=number(leaves), reason=request.rationale))
                    targets.append(dict(symbol=symbol, strategy_id=request.strategy_id, target_quantity=request.intent.target_quantity,
                        actual_quantity=number(actual), in_flight_quantity=number(leaves if status not in ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED') else D(0)),
                        protection_state='UNVERIFIED' if actual else 'NO_POSITION', protected_quantity='0',
                        command_id=command.command_id, reason=request.rationale))
                locks.extend(domain_payload(lock) for lock in tx.risk_locks.applicable(scope))
        return dict(mode='UNIFIED_PAPER', simulation_notice=NOTICE, targets=targets, commands=commands,
            orders=commands, positions=[], protection_plans=[], risk_locks=list({l['lock_id']: l for l in locks}.values()))
