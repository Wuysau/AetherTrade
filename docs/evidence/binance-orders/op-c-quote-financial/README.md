# Source-specific quote kernel and dispatcher evidence

Each directory preserves the original isolated launcher's evidence bytes and a
copied-files-sha256.json map. Credentials/container.env are not copied.

| Directory | Fixed source | Actual PG outcome |
| --- | --- | --- |
| 512-intake-red | 512c09e | 0 pass /6 fail: old intake rejects distinct compilation; later financial assertions not reached |
| 0cf-kernel-green | 0cfc032 | 6 pass /0 fail/error/skip |
| f0-boundary-red | f0beacf | 6 pass /2 fail: genuine book change; clock callable replacement fixture kept separately |
| 86e-clock-fixture | 86efee7 | 7 pass /1 fail: callable replacement does not advance AdmissionWindow's reader |
| 044-clock-red | 04482b1 | 7 pass /1 genuine advancing-clock expiry DID NOT RAISE |
| d6e-kernel-green | d6eaed0 | 8 pass /0 fail/error/skip |
| 490-dispatch-red | 4905fea | 0 pass /5 missing-module fail after actual fixture setup |
| eb7-dispatch-green | eb7c8f3 | 5 pass /0 fail/error/skip |

These are component engineering proofs. Kernel success tests inspect physical
SAME-xmin writes and deliberately roll back when actual request attachment is
missing. No public quote send, native account, venue qualification, complete
46/56 acceptance or full OP-C→D/E/F→G conclusion follows from them.
