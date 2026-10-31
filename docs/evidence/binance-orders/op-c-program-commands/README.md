# Program command engineering evidence

Final tested source: `6e8f250`. Exact committed source was cloned into a fresh
Linux Python3.12 container beside a new PostgreSQL17 container. No published
ports, existing database, account API, venue transport or paid model was used.

- Final PostgreSQL:56 passed,0 failed,0 errors,0 skipped; exit0;196.73s.
- These56 pytest cases are not the56 ORD acceptance scenarios or all46 capabilities.
- Unit/codec/request/reader/attachment/wallet:75 passed,0 failed.
- `final-green/launcher-manifest.json`: source, launcher and file hashes.
- `final-green/container-images.txt`: actual PostgreSQL/Python image hashes.
- `final-green/postgres/*/summary.json`: migrations, roles, command and counts.
- `final-green/postgres/*/program-commands.log` and `.xml`: original results.
- `evidence-index.json`: SHA256 of every published evidence file.

Preserved failures are part of the record: missing-module initial RED is in the
session transcript; staged creation and wallet RED/GREEN XML are retained here.
`unknown-red` shows the actual missing SQL aggregate CAS. `semantic-red` shows
duplicate plan request insertion. `credit-red` shows unauthorized reflection
through repository and raw owner SQL. `identity-race-red` reproduces four raw
two-connection cross-table races. All those regression cases pass in final-green.

Earlier full runs include a300s timeout and short-permit expiry failures. Their
original manifests/XML/logs remain here. The test's diagnostic-read relocation
alone did not resolve expiry;6e8f250 reduced repeated immutable validation within
one transaction. Current risk/qualification/lease/clocks, independent SQL expiry,
and the <=5000ms TTL remain enforced.

This selected suite's CI summary deliberately says qualification NOT_RUN. Its
accepted=true means these isolated engineering tests passed; it is not full
application/native/venue qualification. Synthetic PAPER qualification fixtures
are explicitly labeled. Testnet/Demo/LIVE, actual fills and controlled transport
remain NOT_RUN. Equity BUY remains blocked until OP-E proves CARD/MAIN funding
location against assessed balances. The full46-capability target is unchanged.

The root integrated service separately tests genuine local-activation obligation
composition, public admission and replay. This branch contains no fake local
registry and no fabricated external algorithm mother.
