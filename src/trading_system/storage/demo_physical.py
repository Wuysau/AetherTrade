"""Fail-closed physical-account ownership; not venue or PostgreSQL qualification.

The transaction-scoped lock connection stays checked out until close. An
orderly, fully evidenced CLEAN transition can produce a one-use handoff only
after commit acknowledgement; the capability is bound to the same service and
process. Restart, loss or uncertain completion remains blocked. Lock loss cannot
stop an already-open socket or in-flight HTTP. PostgreSQL and venue qualification
remain outstanding; the deployment execution gate must remain closed.
"""
import asyncio
import os
from threading import RLock, get_ident
from sqlalchemy import Column, Integer, JSON, String, Table, insert, select, update
from trading_system.engine.binance_demo.contracts import DemoError


_HANDOFF_ISSUER = object()


class _Handoff:
    """Non-serializable one-shot capability bound to one service authority."""
    __slots__ = ('__authority', '__pid', '__evidence', '__used')

    def __init__(self, authority, evidence, *, _issuer=None):
        if _issuer is not _HANDOFF_ISSUER or authority is None:
            raise DemoError('DEMO_HANDOFF_CAPABILITY_REQUIRED')
        self.__authority, self.__pid, self.__evidence, self.__used = authority, os.getpid(), dict(evidence), False

    def consume(self, authority, evidence):
        if self.__used or authority is not self.__authority or os.getpid() != self.__pid or evidence != self.__evidence:
            raise DemoError('DEMO_PHYSICAL_RECOVERY_REQUIRED')
        self.__used = True
        return dict(self.__evidence)

    def __reduce__(self):
        raise TypeError('Physical handoff capability cannot be serialized')


def define_physical_owners(metadata):
    return Table('demo_physical_owners', metadata,
        Column('physical_key', String(64), primary_key=True),
        Column('identity', JSON, nullable=False),
        Column('account_key', String(64), nullable=False),
        Column('generation', Integer, nullable=False),
        Column('state', String(16), nullable=False),
        Column('clean_evidence', JSON))


def _reserve_new_dirty(engine, table, accounts, scope, *, authority=None, handoff=None):
    """Create generation one or consume an acknowledged in-process handoff."""
    identity = {'environment':scope.environment, 'venue':scope.venue, 'product':scope.product, 'account_id':scope.account_id}
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.exec_driver_sql('SET LOCAL synchronous_commit = on')
        row = conn.execute(select(table).where(table.c.physical_key == scope.physical_account_key).with_for_update()).mappings().first()
        matching = []
        for old in conn.execute(select(accounts.c.account_key, accounts.c.identity)).mappings():
            old_identity = old['identity']
            if not isinstance(old_identity, dict): raise DemoError('DEMO_LEGACY_PHYSICAL_MAPPING_REQUIRED')
            keys = ('environment','venue','product','account_id')
            if any(key not in old_identity for key in keys): raise DemoError('DEMO_LEGACY_PHYSICAL_MAPPING_REQUIRED')
            if all(old_identity[key] == identity[key] for key in keys): matching.append(old['account_key'])
        if row is None:
            if handoff is not None or matching: raise DemoError('DEMO_LEGACY_PHYSICAL_MAPPING_REQUIRED')
            conn.execute(insert(table).values(physical_key=scope.physical_account_key, identity=identity,
                account_key=scope.account_key, generation=1, state='DIRTY', clean_evidence=None))
            return 1
        if (type(handoff) is not _Handoff or authority is None or row['state'] != 'CLEAN' or
                row['account_key'] != scope.account_key or row['identity'] != identity or matching != [scope.account_key]):
            raise DemoError('DEMO_PHYSICAL_RECOVERY_REQUIRED')
        evidence = handoff.consume(authority, row['clean_evidence'])
        if (evidence.get('physical_key') != scope.physical_account_key or evidence.get('account_key') != scope.account_key or
                evidence.get('generation') != row['generation']):
            raise DemoError('DEMO_PHYSICAL_RECOVERY_REQUIRED')
        generation = row['generation'] + 1
        conn.execute(update(table).where(table.c.physical_key == scope.physical_account_key,
            table.c.state == 'CLEAN', table.c.generation == row['generation']).values(state='DIRTY', generation=generation, clean_evidence=None))
        return generation


