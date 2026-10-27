"""Controlled synthetic venue, durable in a separately provisioned PAPER database.

No transport, credential reader, exchange API or native engine is constructed.
The caller is the trusted execution owner. Its authority guard must reload the
committed command/permit and current authorization under the owner lock, closing
all business-ledger transactions before yielding. This adapter's transaction is
the simulated venue side effect, independently committed from that ledger.

Submission acknowledges reception only. Fills/cancellation/amendment require an
explicit server-authorized synthetic event; neither prices nor liquidity are
invented. Reports intentionally lack account/protection coverage and cannot by
themselves unlock recovery, release reservations, or verify native protection.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec, ExecutionCommand
from trading_system.engine.nautilus.bridge import BridgeDenied, _clock, validate_bridge_authority


SOURCE = 'PAPER_SYNTHETIC_NOT_EXCHANGE_EVIDENCE'
TERMINAL = frozenset({'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'})


def _difference(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(len(left.as_tuple().digits), len(right.as_tuple().digits)) + abs(
            left.as_tuple().exponent - right.as_tuple().exponent) + 1
        return left - right


def paper_client_order_id(scope: t.ScopeKey, logical_id: str, operation: str) -> str:
    """Deterministic scoped ID, not a qualified exchange-specific ID encoding."""
    if scope.environment != t.Environment.PAPER or not isinstance(logical_id, str) or not logical_id:
        raise BridgeDenied('PAPER_ONLY_EXPLICIT_ID_REQUIRED')
    if operation not in {'SUBMIT', 'CLOSE', 'SET_PROTECTION', 'CANCEL', 'AMEND'}:
        raise BridgeDenied('UNSUPPORTED_OPERATION')
    return 'P1' + canonical_hash({'scope': domain_payload(scope), 'logical_id': logical_id,
                                  'operation': operation})[:32]


@dataclass(frozen=True, kw_only=True)
class SyntheticOrderEvent:
    event_id: str
    scope: t.ScopeKey
    venue_client_order_id: str
    status: str
    cumulative_quantity: Decimal
    occurred_at: str
    price: Decimal | None = None
    # AMENDED must refer to the specific pending durable amend command.
    command_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class SyntheticProtectionEvidence:
    order: t.OrderProjection
    stop_price: t.Price
    quantity: t.Quantity
    evidence: t.EvidenceRef


class PaperEngine:
    def __init__(self, scope: t.ScopeKey, *, dsn: str, authority_guard,
                 owner_strategy_id: str, quantity_unit: str, price_currency: str,
                 event_guard=None, event_dsn=None, clock=_clock, lose_ack_for=frozenset()):
        if (not isinstance(scope, t.ScopeKey) or scope.environment != t.Environment.PAPER
                or not scope.venue_account_id or not scope.instrument_uid
                or scope.venue_leg not in {'BOTH', 'LONG', 'SHORT'}):
            raise BridgeDenied('PAPER_ONLY_EXPLICIT_SCOPE_REQUIRED')
        if (not dsn or not callable(authority_guard) or not owner_strategy_id
                or not quantity_unit or not price_currency):
            raise BridgeDenied('TRUSTED_PAPER_COMPOSITION_REQUIRED')
        self.scope, self._dsn = scope, dsn
        self._event_dsn = event_dsn
        self._scope_key = canonical_json(domain_payload(scope))
        self._authority_guard, self._event_guard, self._clock = authority_guard, event_guard, clock
        self._owner_strategy_id = owner_strategy_id
        self._quantity_unit, self._price_currency = quantity_unit, price_currency
        self._lose_ack_for = frozenset(lose_ack_for)

    @property
    def live_enabled(self):
        return False

    def enable_native_path(self, path):
        raise BridgeDenied('NATIVE_PATH_NOT_QUALIFIED')

    def _connect(self):
        return self._open(self._dsn, controller=False)

    def _event_connect(self):
        if not self._event_dsn:
            raise BridgeDenied('SEPARATE_SYNTHETIC_CONTROLLER_DATABASE_ROLE_REQUIRED')
        return self._open(self._event_dsn, controller=True)

    @staticmethod
    def _open(dsn, *, controller):
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True,
            options='-c search_path=pg_catalog')
        try:
            role = conn.execute('''SELECT NOT starts_with(current_database(),'paper_engine_')
                OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='paper_sim')
                OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                OR r.rolreplication OR r.rolbypassrls OR r.rolinherit
                OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid)
                OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid)
                OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relowner=r.oid)
                OR (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relkind='r') <> 3
                OR has_schema_privilege(current_user,'public','CREATE')
                OR has_database_privilege(current_user,current_database(),'TEMP') AS privileged,
                has_table_privilege(current_user,'public.paper_engine_commands','INSERT') AS commands,
                has_table_privilege(current_user,'public.paper_engine_events','INSERT') AS events,
                has_column_privilege(current_user,'public.paper_engine_orders','filled','UPDATE')
                OR has_column_privilege(current_user,'public.paper_engine_orders','lifecycle','UPDATE')
                OR has_column_privilege(current_user,'public.paper_engine_orders','quantity','UPDATE') AS economic_update,
                has_column_privilege(current_user,'public.paper_engine_orders','filled','INSERT') AS fabricated_initial_fill,
                has_table_privilege(current_user,'public.paper_engine_orders','DELETE,TRUNCATE')
                OR has_table_privilege(current_user,'public.paper_engine_commands','DELETE,TRUNCATE')
                OR has_table_privilege(current_user,'public.paper_engine_events','DELETE,TRUNCATE') AS destructive
                FROM pg_roles r WHERE r.rolname=current_user''',
                (['paper_engine_orders', 'paper_engine_commands', 'paper_engine_events'],
                 ['paper_engine_orders', 'paper_engine_commands', 'paper_engine_events'])).fetchone()
            if (role is None or role['privileged'] or role['destructive']
                    or role['fabricated_initial_fill'] or role['economic_update'] != controller
                    or role['commands'] != (not controller) or role['events'] != controller):
                raise BridgeDenied('RESTRICTED_SEPARATE_PAPER_DATABASE_ROLES_REQUIRED')
            conn.autocommit = False
            return conn
        except BaseException:
            conn.close()
            raise

    def _check_authority(self, command, permit):
        # Guards are reentrant trusted owner guards. Reload, do not re-use the
        # snapshot captured before a database lock wait. The outer guard still
        # serializes revocation through transaction commit.
        with self._authority_guard(command.command_id) as current:
            validate_bridge_authority(command, permit, current, scope=self.scope, clock=self._clock)

    def submit(self, command, permit):
        return self._write(command, permit, {'SUBMIT', 'CLOSE', 'SET_PROTECTION'})

    def cancel(self, command, permit):
        return self._write(command, permit, {'CANCEL'})

    def amend(self, command, permit):
        return self._write(command, permit, {'AMEND'})

    def _write(self, command, permit, operations):
        if not isinstance(command, ExecutionCommand) or not isinstance(permit, t.DurablePermit):
            raise BridgeDenied('DURABLE_PERMIT_REQUIRED')
        if command.operation not in operations:
            raise BridgeDenied('WRONG_ENGINE_METHOD')
        with self._authority_guard(command.command_id) as authority:
            validate_bridge_authority(command, permit, authority, scope=self.scope, clock=self._clock)
            expected = paper_client_order_id(self.scope, command.logical_client_order_id, command.operation)
            if command.venue_client_order_id != expected or command.venue_order_id is not None:
                raise BridgeDenied('REGISTERED_IDENTITY_MISMATCH')
            body = ContractCodec().encode(command)
            request_hash = canonical_hash(body)
            try:
                with self._connect() as conn:
                    # The unique indexes also fence concurrent adapter instances;
                    # no memory-only consumed set is relied on after restart.
                    exists = conn.execute('SELECT 1 FROM public.paper_engine_commands WHERE scope_key=%s '
                        'AND (command_id=%s OR client_id=%s)',
                        (self._scope_key, command.command_id, expected)).fetchone()
                    if exists:
                        raise BridgeDenied('COMMAND_ALREADY_OBSERVED_QUERY_SAME_ID')
                    validate_bridge_authority(command, permit, authority, scope=self.scope, clock=self._clock)
                    if command.operation in {'CANCEL', 'AMEND'}:
                        target = conn.execute('SELECT * FROM public.paper_engine_orders WHERE scope_key=%s '
                            'AND order_id=%s FOR UPDATE', (self._scope_key, command.target_order_id)).fetchone()
                        self._check_authority(command, permit)
                        if (target is None or target['ownership_epoch'] != command.ownership_epoch
                                or target['owner_strategy_id'] != self._owner_strategy_id
                                or target['lifecycle'] in TERMINAL or target['pending'] != 'NONE'):
                            raise BridgeDenied('CURRENT_OWNED_WORKING_TARGET_REQUIRED')
                        if command.operation == 'AMEND':
                            quantity = self._quantity(body['canonical_payload'].get('quantity'))
                            if quantity < target['filled'] or quantity <= 0:
                                raise BridgeDenied('AMEND_BELOW_ACTUAL_FILL')
                        target_client = target['client_id']
                        self._check_authority(command, permit)
                        conn.execute('UPDATE public.paper_engine_orders SET pending=%s, pending_command_id=%s, certainty=%s, '
                            'revision=revision+1 WHERE scope_key=%s AND client_id=%s',
                            (command.operation, command.command_id, 'UNKNOWN', self._scope_key, target_client))
                    else:
                        quantity = self._quantity(body['canonical_payload'].get('quantity'))
                        if quantity <= 0 or command.target_order_id is not None:
                            raise BridgeDenied('EXPLICIT_BASE_QUANTITY_REQUIRED')
                        target_client = expected
                        self._check_authority(command, permit)
                        conn.execute('INSERT INTO public.paper_engine_orders '
                            '(environment,scope_key,client_id,order_id,logical_id,owner_strategy_id,'
                            'ownership_epoch,quantity_unit,price_currency,quantity) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            ('PAPER', self._scope_key, expected, 'paper-order:' + expected,
                             command.logical_client_order_id, self._owner_strategy_id,
                             command.ownership_epoch, self._quantity_unit, self._price_currency, quantity))
                    self._check_authority(command, permit)
                    conn.execute('INSERT INTO public.paper_engine_commands '
                        '(environment,scope_key,command_id,client_id,target_client_id,operation,request_hash,request) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', ('PAPER', self._scope_key, command.command_id,
                            expected, target_client, command.operation, request_hash, Jsonb(body)))
                    # Any statement can wait on a table/index/foreign-key lock.
                    # A late denial rolls back ALL staged venue mutations.
                    self._check_authority(command, permit)
            except psycopg.errors.UniqueViolation as exc:
                raise BridgeDenied('IDENTITY_ALREADY_OBSERVED_QUERY_SAME_ID') from exc
        # A simulated timeout occurs AFTER the separate simulated venue commit.
        if command.command_id in self._lose_ack_for:
            raise TimeoutError('SYNTHETIC_ACK_LOST_QUERY_ORIGINAL_CLIENT_ID')
        pending = command.operation in {'CANCEL', 'AMEND'}
        return t.CommandReceipt(receipt_id='paper-ack:' + command.command_id,
            command_id=command.command_id,
            status=t.CommandState.UNKNOWN if pending else t.CommandState.VENUE_CONFIRMED,
            reason=('PAPER_OPERATION_PENDING_SYNTHETIC_EVENT' if pending else
                'PAPER_REQUEST_ACKNOWLEDGED_NOT_FILLED_OR_PROTECTION_VERIFIED'),
            existing_result_ref='paper-command:' + request_hash)

    @staticmethod
    def _quantity(value):
        if not isinstance(value, (str, Decimal)):
            raise BridgeDenied('EXACT_DECIMAL_REQUIRED')
        try:
            result = Decimal(value)
        except Exception as exc:
            raise BridgeDenied('EXACT_DECIMAL_REQUIRED') from exc
        if not result.is_finite():
            raise BridgeDenied('FINITE_DECIMAL_REQUIRED')
        return result

    def inject(self, event: SyntheticOrderEvent) -> bool:
        """Trusted scenario control only; duplicate identical events return False."""
        if self._event_guard is None:
            raise BridgeDenied('SYNTHETIC_EVENT_CONTROLLER_REQUIRED')
        with self._event_guard(event) as allowed_scope:
            if (not isinstance(event, SyntheticOrderEvent) or allowed_scope != self.scope
                    or event.scope != self.scope or not event.event_id or not event.venue_client_order_id
                    or not isinstance(event.cumulative_quantity, Decimal)
                    or not event.cumulative_quantity.is_finite() or event.cumulative_quantity < 0
                    or (event.price is not None and (not isinstance(event.price, Decimal)
                        or not event.price.is_finite() or event.price <= 0))):
                raise BridgeDenied('INVALID_SYNTHETIC_EVENT')
            now = self._clock()
            if (now.clock_health != 'HEALTHY'
                    or utc_timestamp(event.occurred_at) > utc_timestamp(now.utc_now)):
                raise BridgeDenied('INVALID_EVENT_TIME')
            payload = domain_payload(event)
            payload_hash = canonical_hash(payload)
            with self._event_connect() as conn:
                order = conn.execute('SELECT * FROM public.paper_engine_orders WHERE scope_key=%s '
                    'AND client_id=%s FOR UPDATE', (self._scope_key, event.venue_client_order_id)).fetchone()
                self._check_event_control(event)
                if order is None:
                    raise BridgeDenied('ORIGINAL_CLIENT_ID_REQUIRED')
                previous = conn.execute('SELECT payload_hash FROM public.paper_engine_events '
                    'WHERE scope_key=%s AND event_id=%s', (self._scope_key, event.event_id)).fetchone()
                if previous:
                    if previous['payload_hash'] != payload_hash:
                        raise BridgeDenied('EVENT_ID_CONFLICT')
                    return False
                delta = _difference(event.cumulative_quantity, order['filled'])
                if delta < 0 or event.cumulative_quantity > order['quantity']:
                    raise BridgeDenied('CUMULATIVE_FILL_OUT_OF_BOUNDS')
                status = event.status
                if order['lifecycle'] in TERMINAL:
                    raise BridgeDenied('TERMINAL_ORDER_CANNOT_TRANSITION')
                if status in {'PARTIALLY_FILLED', 'FILLED'}:
                    if (delta <= 0 or event.price is None
                            or (status == 'FILLED') != (event.cumulative_quantity == order['quantity'])):
                        raise BridgeDenied('INVALID_FILL_TRANSITION')
                    lifecycle, pending, certainty = status, order['pending'], order['certainty']
                    if status == 'FILLED':
                        pending, certainty = 'NONE', 'CONFIRMED'
                elif delta != 0 or event.price is not None:
                    raise BridgeDenied('NON_FILL_EVENT_CANNOT_CREATE_FILL')
                elif status == 'CANCEL_UNKNOWN':
                    if order['pending'] != 'CANCEL':
                        raise BridgeDenied('PENDING_CANCEL_REQUIRED')
                    lifecycle, pending, certainty = order['lifecycle'], 'CANCEL', 'UNKNOWN'
                elif status in {'PROTECTION_VERIFIED', 'PROTECTION_FAILED'}:
                    stop = conn.execute('SELECT request FROM public.paper_engine_commands WHERE scope_key=%s '
                        'AND target_client_id=%s AND operation=%s',
                        (self._scope_key, order['client_id'], 'SET_PROTECTION')).fetchone()
                    if stop is None or order['filled'] != 0 or order['pending'] != 'NONE':
                        raise BridgeDenied('CURRENT_UNFILLED_PROTECTIVE_ORDER_REQUIRED')
                    terms = stop['request']['canonical_payload']
                    if (terms.get('side') != 'SELL' or terms.get('order_type') != 'STOP_MARKET'
                            or self._quantity(terms.get('trigger_price')) <= 0):
                        raise BridgeDenied('EXACT_PROTECTIVE_TERMS_REQUIRED')
                    lifecycle = order['lifecycle'] if status == 'PROTECTION_VERIFIED' else 'REJECTED'
                    pending, certainty = 'NONE', 'CONFIRMED'
                elif status == 'AMENDED':
                    amendment = conn.execute('SELECT request FROM public.paper_engine_commands WHERE scope_key=%s '
                        'AND command_id=%s AND target_client_id=%s AND operation=%s',
                        (self._scope_key, event.command_id, order['client_id'], 'AMEND')).fetchone()
                    if (order['pending'] != 'AMEND' or amendment is None
                            or order['pending_command_id'] != event.command_id):
                        raise BridgeDenied('PENDING_AMEND_COMMAND_REQUIRED')
                    quantity = self._quantity(amendment['request']['canonical_payload']['quantity'])
                    if quantity <= order['filled']:
                        raise BridgeDenied('AMEND_MUST_RETAIN_WORKING_QUANTITY')
                    self._check_event_control(event)
                    conn.execute('UPDATE public.paper_engine_orders SET quantity=%s WHERE scope_key=%s AND client_id=%s',
                        (quantity, self._scope_key, order['client_id']))
                    lifecycle, pending, certainty = order['lifecycle'], 'NONE', 'CONFIRMED'
                elif status in {'CANCELED', 'EXPIRED', 'REJECTED', 'SUBMITTED'}:
                    if ((status == 'REJECTED' and order['filled'] != 0)
                            or (status == 'SUBMITTED' and order['lifecycle'] != 'SUBMITTED')
                            or (status == 'CANCELED' and order['pending'] != 'CANCEL')):
                        raise BridgeDenied('INVALID_LIFECYCLE_TRANSITION')
                    lifecycle, pending, certainty = status, 'NONE', 'CONFIRMED'
                    if status == 'SUBMITTED':
                        pending, certainty = order['pending'], order['certainty']
                else:
                    raise BridgeDenied('UNSUPPORTED_SYNTHETIC_TRANSITION')
                self._check_event_control(event)
                conn.execute('INSERT INTO public.paper_engine_events '
                    '(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', ('PAPER', self._scope_key, event.event_id,
                        order['client_id'], payload_hash, Jsonb(payload), delta, event.price, event.occurred_at))
                self._check_event_control(event)
                conn.execute('UPDATE public.paper_engine_orders SET filled=%s,lifecycle=%s,pending=%s,'
                    'pending_command_id=%s,certainty=%s,'
                    'revision=revision+1 WHERE scope_key=%s AND client_id=%s',
                    (event.cumulative_quantity, lifecycle, pending,
                     order['pending_command_id'] if pending != 'NONE' else None,
                     certainty, self._scope_key, order['client_id']))
                self._check_event_control(event)
        return True

    def _check_event_control(self, event):
        with self._event_guard(event) as current_scope:
            now = self._clock()
            if (current_scope != self.scope or now.clock_health != 'HEALTHY'
                    or utc_timestamp(event.occurred_at) > utc_timestamp(now.utc_now)):
                raise BridgeDenied('SYNTHETIC_EVENT_AUTHORITY_CHANGED')

    def _identity(self, row):
        return t.OrderIdentity(scope=self.scope, logical_client_order_id=row['logical_id'],
            native_client_order_id='paper-native:' + row['client_id'],
            venue_client_order_id=row['client_id'], venue_order_id=row['order_id'])

    def protection_evidence(self, command_id: str) -> SyntheticProtectionEvidence | None:
        """A synthetic controller verification event, never the submit ACK.

        Exact terms and working quantity are independently queried from the
        restricted venue database. This proves only the declared virtual stop.
        """
        with self._connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            command = conn.execute('SELECT * FROM public.paper_engine_commands WHERE scope_key=%s '
                'AND command_id=%s AND operation=%s', (self._scope_key, command_id, 'SET_PROTECTION')).fetchone()
            if command is None:
                return None
            row = conn.execute('SELECT * FROM public.paper_engine_orders WHERE scope_key=%s AND client_id=%s',
                (self._scope_key, command['target_client_id'])).fetchone()
            event = conn.execute("SELECT * FROM public.paper_engine_events WHERE scope_key=%s AND client_id=%s "
                "AND payload->>'status'='PROTECTION_VERIFIED' ORDER BY occurred_at DESC,event_id DESC LIMIT 1",
                (self._scope_key, command['target_client_id'])).fetchone()
            if (row is None or event is None or row['lifecycle'] != 'SUBMITTED' or row['filled'] != 0
                    or row['pending'] != 'NONE' or row['certainty'] != 'CONFIRMED'):
                return None
            terms = command['request']['canonical_payload']
            if (terms.get('side') != 'SELL' or terms.get('order_type') != 'STOP_MARKET'
                    or self._quantity(terms.get('quantity')) != row['quantity']):
                return None
            ref = self._ref('paper-protection:' + event['event_id'],
                {'event': event['payload'], 'request': command['request'], 'order_revision': row['revision']}, self._clock().utc_now)
            return SyntheticProtectionEvidence(order=self._projection(row),
                stop_price=t.Price(value=self._quantity(terms['trigger_price']), currency=row['price_currency']),
                quantity=t.Quantity(value=row['quantity'], unit=row['quantity_unit']), evidence=ref)

    def _projection(self, row):
        # Leaves is executable remaining quantity. A confirmed terminal order
        # has no executable remainder; UNKNOWN cancellation retains all leaves.
        leaves = Decimal(0) if row['lifecycle'] in TERMINAL else _difference(row['quantity'], row['filled'])
        return t.OrderProjection(order=t.OwnedOrderRef(identity=self._identity(row),
            owner_strategy_id=row['owner_strategy_id'], ownership_epoch=row['ownership_epoch']),
            lifecycle_state=t.OrderLifecycle(row['lifecycle']), pending_operation=row['pending'],
            command_certainty=row['certainty'], outcome_provenance='VENUE_QUERY',
            filled_quantity=t.Quantity(value=row['filled'], unit=row['quantity_unit']),
            leaves_quantity=t.Quantity(value=leaves, unit=row['quantity_unit']),
            lifecycle_revision=row['revision'], sequence=row['revision'])

    def _ref(self, identifier, payload, at):
        return t.EvidenceRef(id=identifier, hash=canonical_hash(payload), environment=t.Environment.PAPER,
            source=SOURCE, visible_at=at)

    def query(self, query: t.ReconciliationCase) -> t.VenueReport:
        if not isinstance(query, t.ReconciliationCase) or query.scope != self.scope:
            raise BridgeDenied('QUERY_SCOPE_MISMATCH')
        at = self._clock().utc_now
        missing = ['PAPER_BALANCES_POSITIONS_PROTECTION_NOT_COVERED']
        orders, fills, evidence = [], [], []
        try:
            with self._connect() as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                if query.command_ids:
                    commands = conn.execute('SELECT command_id,target_client_id FROM public.paper_engine_commands '
                        'WHERE scope_key=%s AND command_id=ANY(%s)',
                        (self._scope_key, list(query.command_ids))).fetchall()
                    if {r['command_id'] for r in commands} != set(query.command_ids):
                        missing.append('COMMAND_NOT_OBSERVED')
                    clients = [r['target_client_id'] for r in commands]
                    rows = conn.execute('SELECT * FROM public.paper_engine_orders WHERE scope_key=%s AND client_id=ANY(%s) '
                        'ORDER BY client_id', (self._scope_key, clients)).fetchall()
                else:
                    rows = conn.execute('SELECT * FROM public.paper_engine_orders WHERE scope_key=%s ORDER BY client_id',
                        (self._scope_key,)).fetchall()
                for row in rows:
                    orders.append(self._projection(row))
                    events = conn.execute('SELECT * FROM public.paper_engine_events WHERE scope_key=%s AND client_id=%s '
                        'AND fill_delta>0 ORDER BY occurred_at,event_id', (self._scope_key, row['client_id'])).fetchall()
                    for event in events:
                        occurred = event['occurred_at'].isoformat()
                        ref = self._ref('paper-event:' + event['event_id'], event['payload'], at)
                        fills.append(t.Fill(fill_id='paper-fill:' + event['event_id'], venue=SOURCE,
                            scope=self.scope, native_trade_id=event['event_id'], order=self._identity(row),
                            quantity=t.Quantity(value=event['fill_delta'], unit=row['quantity_unit']),
                            price=t.Price(value=event['price'], currency=row['price_currency']), fees=(),
                            occurred_at=occurred, correction_of=None, evidence=ref))
                evidence.append(self._ref('paper-query:' + query.case_id,
                    {'scope': domain_payload(self.scope), 'commands': list(query.command_ids),
                     'orders': [domain_payload(o) for o in orders], 'as_of': at}, at))
            succeeded = True
        except psycopg.Error:
            orders, fills, evidence = [], [], []
            missing.append('PAPER_DATABASE_QUERY_FAILED')
            succeeded = False
        return t.VenueReport(coverage=t.Coverage(scope=self.scope, query_succeeded=succeeded,
            pagination_complete=succeeded, supported=True, missing=tuple(missing)),
            orders=tuple(orders), positions=(), balances=(), fills=tuple(fills), evidence=tuple(evidence),
            next_cursor=None, as_of=at)
