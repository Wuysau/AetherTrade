"""Read durable program obligations without inventing quantities or authority.

The source namespace is distinct from legacy commands. No state label or owner-
authored audit row resolves a program write: a qualified resolution port does
not exist yet. Older ledgers may lack this additive table; corrupt present rows
must never disappear through a payload-state filter or collateral inner join.
"""
from dataclasses import dataclass, replace

from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_lifecycle import ApplicationWrite
from .models import MissingEvidence, account_key, scope_id


@dataclass(frozen=True, kw_only=True)
class ApplicationWriteObligation:
    original: ApplicationWrite
    current: ApplicationWrite
    collateral_group_id: str

    @property
    def identity(self):
        return 'PROGRAM_WRITE:' + self.original.service_namespace + ':' + scope_id(
            self.original.scope) + ':' + self.original.command_id

    @property
    def unresolved(self):
        # An ACK/terminal/abort label alone is not complete authoritative
        # reconciliation. A future qualified resolver must discharge it.
        return self.current.state != 'DURABLE_READY'


def _require(condition, reason):
    if not condition:
        raise MissingEvidence('PROGRAM_WRITE_' + reason)


def _decode(payload):
    try:
        return VersionedOrderCodec().decode('application-write', payload)
    except Exception as exc:
        raise MissingEvidence('PROGRAM_WRITE_SOURCE_INVALID') from exc


def application_write_obligations(tx, *, scope_only=False):
    """Read this collateral group's writes under the caller's existing mutex.

    First identities are decoded before resolving their current account binding.
    A missing binding or malformed first identity cannot be safely assigned to
    another group, so it fails closed. Valid unrelated groups are ignored; an
    older owner epoch is retained as an obligation rather than filtered out.
    """
    if tx.execute("SELECT to_regclass('ledger.order_writes_v2')").fetchone()[0] is None:
        return ()
    rows = tx.execute('SELECT scope_id,record_id,sequence,payload FROM ledger.order_writes_v2 '
                      'ORDER BY scope_id,record_id,sequence').fetchall()
    histories = {}
    for physical_scope, identifier, sequence, payload in rows:
        _require(isinstance(physical_scope, str) and isinstance(identifier, str)
            and type(sequence) is int and sequence > 0, 'HISTORY_INVALID')
        histories.setdefault((physical_scope, identifier), []).append((sequence, payload))
    bindings, programs, selected = {}, {}, {}
    codec = VersionedOrderCodec()
    for (physical_scope, identifier), history in histories.items():
        history.sort(key=lambda item: item[0])
        _require(history[0][0] == 1, 'ORIGINAL_REQUIRED')
        original = _decode(history[0][1])
        _require(original.command_id == identifier and scope_id(original.scope) == physical_scope
            and original.state == 'DURABLE_READY', 'ORIGINAL_IDENTITY_MISMATCH')
        if original.scope.environment != tx.scope.environment:
            continue
        key = account_key(original.scope)
        if key not in bindings:
            row = tx.execute('SELECT collateral_group_id FROM ledger.account_bindings '
                'WHERE environment=%s AND account_id=%s AND product=%s', key).fetchone()
            _require(row is not None and bool(row[0]), 'COLLATERAL_BINDING_REQUIRED')
            bindings[key] = row[0]
        if bindings[key] != tx.group_id:
            continue
        _require(tuple(s for s, _ in history) == tuple(range(1, len(history) + 1)), 'HISTORY_INVALID')
        unresolved_seen = False
        for _, payload in history[1:]:
            current = _decode(payload)
            _require(replace(current, state=original.state,
                durable_record_ref=original.durable_record_ref) == original, 'IMMUTABLE_IDENTITY_CHANGED')
            # A corrupted/imported READY label cannot erase an earlier send
            # boundary, irrespective of cosmetic durable_record_ref changes.
            # Ordinary0008 writes already prohibit this transition in SQL.
            _require(not (unresolved_seen and current.state == 'DURABLE_READY'), 'STATE_REGRESSION')
            unresolved_seen = unresolved_seen or current.state != 'DURABLE_READY'
        current = _decode(history[-1][1])
        program_key = (physical_scope, original.program_id)
        if program_key not in programs:
            row = tx.execute('SELECT payload FROM ledger.order_programs '
                'WHERE scope_id=%s AND record_id=%s', program_key).fetchone()
            _require(row is not None, 'PROGRAM_REQUIRED')
            try:
                programs[program_key] = codec.decode('order-program', row[0])
            except Exception as exc:
                raise MissingEvidence('PROGRAM_WRITE_PROGRAM_INVALID') from exc
        program = programs[program_key]
        _require(program.program_id == original.program_id and codec.hash('order-program', program)
            == original.canonical_program_hash, 'PROGRAM_HASH_MISMATCH')
        selected[(physical_scope, identifier)] = ApplicationWriteObligation(original=original,
            current=current, collateral_group_id=bindings[key])
    # Application children remain independent local writes. Native children
    # live only in observation tables and never enter this command reader.
    for (physical_scope, identifier), obligation in selected.items():
        write = obligation.original
        seen = {identifier}
        while write.origin == 'APPLICATION_GENERATED_CHILD':
            _require(write.parent_command_id not in seen, 'PARENT_LINK_INVALID')
            seen.add(write.parent_command_id)
            parent = selected.get((physical_scope, write.parent_command_id))
            _require(parent is not None and parent.original.program_id == write.program_id
                and parent.original.canonical_program_hash == write.canonical_program_hash, 'PARENT_LINK_REQUIRED')
            write = parent.original
    return tuple(obligation for obligation in selected.values()
        if not scope_only or obligation.original.scope == tx.scope)
