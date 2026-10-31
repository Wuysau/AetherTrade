# Quote cash, receipt and position-band containment components

`derive_quote_cash_requirements` uses the actual outward quantity interval and
separates native gross quote usage, received asset, external fee assets and
receipt-debited fees. BUY quote holds and SELL maximum owned base debit remain
different. Third-asset fees remain reserved in that asset; no implicit FX or
hardcoded settlement currency is used. BUY external base fees require already
owned base, without borrowing future receipts. A maximum receipt fee is not a
minimum fee: unconditional net receipt stays an interval including zero, not a
fabricated fixed net quantity. The interval is recomputed to reject forged lower
ceilings. SHARE and BASE ledger units remain explicit.

`assess_position_value_band` combines the shared TargetScopeState's individual
possible leaves with the new signed position-delta interval. Opposite orders do
not net before they fill. Existing UNKNOWN BUY upper quantities cannot authorize
extra BUY requests; a prior SELL cannot finance a new BUY by reducing the net
working sum. Cash oversells, cross-zero reversal and an impossible positive SELL
position delta deny. Exact off-step owned dust remains in the equation.

The approved reduction ruling is explicit: an owned LONG above its desired band
may reduce, while NO-FILL/partial results retain above-goal inventory and an
unmet-goal/management duty. SHORT BUY reduction is symmetric. Every possible new
reducing endpoint must stay within the pre-existing exposure envelope and avoid
crossing zero or overselling. Existing obligations never authorize another
increasing request outside the desired upper band. Independent hard risk and
authorization limits still apply to every endpoint.

These are actual deterministic financial components, not ALLOW decisions or send
authority. Current enforced execution bounds, exact source/owner/book facts,
shared risk assessment, interval financial archive/intent, real wallet funding
location, residual protection/loss duties and SAME transaction admission still
must be integrated and qualified. No old scalar record/hash was changed.

Observed RED:9 missing cash-requirement cases and9 missing band cases; an additional
positive-SELL-delta case failed before its shape guard. Current new/shared/legacy
target and interval suite passed161 with zero skips, exit0. This is not PostgreSQL,
market-path, native or all46/56 qualification.
