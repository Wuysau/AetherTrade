# Isolated ROOT cash and host integration

Cash selection source03298138287fd1e39fbac95b28629e6fe0dd9859 imports reviewed
933040b/805aa53 components.27selected/already-current Cash paths and23preserved
ROOT guard/producer/migration paths are hash-bound in
ROOT-CASH-HOST-DEPENDENCY-a799f19.json. No older neutral cash path remains on the
selected routes. Protected openings, actual raw/source/owned batch transitions,
full0018account snapshot coverage, readonly replay and working projection remain
the source of current balances. Observed preview is financial-only and never
substitutes for strict funding/send guards. Existing5ef/current permit checks and
quote/native/management source separation remain unchanged.

Twelve affected actual PG suites on0329813 passed (140cases,0fail/error/skip):
owned11,working17,accountcash9,source16,firstcapital3,knownness8,physical16,
oldexecution37,rawaccounting9,V2owned2,openinghistory/exclusivity9,bookcash3.
Each has its own original terminal handle and private containers. This is not a
new full72-suite gate, nor reinterpretation of the olda0 full50 proof.

`runtime.program_paper_host.ProgramPaperHost` is an explicit local composition,
not registered in a server startup, HTTP route, scheduler or default mode. It
receives real ExecutionOwner/ProgramCommandBoundary/owner UoW/private source
service and constructs existing owned accounting and working services using the
exact local opening reader. `dispatch(id,lease)` reads original immutable data
and uses existing current mark/consume/owner submit once. MAY/UNKNOWN is queried
by originalID. `observe(id)` posts raw/owned facts before current working order;
on source failure it persists the existing protected UNKNOWN observation.
`reconcile_book()` explicitly acts only in the owner's current scope, never via
silent replay or cross-scope save. `working_inputs()` returns observed facts;
it supplies no current-risk approval, new intake or NO_ACTION.

Actuala799f19 host7PG passed51.117s with0fail/error/skip. V1/V2 cases cover actual
funded first command/owner session/one permit, unfilled observation, finite.4 fill,
.001fee, owned.4 versus unowned initialBTC10, FEE32.596, globalUSDT967371.404/BTC10.4,
working.6, binding and replay. Missing owner preserves raw with no inventory/cash
claim. Historical GTC observation after oldTTL remains a query. Native/quote are
rejected before MAY. Unknown source preserves old book and everaccepted history
but blocks current working authority without a second consumption.

Initial6a04 actual7missingmodule RED13.20s is preserved. A new real host788f
counterexample produced6pass/1DIDNOTRAISE46.47s: returning accountingSOURCE_BLOCKED
alone left oldAccepted current for working inputs. The correction invokes the
already existing observer on that branch; the final negative requires current
UNKNOWN/workingdenial and no book/token changes. No synthetic fills or caller
approval booleans were substituted for the tested services.

Historical pre18 source fixture requires actual old3d13721 bytes. That commit is
not an ancestor of ROOT. An explicit `--historical-source-commit FULL_SHA` option
records and includes it in the offline source bundle; an actual isolated bundle
clone and the final16PG migration cohort prove availability. It changes no Git
history, old runtime/source/hash or current firstcapital guard. Private env files
remain unpublished.24+15 focused unit tests pass1.86/0.46s.

Cost/PnL/history remains incomplete, the true bounded intake still blocks on
POSITION_EVIDENCE, and generic newwrite structural safety is retained. Quote/
native/managed tags have no financial registration or V1 alias. Pending managed
duties are rejected by the host. Effective27/28/29 will be independently selected
and tested next; no new30+ migration is allocated here. New host/composition needs
fresh independent review before promotion. All46targets/56cases remain required;
no operational or externalmode enablement occurs in this checkpoint.
