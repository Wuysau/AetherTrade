# Independent review and resolution

Reviewed candidate: b984749e858543b6e91bf4f155fbe75f698abace.
Read-only reviewer: final_code_review (GPT-6 Astra). No venue, model, PG or Docker actions.

| Finding | Resolution and verification |
| --- | --- |
| P1 resting LIMIT position frames permanently blocked | `demo_balance_history.justify_position` explains exact-time historical balances from verified original-order reports and fee postings; pending snapshots are not dropped. Matching lock/unlock PASS; incorrect 49-USDT lock against 50-USDT order FAIL/rejected. |
| P1 credential recovery stranded old observations and model work | Generation-scoped observation identity; independently signed exact observation hashes authorize revalidation by the original reducer, with immutable originals and audited replacement links. Interrupted decisions become FAILED and are not retried. Regression PASS. |
| P1 balance invalidation blocked retained CANCEL | Signed proof pointer preserved; purpose-specific sender callback verifies cancellation separately without reauthorizing ENTRY. Actual verified-composition wiring and durable cancel regression PASS. |
| P1 expired/disconnected credentials blocked takeover | Authenticated operator reads deployment-fixed account scope independently of trading lease; active workers/drain failures still block acknowledgement. Offline no-lease regression PASS. |
| P2 unresolved history could produce READY/CLEAN | Locked policy verification checks unresolved historical markers; regression demonstrated old failure and now rejects. |
| P2 stale UI request left controls busy | Connection boundary resets pending state; request identity protects later requests. Regression demonstrated old failure and now passes. |

All six findings were accepted. No minor findings deferred. Fix pass used targeted regressions followed by related task regression: 337 Python cases PASS, 27 UI cases PASS, TypeScript PASS. Supplemental forced-exit parameterization: 2 cases PASS (one overlaps the regression selection); no full integration claim.

Reviewer declined actual PostgreSQL locking/migrations/crashes, deployed key protection/immutable-process isolation, live Binance contracts/orders/fills, paid models and full acceptance. Ruling: these are explicitly outside this authorized offline phase and remain BLOCKED/NOT_RUN. They are mandatory before actual trading enablement. No proof or capability was promoted using test output.

Other implementation rulings: reuse local venv and task-local node_modules junction; Python UTF-8 mode for pre-existing Chinese fixtures; distinctive synthetic model key to avoid deliberate secret-filter collision; source/evidence retained instead of skill scratch cleanup; coherent implementation and review-fix commits rather than per-file commits. None grants trading authority or changes the approved money/UNKNOWN contracts.
