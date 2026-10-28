"""Durable, opt-in AI scheduling and local PAPER execution only.

All state transitions serialize on Store.transaction's account lock. Network I/O
is outside that lock. An expired job is terminal and never resubmitted.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from sqlalchemy import Column, JSON, String, Table, insert, select, update
from starlette.concurrency import run_in_threadpool

from . import model_repository as repository
from .errors import ApiError
from .store import FEE, metadata, now, stamp, rows, strategies, positions
from .validation import number

schedules = Table("console_ai_schedules", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
jobs = Table("console_ai_jobs", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
notifications = Table("console_notifications", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
log = logging.getLogger(__name__)


def read(connection, table, identifier):
    return connection.execute(select(table.c.data).where(table.c.id == identifier)).scalar_one_or_none()


def put(connection, table, identifier, data):
    if read(connection, table, identifier) is None:
        connection.execute(insert(table).values(id=identifier, data=data))
    else:
        connection.execute(update(table).where(table.c.id == identifier).values(data=data))


class AutomationService:
    def __init__(self, store, models, quote_snapshot, settings, feed=None):
        self.store, self.models, self.quote_snapshot, self.settings = store, models, quote_snapshot, settings
        self.feed = feed
        self.worker_id = str(uuid4())

    @staticmethod
    def _active(strategy):
        return strategy.get("status") == "ACTIVE" and strategy.get("mode") in ("AI", "RULE", "HYBRID") and strategy.get("_activation_authorized") is True

    @staticmethod
    def _ready(config):
        return bool(config and config.get("enabled") and config.get("model")
                    and (config.get("local_mode") or config.get("key_available", bool(config.get("encrypted_key")))))

    @staticmethod
    def _binding(strategy):
        return (strategy.get("analysis_mode", "single"),
                strategy.get("model_profile_ids", [repository.DEFAULT_PROFILE_ID] if strategy.get("mode") == "AI" else []))

    @staticmethod
    def _job_binding(job):
        # Old durable claims only recorded the default model revision.
        identifiers = job.get("model_profile_ids", [repository.DEFAULT_PROFILE_ID])
        revisions = job.get("profile_revisions")
        if revisions is None:
            revisions = {repository.DEFAULT_PROFILE_ID: job["model_revision"]}
        return job.get("analysis_mode", "single"), identifiers, revisions

    @staticmethod
    def _profile_map(configs):
        # Preserve direct _claim(get_config(), ...) callers and legacy test doubles.
        if isinstance(configs, dict):
            if "profiles" in configs:
                configs = configs["profiles"]
            elif "revision" in configs:
                return {configs.get("id", repository.DEFAULT_PROFILE_ID): configs}
            else:
                return configs
        return {config["id"]: config for config in configs}

    def _configs(self):
        return self.models.list_configs() if hasattr(self.models, "list_configs") else self.models.get_config()

    def _notify(self, connection, job, decision, status, reason):
        # Job ID is a uniqueness constraint, including recovery after restart.
        if read(connection, notifications, job["id"]) is not None:
            return
        failed = status in ("ERROR", "BLOCKED", "SKIPPED")
        message = reason
        if decision and status != "ERROR":
            action = {"BUY": "建议买入", "SELL": "建议卖出", "HOLD": "建议观察"}.get(decision.get("action"), "分析结果")
            if decision.get("consensus_status") == "AGREED":
                action = "所选模型方向一致 · " + action
            elif decision.get("consensus_status") == "DISAGREED":
                action = "所选模型存在方向分歧 · 建议观察"
            summary = " ".join(str(decision.get("summary", "")).split())[:160]
            message = f"{action}：{summary}。{reason}"
        item = dict(id=job["id"], kind="AI_ERROR" if status == "ERROR" else "AI_RESULT",
                    title=job["strategy_name"] + " · AI 分析", message=message[:400],
                    severity="warning" if failed else "success" if status == "FILLED" else "info",
                    created_at=stamp(), read_at=None, strategy_id=job["strategy_id"],
                    decision_id=decision.get("id") if decision else None, order_id=decision.get("order_id") if decision else None)
        put(connection, notifications, item["id"], item)

    def _annotate(self, connection, job, decision, status, reason):
        if not decision:
            return None
        decision = dict(decision, strategy_id=job["strategy_id"], strategy_name=job["strategy_name"],
                        execution_status=status, execution_reason=reason, order_id=decision.get("order_id"))
        mode, identifiers, revisions = self._job_binding(job)
        decision.setdefault("analysis_mode", mode)
        decision.setdefault("model_profile_ids", identifiers)
        decision.setdefault("profile_revisions", revisions)
        record = repository.read_run(connection, job["model_key"])
        if record and record.get("status") == "SUCCEEDED":
            record["result"]["decision"] = decision
            repository.write_run(connection, job["model_key"], record)
        return decision

    def _attach_late_result(self, connection, job):
        # A provider may complete after another worker recovered its expired job.
        # Keep that advice visibly associated with the terminal, nonexecuted job.
        run = repository.read_run(connection, job["model_key"])
        if job.get("decision") or not run or run.get("status") != "SUCCEEDED":
            return
        decision = self._annotate(connection, job, run["result"]["decision"], job["execution_status"], job["execution_reason"])
        job["decision"] = decision
        put(connection, jobs, job["id"], job)
        notification = read(connection, notifications, job["id"])
        if notification:
            notification["decision_id"] = decision["id"]
            put(connection, notifications, job["id"], notification)

    def _complete(self, connection, job, decision, status, reason):
        decision = self._annotate(connection, job, decision, status, reason)
        job.update(status="FINISHED", execution_status=status, execution_reason=reason,
                   decision=decision, completed_at=stamp())
        if decision and decision.get("analysis_mode") == "consensus":
            job.update(consensus_status=decision.get("consensus_status"), model_results=decision.get("model_results", []))
        put(connection, jobs, job["id"], job)
        schedule = read(connection, schedules, job["strategy_id"]) or {}
        schedule.update(job_id=None, last_analysis_at=stamp(), last_analysis_error=reason if status in ("ERROR", "BLOCKED") else None,
                        runtime_status="ERROR" if status == "ERROR" else "READY",
                        next_analysis_at=(now() + timedelta(seconds=job["interval"])).isoformat())
        if decision and decision.get('schema_revision') == 'assessment_v1':
            schedule.update(watch_conditions=decision.get('watch_conditions', []),
                watch_baseline=job.get('watch_observation'), last_assessment=decision.get('assessment_status'))
            if decision.get('next_review_at'):
                from trading_system.contracts.canonical import utc_timestamp
                try:
                    if utc_timestamp(decision['next_review_at']) > now():
                        schedule['next_analysis_at'] = decision['next_review_at']
                except (ValueError, TypeError):
                    pass
        put(connection, schedules, job["strategy_id"], schedule)
        self._notify(connection, job, decision, status, reason)

    def _claim(self, config, market):
        available = self._profile_map(config)
        with self.store.transaction() as connection:
            # Recovery never replays a model call or executes old advice.
            current_time = stamp()
            for job in rows(connection, jobs):
                if job["status"] == "FINISHED":
                    self._attach_late_result(connection, job)
                if job["status"] == "RUNNING" and job["lease_until"] <= current_time:
                    run = repository.read_run(connection, job["model_key"])
                    decision = run["result"]["decision"] if run and run.get("status") == "SUCCEEDED" else None
                    self._complete(connection, job, decision, "SKIPPED", "分析任务已中断或租约过期；未重试模型或下单。")
            if not self.settings.automation_enabled:
                return None
            saved_schedules = {r.id: r.data for r in connection.execute(select(schedules))}
            # Oldest due first: a slow provider must not starve later strategies.
            candidates = sorted(rows(connection, strategies), key=lambda s: saved_schedules.get(s["id"], {}).get("next_analysis_at") or s["created_at"])
            for strategy in candidates:
                if not self._active(strategy):
                    continue
                schedule = read(connection, schedules, strategy["id"]) or {}
                if schedule.get("job_id"):
                    continue
                if schedule.get('watch_conditions') and schedule.get('watch_baseline'):
                    from .decision_execution import watch_triggered
                    try:
                        price = self.store._price(market, strategy['symbol'], self.settings.quote_max_age_seconds)
                        if watch_triggered(schedule['watch_conditions'], schedule['watch_baseline'], {'price': str(price)}):
                            schedule['next_analysis_at'] = current_time
                    except ApiError:
                        pass
                if schedule.get("activated_at") == strategy.get("_activated_at") and schedule.get("next_analysis_at") and schedule["next_analysis_at"] > current_time:
                    continue
                analysis_mode, identifiers = self._binding(strategy)
                selected = [available.get(identifier) for identifier in identifiers]
                current = [repository.read_profile(connection, identifier) for identifier in identifiers]
                valid_count = (not identifiers) if strategy['mode'] == 'RULE' else (len(identifiers) == 1 if analysis_mode == "single" else analysis_mode == "consensus" and 2 <= len(identifiers) <= 3)
                if (not valid_count or len(set(identifiers)) != len(identifiers)
                        or any(not self._ready(public) or not self._ready(saved) or public["revision"] != saved["revision"]
                               for public, saved in zip(selected, current))):
                    schedule.update(runtime_status="WAITING_MODEL")
                    put(connection, schedules, strategy["id"], schedule)
                    continue
                try:
                    self.store._price(market, strategy["symbol"], self.settings.quote_max_age_seconds)
                except ApiError:
                    schedule.update(runtime_status="WAITING_QUOTES")
                    put(connection, schedules, strategy["id"], schedule)
                    continue
                identifier = str(uuid4())
                job = dict(id=identifier, strategy_id=strategy["id"], strategy_name=strategy["name"],
                           decision_mode=strategy['mode'], rule=strategy.get('rule'),
                           strategy_revision=strategy["revision"], analysis_mode=analysis_mode,
                           decision_policy=strategy.get("decision_policy", "unanimous"),
                           decision_schema=strategy.get("decision_schema", "legacy"), task_profile=strategy.get("task_profile"),
                           model_profile_ids=list(identifiers), profile_revisions={item["id"]: item["revision"] for item in current},
                           profile_names={item["id"]: item["name"] for item in current},
                           symbol=strategy["symbol"], auto_execute=strategy.get("auto_execute", False),
                           interval=strategy.get("analysis_interval_seconds", 300),
                           model_key="auto-analysis:" + identifier, worker_id=self.worker_id,
                           created_at=current_time, lease_until=(now() + timedelta(seconds=max(180, max((item.get("timeout_seconds", 120) for item in current), default=120) + 60))).isoformat(), status="RUNNING")
                if identifiers == [repository.DEFAULT_PROFILE_ID]:
                    job["model_revision"] = current[0]["revision"]
                put(connection, jobs, identifier, job)
                schedule.update(job_id=identifier, runtime_status="RUNNING", activated_at=strategy.get("_activated_at"))
                put(connection, schedules, strategy["id"], schedule)
                return job
        return None

    def _execute(self, connection, job, strategy, decision, market):
        if decision.get("schema_revision") == "assessment_v1":
            return self._execute_assessment(connection, job, strategy, decision, market)
        if strategy['mode'] != 'RULE':
            raise ApiError('LEGACY_REAUTH_REQUIRED', '旧模型建议仅供查阅；请明确批准新版策略后重新分析。', 409)
        action = decision.get("action")
        if decision.get("symbol") != job["symbol"] or action not in ("BUY", "SELL", "HOLD"):
            raise ApiError("INVALID_MODEL_ADVICE", "模型建议标的或方向无效。")
        if action == "HOLD":
            return "NO_ACTION", "模型建议观望；未下单。"
        account = self.store._state(connection)["account"]
        held = connection.execute(select(positions.c.data).where(positions.c.symbol == job["symbol"])).scalar_one_or_none()
        if self.store.execution:
            held = next((item for item in self.store.execution.account_view()['positions'] if item['symbol'] == job['symbol']), None)
        if held and held.get("strategy_id") != strategy["id"]:
            raise ApiError("POSITION_OWNERSHIP", "该品种持仓属于手动操作或其他策略；未自动下单。", 409)
        price = self.store._price(market, job["symbol"], self.settings.quote_max_age_seconds)
        if strategy['mode'] in ('RULE', 'HYBRID') and action == 'BUY' and not self._rule_matches(strategy['rule'], price):
            return 'NO_ACTION', '当前价格不满足服务端规则；未创建命令。'
        from trading_system.contracts.canonical import utc_timestamp
        try:
            age = (now() - utc_timestamp(decision['created_at'])).total_seconds()
            basis_age = (now() - utc_timestamp(decision['data_as_of'])).total_seconds()
            quote_at = next(q['as_of'] for q in market['quotes'] if q['symbol'] == job['symbol'])
            ordered = utc_timestamp(decision['data_as_of']) <= utc_timestamp(quote_at)
            valid = 0 <= age <= 120 and 0 <= basis_age <= self.settings.quote_max_age_seconds and ordered
        except (KeyError, ValueError, TypeError, StopIteration):
            valid = False
        if not valid:
            raise ApiError('STALE_DECISION', '决策或其行情依据已过期、缺失或时间乱序；未创建命令。', 409)
        if action == "SELL":
            if not held:
                return "NO_ACTION", "策略没有可卖出的自有持仓。"
            quantity = Decimal(held["quantity"])
        else:
            equity, _ = self.store._valuation(connection, account, market, fresh=True, maximum_age=self.settings.quote_max_age_seconds)
            target = min(Decimal(strategy["budget"]), equity * Decimal(strategy["target_pct"]) / 100)
            owned = (Decimal(held["quantity"]) * price * (1 + FEE)
                     + Decimal(held.get("_entry_fees", "0"))) if held else Decimal(0)
            # Allocation backs both entry and planned exit fees. Existing
            # exposure keeps its remaining entry-fee basis plus one exit fee.
            funds = min(max(Decimal(0), target - owned), Decimal(account["cash"]))
            quantity = (funds / (price * (1 + 2 * FEE))).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if quantity <= 0:
                return "NO_ACTION", "策略已达到目标配置或可用资金不足最小下单精度。"
        result = self.store._order(connection, dict(symbol=job["symbol"], side=action, quantity=number(quantity), strategy_id=strategy["id"],
            decision_run_id=job['id'], rationale=decision.get('summary', '服务端规则或模型决策')),
                                   "auto-order:" + job["id"], market, self.settings.quote_max_age_seconds)
        decision.update(order_id=result["order"]["id"], command_id=result['order']['command_id'], risk_status="PAPER_PASSED")
        return "PENDING", "已通过统一 PAPER 风控并持久化命令；等待执行与成交事件。"

    def _execute_assessment(self, connection, job, strategy, decision, market):
        from trading_system.contracts.canonical import utc_timestamp
        try:
            valid = (0 <= (now() - utc_timestamp(decision['created_at'])).total_seconds() <= 120
                and 0 <= (now() - utc_timestamp(decision['data_as_of'])).total_seconds() <= self.settings.quote_max_age_seconds)
        except (KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            raise ApiError('STALE_DECISION', '决策或依据已过期。', 409)
        proposal = decision.get('proposal')
        if proposal and proposal.get('action') != 'HOLD':
            basis = (job.get('watch_observation') or {}).get('price')
            current = self.store._price(market, job['symbol'], self.settings.quote_max_age_seconds)
            if not basis or abs(current / Decimal(basis) - 1) * 10000 > Decimal(proposal['entry']['maximum_entry_slippage_bps']):
                raise ApiError('MARKET_REPLAN_REQUIRED', '执行报价已超出提案价格容忍范围。', 409)
        if not proposal or proposal.get('action') == 'HOLD':
            return 'NO_ACTION', '有效观察结论；原保护和退出责任保持。'
        if self.store.execution is None or self.store.sqlite:
            raise ApiError('UNIFIED_PAPER_REQUIRED', '统一 PAPER 执行服务未配置。', 503)
        from .decision_execution import proposal_target
        current = self.store._price(market, job['symbol'], self.settings.quote_max_age_seconds)
        target = proposal_target(proposal['target'], current)
        held = next((p for p in self.store.execution.account_view()['positions'] if p['symbol'] == job['symbol']), None)
        actual = Decimal(held['quantity']) if held else Decimal(0)
        if target > actual:
            self.store._check_buy(connection, self.store._state(connection), job['symbol'], target-actual,
                current, market, self.settings.quote_max_age_seconds, strategy)
        result = self.store.execution.accept_proposal(proposal, 'auto-proposal:' + job['id'], market,
            self.settings.quote_max_age_seconds, self.store._state(connection), strategy, decision)
        if result.get('status') == 'NO_ACTION':
            return 'NO_ACTION', '已达到提案目标；未重复下单。'
        decision.update(order_id=result['id'], command_id=result['command_id'], risk_status='PAPER_PASSED')
        return 'PENDING', '目标提案已进入统一 PAPER 风控与持久命令链。'

    def _assessment_guard(self, job, decision):
        mode, identifiers, revisions = self._job_binding(job)
        def blocked():
            return 'BLOCKED', '分析证据、参与者、配置修订或复核链无效；未创建命令。'
        if decision.get('assessment_status') != 'DECIDED':
            return 'BLOCKED', decision.get('reason_code', 'ASSESSMENT_NOT_DECIDED')
        if (decision.get('strategy_revision') != job['strategy_revision']
                or decision.get('decision_policy') != job.get('decision_policy', 'unanimous')
                or not job.get('snapshot_hash') or decision.get('snapshot_hash') != job['snapshot_hash']
                or decision.get('snapshot_id') != job.get('snapshot_id')
                or decision.get('required_profile_ids') != identifiers
                or decision.get('profile_revisions') != revisions):
            return blocked()
        attempts = decision.get('component_results', [])
        results = list({r.get('profile_id'): r for r in attempts if isinstance(r, dict)}.values())
        if (not isinstance(results, list) or not results
                or {r.get('profile_id') for r in results} != set(identifiers)
                or any(r.get('status') != 'SUCCEEDED' or r.get('snapshot_hash') != job['snapshot_hash']
                       or r.get('config_revision') != revisions.get(r.get('profile_id')) for r in results)):
            return blocked()
        if mode == 'consensus' and job.get('decision_policy', 'unanimous') == 'unanimous':
            def terms(proposal):
                return {k: v for k, v in proposal.items() if k not in ('proposal_id', 'rationale', 'evidence_refs')} if isinstance(proposal, dict) else None
            expected = terms(decision.get('proposal'))
            if any(terms((r.get('response') or {}).get('proposal')) != expected for r in results):
                return blocked()
        if mode == 'consensus' and job.get('decision_policy') == 'evidence_review':
            review = decision.get('review_report') or {}
            if review.get('status') not in ('VERIFIED', 'SOFT'):
                return blocked()
            if review.get('status') == 'SOFT':
                history = decision.get('adjudication_history') or []
                if len(history) != 1 or any(r.get('status') != 'SUCCEEDED' or r.get('profile_id') != identifiers[0]
                    or r.get('snapshot_hash') != job['snapshot_hash'] or r.get('config_revision') != revisions[identifiers[0]] for r in history):
                    return blocked()
        return None

    @staticmethod
    def _rule_matches(rule, price):
        return price <= Decimal(rule['price']) if rule['operator'] == 'LTE' else price >= Decimal(rule['price'])

    def _authorized(self, connection, job):
        strategy = read(connection, strategies, job["strategy_id"])
        mode, identifiers, revisions = self._job_binding(job)
        configs = {identifier: repository.read_profile(connection, identifier) for identifier in identifiers}
        valid = (self.settings.automation_enabled and job["lease_until"] > stamp() and strategy and self._active(strategy)
                 and strategy["revision"] == job["strategy_revision"] and strategy["symbol"] == job["symbol"]
                 and self._binding(strategy) == (mode, identifiers) and set(identifiers) == set(revisions)
                 and all(self._ready(config) and config["revision"] == revisions[identifier] for identifier, config in configs.items()))
        return strategy if valid else None

    def _consensus_guard(self, job, decision):
        if decision.get("schema_revision") == "assessment_v1":
            return self._assessment_guard(job, decision)
        mode, identifiers, revisions = self._job_binding(job)
        if mode != "consensus":
            # Refusal states remain nonexecuting even if an inconsistent service
            # response attaches them to a single-model claim.
            if decision.get("consensus_status") in ("DISAGREED", "INCOMPLETE"):
                decision["action"] = "HOLD"
                if decision["consensus_status"] == "INCOMPLETE":
                    return "ERROR", "模型分析结果不完整；未自动下单。"
                return "NO_ACTION", "模型分析存在方向分歧；未自动下单。"
            return None
        outcomes = decision.get("model_results")
        complete = (isinstance(outcomes, list) and len(outcomes) == len(identifiers)
                    and all(isinstance(item, dict) for item in outcomes)
                    and {item.get("profile_id") for item in outcomes} == set(identifiers)
                    and all(item.get("status") == "SUCCEEDED" and item.get("config_revision") == revisions[item["profile_id"]]
                            and isinstance(item.get("decision"), dict) and item["decision"].get("symbol") == job["symbol"]
                            and item["decision"].get("action") in ("BUY", "SELL", "HOLD") for item in outcomes))
        if not complete or decision.get("consensus_status") not in ("AGREED", "DISAGREED"):
            decision.update(action="HOLD", consensus_status="INCOMPLETE")
            return "ERROR", "交叉分析结果不完整：所选模型失败、缺少结果或结果无效；未自动下单。"
        actions = {item["decision"]["action"] for item in outcomes}
        if decision.get("consensus_status") == "DISAGREED" or len(actions) != 1:
            decision.update(action="HOLD", consensus_status="DISAGREED")
            return "NO_ACTION", "所选模型存在方向分歧；未自动下单。"
        if decision.get("action") not in actions:
            decision.update(action="HOLD", consensus_status="INCOMPLETE")
            return "ERROR", "交叉分析汇总与模型结果不一致；未自动下单。"
        return None

    def _before_dispatch(self, claimed):
        with self.store.transaction() as connection:
            job = read(connection, jobs, claimed["id"])
            return bool(job and job["status"] == "RUNNING" and job["worker_id"] == self.worker_id
                        and self._authorized(connection, job))

    def _finish(self, claimed, result=None, error=None):
        market = self.quote_snapshot()
        with self.store.transaction() as connection:
            job = read(connection, jobs, claimed["id"])
            if not job or job["worker_id"] != self.worker_id:
                return
            if job["status"] != "RUNNING":
                self._attach_late_result(connection, job)
                return
            decision = dict(result["decision"]) if result else None
            strategy = self._authorized(connection, job)
            consensus_guard = self._consensus_guard(job, decision) if decision else None
            if not strategy:
                status, reason = "SKIPPED", "策略、模型配置或任务租约已变更；本次结果不执行。"
            elif error:
                status, reason = "ERROR", error
            elif decision is None:
                status, reason = "SKIPPED", "任务已取消；未调用模型或下单。"
            elif consensus_guard:
                status, reason = consensus_guard
            elif not job["auto_execute"] or not strategy.get("auto_execute"):
                status, reason = "NOT_ENABLED", "分析已完成；此周期未授权自动 PAPER 下单。"
            else:
                try:
                    with connection.begin_nested():
                        status, reason = self._execute(connection, job, strategy, decision, market)
                except ApiError as exc:
                    # The savepoint has rolled back; persist the observed cause
                    # in the outer account transaction before swallowing denial.
                    if getattr(exc, 'daily_loss_lock', None):
                        self.store._save_daily_lock(connection, exc.daily_loss_lock)
                    status, reason = "BLOCKED", exc.message
                    decision.update(risk_status="PAPER_REJECTED", order_id=None)
            self._complete(connection, job, decision, status, reason)

    async def _wake_bar_watches(self):
        """Refresh data only; an unchanged/missing bar never consumes model budget."""
        import time
        if self.feed is None or time.monotonic() < getattr(self, '_watch_poll_after', 0):
            return
        self._watch_poll_after = time.monotonic() + 15
        with self.store.transaction() as connection:
            pending = [(strategy, read(connection, schedules, strategy['id']) or {})
                       for strategy in rows(connection, strategies) if self._active(strategy)]
        from .analysis_snapshot import build_analysis_context
        from .decision_execution import watch_triggered
        for strategy, schedule in pending:
            conditions = [c for c in schedule.get('watch_conditions', []) if c.get('kind') == 'NEW_BAR']
            if not conditions or schedule.get('job_id') or not schedule.get('watch_baseline'):
                continue
            try:
                context = await build_analysis_context(self.store, self.feed, strategy['symbol'],
                    self.settings.quote_max_age_seconds,
                    task_profile={**(strategy.get('task_profile') or {}), 'strategy_revision': strategy['revision']})
                snapshot = context['analysis_snapshot']
                if snapshot['quality']['status'] != 'READY':
                    continue
                closed = [v['closed_until'] for v in snapshot['market']['features'].values() if v.get('closed_until')]
                if closed and watch_triggered(conditions, schedule['watch_baseline'], {'closed_until': max(closed), 'closed_by_interval': {k:v['closed_until'] for k,v in snapshot['market']['features'].items()}}):
                    with self.store.transaction() as connection:
                        latest = read(connection, schedules, strategy['id']) or {}
                        if latest == schedule:
                            latest['next_analysis_at'] = stamp()
                            put(connection, schedules, strategy['id'], latest)
            except ApiError:
                continue

    async def tick(self):
        if not self.settings.automation_enabled:
            return None
        await self._wake_bar_watches()
        config = await run_in_threadpool(self._configs)
        job = await run_in_threadpool(self._claim, config, self.quote_snapshot())
        if not job:
            return None
        try:
            if job.get('decision_schema') == 'assessment_v1' and job.get('decision_mode') != 'RULE':
                if self.feed is None:
                    raise ApiError('ANALYSIS_FEED_REQUIRED', '分析行情服务不可用。', 409)
                from .analysis_snapshot import build_analysis_context
                context = await build_analysis_context(self.store, self.feed, job['symbol'],
                    self.settings.quote_max_age_seconds, task_profile={**(job.get('task_profile') or {}), 'strategy_revision': job['strategy_revision']})
                snapshot = context['analysis_snapshot']
                from .analysis_snapshot import _digest
                with self.store.transaction() as connection:
                    current_strategy = read(connection, strategies, job['strategy_id'])
                equity = snapshot['portfolio']['account'].get('equity')
                if equity is not None and current_strategy:
                    snapshot['constraints']['maximum_target_notional'] = str(min(Decimal(current_strategy['budget']), Decimal(equity)*Decimal(current_strategy['target_pct'])/100))
                    snapshot['snapshot_hash'] = _digest(snapshot)
                    snapshot['snapshot_id'] = 'snapshot:' + snapshot['snapshot_hash'][:32]
                quote = context.get('quote', {})
                closed = [v.get('closed_until') for v in snapshot.get('market', {}).get('features', {}).values() if v.get('closed_until')]
                observation = {'price': quote.get('price'), 'closed_until': max(closed) if closed else None, 'closed_by_interval':{k:v['closed_until'] for k,v in snapshot['market']['features'].items()}}
                with self.store.transaction() as connection:
                    saved = read(connection, jobs, job['id'])
                    saved.update(snapshot_id=snapshot['snapshot_id'], snapshot_hash=snapshot['snapshot_hash'], watch_observation=observation)
                    put(connection, jobs, job['id'], saved)
                job.update(snapshot_id=snapshot['snapshot_id'], snapshot_hash=snapshot['snapshot_hash'])
                if snapshot['quality']['status'] != 'READY':
                    decision = dict(id=job['model_key'], symbol=job['symbol'], created_at=stamp(),
                        schema_revision='assessment_v1', assessment_status='DATA_BLOCKED', proposal=None,
                        reason_code='DATA_BLOCKED', missing_required=snapshot['quality']['missing_required'])
                    await run_in_threadpool(self._finish, job, {'decision': decision})
                    return job['id']
            else:
                context = await run_in_threadpool(self.store.analysis_context, job["symbol"], self.quote_snapshot(), self.settings.quote_max_age_seconds)
            if not await run_in_threadpool(self._before_dispatch, job):
                await run_in_threadpool(self._finish, job)
                return job["id"]
            mode, identifiers, revisions = self._job_binding(job)
            if job.get('decision_mode') == 'RULE':
                price = self.store._price(self.quote_snapshot(), job['symbol'], self.settings.quote_max_age_seconds)
                action = 'BUY' if self._rule_matches(job['rule'], price) else 'HOLD'
                result = {'decision': dict(id='rule:' + job['id'], symbol=job['symbol'], action=action,
                    summary=f"价格 {price} {job['rule']['operator']} {job['rule']['price']}；服务端规则计算。",
                    risk_status='NOT_EVALUATED', created_at=stamp(), data_as_of=context['quote']['as_of'])}
            elif hasattr(self.models, "analyze"):
                options = {'decision_policy': job.get('decision_policy', 'unanimous')} if job.get('decision_schema') == 'assessment_v1' else {}
                result = await self.models.analyze(job["symbol"], mode, identifiers, revisions, job["model_key"], context, **options)
            elif mode == "single" and identifiers == [repository.DEFAULT_PROFILE_ID]:
                result = await self.models.generate(job["symbol"], revisions[repository.DEFAULT_PROFILE_ID], job["model_key"], context)
            else:
                raise ApiError("MODEL_ANALYSIS_UNAVAILABLE", "所选模型分析不可用；未调用替代模型。", 409)
        except ApiError as exc:
            await run_in_threadpool(self._finish, job, None, exc.message)
        except Exception:
            # Never expose provider or credential-bearing exception text.
            await run_in_threadpool(self._finish, job, None, "模型分析失败；未自动下单，也未重试该调用。")
        else:
            await run_in_threadpool(self._finish, job, result)
        return job["id"]

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.warning("AI automation cycle unavailable; durable claims will be recovered")
            await asyncio.sleep(2)

    def notifications(self):
        with self.store.transaction() as connection:
            items = rows(connection, notifications)
        return {"unread_count": sum(item["read_at"] is None for item in items),
                "items": sorted(items, key=lambda i: i["created_at"], reverse=True)[:50]}

    def mark_read(self, payload):
        if not isinstance(payload, dict) or (set(payload) != {"all"} and set(payload) != {"ids"}):
            raise ApiError("INVALID_NOTIFICATIONS", "请提供通知 ID 或 all:true。")
        all_items = payload.get("all") is True
        ids = payload.get("ids", [])
        if ("all" in payload and not all_items) or not isinstance(ids, list) or len(ids) > 200 or any(type(i) is not str or len(i) > 64 for i in ids):
            raise ApiError("INVALID_NOTIFICATIONS", "通知 ID 无效。")
        with self.store.transaction() as connection:
            for item in rows(connection, notifications):
                if item["read_at"] is None and (all_items or item["id"] in ids):
                    item["read_at"] = stamp()
                    put(connection, notifications, item["id"], item)
        return self.notifications()

    def enrich_dashboard(self, result):
        available = self._profile_map(result["model_profiles"] if "model_profiles" in result else self._configs())
        with self.store.transaction() as connection:
            schedules_by_id = {r.id: r.data for r in connection.execute(select(schedules))}
            all_jobs = rows(connection, jobs)
            running = sum(j["status"] == "RUNNING" for j in all_jobs)
        active = armed = 0
        for strategy in result["strategies"]:
            schedule = schedules_by_id.get(strategy["id"], {})
            strategy.setdefault("analysis_interval_seconds", 300)
            strategy.setdefault("auto_execute", False)
            mode, identifiers = self._binding(strategy)
            strategy.setdefault("analysis_mode", mode)
            strategy.setdefault("model_profile_ids", identifiers)
            is_active = self._active(strategy)
            active += int(is_active)
            armed += int(is_active and strategy["auto_execute"])
            runtime = strategy["status"]
            if is_active:
                runtime = schedule.get("runtime_status", "READY")
                if strategy['mode'] != 'RULE' and (not identifiers or any(not self._ready(available.get(identifier)) for identifier in identifiers)):
                    runtime = "WAITING_MODEL"
                elif runtime != "RUNNING":
                    try:
                        self.store._price(result["market"], strategy["symbol"], self.settings.quote_max_age_seconds)
                    except ApiError:
                        runtime = "WAITING_QUOTES"
            strategy.update(runtime_status=runtime, next_analysis_at=schedule.get("next_analysis_at") if is_active else None,
                            last_analysis_at=schedule.get("last_analysis_at"), last_analysis_error=schedule.get("last_analysis_error"))
            strategy.pop("_activation_authorized", None)
            strategy.pop("_activated_at", None)
        result["automation"] = dict(enabled=self.settings.automation_enabled, active_strategies=active, auto_order_strategies=armed, running_jobs=running)
        result["notifications"] = self.notifications()
        referenced = {n["decision_id"] for n in result["notifications"]["items"]}
        present = {d["id"] for d in result.get("decisions", [])}
        for job in all_jobs:
            decision = job.get("decision")
            if decision and decision["id"] in referenced and decision["id"] not in present:
                result.setdefault("decisions", []).append(decision)
                present.add(decision["id"])
        result["decisions"] = sorted(result.get("decisions", []), key=lambda d: d["created_at"], reverse=True)
        for service in result.get("services", []):
            if service["id"] == "ai":
                service["detail"] = "激活的 AI 策略周期分析；只有勾选自动下单的策略才可执行本地 PAPER 交易。"
        return result
