# PAPER owner and daily-loss review policy

The independent owner runs with `aethertrade --paper-config <runtime.json> paper-run`.
It reads durable commands, queries their original synthetic IDs, reconciles fills,
and attempts a bounded stop repair for an existing GAP responsibility. It does not
call models or inject fills or protection verification. An accepted stop remains
unverified until an explicit synthetic event proves it. Existing pending stop IDs
are retained, UNKNOWN and EXIT_ONLY permissions continue to deny repair, and a
pending protected-reduction adjustment suppresses automatic repair through
`pending_protected_adjustment(tx)`.

The worker reports `PAPER_OWNER_NEEDS_OPERATOR_REVIEW` on storage failures or
failed maintenance and keeps running. It reports storage restoration separately;
restoration does not clear any lock. Process supervision remains an operator
responsibility. A failed process or unavailable PostgreSQL cannot execute work;
its durable commands and cause locks are resumed using the same IDs on restart.

DAILY_LOSS uses the configured console risk-day timezone (default Asia/Shanghai).
The release policy is `NEXT_CONFIGURED_DAY_FLAT_REVIEW_REQUIRED`: a price rebound,
a new day, pausing/resuming, or starting the worker never unlocks it. Explicitly
run:

```
aethertrade --paper-config runtime.json --resolver-profile runtime.resolver.json paper-daily-review <lock_id> --operator-review-id <review-ticket>
```

The separate restricted review login is never loaded by serve/paper-run. Review
requires a later day in the same configured timezone, archived original cause,
explicit operator review ID, complete synthetic venue order/fill queries for all
symbols, matching business fills, flat positions and no working/pending commands.
Any other active cause remains a blocker. The current day loss baseline must be
below the configured threshold at the reconciled cash balance. Only the trusted
resolver role commits the resolution event/projection; console state updates
after that commit. A crash between commits leaves the console locked until review
is retried. Historical locks without archived risk-day evidence stay locked;
there is no inferred backfill or generic unlock endpoint. Reviewing an invested
account is deliberately unsupported in this conservative PAPER policy.

Unchanged balance refreshes and unchanged order queries preserve the book basis.
Economic changes still advance it. A later exposure gap re-arms a previously
resolved protection cause using an intent-bound identity. This is local PAPER
behavior and conveys no Testnet, real venue, liquidity, or native protection
qualification.
