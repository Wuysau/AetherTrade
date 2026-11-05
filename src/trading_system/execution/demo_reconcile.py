"""Bounded overlap backfill. Completion is evidence, never automatic READY."""
from decimal import Decimal
import time
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal
from .demo_reports import DemoReportVerifier


class DemoReconciler:
    def __init__(self, repository, transport, *, clock=time.time):
        self.repository, self.transport, self.clock = repository, transport, clock
        self.verifier = DemoReportVerifier(repository)

    @staticmethod
    def _checked_call(stream, operation, *args, **kwargs):
        if not stream.healthy: raise DemoError('DEMO_STREAM_GAP')
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        try:
            value = operation(*args, **kwargs)
        except BaseException:
            if hasattr(stream, 'validate_owner'): stream.validate_owner()
            raise
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        if not stream.healthy: raise DemoError('DEMO_STREAM_GAP')
        return value

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
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        generation = getattr(stream, 'physical_generation', None)
        account = self.repository.account(scope)
        if (self.transport.credential_ref != account['credential_ref'] or stream.credential_ref != account['credential_ref'] or
                stream.credential_revision != account['credential_revision']):
            raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
        if type(max_order_queries) is not int or not 1 <= max_order_queries <= 100:
            raise DemoError('DEMO_ORDER_BUDGET_INVALID')
        pending = self.repository.pending_commands(scope, limit=max_order_queries + 1)
        if len(pending) > max_order_queries:
            raise DemoError('DEMO_ORDER_BUDGET_EXCEEDED')
        self.repository.pause(scope, 'DEMO_RECONCILING')
        self._checked_call(stream, self.transport.sync_time)
        for command in pending:
            if command['request']['symbol'] != symbol:
                continue
            owner_scope = DemoScope(**command['scope'])
            try:
                result = self._checked_call(stream, self.transport.query_order, symbol, command['client_id'])
            except DemoError as exc:
                if exc.code == 'DEMO_ORDER_NOT_FOUND':
                    self.repository.reconcile_not_found(owner_scope, command['command_id'])
                    continue
                raise
            observation = self._checked_call(stream, self.repository.observe, owner_scope, source='REST_ORDER', payload=result,
                received_at=int(self.clock()), credential_ref=account['credential_ref'], credential_revision=stream.credential_revision, stream_generation=generation)
            self._checked_call(stream, self.verifier.process, owner_scope, observation)
        progress = self.repository.cursor(scope, symbol)
        cursor, revision = progress['cursor'].get('from_id'), progress['revision']
        complete = False
        for _ in range(max_pages):
            if not stream.healthy:
                raise DemoError('DEMO_STREAM_GAP')
            # Keep one trade of overlap with the previously persisted page.
            page = self._checked_call(stream, self.transport.trades, symbol, from_id=cursor, limit=page_size)
            if not isinstance(page, list) or any(type(item.get('id')) is not int for item in page):
                raise DemoError('DEMO_BACKFILL_RESPONSE_INVALID')
            ids = [item['id'] for item in page]
            if ids != sorted(set(ids)) or any(value < 0 for value in ids):
                raise DemoError('DEMO_BACKFILL_ORDER_INVALID')
            for trade in page:
                owner_scope = self.repository.resolve_order_scope(scope, order_id=trade.get('orderId'), symbol=symbol)
                observation = self._checked_call(stream, self.repository.observe, owner_scope, source='REST_TRADES', payload=trade,
                    received_at=int(self.clock()), credential_ref=account['credential_ref'], credential_revision=stream.credential_revision, stream_generation=generation)
                self._checked_call(stream, self.verifier.process, owner_scope, observation)
            complete = len(page) < page_size
            next_cursor = ids[-1] if ids else cursor
            if not complete and next_cursor == cursor:
                raise DemoError('DEMO_BACKFILL_NO_PROGRESS')
            self._checked_call(stream, self.repository.save_cursor, scope, symbol, {'from_id': next_cursor}, complete=complete, expected_revision=revision, credential_revision=stream.credential_revision, stream_generation=generation)
            revision += 1
            cursor = next_cursor
            if complete:
                break
        # Empty/truncated history cannot prove reset-free coverage without a
        # qualified baseline. Report only page completion and leave account paused.
        return {'pages_complete': complete, 'cursor': cursor, 'execution_authorized': False,
                'state': 'RECOVERY_REQUIRED', 'reason': 'DEMO_BASELINE_AND_ACCOUNT_RECONCILIATION_REQUIRED'}

    def reconcile_for_clean(self, scope, *, stream, current_code_sha):
        """Record a subscribed, flat-account checkpoint; this does not set CLEAN."""
        from uuid import uuid4
        from sqlalchemy import select, func
        from trading_system.engine.binance_demo.contracts import digest
        from trading_system.storage.demo import (accounts, commands, inbox, observations, protection, outbox, decisions,
            final_activity_digest, require_physical_generation)
        if stream is None or not stream.healthy or stream.scope.account_key != scope.account_key:
            raise DemoError('DEMO_CONNECTED_STREAM_REQUIRED')
        run_nonce = getattr(stream, 'active_run_nonce', None)
        if not isinstance(run_nonce, str) or not run_nonce:
            raise DemoError('DEMO_STREAM_RUN_REQUIRED')
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        generation = getattr(stream, 'physical_generation', None)
        if type(generation) is not int or generation < 1:
            raise DemoError('DEMO_PHYSICAL_GENERATION_REJECTED')
        account = self.repository.account(scope); evidence = account.get('qualification') or {}
        required = {'state':'DEMO_VERIFIED', 'code_sha':current_code_sha, 'account_key':scope.account_key,
                    'credential_revision':account.get('credential_revision'), 'product':'SPOT', 'history_coverage':'COMPLETE'}
        if (any(evidence.get(key) != value for key, value in required.items()) or not evidence.get('baseline_hash') or
                not evidence.get('evidence_sha256') or not evidence.get('official_contract_revision') or
                account.get('balances') is None or account.get('frozen') is not False or digest(account['balances']) != evidence['baseline_hash']):
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        progress = self.reconcile(scope, symbol='BTCUSDT', stream=stream)
        if not progress['pages_complete']: raise DemoError('DEMO_BACKFILL_INCOMPLETE')
        # Private frames may have arrived during REST overlap. Drain them under
        # their original scope before taking the final durable snapshot.
        previous = None
        for _ in range(100):
            batch = self._checked_call(stream, self.repository.pending_observations, scope, limit=1000)
            if not batch: break
            ids = tuple(item['observation_id'] for item in batch)
            if ids == previous: raise DemoError('DEMO_INBOX_DRAIN_NO_PROGRESS')
            previous = ids
            for item in batch:
                if not item.get('scope'): raise DemoError('DEMO_OBSERVATION_SCOPE_MISSING')
                self._checked_call(stream, self.verifier.process, DemoScope(**item['scope']), item['observation_id'])
        else:
            raise DemoError('DEMO_INBOX_DRAIN_LIMIT')
        revision = self.repository.account(scope)['revision']
        actual = self._checked_call(stream, self.transport.open_orders, None)
        if not isinstance(actual, list) or actual:
            raise DemoError('DEMO_OPEN_ORDER_RECONCILIATION_REQUIRED')
        raw = self._checked_call(stream, self.transport.account)
        if (not isinstance(raw, dict) or raw.get('accountType') != 'SPOT' or
                type(raw.get('uid')) is not int or str(raw['uid']) != scope.account_id):
            raise DemoError('DEMO_ACCOUNT_BINDING_REJECTED')
        self._checked_call(stream, self.check_balances, scope, raw['balances'])
        if self.repository.account_deltas(scope).get('BTC', Decimal(0)) != 0:
            raise DemoError('DEMO_OPEN_EXPOSURE_REVIEW_REQUIRED')
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        with self.repository.safety_guard(scope), self.repository.transaction(scope.account_key) as conn:
            if hasattr(stream, 'validate_owner'): stream.validate_owner()
            current = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if (current['frozen'] or current['revision'] != revision or current['credential_ref'] != stream.credential_ref or
                    current['credential_revision'] != stream.credential_revision or digest(current.get('qualification') or {}) != digest(evidence)):
                raise DemoError('DEMO_RECONCILIATION_RACED')
            if self.repository.engine.dialect.name == 'postgresql': require_physical_generation(conn, scope, generation)
            count = lambda table, predicate: conn.execute(select(func.count()).select_from(table).where(predicate)).scalar_one()
            blocked = count(commands, (commands.c.account_key == scope.account_key) &
                (commands.c.state.in_(['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN']) |
                 (commands.c.reserve_amount != '0') |
                 ((commands.c.state.in_(['ACKNOWLEDGED','OBSERVED'])) &
                  (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED','CANCELED','EXPIRED','REJECTED','EXPIRED_IN_MATCH'])))))
            blocked += count(inbox, (inbox.c.state != 'PROCESSED') & inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))
            blocked += count(protection, (protection.c.account_key == scope.account_key) & (protection.c.state != 'SATISFIED'))
            blocked += count(decisions, (decisions.c.account_key == scope.account_key) & (decisions.c.state == 'IN_FLIGHT'))
            blocked += count(outbox, (outbox.c.account_key == scope.account_key) & (outbox.c.kind == 'EXECUTION_RISK_BREACH'))
            if blocked or not stream.healthy or stream.active_run_nonce != run_nonce: raise DemoError('DEMO_RECOVERY_REQUIRED')
            receipt = {'physical_key':scope.physical_account_key, 'account_key':scope.account_key, 'physical_generation':generation,
                'credential_revision':stream.credential_revision, 'subscription_id':stream.subscription_id, 'stream_run_nonce':run_nonce,
                'qualification_hash':digest(evidence), 'activity_hash':final_activity_digest(conn, scope),
                'cursor':progress['cursor'], 'open_orders_hash':digest(actual), 'balances_hash':digest(raw['balances']),
                'checked_at':int(self.clock()), 'nonce':uuid4().hex}
            self.repository._event(conn, scope.account_key, 'FINAL_RECONCILIATION', receipt['nonce'], receipt)
        return receipt

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
        actual = self._checked_call(stream, self.transport.open_orders, None)
        if not isinstance(actual, list) or len(actual) > 100:
            raise DemoError('DEMO_OPEN_ORDER_BUDGET_EXCEEDED')
        expected = {(row['request']['symbol'], row['client_id']) for row in self.repository.pending_commands(scope, limit=101)
                    if row['order_status'] not in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}}
        seen = {(row.get('symbol'), row.get('clientOrderId')) for row in actual}
        if len(seen) != len(actual) or seen != expected:
            raise DemoError('DEMO_OPEN_ORDER_RECONCILIATION_REQUIRED')
        raw = self._checked_call(stream, self.transport.account)
        if raw.get('accountType') != 'SPOT':
            raise DemoError('DEMO_ACCOUNT_BINDING_REJECTED')
        self.check_balances(scope, raw['balances'])
        account = self.repository.account(scope)
        if account['revision'] != expected_revision:
            raise DemoError('DEMO_RECONCILIATION_RACED')
        if digest(account['balances']) != evidence['baseline_hash'] or account['frozen']:
            raise DemoError('DEMO_QUALIFICATION_BASELINE_REQUIRED')
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        with self.repository.safety_guard(scope), self.repository.transaction(scope.account_key) as conn:
            from sqlalchemy import select
            if hasattr(stream, 'validate_owner'): stream.validate_owner()
            if self.repository.engine.dialect.name == 'postgresql':
                from trading_system.storage.demo import require_physical_generation
                require_physical_generation(conn, scope, getattr(stream, 'physical_generation', None))
            final_account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if final_account['credential_ref'] != self.transport.credential_ref or final_account['credential_ref'] != stream.credential_ref or final_account['credential_revision'] != stream.credential_revision or final_account['credential_revision'] != required['credential_revision'] or digest(final_account.get('qualification') or {}) != digest(evidence):
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
                'physical_key': scope.physical_account_key, 'physical_generation': getattr(stream, 'physical_generation', None),
                'subscription_id': stream.subscription_id, 'account_revision': account['revision']}
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key, accounts.c.revision == account['revision']).values(state='READY', revision=account['revision'] + 1))
            self.repository._event(conn, scope.account_key, 'RECONCILIATION_READY', digest(receipt), receipt)
        return {'state': 'READY', 'execution_authorized': False, 'receipt': receipt}
