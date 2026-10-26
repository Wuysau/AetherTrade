"""Deterministic replacement transitions; decisions are not write authority."""
from dataclasses import dataclass, replace

from trading_system.contracts import types as t
from .native import NativeCapability, same_order, settled


@dataclass(frozen=True, kw_only=True)
class ReplacementProgress:
    old: t.ProtectionPlan
    new: t.ProtectionPlan
    mode: str
    phase: str
    gap_since: str | None
    started_at: str


@dataclass(frozen=True, kw_only=True)
class ReplacementDecision:
    progress: ReplacementProgress
    operation: str | None
    target: t.OwnedOrderRef | None
    missing: tuple[str, ...]


def start(old: t.ProtectionPlan, new: t.ProtectionPlan, capability: NativeCapability,
          approved_flow: str | None, now: str) -> ReplacementDecision:
    if (old.scope != new.scope or old.plan_id != new.plan_id
            or new.replacement_revision != old.replacement_revision + 1
            or len(old.native_order_ids) != 1 or new.native_order_ids
            or old.mode != 'NATIVE' or new.mode != 'NATIVE'):
        raise ValueError('INVALID_REPLACEMENT_BINDING')
    if capability.atomic_amend:
        mode, phase, operation, target = 'ATOMIC', 'VERIFY_NEW', 'AMEND', old.native_order_ids[0]
    elif capability.coexist and old.scope.product == t.Product.USD_M_PERP:
        # Cash assets are never assumed free to back two full-size sell orders.
        mode, phase, operation, target = 'NEW_FIRST', 'VERIFY_NEW', 'SET_PROTECTION', None
    elif approved_flow in {'CANCEL_CONFIRM_REPLACE', 'CANCEL_CONFIRM_CLOSE'}:
        mode, phase, operation, target = approved_flow, 'VERIFY_CANCEL', 'CANCEL', old.native_order_ids[0]
    else:
        return ReplacementDecision(progress=ReplacementProgress(old=old, new=new,
            mode='BLOCKED', phase='BLOCKED', gap_since=None, started_at=now), operation=None,
            target=None, missing=('APPROVED_REPLACEMENT_FLOW_REQUIRED',))
    gap = now if mode.startswith('CANCEL_') else old.coverage_gap_since
    return ReplacementDecision(progress=ReplacementProgress(old=old, new=new,
        mode=mode, phase=phase, gap_since=gap, started_at=now), operation=operation, target=target, missing=())


def advance(progress: ReplacementProgress, report: t.VenueReport, *,
            new_verified: bool) -> ReplacementDecision:
    """Caller validates fresh full coverage and a report-bound archive proof.

    Absence of the old order is insufficient: require an explicit original-ID
    terminal projection. This conservative path cannot silently skip a cancel.
    """
    old = progress.old.native_order_ids[0]
    matches = [o for o in report.orders if same_order(old, o.order)]
    canceled = len(matches) == 1 and settled(matches[0])
    if progress.phase == 'VERIFY_CANCEL' and canceled:
        if progress.mode == 'CANCEL_CONFIRM_CLOSE':
            return ReplacementDecision(progress=replace(progress, phase='EXIT_REQUIRED'),
                operation=None, target=None, missing=('AUTHORIZED_EXIT_REQUIRED',))
        return ReplacementDecision(progress=replace(progress, phase='VERIFY_NEW'),
            operation='SET_PROTECTION', target=None, missing=())
    if progress.phase == 'VERIFY_NEW' and new_verified:
        if progress.mode == 'NEW_FIRST':
            return ReplacementDecision(progress=replace(progress, phase='VERIFY_OLD_CANCEL'),
                operation='CANCEL', target=old, missing=())
        return ReplacementDecision(progress=replace(progress, phase='COMPLETE'),
                                   operation=None, target=None, missing=())
    if progress.phase == 'VERIFY_OLD_CANCEL' and canceled and new_verified:
        return ReplacementDecision(progress=replace(progress, phase='COMPLETE'),
                                   operation=None, target=None, missing=())
    return ReplacementDecision(progress=progress, operation=None, target=None,
                               missing=('REPLACEMENT_EVIDENCE_PENDING',))
