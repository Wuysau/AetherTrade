"""Fixed environment identity and isolation invariants (policy, not OS proof).

DEV is the only automated test environment; TEST is user acceptance. This module
declares the exact identities that must be provisioned and verified externally.
Passing these checks does not create containers, grant permissions or prove that
the running services match the declaration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class Role(str, Enum):
    DEV = 'DEV'
    TEST = 'TEST'


DEV = Role.DEV
TEST = Role.TEST


_SHA = re.compile(r'[0-9a-f]{40}\Z')


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    role: Role
    service_id: str
    branch: str
    compose_project: str
    database_identity: str
    port: int
    network: str
    volume: str
    credential_id: str
    trading_mode: str
    deployed_commit: str | None = None


# Long-lived DEV: public-snapshot source, fixed shared services. The deployed
# commit is intentionally the running snapshot, not a TEST approval.
FIXED_DEV = ServiceIdentity(
    role=Role.DEV, service_id='dev-cluster-1', branch='public-snapshot',
    compose_project='aether-dev', database_identity='dev-postgres-aether',
    port=55432, network='aether-dev-net', volume='aether-dev-data',
    credential_id='dev-runner-credential', trading_mode='PAPER',
    deployed_commit=None)
# Long-lived TEST: dev branch, user acceptance, pinned deployment. Its data,
# credentials, ports, network and volume must never be shared with DEV.
FIXED_TEST = ServiceIdentity(
    role=Role.TEST, service_id='test-cluster-1', branch='dev',
    compose_project='aether-test', database_identity='test-postgres-aether',
    port=55433, network='aether-test-net', volume='aether-test-data',
    credential_id='test-operator-credential', trading_mode='PAPER',
    deployed_commit='0' * 40)


@dataclass(frozen=True, slots=True)
class IsolationVerdict:
    status: str
    findings: tuple[str, ...]

    @property
    def isolated(self) -> bool:
        return self.status == 'PASS' and not self.findings


def fixed_service(role: Role) -> ServiceIdentity:
    if role is Role.DEV:
        return FIXED_DEV
    if role is Role.TEST:
        return FIXED_TEST
    raise ValueError('UNKNOWN_ENVIRONMENT_ROLE')


def verify_isolation(dev: ServiceIdentity, test: ServiceIdentity) -> IsolationVerdict:
    findings: list[str] = []

    def need(condition: bool, code: str) -> None:
        if not condition:
            findings.append(code)

    need(dev.role is Role.DEV and test.role is Role.TEST, 'ROLE')
    need(dev.trading_mode == test.trading_mode == 'PAPER', 'TRADING_MODE')
    need(dev.branch == 'public-snapshot', 'BRANCH')
    need(test.branch == 'dev', 'BRANCH')
    for name, value in (('SERVICE_ID', dev.service_id), ('COMPOSE_PROJECT', dev.compose_project),
                        ('DATABASE_IDENTITY', dev.database_identity), ('NETWORK', dev.network),
                        ('VOLUME', dev.volume), ('CREDENTIAL_ID', dev.credential_id)):
        need(isinstance(value, str) and bool(value), name + '_MISSING')
    for name in ('service_id', 'compose_project', 'database_identity', 'port', 'network',
                 'volume', 'credential_id'):
        left, right = getattr(dev, name), getattr(test, name)
        need(left != right, name.upper() + '_SHARED')
    need(type(dev.port) is int and 0 < dev.port < 65536, 'DEV_PORT_INVALID')
    need(type(test.port) is int and 0 < test.port < 65536, 'TEST_PORT_INVALID')
    need(isinstance(test.deployed_commit, str) and _SHA.fullmatch(test.deployed_commit) is not None,
         'DEPLOYED_COMMIT')
    need(dev.deployed_commit is None or (isinstance(dev.deployed_commit, str)
         and _SHA.fullmatch(dev.deployed_commit) is not None), 'DEV_DEPLOYED_COMMIT')
    return IsolationVerdict(status='PASS' if not findings else 'BLOCKED',
                            findings=tuple(sorted(set(findings))))


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    cluster_instance_id: str
    database_oid: int
    connection_role: str
    lease_id: str


def verify_resource_identity(expected: ResourceIdentity,
                             observed: ResourceIdentity) -> bool:
    """Names alone never prove ownership: compare instance, OID, role and lease."""
    return (type(observed) is ResourceIdentity
            and expected == observed
            and isinstance(expected.cluster_instance_id, str)
            and bool(expected.cluster_instance_id)
            and type(expected.database_oid) is int
            and expected.database_oid > 0
            and isinstance(expected.connection_role, str)
            and bool(expected.connection_role)
            and isinstance(expected.lease_id, str)
            and bool(expected.lease_id))
