# IP-32 offline isolation profile

Status: CODE_READY_VERIFY_PENDING. Tests and review: DEFERRED_TO_BATCH.
Native and platform qualification: NOT_RUN. `live_enabled` is false.

`compose.yaml` uses JSON syntax (valid YAML) so the exact preflight document can
be read with the standard library. It is a constrained offline deployment
template, not a working trading stack. Every service exits with an explicit
runtime-binding error. There is no trading startup, credential input, external
port, default network, restart/promotion loop, application hot reload or image
pull/build in this task. The two Dockerfiles build independent Linux source/probe
images; they do not install the unresolved native dependency set.

| Service | UID:GID | Internal networks | Private persistent volume |
|---|---|---|---|
| execution-owner | 10001:10001 | owner, ledger, cache | owner-state |
| decision-worker | 10002:10002 | research | none |
| control-api | 10003:10003 | control | none |
| notifier | 10004:10004 | observation | notification-state |
| research-sandbox | 10005:10005 | network_mode=none | none |
| ledger | 10006:10006 | ledger | ledger-data |
| kernel-cache | 10007:10007 | cache | cache-data |

All services are nonroot, read-only, drop every capability, prohibit new
privileges, and bound CPU, memory, PID count and temporary storage. Volumes are
distinct managed volumes, never home directories, host dependency trees, Docker
sockets or credential mounts. Provision state directories to the assigned UID
with mode 0700 and files with 0600; empty named volumes do not establish this
ownership automatically. The later probe provisions only a synthetic sentinel
volume under its own unique Docker resource names.

No service bridges the research/control/observation networks to owner. The
business ledger and kernel cache have distinct network and volume domains. The
ledger role policy remains `deploy/db/roles.sql`; cache integration must use a
separate noninheriting role/schema, with no ledger access. Sharing a physical
database requires actual negative permission tests, not role-name comparisons.
The opt-in backing probe applies the real ledger migration/role policy to a
disposable PostgreSQL instance and tests cross-schema access denial. Its cache
table is synthetic; this does not qualify Nautilus cache backing or its failure
behavior.

`DeploymentVerifier` consumes artifact-inventory and rendered-config bytes from
trusted readers and checks their SHA-256 against `DeploymentManifest`. The
artifact inventory has exactly `{"images": {"service-name": "image@sha256:..."},
"dependency_lock_sha256": "<lock byte hash>"}`. A separate trusted reader supplies
the dependency-lock bytes for fingerprint comparison. Matching this hash does
not turn IP-02's unresolved lock boundary into an installable dependency lock.
The config is this JSON document with image variables resolved. Manifests use
plain 64-character lowercase SHA-256 for those two byte streams. Image references
use `@sha256:`. Unknown service fields, extra services, host mounts, external
networks, ports, mutable tags, root users, alternate commands and replicas are
rejected. A clean static preflight returns NOT_RUN with no runtime evidence.
The preflight requires exact JSON value types, rejects duplicate object keys
and non-finite JSON numbers, and does not treat numeric 0/1 as booleans or null
volume declarations as empty managed-volume objects.

An image digest proves byte identity, not safety, origin or qualification. The
reviewed runner must supply `IP32_OWNER_IMAGE` and `IP32_DECISION_IMAGE` by digest
after independently building the Dockerfiles in a clean Linux directory. Base
images must be passed as `PYTHON_IMAGE` by digest, with an empty build environment
apart from explicitly reviewed inputs. COPY instructions exclude Windows venvs,
node_modules and host configuration. IP-02's `requirements.lock` currently
records unresolved native artifacts; its comments are not an installable lock.
No fabricated digest or floating dependency is substituted.

The current template deliberately cannot provide application routing or a
usable ledger/cache server. Integration must supply reviewed runtime entrypoints,
authenticated narrow proposal/control/observation bridges, locked transitive
dependencies, exact native bindings and database provisioning. Such changes need
a reviewed profile and the corresponding verifier update. Adding networks or
overriding commands silently would invalidate this preflight profile. Runtime
production deployment is outside this stage's authorization.

One instance per PAPER account/product must additionally hold IP-17's
`LinuxShardLock` in a private directory shared by every contender for that shard.
Compose replicas or naming alone cannot enforce ownership. The container probe
tests a real OS flock with a second process; the separate application-lock probe
tests IP-17's implementation. Neither proves cross-host fencing. Lease expiry
does not revoke an old host's ability to write; use externally verified fencing
followed by the existing recovery gate before any future authorized takeover.
