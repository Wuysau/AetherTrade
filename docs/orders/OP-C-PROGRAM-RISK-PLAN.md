# Generic program risk derivation

Approved root task: actual pure risk metrics/evaluation through the existing
RiskEvaluator, common policy predicates and SAME ExposureCalculator. No fake
TradeIntent, order, native qualification, production reader or permit.

1. Extend ExposureCalculator with explicit pending quantity obligations (empty
   default keeps legacy output), source/reference/unit/dedup checks and exact math.
2. Add source-bound ProgramRiskFacts, per-node execution/cost/margin/child-count
   bounds and phase/quantity-specific protection duties. Unknown facts deny.
3. Derive initial/contingent portfolio states with the existing calculator, proper
   signed net/group reports, retained-position loss and full horizon costs.
4. Reuse common scope/authority/basis/evidence/freshness and policy checks from
   legacy where practical, keeping golden diagnostic parity. Add a pure
   evaluate_program entry to the existing evaluator, not another risk engine.
5. Cover every currently unblocked allocation pattern and fail closed for native
   full-fill-only partial-protection gaps, software overlap, unknown quantities,
   fee/funding/margin/count/source/time mismatches. Existing OCO/quote/residual
   blockers stay explicit and all46 capability goals remain tracked.
6. Run TDD, legacy regression and actual PG regressions after shared-core changes.
   Keep generic financial archive integration independent until these facts/types
   stabilize. Results remain non-authorizing even when mathematical verdict ALLOW.