class PhysicalOwner:
    """A private PG transaction lock, pinned until rollback."""
    def __init__(self, connection, scope, key, backend_pid, generation, *, authority=None):
        self.__connection, self.__scope, self.__key = connection, scope, key
        self.__backend_pid, self.__generation = backend_pid, generation
        self.__authority = authority
        self.__stream = None
        self.__closed = False
        self.__mutex = RLock()

    @classmethod
    def acquire(cls, engine, table, accounts, scope, *, authority=None, handoff=None):
        if engine.dialect.name != 'postgresql':
            raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        # Negative keys are separate from the current positive epoch send key.
        key = -1 - int(scope.physical_account_key[:15], 16)
        conn = engine.connect()
        try:
            locked = conn.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)', (key,)).scalar_one()
            if not locked:
                raise DemoError('DEMO_PHYSICAL_OWNER_BUSY')
            pid = conn.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
            owner = cls(conn, scope, key, pid, 1, authority=authority)
            owner.validate(scope)
            owner.__generation = _reserve_new_dirty(engine, table, accounts, scope, authority=authority, handoff=handoff)
            owner.validate(scope)
            return owner
        except BaseException:
            # Never touch the durable DIRTY row on error.
            try:
                conn.rollback()
            except Exception:
                try: conn.invalidate()
                except Exception: pass
            conn.close()
            raise

    @property
    def generation(self):
        return self.__generation

    def validate(self, scope):
        with self.__mutex:
            return self.__validate(scope)

    def __validate(self, scope):
        if self.__closed or scope.physical_account_key != self.__scope.physical_account_key or scope.account_key != self.__scope.account_key:
            raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
        conn = self.__connection
        try:
            if conn.invalidated or not conn.in_transaction():
                raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
            if conn.exec_driver_sql('SELECT pg_backend_pid()').scalar_one() != self.__backend_pid:
                raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
            unsigned = self.__key & ((1 << 64) - 1)
            held = conn.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted AND objsubid = 1 AND classid::bigint = %s AND objid::bigint = %s)", (unsigned >> 32, unsigned & 0xffffffff)).scalar_one()
            if not held:
                raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
        except DemoError:
            raise
        except Exception:
            raise DemoError('DEMO_PHYSICAL_OWNER_LOST') from None

    def register_stream(self, stream, scope):
        with self.__mutex:
            self.validate(scope)
            if self.__stream is not None and self.__stream is not stream:
                raise DemoError('DEMO_PHYSICAL_STREAM_ALREADY_BOUND')
            self.__stream = stream

    def commit_clean(self, scope, receipt, *, close_proof, validate_sender, now):
        """Seal a quiesced generation using the same lock-owning transaction.

        Returns an in-memory handoff only after commit and connection close both
        return successfully. An uncertain outcome never returns that handoff.
        The caller must have closed/joined the stream and all workers first.
        """
        from sqlalchemy import update
        from trading_system.engine.binance_demo.contracts import digest
        from trading_system.storage.demo import accounts, outbox, physical_owners, final_activity_digest
        with self.__mutex:
            self.validate(scope); validate_sender()
            from trading_system.engine.binance_demo.stream import _ClosedStreamProof
            if (self.__authority is None or type(close_proof) is not _ClosedStreamProof or
                    close_proof.stream is not self.__stream or self.__stream is None or
                    self.__stream.closed_proof is not close_proof or not isinstance(receipt, dict) or
                    receipt.get('stream_run_nonce') != close_proof.run_nonce):
                raise DemoError('DEMO_STREAM_CLOSE_PROOF_REQUIRED')
            close_proof.consume(self, scope, self.generation, receipt.get('credential_revision'), receipt.get('subscription_id'))
            conn = self.__connection
            try:
                if conn.dialect.name == 'postgresql': conn.exec_driver_sql('SET LOCAL synchronous_commit = on')
                account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key).with_for_update()).mappings().one()
                owner = conn.execute(select(physical_owners).where(physical_owners.c.physical_key == scope.physical_account_key).with_for_update()).mappings().one()
                checked_at = receipt.get('checked_at') if isinstance(receipt, dict) else None
                if (type(now) is not int or type(checked_at) is not int or not 0 <= now - checked_at <= 30 or
                        owner['state'] != 'DIRTY' or owner['account_key'] != scope.account_key or owner['generation'] != self.generation or
                        receipt.get('physical_key') != scope.physical_account_key or receipt.get('account_key') != scope.account_key or
                        receipt.get('physical_generation') != self.generation or receipt.get('credential_revision') != account['credential_revision'] or
                        account['state'] != 'RECOVERY_REQUIRED' or account['frozen'] or
                        receipt.get('qualification_hash') != digest(account.get('qualification') or {})):
                    raise DemoError('DEMO_FINAL_RECONCILIATION_REQUIRED')
                nonce = receipt.get('nonce')
                if not isinstance(nonce, str) or len(nonce) != 32:
                    raise DemoError('DEMO_FINAL_RECONCILIATION_REQUIRED')
                event_id = digest([scope.account_key, 'FINAL_RECONCILIATION', nonce])
                event = conn.execute(select(outbox).where(outbox.c.event_id == event_id)).mappings().first()
                if event is None or event['kind'] != 'FINAL_RECONCILIATION' or event['payload'] != receipt:
                    raise DemoError('DEMO_FINAL_RECONCILIATION_REQUIRED')
                if final_activity_digest(conn, scope) != receipt.get('activity_hash'):
                    raise DemoError('DEMO_FINAL_ACTIVITY_CHANGED')
                self.validate(scope); validate_sender()
                evidence = {'receipt_hash':digest(receipt), 'physical_key':scope.physical_account_key,
                            'account_key':scope.account_key, 'generation':self.generation,
                            'credential_revision':account['credential_revision']}
                conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(
                    state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
                conn.execute(update(physical_owners).where(physical_owners.c.physical_key == scope.physical_account_key,
                    physical_owners.c.state == 'DIRTY', physical_owners.c.generation == self.generation).values(
                    state='CLEAN', clean_evidence=evidence))
                self.validate(scope); validate_sender()
                self.__closed = True
                conn.commit()  # Also releases the physical transaction lock.
                conn.close()
                return _Handoff(self.__authority, evidence, _issuer=_HANDOFF_ISSUER)
            except BaseException:
                self.__closed = True
                try: conn.rollback()
                except Exception:
                    try: conn.invalidate()
                    except Exception: pass
                try: conn.close()
                except Exception: pass
                raise

    def close(self):
        with self.__mutex:
            return self.__close()

    def __close(self):
        if self.__closed: return
        self.__closed = True
        try:
            self.__connection.rollback()
        except Exception:
            try: self.__connection.invalidate()
            except Exception: pass
            raise DemoError('DEMO_PHYSICAL_OWNER_LOST') from None
        finally:
            self.__connection.close()


def _execution_context():
    try: task = asyncio.current_task()
    except RuntimeError: task = None
    return get_ident(), task


class PinnedSenderLock:
    """Epoch sender transaction lock held on a private connection."""
    def __init__(self, connection, key, pid):
        self.__connection, self.__key, self.__pid = connection, key, pid
        self.__context = _execution_context()
        self.__closed = False

    @classmethod
    def acquire(cls, engine, account_key):
        if engine.dialect.name != 'postgresql':
            raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        key = int(account_key[:15], 16)
        conn = engine.connect(); locked = False
        try:
            locked = bool(conn.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)', (key,)).scalar_one())
            if not locked: raise DemoError('DEMO_OWNER_BUSY')
            pid = conn.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
            guard = cls(conn, key, pid); guard.validate()
            return guard
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                try: conn.invalidate()
                except Exception: pass
            conn.close()
            raise

    def validate(self):
        conn = self.__connection
        try:
            if self.__closed or _execution_context() != self.__context or conn.invalidated or not conn.in_transaction(): raise DemoError('DEMO_OWNER_LOST')
            if conn.exec_driver_sql('SELECT pg_backend_pid()').scalar_one() != self.__pid: raise DemoError('DEMO_OWNER_LOST')
            unsigned = self.__key & ((1 << 64) - 1)
            held = conn.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted AND objsubid = 1 AND classid::bigint = %s AND objid::bigint = %s)", (unsigned >> 32, unsigned & 0xffffffff)).scalar_one()
            if not held: raise DemoError('DEMO_OWNER_LOST')
        except DemoError: raise
        except Exception: raise DemoError('DEMO_OWNER_LOST') from None

    def close(self):
        if self.__closed: return
        self.__closed = True
        try:
            self.__connection.rollback()
        except Exception:
            try: self.__connection.invalidate()
            except Exception: pass
            raise DemoError('DEMO_OWNER_LOST') from None
        finally:
            self.__connection.close()
