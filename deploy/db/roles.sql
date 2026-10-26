-- Isolated migration administrator only. NOLOGIN groups: no passwords, users,
-- memberships, database ownership, or real account access are provisioned.
BEGIN;
DO $$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['ledger_decision','ledger_control','ledger_owner'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',role_name);
        ELSE
            -- Do not silently reuse an unexpectedly privileged host role.
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name AND
                (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
                OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=role_name)) THEN
                RAISE EXCEPTION 'unsafe pre-existing ledger role %',role_name;
            END IF;
        END IF;
    END LOOP;
END $$;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC,ledger_decision,ledger_control,ledger_owner;
GRANT USAGE ON SCHEMA ledger TO ledger_decision,ledger_control,ledger_owner;
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM ledger_decision,ledger_control,ledger_owner;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM ledger_decision,ledger_control,ledger_owner;
GRANT SELECT ON ledger.instruments,ledger.capabilities,ledger.policies,ledger.strategy_revisions,
    ledger.deployments TO ledger_decision;
GRANT SELECT,INSERT ON ledger.decision_runs,ledger.proposals TO ledger_decision;
GRANT SELECT ON ledger.order_projections,ledger.book_snapshots,ledger.risk_locks TO ledger_control;
GRANT SELECT,INSERT ON ledger.control_commands TO ledger_control;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO ledger_owner;
GRANT INSERT ON ledger.intents,ledger.reservations,ledger.reservation_assets,ledger.commands,
    ledger.command_identities,ledger.domain_events,ledger.outbox,ledger.outbox_receipts,
    ledger.venue_events,ledger.fills,ledger.fill_fees,ledger.positions,ledger.protection_plans,
    ledger.risk_locks,ledger.reconciliation_cases,ledger.ownership_transfers,ledger.cashflows,
    ledger.audit_events,ledger.book_snapshots,ledger.book_balances,ledger.order_projections,
    ledger.transaction_observations,ledger.aggregate_heads TO ledger_owner;
GRANT UPDATE(sequence) ON ledger.aggregate_heads TO ledger_owner;
-- PostgreSQL row locks require UPDATE privilege on at least one column.
-- An immutable trigger prevents runtime changes to the binding/mutex rows.
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_owner;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_owner;
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_owner;
CREATE TRIGGER binding_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.account_bindings
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER mutex_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.collateral_mutex
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
-- IP-16 must introduce a reviewed ownership-transfer CAS path before changes
-- to active owners are enabled. SELECT FOR SHARE is allowed, direct mutation is not.
CREATE TRIGGER owner_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.active_owners
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
COMMIT;
