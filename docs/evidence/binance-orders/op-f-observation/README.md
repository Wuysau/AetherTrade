# OP-F observation evidence

Fixed source:
- `31bf6df748dde2f4ba2885448705a112cd120e47`: initial backend/API/CLI + 3 actual PG.
- `97ce8d7`: first UI and read projection followups + 3 actual PG.
- `d51523f`: current semantic scope labels/local-state origin + 7 actual PG.
  The complete hashes are retained in each raw PostgreSQL summary.

Current results at d51523f:
- `pgd515/*/order-observation.log/xml`: 3 passed, no failure/error/skip.
- `pgd515/*/order-observation-admission.log/xml`: 4 passed, no failure/error/skip.
  These use actual public admission, request and local activation components.
- `backend-d515.txt`: 25 passed / 2 excluded paper-runtime tests; prior explicit
  isolated PG diagnostics independently cover the new observation cases.
- `frontend-final.txt`: 118 passed (12 files). `build-final.txt`: exit 0,
  including TypeScript checking. Tests and commands ran on this worktree's source.
- `production-api-admitted.txt`: real HTTP 200, original LIMIT 81490.00, actual
  DURABLE_READY/request/reservation/receipt, execution_authorized=false,
  lifecycle NOT_OBSERVED and current qualification UNKNOWN.
- `production-api.txt`: unauthorized scope 404, all46 preserved, original fields.
- `production-api-anonymous.txt`: unauthenticated list/capabilities both 401.
- `browser-current-desktop.txt` / `browser-current-mobile.txt`: 1440/390 viewport
  equals document width; actual receipt and DURABLE_READY remain distinct from
  venue state. Matching `*-current.png` are final built production UI screenshots.
- `browser-pagination.txt`: real owned browser 20/50 selectors, no document overflow.
- `desktop.png/mobile.png`: EARLIER source97 UI, explicit synthetic unadmitted
  two-leg 81490/81330 proposal, no financial receipt, command or invented fill.
- `cli-capabilities.json`: source-local CLI reference read, all46 targets.

Production browser used Playwright CLI session `opf8897`, our own loopback8897
server, and only newly created isolated PG diagnostic databases. No user's8787
service, existing credentials, model configuration, paid call or venue account
was used. `source-browser_app.py` is the actual local test composition: genuine
OrderObservationService over test PG, independent SQLite console login, market
and automation disabled. The synthetic proposal is explicitly labelled and has
no financial admission/command/fill; the final admission screenshots instead use
actual public-admission fixture rows. Local qualification evidence is explicitly
SYNTHETIC_QUALIFICATION_FIXTURE_NOT_NATIVE. No native qualification claim follows.

Original failures retained:
- `backend-red.txt`: initial missing module (global Python). Global Python also
  lacked FastAPI; subsequent checks use the existing project .venv with local
  pytest paths. `api-first-diagnostic.txt`: 405 vs mistaken403 test expectation,
  corrected to actual absent write route405, no route or guard loosened.
- `frontend-red.txt`: missing component before implementation.
- `source-runner.txt`: missing git in initial diagnostic image; no PG cases ran.
- `source-runner-retry.txt`: short GITHUB_SHA mismatch; no PG cases ran. Full exact
  SHA was then supplied. `source-runner-final/97/d515.txt` retain actual outcomes.
- `cli-environment-first-diagnostic.txt`: .venv editable install resolved main
  checkout without task PYTHONPATH; source-local sys.path invocation corrected it.
- Browser raw session had one startup connection refusal during own server
  restart and one EXPECTED404 probe for an ungranted scope. Neither is hidden as
  a clean console. No unexpected product exception occurred after startup.

All PG runs are DIAGNOSTICS via an explicitly limited `gate.SUITES` override of
the existing isolated runner. Initial31/d515 summaries inherited its generated
POSTGRES_TESTS_PASSED label for the overridden set; that label is NOT a full gate
for the ordinary required inventory. Parent must register both new modules in
required CI and run the integrated full inventory. Native/Testnet/LIVE remain
NOT_RUN and full ORD-45/56 remain incomplete pending genuine host/AI context.

`copy-map.json` verifies each copied PG raw artifact and run/composition source
byte-for-byte against its actual ignored runtime source. It contains SHA256,
no secrets. Container image IDs and resolved Python dependencies are separate.
No secret `container.env`, raw DSN or browser cookie was copied. The raw ignored
runtime remains available for review; no databases or containers were removed.
