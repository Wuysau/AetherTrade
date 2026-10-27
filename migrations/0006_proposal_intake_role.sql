-- The decision writer cannot lock management rows or advance the server fence;
-- the execution owner cannot author proposals. Give trusted server intake its
-- own bounded composition role. This grants no command/reservation/fill writes.
-- Apply after 0001 + deploy/db/roles.sql. No dependency on owner CAS migration.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_intake') THEN
        CREATE ROLE ledger_intake NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_intake' AND
        (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
        OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='ledger_intake')) THEN
        RAISE EXCEPTION 'unsafe pre-existing ledger_intake role';
    END IF;
END $$;
REVOKE ALL ON SCHEMA ledger FROM ledger_intake;
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM ledger_intake;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM ledger_intake;
GRANT USAGE ON SCHEMA ledger TO ledger_intake;
GRANT SELECT ON ledger.account_bindings,ledger.collateral_mutex,ledger.active_owners,
    ledger.book_snapshots,ledger.order_projections,ledger.proposals,ledger.audit_events,
    ledger.aggregate_heads,ledger.instruments,ledger.capabilities,ledger.policies,
    ledger.authorizations,ledger.strategy_revisions,ledger.deployments,ledger.decision_runs,
    ledger.intents,ledger.reservations,ledger.commands,ledger.venue_events,ledger.fills,
    ledger.positions,ledger.protection_plans,ledger.risk_locks TO ledger_intake;
GRANT INSERT ON ledger.proposals,ledger.audit_events,ledger.aggregate_heads,
    ledger.transaction_observations TO ledger_intake;
GRANT UPDATE(sequence) ON ledger.aggregate_heads TO ledger_intake;
-- UPDATE privilege enables row locks only; the existing immutable/CAS guards
-- continue to reject direct mutation of management/binding/mutex records.
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_intake;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_intake;
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_intake;
COMMIT;
