"""Independent cancel/replacement outcome record for cancel-replace.

Both outcomes remain separately observable. A successful replacement does not
erase a failed/unknown cancel, and no outcome releases the old hold or declares
children terminal. This is a durable record contract, not a transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.order_program import identifier

OUTCOMES = frozenset({'SUCCESS', 'FAILURE', 'UNKNOWN', 'NOT_ATTEMPTED'})
DUTIES = ('RETAIN_ORIGINAL_HOLD', 'RETAIN_CHILD_DUTY', 'NO_FUNDS_RELEASE')


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementOutcomeV2:
    contract_version: Literal['2026-10-31']
    record_kind: Literal['MANAGEMENT_OUTCOME_V2']
    command_id: str
    request_hash: str
    cancel_result: str
    replacement_result: str
    raw_payload_hash: str
    cancel_venue_id: str | None
    replacement_client_id: str | None
    replacement_venue_id: str | None
    not_attempted_reason: str | None
    duties: tuple[str, ...]

    @property
    def funds_released(self) -> Literal[False]:
        return False

    @property
    def children_terminal(self) -> Literal[False]:
        return False

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def bind_management_outcome(*, command_id: str, request_hash: str, cancel_result: str,
                            replacement_result: str, raw_payload_hash: str,
                            cancel_venue_id: str | None = None,
                            replacement_client_id: str | None = None,
                            replacement_venue_id: str | None = None,
                            not_attempted_reason: str | None = None) -> ManagementOutcomeV2:
    identifier(command_id)
    for value, code in ((request_hash, 'MANAGEMENT_REQUEST_HASH_REQUIRED'),
                        (raw_payload_hash, 'MANAGEMENT_RAW_HASH_REQUIRED')):
        if not isinstance(value, str) or not value:
            raise ValueError(code)
    if cancel_result not in OUTCOMES or replacement_result not in OUTCOMES:
        raise ValueError('MANAGEMENT_OUTCOME_STATE_INVALID')
    if cancel_result == 'NOT_ATTEMPTED' or replacement_result == 'NOT_ATTEMPTED':
        if not not_attempted_reason:
            raise ValueError('MANAGEMENT_NOT_ATTEMPTED_REASON_REQUIRED')
    duties = list(DUTIES)
    if cancel_result == 'UNKNOWN' or replacement_result == 'UNKNOWN':
        duties.append('QUERY_BOTH_ORIGINAL_IDS')
    if cancel_result == 'SUCCESS' and replacement_result == 'FAILURE':
        duties.append('REPLACEMENT_FAILED_OLD_HOLD_RETAINED')
    if cancel_result == 'FAILURE':
        duties.append('ORIGINAL_ORDER_STILL_LIVE_OR_LATE_FILL')
    if replacement_result == 'SUCCESS' and cancel_result != 'SUCCESS':
        duties.append('BOTH_ORDERS_MAY_BE_LIVE')
    if replacement_result == 'SUCCESS':
        duties.append('REPLACEMENT_MUST_HAVE_OWN_COMMAND_AND_PERMIT')
    return ManagementOutcomeV2(
        contract_version='2026-10-31', record_kind='MANAGEMENT_OUTCOME_V2',
        command_id=command_id, request_hash=request_hash,
        cancel_result=cancel_result, replacement_result=replacement_result,
        raw_payload_hash=raw_payload_hash, cancel_venue_id=cancel_venue_id,
        replacement_client_id=replacement_client_id,
        replacement_venue_id=replacement_venue_id,
        not_attempted_reason=not_attempted_reason,
        duties=tuple(dict.fromkeys(duties)))


def management_outcome_hash(outcome: ManagementOutcomeV2) -> str:
    from trading_system.contracts.canonical import domain_payload
    if type(outcome) is not ManagementOutcomeV2:
        raise ValueError('MANAGEMENT_OUTCOME_REQUIRED')
    return canonical_hash(domain_payload(outcome))
