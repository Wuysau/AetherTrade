"""Read-only health, bounded latency measurements and deployment preflight.

None of these observations authorizes trading or clears a durable risk lock.
Deployment preflight deliberately cannot certify native/platform qualification.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class HealthPublisher:
    """Poll independently of decision ticks; readers supply authoritative state.

    Missing/stale process or risk state makes this projection fail closed. The
    result is display data, never an execution permit or a replacement risk gate.
    """
    def __init__(self, process_ids, *, read_process, read_permission=None,
                 clock=utc_now, maximum_age_seconds=1):
        self.process_ids = tuple(process_ids)
        if (not self.process_ids or len(set(self.process_ids)) != len(self.process_ids)
                or any(not isinstance(p, str) or not p for p in self.process_ids)
                or type(maximum_age_seconds) is not int or maximum_age_seconds <= 0):
            raise ValueError('unique processes and positive freshness bound required')
        self.read_process, self.read_permission = read_process, read_permission
        self.clock, self.maximum_age_seconds = clock, maximum_age_seconds

    def snapshot(self) -> t.SystemHealth:
        processes, missing = [], []
        try:
            now = utc_timestamp(self.clock())
        except Exception:
            return t.SystemHealth(processes=(), risk_permission=t.RiskPermission.RECOVERY_REQUIRED,
                                  missing=('CLOCK_UNAVAILABLE',))
        for process_id in self.process_ids:
            try:
                health = self.read_process(process_id)
                if (not isinstance(health, t.ProcessHealth) or health.process_id != process_id
                        or not isinstance(health.state, t.ProcessState)
                        or not isinstance(health.faults, tuple)
                        or any(not isinstance(f, str) or not f for f in health.faults)):
                    raise ValueError('invalid process observation')
                age = (now - utc_timestamp(health.observed_at)).total_seconds()
                if not 0 <= age <= self.maximum_age_seconds:
                    missing.append('STALE_PROCESS:' + process_id)
                if health.state != t.ProcessState.READY or health.faults:
                    missing.append('UNHEALTHY_PROCESS:' + process_id)
                processes.append(health)
            except Exception:
                missing.append('PROCESS_UNAVAILABLE:' + process_id)
        try:
            permission = self.read_permission() if self.read_permission else None
            if not isinstance(permission, t.RiskPermission):
                raise ValueError('authoritative permission missing')
        except Exception:
            permission = t.RiskPermission.RECOVERY_REQUIRED
            missing.append('RISK_PERMISSION_UNAVAILABLE')
        if missing:
            permission = t.RiskPermission.RECOVERY_REQUIRED
        return t.SystemHealth(processes=tuple(processes), risk_permission=permission,
                              missing=tuple(missing))


REFERENCE_LOAD = (50, 5, 10)  # instruments, decision strategies, managed positions
TARGETS = {
    'fault_to_no_new_risk': (99, 1_000_000_000),
    'fill_to_protection_start': (99, 250_000_000),
    'control_to_durable_queue': (95, 1_000_000_000),
    'pretrade_complete_snapshot': (95, 100_000_000),
}


@dataclass(frozen=True)
class LatencyReport:
    metric: str
    samples: int
    total_observations: int
    p95_ns: int | None
    p99_ns: int | None
    target_percentile: int
    target_ns: int
    load: tuple[int, int, int]
    source: str
    # Measurement presence does not imply acceptance or representative sampling.
    acceptance: str = 'NOT_RUN'


class LatencyMetrics:
    """Nearest-rank percentiles over a bounded window of observed durations.

    Caller records the actual endpoints named by TARGETS using a monotonic clock;
    no timing values are generated from targets. Source describes the workload.
    """
    def __init__(self, *, load=REFERENCE_LOAD, source, capacity=10000):
        if (len(load) != 3 or any(type(v) is not int or v < 0 for v in load)
                or type(capacity) is not int or capacity < 1 or not source):
            raise ValueError('measurement metadata and positive capacity required')
        self.load, self.source = tuple(load), source
        self._samples = {name: deque(maxlen=capacity) for name in TARGETS}
        self._totals = dict.fromkeys(TARGETS, 0)
        self._lock = RLock()

    def observe(self, metric, *, started_ns, finished_ns):
        if (metric not in TARGETS or type(started_ns) is not int or type(finished_ns) is not int
                or started_ns < 0 or finished_ns < started_ns):
            raise ValueError('known metric and nondecreasing monotonic timestamps required')
        with self._lock:
            self._samples[metric].append(finished_ns - started_ns)
            self._totals[metric] += 1

    def report(self, metric) -> LatencyReport:
        with self._lock:
            values = sorted(self._samples[metric])
            total = self._totals[metric]
        def percentile(p):
            return values[(len(values) * p + 99) // 100 - 1] if values else None
        p, target = TARGETS[metric]
        return LatencyReport(metric, len(values), total, percentile(95), percentile(99),
                             p, target, self.load, self.source)


# This offline topology has no public ports, credentials, production bindings or
# reloadable application entrypoints. Host-supplied overrides need a new review.
SERVICE_POLICY = {
    'execution-owner': ('10001:10001', ('owner', 'ledger', 'cache'), ('owner-state',)),
    'decision-worker': ('10002:10002', ('research',), ()),
    'control-api': ('10003:10003', ('control',), ()),
    'notifier': ('10004:10004', ('observation',), ('notification-state',)),
    'research-sandbox': ('10005:10005', (), ()),
    'ledger': ('10006:10006', ('ledger',), ('ledger-data',)),
    'kernel-cache': ('10007:10007', ('cache',), ('cache-data',)),
}
OFFLINE_COMMAND = ['python', '-c', "raise SystemExit('OFFLINE_TEMPLATE: runtime binding not configured')"]
_IMAGE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def _same_json(actual, expected):
    """Profile equality includes JSON types: 1 is not the boolean true."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (actual.keys() == expected.keys()
                and all(_same_json(actual[key], value) for key, value in expected.items()))
    if isinstance(expected, list):
        return (len(actual) == len(expected)
                and all(_same_json(a, b) for a, b in zip(actual, expected)))
    return actual == expected


