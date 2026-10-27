"""Shared immutable command/outcome bindings; no persistence or send authority."""
from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec, ExecutionCommand


def command_request(command: ExecutionCommand) -> dict:
    """Whole immutable request, excluding only outcome metadata."""
    body = ContractCodec().encode(command)
    for key in ('state', 'outcome_provenance', 'durable_record_ref'):
        del body[key]
    return body


def command_hash(command: ExecutionCommand) -> str:
    return canonical_hash(command_request(command))


@dataclass(frozen=True, kw_only=True)
class CommandOutcome:
    command_id: str
    request_hash: str
    durable_record_ref: str
    receipt: t.CommandReceipt
    provenance: str
