# Programme evidence review implementation plan

> **For agentic workers:** Execute inline with TDD and preserve source-bound evidence; use existing agents only within their current authorization.

**Goal:** Reuse the established factual review semantics for explicit2026-10-29 programme research, with no unconditional HOLD on research preferences.

**Architecture:** Keep the frozen programme frame/full V3 proposal parser and existing durable model journal. Primary emits the complete candidate; additional configured profiles use the existing review_v1 language and validate_review. Verified HARD facts or failed/unverifiable critical review prevent candidate handoff. SOFT preference issues return to the primary for one bounded adjudication rather than discarding a candidate. Default UNQUALIFIED automatic paths remain provider-free; research requires the existing authenticated cost acknowledgement.

**Tech Stack:** Existing ModelService, provider payload/parser, strict schemas, pytest/local fake clients.

**Spec:** Approved decision evidence semantics and order handoff architecture; `docs/orders/OP-F-OBSERVATION.md`. Public strategy/HTTP opt-in and real qualified host registration remain integrator-owned.

## Constraints

- No real/paid provider, account, TESTNET/LIVE call.
- No new trading core or bypass of GenericProgramAdmissionService/current risk.
- Old snapshot/hash/review schema, historical strategies and existing assessment paths remain compatible.
- Bind manual research acknowledgement to the exact decision policy as well as models/revisions/frame/key/session.
- Persist exact role/candidate/context requests before each fake/provider IO, with existing bounded call/time limits and cache semantics.
- A failed review retains the original candidate in component records but exposes no approvable proposal. A valid research result remains UNQUALIFIED and never issues permit/fill.

## Tasks

1. Add RED cases for valid full V3 review prompt (review_v1, full candidate), manual-policy binding and SOFT→primary adjudication. Add HARD/UNVERIFIED/reviewer failure cases that preserve journal evidence and block candidate.
2. Update programme prompt selection and programme research branch to reuse validate_review. Keep unanimous mode behavior; add evidence_review only for explicit consensus. Allow at most one primary adjudication. New target cannot increase/reverse primary exposure or leverage; full independent compiler/risk still gates later admission.
3. Run all new and existing model/assessment tests; retain RED/GREEN. No new public protocol opt-in. Update OP-F remaining work and provide reviewed commits to integration_next. Real financial handoff remains separately tested, never replaced by fake admit success.

## Actual progress

Code514d9f8:8 genuine missing-policy/prompt REDs→52 focused passes. Additional
policy binding and final expiry counterexamples are retained. Code8df844e adds
the final current-context/proposal expiry check after review;54 focused passes.
Source90d32b5 ordinary server531passed/56paper-runtime deselected; the missing-
DSN setup error was then completed in real isolated console-contract PG56passed,
zero failures/errors/skips on the same fixed source. No safety test was deleted,
weakened or treated as passed merely through marker exclusion. The separate
file-not-found command executed0 tests and is preserved as a command error.

Real provider/venue calls remain absent. All candidate research remains
UNQUALIFIED and the default automatic branch is provider-free. Public opt-in,
qualified current host and whole46/56 integration remain active work.
