"""IP-32 offline fixtures and opt-in disposable Linux container probes.

No image pulls/builds, production endpoints, Docker home configuration or secrets.
Native opt-in requires an explicitly provisioned local Linux Docker daemon and
digest-pinned offline Python images. These are OS probes, not Nautilus evidence.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec
from trading_system.observability.metrics import SERVICE_POLICY
from trading_system.runtime.backup import BackupState
from trading_system.runtime.emergency_journal import GENESIS

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-23T00:00:00Z'
LATER = '2026-10-23T00:01:00Z'
SCOPE = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-ip32',
    product=t.Product.SPOT, instrument_uid='synthetic:BTCUSDT', venue_leg='BOTH')


def dependency_lock():
    return (ROOT / 'qualification/nautilus/requirements.lock').read_bytes()


def ref(name):
    return t.EvidenceRef(id='offline:' + name, hash=canonical_hash({'fixture': name}),
        environment=t.Environment.PAPER, source='IP32-OFFLINE-NOT-PLATFORM', visible_at=NOW)


def alert(cause='STORAGE_UNAVAILABLE', identity='alert-1', scope=SCOPE):
    return t.RiskAlert(alert_id=identity, scope=scope, cause=cause,
                       evidence=(ref('failure'),), occurred_at=NOW)


def rendered_config():
    """Synthetic digest resolution solely for unit tests; not image evidence."""
    config = json.loads((ROOT / 'deploy/compose.yaml').read_text('utf-8'))
    for name, service in config['services'].items():
        service['image'] = 'offline/' + name + '@sha256:' + 'a' * 64
    return config


def manifest_inputs(config=None):
    config = rendered_config() if config is None else config
    config_bytes = json.dumps(config, sort_keys=True).encode()
    inventory = json.dumps({'images': {k: v['image'] for k, v in config['services'].items()},
        'dependency_lock_sha256': hashlib.sha256(dependency_lock()).hexdigest()}, sort_keys=True).encode()
    manifest = t.DeploymentManifest(deployment_id='offline-ip32',
        artifact_digest=hashlib.sha256(inventory).hexdigest(),
        config_digest=hashlib.sha256(config_bytes).hexdigest(),
        environment=t.Environment.PAPER, processes=tuple(SERVICE_POLICY))
    return manifest, inventory, config_bytes


def backup_state():
    shard = t.ExecutionShard(environment=t.Environment.PAPER, venue_account_id=SCOPE.venue_account_id,
                             product=SCOPE.product, node_id='synthetic-old', execution_epoch=7)
    lease = t.OwnerLease(shard=shard, instance_id='synthetic-old-instance', expires_at=LATER,
                         deadline_monotonic_ns=60_000_000_000)
    authorization = t.Authorization(authorization_id='synthetic-grant', owner_strategy_id='strategy',
        environment=t.Environment.PAPER, deployment_id='offline-ip32', scopes=(SCOPE,),
        maximum_capital=t.Money(amount=Decimal('10'), currency='USDT'), policy_revision=1,
        protection_options=('NATIVE_REQUIRED',), model_cost_budget=t.Money(amount=Decimal('0'), currency='USD'),
        valid_from=NOW, expires_at=LATER, revoked_at=None)
    locks = tuple(t.RiskLock(lock_id='lock-' + cause, scope=SCOPE, cause=cause, state='ACTIVE',
        permission=t.RiskPermission.RECOVERY_REQUIRED, required_evidence=('CURRENT_RECOVERY_GATE',),
        raised_at=NOW) for cause in ('STORAGE_UNAVAILABLE', 'PROTECTION_MISSING'))
    payload = json.loads((ROOT / 'tests/fixtures/contracts/examples/execution-command.paper.json').read_text('utf-8'))
    payload.update(venue_account_id=SCOPE.venue_account_id, instrument_uid=SCOPE.instrument_uid,
                   execution_epoch=7, state='MAY_HAVE_SENT', created_at=NOW)
    command = ContractCodec().decode('execution-command', payload)
    position = t.ManagedPosition(position_id='synthetic-position', scope=SCOPE,
        actual_quantity=t.Quantity(value=Decimal('0.001'), unit='BTC'), owner_strategy_id='strategy',
        owner_book_id='book', ownership_epoch=1, cost_evidence=t.Coverage(scope=SCOPE,
        query_succeeded=False, pagination_complete=False, supported=False, missing=('ECONOMIC_HISTORY',)),
        realized_pnl=None, unrealized_valuation=None, active_exit_plan_id=None, risk_locks=locks)
    return BackupState(shard=shard, lease=lease, created_at=NOW, authorizations=(authorization,),
        risk_locks=locks, commands=(command,), positions=(position,), reservations=(), protection_plans=(),
        unresolved_cases=(), journal_head=GENESIS, journal_records=(), kernel_cache=ref('cache'),
        credential_recovery_instructions=ref('instructions-no-keys'), research_snapshot_index=ref('research'),
        paused_task_ids=('research-job',))


@dataclass(frozen=True)
class AccessProbe:
    can_read_signing_key: bool
    can_reach_owner_write_endpoint: bool
    shared_volume_readable: bool
    owner_positive_control: bool


class ContainerCase:
    def __init__(self, directory, owner_image, decision_image):
        self.directory = directory
        self.images = {'execution-owner': owner_image, 'decision-worker': decision_image}
        self.prefix = 'ip32-probe-' + uuid.uuid4().hex[:12]
        self.containers, self.networks, self.volumes, self.logs = [], [], [], []
        self.config = directory / 'docker-config'
        self.config.mkdir()
        (self.config / 'config.json').write_text('{}', encoding='utf-8')
        self.document = json.loads((ROOT / 'deploy/compose.yaml').read_text('utf-8'))

    def docker(self, *args, check=True, input=None):
        # An empty explicit config and fixed local socket avoid home credentials,
        # contexts, remote hosts and implicit registry authentication.
        try:
            result = subprocess.run(['docker', '--config', str(self.config), '--host',
                'unix:///var/run/docker.sock', *args], text=True, input=input, capture_output=True,
                timeout=25, env={'PATH': os.environ.get('PATH', ''), 'HOME': str(self.config)})
        except subprocess.TimeoutExpired as error:
            def output(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
            self.logs.append({'args': list(args), 'returncode': None, 'timeout_seconds': 25,
                              'stdout': output(error.stdout), 'stderr': output(error.stderr)})
            raise
        self.logs.append({'args': list(args), 'returncode': result.returncode,
                          'stdout': result.stdout, 'stderr': result.stderr})
        if check and result.returncode:
            raise AssertionError('container probe command failed: ' + result.stderr)
        return result

    def start(self):
        for image in set(self.images.values()):
            self.docker('image', 'inspect', image)  # never pull
        for net in ('owner', 'ledger', 'cache', 'research'):
            name = self.prefix + '-' + net
            self.docker('network', 'create', '--internal', '--label', 'ip32.probe=' + self.prefix, name)
            self.networks.append(name)
        volume = self.prefix + '-owner-state'
        self.docker('volume', 'create', '--label', 'ip32.probe=' + self.prefix, volume)
        self.volumes.append(volume)
        # Synthetic sentinel only; no host mounts. UID/mode setup is an explicit
        # test provisioning action, separate from the nonroot owner process.
        seed = self.prefix + '-seed'
        self.containers.append(seed)
        self.docker('run', '--name', seed, '--pull=never', '--network=none',
            '--label', 'ip32.probe=' + self.prefix, '--read-only', '--user', '0:0',
            '--mount', 'type=volume,src=' + volume + ',dst=/state/owner-state',
            '--entrypoint', 'python', self.images['execution-owner'], '-c',
            "import os; p='/state/owner-state'; os.chown(p,10001,10001); os.chmod(p,0o700); "
            "f=p+'/signing-key'; open(f,'w').write('SYNTHETIC-NOT-A-KEY'); os.chown(f,10001,10001); os.chmod(f,0o600)")
        owner_script = """import fcntl, socket
