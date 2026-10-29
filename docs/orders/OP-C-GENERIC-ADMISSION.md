# Generic typed program admission composition

The public `GenericProgramAdmissionService` retains the full controlled intake
and uses the existing ReservationService transaction. Request IDs bind the exact
scope, financial intent, complete compilation and original request index. Local
TWAP/VP activation has no invented venue mother. Initial external creation uses
the actual ProgramCommandRepository attachment API; local scheduling uses the
internal activation API. Neither attachment implementation is replaced by a
callback supplied by the trading caller.

The receipt records exact source/context/compilation/bundle and attachment hashes.
It is returned after the enclosing financial commit succeeds and provides no
execution authority or permit. Attachment failure propagates and rolls back the
financial transaction while leaving restricted intake intact. After attachment,
the composition again checks healthy clock, bounded wall/monotonic time, current
deployment fence and unchanged book. An attachment reader cannot extend the
original submission window.

This is an active integration slice. The command repository and internal local
activation implementations are independently being built in their own worktrees.
Until their real tested commits are integrated, positive public attachment and
replay PostgreSQL qualification remain NOT_RUN. Production readers/transport are
not registered. Missing clock and current execution lease deny admission. Unit
transaction probes verify composition only and never qualify financial SQL or a
native adapter.

The initial external slice requires INITIAL timing for every request. Full native
lists have a single original mother request. Local OTO/OPO and other staged graphs
still require concrete staged activation/funding/state integration; this is a
tracked implementation gap, not a reduced capability catalog. Deferred indices
must retain their original indices and cannot be renumbered or sent early. All46
targets,56 acceptance cases and OP-D/E/F/G remain in scope.

Observed unit RED: missing module/service/source-reader API (4 and2 failing
composition cases). The new identity and final attachment-boundary tests passed11;
combined intake/current-input/request/old-obligation/V3-codec checks passed76.
Actual PostgreSQL public negative composition is added to the existing financial
admission suite, pending a fresh fixed-source diagnostic. No positive financial,
command, outbox, permit, matcher or native qualification is claimed by these unit
tests. TESTNET/LIVE and paid model calls remain disabled for this engineering work.
