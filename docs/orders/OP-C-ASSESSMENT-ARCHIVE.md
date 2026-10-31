# Separate complete-assessment archive revision

Add `PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE` revision1 as a separate static type
carrying actual ProgramRiskFacts and ProgramRiskDecision. Preserve the original
2026-10-29 content archive's fields, strict reader, hashes and blocked converter.
DENY may retain metrics=None; no zero metrics or budget-facts alias is invented.

The new validator replays the existing compiler, target allocator and exposure
calculator from captured typed facts. It then requires the real
RiskEvaluator.evaluate_program result, including exact decision and metrics.
Until that method is implemented the specific result is
PROGRAM_RISK_ASSESSMENT_NOT_AVAILABLE. No validation callback or supplied ALLOW
can replace it. Codecs and pure risk calculations remain non-authorizing; source
intake authentication, current owner, actual financial bundle and send permit
remain separate mandatory duties of the future outer service.

Dependencies are actual commits8972f4e and6791518, with their existing policy
chain79eae93/c71a540 and already-present88d3c90. No class copy or risk stub is used.

## Implemented replay and API

The actual assessor a055f2c, retained/native-stop correction3aa2ad9 and explicit
bounded-reduction type/proof commitsad0b9c2/d92dfda are dependencies. No risk code
copy or supplied validation callback is used. The new archive includes actual
ProgramRiskFacts, optional normal/ReductionRiskMetrics and ProgramRiskDecision.
The codec dispatches metric kind/version explicitly before generic restoration.
False protection, unknown hard loss, exact proof references and EXIT_ONLY
residual state cannot be erased into ordinary complete metrics.

The APIs in `intents/program_assessment_archive.py` are:

- ProgramFinancialAssessmentArchiveCodec.encode/decode/hash: static known-type
  restoration, actual captured compiler/target/exposure/evaluator replay and
  exact canonical typed comparison of the full decision, metrics and hashes.
- replay_assessment: returns the actually recomputed mathematical decision after
  matching the supplied archive. It is not a source authenticator or permit.
- financial_intent_from_assessment_archive and
  financial_risk_basis_from_assessment_archive: return the existing execution-
  neutral public metadata only for recomputed complete ALLOW content. Actual
  DENY, unknown assessment API, altered outputs or lost obligations fail closed.

Replay uses the captured book/facts/clock, making it suitable for content/history
verification. A real outer service must read the bounded intake and current
authorization/owner/market/portfolio/collateral facts, enforce current clocks,
and use the existing kernel/UoW. A saved or recomputed historical ALLOW must not
renew financial authority. The risk basis carries actual incremental required
assets; whole program totals and gross limits cannot replace those requirements.

For bounded EXIT metrics, full proof/residual state and the obligations
PERSIST_RESIDUAL_RISK_LOCK, RETAIN_MANAGE_UNTIL_FLAT and
CURRENT_LOCKED_ACTION_VERIFIER_REQUIRED are retained. Actual lock persistence,
source authentication, budget reservation and the current real verifier remain
mandatory outer duties; this module writes none of them and creates no command.

## Actual validation

The final contracts/intents/unit run passes1349 cases. New real PostgreSQL tests
pass1 complete archive/metadata restart case plus10 existing financial-record
cases, with zero failures/skips. The archive case preserves a genuinely derived
bounded EXIT with False protection, None loss/bound and all residual duties; it
asserts no command, outbox, reservation or risk-lock writes. This proves storage
and content fidelity, not an executable financial bundle or venue qualification.

Raw retained-risk RED2, subtype restoration RED1 and canonical-byte RED2 are
retained with final JUnit/XML/source/image evidence in
`docs/evidence/binance-orders/op-c-assessment-archive/`. The byte checks catch
Python False/0 and Decimal0/-0 equality collisions rather than trusting dataclass
equality. Existing normal/protected partial exits, invalid stops, full V2/V3 raw
parameters, FX/cost/protection/count/horizon data, forged self-consistent ALLOW,
unknown/recovery locks and erased residual obligations have actual tests.

The old known-content archive module is byte-identical to d13a9f2; its converters
remain blocked. All42 prior public/packaged files and existing V2/V3 known-content
archive hashes remain unchanged. No migration or schema rewrite is introduced.