f = open('/state/owner-state/shard.lock','w')
fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('0.0.0.0',8765)); s.listen()
while True:
 c,a=s.accept(); c.sendall(b'IP32-SYNTHETIC'); c.close()
"""
        self.run_service('execution-owner', owner_script)
        self.run_service('decision-worker', 'import time; time.sleep(600)')
        for _ in range(20):
            if self.exec_python('execution-owner', "import socket; socket.create_connection(('127.0.0.1',8765),1).close()", check=False).returncode == 0:
                break
            time.sleep(.1)
        else:
            raise AssertionError('owner positive control failed to start')

    def run_service(self, service_name, script):
        service = self.document['services'][service_name]
        name = self.prefix + '-' + service_name
        self.containers.append(name)
        args = ['run', '-d', '--name', name, '--pull=never', '--label', 'ip32.probe=' + self.prefix,
            '--user', service['user'], '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges:true', '--pids-limit', str(service['pids_limit']),
            '--memory', service['mem_limit'], '--cpus', service['cpus'],
            '--tmpfs', service['tmpfs'][0], '--network', self.prefix + '-' + service['networks'][0]]
        for mount in service['volumes']:
            args += ['--mount', 'type=volume,src=' + self.prefix + '-' + mount['source'] + ',dst=' + mount['target']]
        for k, v in service['environment'].items():
            args += ['--env', k + '=' + v]
        self.docker(*args, '--entrypoint', 'python', self.images[service_name], '-c', script)
        for net in service['networks'][1:]:
            self.docker('network', 'connect', self.prefix + '-' + net, name)

    def exec_python(self, service, code, *, check=True):
        return self.docker('exec', self.prefix + '-' + service, 'python', '-c', code, check=check)

    def probe_decision_container_access(self):
        owner = json.loads(self.docker('inspect', self.prefix + '-execution-owner').stdout)[0]
        ip = owner['NetworkSettings']['Networks'][self.prefix + '-owner']['IPAddress']
        positive = self.exec_python('execution-owner',
            "import socket; assert open('/state/owner-state/signing-key').read()=='SYNTHETIC-NOT-A-KEY'; "
            "assert socket.create_connection(('127.0.0.1',8765),1).recv(32)==b'IP32-SYNTHETIC'")
        script = f"""import json,socket
