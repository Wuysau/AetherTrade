# PostgreSQL command and risk-lock authority evidence

These are synthetic PAPER ledger tests, not exchange qualification or a complete end-to-end trading acceptance. Fixed source in the isolated worker: `0abfa889f824b859e7697bb77e519ef19f5616ee`; corresponding integrated source commit is `2fcdc36` on `codex/unified-paper-execution`.

Original `0001_business_ledger.sql` and its append-only guards remain unchanged. The isolated Linux/Python 3.12.16 PostgreSQL 17.11 run installed:

| Migration | SHA-256 |
|---|---|
| `0001_business_ledger.sql` | `b3bfbaaec9dbeeb207297a395290dd4083d5b6101c98a11e240d36a9b32b5dc6` |
| `0002_risk_resolution_authority.sql` where resolution was exercised | `f2597dd09832aef2b4fa1a82f0152c10226b613cdbb5ffce24285ffc6b0793a4` |
| `0004_command_abort_guard.sql` | `19017e040298f0a0f814312252d4c3f3bce25298f3e58512c6d9e8efcf4b73b6` |

```sh
python -m pytest -q tests/integration/test_ledger_transactions.py \
  tests/integration/test_reservations.py tests/integration/test_persistent_risk_locks.py \
  tests/integration/test_durable_dispatch.py --junitxml=/evidence/isolation-guard.xml
# exit 0: 194 passed, 0 skipped
```

Reservation reconciliation reads the effective durable command outcome. A READY command can be aborted as unsent only through an authoritative, transaction-bound record; an audit label alone cannot release backing. Both the base repository and the SQL transition guard reject issuing a send permit after that abort. The original per-command mutation guard and shared collateral mutex stay in force.

The run exercised a legitimate release followed by forbidden base-port and direct-SQL send attempts, abort/send races in both orders, rollback, ordinary send before abort, mismatched scope/hash/receipt and restricted-role forgery attempts. Independent review found that old `REPEATABLE READ`/`SERIALIZABLE` snapshots could otherwise miss an abort despite waiting on an unchanged mutex. Both database boundaries now require `READ COMMITTED` before state access; real two-session tests cover both orderings at both stronger isolation levels. Independent rereview found that issue resolved.

Risk-lock resolution uses an additive, separate resolver role and transaction-bound private approval. The ordinary `ledger_owner` role cannot assume the resolver, create approval rows or directly resolve locks. The resolver is a trusted global authority: production verifier/credential wiring and multi-tenant RLS are not established by this component test. The default service denies resolution without a trusted verifier and separate resolver UoW.

Raw evidence is losslessly compressed beside this report. SHA-256 of the decompressed files:

| Artifact | SHA-256 |
|---|---|
| `isolation-guard.log.xz` | `696cfedea84ab5ac8918723b5881d749121bb4beaac872e9270431f7e1777b88` |
| `isolation-guard.xml.xz` | `513a9336eadbf5138d75e9a20b5cf0a95b415516c04f615eb9f4bbb11f50eaea` |
| `isolation-guard.exit.xz` | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `isolation-runtime-proof.json.xz` | `f0500d2a5834003a6f70e619a062ff357cac9b5666ace8fd005b4445ed65292d` |
| `isolation-ledger-snapshot.json.xz` | `2f74adf81bbe5b6e5729e47b9df670b3e98712ab6e7cad484741794331ba4fbc` |
| `abortguard-ledger-snapshot.py.xz` | `98ff8fa62fe2a10c3c2d6ccafebd98030ca62a1010935d64eae79d74fe2befb9` |

The synthetic ledger snapshot records initial command, authoritative abort, latest raw command and reservation creation/projection for inspection; the included script shows the original queries. The original exact exit file contains `0`. The artifacts were checked for known local database and model credentials before inclusion.

**Not covered:** offline emergency-journal permit revocation, actual platform ACK/query behavior, full 40% protection plus 60% unknown-cancel reservation flow, and product UI/CLI integration. Those remain separate gates; this evidence does not open TESTNET or LIVE writes.
