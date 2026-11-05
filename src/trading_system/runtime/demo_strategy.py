"""Native assessment_v1 → deterministic risk → durable Demo command composition.

No implicit scheduler or model permission. Every call consumes a stored grant,
current trusted frame and immutable decision receipt. PAPER is never involved.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import inspect
import time

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal, digest
from trading_system.engine.binance_demo.orders import compile_spot_order
from trading_system.intents.semantic_validation import quantity_from_notional, quantity_on_grid
from trading_system.server.demo_snapshot import validate_demo_snapshot
from trading_system.server.decision_assessment import validate_assessment
from trading_system.execution.demo_reports import DemoReportVerifier
from .demo_host import AdmissionContext, DemoRuntimeHost

ENVELOPE = ('schema_revision', 'summary', 'proposal', 'evidence_claims', 'watch_conditions', 'next_review_at')


class DemoStrategyHost:
    def __init__(self, repository, scope, *, frame_loader, model_service, execution_owner, rule_assessor=None, hybrid_gate=None, current_code_sha, stream=None, memory_service=None, clock=time.time):
        if not callable(frame_loader) or scope.product != 'SPOT' or len(current_code_sha) != 40:
            raise DemoError('DEMO_HOST_COMPOSITION_INVALID')
        self.repository, self.scope, self.frame_loader = repository, scope, frame_loader
        self.models, self.owner, self.rule_assessor, self.hybrid_gate = model_service, execution_owner, rule_assessor, hybrid_gate
        self.current_code_sha, self.clock, self.stream = current_code_sha, clock, stream
        self.memory = memory_service

    def _strategy(self):
        strategy = self.repository.strategy(self.scope)
        if strategy.get('authorized') is not True or self.clock() >= strategy['authorized_until']:
            raise DemoError('DEMO_STRATEGY_NOT_AUTHORIZED')
        return strategy

    def _frame(self):
        snapshot = validate_demo_snapshot(self.frame_loader())
        if snapshot['scope'] != __import__('dataclasses').asdict(self.scope) or snapshot['bindings']['code_sha'] != self.current_code_sha:
            raise DemoError('DEMO_FRAME_BINDING_CHANGED')
        if not 0 <= self.clock() - datetime.fromisoformat(snapshot['market']['quote']['as_of']).timestamp() <= 5 or not 0 <= self.clock() - datetime.fromisoformat(snapshot['bindings']['account_as_of']).timestamp() <= 15:
            raise DemoError('DEMO_SNAPSHOT_STALE')
        execution = snapshot['market']['execution_context']
        if execution and not 0 <= self.clock() - datetime.fromisoformat(execution['as_of']).timestamp() <= 5:
            raise DemoError('DEMO_FILTER_CONTEXT_STALE')
        return snapshot

    async def assess_once(self, run_id, *, expected_strategy=None, before_model=None):
        strategy = self._strategy()
        if expected_strategy is not None and strategy != expected_strategy:
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        previous = self.repository.decision(self.scope, run_id)
        if previous:
            if previous['strategy'] != strategy:
                raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            if previous['state'] in {'ASSESSED', 'STAGED', 'MANAGEMENT_APPLIED'}:
                return previous
            raise DemoError('DEMO_MODEL_RUN_NOT_RETRYABLE')
        mode = strategy.get('mode')
        if mode not in {'RULE', 'AI', 'HYBRID'}:
            raise DemoError('DEMO_STRATEGY_MODE_INVALID')
        if mode != 'RULE' and (strategy.get('allow_model_calls') is not True or self.models is None):
            raise DemoError('DEMO_MODEL_PERMISSION_REQUIRED')
        if self.memory is not None:
            await asyncio.to_thread(self.memory.project, self.scope)
        snapshot = await asyncio.to_thread(self._frame)
        if snapshot['task_profile']['strategy_revision'] != strategy['revision']:
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        source = 'verified_rule_v1' if mode == 'RULE' else 'assessment_v1'
        memory_context = await asyncio.to_thread(self.memory.context, self.scope, snapshot_hash=snapshot['snapshot_hash']) if self.memory is not None else None
        if self.repository.account(self.scope)['revision'] != snapshot['bindings']['account_revision']:
            raise DemoError('DEMO_FRAME_BINDING_CHANGED')
        self.repository.begin_decision(self.scope, run_id, snapshot, strategy, source, memory_context=memory_context)
        try:
            if self._strategy() != strategy:
                raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            if snapshot['quality']['status'] != 'READY':
                raise DemoError('DEMO_REQUIRED_EVIDENCE_MISSING')
            if before_model is not None: before_model()
            if mode == 'RULE':
                if not callable(self.rule_assessor) or not strategy.get('rule_revision'):
                    raise DemoError('DEMO_RULE_SOURCE_REQUIRED')
                value = await asyncio.to_thread(self.rule_assessor, deepcopy(snapshot))
                if inspect.isawaitable(value): value = await value
                validate_assessment(value, snapshot)
                result = {'decision': {**value, 'assessment_status': 'DECIDED', 'snapshot_hash': snapshot['snapshot_hash'],
                    'snapshot_id': snapshot['snapshot_id'], 'strategy_revision': strategy['revision'], 'source': source}}
            else:
                request_options = {'decision_policy': strategy['decision_policy']}
                if before_model is not None:
                    def request_guard():
                        before_model()
                        return True
                    request_options['request_guard'] = request_guard
                result = await self.models.analyze('BTCUSDT', strategy['analysis_mode'], strategy['model_profile_ids'],
                    strategy['model_revisions'], 'demo:' + digest([self.scope.key, run_id]),
                    {'analysis_snapshot': snapshot, 'quote_max_age_seconds': 5, **({'strategy_memory': memory_context,
                    'memory_binding': {'schema_revision': 'demo-memory-binding-v1', 'scope_key': self.scope.key, 'context_hash': memory_context['context_hash']},
                    'memory_record_decision': False} if memory_context is not None else {})}, **request_options)
            if self._strategy() != strategy:
                raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            return self.repository.finish_decision(self.scope, run_id, result=result)
        except Exception as exc:
            code = exc.code if isinstance(exc, DemoError) else 'DEMO_ASSESSMENT_FAILED'
            self.repository.finish_decision(self.scope, run_id, error=code)
            raise DemoError(code) from None

    def _validate_receipt(self, row, strategy):
        decision = (row.get('result') or {}).get('decision') or {}
        snapshot = row['snapshot']
        if row['strategy'] != strategy or decision.get('assessment_status') != 'DECIDED' or decision.get('snapshot_hash') != snapshot['snapshot_hash'] or decision.get('snapshot_id') != snapshot['snapshot_id'] or decision.get('strategy_revision') != strategy['revision']:
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        memory = row.get('memory_context')
        if memory is not None:
            if memory.get('scope') != __import__('dataclasses').asdict(self.scope) or memory.get('snapshot_hash') != snapshot['snapshot_hash'] or memory.get('context_hash') != digest({key: value for key, value in memory.items() if key != 'context_hash'}):
                raise DemoError('DEMO_MEMORY_RECEIPT_INVALID')
        if row['source'] == 'assessment_v1':
            clean = {'symbol': 'BTCUSDT', 'analysis_snapshot': snapshot}
            if memory is not None:
                clean.update(strategy_memory=memory, memory_binding={'schema_revision': 'demo-memory-binding-v1', 'scope_key': self.scope.key, 'context_hash': memory['context_hash']}, memory_record_decision=False)
            if decision.get('input_hash') != self.models._hash(clean):
                raise DemoError('DEMO_MODEL_CONTEXT_RECEIPT_INVALID')
            ids, revisions = strategy['model_profile_ids'], strategy['model_revisions']
            if decision.get('required_profile_ids') != ids or decision.get('profile_revisions') != revisions or decision.get('decision_policy') != strategy['decision_policy']:
                raise DemoError('DEMO_MODEL_RECEIPT_INVALID')
            latest = {item.get('profile_id'): item for item in decision.get('component_results', []) if isinstance(item, dict)}
            if set(latest) != set(ids) or any(item.get('status') != 'SUCCEEDED' or item.get('snapshot_hash') != snapshot['snapshot_hash'] or item.get('config_revision') != revisions[key] for key, item in latest.items()):
                raise DemoError('DEMO_MODEL_RECEIPT_INVALID')
            if any(self.models.get_config(key)['revision'] != revision for key, revision in revisions.items()):
                raise DemoError('DEMO_MODEL_REVISION_CHANGED')
            if strategy['analysis_mode'] == 'consensus' and strategy['decision_policy'] == 'unanimous':
                def terms(proposal):
                    return {k: v for k, v in proposal.items() if k not in {'proposal_id', 'rationale', 'evidence_refs'}} if proposal else None
                if any(terms((item.get('response') or {}).get('proposal')) != terms(decision.get('proposal')) for item in latest.values()):
                    raise DemoError('DEMO_CONSENSUS_NOT_VERIFIED')
            elif strategy['analysis_mode'] == 'consensus':
                if (decision.get('review_report') or {}).get('status') not in {'VERIFIED', 'SOFT'}:
                    raise DemoError('DEMO_REVIEW_NOT_VERIFIED')
                if decision['review_report']['status'] == 'SOFT' and len(decision.get('adjudication_history') or []) != 1:
                    raise DemoError('DEMO_REVIEW_NOT_VERIFIED')
        value = {key: decision.get(key) for key in ENVELOPE}
        try: validate_assessment(value, snapshot)
        except Exception: raise DemoError('DEMO_ASSESSMENT_INVALID') from None
        return value

    def admit_assessment(self, run_id):
        row = self.repository.decision(self.scope, run_id)
        if row is None or row['state'] not in {'ASSESSED', 'STAGED', 'MANAGEMENT_APPLIED'}:
            raise DemoError('DEMO_ASSESSMENT_UNAVAILABLE')
        if row['state'] == 'MANAGEMENT_APPLIED':
            return self.repository.exit_update(self.scope, run_id)
        strategy = self._strategy()
        value = self._validate_receipt(row, strategy)
        if row['command_id']:
            return self.repository.command(self.scope, row['command_id'])
        proposal = value['proposal']
        if proposal is None or proposal['action'] == 'HOLD':
            return {'state': 'NO_ACTION', 'execution_authorized': False}
        if proposal['action'] == 'UPDATE_EXIT':
            current = self._frame()
            if strategy.get('allow_execution') is not True:
                raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
            binding = row['snapshot']['bindings']
            if current['bindings'] != binding:
                # Refresh timestamps are allowed, identities/revisions are not.
                keys = set(binding) - {'account_as_of'}
                if any(current['bindings'][key] != binding[key] for key in keys):
                    raise DemoError('DEMO_EXIT_UPDATE_BINDING_CHANGED')
            if strategy['mode'] == 'HYBRID' and (not callable(self.hybrid_gate) or self.hybrid_gate(value, current) is not True):
                raise DemoError('DEMO_HYBRID_GATE_REJECTED')
            return self.repository.apply_exit_update(self.scope, run_id, candidate=proposal, expected_account_revision=binding['account_revision'],
                expected_plan_revision=binding['plan_revision'], expected_duties_hash=binding['protection_hash'], strategy=strategy,
                now=int(self.clock()), bid=current['market']['book']['bid'], clock=self.clock)
        if proposal['action'] != 'SET_TARGET':
            raise DemoError('DEMO_MANAGEMENT_REQUIRES_SEPARATE_COMMAND')
        current = self._frame()
        binding = row['snapshot']['bindings']
        if any(current['bindings'][key] != binding[key] for key in ('account_revision', 'credential_revision', 'strategy_revision', 'policy_revision', 'model_revisions', 'rules_hash', 'qualification_hash', 'authorization_ref', 'code_sha')):
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        health = self.repository.health(self.scope)
        if health['unknown'] or health['pending_inbox']:
            raise DemoError('DEMO_RECOVERY_REQUIRED')
        if current['quality']['status'] != 'READY' or not current['costs']['complete']:
            raise DemoError('DEMO_REQUIRED_EVIDENCE_MISSING')
        price = decimal(proposal['entry']['limit_price'], positive=True) if proposal['entry']['order_preference'] == 'LIMIT' else decimal(current['market']['quote']['price'], positive=True)
        if proposal['entry']['order_preference'] != 'LIMIT':
            raise DemoError('DEMO_MARKET_RISK_NOT_QUALIFIED')
        before, after = decimal(row['snapshot']['market']['quote']['price']), decimal(current['market']['quote']['price'])
        if abs(after - before) * 10000 > before * decimal(proposal['entry']['maximum_entry_slippage_bps']):
            raise DemoError('DEMO_PRICE_MOVED')
        rules = current['rules']; lot = next(item for item in rules['filters'] if item['filterType'] == 'LOT_SIZE')
        step = decimal(lot['stepSize'], positive=True)
        target = decimal(proposal['target']['value'])
        if proposal['target']['sizing'] == 'QUOTE_NOTIONAL':
            target = quantity_from_notional(target, price, step, True)
        else:
            target = quantity_on_grid(target, step, False)
        held = decimal((current['portfolio']['position'] or {}).get('quantity', '0'))
        delta = target - held
        if delta == 0: return {'state': 'NO_ACTION', 'execution_authorized': False}
        side = 'BUY' if delta > 0 else 'SELL'; quantity = abs(delta)
        if side == 'BUY' and health['unprotected']:
            raise DemoError('DEMO_RECOVERY_REQUIRED')
        if self.repository.active_orders(self.scope):
            raise DemoError('DEMO_ACTIVE_ORDER_REQUIRES_RECONCILIATION')
        stop = decimal(proposal['exit_plan']['stop_price'], positive=True) if side == 'BUY' else Decimal(0)
        slippage = decimal(proposal['entry']['maximum_entry_slippage_bps'])
        if slippage > min(decimal(strategy.get('max_entry_slippage_bps', '50')), Decimal('50')):
            raise DemoError('DEMO_EXECUTABLE_PRICE_REJECTED')
        bid, ask = decimal(current['market']['book']['bid']), decimal(current['market']['book']['ask'])
        if (side == 'BUY' and (price > ask * (1 + slippage / 10000) or stop >= bid)) or (side == 'SELL' and price < bid * (1 - slippage / 10000)):
            raise DemoError('DEMO_EXECUTABLE_PRICE_REJECTED')
        fee = decimal(current['costs']['fee_rate_cap'])
        if side == 'SELL':
            quantity = quantity_on_grid(min(quantity, held / (1 + fee)), step, True)
        risk_state = self.repository.risk_summary(self.scope, mark=str(after), now=int(self.clock()))
        if side == 'BUY' and (not risk_state['complete'] or risk_state['loss'] >= decimal(strategy['max_daily_loss'])):
            raise DemoError('DEMO_DAILY_LOSS_LIMIT')
        planned_loss = max(Decimal(0), price - stop) * quantity + price * quantity * fee * 2
        if side == 'BUY' and (not current['constraints']['protection_supported'] or planned_loss > decimal(strategy['max_planned_loss']) or risk_state['loss'] + planned_loss > decimal(strategy['max_daily_loss']) or target * price > min(decimal(strategy['budget']), Decimal('10000'))):
            raise DemoError('DEMO_DETERMINISTIC_RISK_REJECTED')
        if strategy['mode'] == 'HYBRID' and (not callable(self.hybrid_gate) or self.hybrid_gate(value, current) is not True):
            raise DemoError('DEMO_HYBRID_GATE_REJECTED')
        client_id = 'd-' + digest([self.scope.key, run_id])[:30]
        request = compile_spot_order(rules, side=side, order_type='LIMIT', quantity=str(quantity), price=str(price), client_id=client_id, reference_price=str(after), filter_context=current['market']['execution_context'])
        reserve = quantity * price * (1 + fee) if side == 'BUY' else quantity * (1 + fee)
        available = current['portfolio']['account']['cash'] if side == 'BUY' else str(held)
        deadline = min(int(datetime.fromisoformat(proposal['valid_until']).timestamp()), int(datetime.fromisoformat(proposal['entry']['entry_deadline']).timestamp()))
        risk = {'run_id': run_id, 'memory_hash': (row.get('memory_context') or {}).get('context_hash'), 'snapshot_hash': row['snapshot']['snapshot_hash'], 'strategy_revision': strategy['revision'],
            'qualification_hash': binding['qualification_hash'], 'policy_revision': strategy['policy_revision'], 'model_revisions': strategy['model_revisions'], 'authorization_ref': strategy['authorization_ref'],
            'rules_hash': binding['rules_hash'], 'planned_loss': str(planned_loss), 'proposal_hash': digest(proposal)}
        plan = {'stop_trigger': proposal['exit_plan']['stop_trigger'], 'stop_price': str(stop), 'maximum_holding_seconds': proposal['exit_plan']['maximum_holding_seconds'], 'proposal_id': proposal['proposal_id'],
                'authorization_ref': strategy['authorization_ref'], 'mode': strategy['protection_mode']} if side == 'BUY' else None
        command = self.repository.stage(self.scope, command_id='demo:' + digest([self.scope.key, run_id]), request=request,
            reserve_asset='USDT' if side == 'BUY' else 'BTC', reserve_amount=str(reserve), available=available,
            authorization_ref=strategy['authorization_ref'], expires_at=deadline, now=int(self.clock()),
            purpose='ENTRY' if side == 'BUY' else 'REDUCTION', protection_plan=plan, risk_receipt=risk,
            expected_account_revision=binding['account_revision'], expected_strategy_revision=strategy['revision'], reject_active_orders=True)
        self.repository.bind_decision_command(self.scope, run_id, command['command_id'])
        return command

    def drain_inbox(self, *, limit=100):
        processed = 0
        for observation in self.repository.pending_observations(self.scope, limit):
            if not observation.get('scope'):
                raise DemoError('DEMO_OBSERVATION_SCOPE_MISSING')
            DemoReportVerifier(self.repository).process(DemoScope(**observation['scope']), observation['observation_id'])
            processed += 1
        return {'processed': processed, 'remaining': self.repository.health(self.scope)['pending_inbox']}

    def dispatch(self, command_id):
        if self.owner is None:
            raise DemoError('DEMO_EXECUTION_OWNER_REQUIRED')
        strategy, current = self._strategy(), self._frame()
        if strategy.get('allow_execution') is not True:
            raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
        command = self.repository.command(self.scope, command_id)
        risk = command['risk_receipt'] or {}
        binding = current['bindings']
        if any(risk.get(key) != binding[key] for key in ('strategy_revision', 'policy_revision', 'model_revisions', 'rules_hash', 'qualification_hash', 'authorization_ref')):
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        if command['admission_revision'] != binding['account_revision']:
            raise DemoError('DEMO_DECISION_BINDING_CHANGED')
        if risk.get('run_id'):
            self._validate_receipt(self.repository.decision(self.scope, risk['run_id']), strategy)
        account = self.repository.account(self.scope); evidence = account.get('qualification') or {}
        health = self.repository.health(self.scope, now=int(self.clock()))
        context = AdmissionContext(now=int(self.clock()), quote_at=int(datetime.fromisoformat(current['market']['quote']['as_of']).timestamp()),
            account_at=int(datetime.fromisoformat(binding['account_as_of']).timestamp()), quote_source='BINANCE_DEMO',
            model_source='assessment_v1' if risk.get('run_id') and strategy['mode'] != 'RULE' else 'verified_rule_v1',
            decision_expires_at=min(command['expires_at'], strategy['authorized_until']), capability_state=evidence.get('state'),
            code_sha=self.current_code_sha, qualified_sha=evidence.get('code_sha'),
            private_stream_healthy=bool(self.stream and self.stream.healthy and self.stream.credential_ref == account['credential_ref']),
            account_state=account['state'], authorized=True, budget=strategy['budget'], available=current['portfolio']['account']['cash'],
            risk_limit=strategy['budget'], unresolved_unknown=health['unknown'], unprotected=health['unprotected'], pending_inbox=health['pending_inbox'])
        return self.owner.dispatch(self.scope, command_id, context=context)

    def stage_cancel(self, target_command_id):
        strategy, current = self._strategy(), self._frame()
        target = self.repository.command(self.scope, target_command_id)
        if target['method'] != 'POST' or target['state'] not in {'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED'}:
            raise DemoError('DEMO_CANCEL_TARGET_INVALID')
        if target['order_status'] in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}:
            return {'state': 'NO_ACTION', 'execution_authorized': False}
        now = int(self.clock()); binding = current['bindings']
        self.repository.abandon_stale_unsent(self.scope, now=now)
        previous = self.repository.pending_cancel(self.scope, target['client_id'])
        if previous: return previous
        identity = 'cancel:' + digest([target_command_id, target['request_hash'], binding['account_revision'], self.repository.cancel_lineage(self.scope, target['client_id'])])
        try: return self.repository.command(self.scope, identity)
        except DemoError as exc:
            if exc.code != 'DEMO_COMMAND_NOT_FOUND': raise
        risk = {key: binding[key] for key in ('strategy_revision', 'policy_revision', 'model_revisions', 'rules_hash', 'qualification_hash', 'authorization_ref')}
        risk['target_command_id'] = target_command_id
        return self.repository.stage(self.scope, command_id=identity,
            request={'symbol': target['request']['symbol'], 'origClientOrderId': target['client_id']}, method='DELETE', purpose='CANCEL',
            reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref=strategy['authorization_ref'],
            expires_at=min(now + 60, strategy['authorized_until']), now=now, risk_receipt=risk,
            expected_account_revision=binding['account_revision'], expected_strategy_revision=strategy['revision'])

    def protection_tick(self):
        duties = self.repository.protection_duties(self.scope)
        if not duties:
            self.repository.abandon_unneeded_protection(self.scope)
            if not any(row['purpose'] == 'PROTECTION' for row in self.repository.active_orders(self.scope)):
                return {'state': 'NO_EXPOSURE', 'commands': []}
        strategy, current = self._strategy(), self._frame()
        account = self.repository.account(self.scope); evidence = account.get('qualification') or {}
        if not self.owner or not self.stream or not self.stream.healthy or self.stream.credential_ref != account['credential_ref'] or evidence.get('protection') != 'HOSTED' or evidence.get('code_sha') != self.current_code_sha or evidence.get('state') != 'DEMO_VERIFIED':
            raise DemoError('DEMO_HOSTED_PROTECTION_UNAVAILABLE')
        last = self.owner.transport.last_price('BTCUSDT')
        if last.get('symbol') != 'BTCUSDT':
            raise DemoError('DEMO_MARKET_SOURCE_INVALID')
        price = decimal(last['price'], positive=True); now = int(self.clock())
        triggered = []
        for duty in duties:
            plan = duty['plan'] or {}
            if plan.get('authorization_ref') != strategy['authorization_ref'] or plan.get('mode') != 'HOSTED' or plan.get('stop_trigger', 'LAST') != 'LAST':
                raise DemoError('DEMO_PROTECTION_PLAN_UNSUPPORTED')
            if price <= decimal(plan['stop_price'], positive=True) or now >= duty['opened_at'] + plan['maximum_holding_seconds']:
                triggered.append(duty)
        self.repository.abandon_stale_unsent(self.scope, now=now)
        active = self.repository.active_orders(self.scope)
        expired = [row for row in active if row['purpose'] == 'PROTECTION' and (not duties or now >= row['expires_at'])]
        if any(row['state'] == 'ACKNOWLEDGED' and row['order_status'] is None for row in expired):
            return {'state': 'RECONCILIATION_REQUIRED', 'commands': []}
        if active and (triggered or expired):
            entries = [row for row in active if row['purpose'] == 'ENTRY' and row['state'] != 'DURABLE_READY']
            # A resting GTC exit has no inherent deadline. Cancel only a known
            # active order after its send-authority deadline; an uncertain order
            # must first be reconciled by its original identity.
            expired_exits = [row for row in expired if row['purpose'] == 'PROTECTION'
                and row['state'] in {'ACKNOWLEDGED', 'OBSERVED'} and row['order_status'] not in {None, 'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}
                ]
            cancellations = [self.stage_cancel(row['command_id']) for row in entries + expired_exits]
            return {'state': 'CANCEL_ENTRY_FIRST' if entries else 'CANCEL_STALE_PROTECTION' if expired_exits else 'MITIGATION_PENDING',
                    'commands': [command for command in cancellations if command['state'] == 'DURABLE_READY']}
        if triggered:
            # Conservative base-fee headroom may leave filter-sized dust. It
            # remains an explicit unsatisfied responsibility, never called FLAT.
            if any(row['mitigation_command_id'] for row in triggered):
                raise DemoError('DEMO_PROTECTION_RESIDUAL_REVIEW_REQUIRED')
            rules = current['rules']; filters = {item['filterType']: item for item in rules['filters']}
            held = sum((decimal(row['remaining_quantity']) for row in triggered), Decimal(0))
            fee = decimal(current['costs']['fee_rate_cap'])
            quantity = quantity_on_grid(held / (1 + fee), decimal(filters['LOT_SIZE']['stepSize'], positive=True), True)
            bid = decimal(current['market']['book']['bid'], positive=True)
            tick = decimal(filters['PRICE_FILTER']['tickSize'], positive=True)
            limit = (bid // tick) * tick
            fill_keys = sorted(row['fill_key'] for row in triggered)
            lineage = self.repository.mitigation_lineage(self.scope, fill_keys)
            identity = digest([self.scope.key, fill_keys, lineage, strategy['revision'], current['bindings']['account_revision']])
            request = compile_spot_order(rules, side='SELL', order_type='LIMIT', quantity=str(quantity), price=str(limit), client_id='p-' + identity[:30], reference_price=str(price), filter_context=current['market']['execution_context'])
            binding = current['bindings']; risk = {key: binding[key] for key in ('strategy_revision', 'policy_revision', 'model_revisions', 'rules_hash', 'qualification_hash', 'authorization_ref')}
            risk['protection_fill_keys'] = [row['fill_key'] for row in triggered]
            risk['fee_rate_cap'] = str(fee)
            command = self.repository.stage(self.scope, command_id='protect:' + identity, request=request, purpose='PROTECTION',
                reserve_asset='BTC', reserve_amount=str(quantity * (1 + fee)), available=str(held), authorization_ref=strategy['authorization_ref'],
                expires_at=min(now + 60, strategy['authorized_until']), now=now, risk_receipt=risk,
                expected_account_revision=binding['account_revision'], expected_strategy_revision=strategy['revision'], reject_active_orders=True)
            self.repository.bind_mitigation(self.scope, risk['protection_fill_keys'], command['command_id'])
            return {'state': 'MITIGATION_STAGED', 'commands': [command]}
        if self.repository.health(self.scope, now=now)['unknown']:
            raise DemoError('DEMO_RECOVERY_REQUIRED')
        for duty in duties:
            if duty['state'] in {'REQUIRED', 'HOSTED_ACTIVE'}:
                self.repository.renew_hosted_protection(self.scope, fill_key=duty['fill_key'], now=now, until=min(now + 5, strategy['authorized_until']), authorization_ref=strategy['authorization_ref'])
        return {'state': 'HOSTED_ACTIVE', 'commands': []}

    def status(self):
        return {'environment': 'BINANCE_DEMO', 'configured': True, 'account_state': self.repository.account(self.scope)['state'],
                'health': self.repository.health(self.scope), 'execution_authorized': False, 'code_sha': self.current_code_sha}
