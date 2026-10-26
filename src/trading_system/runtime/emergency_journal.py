"""Restricted append/fsync evidence, never a second ledger or replay queue.

The authority reader is trusted owner composition, not a request parameter.
An uncertain write leaves a writer marker and poisons the journal: recovery
must investigate it, never truncate a torn tail or mint a replacement permit.
Windows files are offline probes; directory durability requires Linux evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import stat
from threading import RLock
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec
from trading_system.execution.dispatcher import clock_now


GENESIS = '0' * 64
BINARY = getattr(os, 'O_BINARY', 0)


class JournalDenied(ValueError):
    pass


@dataclass(frozen=True, kw_only=True)
class JournalAuthority:
    scope: t.ScopeKey
    request_hash: str
    expires_at: str
    deadline_monotonic_ns: int


@dataclass(frozen=True, kw_only=True)
class JournalEntry:
    sequence: int
    record_hash: str
    record_json: str

    @property
    def command(self):
        return ContractCodec().decode('execution-command', json.loads(self.record_json)['command'])


def sync_directory(directory: Path) -> None:
    if os.name == 'posix':
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def regular_file(fd: int) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise JournalDenied('REGULAR_PRIVATE_FILE_REQUIRED')
    if os.name == 'posix' and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
        raise JournalDenied('PRIVATE_OWNER_FILE_REQUIRED')


class EmergencyJournal:
    def __init__(self, path: Path, *, authorize: Callable | None = None, clock=clock_now):
        self.path = Path(path)
        self.marker = self.path.with_name(self.path.name + '.writer')
        self.authorize, self.clock = authorize, clock
        self._lock = RLock()
        self._poisoned = False

    def _read(self) -> tuple[JournalEntry, ...]:
        if not self.path.exists() and not self.path.is_symlink():
            return ()
        fd = os.open(self.path, os.O_RDONLY | BINARY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            regular_file(fd)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read()
        finally:
            os.close(fd)
        if data and not data.endswith(b'\n'):
            raise JournalDenied('TORN_JOURNAL_REQUIRES_RECOVERY')
        entries, previous = [], GENESIS
        commands, clients, records = set(), set(), set()
        for line in data.splitlines():
            try:
                frame = json.loads(line)
                body = frame['body']
                digest = canonical_hash(body)
                record = body['record']
                if (frame.keys() != {'body', 'hash'} or body.keys() != {'sequence', 'record'}
                        or digest != frame['hash'] or body['sequence'] != len(entries) + 1
                        or record['prior_record_hash'] != previous):
                    raise ValueError('hash chain mismatch')
                entry = JournalEntry(sequence=body['sequence'], record_hash=digest,
                                     record_json=canonical_json(record))
                command = entry.command
                if (command.state != 'MAY_HAVE_SENT'
                        or command.durable_record_ref != 'emergency:' + record['record_id']
                        or command.command_id in commands or command.venue_client_order_id in clients
                        or record['record_id'] in records
                        or canonical_hash(ContractCodec().encode(command)['canonical_payload']) != command.payload_hash):
                    raise ValueError('invalid durable identity')
                commands.add(command.command_id)
                clients.add(command.venue_client_order_id)
                records.add(record['record_id'])
                entries.append(entry)
                previous = digest
            except Exception as exc:
                raise JournalDenied('INVALID_JOURNAL_REQUIRES_RECOVERY') from exc
        return tuple(entries)

    def snapshot(self) -> tuple[JournalEntry, ...]:
        with self._lock:
            if self._poisoned or self.marker.exists() or self.marker.is_symlink():
                raise JournalDenied('UNCERTAIN_WRITER_REQUIRES_RECOVERY')
            return self._read()

    @property
    def head(self) -> str:
        entries = self.snapshot()
        return entries[-1].record_hash if entries else GENESIS

    def append_and_fsync(self, record: t.EmergencyRecord) -> t.DurablePermit:
        with self._lock:
            if self._poisoned:
                raise JournalDenied('UNCERTAIN_WRITER_REQUIRES_RECOVERY')
            if not isinstance(record, t.EmergencyRecord) or not record.record_id.strip():
                raise JournalDenied('TYPED_RECORD_REQUIRED')
            authority = self.authorize(record) if self.authorize else None
            now = self.clock()
            command = record.command
            if (not isinstance(authority, JournalAuthority)
                    or authority.request_hash != canonical_hash(domain_payload(record))
                    or authority.scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
                    or now.clock_health != 'HEALTHY'
                    or type(authority.deadline_monotonic_ns) is not int
                    or type(now.monotonic_ns) is not int or now.monotonic_ns < 0
                    or now.monotonic_ns >= authority.deadline_monotonic_ns
                    or utc_timestamp(now.utc_now) >= utc_timestamp(authority.expires_at)
                    or command.state != 'DURABLE_READY'
                    or command.operation not in ('CLOSE', 'CANCEL', 'SET_PROTECTION')
                    or (command.environment, command.venue_account_id, command.product, command.instrument_uid)
                    != (authority.scope.environment, authority.scope.venue_account_id,
                        authority.scope.product, authority.scope.instrument_uid)):
                raise JournalDenied('CURRENT_RESTRICTED_OWNER_AUTHORITY_REQUIRED')
            # The parent is provisioned by trusted composition, never a credential path.
            if not self.path.parent.is_dir() or self.path.is_symlink() or self.marker.is_symlink():
                raise JournalDenied('PROVISIONED_JOURNAL_DIRECTORY_REQUIRED')
            marker_fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY, 0o600)
            success = False
            try:
                os.fsync(marker_fd)
                sync_directory(self.path.parent)
                entries = self._read()
                previous = entries[-1].record_hash if entries else GENESIS
                if record.prior_record_hash != previous:
                    raise JournalDenied('STALE_JOURNAL_HEAD')
                if any(e.command.command_id == command.command_id
                       or e.command.venue_client_order_id == command.venue_client_order_id
                       or json.loads(e.record_json)['record_id'] == record.record_id for e in entries):
                    raise JournalDenied('QUERY_ORIGINAL_COMMAND_NO_REPLAY')
                durable = replace(command, state='MAY_HAVE_SENT', durable_record_ref='emergency:' + record.record_id)
                saved = replace(record, command=durable)
                body = {'sequence': len(entries) + 1, 'record': domain_payload(saved)}
                frame = (canonical_json({'body': body, 'hash': canonical_hash(body)}) + '\n').encode('utf-8')
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | BINARY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    regular_file(fd)
                    remaining = memoryview(frame)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written <= 0:
                            raise OSError('short journal write')
                        remaining = remaining[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                sync_directory(self.path.parent)
                success = True
            finally:
                os.close(marker_fd)
                if success:
                    try:
                        self.marker.unlink()
                        sync_directory(self.path.parent)
                    except Exception:
                        self._poisoned = True
                        raise
                else:
                    self._poisoned = True
            return t.DurablePermit(durable_record_ref=durable.durable_record_ref,
                command_id=command.command_id, payload_hash=command.payload_hash, scope=authority.scope,
                ownership_epoch=command.ownership_epoch, execution_epoch=command.execution_epoch,
                policy_revision=command.policy_revision, expires_at=authority.expires_at,
                deadline_monotonic_ns=authority.deadline_monotonic_ns)
