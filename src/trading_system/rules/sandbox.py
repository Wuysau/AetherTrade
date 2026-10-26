"""Offline Python research boundary. No execution-owner or credential adapters.

LinuxRunner requires a separately provisioned, reviewed, credential-free rootfs
containing /usr/bin/python3 and locked dependencies. It never mounts host /usr,
home, sockets or databases. Native qualification is a separate, unrun activity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import tempfile
import time
from threading import Event
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec


class SandboxDenied(ValueError):
    pass


def digest(value: str) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise SandboxDenied('INVALID_DIGEST')


def validate_artifact(artifact: t.RuleArtifact) -> None:
    if type(artifact) is not t.RuleArtifact:
        raise SandboxDenied('INVALID_ARTIFACT')
    digest(artifact.hash)
    digest(artifact.dependency_lock_hash)
    if (type(artifact.artifact_ref) is not str or not artifact.artifact_ref.strip()
            or len(artifact.artifact_ref) > 256):
        raise SandboxDenied('INVALID_ARTIFACT_REF')


def validate_limits(limits: t.SandboxLimits) -> None:
    if type(limits) is not t.SandboxLimits:
        raise SandboxDenied('INVALID_LIMIT')
    for value, maximum in ((limits.cpu_ms, 60000), (limits.memory_bytes, 512 * 1024**2),
                           (limits.output_bytes, 1024**2)):
        if type(value) is not int or not 0 < value <= maximum:
            raise SandboxDenied('INVALID_LIMIT')
    if limits.network != 'DENY' or limits.credentials != 'NONE':
        raise SandboxDenied('PRIVILEGED_CAPABILITY_DENIED')


@dataclass(frozen=True)
class ArtifactBytes:
    source: bytes
    dependency_lock: bytes


def validate_bundle(bundle: ArtifactBytes) -> None:
    if (type(bundle) is not ArtifactBytes or type(bundle.source) is not bytes
            or type(bundle.dependency_lock) is not bytes
            or not 0 < len(bundle.source) <= 1024**2
            or not 0 < len(bundle.dependency_lock) <= 1024**2):
        raise SandboxDenied('INVALID_ARTIFACT_BYTES')


class RuleSandbox:
    def __init__(self, *, load: Callable[[str], ArtifactBytes], runner=None):
        # Loader is a trusted content-store lookup, never a model-selected path.
        self._load, self._runner = load, runner

    def run(self, artifact: t.RuleArtifact, input: t.RuleInput,
            limits: t.SandboxLimits) -> t.RuleEvaluation:
        def reject(reason):
            return t.RuleEvaluation(proposal=None, action='REJECT', state=input.state, reasons=(reason,))
        try:
            validate_artifact(artifact)
            validate_limits(limits)
            if (input.snapshot.scope.environment != t.Environment.RESEARCH
                    or input.snapshot.ref.environment != t.Environment.RESEARCH
                    or input.snapshot.coverage.scope != input.snapshot.scope):
                raise SandboxDenied('RESEARCH_ONLY')
            state = input.state
            if (type(state) is not t.RuleState or type(state.revision) is not int
                    or state.revision < 0
                    or (state.previous_condition is not None and type(state.previous_condition) is not bool)
                    or (state.last_bar_id is not None and
                        (type(state.last_bar_id) is not str or len(state.last_bar_id) > 256))):
                raise SandboxDenied('INVALID_STATE')
            if state.last_triggered_at is not None:
                utc_timestamp(state.last_triggered_at)
            if self._runner is None:
                raise SandboxDenied('NATIVE_RUNNER_NOT_CONFIGURED')
            bundle = self._load(artifact.artifact_ref)
            validate_bundle(bundle)
            if (hashlib.sha256(bundle.source).hexdigest() != artifact.hash
                    or hashlib.sha256(bundle.dependency_lock).hexdigest() != artifact.dependency_lock_hash):
                raise SandboxDenied('ARTIFACT_INTEGRITY_FAILED')
            # Deliberate public projection: no account/scope/private refs/environment.
            packet = input.snapshot
            payload = canonical_json({'quote': domain_payload(packet.quote),
                'published_at': packet.published_at, 'state': domain_payload(input.state)})
            encoded = payload.encode('utf-8')
            if len(encoded) > 1024**2:
                raise SandboxDenied('INPUT_LIMIT')
            raw = self._runner.run(bundle, encoded, limits)
            if type(raw) is not bytes or len(raw) > limits.output_bytes:
                raise SandboxDenied('OUTPUT_LIMIT')
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise SandboxDenied('DUPLICATE_OUTPUT_KEY')
                    result[key] = value
                return result
            def invalid_constant(value):
                raise SandboxDenied('INVALID_OUTPUT_NUMBER')
            output = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
            if type(output) is not dict or set(output) != {'action', 'proposal'}:
                raise SandboxDenied('INVALID_OUTPUT')
            if output['action'] == 'HOLD' and output['proposal'] is None:
                return t.RuleEvaluation(proposal=None, action='HOLD', state=input.state, reasons=())
            if output['action'] != 'PROPOSAL' or type(output['proposal']) is not dict:
                raise SandboxDenied('INVALID_OUTPUT')
            proposal = ContractCodec().decode('decision-proposal', output['proposal'])
            # This untrusted proposal must still pass IP-09 binding and IP-06 risk.
            return t.RuleEvaluation(proposal=proposal, action='PROPOSAL', state=input.state,
                                    reasons=('UNTRUSTED_REQUIRES_NORMAL_RISK_INGRESS',))
        except SandboxDenied as exc:
            return reject(str(exc))
        except Exception:
            # Do not echo source/output/host paths or exception text.
            return reject('SANDBOX_FAILED')


def _seccomp_x86_64() -> bytes:
    """Classic BPF: kill wrong ABI; deny process creation and privileged syscalls.

    Network is also absent via an independent network namespace. Denying clone
    makes RLIMIT_AS aggregate for the single research process (including threads).
    x32 syscalls are rejected, not interpreted as native x86_64 syscall numbers.
    """
    rows = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E), (0x06, 0, 0, 0x80000000),
            (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000)]
    for number in (41, 53, 56, 57, 58, 101, 165, 166, 169, 175, 176, 246, 248,
                   249, 250, 272, 298, 304, 308, 310, 311, 313, 319, 321, 323, 425,
                   426, 427, 435):
        rows.extend(((0x15, 0, 1, number), (0x06, 0, 0, 0x50001)))
    rows.append((0x06, 0, 0, 0x7FFF0000))
    return b''.join(struct.pack('HBBI', *row) for row in rows)


class LinuxRunner:
    """One call per worker; cancel via the caller-owned Event.

    Provisioning/rootfs review is external to this module. No host fallback.
    A rootfs must have empty /job, /tmp, /proc and /dev mount points and a lock
    at /dependency.lock. It must be immutable to the worker identity.
    """
    def __init__(self, *, rootfs: Path, cancel: Event | None = None,
                 bwrap: str = '/usr/bin/bwrap', prlimit: str = '/usr/bin/prlimit'):
        self.rootfs = Path(rootfs).resolve()
        self.cancel = cancel if cancel is not None else Event()
        self.bwrap, self.prlimit = bwrap, prlimit

    def run(self, bundle: ArtifactBytes, payload: bytes, limits: t.SandboxLimits) -> bytes:
        validate_limits(limits)
        validate_bundle(bundle)
        if type(payload) is not bytes or len(payload) > 1024**2:
            raise SandboxDenied('INPUT_LIMIT')
        if platform.system() != 'Linux' or platform.machine() != 'x86_64':
            raise SandboxDenied('LINUX_X86_64_REQUIRED')
        if self.cancel.is_set():
            raise SandboxDenied('CANCELLED')
        if self.rootfs == Path('/') or not self.rootfs.is_dir():
            raise SandboxDenied('INVALID_ROOTFS')
        # These are mount destinations, not host paths supplied by rule code.
        # Reject symlinks so an incomplete image cannot redirect the mounts.
        for name in ('job', 'tmp', 'proc', 'dev'):
            target = self.rootfs / name
            if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
                raise SandboxDenied('INVALID_ROOTFS_MOUNTPOINT')
        lock = self.rootfs / 'dependency.lock'
        if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > 1024**2:
            raise SandboxDenied('INVALID_ROOTFS_LOCK')
        if lock.read_bytes() != bundle.dependency_lock:
            raise SandboxDenied('ROOTFS_DEPENDENCY_MISMATCH')
        with tempfile.TemporaryDirectory(prefix='research-') as directory:
            job = Path(directory)
            (job / 'rule.py').write_bytes(bundle.source)
            with tempfile.TemporaryFile() as policy, tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
                policy.write(_seccomp_x86_64())
                policy.seek(0)
                stdin.write(payload)
                stdin.seek(0)
                command = [self.prlimit, f'--as={limits.memory_bytes}',
                    f'--cpu={max(1, math.ceil(limits.cpu_ms / 1000))}',
                    f'--fsize={limits.output_bytes}', '--core=0', '--nofile=64', '--',
                    self.bwrap, '--unshare-all', '--die-with-parent', '--new-session',
                    '--cap-drop', 'ALL', '--clearenv', '--ro-bind', str(self.rootfs), '/',
                    '--proc', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
                    '--size', '1048576', '--tmpfs', '/tmp',
                    '--ro-bind', str(job), '/job', '--chdir', '/job',
                    '--seccomp', str(policy.fileno()), '/usr/bin/python3', '-I', '-B', '/job/rule.py']
                process = subprocess.Popen(command, stdin=stdin, stdout=stdout,
                    stderr=subprocess.DEVNULL, env={}, close_fds=True,
                    pass_fds=(policy.fileno(),), start_new_session=True)
                deadline = time.monotonic() + limits.cpu_ms / 1000
                try:
                    while process.poll() is None:
                        if self.cancel.is_set():
                            raise SandboxDenied('CANCELLED')
                        if time.monotonic() >= deadline:
                            raise SandboxDenied('TIME_LIMIT')
                        self.cancel.wait(0.01)
                    if self.cancel.is_set():
                        raise SandboxDenied('CANCELLED')
                    if process.returncode != 0:
                        raise SandboxDenied('WORKER_FAILED_OR_RESOURCE_LIMIT')
                    stdout.seek(0)
                    output = stdout.read(limits.output_bytes + 1)
                    if len(output) > limits.output_bytes:
                        raise SandboxDenied('OUTPUT_LIMIT')
                    return output
                finally:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait()
