"""Activation-bound observed-volume facts for local VP admission.

Only archived, verified, complete pages may cap a VP child. Short pages or
candidate observations remain observations and never create headroom. This
module does not read the network or archive files; a trusted host supplies the
page facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.order_program import amount
from trading_system.risk.policy_checks import exact_risk_arithmetic


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalVolumePage:
    request_hash: str
    manifest_hash: str
    payload_hash: str
    start: str
    end: str
    quantity_unit: str
    observed_quantity: str
    source_verified: bool
    coverage_complete: bool
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalVolumeWindowFacts:
    activation_id: str
    scope: object
    window_start: str
    window_end: str
    quantity_basis: Literal['TOTAL', 'NON_RPI']
    quantity_unit: str
    observed_quantity: str
    source_verified: bool
    coverage_complete: bool
    scan_status: str
    request_hashes: tuple[str, ...]
    manifest_hashes: tuple[str, ...]
    payload_hashes: tuple[str, ...]

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def bind_local_volume_window(*, activation, pages: tuple[LocalVolumePage, ...],
                             now: str, quantity_basis: str = 'TOTAL') -> LocalVolumeWindowFacts:
    if quantity_basis not in ('TOTAL', 'NON_RPI'):
        raise ValueError('LOCAL_VOLUME_BASIS_REQUIRED')
    if type(pages) is not tuple or not pages:
        raise ValueError('LOCAL_VOLUME_PAGES_REQUIRED')
    if any(type(page) is not LocalVolumePage for page in pages):
        raise ValueError('LOCAL_VOLUME_PAGE_REQUIRED')
    unit = activation.authority.units.quantity_unit
    start = utc_timestamp(activation.activated_at)
    end = utc_timestamp(now)
    previous_end = None
    total = Decimal(0)
    ordered = tuple(sorted(pages, key=lambda value: utc_timestamp(value.start)))
    with exact_risk_arithmetic():
        for page in ordered:
            page_start, page_end = utc_timestamp(page.start), utc_timestamp(page.end)
            if page_start < start or page_end > end or page_start >= page_end:
                raise ValueError('LOCAL_VOLUME_WINDOW_INVALID')
            if previous_end is not None and page_start != previous_end:
                raise ValueError('LOCAL_VOLUME_WINDOW_GAP_OR_OVERLAP')
            if page.quantity_unit != unit:
                raise ValueError('LOCAL_VOLUME_UNIT_CHANGED')
            total += amount(page.observed_quantity, zero=True)
            previous_end = page_end
    verified = all(page.source_verified for page in pages)
    complete = (all(page.coverage_complete for page in pages)
                and utc_timestamp(ordered[0].start) == start
                and utc_timestamp(ordered[-1].end) == end)
    return LocalVolumeWindowFacts(
        activation_id=activation.activation_id, scope=activation.authority.scope,
        window_start=activation.activated_at, window_end=now, quantity_basis=quantity_basis,
        quantity_unit=unit, observed_quantity=format(total, 'f'),
        source_verified=verified, coverage_complete=complete,
        scan_status='PAGE_BOUNDARY_OBSERVED',
        request_hashes=tuple(page.request_hash for page in pages),
        manifest_hashes=tuple(page.manifest_hash for page in pages),
        payload_hashes=tuple(page.payload_hash for page in pages))


def require_verified_volume(facts: LocalVolumeWindowFacts) -> None:
    if type(facts) is not LocalVolumeWindowFacts:
        raise ValueError('LOCAL_VOLUME_FACTS_REQUIRED')
    if not facts.source_verified:
        raise ValueError('LOCAL_VOLUME_SOURCE_UNVERIFIED')
    if not facts.coverage_complete or facts.scan_status != 'PAGE_BOUNDARY_OBSERVED':
        raise ValueError('LOCAL_VOLUME_COVERAGE_INCOMPLETE')
