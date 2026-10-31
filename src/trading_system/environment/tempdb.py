"""Controlled temporary-database leases inside the single fixed DEV cluster.

Allocation and reclamation are registry decisions, not SQL execution. A name
prefix is never sufficient ownership proof: the exact cluster instance ID,
database OID, connection role and lease must match an observation. Unknown,
expired, foreign or still-connected resources stay in place as BLOCKED.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .budget import Budget, validate_budget
from .identity import FIXED_DEV


class TempDatabaseRefused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TempDatabaseLease:
    run_id: str
    instance_id: str
    database_name: str
    database_oid: int
    connection_role: str
    lease_id: str
    created_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class ReclaimResult:
    status: str
    reason: str = ''


def _lease_id(run_id: str, name: str, oid: int) -> str:
    return hashlib.sha256(f'{run_id}\0{name}\0{oid}'.encode('utf-8')).hexdigest()[:32]


class TempDatabaseManager:
    def __init__(self, budget: Budget, *, clock, observe):
        validate_budget(budget)
        self.budget, self.clock, self.observe = budget, clock, observe
        self._active: dict[str, TempDatabaseLease] = {}
        self._released: dict[str, TempDatabaseLease] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    def _expired(self, lease: TempDatabaseLease) -> bool:
        return self.clock() >= lease.expires_at

    def allocate(self, *, run_id: str, instance_id: str, database_name: str,
                 database_oid: int, connection_role: str) -> TempDatabaseLease:
        for value, code in ((run_id, 'RUN_ID_REQUIRED'), (database_name, 'DATABASE_NAME_REQUIRED'),
                            (connection_role, 'CONNECTION_ROLE_REQUIRED')):
            if not isinstance(value, str) or not value:
                raise TempDatabaseRefused(code)
        if instance_id != FIXED_DEV.service_id:
            raise TempDatabaseRefused('FIXED_DEV_INSTANCE_REQUIRED')
        if (not database_name.startswith('at_dev_') or len(database_name) > 63):
            raise TempDatabaseRefused('TEMP_DATABASE_NAMESPACE_REQUIRED')
        if type(database_oid) is not int or database_oid <= 0:
            raise TempDatabaseRefused('DATABASE_OID_REQUIRED')
        if any(self._expired(lease) for lease in self._active.values()):
            raise TempDatabaseRefused('EXPIRED_LEASE_UNRESOLVED')
        if len(self._active) >= self.budget.temp_database_limit:
            raise TempDatabaseRefused('TEMP_DATABASE_BUDGET_EXHAUSTED')
        if any(lease.database_name == database_name for lease in self._active.values()):
            raise TempDatabaseRefused('TEMP_DATABASE_NAME_CONFLICT')
        if any(lease.database_oid == database_oid for lease in self._active.values()):
            raise TempDatabaseRefused('TEMP_DATABASE_OID_CONFLICT')
        observed = self.observe(database_name)
        if observed != (instance_id, database_name, database_oid):
            raise TempDatabaseRefused('TEMP_DATABASE_IDENTITY_UNVERIFIED')
        now = self.clock()
        lease = TempDatabaseLease(run_id=run_id, instance_id=instance_id,
                                  database_name=database_name, database_oid=database_oid,
                                  connection_role=connection_role,
                                  lease_id=_lease_id(run_id, database_name, database_oid),
                                  created_at=now,
                                  expires_at=now + self.budget.lease_seconds)
        self._active[lease.lease_id] = lease
        return lease

    def release(self, lease: TempDatabaseLease, *, active_connections: int,
                ownership_proof: str) -> ReclaimResult:
        if type(lease) is not TempDatabaseLease:
            return ReclaimResult('BLOCKED', 'LEASE_REQUIRED')
        current = self._active.get(lease.lease_id)
        if current is None:
            released = self._released.get(lease.lease_id)
            if released == lease and ownership_proof == 'owner:' + lease.run_id:
                return ReclaimResult('ALREADY_RELEASED')
            return ReclaimResult('BLOCKED', 'UNKNOWN_OR_FOREIGN_RESOURCE')
        if current != lease:
            return ReclaimResult('BLOCKED', 'LEASE_IDENTITY_CHANGED')
        if ownership_proof != 'owner:' + lease.run_id:
            return ReclaimResult('BLOCKED', 'OWNERSHIP_PROOF_REQUIRED')
        if type(active_connections) is not int or active_connections != 0:
            return ReclaimResult('BLOCKED', 'ACTIVE_CONNECTIONS_PRESENT')
        if self._expired(lease):
            return ReclaimResult('BLOCKED', 'LEASE_EXPIRED')
        if self.observe(lease.database_name) != (lease.instance_id, lease.database_name,
                                                 lease.database_oid):
            return ReclaimResult('BLOCKED', 'RESOURCE_IDENTITY_CHANGED')
        del self._active[lease.lease_id]
        self._released[lease.lease_id] = lease
        return ReclaimResult('RELEASED')
