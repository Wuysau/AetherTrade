"""Offline ownership ledger; the five-part ScopeKey has one PortfolioBook.

No credentials, transport, permit issuance or lock resolution. Host composition
must use this gate in ingress AND under the execution-owner guard before send.
Eligibility here never replaces the dispatcher or current risk/quantity checks.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.storage.events import EventStore
from trading_system.storage.models import SequenceConflict, decode, encode, scope_id


@dataclass(frozen=True)
class _HashInput:
    value: object


def digest(value):
    return canonical_hash(domain_payload(_HashInput(value)))


class OwnerCommitUnavailable(RuntimeError):
    """Reviewed database CAS capability has not been supplied by the host."""


class PostgresOwnerCAS:
    """Opt-in controller capability; never elevates or opens another connection.

    A separately trusted archive verifier must first issue a short-lived,
    exact-payload authority through 0005's verifier-only SQL function. The local
    selector returns its identity, which is a reference, never a bearer secret.
    The current connection must already be the separately assigned controller.
    SQL rechecks expiry, revocation and ledger revisions under the group/owner
    guard and requires both projections in this transaction at commit time.
    Missing composition/authority denies; ordinary ledger_owner cannot use it.
    """
    def __init__(self, *, read_authority=None):
        self.read_authority = read_authority

    def __call__(self, tx, old, new):
        if self.read_authority is None:
            raise OwnerCommitUnavailable('TRUSTED_OWNER_AUTHORITY_REQUIRED')
        authority_id = self.read_authority(tx, old, new)
        if not isinstance(authority_id, str) or not authority_id.strip():
            raise OwnerCommitUnavailable('TRUSTED_OWNER_AUTHORITY_REQUIRED')
        # OwnershipService increments the projection sequence after this call.
        # The deferred SQL guard requires that exact final record, not a second
        # independently committed projection or a permissive active-row UPDATE.
        final = replace(new, sequence=old.sequence + 1)
        tx.execute('SELECT ledger.commit_owner_transfer(%s,%s,%s::jsonb,%s::jsonb,%s)',
            (authority_id, scope_id(tx.scope), encode(old), encode(final), tx.transaction_id))


def offline(scope):
    if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
        raise ValueError('ENVIRONMENT_NOT_ENABLED')


@dataclass(frozen=True, kw_only=True)
class OwnerRecord:
    position: t.ManagedPosition
    policy_revision: int
    sequence: int
    mode: str = 'ACTIVE'
    transfer_id: str | None = None
    # Attribution conveys no management right, including exit rights.
    contributors: tuple[str, ...] = ()
    protection_responsibility: str = 'SYSTEM_PROTECTION'
    intervention_ids: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OwnershipVerdict:
    allowed: bool
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class TransferRecord:
    projection: t.TransferProjection
    context: t.ControlContext
    request_hash: str
    sequence: int
    accepted_exit_plan_id: str | None = None


class RegistryTx:
    """Adapter over IP-03's collateral mutex and append-only repositories."""
    def __init__(self, tx, owner_commit=None):
        self.tx = tx
        self.scope = tx.scope
        self.owner_commit = owner_commit

    def get(self, table, key, cls):
        return self.tx._latest(table, key, cls)

    def append(self, table, key, value, expected):
        return self.tx._append(table, key, value, expected)

    def global_get(self, table, key, cls):
        # Names are fixed by this module's callers, never caller request data.
        if table not in {'ownership_transfers', 'audit_events'}:
            raise ValueError('UNSUPPORTED_TABLE')
        rows = self.tx.execute(f'SELECT DISTINCT ON (scope_id) payload FROM ledger.{table} '
            'WHERE record_id=%s ORDER BY scope_id,sequence DESC', (key,)).fetchall()
        if len(rows) > 1:
            raise ValueError('AMBIGUOUS_GLOBAL_ID')
        return decode(cls, rows[0][0]) if rows else None

    def serialize(self, key):
        # Cross-scope idempotency/transfer identity; hash collisions only serialize.
        self.tx.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))

    def owner(self):
        return self.get('audit_events', 'ip16:owner', OwnerRecord)

    def active_owner(self):
        s = self.scope
        return self.tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision '
            'FROM ledger.active_owners WHERE environment=%s AND account_id=%s AND product=%s '
            'AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
            (str(s.environment), s.venue_account_id, str(s.product), s.instrument_uid, s.venue_leg)).fetchone()

    def commit_owner(self, old, new):
        """Only a reviewed host CAS path may mutate the immutable active-owner row.

        Ordinary IP-03 roles/trigger deny that write. There is no fallback UPDATE,
        elevated connection or trigger bypass. The opt-in 0005 capability runs
        in THIS transaction, checks old owner / epoch / policy and serializes
        in-flight sends. It may not commit independently.
        """
        if self.owner_commit is None:
            raise OwnerCommitUnavailable('REVIEWED_OWNER_CAS_REQUIRED')
        p, q = old.position, new.position
        if self.active_owner() != (p.owner_strategy_id, p.ownership_epoch, old.policy_revision):
            raise SequenceConflict('STALE_OWNER_EPOCH')
        try:
            self.owner_commit(self.tx, old, new)
            if self.active_owner() != (q.owner_strategy_id, q.ownership_epoch, new.policy_revision):
                raise SequenceConflict('OWNER_CAS_NOT_APPLIED')
        except Exception as exc:
            # Once a callback starts, it may already have written. In particular,
            # its OwnerCommitUnavailable must not become a committable BLOCKED
            # receipt with an old projection and a changed active-owner row.
            self.tx.failed = True
            raise RuntimeError('OWNER_CAS_FAILED') from exc

    def save_owner(self, old, new):
        if (new.position.scope != self.scope or self.owner() != old
                or new.sequence != (old.sequence if old else 0) + 1):
            raise SequenceConflict('OWNER_SEQUENCE_CONFLICT')
        self.append('audit_events', 'ip16:owner', new, old.sequence if old else 0)

    def raise_lock(self, lock, evidence):
        old = self.get('risk_locks', lock.lock_id, t.RiskLock)
        if old is not None:
            return old  # Never reopen or silently resolve an existing cause.
        self.tx.risk_locks.append(lock, 0)
        EventStore().append(self.tx, t.DomainEvent(event_id=lock.lock_id + ':raised',
            scope=self.scope, aggregate_id='risk-lock:' + lock.lock_id, revision=1,
            occurred_at=lock.raised_at, evidence=(evidence,), payload=lock), 0)
        return lock

    def locks(self):
        return self.tx.risk_locks.active(self.scope)


