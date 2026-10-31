"""Terminal resolution for native-style lists (OCO/OTO/OTOCO/OPO/OPOCO).

This is a decision model, not an executor. A working leg that becomes
CANCELED/EXPIRED/REJECTED must release pending legs from WAITING state without
inventing fills; the parent stays reconciliation-required until every member is
known terminal. No funds are released and no sibling is silently cancelled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts.order_program import identifier

TERMINAL = frozenset({'CANCELED', 'EXPIRED', 'REJECTED', 'FILLED'})
UNKNOWN = frozenset({'UNKNOWN', 'PENDING', 'RECONCILIATION_REQUIRED'})


def require(condition, code):
    if not condition:
        raise ValueError(code)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListMember:
    member_id: str
    state: str
    role: Literal['WORKING', 'PENDING', 'PROTECTION']


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberDisposition:
    member_id: str
    state: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTerminalDecision:
    parent_state: Literal['ACTIVE', 'COMPLETED', 'RECONCILIATION_REQUIRED']
    dispositions: tuple[MemberDisposition, ...]
    duties: tuple[str, ...] = ()

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


def resolve_list_terminal(template: str, members: tuple[ListMember, ...]) -> ListTerminalDecision:
    require(template in ('OCO', 'OTO', 'OTOCO', 'OPO', 'OPOCO'), 'NATIVE_LIST_TEMPLATE_REQUIRED')
    require(type(members) is tuple and members and len(members) <= 32,
            'NATIVE_LIST_MEMBERS_REQUIRED')
    require(all(type(m) is ListMember for m in members), 'NATIVE_LIST_MEMBER_REQUIRED')
    identifiers = [m.member_id for m in members]
    require(len(set(identifiers)) == len(identifiers), 'NATIVE_LIST_MEMBER_DUPLICATE')
    for member in members:
        identifier(member.member_id)
        require(isinstance(member.state, str) and member.state, 'NATIVE_LIST_STATE_REQUIRED')
        require(member.role in ('WORKING', 'PENDING', 'PROTECTION'), 'NATIVE_LIST_ROLE_REQUIRED')
    unknown = tuple(m for m in members if m.state in UNKNOWN)
    if unknown:
        return ListTerminalDecision(
            parent_state='RECONCILIATION_REQUIRED',
            dispositions=tuple(MemberDisposition(member_id=m.member_id, state=m.state, reason= 'MEMBER_UNKNOWN')
                               for m in members),
            duties=('QUERY_ORIGINAL_IDS', 'RETAIN_LIST_DUTY', 'NO_FUNDS_RELEASE'))
    working_terminal = any(m.role == 'WORKING' and m.state in TERMINAL for m in members)
    if working_terminal:
        dispositions, duties = [], ['RETAIN_LIST_DUTY']
        for member in members:
            if member.state in TERMINAL:
                dispositions.append(MemberDisposition(member_id=member.member_id, state=member.state, reason=
                                                       'ALREADY_TERMINAL'))
            elif member.role == 'PENDING':
                dispositions.append(MemberDisposition(member_id=member.member_id, state='CANCELED', reason=
                                                       'WORKING_LEG_TERMINAL'))
                duties.append('VERIFY_PENDING_LEG_NEVER_SENT')
            else:
                dispositions.append(MemberDisposition(member_id=member.member_id, state='CANCELED', reason=
                                                       'WORKING_LEG_TERMINAL'))
        parent = ('COMPLETED' if all(d.state in TERMINAL for d in dispositions)
                  else 'RECONCILIATION_REQUIRED')
        return ListTerminalDecision(parent_state=parent, dispositions=tuple(dispositions),
                                    duties=tuple(dict.fromkeys(duties)))
    if all(m.state in TERMINAL for m in members):
        return ListTerminalDecision(
            parent_state='COMPLETED',
            dispositions=tuple(MemberDisposition(member_id=m.member_id, state=m.state, reason= 'ALREADY_TERMINAL')
                               for m in members),
            duties=('RETAIN_PROTECTION_DUTY',))
    return ListTerminalDecision(
        parent_state='ACTIVE',
        dispositions=tuple(MemberDisposition(member_id=m.member_id, state=m.state, reason= 'STILL_LIVE')
                           for m in members),
        duties=('RETAIN_LIST_DUTY',))
