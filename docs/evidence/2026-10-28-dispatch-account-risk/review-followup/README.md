# Review follow-up: receipt/recovery admission

Fixed source: `e115f3b`, based on `7334c5e`. Same isolated Windows application / Docker PostgreSQL scope and loader as the parent evidence directory. Every console case provisions fresh three-database restricted-role profiles; no reset/deletion or external venues. Source hashes are recorded for both RED attempts and the final fixed dispatcher/test files.

Review found that the optional initial `before_check` callback ran even for commands whose state prohibits a send. The public runtime already queries/gates those states, but direct dispatcher receipt/recovery calls could fail on current account quotes or limits. The fix peeks at state under the existing owner fence, outside the admission callback, and invokes first-send admission only for DURABLE_READY. The post-durable and all PaperEngine authority checks remain unchanged.

Final nine new PostgreSQL cases cross UNKNOWN / MAY_HAVE_SENT / VENUE_CONFIRMED with stale unrelated BTC, BTC daily-loss price 1, and ETH price change 4000. The UNKNOWN setup stops after actual simulated ACK loss before automatic reconciliation; VENUE_CONFIRMED includes a full ETH fill to ensure maintenance never double-counts its completed quantity as new exposure. Direct dispatcher receipts, public original-ID query/reconcile, and unchanged venue command counts are asserted. The original eight denial/persistence/bounded-exit tests also remain in the final run.

Commands use the Python executable and cwd documented in the parent README. Arguments below follow `.runtime/run_probe.py`, except the corrected RED replay which runs `.runtime/repeat_red.py`. Set `PYTHONIOENCODING=utf-8` for the final matrix and corrected replay commands. This also avoids an early loader console-print encoding exception after pytest had already saved its exit and raw log.

| Log suffix | Arguments / source | Outcome |
| --- | --- | --- |
| `45e4dc7c` | `tests/integration/test_paper_dispatch_account_risk.py::test_repeat_dispatch_receipt_needs_no_new_risk_admission`, pre-fix dispatcher | 2 genuine QUOTE_UNAVAILABLE failures (MAY_HAVE_SENT and confirmed receipt) plus one UNKNOWN fixture failure: public runtime had already reconciled it to confirmed |
| `4dc7bffd` | `tests/integration/test_paper_dispatch_account_risk.py`, fixed dispatcher, first fixture | 10 passed / 1 UNKNOWN fixture failure |
| `db318975` | `tests/integration/test_paper_dispatch_account_risk.py`, fixed dispatcher, expanded matrix with first fixture | 14 passed / 3 UNKNOWN fixture failures |
| `5e0f0412` | `tests/integration/test_paper_dispatch_account_risk.py`, final fixture and fixed dispatcher | **17 passed, exit 0** |
| `3740982d` | `.runtime/repeat_red.py`: final `...[stale-UNKNOWN]` test with dispatcher bytes from `7334c5e` | **1 genuine RED failure, QUOTE_UNAVAILABLE, exit 1** |
| `59dd350e` | `tests/integration/test_paper_dispatch_account_risk.py::test_repeat_dispatch_receipt_needs_no_new_risk_admission[stale-UNKNOWN]`, restored fixed dispatcher | **1 passed, exit 0** |

`repeat_red.py.txt` archives the replay script. It temporarily substitutes the old dispatcher, records SHA256 for the exact old bytes and final test, runs the final ACK-loss regression, and restores the fixed file byte-for-byte in `finally`. The final focused suite was already green before this replay; the single restored-source green confirms it afterward. The intermediate fixture failures are preserved, not reported as production defects.

Full integrated source qualification and Linux process tests remain outside this follow-up evidence.
