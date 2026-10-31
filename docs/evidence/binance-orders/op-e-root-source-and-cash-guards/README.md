# Root source and neutral cash components

These are separate fixed-source component checks, not a combined full gate:

- bc4b3d6: actual frozen V2 observation51PG passed0fail/error/skip238.15s.
-3533b98: actual cash-boundary3PG RED13.10s. Unbound collateral passed; current
  and post-wait paths reached existing risk-lock denial without rederiving cash.
  The latter do not claim the existing risk lock allowed a send.
-43ba29c: actual strict cash-boundary3PG passed0fail/error/skip12.46s;
  separate accountcash7PG and cross-scope bookcash3PG also passed without skips.
-43ba29c ordinary unit/contracts/intents:1770pass26fail66.25s. All26 failures
  used one old SimpleNamespace fixture without require_scope or a no-source-table
  response. The fixture was extended explicitly; no production fallback or
  safety assertion was removed. Its focused119 tests then passed4.12s. Complete
  new-source93efd36 rerun passed1796/0fail/error/skip61.87s, with original log/XML.

86 original files retain source hashes in copy-map.json, including every RED
and complete launcher/JUnit output. Neither source verification, revision binding
nor an economic posting marker proves completed ownership projection. The known
strict ownership-ready/replay/two-scope preview fixes remain separately reviewed
work in the cash tree. No owner writer or executable host was installed here.

Root53→59 mandatory suites retain all original50 suites. Historical fulla0ca274
proof remains tied to that source, and these components never upgrade it. V2
source service registry is still V1-only until its reviewed tagged adapter is
selected. Full46 capabilities/56 cases and C–G remain ACTIVE and incomplete.
