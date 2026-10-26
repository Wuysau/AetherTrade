"""Offline evidence reconciliation; results do not mutate commands or locks.

VenueReport lacks command-result bindings and endpoint semantics. The injected
reader must verify archived evidence and return a typed attestation bound to
the entire report and case. Default denies; a source label is never proof.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_ref(ref: t.EvidenceRef, scope: t.ScopeKey, now: str, maximum_age_seconds: int) -> bool:
    try:
        age = utc_timestamp(now) - utc_timestamp(ref.visible_at)
        return (ref.environment == scope.environment and bool(ref.id.strip())
                and bool(ref.source.strip()) and len(ref.hash) == 64
                and all(c in '0123456789abcdef' for c in ref.hash)
                and 0 <= age.total_seconds() <= maximum_age_seconds)
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True, kw_only=True)
class CommandBinding:
    scope: t.ScopeKey
    command_id: str
    operation: str
    order: t.OrderIdentity
    request_hash: str


@dataclass(frozen=True, kw_only=True)
class CommandResolution:
    binding: CommandBinding
    provenance: str
    evidence: tuple[t.EvidenceRef, ...]


@dataclass(frozen=True, kw_only=True)
class ReportAttestation:
    case_id: str
    case_hash: str
    scope: t.ScopeKey
    report_hash: str
    commands: tuple[CommandResolution, ...]
    satisfied: tuple[str, ...]
    current_positions_verified: bool
    current_orders_verified: bool
    # Spot/equity require asset balances AND ownership allocation reconciliation.
    balances_and_allocations_verified: bool
    economic_history_complete: bool


@dataclass(frozen=True, kw_only=True)
class ReconciliationAssessment:
    result: t.ReconciliationResult
    flat_verified: bool
    performance_complete: bool
    # A report alone never authorizes a write or clears a risk lock.
    allow_new_risk: bool = False


class Reconciler:
    def __init__(self, *, read_attestation: Callable | None = None,
                 read_command: Callable | None = None, clock: Callable = utc_now,
                 maximum_age_seconds: int = 60):
        if type(maximum_age_seconds) is not int or maximum_age_seconds <= 0:
            raise ValueError('positive evidence age required')
        self.read_attestation = read_attestation
        self.read_command = read_command
        self.clock = clock
        self.maximum_age_seconds = maximum_age_seconds

    def reconcile(self, case: t.ReconciliationCase, report: t.VenueReport) -> t.ReconciliationResult:
        return self.assess(case, report).result

    def assess(self, case: t.ReconciliationCase, report: t.VenueReport) -> ReconciliationAssessment:
        missing = list(case.missing)

        def denied(reason):
            return ReconciliationAssessment(result=t.ReconciliationResult(case_id=case.case_id,
                resolved=False, missing=tuple(dict.fromkeys((*missing, reason))), residual=None,
                evidence=()), flat_verified=False, performance_complete=False)

        if case.scope.environment not in (t.Environment.RESEARCH, t.Environment.PAPER):
            return denied('ENVIRONMENT_NOT_ENABLED')
        if report is None:
            return denied('REPORT_MISSING')
        coverage = report.coverage
        if (coverage.scope != case.scope or coverage.query_succeeded is not True
                or coverage.pagination_complete is not True or coverage.supported is not True
                or coverage.missing or report.next_cursor is not None):
            return denied('COVERAGE_INCOMPLETE')
        now = self.clock()
        try:
            age = (utc_timestamp(now) - utc_timestamp(report.as_of)).total_seconds()
        except (ValueError, TypeError):
            return denied('REPORT_TIME_INVALID')
        if not 0 <= age <= self.maximum_age_seconds:
            return denied('REPORT_STALE_OR_FUTURE')
        if not report.evidence or any(not valid_ref(ref, case.scope, now, self.maximum_age_seconds)
                                      for ref in report.evidence):
            return denied('REPORT_EVIDENCE_INVALID')
        if (any(order.order.identity.scope != case.scope for order in report.orders)
                or any(position.scope != case.scope for position in report.positions)
                or any(fill.scope != case.scope or fill.order.scope != case.scope for fill in report.fills)):
            return denied('REPORT_SCOPE_MISMATCH')
        try:
            report_hash = canonical_hash(domain_payload(report))
            case_hash = canonical_hash(domain_payload(case))
        except (TypeError, ValueError):
            return denied('REPORT_OR_CASE_INVALID')
        try:
            proof = self.read_attestation(case, report) if self.read_attestation else None
        except Exception:
            return denied('ATTESTATION_UNAVAILABLE')
        if (not isinstance(proof, ReportAttestation) or proof.case_id != case.case_id
                or proof.case_hash != case_hash
                or proof.scope != case.scope or proof.report_hash != report_hash):
            return denied('ATTESTATION_MISSING_OR_MISMATCH')
        if (not isinstance(proof.commands, tuple)
                or any(not isinstance(item, CommandResolution)
                       or not isinstance(item.binding, CommandBinding) for item in proof.commands)
                or not isinstance(proof.satisfied, tuple)
                or any(not isinstance(item, str) for item in proof.satisfied)):
            return denied('ATTESTATION_INVALID')
        unresolved_commands = []
        for command_id in case.command_ids:
            try:
                binding = self.read_command(case.scope, command_id) if self.read_command else None
            except Exception:
                binding = None
            matches = [item for item in proof.commands if item.binding.command_id == command_id]
            if (not isinstance(binding, CommandBinding) or binding.scope != case.scope
                    or binding.command_id != command_id or not isinstance(binding.order, t.OrderIdentity)
                    or binding.order.scope != case.scope
                    or binding.operation not in {'SUBMIT', 'CANCEL', 'AMEND', 'CLOSE', 'SET_PROTECTION'}
                    or not isinstance(binding.request_hash, str) or len(binding.request_hash) != 64
                    or any(c not in '0123456789abcdef' for c in binding.request_hash)
                    or len(matches) != 1 or matches[0].binding != binding
                    or matches[0].provenance not in {'VENUE_EVENT', 'VENUE_QUERY'}
                    or not isinstance(matches[0].evidence, tuple) or not matches[0].evidence
                    or any(ref not in report.evidence for ref in matches[0].evidence)):
                unresolved_commands.append('COMMAND_UNKNOWN:' + command_id)
        positions_verified = proof.current_positions_verified is True
        if case.scope.product in (t.Product.SPOT, t.Product.US_EQUITY):
            positions_verified = positions_verified and proof.balances_and_allocations_verified is True
        # Satisfied obligations come from the trusted archive verifier, not the
        # raw report's lifecycle flags or a missing order in an empty response.
        missing = [item for item in missing if item not in proof.satisfied]
        missing.extend(unresolved_commands)
        if case.position_ids and not positions_verified:
            missing.append('CURRENT_POSITIONS_UNVERIFIED')
        if not case.command_ids and not case.position_ids and not case.missing:
            missing.append('CASE_OBLIGATIONS_MISSING')
        # A zero snapshot with a possibly fillable order is not verified flat.
        # Local terminal policy, unconfirmed cancel/amend and unresolved case
        # obligations must not erase potential future exposure.
        orders_settled = all(
            order.lifecycle_state in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'}
            and order.command_certainty == 'CONFIRMED'
            and order.pending_operation == 'NONE'
            and order.outcome_provenance in {'VENUE_EVENT', 'VENUE_QUERY'}
            and order.leaves_quantity.value == 0
            for order in report.orders)
        flat = (not missing and positions_verified and proof.current_orders_verified is True and orders_settled
                and all(p.actual_quantity.value == 0 for p in report.positions))
        return ReconciliationAssessment(result=t.ReconciliationResult(case_id=case.case_id,
            resolved=not missing, missing=tuple(dict.fromkeys(missing)), residual=None,
            evidence=report.evidence), flat_verified=flat,
            performance_complete=proof.economic_history_complete is True)
