"""Approved wire shapes. Codec validation is structural, never authorization.

Decimal strings retain their exact spelling. Nested records are immutable.
Use validate_transition before accepting HOLD or UPDATE_EXIT against existing state.
"""
from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias
import json
from jsonschema import Draft202012Validator, FormatChecker
from .canonical import canonical_hash, canonical_json, decimal_string, utc_timestamp

class Absent(Enum):
    VALUE = "absent"

ABSENT = Absent.VALUE

@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionProposalTarget:
    side: Literal['LONG', 'SHORT', 'FLAT']
    sizing: Literal['QUOTE_NOTIONAL', 'BASE_QUANTITY']
    value: str

@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionProposalEntry:
    order_preference: Literal['MARKET', 'LIMIT']
    limit_price: str | None
    maximum_entry_slippage_bps: str
    entry_deadline: str
    allow_partial: bool

@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionProposalExitPlan:
    stop_price: str | None
    stop_trigger: Literal['LAST', 'MARK', 'BID_ASK']
    review_due_at: str
    maximum_holding_seconds: int
    thesis_invalidation: str

@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionProposalRationale:
    summary: str
    counter_evidence: tuple[str, ...]
    missing_information: tuple[str, ...]

@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionProposal:
    contract_version: Literal['2026-10-20']
    proposal_id: str
    instrument_ref: str
    action: Literal['SET_TARGET', 'UPDATE_EXIT', 'HOLD']
    target: DecisionProposalTarget
    requested_leverage: int | Absent = ABSENT
    entry: DecisionProposalEntry
    exit_plan: DecisionProposalExitPlan
    evidence_refs: tuple[str, ...]
    rationale: DecisionProposalRationale
    valid_until: str

@dataclass(frozen=True, slots=True, kw_only=True)
class TradeIntentExecution:
    order_preference: Literal['MARKET', 'LIMIT']
    limit_price: str | None
    maximum_entry_slippage_bps: str
    entry_deadline: str
    allow_partial: bool

@dataclass(frozen=True, slots=True, kw_only=True)
class TradeIntentMaxPlannedLoss:
    amount: str
    currency: str

@dataclass(frozen=True, slots=True, kw_only=True)
class TradeIntentExitPlan:
    protection_policy: Literal['NATIVE_REQUIRED', 'HOSTED_ALLOWED_CASH_ONLY']
    stop_trigger: Literal['LAST', 'MARK', 'BID_ASK']
    stop_price: str | None
    review_due_at: str
    maximum_holding_seconds: int

@dataclass(frozen=True, slots=True, kw_only=True)
class TradeIntent:
    contract_version: Literal['2026-10-20']
    intent_id: str
    environment: Literal['RESEARCH', 'PAPER', 'TESTNET', 'LIVE']
    owner_strategy_id: str
    strategy_revision: int
    decision_run_id: str
    authorization_id: str
    venue_account_id: str
    collateral_group_id: str
    venue: Literal['BINANCE']
    product: Literal['SPOT', 'USD_M_PERP', 'US_EQUITY']
    instrument_uid: str
    venue_leg: Literal['BOTH', 'LONG', 'SHORT']
    ownership_epoch: int
    risk_policy_revision: int
    basis_fingerprint: str
    snapshot_id: str
    as_of: str
    created_at: str
    valid_until: str
    evidence_refs: tuple[str, ...]
    action: Literal['SET_TARGET', 'UPDATE_EXIT', 'HOLD']
    target_side: Literal['LONG', 'SHORT', 'FLAT']
    target_quantity: str
    quantity_unit: Literal['BASE', 'CONTRACT', 'SHARE']
    execution: TradeIntentExecution
    max_planned_loss: TradeIntentMaxPlannedLoss
    requested_leverage: int
    exit_plan: TradeIntentExitPlan
    proposal_hash: str
    rationale_summary: str
    supersedes_intent_id: str | None

@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicyMaximumModelCostPerDay:
    amount: str
    currency: str

