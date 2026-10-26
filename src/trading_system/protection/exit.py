"""Authorized, quantity-bounded exits and explicit proof of flatness.

Local readers attest archived evidence and current authorization. Defaults do
not authorize anything. This service has no market-data or execution client.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.recovery.reconciler import valid_ref
from .manager import CommandRequest, ProtectionRecord, validate_command
from .native import (NativeCapability, closing_side, digest, offline, report_errors,
                     position_reported, settled)


@dataclass(frozen=True, kw_only=True)
class ExitFacts:
    position: t.ManagedPosition
    context_hash: str
    plan: t.ProtectionPlan
    capability: NativeCapability
    report: t.VenueReport
    positions_verified: bool
    orders_verified: bool
    balances_and_allocations_verified: bool
    authorization_verified: bool
    owner_strategy_id: str
    market_open: bool
    order_type: str
    limit_price: t.Price | None
    reference_price: t.Price | None
    # MARKET needs an independently enforced venue price bound. Ordinary
    # market orders cannot guarantee a maximum slippage and default to denied.
    market_price_bound_verified: bool = False


@dataclass(frozen=True, kw_only=True)
class FlatProof:
    position_id: str
    owner_strategy_id: str
    owner_book_id: str
    ownership_epoch: int
    report_hash: str
    exit_context_hash: str
    positions_verified: bool
    orders_verified: bool
    balances_and_allocations_verified: bool
    commands_resolved: tuple[str, ...]
    economic_missing_recorded: bool
    economic_missing: tuple[str, ...]


class ExitService:
    def __init__(self, manager, *, read_facts=None, read_flat_proof=None):
        self.manager, self.read_facts, self.read_flat_proof = manager, read_facts, read_flat_proof

    def request(self, position: t.ManagedPosition, context: t.EmergencyContext) -> t.ExitReceipt:
        offline(position.scope)
        manager, now = self.manager, self.manager.clock()
        scope, actor = position.scope, context.context
        incomplete = []
        if (scope not in actor.scopes or actor.environment != scope.environment
                or actor.ownership_epoch != position.ownership_epoch
                or not position.owner_strategy_id or not position.owner_book_id
                or position.ownership_epoch < 1 or not actor.authorization_id
                or not actor.identity_id or not actor.deployment_id or actor.policy_revision < 1):
            incomplete.append('EXIT_SCOPE_OR_OWNER_INVALID')
        if (context.reducible_quantity.unit != position.actual_quantity.unit
                or context.reducible_quantity.value < 0
                or context.reducible_quantity.value > abs(position.actual_quantity.value)):
            incomplete.append('REDUCIBLE_QUANTITY_INVALID')
        try:
            fresh = (utc_timestamp(now) < utc_timestamp(context.expires_at)
                     and valid_ref(context.quantity_evidence, scope, now, 60))
        except (ValueError, TypeError):
            fresh = False
        if not fresh:
            incomplete.append('EXIT_AUTHORITY_OR_QUANTITY_STALE')
        if (not isinstance(context.maximum_slippage_bps, Decimal)
                or not context.maximum_slippage_bps.is_finite()
                or not 0 <= context.maximum_slippage_bps <= 10000):
            incomplete.append('EXIT_PRICE_POLICY_INVALID')
        try:
            previous = manager.repository.load(scope, position.position_id)
        except Exception:
            return t.ExitReceipt(position_id=position.position_id, state='EXITING', command_ids=(),
                                 incomplete_reasons=('STORAGE_UNAVAILABLE',))
        if previous and previous.exit_receipt and previous.exit_receipt.state == 'FLAT_VERIFIED':
            if previous.position == position and position.actual_quantity.value == 0:
                return previous.exit_receipt
            return t.ExitReceipt(position_id=position.position_id, state='EXITING',
                command_ids=previous.exit_receipt.command_ids,
                incomplete_reasons=('POSITION_CHANGED_AFTER_EXIT',))
        if previous and previous.exit_receipt and previous.exit_receipt.command_ids:
            # A new idempotency key/expired context cannot evade a possibly sent
            # close. Reconciliation must settle the original action first.
            return replace(previous.exit_receipt, state='EXITING',
                incomplete_reasons=('QUERY_ORIGINAL_EXIT',))
        if (not actor.idempotency_key or type(actor.expected_sequence) is not int
                or actor.expected_sequence != (previous.revision if previous else 0)):
            incomplete.append('EXIT_SEQUENCE_OR_KEY_INVALID')
        try:
            facts = self.read_facts(position, context) if self.read_facts else None
        except Exception:
            facts = None
        if (not isinstance(facts, ExitFacts) or facts.position != position
                or facts.context_hash != digest(context) or facts.plan.scope != scope
                or facts.authorization_verified is not True
                or facts.owner_strategy_id != position.owner_strategy_id):
            incomplete.append('CURRENT_EXIT_AUTHORITY_REQUIRED')
        elif (report_errors(facts.report, scope, now, 60)
                or not position_reported(facts.report, position)
                or context.quantity_evidence not in facts.report.evidence
                or facts.positions_verified is not True
                or (scope.product != t.Product.USD_M_PERP
                    and facts.balances_and_allocations_verified is not True)):
            incomplete.append('CURRENT_QUANTITY_UNVERIFIED')
        if previous and (previous.position.owner_strategy_id != position.owner_strategy_id
                or previous.position.owner_book_id != position.owner_book_id
                or previous.position.ownership_epoch != position.ownership_epoch):
            incomplete.append('CURRENT_OWNER_MISMATCH')
        if incomplete:
            # Rejected authority never changes an existing lifecycle.
            return t.ExitReceipt(position_id=position.position_id, state='EXITING',
                                 command_ids=(), incomplete_reasons=tuple(incomplete))
        state = 'EXITING'
        quantity = context.reducible_quantity
        incomplete.extend(facts.capability.errors(position, now))
        if not facts.market_open:
            state = 'EXIT_PENDING_MARKET'
            incomplete.append('MARKET_CLOSED_OR_HALTED')
        if quantity.value == 0:
            incomplete.append('ZERO_REQUIRES_FLAT_PROOF')
        elif not facts.capability.tradable(quantity):
            if scope.product in {t.Product.SPOT, t.Product.US_EQUITY}:
                state = 'NONTRADABLE_RESIDUAL'
                incomplete.append('RESIDUAL_RETAINS_VALUATION_AND_OWNERSHIP')
            else:
                incomplete.append('CONTRACT_RESIDUAL_NOT_FLAT')
        if (facts.order_type not in context.allowed_order_types
                or facts.order_type not in facts.capability.order_types):
            incomplete.append('EXIT_TYPE_NOT_PREAUTHORIZED')
        if facts.order_type == 'MARKET':
            if facts.market_price_bound_verified is not True:
                incomplete.append('BOUNDED_MARKET_EXIT_UNAVAILABLE')
        elif facts.order_type == 'LIMIT':
            limit, reference = facts.limit_price, facts.reference_price
            if limit is None or reference is None or limit.currency != reference.currency:
                incomplete.append('EXIT_PRICE_BOUND_UNVERIFIED')
            elif position.actual_quantity.value != 0:
                try:
                    side = closing_side(position)
                except ValueError:
                    incomplete.append('POSITION_DIRECTION_MISMATCH')
                else:
                    slippage = (reference.value - limit.value if side == 'SELL'
                                else limit.value - reference.value) * Decimal(10000) / reference.value
                    if slippage > context.maximum_slippage_bps:
                        incomplete.append('EXIT_PRICE_BOUND_EXCEEDED')
        else:
            incomplete.append('EXIT_TYPE_UNSUPPORTED')
        if (facts.orders_verified is not True or any(not settled(o) for o in facts.report.orders)):
            incomplete.append('EXISTING_ORDERS_REQUIRE_RECONCILIATION')
        if previous:
            try:
                known = manager.repository.commands(scope, previous.command_ids)
                if any(c.state not in {'VENUE_CONFIRMED', 'VENUE_REJECTED', 'NOT_SENT', 'ABORTED_UNSENT'}
                       for c in known):
                    incomplete.append('QUERY_ORIGINAL_BEFORE_EXIT')
            except Exception:
                incomplete.append('COMMAND_STATE_UNAVAILABLE')
        plan, locks = manager._gap(position, previous.plan if previous else facts.plan, previous, now)
        plan = replace(plan, state=t.ProtectionState.EXITING)
        commands = []
        if not incomplete:
            request = CommandRequest(key=position.position_id + ':exit:' + digest(context),
                operation='CLOSE', position=position, plan=plan, quantity=quantity,
                order_type=facts.order_type, price=facts.limit_price)
            try:
                compiled = manager.compile_command(request)
                validate_command(request, compiled)
                if compiled.command.policy_revision != actor.policy_revision:
                    raise ValueError('EXIT_POLICY_REVISION_MISMATCH')
            except Exception:
                incomplete.append('COMMAND_COMPILATION_FAILED')
            else:
                commands.append(compiled.command)
                incomplete.append('REAL_ZERO_AND_RESIDUAL_ORDERS_UNVERIFIED')
        receipt = t.ExitReceipt(position_id=position.position_id, state=state,
            command_ids=tuple(c.command_id for c in commands), incomplete_reasons=tuple(incomplete))
        ids = tuple(dict.fromkeys((*(previous.command_ids if previous else ()), *receipt.command_ids)))
        record = ProtectionRecord(position=position, plan=plan, revision=previous.revision + 1 if previous else 1,
            fill_hashes=previous.fill_hashes if previous else (), entries=previous.entries if previous else (),
            command_ids=ids, locks=locks, case=manager._case(position, ids, incomplete,
                previous.case if previous else None), missing=tuple(incomplete),
            replacement=previous.replacement if previous else None,
            exit_receipt=receipt, exit_context_hash=digest(context), exit_started_at=now)
        action = manager._commit_dispatch(record, previous, commands)
        return replace(receipt, incomplete_reasons=action.missing)

    def verify_flat(self, position: t.ManagedPosition, report: t.VenueReport) -> t.ExitReceipt:
        offline(position.scope)
        manager = self.manager
        try:
            previous = manager.repository.load(position.scope, position.position_id)
        except Exception:
            return t.ExitReceipt(position_id=position.position_id, state='EXITING', command_ids=(),
                incomplete_reasons=('STORAGE_UNAVAILABLE',))
        errors = list(report_errors(report, position.scope, manager.clock(), 60))
        if errors:
            return t.ExitReceipt(position_id=position.position_id, state='EXITING',
                command_ids=previous.exit_receipt.command_ids if previous and previous.exit_receipt else (),
                incomplete_reasons=tuple(errors))
        if previous is None or previous.exit_receipt is None:
            errors.append('DURABLE_EXIT_REQUIRED')
        try:
            proof = self.read_flat_proof(previous, report) if self.read_flat_proof and previous else None
        except Exception:
            proof = None
        if (not isinstance(proof, FlatProof) or proof.position_id != position.position_id
                or proof.owner_strategy_id != position.owner_strategy_id
                or proof.owner_book_id != position.owner_book_id
                or proof.ownership_epoch != position.ownership_epoch
                or proof.report_hash != digest(report) or previous is None
                or proof.exit_context_hash != previous.exit_context_hash
                or proof.positions_verified is not True or proof.orders_verified is not True
                or proof.economic_missing_recorded is not True
                or not set(previous.command_ids) <= set(proof.commands_resolved)
                or (position.scope.product != t.Product.USD_M_PERP
                    and proof.balances_and_allocations_verified is not True)):
            errors.append('FLAT_PROOF_MISSING_OR_MISMATCH')
        if previous:
            p = previous.position
            if (p.owner_strategy_id != position.owner_strategy_id or p.owner_book_id != position.owner_book_id
                    or p.ownership_epoch != position.ownership_epoch
                    or (previous.exit_started_at and
                        utc_timestamp(report.as_of) < utc_timestamp(previous.exit_started_at))):
                errors.append('FLAT_OWNER_OR_TIME_MISMATCH')
        # Explicit scoped zero is required, even when the venue's empty list
        # could be meaningful. Cash additionally needs the allocation proof.
        matches = [p for p in report.positions if p.position_id == position.position_id]
        if (len(matches) != 1 or matches[0].scope != position.scope
                or matches[0].owner_strategy_id != position.owner_strategy_id
                or matches[0].owner_book_id != position.owner_book_id
                or matches[0].ownership_epoch != position.ownership_epoch
                or matches[0].actual_quantity.unit != position.actual_quantity.unit
                or any(p.actual_quantity.value != 0 for p in report.positions)
                or any(not settled(o) for o in report.orders)):
            errors.append('REAL_ZERO_OR_RESIDUAL_ORDERS_UNVERIFIED')
        if errors:
            return t.ExitReceipt(position_id=position.position_id, state='EXITING',
                command_ids=previous.exit_receipt.command_ids if previous and previous.exit_receipt else (),
                incomplete_reasons=tuple(dict.fromkeys(errors)))
        receipt = replace(previous.exit_receipt, state='FLAT_VERIFIED',
                          incomplete_reasons=proof.economic_missing)
        updated = replace(previous, position=matches[0], revision=previous.revision + 1,
            plan=replace(previous.plan, state=t.ProtectionState.FLAT_VERIFIED,
                coverage_quantity=t.Quantity(value=Decimal(0), unit=position.actual_quantity.unit),
                last_verified_at=report.as_of, coverage_gap_since=None), exit_receipt=receipt,
            missing=('CAUSE_LOCK_RESOLUTION_REQUIRED',),
            case=manager._case(position, previous.command_ids, ('CAUSE_LOCK_RESOLUTION_REQUIRED',), previous.case))
        try:
            manager.repository.save(updated, previous)
        except Exception:
            return replace(receipt, state='EXITING', incomplete_reasons=('STORAGE_UNAVAILABLE',))
        return receipt
