"""IP-21 fixtures: offline doubles are not native or database qualification.

Native probes require an explicitly provisioned Linux rootfs. No credentials,
production services or real accounts are used, even in native probes.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import platform

import pytest

from trading_system.contracts import types as t
from trading_system.rules.sandbox import ArtifactBytes, LinuxRunner, RuleSandbox
from trading_system.rules.releases import OfflineReleaseRepository, ReleaseService
from unit.support_ip_20 import make_snapshot

NOW = '2026-10-23T00:00:00Z'
LATER = '2026-10-23T00:01:00Z'
LOCK = b'offline-python-stdlib-only\n'
HOLD = b'{"action":"HOLD","proposal":null}'


def bundle(source=b'print(\'{"action":"HOLD","proposal":null}\')', lock=LOCK):
    content = ArtifactBytes(source, lock)
    artifact = t.RuleArtifact(hash=hashlib.sha256(source).hexdigest(),
        artifact_ref='offline-artifact', dependency_lock_hash=hashlib.sha256(lock).hexdigest())
    return artifact, content


def limits(**changes):
    return replace(t.SandboxLimits(cpu_ms=3000, memory_bytes=128 * 1024**2,
        output_bytes=65536, network='DENY', credentials='NONE'), **changes)


def rule_input():
    return t.RuleInput(snapshot=make_snapshot(environment=t.Environment.RESEARCH),
        state=t.RuleState(previous_condition=None, last_triggered_at=None, last_bar_id=None, revision=0))


class OutputDouble:
    """Only a transport double for protocol validation, never isolation evidence."""
    def __init__(self, output=HOLD):
        self.output, self.calls = output, []

    def run(self, content, payload, budget):
        self.calls.append((content, payload, budget))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def offline_sandbox(output=HOLD):
    artifact, content = bundle()
    runner = OutputDouble(output)
    return RuleSandbox(load=lambda ref: content, runner=runner), runner, artifact


def evidence():
    return t.EvidenceRef(id='review', hash='a' * 64, source='offline-review',
        environment=t.Environment.RESEARCH, visible_at=NOW)


class ReleaseCase:
    def __init__(self):
        self.repository = OfflineReleaseRepository(environment=t.Environment.RESEARCH)
        self.permitted = False
        self.evidence_valid = True
        self.evidence_checks = []
        self.service = ReleaseService(repository=self.repository, environment=t.Environment.RESEARCH,
            now=lambda: NOW, evaluate=lambda change: (evidence(),),
            authorize=self.authorize, verify_evidence=self.verify_evidence)

    def authorize(self, candidate, approval):
        # Synthetic authenticated authority, scoped to this offline case. No
        # profitable metric, model string or candidate field grants permission.
        return (self.permitted and approval.actor.identity_id == 'synthetic-human'
            and approval.actor.policy_revision == 1 and approval.actor.ownership_epoch == 1
            and approval.actor.scopes == (rule_input().snapshot.scope,)
            and approval.authorization_id == 'synthetic-authorization')

    def verify_evidence(self, ref, change, purpose):
        self.evidence_checks.append((ref, change, purpose))
        return self.evidence_valid and ref == evidence() and purpose in ('REVIEW', 'EVALUATION')

    def change(self, source=b'first'):
        return t.StrategyChange(strategy_id='strategy', expected_revision=self.repository.revision('strategy'),
            artifact=bundle(source)[0], review_ref=evidence())

    def approval(self, candidate):
        scope = rule_input().snapshot.scope
        actor = t.ActorContext(identity_id='synthetic-human', environment=t.Environment.RESEARCH,
            deployment_id='offline-deployment', authorization_id='synthetic-authorization',
            policy_revision=1, ownership_epoch=1, scopes=(scope,))
        return t.ReleaseApproval(candidate_id=candidate.candidate_id,
            authorization_id=actor.authorization_id, actor=actor, approved_at=NOW)


@pytest.fixture
def release_case():
    return ReleaseCase()


class NativeCase:
    def __init__(self, rootfs, tmp_path):
        self.rootfs, self.tmp_path = rootfs, tmp_path
        self.lock = (rootfs / 'dependency.lock').read_bytes()

    def sandbox(self, source, cancel=None):
        artifact, content = bundle(source.encode(), self.lock)
        runner = LinuxRunner(rootfs=self.rootfs, cancel=cancel)
        return RuleSandbox(load=lambda ref: content, runner=runner), artifact

    def try_privileged_access(self):
        # Actual synthetic sentinel, outside rootfs; never open a real secret.
        sentinel = self.tmp_path / 'synthetic-host-secret'
        sentinel.write_text('IP21-SENTINEL', encoding='utf-8')
        # All host-side material is synthetic and lives under this test's temp
        # directory, never under a user's home or an actual service directory.
        paths = [str(sentinel)]
        for name in ('docker.sock', 'production-db', 'host-home-marker'):
            item = self.tmp_path / name
            item.write_bytes(b'IP21-SYNTHETIC-ONLY')
            paths.append(str(item))
        source = f'''
import json, os, socket
reads, calls, blocked = [], [], []
for path in {paths!r}:
    try:
        with open(path, 'rb') as stream:
            stream.read(1)
        reads.append(path)
    except OSError:
        blocked.append(path)
if os.environ.get('IP21_SYNTHETIC_HOST_SECRET'):
    reads.append('host-environment')
else:
    blocked.append('host-environment')
for family, address in [(socket.AF_INET, 'synthetic-signing-endpoint'), (socket.AF_UNIX, 'docker-socket')]:
    try:
        sock = socket.socket(family)
    except PermissionError:
        blocked.append(str(address))
    else:
        sock.close()
        raise AssertionError('socket creation must be denied before any service call')
print(json.dumps(dict(secret_reads=reads, signature_service_calls=calls, blocked_capabilities=blocked)))
'''
        _, content = bundle(source.encode(), self.lock)
        raw = LinuxRunner(rootfs=self.rootfs).run(content, b'{}', limits())
        return PrivilegeProbeResult(**json.loads(raw))


@dataclass(frozen=True)
class PrivilegeProbeResult:
    secret_reads: list[str]
    signature_service_calls: list[str]
    blocked_capabilities: list[str]


@pytest.fixture
def sandbox_case(tmp_path):
    location = os.environ.get('IP21_NATIVE_ROOTFS')
    if not location or platform.system() != 'Linux':
        pytest.skip('NOT_RUN: requires explicitly provisioned isolated Linux IP21_NATIVE_ROOTFS')
    rootfs = Path(location).resolve()
    if not rootfs.is_dir() or rootfs == Path('/'):
        pytest.fail('Invalid explicit native rootfs; no host fallback')
    return NativeCase(rootfs, tmp_path)
