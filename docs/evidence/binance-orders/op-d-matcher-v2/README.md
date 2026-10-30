# Actual OP-D V2 component evidence

Functional checkpoint `1b12ed9` is a component checkpoint, not completion of the
46-capability/56-scenario goal, native qualification, or a final0014 interface
freeze. Every source-specific run remains separate in `runs.json`.

| Fixed source | Actual check | Result |
| --- | --- | --- |
| 1b12ed9 | Whole V2 matcher module, fresh isolated PostgreSQL | 21 passed, 0 failed/error/skipped, 102.19s |
| 1b12ed9 | Unchanged full V1 test functions against actual0014 allocator | 19 passed, 0 failed/error/skipped, 85.45s |
| 1b12ed9 | Existing legacy PaperEngine PostgreSQL/role/fault cohort | 59 passed, 0 failed/error/skipped, 4.63s |
| 1b12ed9 | Python unit/contracts/intents | 1525 passed, 65.88s |

Original interrupted `27e3add` run is retained as8 passed/1 PostgreSQL connection
loss, with runner exit137 after Docker interruption; it is not counted as green.
Fresh `27e3add` subsequently passed all9 tests on a new isolated cluster. Early
fixture failures (a bid below the unchanged SELL limit, invalid crossed source
book, invalid decimal spelling and attempted shared AAPL sequence reset) remain
visible alongside genuine missing-feature/logic REDs; no runtime guard was
relaxed to hide them.

Genuine logic REDs include terminal conditional reactivation (`3981d48`), future
book accepted before the actual engine clock (`a6cb594`), missing trailing source
history (`87eda16`), missing actual native OCO and OTO/OTOCO execution, incorrect
initial-entry gross cap applied to a pending exit (`59180e1`), per-node policy
bypass (`840f9ee`), partial STP taker expiry (`e8725b4`) and missing DECREMENT.
Their raw logs, JUnit and fixed source summaries are preserved; all were followed
by actual positive PG checks. Imports that initially exposed absent feature
DTOs are retained as missing-feature RED, not described as executed semantics.

The private cluster has no published host ports, no operational credentials,
no account/model calls, and fresh databases per runner module invocation.
PostgreSQL17.11 image is
`sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`.
The explicitly selected sanitized offline dependency image is
`sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7`.
No TLS/TTL widening or per-case financial database isolation workaround was used.

`copy-map.json` verifies178 copied raw artifacts byte-for-byte against their
original local files. `source-1b12ed9.json` records10 exact committed source
hashes, including unchanged0013. `fresh/commands` retains invocation argv and
exit codes; DSN/env files and source bundles are excluded. Each source summary
also binds migration/provisioner hashes and actual database version. Python XML
and output are in `python/`.

Current root guard/quote/shared-risk changes are not included in this old-source
checkpoint. A new combined root qualification remains required after serial
integration, including original immutable permit-window rechecks. Native source
normalization for these new V2 reports is not yet qualified.
