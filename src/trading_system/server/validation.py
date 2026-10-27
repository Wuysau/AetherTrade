"""Bounded console inputs using the project's lossless decimal profile."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from trading_system.contracts.canonical import decimal_string
from .errors import ApiError

from .instruments import ALL_SYMBOLS

SYMBOLS = ALL_SYMBOLS
Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "TSLABUSDT", "NVDABUSDT", "AAPLBUSDT"]


def amount(value: str, *, positive: bool = True, maximum: str = "1000000000000", scale: int = 12) -> Decimal:
    try:
        if type(value) is not str or len(value) > 48:
            raise ValueError()
        number = decimal_string(value)
        if number < 0 or (positive and number == 0) or number > Decimal(maximum) or number.as_tuple().exponent < -scale:
            raise ValueError()
        return number
    except (ValueError, TypeError):
        raise ApiError("INVALID_DECIMAL", "金额、数量或比例必须是范围内的有限十进制字符串。") from None


def number(value: Decimal | int) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(Payload):
    username: StrictStr = Field(min_length=2, max_length=64)
    password: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value):
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid username")
        return value


class Setup(Credentials):
    password: StrictStr = Field(min_length=10, max_length=256)
    initial_cash: StrictStr


class PriceRule(Payload):
    kind: Literal['PRICE_THRESHOLD'] = 'PRICE_THRESHOLD'
    operator: Literal['LTE', 'GTE']
    price: StrictStr
    stop_loss_pct: StrictStr = '5'


class StrategyCreate(Payload):
    name: StrictStr = Field(min_length=1, max_length=80)
    symbol: Symbol
    mode: Literal["AI", "RULE", "HYBRID"]
    budget: StrictStr
    target_pct: StrictStr
    status: Literal["DRAFT", "ACTIVE", "PAUSED"] = "DRAFT"
    analysis_interval_seconds: StrictInt = Field(default=300, ge=60, le=86400)
    auto_execute: StrictBool = False
    acknowledge_model_cost: StrictBool = False
    analysis_mode: Literal["single", "consensus"] = "single"
    model_profile_ids: list[StrictStr] = Field(default_factory=list, max_length=3)
    rule: PriceRule | None = None
    acknowledge_paper_automation: StrictBool = False


class StrategyPatch(Payload):
    name: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    symbol: Symbol | None = None
    mode: Literal["AI", "RULE", "HYBRID"] | None = None
    budget: StrictStr | None = None
    target_pct: StrictStr | None = None
    status: Literal["DRAFT", "ACTIVE", "PAUSED"] | None = None
    analysis_interval_seconds: StrictInt | None = Field(default=None, ge=60, le=86400)
    auto_execute: StrictBool | None = None
    acknowledge_model_cost: StrictBool = False
    analysis_mode: Literal["single", "consensus"] | None = None
    model_profile_ids: list[StrictStr] | None = Field(default=None, max_length=3)
    expected_revision: int = Field(ge=1)
    rule: PriceRule | None = None
    acknowledge_paper_automation: StrictBool = False


class RiskUpdate(Payload):
    max_position_pct: StrictStr
    max_daily_loss: StrictStr
    expected_revision: int = Field(ge=1)


class PaperOrder(Payload):
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    quantity: StrictStr
    strategy_id: StrictStr | None = Field(default=None, max_length=64)


class PaperEvent(Payload):
    event_id: StrictStr = Field(min_length=1, max_length=100)
    status: Literal['SUBMITTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_UNKNOWN', 'CANCELED', 'EXPIRED', 'REJECTED', 'PROTECTION_VERIFIED', 'PROTECTION_FAILED']
    cumulative_quantity: StrictStr
    price: StrictStr | None = None
    occurred_at: StrictStr | None = None


class PaperAction(Payload):
    action: Literal["pause_new", "resume_new", "cancel_entries", "exit_owned"]
    expected_revision: int = Field(ge=1)
    confirmation: StrictStr | None = Field(default=None, max_length=32)


class ModelTest(Payload):
    expected_revision: StrictInt = Field(ge=1)


class ModelAnalysis(ModelTest):
    symbol: Symbol
    acknowledge_cost: StrictBool

    @field_validator("acknowledge_cost")
    @classmethod
    def explicit_acknowledgement(cls, value):
        if value is not True:
            raise ValueError("manual provider-call acknowledgement required")
        return value
