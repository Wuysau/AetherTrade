"""Bounded console inputs using the project's lossless decimal profile."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from trading_system.contracts.canonical import decimal_string
from .errors import ApiError

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


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


class StrategyCreate(Payload):
    name: StrictStr = Field(min_length=1, max_length=80)
    symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    mode: Literal["AI", "RULE", "HYBRID"]
    budget: StrictStr
    target_pct: StrictStr


class StrategyPatch(Payload):
    name: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"] | None = None
    mode: Literal["AI", "RULE", "HYBRID"] | None = None
    budget: StrictStr | None = None
    target_pct: StrictStr | None = None
    status: Literal["DRAFT", "PAUSED"] | None = None
    expected_revision: int = Field(ge=1)


class RiskUpdate(Payload):
    max_position_pct: StrictStr
    max_daily_loss: StrictStr
    expected_revision: int = Field(ge=1)


class PaperOrder(Payload):
    symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    side: Literal["BUY", "SELL"]
    quantity: StrictStr
    strategy_id: StrictStr | None = Field(default=None, max_length=64)


class PaperAction(Payload):
    action: Literal["pause_new", "resume_new", "cancel_entries", "exit_owned"]
    expected_revision: int = Field(ge=1)
    confirmation: StrictStr | None = Field(default=None, max_length=32)