@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicy:
    contract_version: Literal['2026-10-20']
    policy_id: str
    revision: int
    environment: Literal['RESEARCH', 'PAPER', 'TESTNET', 'LIVE']
    collateral_group_id: str
    valuation_currency: str
    maximum_capital: str
    cash_buffer: str
    maximum_order_notional: str
    maximum_planned_loss_per_intent: str
    maximum_total_planned_loss: str
    maximum_gross_exposure: str
    maximum_net_exposure: str
    maximum_open_positions: int
    maximum_active_orders: int
    maximum_leverage: int
    daily_loss_fraction: str
    maximum_drawdown_fraction: str
    risk_day_timezone: str
    maximum_quote_age_ms: int
    maximum_entry_slippage_bps: str
    maximum_model_cost_per_day: RiskPolicyMaximumModelCostPerDay
    protection_policy: Literal['NATIVE_REQUIRED', 'HOSTED_ALLOWED_CASH_ONLY']
    daily_lock_release: Literal['REVIEW_REQUIRED', 'RECHECK_ON_NEW_RISK_DAY']
    drawdown_lock_release: Literal['REVIEW_REQUIRED']
    native_risk_bypass: Literal[False]
    live_enabled: bool

@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionCommandCanonicalPayload:
    side: Literal['BUY', 'SELL'] | Absent = ABSENT
    order_type: Literal['MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LIMIT', 'TAKE_PROFIT_MARKET', 'TAKE_PROFIT_LIMIT'] | Absent = ABSENT
    quantity: str | Absent = ABSENT
    quote_notional: str | Absent = ABSENT
    price: str | Absent = ABSENT
    trigger_price: str | Absent = ABSENT
    reduce_only: bool | Absent = ABSENT
    full_position_exit: bool | Absent = ABSENT
    position_side: Literal['BOTH', 'LONG', 'SHORT'] | Absent = ABSENT
    time_in_force: Literal['GTC', 'GTD', 'DAY', 'IOC', 'FOK'] | Absent = ABSENT
    tokenize: Literal[False] | Absent = ABSENT

@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionCommand:
    contract_version: Literal['2026-10-20']
    command_id: str
    attempt_id: str
    intent_id: str
    parent_command_id: str | None
    environment: Literal['RESEARCH', 'PAPER', 'TESTNET', 'LIVE']
    venue_account_id: str
    product: Literal['SPOT', 'USD_M_PERP', 'US_EQUITY']
    instrument_uid: str
    operation: Literal['SUBMIT', 'CANCEL', 'AMEND', 'SET_PROTECTION', 'CLOSE']
    logical_client_order_id: str
    venue_client_order_id: str
    venue_order_id: str | None
    target_order_id: str | None
    canonical_payload: ExecutionCommandCanonicalPayload
    payload_hash: str
    ownership_epoch: int
    execution_epoch: int
    policy_revision: int
    state: Literal['DURABLE_READY', 'MAY_HAVE_SENT', 'UNKNOWN', 'NOT_SENT', 'VENUE_REJECTED', 'VENUE_CONFIRMED', 'ABORTED_UNSENT']
    outcome_provenance: Literal['LOCAL_VALIDATION', 'VENUE_EVENT', 'VENUE_QUERY', 'RECONCILIATION_INFERRED', 'LOCAL_POLICY', 'NONE']
    durable_record_ref: str
    created_at: str

WireRecord: TypeAlias = DecisionProposal | TradeIntent | RiskPolicy | ExecutionCommand

_MODELS = {c.__name__: c for c in (DecisionProposalTarget, DecisionProposalEntry, DecisionProposalExitPlan, DecisionProposalRationale, DecisionProposal, TradeIntentExecution, TradeIntentMaxPlannedLoss, TradeIntentExitPlan, TradeIntent, RiskPolicyMaximumModelCostPerDay, RiskPolicy, ExecutionCommandCanonicalPayload, ExecutionCommand,)}

_KINDS = {"decision-proposal": DecisionProposal, "trade-intent": TradeIntent,
          "risk-policy": RiskPolicy, "execution-command": ExecutionCommand}

def _pack(value):
    if is_dataclass(value):
        return {f.name: _pack(getattr(value, f.name)) for f in fields(value)
                if getattr(value, f.name) is not ABSENT}
    if isinstance(value, tuple):
        return [_pack(item) for item in value]
    return value

