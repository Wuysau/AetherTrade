"""Lossless application JSON profile (not a claim of RFC 8785 compliance)."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)\Z", re.ASCII)


def decimal_string(value: str) -> Decimal:
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        raise ValueError("expected finite plain decimal string")
    return Decimal(value)


def utc_timestamp(value: str) -> datetime:
    if type(value) is not str or not _UTC.fullmatch(value):
        raise ValueError("expected UTC RFC3339 timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _json_value(value: object) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON keys must be strings")
            _json_value(item)
        return
    raise ValueError("only JSON values without floating point are permitted")


def canonical_json(payload: dict) -> str:
    if type(payload) is not dict:
        raise ValueError("expected JSON object")
    _json_value(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def domain_payload(record: object) -> dict:
    """Serialize a typed domain record without float conversion.

    This is serialization, not a validator or authority issuer. WireRecord
    callers use ContractCodec so approved optional/absent fields are preserved.
    """
    from .wire import ContractCodec, DecisionProposal, ExecutionCommand, RiskPolicy, TradeIntent
    wire_types = (DecisionProposal, ExecutionCommand, RiskPolicy, TradeIntent)

    def pack(value):
        if isinstance(value, wire_types):
            return ContractCodec().encode(value)
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError('non-finite domain decimal')
            return format(value, 'f')
        if isinstance(value, Enum):
            return pack(value.value)
        if is_dataclass(value) and not isinstance(value, type):
            return {field.name: pack(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, tuple):
            return [pack(item) for item in value]
        _json_value(value)
        return value

    if not is_dataclass(record) or isinstance(record, type):
        raise ValueError('expected domain record')
    # Approved wire records have ABSENT semantics and a separate schema codec.
    if isinstance(record, wire_types):
        return ContractCodec().encode(record)
    payload = pack(record)
    canonical_json(payload)
    return payload
