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