def _read_json(raw):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('non-finite JSON number')

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def deployment_faults(document) -> tuple[str, ...]:
    """Validate this deliberately narrow rendered Compose profile, not all YAML."""
    faults = []
    try:
        if set(document) != {'name', 'services', 'networks', 'volumes', 'x-ip32'}:
            faults.append('UNSUPPORTED_COMPOSE_FIELDS')
        if document['name'] != 'ip32-offline':
            faults.append('OFFLINE_PROJECT_REQUIRED')
        policy = document['x-ip32']
        if not _same_json(policy, {'environment': 'PAPER', 'live_enabled': False,
                       'native_qualification': 'NOT_RUN', 'hot_reload': False,
                       'business_role': 'ledger_owner', 'cache_role': 'kernel_cache',
                       'dependency_lock': 'qualification/nautilus/requirements.lock'}):
            faults.append('OFFLINE_POLICY_REQUIRED')
        networks = {n for _, nets, _ in SERVICE_POLICY.values() for n in nets}
        if not _same_json(document['networks'], {n: {'internal': True} for n in networks}):
            faults.append('INTERNAL_NETWORKS_REQUIRED')
        volumes = {v for _, _, mounts in SERVICE_POLICY.values() for v in mounts}
        if not _same_json(document['volumes'], {v: {} for v in volumes}):
            faults.append('PRIVATE_MANAGED_VOLUMES_REQUIRED')
        if set(document['services']) != set(SERVICE_POLICY):
            faults.append('EXACT_PROCESS_SET_REQUIRED')
        for name, (uid, nets, mounts) in SERVICE_POLICY.items():
            service = document['services'][name]
            expected = {
                'user': uid, 'read_only': True, 'cap_drop': ['ALL'],
                'security_opt': ['no-new-privileges:true'], 'restart': 'no',
                'pids_limit': 64, 'mem_limit': '256m', 'cpus': '1.0',
                'command': OFFLINE_COMMAND,
                'environment': {'TRADING_ENVIRONMENT': 'PAPER', 'LIVE_ENABLED': 'false',
                                'PYTHONDONTWRITEBYTECODE': '1'},
                'tmpfs': ['/tmp:rw,noexec,nosuid,nodev,size=16m'],
                'volumes': [{'type': 'volume', 'source': v, 'target': '/state/' + v,
                             'volume': {'nocopy': True}} for v in mounts],
            }
            if nets:
                expected['networks'] = list(nets)
            else:
                expected['network_mode'] = 'none'
            if set(service) != set(expected) | {'image'}:
                faults.append('UNSUPPORTED_SERVICE_FIELDS:' + name)
            if any(not _same_json(service.get(k), v) for k, v in expected.items()):
                faults.append('ISOLATION_POLICY_MISMATCH:' + name)
            if not _IMAGE.fullmatch(str(service.get('image', ''))):
                faults.append('PINNED_IMAGE_REQUIRED:' + name)
    except (KeyError, TypeError, AttributeError, ValueError):
        faults.append('MALFORMED_COMPOSE')
    return tuple(dict.fromkeys(faults))


