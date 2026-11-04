"""Bounded overlap backfill. Completion is evidence, never automatic READY."""
from decimal import Decimal
import time
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal
from .demo_reports import DemoReportVerifier


class DemoReconciler:
    def __init__(self, repository, transport, *, clock=time.time):
        self.repository, self.transport, self.clock = repository, transport, clock
        self.verifier = DemoReportVerifier(repository)

    def check_balances(self, scope, balances):
        observed = {}
        for item in balances:
            if item['asset'] in observed:
                raise DemoError('DEMO_BALANCE_DUPLICATE')
            observed[item['asset']] = decimal(item['free']) + decimal(item['locked'])
        baseline = self.repository.balance_baseline(scope, {k: str(v) for k, v in observed.items()})
        deltas = self.repository.account_deltas(scope)
        for asset in observed.keys() | baseline.keys() | deltas.keys():
            expected = decimal(baseline.get(asset, '0')) + deltas.get(asset, Decimal(0))
            if expected != observed.get(asset, Decimal(0)):
                self.repository.pause(scope, 'DEMO_BALANCE_MISMATCH')
                raise DemoError('DEMO_BALANCE_MISMATCH')
        return True

    def reconcile(self, scope, *, symbol, stream, max_pages=5, page_size=500, max_order_queries=50):
        if stream is None or not stream.healthy or stream.scope.account_key != scope.account_key:
            raise DemoError('DEMO_CONNECTED_STREAM_REQUIRED')
        if type(max_pages) is not int or not 1 <= max_pages <= 20 or type(page_size) is not int or not 1 <= page_size <= 1000:
            raise DemoError('DEMO_BACKFILL_BUDGET_INVALID')
        account = self.repository.account(scope)
        if self.transport.credential_ref != account['credential_ref'] or stream.credential_ref != account['credential_ref']:
            raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
        if type(max_order_queries) is not int or not 1 <= max_order_queries <= 100:
            raise DemoError('DEMO_ORDER_BUDGET_INVALID')
        pending = self.repository.pending_commands(scope, limit=max_order_queries + 1)
        if len(pending) > max_order_queries:
            raise DemoError('DEMO_ORDER_BUDGET_EXCEEDED')
        self.repository.pause(scope, 'DEMO_RECONCILING')
        self.transport.sync_time()
        for command in pending:
            if command['request']['symbol'] != symbol:
                continue
            owner_scope = DemoScope(**command['scope'])
            try:
                result = self.transport.query_order(symbol, command['client_id'])
            except DemoError as exc:
                if exc.code == 'DEMO_ORDER_NOT_FOUND':
                    self.repository.reconcile_not_found(owner_scope, command['command_id'])
                    continue
                raise
            observation = self.repository.observe(owner_scope, source='REST_ORDER', payload=result,
                received_at=int(self.clock()), credential_ref=account['credential_ref'])
            self.verifier.process(owner_scope, observation)
        progress = self.repository.cursor(scope, symbol)
        cursor, revision = progress['cursor'].get('from_id'), progress['revision']
        complete = False
        for _ in range(max_pages):
            if not stream.healthy:
                raise DemoError('DEMO_STREAM_GAP')
            # Keep one trade of overlap with the previously persisted page.
            page = self.transport.trades(symbol, from_id=cursor, limit=page_size)
            if not isinstance(page, list) or any(type(item.get('id')) is not int for item in page):
                raise DemoError('DEMO_BACKFILL_RESPONSE_INVALID')
            ids = [item['id'] for item in page]
            if ids != sorted(set(ids)) or any(value < 0 for value in ids):
                raise DemoError('DEMO_BACKFILL_ORDER_INVALID')
            for trade in page:
                owner_scope = self.repository.resolve_order_scope(scope, order_id=trade.get('orderId'), symbol=symbol)
                observation = self.repository.observe(owner_scope, source='REST_TRADES', payload=trade,
                    received_at=int(self.clock()), credential_ref=account['credential_ref'])
                self.verifier.process(owner_scope, observation)
            complete = len(page) < page_size
            next_cursor = ids[-1] if ids else cursor
            if not complete and next_cursor == cursor:
                raise DemoError('DEMO_BACKFILL_NO_PROGRESS')
            self.repository.save_cursor(scope, symbol, {'from_id': next_cursor}, complete=complete, expected_revision=revision)
            revision += 1
            cursor = next_cursor
            if complete:
                break
        # Empty/truncated history cannot prove reset-free coverage without a
        # qualified baseline. Report only page completion and leave account paused.
        return {'pages_complete': complete, 'cursor': cursor, 'execution_authorized': False,
                'state': 'RECOVERY_REQUIRED', 'reason': 'DEMO_BASELINE_AND_ACCOUNT_RECONCILIATION_REQUIRED'}

    def reconcile_and_resume(self, scope, *, stream, current_code_sha):
        from trading_system.engine.binance_demo.contracts import digest
        from trading_system.storage.demo import accounts
        from sqlalchemy import update
        account = self.repository.account(scope)
        evidence = account.get('qualification') or {}
        required = {'state': 'DEMO_VERIFIED', 'code_sha': current_code_sha, 'account_key': scope.account_key,
                    'credential_revision': account['credential_revision'], 'product': scope.product, 'history_coverage': 'COMPLETE'}
        if any(evidence.get(key) != value for key, value in required.items()) or not evidence.get('baseline_hash') or not evidence.get('evidence_sha256') or not evidence.get('official_contract_revision'):
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        progress = self.reconcile(scope, symbol='BTCUSDT', stream=stream)
        if not progress['pages_complete']:
            raise DemoError('DEMO_BACKFILL_INCOMPLETE')
        # Stream was subscribed before all reads, and its durable inbox must
        # have drained. READY is withheld if a report races this gate.
        expected_revision = self.repository.account(scope)['revision']
        actual = self.transport.open_orders(None)
        if not isinstance(actual, list) or len(actual) > 100:
            raise DemoError('DEMO_OPEN_ORDER_BUDGET_EXCEEDED')
        expected = {(row['request']['symbol'], row['client_id']) for row in self.repository.pending_commands(scope, limit=101)
                    if row['order_status'] not in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}}
        seen = {(row.get('symbol'), row.get('clientOrderId')) for row in actual}
        if len(seen) != len(actual) or seen != expected:
            raise DemoError('DEMO_OPEN_ORDER_RECONCILIATION_REQUIRED')
        raw = self.transport.account()
        if raw.get('accountType') != 'SPOT':
            raise DemoError('DEMO_ACCOUNT_BINDING_REJECTED')
        self.check_balances(scope, raw['balances'])
        account = self.repository.account(scope)
        if account['revision'] != expected_revision:
            raise DemoError('DEMO_RECONCILIATION_RACED')
        if digest(account['balances']) != evidence['baseline_hash'] or account['frozen']:
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        with self.repository.safety_guard(scope), self.repository.transaction(scope.account_key) as conn:
            from sqlalchemy import select
            final_account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if final_account['credential_ref'] != self.transport.credential_ref or final_account['credential_ref'] != stream.credential_ref or final_account['credential_revision'] != required['credential_revision'] or digest(final_account.get('qualification') or {}) != digest(evidence):
                raise DemoError('DEMO_RECONCILIATION_BINDING_CHANGED')
            if final_account['revision'] != expected_revision:
                raise DemoError('DEMO_RECONCILIATION_RACED')
            # The held sender guard serializes balance/posting/pause changes.
            # Observation inserts also lock this row, so none can commit
            # between this health check and the READY commit.
            from trading_system.storage.demo import commands, inbox, observations, protection, outbox
            from sqlalchemy import select, func
            count = lambda table, predicate: conn.execute(select(func.count()).select_from(table).where(predicate)).scalar_one()
            blocked = count(commands, (commands.c.account_key == scope.account_key) & commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN']))
            blocked += count(inbox, (inbox.c.state != 'PROCESSED') & inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))
            blocked += count(protection, (protection.c.account_key == scope.account_key) & (protection.c.state != 'SATISFIED') & ((protection.c.state != 'HOSTED_ACTIVE') | (protection.c.coverage_until <= int(self.clock()))))
            # Risk breaches are durable and presently have no automatic resolution.
            # Keep ingesting facts, but never resume trading on requalification alone.
            blocked += count(outbox, (outbox.c.account_key == scope.account_key) & (outbox.c.kind == 'EXECUTION_RISK_BREACH'))
            if blocked or not stream.healthy:
                raise DemoError('DEMO_RECOVERY_REQUIRED')
            receipt = {'cursor': progress['cursor'], 'open_orders_hash': digest(actual), 'balances_hash': digest(raw['balances']),
                'qualification_hash': digest(evidence), 'credential_revision': account['credential_revision'], 'checked_at': int(self.clock()),
                'subscription_id': stream.subscription_id, 'account_revision': account['revision']}
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key, accounts.c.revision == account['revision']).values(state='READY', revision=account['revision'] + 1))
            self.repository._event(conn, scope.account_key, 'RECONCILIATION_READY', digest(receipt), receipt)
        return {'state': 'READY', 'execution_authorized': False, 'receipt': receipt}
