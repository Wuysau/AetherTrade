"""Credential-free provider boundary and local, durable research audit.

Adapters are trusted host code, not model tools. They own any provider-specific
transport outside this module and must enforce CallLimits during generation.
No SDK, HTTP client, credential loader, retries or fallback is installed here.
The SQLite journal is an offline audit adapter, not the trading ledger.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
import sqlite3
from typing import Protocol

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, utc_timestamp
from .cost_budget import CallLimits


class Role(StrEnum):
    RESEARCH = 'research'
    DECISION = 'decision'
    REVIEW = 'review'


class ModelBoundaryError(ValueError):
    """Stable local reason only; never include provider exceptions or secrets."""


def bounded_utf8(text: str, maximum_bytes: int = 262144) -> bytes:
    if type(text) is not str or len(text) > maximum_bytes:
        raise ModelBoundaryError('INVALID_MODEL_OUTPUT')
    try:
        encoded = text.encode('utf-8')
    except UnicodeError:
        raise ModelBoundaryError('INVALID_MODEL_OUTPUT') from None
    if len(encoded) > maximum_bytes:
        raise ModelBoundaryError('INVALID_MODEL_OUTPUT')
    return encoded


def public_token(value: str) -> str:
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_:.\-]{1,100}', value) is None:
        raise ModelBoundaryError('INVALID_PUBLIC_HANDLE')
    return value


def strict_json(text: str, *, maximum_bytes: int = 262144) -> dict:
    """Whole JSON objects only: reject duplicate keys, NaN and JSON floats."""
    bounded_utf8(text, maximum_bytes)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ModelBoundaryError('DUPLICATE_JSON_KEY')
            result[key] = value
        return result

    def invalid_number(_):
        raise ModelBoundaryError('INVALID_JSON_NUMBER')

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_float=invalid_number,
                            parse_constant=invalid_number)
        # Encoding the decoded object also rejects escaped lone surrogates.
        bounded_utf8(canonical_json(result), maximum_bytes)
        return result
    except (ValueError, TypeError, RecursionError):
        raise ModelBoundaryError('INVALID_MODEL_OUTPUT') from None


@dataclass(frozen=True, kw_only=True)
class ProviderConfig:
    provider: str
    model: str
    model_version: str
    prompt_version: str
    prompt: str
    roles: tuple[Role, ...]

    def __post_init__(self):
        for value in (self.provider, self.model, self.model_version, self.prompt_version):
            public_token(value)
        if (type(self.prompt) is not str or not self.prompt or len(self.prompt) > 16000 or not self.roles
                or any(type(role) is not Role for role in self.roles)):
            raise ValueError('INVALID_PROVIDER_CONFIG')
        bounded_utf8(self.prompt)


@dataclass(frozen=True, kw_only=True)
class ProviderInput:
    """Fresh per call; no job/context, transport configuration or conversation."""
    role: Role
    model: str
    model_version: str
    prompt_version: str
    system_prompt: str
    data_json: str


@dataclass(frozen=True, kw_only=True)
class ProviderOutput:
    complete: bool
    model_version: str
    public_json: str
    # Trusted adapter billing, NEVER parsed out of public_json/model text.
    billed_cost: t.Money | None


class ProviderAdapter(Protocol):
    def complete(self, request: ProviderInput, limits: CallLimits) -> ProviderOutput: ...


class ProviderRegistry:
    def __init__(self, entries: tuple[tuple[ProviderConfig, ProviderAdapter], ...]):
        self._entries = {}
        for config, adapter in entries:
            key = config.provider, config.model
            if key in self._entries:
                raise ValueError('DUPLICATE_PROVIDER_MODEL')
            self._entries[key] = config, adapter

    def resolve(self, provider: str, model: str, role: Role):
        entry = self._entries.get((provider, model))
        if entry is None or role not in entry[0].roles:
            raise ModelBoundaryError('PROVIDER_MODEL_ROLE_NOT_ALLOWED')
        return entry


class DecisionAudit:
    """Append-only canonical records committed before references are returned.

    Use a dedicated local file, with host-managed access/retention. Callers cannot
    supply SQL. A new connection per append also avoids holding DB transactions
    across provider calls. This does not implement multi-host budget recovery.
    """
    def __init__(self, path: str | Path):
        if str(path) == ':memory:':
            raise ValueError('FILE_BACKED_AUDIT_REQUIRED')
        self._path = str(path)
        db = self._connect()
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS decision_audit '
                           '(record_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self._path)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def append(self, *, kind: str, identifier: str, payload: Mapping,
               environment: t.Environment, visible_at: str) -> t.EvidenceRef:
        if environment == t.Environment.LIVE:
            raise ModelBoundaryError('LIVE_DISABLED')
        utc_timestamp(visible_at)
        public_token(kind)
        record = {'kind': kind, 'identifier': identifier, 'payload': dict(payload),
                  'environment': environment.value, 'visible_at': visible_at}
        encoded, digest = canonical_json(record), canonical_hash(record)
        key = kind + ':' + digest
        db = self._connect()
        try:
            with db:
                db.execute('INSERT OR IGNORE INTO decision_audit VALUES (?, ?, ?)', (key, encoded, digest))
                row = db.execute('SELECT payload, digest FROM decision_audit WHERE record_id=?', (key,)).fetchone()
                if row != (encoded, digest):
                    raise ModelBoundaryError('AUDIT_CONFLICT')
        finally:
            db.close()
        return t.EvidenceRef(id=key, hash=digest, environment=environment,
                             source='ip19-local-audit', visible_at=visible_at)

    def read(self, ref: t.EvidenceRef) -> dict:
        db = self._connect()
        try:
            row = db.execute('SELECT payload, digest FROM decision_audit WHERE record_id=?', (ref.id,)).fetchone()
        finally:
            db.close()
        if row is None:
            raise ModelBoundaryError('AUDIT_NOT_FOUND')
        record = strict_json(row[0], maximum_bytes=2_000_000)
        if (row[1] != ref.hash or canonical_hash(record) != ref.hash
                or ref.source != 'ip19-local-audit'
                or record.get('environment') != ref.environment.value or record.get('visible_at') != ref.visible_at):
            raise ModelBoundaryError('AUDIT_INTEGRITY_FAILED')
        return record
