# Source knownness and first generic capital

Separate fixed-source component results, never a combined full gate:

- b7f6d85: actual source-backed submission8PG passed0fail/error/skip49.69s.
  Genuine V1/V2 unfilled acceptance after creation TTL, real rejection, owner
  denial, foreign snapshot/venue-ID rejection and concurrent delayed query race.
- a9a076f: first generic capital3PG passed0fail/error/skip6.46s. Missing protected
  opening and owner-inflated book reject; actual funded MAY/consume/venue chain
  binds original opening/revision without creating fills or inventory.

All seven original runs/112 raw files are preserved with source/byte maps:

| Source | Actual result | Interpretation |
| --- | --- | --- |
|fb8c62b|5fail9.72s| Earlier fixture lacked19 readonly source privileges; no consume |
|ee3adf4|5fail21.11s|4 genuine missing-module failures,1 crossed-book fixture error |
|bb7da71|1pass2fail8.03s| Genuine first-missing-opening unsafe admission;1 rawstatus fixture mismatch |
|a9a076f capital|3pass6.46s| Strict current funding and original-context fields proved |
|a9a076f submission|5fail20.89s| New reader required consume before own pre-consume MAY |
|b188c87|4pass1fail29.17s| Corrected reader; final fake-audit test used wrong domain insertion API |
|b7f6d85|8pass49.69s| All corrected and new actual authority/identity/race cases pass |

All errors/skips are zero. Every failure remains in original log/XML; source
versions are not summed as one pass. Latest query failure remains UNKNOWN while
earlier actual acceptance history survives. No new permit, resend, terminal,
ownership, cash credit or release follows from submission knownness.
