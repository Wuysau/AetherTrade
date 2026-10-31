# Actual program creation dispatcher

ProgramCommandDispatcher receives only a command ID and current lease. Trusted
host construction supplies the existing owner UoW, protected ProgramCommandBoundary,
actual PaperEngine/ExecutionOwner and mandatory shared revocation side-effect
guard. An absent composition denies before MAY; no queue permit is accepted.

READY dispatch commits the protected MAY mark, then calls the actual controlled
engine, which consumes the single permit. No business transaction spans SIM IO.
A receipt is an observation, never fill/accounting/protection/release authority.
MAY/UNKNOWN recovery reads immutable original IDs without a new risk replay,
new IDs, renewed permit or resubmission. An ordinary error after MAY records
UNKNOWN. A process-boundary exception leaves durable MAY for original-ID query.

Fixed4905fea gave5 real missing-module PG failures after actual financial/source
fixture setup. Fixeb7c8f3 passed5 real PG cases, zero failures/errors/skips:
actual partial fill with one consumption, lost ACK after SIM commit, a SystemExit
between MAY and engine, absent revocation guard and foreign lease. SystemExit
proves control-flow unwinding; it is not a real kill/deployment qualification.

The dispatcher currently covers the integrated V3 single atomic PAPER creation
interface. Advanced source/lifecycle/management and quote financial kinds remain
explicit work, not inferred from vocabulary. Trusted report ingestion and same
ledger accounting must preserve native quantities, FinancialUnit, raw fees and
economic discrepancies. No ACK/status alone releases funds or declares FLAT.
