# Later native/position source-specific evidence

This supplements the immutable1b12 checkpoint; it does not replace or relabel its
178 previously verified raw files. `copy-map.json` has87 new artifacts and
`runs.json` has22 source-specific runs. `source-checkpoints.json` hashes6 exact
committed checkpoints including unchanged0013 and current RED codeca57f79.

- e45a452 whole27 V2 PG passed126.39s; its unchanged V1-on0014 cohort passed19
  cases86.73s. These include parent-only source/nullable-target and exact fee-copy
  regressions, not native child-generation qualification.
- 89469c8 whole30 V2 PG passed140.94s with native TWAP child generation, separate
  Spot/USD-M units/routes, one finite source pool, original mother-only command,
  replay/runtime generation denial and mutable-cap source rejection.
- 96ecab4 missing-position hold actual1 PG passed. Qualified current positions
  were not inferred from money or stale original financial book.
- Current96de153 position increment failed on missing private SELECT permission.
  ca57f79 fixes that read-only grant but the actual short PG remains **RED**:
  after genuine0.2 execution, venue-position projection remains1, expected0.8.
  Source count/pool/quantity data and exact stack traces are preserved.

There is no current full-source/legacy/Python GREEN claim after the position
increment. The parent requested handoff instead of a new long gate. All test
processes are terminal; the idle owned runner was intentionally stopped only
after `docker top` showed `sleep14400` and no test process. Private PG rows remain.
See [complete release handoff](../../../orders/OP-D-RELEASE-HANDOFF-2026-10-30.md).

All data is isolated synthetic PAPER. No live/TESTNET/account/model qualification
is implied by these database executions; no DSN/env/source bundle was copied.
