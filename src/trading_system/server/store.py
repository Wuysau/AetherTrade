"""Transactional local PAPER ledger, isolated from native execution authority.

Amounts are stored as JSON decimal strings, avoiding SQLite numeric coercion.
All writes lock the singleton account row (BEGIN IMMEDIATE on SQLite; FOR
UPDATE on PostgreSQL), including idempotency checks and first-owner setup.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import secrets
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import (JSON, Boolean, Column, Integer, MetaData, String, Table,
                        create_engine, delete, insert, select, update)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from trading_system.contracts.canonical import canonical_hash, utc_timestamp, domain_payload
from trading_system.contracts import types as t
from trading_system.risk.locks import POLICIES
from .errors import ApiError
from .security import csrf_token, password_hash, password_matches, token_hash
from .validation import SYMBOLS, amount, number

metadata = MetaData()
state = Table("console_state", metadata, Column("id", Integer, primary_key=True), Column("data", JSON, nullable=False))
owners = Table("console_owner", metadata, Column("id", Integer, primary_key=True),
               Column("username", String(64), nullable=False), Column("password_hash", String(256), nullable=False))
sessions = Table("console_sessions", metadata, Column("token_hash", String(64), primary_key=True),
                 Column("username", String(64), nullable=False), Column("expires_at", String(40), nullable=False),
                 Column("revoked", Boolean, nullable=False, default=False))
attempts = Table("console_login_attempts", metadata, Column("id", String(64), primary_key=True),
                 Column("count", Integer, nullable=False), Column("window", String(40), nullable=False))
strategies = Table("console_strategies", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
positions = Table("console_positions", metadata, Column("symbol", String(24), primary_key=True), Column("data", JSON, nullable=False))
orders = Table("console_orders", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
events = Table("console_events", metadata, Column("id", String(64), primary_key=True), Column("data", JSON, nullable=False))
observations = Table("console_observations", metadata, Column("id", String(64), primary_key=True),
                     Column("at", String(40), nullable=False, index=True), Column("equity", String(96), nullable=False))
receipts = Table("console_receipts", metadata, Column("key", String(128), primary_key=True),
                 Column("fingerprint", String(64), nullable=False), Column("data", JSON, nullable=False))
cause_locks = Table("console_cause_locks", metadata, Column("id", String(96), primary_key=True),
                    Column("data", JSON, nullable=False))
FEE = Decimal("0.001")


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat()


def rows(connection, table):
    return list(connection.execute(select(table.c.data)).scalars())


class Store:
    def __init__(self, database_url: str):
        url = make_url(database_url)
        if url.get_backend_name() not in ("sqlite", "postgresql"):
            raise ValueError("Runtime database must be SQLite or PostgreSQL")
        options = {"pool_pre_ping": True, "hide_parameters": True}
        self.sqlite = url.get_backend_name() == "sqlite"
        if self.sqlite:
            if url.database and url.database != ":memory:":
                Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if not url.database or url.database == ":memory:":
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self._lock = threading.RLock()
        self.execution = None

    def initialize(self):
        # Register additive model tables even when Store is used without the app.
        from . import model_repository as repository
        metadata.create_all(self.engine)
        try:
            with self.engine.begin() as connection:
                if connection.execute(select(state.c.id)).first() is None:
                    connection.execute(insert(state).values(id=1, data={}))
        except IntegrityError:
            # Another process initialized the single lock row first.
            pass
        with self.transaction() as connection:
            repository.ensure_profiles(connection)
            for strategy in rows(connection, strategies):
                if "analysis_mode" in strategy and "model_profile_ids" in strategy:
                    continue
                strategy.setdefault("analysis_mode", "single")
                strategy.setdefault("model_profile_ids", [repository.DEFAULT_PROFILE_ID] if strategy.get("mode") == "AI" else [])
                # Binding migration confers no activation/order authority and does
                # not alter a revision that an existing client or job captured.
                connection.execute(update(strategies).where(strategies.c.id == strategy["id"]).values(data=strategy))

    def close(self):
        self.engine.dispose()

    @contextmanager
    def transaction(self):
        with self._lock, (self.execution.control_guard() if self.execution else nullcontext()), self.engine.connect() as connection, localcontext() as arithmetic:
            arithmetic.prec = 80
            if self.sqlite:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                connection.execute(select(state.c.id).where(state.c.id == 1).with_for_update()).first()
                yield connection
                connection.commit()
            except BaseException as exc:
                connection.rollback()
                # Persist only the observed cause after rolling back a rejected
                # order/job. Never commit any of that failed operation's writes.
                if isinstance(exc, ApiError) and getattr(exc, 'daily_loss_lock', None):
                    if self.sqlite:
                        connection.exec_driver_sql('BEGIN IMMEDIATE')
                    else:
                        connection.begin()
                    try:
                        connection.execute(select(state.c.id).where(state.c.id == 1).with_for_update()).first()
                        self._save_daily_lock(connection, exc.daily_loss_lock)
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
                raise

    def needs_setup(self) -> bool:
        with self._lock, self.engine.connect() as connection:
            return connection.execute(select(owners.c.id)).first() is None

    def healthy(self):
        with self._lock, self.engine.connect() as connection:
            connection.execute(select(state.c.id)).first()
        return "CONNECTED"

    def _state(self, connection):
        result = connection.execute(select(state.c.data).where(state.c.id == 1)).scalar_one()
        if not result:
            raise ApiError("SETUP_REQUIRED", "请先创建本地账户。", 409)
        return result

    def _save(self, connection, data):
        connection.execute(update(state).where(state.c.id == 1).values(data=data))

    def _event(self, connection, kind, title, detail):
        event = {"id": str(uuid4()), "kind": kind, "title": title, "detail": detail, "created_at": stamp()}
        connection.execute(insert(events).values(id=event["id"], data=event))
        return event

    def _session(self, connection, username, hours):
        token = secrets.token_urlsafe(48)
        expires = (now() + timedelta(hours=hours)).isoformat()
        connection.execute(delete(sessions).where(sessions.c.expires_at < stamp()))
        connection.execute(insert(sessions).values(token_hash=token_hash(token), username=username, expires_at=expires, revoked=False))
        return {"token": token, "username": username, "csrf_token": csrf_token(token), "environment": "PAPER", "expires_at": expires}

    def setup(self, username, password, initial_cash, session_hours):
        cash = amount(initial_cash)
        if not 2 <= len(username) <= 64 or username != username.strip() or not 10 <= len(password) <= 256:
            raise ApiError("INVALID_CREDENTIALS", "用户名需 2–64 字符，密码需 10–256 字符。")
        hashed = password_hash(password)
        with self.transaction() as connection:
            if connection.execute(select(owners.c.id)).first():
                raise ApiError("ALREADY_CONFIGURED", "本地账户已创建，请登录。", 409)
            connection.execute(insert(owners).values(id=1, username=username, password_hash=hashed))
            account = {"id": "paper-local", "name": "本地 PAPER 账户", "currency": "USDT", "initial_cash": number(cash),
                       "cash": number(cash), "revision": 1, "paused": False}
            self._save(connection, {"account": account, "risk": {"max_position_pct": "25", "max_daily_loss": "2000", "revision": 1}})
            self._observe(connection, cash)
            self._event(connection, "SETUP", "PAPER 账户已创建", "仅使用本地虚拟资金；未连接真实交易账户。")
            return self._session(connection, username, session_hours)

    def login(self, username, password, client_key, session_hours):
        # Persist IP and global windows: neither username guessing nor restart
        # resets the limit. Client key comes from the socket, never forwarded headers.
        buckets = [(token_hash("ip:" + client_key), 10), (token_hash("global"), 50)]
        denied = False
        result = None
        with self.transaction() as connection:
            window_start = (now() - timedelta(minutes=5)).isoformat()
            connection.execute(delete(attempts).where(attempts.c.window < window_start))
            active = {}
            for key, limit in buckets:
                record = connection.execute(select(attempts).where(attempts.c.id == key)).mappings().first()
                active[key] = record
                if record and record["count"] >= limit:
                    raise ApiError("LOGIN_RATE_LIMIT", "登录失败次数过多，请 5 分钟后重试。", 429)
            owner = connection.execute(select(owners)).mappings().first()
            if owner:
                matches = password_matches(password, owner["password_hash"])
                valid = matches and secrets.compare_digest(username.encode(), owner["username"].encode())
            else:
                password_hash(password)  # Similar work for an unconfigured/unknown user.
                valid = False
            if valid:
                connection.execute(delete(attempts).where(attempts.c.id == buckets[0][0]))
                result = self._session(connection, owner["username"], session_hours)
            else:
                denied = True
                for key, _ in buckets:
                    record = active[key]
                    if record:
                        connection.execute(update(attempts).where(attempts.c.id == key).values(count=record["count"] + 1))
                    else:
                        connection.execute(insert(attempts).values(id=key, count=1, window=stamp()))
        if denied:
            raise ApiError("INVALID_LOGIN", "用户名或密码不正确。", 401)
        return result

    def session(self, token):
        if not token or len(token) > 128:
            raise ApiError("UNAUTHENTICATED", "请登录本地账户。", 401)
        with self._lock, self.engine.connect() as connection:
            result = connection.execute(select(sessions).where(sessions.c.token_hash == token_hash(token))).mappings().first()
        if not result or result["revoked"] or utc_timestamp(result["expires_at"]) <= now():
            raise ApiError("SESSION_EXPIRED", "会话已过期，请重新登录。", 401)
        return {"username": result["username"], "csrf_token": csrf_token(token), "environment": "PAPER", "expires_at": result["expires_at"]}

    def logout(self, token):
        with self.transaction() as connection:
            connection.execute(update(sessions).where(sessions.c.token_hash == token_hash(token)).values(revoked=True))

    @staticmethod
    def _revision(current, expected):
        if type(expected) is not int or current != expected:
            raise ApiError("REVISION_CONFLICT", "数据已更新，请刷新后重试。", 409)

    def _strategy_values(self, payload, *, connection, previous_status="DRAFT", previous_routing=None, previous_decision=None):
        from . import model_repository as repository
        if not isinstance(payload.get("name"), str) or not 1 <= len(payload["name"].strip()) <= 80:
            raise ApiError("INVALID_STRATEGY", "策略名称不能为空且最多 80 字符。")
        if payload.get("symbol") not in SYMBOLS or payload.get("mode") not in ("AI", "RULE", "HYBRID"):
            raise ApiError("INVALID_STRATEGY", "策略品种或模式不受支持。")
        payload["name"] = payload["name"].strip()
        payload["budget"] = number(amount(payload.get("budget")))
        payload["target_pct"] = number(amount(payload.get("target_pct"), maximum="100"))
        payload.setdefault("status", "DRAFT")
        payload.setdefault("analysis_interval_seconds", 300)
        payload.setdefault("auto_execute", False)
        status = payload["status"]
        if status not in ("DRAFT", "ACTIVE", "PAUSED"):
            raise ApiError("INVALID_STRATEGY", "策略状态无效。")
        interval = payload["analysis_interval_seconds"]
        if type(interval) is not int or not 60 <= interval <= 86400 or type(payload["auto_execute"]) is not bool:
            raise ApiError("INVALID_STRATEGY", "分析间隔需为 60–86400 秒，自动下单需为布尔值。")
        if payload["mode"] in ("RULE", "HYBRID"):
            rule = payload.get("rule")
            if rule is not None:
                if (not isinstance(rule, dict) or set(rule) != {"kind", "operator", "price", "stop_loss_pct"}
                        or rule.get("kind") != "PRICE_THRESHOLD" or rule.get("operator") not in ("LTE", "GTE")):
                    raise ApiError("INVALID_RULE", "规则需指定 PRICE_THRESHOLD、LTE/GTE、价格和止损比例。")
                amount(rule["price"], scale=18)
                stop = amount(rule["stop_loss_pct"], maximum="100")
                if stop >= 100:
                    raise ApiError("INVALID_RULE", "止损比例必须大于 0 且小于 100。")
            elif status == "ACTIVE":
                raise ApiError("INVALID_RULE", "激活规则或混合策略前请配置价格规则。")
        automation_ack = payload.pop("acknowledge_paper_automation", False)
        if type(automation_ack) is not bool:
            raise ApiError("INVALID_STRATEGY", "自动 PAPER 授权需为布尔值。")
        if payload["mode"] in ("RULE", "HYBRID") and status == "ACTIVE" and previous_status != "ACTIVE" and not automation_ack:
            raise ApiError("PAPER_AUTOMATION_ACK_REQUIRED", "激活前请明确授权周期性 PAPER 自动执行。", 409)
        payload.setdefault("analysis_mode", "single")
        payload.setdefault("model_profile_ids", [repository.DEFAULT_PROFILE_ID] if payload["mode"] == "AI" else [])
        payload.setdefault('decision_schema', 'legacy')
        payload.setdefault('decision_policy', 'unanimous')
        schema, policy = payload['decision_schema'], payload['decision_policy']
        profile = payload.get('task_profile')
        if schema == 'assessment_v1':
            if profile is None:
                raise ApiError('TASK_PROFILE_REQUIRED', '请选择研究周期与必需数据。')
            from .analysis_snapshot import validate_task_profile
            validate_task_profile(dict(profile, strategy_revision=payload.get('revision', 1)))

        if (schema not in ('legacy', 'assessment_v1') or policy not in ('unanimous', 'evidence_review')
                or policy == 'evidence_review' and (schema != 'assessment_v1' or payload['analysis_mode'] != 'consensus')):
            raise ApiError('INVALID_DECISION_POLICY', 'Evidence review requires assessment_v1 and consensus.')
        analysis_mode, identifiers = payload["analysis_mode"], payload["model_profile_ids"]
        if (analysis_mode not in ("single", "consensus") or not isinstance(identifiers, list)
                or len(identifiers) > 3 or any(type(identifier) is not str or not 1 <= len(identifier) <= 64 for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ApiError("INVALID_MODEL_BINDING", "请选择有效且不重复的模型配置。")
        if payload["mode"] == "RULE":
            if identifiers:
                raise ApiError("INVALID_MODEL_BINDING", "RULE 策略不使用模型配置。")
        elif identifiers or status == "ACTIVE":
            if (analysis_mode == "single" and len(identifiers) != 1) or (analysis_mode == "consensus" and not 2 <= len(identifiers) <= 3):
                raise ApiError("INVALID_MODEL_BINDING", "单模型分析需选择 1 个模型，交叉分析需选择 2–3 个模型。")
        if any(repository.read_profile(connection, identifier) is None for identifier in identifiers):
            raise ApiError("MODEL_PROFILE_NOT_FOUND", "所选模型配置不存在，请重新选择。", 409)
        acknowledgement = payload.pop("acknowledge_model_cost", False)
        if type(acknowledgement) is not bool:
            raise ApiError("INVALID_STRATEGY", "模型费用确认需为布尔值。")
        if payload["mode"] != "RULE" and previous_status == "ACTIVE" and (previous_routing != (analysis_mode, identifiers) or previous_decision != (schema, policy, profile)) and acknowledgement is not True:
            raise ApiError("MODEL_COST_ACK_REQUIRED", "更改已激活策略的模型选择前，请确认周期性模型调用费用。", 409)
        if status == "ACTIVE" and previous_status != "ACTIVE":
            if payload["mode"] != "RULE" and acknowledgement is not True:
                raise ApiError("MODEL_COST_ACK_REQUIRED", "激活策略前请确认周期性模型调用费用。", 409)
            payload["_activation_authorized"] = True
            payload["_activated_at"] = stamp()
        if payload["auto_execute"] and status == "DRAFT":
            raise ApiError("INVALID_STRATEGY", "自动下单需要明确激活 AI 策略。")
        return payload

    def create_strategy(self, payload):
        with self.transaction() as connection:
            self._state(connection)
            data = self._strategy_values(dict(payload), connection=connection)
            data.update(id=str(uuid4()), revision=1, created_at=stamp(), updated_at=stamp())
            connection.execute(insert(strategies).values(id=data["id"], data=data))
            self._event(connection, "STRATEGY_SAVED", "策略已保存", f"{data['name']}：{data['status']}，自动 PAPER 下单：{data['auto_execute']}。")
        return {key: value for key, value in data.items() if not key.startswith("_")}

    def update_strategy(self, identifier, payload):
        with self.transaction() as connection:
            data = connection.execute(select(strategies.c.data).where(strategies.c.id == identifier)).scalar_one_or_none()
            if data is None:
                raise ApiError("STRATEGY_NOT_FOUND", "策略不存在。", 404)
            self._revision(data["revision"], payload.get("expected_revision"))
            previous_decision = (data.get('decision_schema', 'legacy'), data.get('decision_policy', 'unanimous'), data.get('task_profile'))
            previous_symbol = data["symbol"]
            previous_status = data["status"]
            previous_auto = data.get("auto_execute", False)
            previous_routing = (data.get("analysis_mode", "single"), list(data.get("model_profile_ids", ["default"] if data["mode"] == "AI" else [])))
            data.update({key: value for key, value in payload.items() if key != "expected_revision"})
            if payload.get("auto_execute") is True and not previous_auto and previous_status != "ACTIVE" and payload.get("status") != "ACTIVE":
                raise ApiError("INVALID_STRATEGY", "启用自动下单时请明确激活策略并确认模型费用。")
            self._strategy_values(data, connection=connection, previous_status=previous_status, previous_routing=previous_routing, previous_decision=previous_decision)
            owned_positions = self.execution.account_view()['positions'] if self.execution else rows(connection, positions)
            if data["symbol"] != previous_symbol and any(
                item.get("strategy_id") == identifier and Decimal(item["quantity"]) != 0
                for item in owned_positions
            ):
                raise ApiError("STRATEGY_HAS_POSITION", "请先退出该策略持仓，再修改交易标的。", 409)
            data.update(revision=data["revision"] + 1, updated_at=stamp())
            connection.execute(update(strategies).where(strategies.c.id == identifier).values(data=data))
            self._event(connection, "STRATEGY_SAVED", "策略已更新", f"{data['name']}：{data['status']}，自动 PAPER 下单：{data['auto_execute']}。")
            return {key: value for key, value in data.items() if not key.startswith("_")}

    def update_risk(self, payload):
        maximum = amount(payload["max_position_pct"], maximum="100")
        daily = amount(payload["max_daily_loss"])
        with self.transaction() as connection:
            data = self._state(connection)
            self._revision(data["risk"]["revision"], payload["expected_revision"])
            data["risk"] = {"max_position_pct": number(maximum), "max_daily_loss": number(daily), "revision": data["risk"]["revision"] + 1}
            self._save(connection, data)
            self._event(connection, "RISK_SAVED", "PAPER 风控已更新", "限制适用于此本地 PAPER 账户后续买入。")
            return data["risk"]

    @staticmethod
    def _price(market, symbol, maximum_age, *, fresh=True):
        try:
            quotes = market.get("quotes")
            if not isinstance(quotes, list):
                raise ValueError()
            matching = [item for item in quotes if isinstance(item, dict) and item.get("symbol") == symbol]
            if len(matching) != 1:
                raise ValueError()
            quote = matching[0]
            price = amount(quote.get("price"), scale=18)
            if fresh:
                age = (now() - utc_timestamp(quote.get("as_of"))).total_seconds()
                if quote.get("stale") is not False or market.get("status") not in ("CONNECTED", "PARTIAL") or not 0 <= age <= maximum_age:
                    raise ValueError()
            return price
        except (ApiError, ValueError, TypeError, AttributeError):
            raise ApiError("QUOTE_UNAVAILABLE", f"{symbol} 缺少有效的新鲜行情；未执行交易。", 409) from None

    def _valuation(self, connection, account, market, *, fresh=False, maximum_age=90):
        if self.execution and not rows(connection, positions) and not rows(connection, orders):
            projection = self.execution.account_view()
            if Decimal(account['initial_cash']) != Decimal(projection['initial_cash']):
                raise ApiError('PAPER_CAPITAL_MISMATCH', '控制台初始虚拟资金与独立业务账本不一致；请使用显式创建的新 PAPER 账户。', 409)
            account['cash'] = projection['cash']
            total = Decimal(account['cash'])
            held = []
            for item in projection['positions']:
                try:
                    price = self._price(market, item['symbol'], maximum_age, fresh=fresh)
                except ApiError:
                    if fresh:
                        raise
                    price = Decimal(item['mark_price'])
                value = Decimal(item['quantity']) * price
                total += value
                held.append({**item, 'market_value': number(value), 'mark_price': number(price),
                    'unrealized_pnl': number(value - Decimal(item['quantity']) * Decimal(item['average_price']))})
            return total, held
        total = Decimal(account["cash"])
        result = []
        for item in rows(connection, positions):
            try:
                price = self._price(market, item["symbol"], maximum_age, fresh=fresh)
            except ApiError:
                if fresh:
                    raise
                price = Decimal(item["mark_price"])
            quantity = Decimal(item["quantity"])
            value = quantity * price
            total += value
            item.update(mark_price=number(price), market_value=number(value),
                        unrealized_pnl=number(value - quantity * Decimal(item["average_price"])))
            result.append(item)
        return total, result

    def _observe(self, connection, equity):
        connection.execute(insert(observations).values(id=str(uuid4()), at=stamp(), equity=number(equity)))

    def _day_start(self, connection, initial):
        zone = self._state(connection)['risk'].get('risk_day_timezone', 'Asia/Shanghai')
        midnight = now().astimezone(ZoneInfo(zone)).replace(hour=0, minute=0, second=0,
            microsecond=0).astimezone(timezone.utc).isoformat()
        before = connection.execute(select(observations.c.equity).where(observations.c.at <= midnight).order_by(observations.c.at.desc()).limit(1)).scalar_one_or_none()
        if before is None:
            before = connection.execute(select(observations.c.equity).order_by(observations.c.at).limit(1)).scalar_one_or_none()
        return Decimal(before or initial)

    def _save_daily_lock(self, connection, record):
        if connection.execute(select(cause_locks.c.id).where(cause_locks.c.id == record['lock_id'])).first() is None:
            connection.execute(insert(cause_locks).values(id=record['lock_id'], data=record))
            self._event(connection, 'RISK_LOCK', '日亏损风险锁已持久化',
                '价格回升、暂停恢复或风险日切换不会解除；需要独立 DAILY_LOSS_REVIEW 授权证据。')
        if self.execution:
            self.execution.latch_daily_lock(record)

    def _daily_loss(self, connection, data, equity, *, prospective_fee=Decimal(0), deny=False):
        active = [item for item in rows(connection, cause_locks) if item['state'] == 'ACTIVE']
        loss = self._day_start(connection, data['account']['initial_cash']) - equity
        breached = loss + prospective_fee >= Decimal(data['risk']['max_daily_loss'])
        record = None
        if breached and not active:
            zone = data['risk'].get('risk_day_timezone', 'Asia/Shanghai')
            day = now().astimezone(ZoneInfo(zone)).date().isoformat()
            permission, required = POLICIES['DAILY_LOSS']
            record = domain_payload(t.RiskLock(lock_id='console-daily:' + day,
                scope=t.ScopeKey(environment=t.Environment.PAPER, venue_account_id=data['account']['id'],
                    product=t.Product.SPOT, instrument_uid='console-account', venue_leg='BOTH'),
                cause='DAILY_LOSS', state='ACTIVE', permission=permission,
                required_evidence=required, raised_at=stamp()))
            record.update(risk_day=day, risk_day_timezone=zone, observed_equity=number(equity),
                baseline=number(self._day_start(connection, data['account']['initial_cash'])),
                prospective_fee=number(prospective_fee), policy_revision=data['risk']['revision'])
        if deny and (active or breached):
            exc = ApiError('DAILY_LOSS_LIMIT', 'PAPER 日亏损风险锁生效；价格回升不会解除。', 409)
            exc.daily_loss_lock = record
            raise exc
        if record:
            self._save_daily_lock(connection, record)
        return active + ([record] if record else [])

    def _cached(self, connection, key, operation, payload):
        if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ApiError("IDEMPOTENCY_REQUIRED", "请提供有效的 Idempotency-Key 请求标识。")
        fingerprint = canonical_hash({"operation": operation, "payload": payload})
        previous = connection.execute(select(receipts).where(receipts.c.key == key)).mappings().first()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ApiError("IDEMPOTENCY_CONFLICT", "同一请求标识已用于不同内容。", 409)
            return previous["data"], fingerprint
        return None, fingerprint

    def _fill(self, connection, account, payload, price):
        symbol, side = payload["symbol"], payload["side"]
        quantity = amount(payload["quantity"])
        held = connection.execute(select(positions.c.data).where(positions.c.symbol == symbol)).scalar_one_or_none()
        old_quantity = Decimal(held["quantity"]) if held else Decimal(0)
        notional, fee = quantity * price, quantity * price * FEE
        if side == "BUY":
            if Decimal(account["cash"]) < notional + fee:
                raise ApiError("INSUFFICIENT_CASH", "PAPER 可用资金不足（含模拟手续费）。", 409)
            account["cash"] = number(Decimal(account["cash"]) - notional - fee)
            new_quantity = old_quantity + quantity
            average = ((Decimal(held["average_price"]) * old_quantity if held else 0) + notional) / new_quantity
            position = {"id": held["id"] if held else str(uuid4()), "symbol": symbol, "product": "SPOT", "side": "LONG",
                        "quantity": number(new_quantity), "average_price": number(average), "mark_price": number(price),
                        "_entry_fees": number(Decimal(held.get("_entry_fees", "0")) + fee if held else fee),
                        "strategy_id": payload.get("strategy_id") if not held or held["strategy_id"] == payload.get("strategy_id") else None,
                        "owner": "LOCAL_PAPER", "protection": "UNCONFIGURED", "updated_at": stamp()}
            if held:
                connection.execute(update(positions).where(positions.c.symbol == symbol).values(data=position))
            else:
                connection.execute(insert(positions).values(symbol=symbol, data=position))
            realized = None
        else:
            if old_quantity < quantity:
                raise ApiError("INSUFFICIENT_POSITION", "PAPER 持仓数量不足；不允许卖空。", 409)
            account["cash"] = number(Decimal(account["cash"]) + notional - fee)
            entry_fees = Decimal(held.get("_entry_fees", "0"))
            allocated_entry_fee = entry_fees * quantity / old_quantity
            realized = number((price - Decimal(held["average_price"])) * quantity - fee - allocated_entry_fee)
            if old_quantity == quantity:
                connection.execute(delete(positions).where(positions.c.symbol == symbol))
            else:
                held.update(quantity=number(old_quantity - quantity), mark_price=number(price),
                            _entry_fees=number(entry_fees - allocated_entry_fee), updated_at=stamp())
                connection.execute(update(positions).where(positions.c.symbol == symbol).values(data=held))
        order = {"id": str(uuid4()), "symbol": symbol, "side": side, "quantity": number(quantity), "price": number(price),
                 "notional": number(notional), "fee": number(fee), "status": "FILLED", "strategy_id": payload.get("strategy_id"), "created_at": stamp()}
        connection.execute(insert(orders).values(id=order["id"], data={**order, "realized_pnl": realized}))
        return order

    def order(self, payload, key, market, maximum_age):
        with self.transaction() as connection:
            return self._order(connection, payload, key, market, maximum_age)

    def _order(self, connection, payload, key, market, maximum_age):
        """Only the unified durable service can create an execution command."""
        cached, fingerprint = self._cached(connection, key, "order", payload)
        if cached:
            return cached
        data = self._state(connection)
        account = data["account"]
        symbol, side = payload.get("symbol"), payload.get("side")
        if symbol not in SYMBOLS or side not in ("BUY", "SELL"):
            raise ApiError("INVALID_ORDER", "PAPER 订单品种或方向无效。")
        quantity = amount(payload.get("quantity"))
        strategy_id = payload.get("strategy_id")
        if strategy_id:
            strategy = connection.execute(select(strategies.c.data).where(strategies.c.id == strategy_id)).scalar_one_or_none()
            if not strategy or strategy["symbol"] != symbol:
                raise ApiError("STRATEGY_MISMATCH", "订单必须关联同一品种的现有策略。")
        price = self._price(market, symbol, maximum_age)
        if side == "BUY":
            if account["paused"]:
                raise ApiError("ENTRIES_PAUSED", "PAPER 新开仓已暂停；仍可卖出已有持仓。", 409)
            equity, held = self._valuation(connection, account, market, fresh=True, maximum_age=maximum_age)
            self._daily_loss(connection, data, equity, prospective_fee=quantity * price * FEE, deny=True)
            previous = next((Decimal(item["market_value"]) for item in held if item["symbol"] == symbol), Decimal(0))
            post_equity = equity - quantity * price * FEE
            if post_equity <= 0 or (previous + quantity * price) * 100 > post_equity * Decimal(data["risk"]["max_position_pct"]):
                raise ApiError("POSITION_LIMIT", "该订单将超过 PAPER 单品种仓位上限。", 409)
            if strategy_id:
                if strategy["status"] == "PAUSED":
                    raise ApiError("STRATEGY_PAUSED", "所选策略已暂停。", 409)
                if previous + quantity * price > Decimal(strategy["budget"]):
                    raise ApiError("STRATEGY_BUDGET", "订单将超过策略预算。", 409)
        if self.execution is None or self.sqlite:
            raise ApiError("UNIFIED_PAPER_REQUIRED", "统一 PAPER 尚未配置：需要独立 PostgreSQL 业务账本与受限模拟平台数据库；未创建订单。", 503)
        if rows(connection, orders) or rows(connection, positions):
            raise ApiError("LEGACY_LEDGER_READ_ONLY", "原本地模拟账本仅供只读；请使用独立的统一 PAPER 账户，禁止隐式迁移为执行证据。", 409)
        order = self.execution.accept(dict(payload), key, market, maximum_age, data, strategy if strategy_id else None)
        self._event(connection, "PAPER_COMMAND", "PAPER 请求已持久化", f"{side} {symbol}；回执不代表成交，需由执行者提交并查询事件。")
        result = {"order": order, "account_revision": account["revision"]}
        connection.execute(insert(receipts).values(key=key, fingerprint=fingerprint, data=result))
        return result

    def action(self, payload, key, market, maximum_age):
        with self.transaction() as connection:
            cached, fingerprint = self._cached(connection, key, "action", payload)
            if cached:
                return cached
            data = self._state(connection)
            account = data["account"]
            self._revision(account["revision"], payload.get("expected_revision"))
            action = payload.get("action")
            affected = 0
            if action in ("pause_new", "resume_new"):
                target = action == "pause_new"
                affected = int(account["paused"] != target)
                account["paused"] = target
                detail = "本地 PAPER 新买入已暂停；卖出仍可用。" if target else "本地 PAPER 手动买入已恢复；未启动自动交易。"
            elif action == "cancel_entries":
                if self.execution is None:
                    raise ApiError("UNIFIED_PAPER_REQUIRED", "统一 PAPER 执行尚未配置；未撤单。", 503)
                affected = self.execution.cancel_entries()
                detail = f"已持久化 {affected} 项撤单请求；状态由后续模拟平台事件确认。"
            elif action == "exit_owned":
                if payload.get("confirmation") != "EXIT PAPER":
                    raise ApiError("CONFIRMATION_REQUIRED", "退出前请明确输入 EXIT PAPER。", 409)
                if self.execution is None:
                    raise ApiError("UNIFIED_PAPER_REQUIRED", "统一 PAPER 执行尚未配置；旧账本为只读，未退出。", 503)
                affected = self.execution.exit_owned(key, market, maximum_age, data)
                detail = f"已持久化 {affected} 项退出请求；尚不代表实际成交。"
            else:
                raise ApiError("INVALID_ACTION", "不支持此 PAPER 操作。")
            if affected:
                account["revision"] += 1
                self._save(connection, data)
                if action == "exit_owned":
                    self._observe(connection, Decimal(account["cash"]))
            event = self._event(connection, "PAPER_ACTION", "PAPER 控制操作已处理", detail)
            result = {"id": event["id"], "action": action, "status": "APPLIED", "affected_count": affected, "detail": detail, "created_at": event["created_at"]}
            connection.execute(insert(receipts).values(key=key, fingerprint=fingerprint, data=result))
            return result

    def dashboard(self, market, period="24H"):
        days = {"24H": 1, "7D": 7, "30D": 30, "90D": 90}
        if period not in days:
            raise ApiError("INVALID_RANGE", "请选择 24H、7D、30D 或 90D。")
        with self.transaction() as connection:
            data = self._state(connection)
            account = data["account"]
            equity, held = self._valuation(connection, account, market)
            try:
                fresh_equity, _ = self._valuation(connection, account, market, fresh=True)
                self._daily_loss(connection, data, fresh_equity)
            except ApiError:
                pass
            initial, cash = Decimal(account["initial_cash"]), Decimal(account["cash"])
            baseline = self._day_start(connection, account["initial_cash"])
            pnl = equity - baseline
            account.update(equity=number(equity), day_pnl=number(pnl), day_pnl_pct=number(pnl / baseline * 100 if baseline else Decimal(0)),
                           exposure_pct=number((equity - cash) / equity * 100 if equity else Decimal(0)),
                           risk_used=number(max(Decimal(0), -pnl)), risk_limit=data["risk"]["max_daily_loss"])
            history = [{"at": item.at, "equity": item.equity} for item in connection.execute(select(observations.c.at, observations.c.equity).order_by(observations.c.at))]
            cutoff = (now() - timedelta(days=days[period])).isoformat()
            trades = sorted(rows(connection, orders), key=lambda item: item["created_at"], reverse=True)
            trades = [{**item, 'provenance': 'LEGACY_LOCAL_PAPER'} for item in trades]
            if self.execution and not trades:
                trades = sorted(self.execution.projection()['orders'], key=lambda item: item['created_at'], reverse=True)
            closed = [Decimal(item["realized_pnl"]) for item in trades if item.get("realized_pnl") is not None]
            peak, drawdown = initial, Decimal(0)
            for point in [*history, {"equity": number(equity)}]:
                value = Decimal(point["equity"])
                peak = max(peak, value)
                drawdown = max(drawdown, (peak - value) / peak * 100 if peak else Decimal(0))
            return {"as_of": stamp(), "environment": "PAPER", "account": account,
                    "history": [item for item in history if item["at"] >= cutoff],
                    "positions": [{key: value for key, value in item.items() if not key.startswith("_")} for item in held],
                    "orders": [{key: value for key, value in item.items() if key != "realized_pnl"} for item in trades],
                    "decisions": [], "strategies": sorted(rows(connection, strategies), key=lambda item: item["created_at"], reverse=True),
                    "events": sorted(rows(connection, events), key=lambda item: item["created_at"], reverse=True)[:100], "risk": data["risk"],
                    "risk_locks": rows(connection, cause_locks),
                    "execution": self.execution.projection() if self.execution else {
                        "mode": "UNAVAILABLE", "simulation_notice": "需要统一 PostgreSQL PAPER 配置。原本地成交记录为只读历史；模拟不能证明真实滑点、流动性或保护。",
                        "targets": [], "commands": [], "orders": [], "positions": [], "protection_plans": [],
                        "risk_locks": rows(connection, cause_locks)},
                    "services": [{"id": "database", "label": "本地账本", "status": "CONNECTED", "detail": "PostgreSQL" if not self.sqlite else "SQLite · 本地模式", "updated_at": stamp()},
                                 {"id": "paper", "label": "PAPER 执行", "status": "CONNECTED", "detail": "仅本地模拟成交；未接入交易所交易接口。", "updated_at": stamp()},
                                 {"id": "ai", "label": "AI 决策", "status": "NOT_CONFIGURED", "detail": "未配置模型；没有生成或自动执行决策。", "updated_at": None},
                                 {"id": "equities", "label": "美股 / ETF", "status": "NOT_CONFIGURED", "detail": "未配置美股或 ETF 行情与交易数据源。", "updated_at": None},
                                 {"id": "native", "label": "自动执行与保护", "status": "NOT_CONFIGURED", "detail": "未连接自动执行服务；持仓尚未配置自动止损或止盈。", "updated_at": None}],
                    "market": market, "research": {"total_return_pct": number((equity - initial) / initial * 100), "max_drawdown_pct": number(drawdown),
                    "win_rate": number(Decimal(sum(value > 0 for value in closed)) / len(closed) * 100) if closed else None,
                    "trade_count": sum(Decimal(item.get('filled_quantity', item['quantity'] if item['status'] == 'FILLED' else '0')) > 0 for item in trades), "fees": number(sum((Decimal(item["fee"]) for item in trades), Decimal(0)))}}

    def record_observation(self, market, maximum_age):
        """Observe only actual current prices; no backfilled or invented history."""
        with self.transaction() as connection:
            if connection.execute(select(owners.c.id)).first() is None:
                return
            data = self._state(connection)
            latest = connection.execute(select(observations.c.at).order_by(observations.c.at.desc()).limit(1)).scalar_one_or_none()
            if latest and (now() - utc_timestamp(latest)).total_seconds() < 60:
                return
            try:
                equity, held = self._valuation(connection, data["account"], market, fresh=True, maximum_age=maximum_age)
            except ApiError:
                return
            for item in held:
                connection.execute(update(positions).where(positions.c.symbol == item["symbol"]).values(data=item))
            self._observe(connection, equity)

            self._daily_loss(connection, data, equity)

    def analysis_context(self, symbol, market, maximum_age):
        """Minimal PAPER context; no identity, credential, connection or session data.

        Freshness is enforced by ModelService after durable idempotent replay.
        Do not include a potentially stale whole-portfolio equity estimate.
        """
        if symbol not in SYMBOLS:
            raise ApiError("INVALID_SYMBOL", "不支持该分析标的。")
        snapshot = self.dashboard(market, "24H")
        account = {key: snapshot['account'][key] for key in ('cash', 'equity', 'currency', 'paused')}
        for holding in snapshot['positions']:
            try:
                self._price(market, holding['symbol'], maximum_age)
            except ApiError:
                account.pop('equity', None)
                break
        matching = [quote for quote in market.get('quotes', []) if isinstance(quote, dict) and quote.get('symbol') == symbol]
        return {'symbol': symbol, 'quote': matching[0] if len(matching) == 1 else None,
                'position': next((position for position in snapshot['positions'] if position['symbol'] == symbol), None),
                'account': account, 'risk': snapshot['risk'],
                'market_status': market.get('status'), 'quote_max_age_seconds': maximum_age}

    def export(self, market):
        data = self.dashboard(market, "90D")
        account = data["account"]
        lines = ["AetherTrade — 本地 PAPER 实际活动报告", f"生成时间：{data['as_of']}",
                 "本报告来自持久化模拟交易账本，不是回测，不代表真实账户或投资收益。",
                 f"初始虚拟资金：{account['initial_cash']} USDT", f"现金：{account['cash']} USDT", f"观察权益：{account['equity']} USDT",
                 f"行情状态：{market.get('status', 'UNAVAILABLE')}；缺少行情时估值沿用最后实际观察价格。",
                 f"成交笔数：{data['research']['trade_count']}；模拟手续费：{data['research']['fees']} USDT", "", "实际 PAPER 成交："]
        fills = [item for item in data['orders'] if Decimal(item.get('filled_quantity', item['quantity'] if item['status'] == 'FILLED' else '0')) > 0]
        lines += [f"{item['created_at']} {item['symbol']} {item['side']} {item.get('filled_quantity', item['quantity'])} @ {item['price']} fee={item['fee']} id={item['id']} source={item.get('provenance', 'LEGACY_LOCAL_PAPER')}" for item in reversed(fills)]
        if not fills:
            lines.append("尚无成交。")
        lines += ["", "AI 分析与自动交易相互独立。研究指标仅基于已记录的 PAPER 活动和行情观察。"]
        return "\n".join(lines) + "\n"
