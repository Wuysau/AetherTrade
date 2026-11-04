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
