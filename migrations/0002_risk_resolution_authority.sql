-- Apply after 0001 and deploy/db/roles.sql as an isolated migration administrator.
-- No runtime login receives this authority automatically. A separately trusted
-- local evidence-verifier process must be explicitly assigned the resolver role.
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_risk_resolver') THEN
        CREATE ROLE ledger_risk_resolver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_risk_resolver' AND
        (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
        OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles
            WHERE rolname='ledger_risk_resolver')) THEN
        RAISE EXCEPTION 'unsafe pre-existing resolver role';
    END IF;
END $$;
GRANT USAGE ON SCHEMA ledger TO ledger_risk_resolver;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO ledger_risk_resolver;
GRANT INSERT ON ledger.domain_events,ledger.outbox,ledger.audit_events,
    ledger.aggregate_heads,ledger.transaction_observations TO ledger_risk_resolver;
GRANT UPDATE(sequence) ON ledger.aggregate_heads TO ledger_risk_resolver;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_risk_resolver;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_risk_resolver;

-- Only the definer function can mint approvals. Runtime audit rows, settings,
-- event payloads and arbitrary authorization strings cannot manufacture one.
CREATE TABLE ledger.risk_resolution_approvals (
    scope_id text NOT NULL, record_id text NOT NULL, sequence bigint NOT NULL,
    payload jsonb NOT NULL, transaction_id text NOT NULL,
    database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
    PRIMARY KEY(scope_id,record_id,sequence)
);
REVOKE ALL ON ledger.risk_resolution_approvals FROM PUBLIC,ledger_owner,
    ledger_control,ledger_decision,ledger_risk_resolver;
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE
    ON ledger.risk_resolution_approvals FOR EACH STATEMENT
    EXECUTE FUNCTION ledger.reject_mutation();

CREATE FUNCTION ledger.check_risk_lock_append() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF NEW.payload->>'state' IS DISTINCT FROM 'ACTIVE' AND NOT EXISTS (
        SELECT 1 FROM ledger.risk_resolution_approvals a
        WHERE a.scope_id=NEW.scope_id AND a.record_id=NEW.record_id
            AND a.sequence=NEW.sequence AND a.payload=NEW.payload
            AND a.transaction_id=NEW.transaction_id
            AND a.database_transaction=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'dedicated lock resolution required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.check_risk_lock_append() FROM PUBLIC;
DROP TRIGGER lock_guard ON ledger.risk_locks;
CREATE TRIGGER lock_guard BEFORE INSERT ON ledger.risk_locks FOR EACH ROW
    EXECUTE FUNCTION ledger.check_risk_lock_append();

CREATE FUNCTION ledger.resolve_risk_lock(p_scope text,p_id text,p_expected bigint,
    p_payload jsonb,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE prior jsonb; prior_sequence bigint; advanced bigint;
BEGIN
    SELECT payload,sequence INTO prior,prior_sequence FROM ledger.risk_locks
        WHERE scope_id=p_scope AND record_id=p_id ORDER BY sequence DESC LIMIT 1;
    IF prior IS NULL OR prior->>'state' IS DISTINCT FROM 'ACTIVE'
        OR prior_sequence IS DISTINCT FROM p_expected
        OR p_payload IS DISTINCT FROM jsonb_set(prior,'{state}','"RESOLVED"'::jsonb)
        OR p_payload->>'lock_id' IS DISTINCT FROM p_id
        OR p_transaction IS NULL OR p_transaction='' THEN
        RAISE EXCEPTION 'invalid or stale resolution' USING ERRCODE='23514';
    END IF;
    UPDATE ledger.aggregate_heads SET sequence=sequence+1
        WHERE scope_id=p_scope AND aggregate_id='risk_locks:' || p_id
            AND sequence=p_expected RETURNING sequence INTO advanced;
    IF advanced IS NULL THEN
        RAISE EXCEPTION 'stale resolution sequence' USING ERRCODE='40001';
    END IF;
    INSERT INTO ledger.risk_resolution_approvals(scope_id,record_id,sequence,payload,transaction_id)
        VALUES(p_scope,p_id,advanced,p_payload,p_transaction);
    INSERT INTO ledger.risk_locks(scope_id,record_id,sequence,payload,transaction_id)
        VALUES(p_scope,p_id,advanced,p_payload,p_transaction);
END $$;
REVOKE ALL ON FUNCTION ledger.resolve_risk_lock(text,text,bigint,jsonb,text)
    FROM PUBLIC,ledger_owner,ledger_decision,ledger_control;
GRANT EXECUTE ON FUNCTION ledger.resolve_risk_lock(text,text,bigint,jsonb,text)
    TO ledger_risk_resolver;
COMMIT;
