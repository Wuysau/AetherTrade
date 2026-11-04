"""Durable observation verification and atomic spot facts. Never sends orders."""
from decimal import Decimal
import re
from sqlalchemy import select, update, insert
from trading_system.engine.binance_demo.contracts import DemoError, decimal, digest
from trading_system.storage.demo import accounts, commands, observations, inbox, fills, postings, protection, plan_heads, decisions

TERMINAL = {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}
STATUSES = TERMINAL | {'NEW', 'PARTIALLY_FILLED', 'PENDING_NEW'}


def spot_postings(*, side, base, quote, quantity, quote_quantity, commission, commission_asset):
    if side not in {'BUY', 'SELL'} or not all(isinstance(asset, str) and re.fullmatch(r'[A-Z0-9]{1,20}', asset) for asset in (base, quote, commission_asset)):
        raise DemoError('DEMO_FILL_ASSET_INVALID')
    qty, cost, fee = decimal(quantity, positive=True), decimal(quote_quantity, positive=True), decimal(commission)
    result = {base: qty if side == 'BUY' else -qty, quote: -cost if side == 'BUY' else cost}
    result[commission_asset] = result.get(commission_asset, Decimal(0)) - fee
    return result


class DemoReportVerifier:
    def __init__(self, repository, *, symbols=None):
        self.repository = repository
        # Explicit instrument binding; never split arbitrary symbols heuristically.
        self.symbols = symbols if symbols is not None else {'BTCUSDT': ('BTC', 'USDT')}

    def process(self, scope, observation_id):
        with self.repository.safety_guard(scope):
            try:
                return self._process(scope, observation_id)
            except DemoError as exc:
                self.repository.freeze_on_conflict(scope, observation_id, exc.code)
                raise

    def _process(self, scope, observation_id):
        with self.repository.transaction(scope.account_key) as conn:
            observation = conn.execute(select(observations).where(observations.c.observation_id == observation_id, observations.c.account_key == scope.account_key, observations.c.scope_key == scope.key)).mappings().first()
            if observation is None:
                raise DemoError('DEMO_OBSERVATION_NOT_FOUND')
            queue = conn.execute(select(inbox).where(inbox.c.observation_id == observation_id).with_for_update()).mappings().one()
            if queue['state'] == 'PROCESSED':
                return False
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if observation['credential_revision'] != account['credential_revision'] or account['frozen']:
                raise DemoError('DEMO_OBSERVATION_STALE')
            event = observation['payload']
            if observation['source'] == 'REST_TRADES':
                row = conn.execute(select(commands).where(commands.c.account_key == scope.account_key, commands.c.scope_key == scope.key,
                    commands.c.namespace == 'SPOT', commands.c.order_id == str(event.get('orderId')))).mappings().first()
                if row is None or event.get('symbol') != row['request']['symbol'] or type(event.get('isBuyer')) is not bool:
                    raise DemoError('DEMO_ORDER_BINDING_REJECTED')
                if row['state'] not in {'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED'}:
                    raise DemoError('DEMO_ORDER_NEVER_SENT')
                side = 'BUY' if event['isBuyer'] else 'SELL'
                if side != row['request']['side']:
                    raise DemoError('DEMO_ORDER_BINDING_REJECTED')
                normalized = {'s': event['symbol'], 'i': event['orderId'], 't': event['id'], 'S': side,
                    'l': event['qty'], 'L': event['price'], 'Y': event['quoteQty'], 'n': event['commission'], 'N': event['commissionAsset'], 'T': event.get('time')}
                self._fill(conn, scope, normalized, row, observation['received_at'])
                total = sum((decimal(p['quantity']) for p in conn.execute(select(fills.c.payload).where(fills.c.account_key == scope.account_key,
                    fills.c.symbol == event['symbol'], fills.c.order_id == str(event['orderId']))).scalars()), Decimal(0))
                if 'quantity' in row['request'] and total > decimal(row['request']['quantity']):
                    raise DemoError('DEMO_FILL_QUANTITY_CONFLICT')
                quote_total = sum((decimal(p['quote_quantity']) for p in conn.execute(select(fills.c.payload).where(fills.c.account_key == scope.account_key, fills.c.symbol == event['symbol'], fills.c.order_id == str(event['orderId']))).scalars()), Decimal(0))
                if row['order_status'] in TERMINAL and total == decimal(row['cumulative_qty']) and (row['request']['type'] != 'MARKET' or quote_total == decimal(row['cumulative_quote'])):
                    conn.execute(update(commands).where(commands.c.command_id == row['command_id']).values(reserve_amount='0'))
                    self._release_mitigation(conn, scope, row)
                # REST trades do not imply an order terminal state. Query that separately.
                conn.execute(update(inbox).where(inbox.c.observation_id == observation_id).values(state='PROCESSED', attempts=inbox.c.attempts + 1))
                return True
            if observation['source'] == 'REST_ORDER':
                event = {'e': 'executionReport', 's': event.get('symbol'), 'c': event.get('clientOrderId'),
                    'S': event.get('side'), 'o': event.get('type'), 'q': event.get('origQty'), 'p': event.get('price'),
                    'i': event.get('orderId'), 'X': event.get('status'), 'z': event.get('executedQty'), 'Z': event.get('cummulativeQuoteQty'), 'Q': event.get('origQuoteOrderQty'), 'x': 'REST_ORDER'}
            elif observation['source'] != 'WS_EXECUTION_REPORT' or event.get('e') != 'executionReport':
                raise DemoError('DEMO_REPORT_NOT_SUPPORTED')
            symbol = event.get('s')
            if symbol not in self.symbols:
                raise DemoError('DEMO_SYMBOL_UNBOUND')
            row = conn.execute(select(commands).where(commands.c.account_key == scope.account_key, commands.c.scope_key == scope.key,
                commands.c.namespace == 'SPOT', commands.c.client_id == (event.get('C') if event.get('X') == 'CANCELED' and event.get('C') else event.get('c')))).mappings().first()
            if row is None:
                raise DemoError('DEMO_ORDER_BINDING_REJECTED')
            if row['state'] not in {'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED'}:
                raise DemoError('DEMO_ORDER_NEVER_SENT')
            request = row['request']
            quote_sized = 'quoteOrderQty' in request
            if event.get('S') != request['side'] or symbol != request['symbol'] or event.get('o') != request['type'] or (not quote_sized and decimal(event['q']) != decimal(request['quantity'])) or (quote_sized and decimal(event.get('Q')) != decimal(request['quoteOrderQty'])):
                raise DemoError('DEMO_ORDER_BINDING_REJECTED')
            if request['type'] == 'LIMIT' and decimal(event.get('p')) != decimal(request['price']):
                raise DemoError('DEMO_LIMIT_PRICE_BINDING_REJECTED')
            if type(event.get('i')) is not int or event['i'] < 0 or (row['order_id'] is not None and row['order_id'] != str(event['i'])):
                raise DemoError('DEMO_ORDER_ID_CONFLICT')
            other_order = conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key, commands.c.namespace == 'SPOT', commands.c.order_id == str(event['i']), commands.c.command_id != row['command_id'])).first()
            if other_order:
                raise DemoError('DEMO_ORDER_ID_CONFLICT')
            status = event.get('X')
            cumulative, previous = decimal(event['z']), decimal(row['cumulative_qty'])
            total = decimal(request['quantity']) if not quote_sized else None
            quote_cumulative = decimal(event.get('Z')) if request['type'] == 'MARKET' else decimal(event.get('Z') or row['cumulative_quote'])
            previous_quote = decimal(row['cumulative_quote'])
            if request['type'] == 'MARKET' and ((cumulative < previous and quote_cumulative > previous_quote) or (cumulative > previous and quote_cumulative < previous_quote) or (cumulative == previous and quote_cumulative != previous_quote)):
                raise DemoError('DEMO_CUMULATIVE_QUOTE_CONFLICT')
            if status not in STATUSES or (total is not None and (cumulative > total or (status == 'FILLED' and cumulative != total))):
                raise DemoError('DEMO_ORDER_QUANTITY_CONFLICT')
            if event.get('x') == 'TRADE':
                self._fill(conn, scope, event, row, observation['received_at'])
            # Older cumulative observations can add missing fills, never regress status.
            current_status = row['order_status']
            if cumulative < previous:
                status = current_status
                cumulative = previous
                quote_cumulative = previous_quote
            elif current_status in TERMINAL:
                if status in TERMINAL and status != current_status:
                    raise DemoError('DEMO_ORDER_TERMINAL_CONFLICT')
                status = current_status
            recorded = sum((decimal(payload['quantity']) for payload in conn.execute(select(fills.c.payload).where(fills.c.account_key == scope.account_key, fills.c.symbol == symbol, fills.c.order_id == str(event['i']))).scalars()), Decimal(0))
            recorded_quote = sum((decimal(payload['quote_quantity']) for payload in conn.execute(select(fills.c.payload).where(fills.c.account_key == scope.account_key, fills.c.symbol == symbol, fills.c.order_id == str(event['i']))).scalars()), Decimal(0))
            if (total is not None and recorded > total) or recorded > cumulative or (request['type'] == 'MARKET' and recorded_quote > quote_cumulative):
                raise DemoError('DEMO_FILL_QUANTITY_CONFLICT')
            # Terminal report alone does not prove financial reconciliation.
            release = status in TERMINAL and recorded == cumulative and (request['type'] != 'MARKET' or recorded_quote == quote_cumulative)
            if release:
                self._release_mitigation(conn, scope, row)
            if quote_sized and quote_cumulative > decimal(request['quoteOrderQty']):
                self._risk_breach(conn, scope, row, 'DEMO_MARKET_QUOTE_CAP_BREACHED')
            conn.execute(update(commands).where(commands.c.command_id == row['command_id']).values(order_id=str(event['i']), order_status=status,
                cumulative_qty=str(cumulative), cumulative_quote=str(quote_cumulative), state='OBSERVED', reserve_amount='0' if release else row['reserve_amount']))
            if status in TERMINAL:
                cancellations = conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.method == 'DELETE', commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED']))).mappings().all()
                for cancellation in cancellations:
                    if cancellation['request'] == {'symbol': symbol, 'origClientOrderId': row['client_id']}:
                        conn.execute(update(commands).where(commands.c.command_id == cancellation['command_id']).values(state='OBSERVED', order_id=str(event['i']), order_status=status, reason='DEMO_CANCEL_TARGET_TERMINAL'))
            conn.execute(update(inbox).where(inbox.c.observation_id == observation_id).values(state='PROCESSED', attempts=inbox.c.attempts + 1))
            self.repository._event(conn, scope.account_key, 'ORDER_OBSERVED', observation_id, {'command_id': row['command_id'], 'status': status})
            return True

    def _fill(self, conn, scope, event, command, received_at):
        trade_id = event.get('t')
        if type(trade_id) is not int or trade_id < 0:
            raise DemoError('DEMO_TRADE_ID_INVALID')
        qty, price = decimal(event['l'], positive=True), decimal(event['L'], positive=True)
        if command['request']['type'] == 'LIMIT':
            limit = decimal(command['request']['price'], positive=True)
            if (event['S'] == 'BUY' and price > limit) or (event['S'] == 'SELL' and price < limit):
                raise DemoError('DEMO_LIMIT_PRICE_VIOLATION')
        quote_qty = decimal(event.get('Y', str(qty * price)), positive=True)
        if quote_qty != qty * price:
            raise DemoError('DEMO_FILL_QUOTE_CONFLICT')
        fee, asset = decimal(event['n']), event.get('N')
        base, quote = self.symbols[event['s']]
        if asset is None and fee == 0:
            asset = quote
        payload = {'symbol': event['s'], 'order_id': str(event['i']), 'trade_id': str(trade_id), 'side': event['S'], 'quantity': str(qty.normalize()),
            'price': str(price.normalize()), 'quote_quantity': str(quote_qty.normalize()), 'commission': str(fee.normalize()), 'commission_asset': asset}
        fill_key = digest([scope.account_key, event['s'], str(trade_id)])
        payload_hash = digest(payload)
        existing = conn.execute(select(fills).where(fills.c.fill_key == fill_key)).mappings().first()
        if existing:
            if existing['payload_hash'] != payload_hash or existing['scope_key'] != scope.key:
                raise DemoError('DEMO_FILL_CONFLICT')
            return
        deltas = spot_postings(side=event['S'], base=base, quote=quote, quantity=str(qty), quote_quantity=str(quote_qty), commission=str(fee), commission_asset=asset)
        occurred_at = (event.get('T') or event.get('E') or received_at * 1000) // 1000
        if type(occurred_at) is not int or occurred_at < 0 or occurred_at > received_at + 5:
            raise DemoError('DEMO_TRADE_TIME_INVALID')
        conn.execute(insert(fills).values(fill_key=fill_key, account_key=scope.account_key, scope_key=scope.key, symbol=event['s'], trade_id=str(trade_id), order_id=str(event['i']), payload_hash=payload_hash, payload=payload, occurred_at=occurred_at))
        for currency, delta in deltas.items():
            conn.execute(insert(postings).values(fill_key=fill_key, asset=currency, account_key=scope.account_key, scope_key=scope.key, delta=str(delta)))
        conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(revision=accounts.c.revision + 1))
        if command['request']['type'] == 'MARKET':
            risk = (command['risk_receipt'] or {}).get('market_risk') or {}
            principal = sum((decimal(p['quote_quantity']) for p in conn.execute(select(fills.c.payload).where(fills.c.account_key == scope.account_key, fills.c.order_id == str(event['i']), fills.c.symbol == event['s'])).scalars()), Decimal(0))
            fee_basis = qty if asset == base else quote_qty if asset == quote else None
            fee_breach = bool(fee) and (asset not in risk.get('fee_assets', []) or fee_basis is None or fee > fee_basis * decimal(risk.get('fee_rate_cap')))
            if ('quoteOrderQty' in command['request'] and principal > decimal(command['request']['quoteOrderQty'])) or fee_breach:
                self._risk_breach(conn, scope, command, 'DEMO_MARKET_COST_POLICY_BREACHED')
        if command['request']['type'] == 'LIMIT' and command['purpose'] == 'PROTECTION' and fee:
            cap = (command['risk_receipt'] or {}).get('fee_rate_cap')
            basis = qty if asset == base else quote_qty if asset == quote else None
            if cap is None or basis is None or fee > basis * decimal(cap):
                self._risk_breach(conn, scope, command, 'DEMO_PROTECTION_FEE_POLICY_BREACHED')
        if event['S'] == 'BUY':
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED'))
            if deltas[base] <= 0:
                if command['request']['type'] != 'MARKET':
                    raise DemoError('DEMO_FILL_FEE_INVALID')
                self._risk_breach(conn, scope, command, 'DEMO_MARKET_UNMATCHED_EXPOSURE')
            else:
                conn.execute(insert(protection).values(fill_key=fill_key, account_key=scope.account_key, scope_key=scope.key, state='REQUIRED',
                    plan=command['protection_plan'], remaining_quantity=str(deltas[base]), entry_command_id=command['command_id'], opened_at=occurred_at, coverage_until=0))
        else:
            remaining = -deltas[base]
            duties = conn.execute(select(protection).where(protection.c.scope_key == scope.key, protection.c.state != 'SATISFIED').order_by(protection.c.opened_at, protection.c.fill_key)).mappings().all()
            if command['purpose'] == 'PROTECTION':
                bound = (command['risk_receipt'] or {}).get('protection_fill_keys')
                if not bound: raise DemoError('DEMO_PROTECTION_BINDING_INVALID')
                duties = [duty for duty in duties if duty['fill_key'] in bound]
            for duty in duties:
                available = decimal(duty['remaining_quantity'])
                consumed = min(available, remaining); left = available - consumed; remaining -= consumed
                conn.execute(update(protection).where(protection.c.fill_key == duty['fill_key']).values(remaining_quantity=str(left), state='SATISFIED' if left == 0 else duty['state']))
                if remaining == 0: break
            if remaining:
                if command['request']['type'] != 'MARKET' and command['purpose'] != 'PROTECTION':
                    raise DemoError('DEMO_UNOWNED_EXPOSURE')
                self._risk_breach(conn, scope, command, 'DEMO_UNMATCHED_EXPOSURE')
        if event['S'] == 'BUY' and (command['risk_receipt'] or {}).get('run_id'):
            run_id = command['risk_receipt']['run_id']
            record = conn.execute(select(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).mappings().one()
            proposal = (record['result'] or {}).get('decision', {}).get('proposal')
            if not proposal or digest(proposal) != command['risk_receipt']['proposal_hash'] or proposal['instrument_ref'] != 'binance-demo:SPOT:BTCUSDT':
                raise DemoError('DEMO_PLAN_LINEAGE_INVALID')
            head = conn.execute(select(plan_heads).where(plan_heads.c.scope_key == scope.key)).mappings().first()
            if not head or head['source_command_id'] != command['command_id']:
                values = {'scope_key': scope.key, 'account_key': scope.account_key, 'revision': head['revision'] + 1 if head else 1,
                    'state': 'ACTIVE', 'proposal': proposal, 'source_command_id': command['command_id'], 'predecessor_hash': digest(dict(head)) if head else None}
                conn.execute(update(plan_heads).where(plan_heads.c.scope_key == scope.key).values(**values) if head else insert(plan_heads).values(**values))
        elif event['S'] == 'SELL':
            if not conn.execute(select(protection.c.fill_key).where(protection.c.scope_key == scope.key, protection.c.state != 'SATISFIED')).first():
                conn.execute(update(plan_heads).where(plan_heads.c.scope_key == scope.key).values(state='CLOSED', revision=plan_heads.c.revision + 1))
        self.repository._event(conn, scope.account_key, 'FILL_ACCOUNTED', fill_key, {'fill_key': fill_key})

    def _release_mitigation(self, conn, scope, command):
        # Only a terminal order with every fill accounted can release remaining
        # duties for another attempt. UNKNOWN never reaches this transition.
        if command['purpose'] == 'PROTECTION':
            conn.execute(update(protection).where(protection.c.scope_key == scope.key,
                protection.c.mitigation_command_id == command['command_id'], protection.c.state == 'MITIGATION_PENDING')
                .values(state='REQUIRED', mitigation_command_id=None, coverage_until=0))

    def _risk_breach(self, conn, scope, command, reason):
        conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED'))
        self.repository._event(conn, scope.account_key, 'EXECUTION_RISK_BREACH', command['command_id'] + ':' + reason,
            {'command_id': command['command_id'], 'reason': reason})
