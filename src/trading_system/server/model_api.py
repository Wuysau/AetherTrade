"""Strict HTTP envelopes for explicit model profile selection."""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .validation import Payload, Symbol


class ProfileVersion(Payload):
    expected_revision: StrictInt = Field(ge=0)


class ProfileAnalysis(Payload):
    symbol: Symbol
    analysis_mode: Literal['single', 'consensus']
    model_profile_ids: list[StrictStr] = Field(min_length=1, max_length=3)
    expected_revisions: dict[StrictStr, StrictInt]
    acknowledge_cost: StrictBool

    @field_validator('acknowledge_cost')
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError('explicit model-call acknowledgement required')
        return value

    @model_validator(mode='after')
    def selection(self):
        identifiers = self.model_profile_ids
        if (len(set(identifiers)) != len(identifiers)
                or any(not 1 <= len(identifier) <= 64 for identifier in identifiers)
                or set(self.expected_revisions) != set(identifiers)
                or any(value < 0 for value in self.expected_revisions.values())
                or (self.analysis_mode == 'single' and len(identifiers) != 1)
                or (self.analysis_mode == 'consensus' and len(identifiers) < 2)):
            raise ValueError('invalid explicit model selection')
        return self
