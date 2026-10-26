"""Offline availability checks. Total balances never imply settled buying power.

Readers are trusted composition ports to archived, reconciled evidence, not
user/model supplied attestations. No settlement schedule or currency conversion
is inferred here. Successful inspection is not a reservation or order permit.
"""
from dataclasses import dataclass
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.protection.native import digest, offline, report_errors
from trading_system.recovery.reconciler import valid_ref


@dataclass(frozen=True, kw_only=True)
class AvailabilityProof:
    scope: t.ScopeKey
    report_hash: str
    session_hash: str
    instrument_hash: str
    rule_revision: int
    session_id: str
    valid_until: str
    settled_available: t.Money
    unsettled: t.Money
    sellable: t.Quantity
    position_id: str
    ownership_epoch: int
    reservations_reconciled: bool
    evidence: t.EvidenceRef


class CashBook:
    def __init__(self, *, read_proof: Callable | None = None):
        self.read_proof = read_proof

    def availability(self, report: t.VenueReport, position: t.ManagedPosition,
                     instrument: t.InstrumentSpec, session: t.SessionState,
                     now: t.ClockReading) -> AvailabilityProof:
        offline(position.scope)
        if (position.scope.product != t.Product.US_EQUITY or position.scope.venue_leg != 'BOTH'
                or instrument.product != position.scope.product
                or instrument.instrument_uid != position.scope.instrument_uid
                or instrument.quantity_step.unit != position.actual_quantity.unit
                or type(instrument.rule_revision) is not int or instrument.rule_revision < 1
                or not position.owner_strategy_id or not position.owner_book_id
                or type(position.ownership_epoch) is not int or position.ownership_epoch < 1
                or session.instrument_uid != instrument.instrument_uid
                or session.state != 'OPEN' or now.clock_health != 'HEALTHY'):
            raise ValueError('EQUITY_SESSION_UNAVAILABLE')
        if report_errors(report, position.scope, now.utc_now, 60):
            raise ValueError('AVAILABILITY_REPORT_INVALID')
        proof = self.read_proof(report, position, instrument, session) if self.read_proof else None
        if (not isinstance(proof, AvailabilityProof) or proof.scope != position.scope
                or proof.report_hash != digest(report) or proof.rule_revision != instrument.rule_revision
                or proof.session_hash != digest(session) or proof.instrument_hash != digest(instrument)
                or proof.session_id != session.session_id or proof.position_id != position.position_id
                or proof.ownership_epoch != position.ownership_epoch
                or proof.reservations_reconciled is not True or proof.evidence not in report.evidence
                or not valid_ref(session.evidence, position.scope, now.utc_now, 60)
                or not utc_timestamp(report.as_of) <= utc_timestamp(now.utc_now) < utc_timestamp(proof.valid_until)
                or not 0 <= (utc_timestamp(now.utc_now) - utc_timestamp(session.observed_at)).total_seconds() <= 60):
            raise ValueError('AVAILABILITY_PROOF_REQUIRED')
        matches = [p for p in report.positions if p.position_id == position.position_id]
        balances = [b for b in report.balances if b.currency == instrument.settlement_currency]
        if (matches != [position] or len(balances) != 1
                or proof.settled_available.currency != instrument.settlement_currency
                or proof.unsettled.currency != instrument.settlement_currency
                or proof.settled_available.amount < 0 or proof.unsettled.amount < 0
                or proof.settled_available.amount + proof.unsettled.amount > balances[0].amount
                or proof.sellable.unit != position.actual_quantity.unit
                or not 0 <= proof.sellable.value <= position.actual_quantity.value):
            raise ValueError('AVAILABILITY_AMOUNTS_INVALID')
        return proof

    def require(self, *, report, position, instrument, session, now,
                cash: t.Money | None = None, shares: t.Quantity | None = None,
                automatic_operations: tuple[str, ...] = ()) -> AvailabilityProof:
        if automatic_operations:
            # No reviewed mint/FX/transfer capability or authorization port exists.
            raise ValueError('AUTOMATIC_FUNDING_UNSUPPORTED')
        proof = self.availability(report, position, instrument, session, now)
        if cash is not None and (cash.currency != proof.settled_available.currency
                or not 0 <= cash.amount <= proof.settled_available.amount):
            raise ValueError('SETTLED_CASH_INSUFFICIENT')
        if shares is not None and (shares.unit != proof.sellable.unit
                or not 0 <= shares.value <= proof.sellable.value):
            raise ValueError('SELLABLE_SHARES_INSUFFICIENT')
        return proof