def _unpack(schema, value, name):
    if value is None:
        return None
    if "anyOf" in schema:
        schema = next(s for s in schema["anyOf"] if s.get("type") != "null")
    if schema.get("type") == "object":
        return _MODELS[name](**{key: _unpack(schema["properties"][key], item,
            name + "".join(x.title() for x in key.split("_"))) for key, item in value.items()})
    if schema.get("type") == "array":
        return tuple(_unpack(schema["items"], item, name + "Item") for item in value)
    return value

def _times(schema, value):
    if value is None:
        return
    if 'anyOf' in schema:
        schema = next(s for s in schema['anyOf'] if s.get('type') != 'null')
    if schema.get('type') == 'string' and '\\.' in schema.get('pattern', ''):
        decimal_string(value)
    if schema.get("format") == "date-time":
        utc_timestamp(value)
    if schema.get("type") == "object":
        for key, item in value.items():
            _times(schema["properties"][key], item)
    if schema.get("type") == "array":
        for item in value:
            _times(schema["items"], item)

class ContractCodec:
    """Repository-local approved schema boundary; no account/network access.

    Success does not bind authority, check market rules, or issue permits.
    Temporal order uses the original timestamp fraction, without float conversion.
    """
    def __init__(self):
        root = Path(__file__).resolve().parents[3]
        directory = root / "contracts"
        self._schemas = {kind: json.loads((directory / (kind + ".schema.json")).read_text(encoding="utf-8")) for kind in _KINDS}

    def decode(self, kind: str, payload: dict) -> WireRecord:
        if kind not in _KINDS:
            raise ValueError("unknown wire kind")
        canonical_json(payload)
        schema = self._schemas[kind]
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
        _times(schema, payload)
        if kind == "trade-intent":
            if _time_key(payload["as_of"]) > _time_key(payload["created_at"]) or _time_key(payload["created_at"]) > _time_key(payload["valid_until"]):
                raise ValueError("snapshot/creation/expiry ordering invalid")
        return _unpack(schema, payload, _KINDS[kind].__name__)

    def encode(self, record: WireRecord) -> dict:
        kind = next((k for k, cls in _KINDS.items() if type(record) is cls), None)
        if kind is None:
            raise ValueError("unknown wire record")
        payload = _pack(record)
        self.decode(kind, payload)
        return payload

    def hash(self, record: WireRecord) -> str:
        return canonical_hash(self.encode(record))

def _time_key(value: str):
    # RFC3339 UTC is checked before this; Decimal retains nanosecond ordering.
    from decimal import Decimal
    base = value.removesuffix("Z").removesuffix("+00:00")
    seconds, _, fraction = base.partition(".")
    return seconds, Decimal("0." + (fraction or "0"))

def validate_transition(previous: DecisionProposal | TradeIntent,
                        candidate: DecisionProposal | TradeIntent) -> None:
    """Check read-only/update semantics against server-held prior responsibility.

    This comparison is necessary but not sufficient for authorization. HOLD
    produces no command. Its request expiry must not replace the existing expiry.
    """
    if type(previous) is not type(candidate):
        raise ValueError("record kinds differ")
    codec = ContractCodec()
    before, after = codec.encode(previous), codec.encode(candidate)
    identity = ("instrument_ref",) if isinstance(candidate, DecisionProposal) else (
        "environment", "venue_account_id", "product", "instrument_uid", "venue_leg",
        "owner_strategy_id", "ownership_epoch", "authorization_id", "risk_policy_revision")
    if any(before[k] != after[k] for k in identity):
        raise ValueError("scope or authority changed")
    target = ("target",) if isinstance(candidate, DecisionProposal) else ("target_side", "target_quantity", "quantity_unit")
    guarded = target
    if candidate.action == "HOLD":
        guarded += ("entry" if isinstance(candidate, DecisionProposal) else "execution",
                    "exit_plan", "valid_until", "requested_leverage")
        if isinstance(candidate, TradeIntent):
            guarded += ("max_planned_loss",)
    elif candidate.action != "UPDATE_EXIT":
        return
    if any(before.get(k) != after.get(k) for k in guarded):
        raise ValueError("HOLD/UPDATE_EXIT changes protected responsibility")
