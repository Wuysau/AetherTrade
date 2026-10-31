# Nullable position source repair

Final fixed source `6133034de955b2ac3a7266df5a6fd773fc4ba394` passed34 actual
Linux/PostgreSQL V2 matcher cases,0failed/errors/skipped,157.49s (XML157.482).
Original90456 is terminalexit0. This is a component result for evolving D,
not frozen verifier compatibility, combined root or full46/56 qualification.

Actual root causes: a healthy position row has nullable discrepancy, so whole-row
`IS NOT NULL` returned false and skipped position debit/dependency copying;
new local PL/pgSQL variable reported shadowed the existing depth column. Fix6543655
uses the nonnullable account_id key for presence and qualifies depth columns.
Parent independently reviewed the scoped product diff. No product SQL changed
afterward; frozen root0014 and verifier producer remain separate.

All six full-cohort originals are preserved in copy-map.json (96raw files):

| Source | Actual result | Meaning |
| --- | --- | --- |
| ca57f79 |28pass5fail140.82s| Genuine position1vs0.8 and four STP ambiguity failures |
|6543655|33pass1fail157.86s| All original cases pass; new MARKET fixture lacks arrival depth |
|4962fd0|33pass1fail185.49s| Genuine split fills0.15; fixture assumed report price order |
|fde952c|33pass1fail156.16s| Exact price mapping passes; GTC correctly retains0.85 |
|565cff7|33pass1fail162.29s| Exact position/dependencies/GTC pass; fixture assumes a cash hold |
|6133034|34pass0fail157.49s| Exact fills0.1at81490+0.05at81489, position0, GTC0.85 and genuine zero asset holds |

Every run has complete original XML/summary and launcher/image/dependency/migration
manifests. All errors/skips are zero; the failing assertions remain unmodified
in their original artifacts. Source versions are never summed into a single gate.
No original safety test was removed or weakened. Position observations grant
no account cash/ownership/send/release permission. Full OP-D and46/56 remain ACTIVE.
