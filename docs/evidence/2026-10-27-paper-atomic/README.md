# PAPER atomic target/reservation: scoped evidence

Source: `e0e2981359677ef0824aafaf1416b67367323740`, based on `008b028`.
Date: 2026-10-27. This is a domain service subtask, not completion of the
production console/AI/RULE/HYBRID execution chain or Testnet qualification.

## Failure and correction

The original real PostgreSQL race at `13c46bf026201f2c8d1270920af4703015c53a4d`
accepted two distinct intents targeting the same owned leg. Each transaction
calculated target minus actual, ignoring the other durable-ready command.
`red-pytest.txt` and `red-junit.xml` retain the actual failing run (exit 1).
Only the synthetic database password is redacted from failure diagnostics.

`ReservationService` now locks the management owner before reading book and
trusted inputs, while retaining the existing collateral mutex. It calls
`compile_locked` in that same transaction before persisting the intent, risk
decision, target basis, reservations, command and outbox. The compiler's pending
quantities come from durable command roots in the exact scope. Reservation cash
amounts are never treated as order quantities. Zero-reservation reductions still
occupy quantity. Unknown snapshot obligations and unbound reservations deny.

The command must equal the resulting remaining delta in quantity and direction.
A distinct request with an already satisfied target receives
`TARGET_ALREADY_SATISFIED`, with no second command. Exact replay returns the
original atomic receipt and book basis; it neither renews authority nor sends.
The compiler reloads and checks the durable deployment fence and owner, requires
trusted target inputs, and rejects expired deadlines, changed rules, stale
books, and mismatched snapshot/context bindings.

`reserve_proposal(intent_id)` reads the immutable `AcceptedProposal` from the
ledger, uses its original book sequence, expected instrument and server context,
and invokes the same transaction path. An old proposal cannot replenish after
a manual book change. No caller-supplied receipt ID or boolean grants permission.

Real PostgreSQL testing exposed that the prior `ProposalIngress` composition
could not run with the existing roles: decision writers lacked management/fence
locks and execution owners lacked proposal INSERT. SQL0006 adds the bounded
NOLOGIN `ledger_intake` role. It can persist intake/fence/transaction records and
take required row locks, but cannot insert commands, reservations, fills,
positions, outbox entries, policies, authorizations or risk locks. The existing
owner and decision roles are unchanged. SQL0005 owner CAS was not modified.

## Reconciliation proof and conservative limits

READY roots count immediately, before any side effect. MAY_HAVE_SENT/UNKNOWN
retain the existing group safety block. An ACK or reservation terminal/release
boolean cannot erase a sent root's quantity obligation.

`TargetReportService` reads exact bytes from a trusted server archive, verifies
their hash and typed complete scope report, and persists a durable binding to
the current book. It requires owned terminal order identities, confirmed
operations, zero leaves, consistent terminal quantities, exact deduplicated
durable fills, and actual position equal to the persisted starting position
plus signed fills. A matching book alone cannot hide a manual position change.
Every reservation attempt rereads this durable proof and revalidates the fill
ledger. Missing evidence, new fills, a changed book or a newly sent root require
fresh reconciliation; no in-memory proof callback bypasses this guard.

The archive reader is a trusted adapter composition boundary. A model, API
request, ACK or ordinary websocket payload must never supply it. The existing
PaperEngine query explicitly lacks full balance/position/protection coverage;
its incomplete report cannot serve as this authoritative complete query.
Historical imported commands without an atomic target basis fail closed.
Ownership transfers and unrelated/manual fills need a reviewed reconciliation
mapping; they cannot be inferred from an ID or local terminal label.

Financial holds and quantity obligations remain separate. Risk evaluation
conservatively reserves the full desired delta when an earlier READY quantity
already exists. For target .9 with .7 READY, only .2 is commanded, while the new
hold can remain .9 in addition to the old hold. This can reject otherwise valid
capacity; it never understates held capital. Reservation reflection/release and
risk-lock resolution still require their own evidence. A complete target report
does not release money, resolve an UNKNOWN command, or unlock protection.

The former same-scope second-order capacity tests now deny the unsafe second
command and retain their positive financial counterexample on an independent
account sharing the pool. An additional same-account/different-instrument test
proves target quantities stay separate while cash remains shared.

## Actual verification

Linux Python 3.12.16, PostgreSQL 17.11, dedicated synthetic-only container
`aethertrade-paper-qualification-5fbf2cf3`. Services connect as the existing
restricted ledger owner or new intake role; test provisioning uses the isolated
migration administrator. No real exchange/account or native gate was accessed.

Applied: SQL0001, `deploy/db/roles.sql`, SQL0004, SQL0006. Exact hashes, database
names, table counts, runtime and source SHA are in `manifest.json`.

| Run | Result | Exit |
| --- | --- | --- |
| `tests/integration/test_paper_duplicate_target.py` | 29 passed, 0 skipped | 0 |
| `tests/integration/test_reservations.py` | 67 passed, 0 skipped | 0 |
| `tests/unit/test_target_compiler.py tests/unit/test_locked_actions.py` | 189 passed | 0 |

The target suite covers the actual race, READY/MAY_HAVE_SENT/UNKNOWN/ACK-only
history, zero-asset reductions, original-ID replay, unsent abort, target minus
in-flight, stale rule/fence/deadline/book/snapshot denial, partial fills with
cancel UNKNOWN, exact archive/fill/position reconciliation, old-proposal manual
change rejection, intake privileges, and shared funds across instruments. The
reservation suite retains concurrent strategies/processes, cross products,
reflection, authoritative abort, stale retry and atomic rollback counterexamples.

Committed `*-pytest.txt` and `*-junit.xml` are the actual output. The compressed
`synthetic-ledger.zip` retains all final append-only synthetic facts for both
database runs, including the earlier command states, books, fills, proposals,
reservations and audit evidence. Databases and `.runtime/atomic-final` are
retained; no test database or worktree was deleted.

Reproduce inside an already provisioned isolated Linux Python 3.12 runtime:

```text
IP07_ISOLATED_DATABASE=YES ATOMIC_TEST_ADMIN_DSN=<isolated administrator DSN> python scripts/verify_atomic_target.py
python -m pytest -q tests/unit/test_target_compiler.py tests/unit/test_locked_actions.py
```

The harness creates a unique fresh `ip07_test_atomic_*` database for each module,
records logs/XML/exit/database names, redacts passwords from failure diagnostics,
and retains all databases. It neither installs dependencies nor starts services.

Production console/Store, AI/RULE/HYBRID routing, lifecycle UI projections,
complete simulator account reconciliation, combined protection recovery/crash
matrix and TESTNET/LIVE access remain **NOT WIRED / NOT QUALIFIED** by this
subtask. `NATIVE_PATH_NOT_QUALIFIED` and all native/live restrictions are retained.