class OwnershipRegistry:
    def __init__(self, uow, *, owner_commit=None):
        self.uow, self.owner_commit = uow, owner_commit

    @contextmanager
    def transaction(self, scope):
        offline(scope)
        with self.uow.transaction(scope) as tx:
            yield RegistryTx(tx, self.owner_commit)

    def locate(self, transfer_id):
        # Read only. advance re-reads under the collateral mutex before mutation.
        with self.uow.connection_factory() as conn:
            rows = conn.execute('SELECT DISTINCT ON (scope_id) payload FROM ledger.ownership_transfers '
                'WHERE record_id=%s ORDER BY scope_id,sequence DESC',
                ('ip16:transfer:' + transfer_id,)).fetchall()
        if len(rows) != 1:
            raise LookupError('TRANSFER_NOT_FOUND_OR_AMBIGUOUS')
        return decode(TransferRecord, rows[0][0]).projection.request.scope

    def register(self, position, policy_revision, *, contributors=()):
        """Bind an already provisioned active-owner row, never claim an external leg.

        position must be the host's reconciled ledger record. This is a trusted
        composition API, not a control endpoint accepting a client position DTO.
        """
        if (not position.owner_book_id or not position.owner_strategy_id
                or type(position.ownership_epoch) is not int or position.ownership_epoch < 1
                or type(policy_revision) is not int or policy_revision < 1):
            raise ValueError('MANAGED_BOOK_REQUIRED')
        with self.transaction(position.scope) as tx:
            if tx.active_owner() != (position.owner_strategy_id, position.ownership_epoch, policy_revision):
                raise ValueError('EXISTING_OWNER_REQUIRED')
            old = tx.owner()
            if old:
                if (old.position != position or old.policy_revision != policy_revision
                        or old.contributors != tuple(contributors)):
                    raise ValueError('OWNERSHIP_CONFLICT')
                return old
            record = OwnerRecord(position=position, policy_revision=policy_revision,
                                 sequence=1, contributors=tuple(contributors))
            tx.save_owner(None, record)
            return record

    def read(self, scope):
        with self.transaction(scope) as tx:
            return tx.owner()

    @staticmethod
    def check_locked(tx, owner_book_id, owner_strategy_id, ownership_epoch, action):
        """Local eligibility only; PROTECT is restricted to the host's system path.

        No boolean supplied by an AI/client can turn an ordinary action into
        PROTECT. Every exit/protection still requires separate reduce-only proof.
        """
        record = tx.owner()
        reason = None
        if record is None:
            reason = 'EXTERNAL_NOT_OWNED'
        elif type(ownership_epoch) is not int or ownership_epoch != record.position.ownership_epoch:
            reason = 'STALE_OWNER_EPOCH'
        elif (owner_book_id, owner_strategy_id) != (record.position.owner_book_id,
                                                  record.position.owner_strategy_id):
            reason = 'OWNERSHIP_CONFLICT'
        elif tx.active_owner() != (owner_strategy_id, ownership_epoch, record.policy_revision):
            reason = 'OWNER_LEDGER_MISMATCH'
        elif action not in {'NEW_RISK', 'DECISION', 'EXIT', 'PROTECT'}:
            reason = 'UNKNOWN_ACTION'
        elif action == 'PROTECT':
            pass  # Responsibility survives freezes; this grants no write permit.
        elif record.mode in {'TRANSFER', 'TRANSFER_BLOCKED'}:
            reason = 'TRANSFER_FROZEN'
        elif action in {'NEW_RISK', 'DECISION'}:
            if record.intervention_ids:
                reason = 'MANUAL_INTERVENTION'
            elif record.mode in {'DRAINING', 'EXITING'}:
                reason = 'OWNER_DRAINING'
            elif tx.locks():
                reason = 'ACTIVE_RISK_LOCKS'
        return OwnershipVerdict(allowed=reason is None, reason=reason)

    def check(self, scope, owner_book_id, owner_strategy_id, ownership_epoch, action):
        with self.transaction(scope) as tx:
            return self.check_locked(tx, owner_book_id, owner_strategy_id, ownership_epoch, action)
