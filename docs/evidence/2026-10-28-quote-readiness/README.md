# Quote readiness and decision history — 2026-10-28

Implemented directly on public-snapshot, as requested. No additional model calls
or orders were manually triggered by this task. User's active revision8 strategy
and existing scheduler permissions were preserved; normal scheduled activity is
separate from these read-only public-market diagnostics.

Root cause reproduced against actual public quotes: 20 samples all parsed; 14
had timestamps ahead of the local cutoff (maximum 0.620913 seconds). MarketFeed
accepts up to five seconds of clock lead; AnalysisSnapshot required age >= 0.
The 15:54 historical record did not preserve raw quote timestamp, so its exact
subcause cannot retrospectively be proved or invented.

Fix: freeze the observed quote, await local time at most the already supported
five-second lead plus 1 ms scheduling margin before any account transaction,
then establish the cutoff and recheck source generation, timestamps and freshness.
No future quote is admitted, no timestamp rewritten, no stale/risk threshold
relaxed. Too-large clock lead, source changes, stale and malformed data retain
explicit blocking reasons. Public-market-only post-fix probe: 5/5 READY, all
quote timestamps <= actual cutoff; waits 0–0.564s. Account inputs in that probe
were an explicitly inert research stub, not execution approval.

New automatic blocks persist full safe analysis snapshots in the existing job
record and project diagnostic reason, timestamps, snapshot/hash, strategy
revision, NOT_EVALUATED risk status and next review. Old job views recover only
known stored identity/revision fields without rewriting historical rows. Manual
blocks expose the same diagnostic quality. The detail panel is brief and labels
this as data rejection, not trading risk rejection.

Decision history is independently scrollable, default 10 per page, options
10/20/50, previous/next controls and external selection navigation. The dashboard
continues to expose a bounded recent 200-record window; pagination does not imply
unlimited archival search. Automatic blocked records are included even after
falling outside the notification window.

Validation:
- python -m pytest tests/server -m "not paper_runtime" -q --tb=short:
  483 passed, 56 deselected (PG-required cases; full PG gate not rerun here).
- npm --prefix web test: 114 passed.
- npm --prefix web run typecheck / build: exit 0.
- Real public quote probes: before.json / after.json; no paid inference.
- Isolated UI fixture visually checked at 390x844 and 1440x900; scroll/paging and
  compact blocked detail fit both. Existing UI styling retained.
- Local API restarted idle after verification, owner continued; deployed.json
  confirms the old 15:54 record now exposes its original snapshot and revision8.

This does not fix or qualify the separately observed provider JSON response
problem, and does not claim an automated trade or model-quality success.
