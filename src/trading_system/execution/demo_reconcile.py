"""Bounded overlap backfill. Completion is evidence, never automatic READY."""
from decimal import Decimal
import time
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal
from .demo_reports import DemoReportVerifier


def require_qualified_stream_barrier():
    # No supported REST/WS sequencing proof or approved alternative is yet
    # available. Empty durable inbox does not prove the socket has no frames.
    raise DemoError('DEMO_ACCOUNT_STREAM_BARRIER_UNQUALIFIED')


class DemoReconciler:
    def __init__(self, repository, transport, *, clock=time.time):
        self.repository, self.transport, self.clock = repository, transport, clock
        self.verifier = DemoReportVerifier(repository)

    @staticmethod
    def _checked_call(guard_stream, operation, *args, **kwargs):
        if not guard_stream.healthy: raise DemoError('DEMO_STREAM_GAP')
        if hasattr(guard_stream, 'validate_owner'): guard_stream.validate_owner()
        try:
            value = operation(*args, **kwargs)
        except BaseException:
            if hasattr(guard_stream, 'validate_owner'): guard_stream.validate_owner()
            raise
        if hasattr(guard_stream, 'validate_owner'): guard_stream.validate_owner()
        if not guard_stream.healthy: raise DemoError('DEMO_STREAM_GAP')
        return value

    def check_balances(self, scope, balances, *, stream=None, expected_revision=None):
        revision = self.repository.account(scope)['revision']
        if expected_revision is not None and revision != expected_revision:
            raise DemoError('DEMO_ACCOUNT_READ_RACED')
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
                self.repository.invalidate_balance_baseline(scope, expected_revision=revision, stream=stream)
                raise DemoError('DEMO_BALANCE_MISMATCH')
        return True

    def review_account_positions(self, scope, *, stream):
        """Compare captured partial position frames with a signed REST snapshot.

        Spot documentation does not provide a shared REST/stream sequence or a
        socket-buffer barrier. A match is recorded but leaves the inbox pending
        and cannot authorize trading, READY, or CLEAN.
        """
        from sqlalchemy import select
        from trading_system.engine.binance_demo.account_events import parse_account_position
        from trading_system.engine.binance_demo.contracts import digest
        from trading_system.storage.demo import accounts, observations, inbox, postings, require_physical_generation
        if stream is None or not stream.healthy or stream.scope.account_key != scope.account_key:
            raise DemoError('DEMO_CONNECTED_STREAM_REQUIRED')
        nonce = getattr(stream, 'active_run_nonce', None)
        if not isinstance(nonce, str) or not nonce: raise DemoError('DEMO_STREAM_RUN_REQUIRED')
        account = self.repository.account(scope)
        if (account['credential_ref'] != self.transport.credential_ref or account['credential_ref'] != stream.credential_ref or
                account['credential_revision'] != stream.credential_revision):
            raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
        if account['balances'] is None: raise DemoError('DEMO_ACCOUNT_BASELINE_REQUIRED')
        def rows_for(conn):
            return conn.execute(select(observations, inbox.c.state.label('inbox_state')).join(inbox,
                observations.c.observation_id == inbox.c.observation_id).where(observations.c.account_key == scope.account_key,
                observations.c.source == 'WS_ACCOUNT_POSITION', inbox.c.state == 'PENDING').limit(1001)).mappings().all()
        with self.repository.engine.connect() as conn:
            rows = rows_for(conn)
        if not rows: return {'state':'NO_PENDING_POSITIONS', 'execution_authorized':False}
        if len(rows) > 1000: raise DemoError('DEMO_ACCOUNT_POSITION_REVIEW_LIMIT')
        generation = getattr(stream, 'physical_generation', None)
        def signatures(items):
            return sorted((row['observation_id'], row['payload_hash'], row['credential_revision'], row['stream_generation']) for row in items)
        captured = signatures(rows)
        ids = [item[0] for item in captured]
        latest, reported = {}, []
        for row in rows:
            event = row['payload']
            if row['credential_revision'] != stream.credential_revision or row['stream_generation'] != generation or digest(event) != row['payload_hash']:
                raise DemoError('DEMO_ACCOUNT_POSITION_BINDING_REJECTED')
            values = parse_account_position(event)
            if event['E'] > int(self.clock() * 1000) + 5000:
                raise DemoError('DEMO_ACCOUNT_POSITION_INVALID')
            for asset, value in values.items():
                reported.append((asset, value))
                old = latest.get(asset)
                if old and old[0] == event['u'] and old[1] != value: raise DemoError('DEMO_ACCOUNT_POSITION_CONFLICT')
                if old is None or event['u'] > old[0]: latest[asset] = (event['u'], value)
        raw = self._checked_call(stream, self.transport.account)
        if (not isinstance(raw, dict) or raw.get('accountType') != 'SPOT' or type(raw.get('uid')) is not int or
                str(raw['uid']) != scope.account_id or type(raw.get('updateTime')) is not int or
                not isinstance(raw.get('balances'), list)):
            raise DemoError('DEMO_ACCOUNT_BINDING_REJECTED')
        if raw['updateTime'] < max(value[0] for value in latest.values()):
            raise DemoError('DEMO_ACCOUNT_POSITION_SNAPSHOT_STALE')
        actual = {}
        for item in raw['balances']:
            if not isinstance(item, dict) or not isinstance(item.get('asset'), str) or item['asset'] in actual:
                raise DemoError('DEMO_BALANCE_DUPLICATE')
            actual[item['asset']] = (decimal(item.get('free')), decimal(item.get('locked')))
        # A discontinuity can be a reset or unaccounted activity. Revoke old
        # qualification, but never replace the historical baseline.
        self._checked_call(stream, self.check_balances, scope, raw['balances'], stream=stream, expected_revision=account['revision'])
        if any(actual.get(asset) != value for asset, value in reported):
            raise DemoError('DEMO_ACCOUNT_POSITION_MISMATCH')
        if hasattr(stream, 'validate_owner'): stream.validate_owner()
        with self.repository.safety_guard(scope), self.repository.transaction(scope.account_key) as conn:
            if hasattr(stream, 'validate_owner'): stream.validate_owner()
            current = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if (not stream.healthy or stream.active_run_nonce != nonce or current['frozen'] or
                    current['revision'] != account['revision'] or current['credential_ref'] != stream.credential_ref or
                    current['credential_revision'] != stream.credential_revision):
                raise DemoError('DEMO_ACCOUNT_POSITION_REVIEW_RACED')
            if self.repository.engine.dialect.name == 'postgresql': require_physical_generation(conn, scope, generation)
            pending = rows_for(conn)
            if len(pending) > 1000 or signatures(pending) != captured:
                raise DemoError('DEMO_ACCOUNT_POSITION_REVIEW_RACED')
            other = conn.execute(select(inbox.c.observation_id).join(observations,
                inbox.c.observation_id == observations.c.observation_id).where(observations.c.account_key == scope.account_key,
                observations.c.source != 'WS_ACCOUNT_POSITION', inbox.c.state != 'PROCESSED')).first()
            if other: raise DemoError('DEMO_INBOX_RECONCILIATION_REQUIRED')
            deltas = {}
            for asset, amount in conn.execute(select(postings.c.asset, postings.c.delta).where(postings.c.account_key == scope.account_key)):
                deltas[asset] = deltas.get(asset, Decimal(0)) + Decimal(amount)
            baseline = current['balances']
            for asset in set(actual) | set(baseline) | set(deltas):
                expected = decimal(baseline.get(asset, '0')) + deltas.get(asset, Decimal(0))
                if expected != sum(actual.get(asset, (Decimal(0), Decimal(0)))):
                    raise DemoError('DEMO_BALANCE_MISMATCH')
            receipt = {'observation_ids':ids, 'snapshot_hash':digest(raw), 'account_revision':current['revision'],
                       'credential_revision':stream.credential_revision, 'stream_generation':generation,
                       'stream_run_nonce':nonce, 'state':'MATCHED_UNQUALIFIED'}
            self.repository._event(conn, scope.account_key, 'ACCOUNT_POSITION_REVIEW', digest(receipt), receipt)
        return {'state':'MATCHED_UNQUALIFIED', 'execution_authorized':False,
                'blocker':'DEMO_ACCOUNT_STREAM_SNAPSHOT_ORDERING_UNQUALIFIED', 'count':len(ids)}

    def reconcile(self, scope, *, symbol, stream, max_pages=5, page_size=500, max_order_queries=50, review_positions=True):
        if stream is None or not stream.healthy or stream.scope.account_key != scope.account_key:
            raise DemoError('DEMO_CONNECTED_STREAM_REQUIRED')
        if type(max_pages) is not int or not 1 <= max_pages <= 20 or type(page_size) is not int or not 2 <= page_size <= 1000:
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
        if revision == 0:
            cursor = 0
            overlap_required = False
        elif (progress['cursor'].get('started_at_zero') is not True or
                type(cursor) is not int or cursor < 0 or type(progress['cursor'].get('overlap_required')) is not bool):
            # Older cursors may have started from the venue's latest page.
            # They are not evidence of earlier history and need review.
            raise DemoError('DEMO_HISTORY_CURSOR_UNVERIFIED')
        else:
            overlap_required = progress['cursor']['overlap_required']
        complete = False
        for _ in range(max_pages):
            if not stream.healthy:
                raise DemoError('DEMO_STREAM_GAP')
            # Keep one trade of overlap with the previously persisted page.
            page = self._checked_call(stream, self.transport.trades, symbol, from_id=cursor, limit=page_size)
            if not isinstance(page, list) or len(page) > page_size or any(not isinstance(item, dict) or type(item.get('id')) is not int for item in page):
                raise DemoError('DEMO_BACKFILL_RESPONSE_INVALID')
            ids = [item['id'] for item in page]
            if ids != sorted(set(ids)) or any(value < 0 for value in ids):
                raise DemoError('DEMO_BACKFILL_ORDER_INVALID')
            if (ids and ids[0] < cursor) or (overlap_required and (not ids or ids[0] != cursor)):
                self._checked_call(stream, self.repository.save_cursor, scope, symbol,
                    {'from_id':cursor, 'started_at_zero':True, 'overlap_required':overlap_required}, complete=False,
                    expected_revision=revision, credential_revision=stream.credential_revision, stream_generation=generation)
                raise DemoError('DEMO_BACKFILL_OVERLAP_LOST')
            for trade in page:
                try:
                    owner_scope = self.repository.resolve_order_scope(scope, order_id=trade.get('orderId'), symbol=symbol)
                except DemoError as exc:
                    if exc.code != 'DEMO_ORDER_BINDING_REJECTED': raise
                    # Unknown ownership is not evidence that the trade is
                    # external: it may belong to our own ambiguous send.
                    owner_scope = self._checked_call(stream, self.repository.record_unresolved_trade, scope, symbol=symbol,
                        trade=trade, credential_ref=account['credential_ref'], credential_revision=stream.credential_revision,
                        stream_generation=generation)
                    if owner_scope is None:
                        raise DemoError('DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED') from None
                observation = self._checked_call(stream, self.repository.observe, owner_scope, source='REST_TRADES', payload=trade,
                    received_at=int(self.clock()), credential_ref=account['credential_ref'], credential_revision=stream.credential_revision, stream_generation=generation)
                self._checked_call(stream, self.verifier.process, owner_scope, observation)
            complete = len(page) < page_size
            next_cursor = ids[-1] if ids else cursor
            if not complete and next_cursor == cursor:
                raise DemoError('DEMO_BACKFILL_NO_PROGRESS')
            overlap_required = bool(ids) or overlap_required
            self._checked_call(stream, self.repository.save_cursor, scope, symbol, {'from_id': next_cursor, 'started_at_zero':True, 'overlap_required':overlap_required}, complete=complete, expected_revision=revision, credential_revision=stream.credential_revision, stream_generation=generation)
            revision += 1
            cursor = next_cursor
            if complete:
                break
        # Empty/truncated history cannot prove reset-free coverage without a
        # qualified baseline. Report only page completion and leave account paused.
        from sqlalchemy import select
        from trading_system.storage.demo import observations, inbox
        with self.repository.engine.connect() as conn:
            has_positions = conn.execute(select(observations.c.observation_id).join(inbox,
                observations.c.observation_id == inbox.c.observation_id).where(
                observations.c.account_key == scope.account_key, observations.c.source == 'WS_ACCOUNT_POSITION',
                inbox.c.state == 'PENDING').limit(1)).first() is not None
        review = self.review_account_positions(scope, stream=stream) if has_positions and review_positions else None
        return {'pages_complete': complete, 'cursor': cursor, 'execution_authorized': False,
                'state': 'RECOVERY_REQUIRED', 'reason': 'DEMO_BASELINE_AND_ACCOUNT_RECONCILIATION_REQUIRED',
                'account_position_review': review}

    def reconcile_for_clean(self, scope, *, stream, current_code_sha):
        """Record a subscribed, flat-account checkpoint; this does not set CLEAN."""
        from uuid import uuid4
        from sqlalchemy import select, func
        from trading_system.engine.binance_demo.contracts import digest
        from trading_system.storage.demo import (accounts, commands, inbox, observations, protection, outbox, decisions, unresolved_trade_history,
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
                if item['source'] == 'WS_ACCOUNT_POSITION':
                    raise DemoError('DEMO_ACCOUNT_POSITION_RECONCILIATION_REQUIRED')
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
        self._checked_call(stream, self.check_balances, scope, raw['balances'], stream=stream, expected_revision=revision)
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
            blocked += len(unresolved_trade_history(conn, scope.account_key))
            if blocked or not stream.healthy or stream.active_run_nonce != run_nonce: raise DemoError('DEMO_RECOVERY_REQUIRED')
            require_qualified_stream_barrier()
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
        self.check_balances(scope, raw['balances'], stream=stream, expected_revision=expected_revision)
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
            from trading_system.storage.demo import commands, inbox, observations, protection, outbox, unresolved_trade_history
            from sqlalchemy import select, func
            count = lambda table, predicate: conn.execute(select(func.count()).select_from(table).where(predicate)).scalar_one()
            blocked = count(commands, (commands.c.account_key == scope.account_key) & commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN']))
            blocked += count(inbox, (inbox.c.state != 'PROCESSED') & inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))
            blocked += count(protection, (protection.c.account_key == scope.account_key) & (protection.c.state != 'SATISFIED') & ((protection.c.state != 'HOSTED_ACTIVE') | (protection.c.coverage_until <= int(self.clock()))))
            # Risk breaches are durable and presently have no automatic resolution.
            # Keep ingesting facts, but never resume trading on requalification alone.
            blocked += count(outbox, (outbox.c.account_key == scope.account_key) & (outbox.c.kind == 'EXECUTION_RISK_BREACH'))
            blocked += len(unresolved_trade_history(conn, scope.account_key))
            if blocked or not stream.healthy:
                raise DemoError('DEMO_RECOVERY_REQUIRED')
            require_qualified_stream_barrier()
            receipt = {'cursor': progress['cursor'], 'open_orders_hash': digest(actual), 'balances_hash': digest(raw['balances']),
                'qualification_hash': digest(evidence), 'credential_revision': account['credential_revision'], 'checked_at': int(self.clock()),
                'physical_key': scope.physical_account_key, 'physical_generation': getattr(stream, 'physical_generation', None),
                'subscription_id': stream.subscription_id, 'account_revision': account['revision']}
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key, accounts.c.revision == account['revision']).values(state='READY', revision=account['revision'] + 1))
            self.repository._event(conn, scope.account_key, 'RECONCILIATION_READY', digest(receipt), receipt)
        return {'state': 'READY', 'execution_authorized': False, 'receipt': receipt}
