"""Read-only projection of existing execution facts into memory references.

The execution ledger remains the only authority for orders, fills, positions,
fees and protection. This module never writes a second ledger; it only creates
stable references and explicit UNKNOWN/STALE markers for the context assembler.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .contracts import ExecutionFactRef, StrategyMemoryScope

_TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "ABORTED_UNSENT", "COMPLETED"}
_UNKNOWN = {"UNKNOWN", "CANCEL_UNKNOWN", "PENDING", "STALE", "UNRECONCILED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionFactProjector:
    def __init__(self, execution, *, clock=None):
        self.execution = execution
        self.clock = clock or _now

    def _projection(self) -> dict:
        if self.execution is None:
            return {}
        projection = getattr(self.execution, "projection", None)
        return projection() if callable(projection) else {}

    def facts(self, scope: StrategyMemoryScope, *, plan_id: str | None = None) -> tuple[ExecutionFactRef, ...]:
        projection = self._projection()
        recorded_at = self.clock()
        result: list[ExecutionFactRef] = []
        symbol = scope.instrument.split(":")[-1]
        for order in projection.get("orders", []):
            if not isinstance(order, dict) or order.get("symbol") != symbol:
                continue
            if order.get("strategy_id") not in (None, scope.strategy_id):
                continue
            status = str(order.get("status", "UNKNOWN"))
            unknown = status in _UNKNOWN
            result.append(ExecutionFactRef(
                fact_type="ORDER", source_system="EXECUTION_LEDGER",
                source_id=str(order.get("id") or order.get("command_id")),
                scope=scope, occurred_at=str(order.get("created_at") or recorded_at),
                recorded_at=recorded_at, status=status,
                order_id=order.get("id"), command_id=order.get("command_id"),
                plan_id=plan_id, stale=bool(order.get("stale")), unknown=unknown))
            filled = str(order.get("filled_quantity", "0"))
            if filled not in ("0", "0.0", "0.000000000000"):
                result.append(ExecutionFactRef(
                    fact_type="FILL", source_system="EXECUTION_LEDGER",
                    source_id=f"{order.get('id') or order.get('command_id')}:aggregate",
                    scope=scope, occurred_at=str(order.get("created_at") or recorded_at),
                    recorded_at=recorded_at,
                    status="PARTIAL" if status not in _TERMINAL else status,
                    order_id=order.get("id"), command_id=order.get("command_id"),
                    fill_id=order.get("fill_id"), plan_id=plan_id,
                    stale=bool(order.get("stale")), unknown=unknown))
        for command in projection.get("commands", []):
            if not isinstance(command, dict) or command.get("symbol") != symbol:
                continue
            status = str(command.get("order_status") or command.get("state") or "UNKNOWN")
            result.append(ExecutionFactRef(
                fact_type="COMMAND", source_system="EXECUTION_LEDGER",
                source_id=str(command.get("command_id")), scope=scope,
                occurred_at=recorded_at, recorded_at=recorded_at, status=status,
                command_id=command.get("command_id"), plan_id=plan_id,
                unknown=status in _UNKNOWN or str(command.get("state")) in _UNKNOWN))
        for position in projection.get("positions", []):
            if not isinstance(position, dict) or position.get("symbol") != symbol:
                continue
            if position.get("strategy_id") not in (None, scope.strategy_id):
                continue
            result.append(ExecutionFactRef(
                fact_type="POSITION", source_system="EXECUTION_LEDGER",
                source_id=str(position.get("id") or f"position:{symbol}"), scope=scope,
                occurred_at=str(position.get("updated_at") or recorded_at), recorded_at=recorded_at,
                status="OPEN" if str(position.get("quantity", "0")) not in ("0", "0.0", "0.000000000000") else "FLAT",
                plan_id=plan_id))
        for plan in projection.get("protection_plans", []):
            if not isinstance(plan, dict):
                continue
            result.append(ExecutionFactRef(
                fact_type="PROTECTION", source_system="EXECUTION_LEDGER",
                source_id=str(plan.get("plan_id") or plan.get("protection_id") or "protection"),
                scope=scope, occurred_at=recorded_at, recorded_at=recorded_at,
                status=str(plan.get("state") or "UNKNOWN"), plan_id=plan_id,
                unknown=str(plan.get("state")) in _UNKNOWN))
        return tuple(result)

    def summary(self, scope: StrategyMemoryScope, *, plan_id: str | None = None) -> dict:
        facts = self.facts(scope, plan_id=plan_id)
        return {
            "authority": "EXECUTION_LEDGER",
            "plan_id": plan_id,
            "facts": [self._payload(fact) for fact in facts],
            "unknown": [fact.source_id for fact in facts if fact.unknown],
            "stale": [fact.source_id for fact in facts if fact.stale],
            "active_orders": [fact.source_id for fact in facts if fact.fact_type == "ORDER"
                              and fact.status not in _TERMINAL],
        }

    @staticmethod
    def _payload(fact: ExecutionFactRef) -> dict:
        from trading_system.contracts.canonical import domain_payload
        return domain_payload(fact)
