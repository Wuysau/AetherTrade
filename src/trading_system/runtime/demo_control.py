"""Explicit operator inputs for a bounded BTCUSDT Demo AI strategy.

Validation does not qualify an account, start a model or authorize a venue send.
"""
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from trading_system.engine.binance_demo.contracts import decimal


class DemoStrategyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_profile_id: str = Field(min_length=1, max_length=64)
    model_revision: int = Field(strict=True, ge=1)
    expected_strategy_revision: int = Field(strict=True, ge=0)
    budget: str
    max_planned_loss: str
    max_daily_loss: str
    fee_rate_cap: str
    holding_period_seconds: int = Field(strict=True, ge=60, le=86400)
    authorization_seconds: int = Field(strict=True, ge=60, le=900)
    acknowledge_model_cost: bool = Field(strict=True)
    acknowledge_demo_execution: bool = Field(strict=True)

    @model_validator(mode='after')
    def validate_limits(self):
        budget = decimal(self.budget, positive=True)
        planned = decimal(self.max_planned_loss, positive=True)
        daily = decimal(self.max_daily_loss, positive=True)
        fee = decimal(self.fee_rate_cap, positive=True)
        if budget > 10000 or planned > daily or daily > budget or fee > Decimal('0.01'):
            raise ValueError('Invalid Demo risk limits')
        if not self.acknowledge_model_cost or not self.acknowledge_demo_execution:
            raise ValueError('Explicit model-cost and Demo execution acknowledgements required')
        return self


class DemoBindInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledge_account: bool = Field(strict=True)

    @model_validator(mode='after')
    def require_acknowledgement(self):
        if not self.acknowledge_account: raise ValueError('Explicit Demo account acknowledgement required')
        return self


class DemoTakeoverInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_responsibility: bool = Field(strict=True)

    @model_validator(mode='after')
    def require_acknowledgement(self):
        if not self.acknowledge_responsibility: raise ValueError('Explicit responsibility acknowledgement required')
        return self


class DemoWindowInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    duration_seconds: int = Field(strict=True, ge=1, le=900)
    decision_interval_seconds: int = Field(strict=True, ge=30, le=900)
    expected_strategy_revision: int = Field(strict=True, ge=1)
    expected_grant_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    acknowledge_model_cost: bool = Field(strict=True)
    acknowledge_demo_orders: bool = Field(strict=True)

    @model_validator(mode='after')
    def require_acknowledgements(self):
        if not self.acknowledge_model_cost or not self.acknowledge_demo_orders:
            raise ValueError('Explicit model-cost and Demo-order acknowledgements required')
        return self


class DemoRecoveryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    duration_seconds: int = Field(strict=True, ge=1, le=900)
    acknowledge_state_changes: bool = Field(strict=True)

    @model_validator(mode='after')
    def require_acknowledgement(self):
        if not self.acknowledge_state_changes: raise ValueError('Explicit reconciliation acknowledgement required')
        return self