class DeploymentVerifier:
    """Content-bound static preflight. A clean result remains NOT_RUN.

    read_artifact supplies the trusted artifact *inventory bytes*, containing
    image digests; read_config supplies the rendered JSON Compose bytes. No
    shell, network, native launch, or caller-provided PASS flags are consumed.
    """
    def __init__(self, *, read_artifact, read_config, read_dependency_lock=None):
        self.read_artifact, self.read_config = read_artifact, read_config
        self.read_dependency_lock = read_dependency_lock

    def check(self, manifest: t.DeploymentManifest) -> t.DeploymentEvidence:
        faults = []
        if (not isinstance(manifest, t.DeploymentManifest)
                or manifest.environment is not t.Environment.PAPER
                or type(manifest.deployment_id) is not str or not manifest.deployment_id
                or type(manifest.processes) is not tuple
                or any(type(p) is not str for p in manifest.processes)
                or len(set(manifest.processes)) != len(manifest.processes)
                or set(manifest.processes) != set(SERVICE_POLICY)):
            faults.append('OFFLINE_MANIFEST_REQUIRED')
        if any(type(d) is not str or not _HASH.fullmatch(d) for d in
               (getattr(manifest, 'artifact_digest', None), getattr(manifest, 'config_digest', None))):
            faults.append('MANIFEST_DIGEST_REQUIRED')
        if not faults:
            try:
                artifact = self.read_artifact(manifest.artifact_digest)
                config = self.read_config(manifest.config_digest)
                if (hashlib.sha256(artifact).hexdigest() != manifest.artifact_digest
                        or hashlib.sha256(config).hexdigest() != manifest.config_digest):
                    faults.append('CONTENT_DIGEST_MISMATCH')
                else:
                    document, inventory = _read_json(config), _read_json(artifact)
                    faults.extend(deployment_faults(document))
                    if (set(inventory) != {'images', 'dependency_lock_sha256'}
                            or inventory['images'] != {k: v['image'] for k, v in document['services'].items()}):
                        faults.append('IMAGE_INVENTORY_MISMATCH')
                    lock_hash = inventory.get('dependency_lock_sha256', '')
                    if not _HASH.fullmatch(str(lock_hash)) or self.read_dependency_lock is None:
                        faults.append('DEPENDENCY_LOCK_UNAVAILABLE')
                    elif hashlib.sha256(self.read_dependency_lock()).hexdigest() != lock_hash:
                        faults.append('DEPENDENCY_LOCK_DIGEST_MISMATCH')
            except Exception:
                faults.append('ARTIFACT_OR_CONFIG_UNAVAILABLE')
        return t.DeploymentEvidence(manifest=manifest, evidence=(), faults=tuple(faults) or
            ('NATIVE_ISOLATION_BACKING_RESTORE_AND_LOAD_NOT_RUN',), status='BLOCKED' if faults else 'NOT_RUN')
