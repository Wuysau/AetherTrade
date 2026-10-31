# Domain DAILY_LOSS monitor — design and implementation plan

Approved task: next-engineering-task A.4, with A.3 management intersections.
The coordinating task approved the engineering choices below on 2026-10-27.

## Design

Build one offline domain monitor over the existing PostgreSQL UoW, RiskDayService
and original t.RiskLock. A separate legacy lock or caller-side threshold check
would create another authority and is excluded. The monitor does not enable
Testnet/native/live, trading, or approval issuance.

The approved SPEC defines `E(t)-E(day_start)-F` and IANA risk days but omits the
fraction denominator. Use positive, exactly evidenced opening equity as a fixed
denominator, explicitly approved as an engineering choice. Latch if any verified
observed in-day point reaches the configured loss fraction; complete observed
coverage does not establish an unobserved market trough.

Only an explicitly injected trusted local reader supplies the typed evidence
report inside the collateral mutex. Re-read policy from its persisted source and
compare the exact authorized revision/content. Re-read authoritative group
members. Require complete equity/valuation/cashflow/cost coverage, exact opening,
valid historical event evidence and fresh latest observation/coverage. Empty or
unknown cost coverage is not evidence of zero. Use RiskDayService.pnl and exact
arithmetic; fees already reflected in equity are not deducted twice.

Missing reader, invalid/missing evidence, stale/future time or unknown coverage
denies new risk. It does not fabricate an observed daily-loss breach or prevent
independently authorized/proved A.3 management beyond existing lock intersection.
Refresh healthy UTC/monotonic service clocks after reader/database work and
immediately before writes. Return only after UoW commit; database loss is not a
successful assessment. Breach appends the original lock, event and evidence
record atomically. Reusing any applicable active daily lock under the same group
mutex deduplicates concurrent BTC/ETH triggers. Rebound, restart and rollover
never resolve a lock.

Existing source resolution uses its separate trusted resolver. Monitor reads
validated resolved originals and the authoritative ResolutionAudit. That audit
does not bind a complete observation watermark: safe rearm therefore
remains DENY_NEW_RISK with an explicit missing-watermark code, including rollover
without separate authoritative new-day policy review. Do not replay an
already reviewed historical breach or invent an override/new authorization.
The original day baseline is never reset. Console/AI/manual wiring and a real
production archive reader remain explicit integration work, not a second lock.

## Implementation

1. Extend only RiskLockRepository.applicable with keyword include_resolved=False;
   preserve default active-only behavior and all identity/history/group checks.
   Prove resolved-original validation and unrelated-group isolation.
2. Add risk/daily_loss.py: typed report/coverage/result, trusted local reader,
   persisted-policy/group validation, clock/coverage checks, approved P&L,
   atomic observed-breach latch and fail-closed review watermark gap.
3. Add isolated PostgreSQL tests reading protected, server-held synthetic report
   archives: threshold/commit/restart/recovery/rollover, IANA/DST and cashflow
   adjustment, concurrent group triggers, source review, A.3 partial management,
   invalid/missing data, clock races and actual transaction loss/rollback.
4. Run focused unit and real Linux/PostgreSQL probes on fresh ip08_test_* DBs,
   preserve failing regression then fixed-commit raw output, exit, source hashes
   and source identity. Record wiring limits without claiming full PAPER/LIVE.
