# Independent review provenance and scope

The coordinating root agent relayed the explicit independent reviewer result
from `/root/integration_review`: at source `7136ac5ab815`, there were no
unresolved known P0/P1/P2 findings within its reviewed unified PAPER scope.
This is a scoped finding, not an unrestricted system-safety claim.

Earlier actual focused PostgreSQL diagnostics are preserved in
`supplemental-frontend-reviewer-raw.zip`: `marked-console-probe-5abb78df.log/.exit`
(5 passed, exit0) and `marked-console-probe-28c8942a.log/.exit` (4 passed, exit0).
The source/invocation association is relayed by root; their short raw logs alone
do not contain complete attribution.

Root's actual CLI demo then exposed the installed-entrypoint packaging import
error corrected at `c7ce6a420697`. Its actual stop-fill demo exposed the research
fee/history omission corrected at `6cbc8707436f`. These later findings supersede
any inference that the earlier review covered those behaviors without defects.
The reporting RED, intermediate test-assertion error, GREEN and exact source
patch are preserved separately in `reporting-regression-red-green-raw.zip`.
The intermediate `KeyError: realized_pnl` came from asserting an internal field
through the public dashboard; the completed test reads that field through the
internal projection. It is distinguished from the original fee-reporting defect.

Root relayed the final independent reviewer statement for `c7ce6a4` and
`6cbc870` (spacing normalized here; verbatim relay in `final-reviewer-index.json`):

> Reviewed c7ce6a4 and 6cbc870: no new actionable finding. Provision helper byte
> identical after normalizing parents 1 -> 3; role SQL/restrictions unchanged.
> Executed protection orders now enter fee + realized history, target attribution
> still excludes protect, cancel records excluded. Independent targeted 2 passed
> 7.66s, provision entrypoint + stop accounting. Prior qualified risk review
> remains applicable; changes don't alter execution authority. Full final source
> qualification is the root gate.

The actual fresh targeted PostgreSQL log and exit file are in
`final-reviewer-raw.zip`: 2 passed in 7.66s, exit0. Full committed-source
qualification is separately archived; neither targeted review nor passing tests
claims real-venue, TESTNET or LIVE readiness. Root's actual CLI/API/browser
artifacts are maintained separately from this backend evidence collection.
