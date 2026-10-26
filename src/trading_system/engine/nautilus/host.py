"""Independent offline process probe; native LiveNode startup stays disabled.

No dynamically selected module, executable, environment or native callback is
accepted. The fixed child has an empty environment and never imports Nautilus.
A local exclusive marker prevents two probes for one environment/account/product
in the same lock directory. This is not a cross-host fence or a recovery gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash


_PROBE = "import json,os;print(json.dumps({'pid':os.getpid(),'mode':'OFFLINE_PROCESS_PROBE'}))"


@dataclass(frozen=True, kw_only=True)
class ProcessObservation:
    """Child PID observation; build_digest is a caller label, not measured provenance."""
    supervisor_pid: int
    child_pid: int
    build_digest: str
    shard: t.ExecutionShard
    mode: str = 'OFFLINE_PROCESS_PROBE'


class NautilusHost:
    def __init__(self, *, offline_probe: bool = False, lock_directory: Path | None = None,
                 timeout_seconds: int = 10, unresolved: tuple[t.ReconciliationCase, ...] = ()):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError('bounded process timeout required')
        if type(offline_probe) is not bool:
            raise ValueError('explicit offline mode required')
        self._offline_probe = offline_probe
        self._directory = None if lock_directory is None else Path(lock_directory)
        self._timeout = timeout_seconds
        self._unresolved = unresolved
        self._run_lock = Lock()
        self.observation: ProcessObservation | None = None
        self.last_reason = 'NOT_RUN'

    @property
    def live_enabled(self) -> bool:
        return False

    @property
    def recovery_ready(self) -> bool:
        return False

    @staticmethod
    def _valid_build(build: t.QualifiedBuild) -> bool:
        # Metadata checks only; a successful offline probe cannot qualify a build.
        if (not isinstance(build, t.QualifiedBuild)
                or not isinstance(build.candidate, t.CandidateSpec)
                or not isinstance(build.candidate.build_environment, t.BuildEnvironment)
                or not isinstance(build.qualification_ref, t.EvidenceRef)):
            return False
        candidate, env = build.candidate, build.candidate.build_environment
        return bool(all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
                        for value in (candidate.review_ref, candidate.deployment_ref))
                    and all(isinstance(value, str) and re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', value) for value in
                            (candidate.source_digest, build.artifact_digest,
                             env.image_digest, env.dependency_digest, build.qualification_ref.hash))
                    and all(isinstance(value, str) and value.strip() for value in
                            (candidate.candidate_id, env.os, env.arch, env.python_abi,
                             build.qualification_ref.id)))

    @contextmanager
    def _claim(self, shard: t.ExecutionShard, instance_id: str):
        if self._directory is None:
            raise ValueError('explicit offline lock directory required')
        self._directory.mkdir(parents=True, exist_ok=True)
        key = canonical_hash({'environment': str(shard.environment),
                              'account': shard.venue_account_id, 'product': str(shard.product)})
        marker = self._directory / (key + '.lock')
        # No stale-PID or timeout deletion: crash leftovers require investigation.
        with marker.open('x', encoding='utf-8') as stream:
            stream.write(instance_id)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            yield
        finally:
            # Delete only this instance's marker, never another owner's claim.
            if marker.read_text(encoding='utf-8') != instance_id:
                raise ValueError('offline claim ownership changed')
            marker.unlink()

    def run(self, shard: t.ExecutionShard, build: t.QualifiedBuild) -> t.HostExit:
        instance_id = 'ip11:' + uuid4().hex
        if not self._run_lock.acquire(blocking=False):
            return t.HostExit(instance_id=instance_id, exit_code=2,
                              unresolved=self._unresolved, evidence=())
        try:
            self.observation = None
            code = 2
            if not isinstance(shard, t.ExecutionShard):
                self.last_reason = 'INVALID_SHARD'
            elif shard.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
                self.last_reason = 'OFFLINE_SCOPE_REQUIRED'
            elif not self._offline_probe:
                self.last_reason = 'NATIVE_BINDING_NOT_QUALIFIED'
            elif (not isinstance(shard.venue_account_id, str) or not shard.venue_account_id.strip()
                    or not isinstance(shard.node_id, str) or not shard.node_id.strip()
                    or shard.product not in tuple(t.Product)
                    or type(shard.execution_epoch) is not int or shard.execution_epoch < 1):
                self.last_reason = 'INVALID_SHARD'
            elif not self._valid_build(build):
                self.last_reason = 'BUILD_METADATA_INCOMPLETE'
            else:
                try:
                    with self._claim(shard, instance_id):
                        result = subprocess.run([sys.executable, '-I', '-S', '-c', _PROBE],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=self._timeout, env={}, cwd=str(self._directory.resolve()),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        output = json.loads(result.stdout)
                        if (result.returncode != 0 or not isinstance(output, dict)
                                or output.get('mode') != 'OFFLINE_PROCESS_PROBE'
                                or type(output.get('pid')) is not int or output['pid'] <= 0
                                or output['pid'] == os.getpid()):
                            raise ValueError('invalid child observation')
                        observation = ProcessObservation(supervisor_pid=os.getpid(),
                            child_pid=output['pid'], build_digest=build.artifact_digest, shard=shard)
                    # Publish only after the claim was released successfully.
                    self.observation = observation
                    self.last_reason = 'OFFLINE_PROBE_ONLY_NATIVE_NOT_RUN'
                    code = 0
                except FileExistsError:
                    self.last_reason = 'SHARD_ALREADY_CLAIMED_OR_STALE_MARKER'
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.last_reason = 'OFFLINE_PROCESS_PROBE_FAILED'
                    code = 3
            # No fabricated EvidenceRefs or READY claim from a process exit.
            return t.HostExit(instance_id=instance_id, exit_code=code,
                              unresolved=self._unresolved, evidence=())
        finally:
            self._run_lock.release()
