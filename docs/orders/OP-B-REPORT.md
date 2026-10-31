# OP-B delivery — 2026-10-29

Approved ADR-ORDER-001 is implemented in versioned contracts, lifecycle guards
and additive storage. ADR-005 is supplemented; old inventories are archived.
Nautilus target stable1.231.0 Windows CPython3.14 is pinned with45 exact artifacts
and actual isolated install/import/LIMIT construction evidence. Linux/container
and native venue qualification remain BLOCKED/NOT_RUN, never inferred from latest.

Implemented: AtomicOrderSpec, OrderProgram and OrderProposal; explicit price,
quantity, trigger, TIF, routing/session/display/STP variants; template topology;
version dispatch; capability/rule snapshots; APPLICATION_GENERATED_CHILD writes
vs VENUE_GENERATED_CHILD observations; scope/service/client/venue parent binding;
cumulative fills/fees/remaining exposure and net protection obligations; precise
Decimal arithmetic; parent chronology/cancel-state guards; typed quarantine and
append-only shared-ledger storage, idempotency and funding references.

Verified source d5074c9:
- Full mandatory Linux/PostgreSQL: 21 suites,828 passed,0 failed/errors/skipped.
- New order-program suite:20 real PG tests, including a command committed before
  DDL upgrade, original-ID/hash recovery, no resend, concurrency, foreign source,
  parent UNKNOWN, cross-parent source replay, active funding and restricted roles.
- Ordinary Python:2499 passed,382 skipped,96 deselected; exit0. DB opt-in tests are
  covered by the separate mandatory job. Native/rootfs/workload qualifications
  without environments remain unrun; the skips are not execution proof.
- Relevant contract/native boundary subset:230 passed. Independent read-only
  review's last three parent-state findings were corrected and rechecked.

Commands:
```
python tools/qualify_paper_docker.py --commit d5074c9 --python-suite --timeout 5400
python -m pytest tests/contracts/test_order_program_v2.py tests/contracts/test_order_lifecycle.py tests/qualification/test_candidate_probe.py tests/qualification/test_nautilus_bridge.py tests/qualification/test_nautilus_order_target.py -q --tb=short
```

Earlier red runs retained:79f3890 10 PG passes/1 fixture rollback assertion error;
c3c3733 PG passed but ordinary suite caught unsynchronized child-path inventory.
Both corrected without removing or skipping safety tests. Review also caught
hash-container misuse, double-counted held money, event dedup vs application,
resolved-lock reuse, SQL NULL guard, rounding and topology errors; corrected code
and counterexamples are retained in commits/tests.

ORD-47 now PASS for isolated PAPER migration compatibility. Remaining55 ORD
scenarios remain NOT_RUN as full scenarios; subcomponent tests do not promote
whole catalog features. All46 target capabilities remain in the requirement set.
Original design package is unchanged; current matrix is
qualification/binance_orders/acceptance-status.json.

OP-C still must connect program compilation, trusted dynamic rules and budgets,
existing target/risk/reservation services, permit issuance and reviewed parent
funding release. OP-D/E/F/G remain downstream work. No new execution transport is
activated by OP-B; no LIMIT-to-MARKET conversion or fabricated fill was introduced.
See OP-B-MIGRATION.md for exact upgrade, recovery and rollback restrictions.