try:
 open('/state/owner-state/signing-key').read(); readable=True
except (FileNotFoundError,PermissionError): readable=False
try:
 socket.create_connection(({ip!r},8765),1).close(); reachable=True
except OSError: reachable=False
print(json.dumps([readable,reachable]))
"""
        readable, reachable = json.loads(self.exec_python('decision-worker', script).stdout)
        challenge = self.prefix + '-uid-challenge'
        self.containers.append(challenge)
        result = self.docker('run', '--name', challenge, '--pull=never', '--network=none',
            '--label', 'ip32.probe=' + self.prefix, '--user', '10002:10002', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
            '--mount', 'type=volume,src=' + self.volumes[0] + ',dst=/state/owner-state,readonly',
            '--entrypoint', 'python', self.images['decision-worker'], '-c',
            "import sys\ntry: open('/state/owner-state/signing-key').read()\nexcept PermissionError: sys.exit(3)", check=False)
        assert result.returncode in (0, 3), result.stderr
        # Recheck the endpoint after the negative; a crashed service is not denial.
        self.exec_python('execution-owner', "import socket; socket.create_connection(('127.0.0.1',8765),1).close()")
        return AccessProbe(readable, reachable, result.returncode == 0, positive.returncode == 0)

    def duplicate_owner_exit(self):
        return self.exec_python('execution-owner',
            "import fcntl,sys\nf=open('/state/owner-state/shard.lock','w')\n"
            "try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(3)",
            check=False).returncode

    def backing_roles(self, image):
        self.docker('image', 'inspect', image)
        name = self.prefix + '-postgres'
        self.containers.append(name)
        self.docker('run', '-d', '--name', name, '--pull=never', '--network=none',
            '--label', 'ip32.probe=' + self.prefix, '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=256m', image)
        for _ in range(50):
            if self.docker('exec', name, 'pg_isready', '-U', 'postgres', check=False).returncode == 0:
                break
            time.sleep(.1)
        else:
            raise AssertionError('disposable PostgreSQL not ready')
        def sql(body, check=True):
            return self.docker('exec', '-i', name, 'psql', '-X', '-U', 'postgres', '-v',
                               'ON_ERROR_STOP=1', '-At', input=body, check=check)
        sql((ROOT / 'migrations/0001_business_ledger.sql').read_text('utf-8'))
        sql((ROOT / 'deploy/db/roles.sql').read_text('utf-8'))
        sql('CREATE ROLE kernel_cache NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS; '
            'CREATE SCHEMA kernel_cache; REVOKE ALL ON SCHEMA kernel_cache FROM PUBLIC; '
            'CREATE TABLE kernel_cache.entries (id text PRIMARY KEY); '
            'GRANT USAGE ON SCHEMA kernel_cache TO kernel_cache; '
            'GRANT SELECT,INSERT ON kernel_cache.entries TO kernel_cache;')
        sql('SET ROLE ledger_owner; SELECT count(*) FROM ledger.commands;')
        sql('SET ROLE kernel_cache; SELECT count(*) FROM kernel_cache.entries;')
        denied = [sql('SET ROLE ' + role + '; SELECT * FROM ' + table + ';', check=False)
                  for role, table in [('ledger_owner', 'kernel_cache.entries'),
                                      ('kernel_cache', 'ledger.commands'),
                                      ('ledger_decision', 'ledger.commands')]]
        return all(r.returncode != 0 and 'permission denied' in r.stderr for r in denied)

    def close(self):
        errors = []
        for kind, names in [('container', self.containers), ('network', self.networks), ('volume', self.volumes)]:
            for name in reversed(names):
                # Only unique names created by this fixture are ever removed.
                try:
                    args = (kind, 'rm', '-f', name) if kind == 'container' else (kind, 'rm', name)
                    result = self.docker(*args, check=False)
                    if result.returncode:
                        errors.append(name)
                except Exception:
                    errors.append(name)
        (self.directory / 'container-probe.json').write_text(json.dumps({
            'evidence_layer': 'OS_CONTAINER_PROBE', 'native_qualification': 'NOT_RUN',
            'cleanup_errors': errors, 'commands': self.logs}, indent=2), encoding='utf-8')
        if errors:
            raise AssertionError('probe cleanup incomplete: ' + ', '.join(errors))


def pinned_image(name):
    image = os.environ.get(name, '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}', image):
        pytest.skip('NOT_RUN: explicitly provisioned digest-pinned ' + name + ' required')
    return image


@pytest.fixture
def deployment_case(tmp_path):
    if os.environ.get('IP32_RUN_CONTAINERS') != '1' or sys.platform != 'linux':
        pytest.skip('NOT_RUN: opt-in isolated Linux container runner required')
    case = ContainerCase(tmp_path, pinned_image('IP32_OWNER_IMAGE'), pinned_image('IP32_DECISION_IMAGE'))
    try:
        case.start()
        yield case
    finally:
        case.close()
