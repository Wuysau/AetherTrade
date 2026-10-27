-- No caller-controlled deferred work may follow the owner CAS commit check.
-- Apply to the dedicated business-ledger database as migration administrator.
BEGIN;
DO $$ BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
END $$;
ALTER FUNCTION ledger.owner_cas_guard(jsonb) RENAME TO owner_cas_guard_base;
CREATE FUNCTION ledger.owner_cas_guard(p_scope jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE caller text := COALESCE(NULLIF(current_setting('role'),'none'),session_user);
BEGIN
    IF has_database_privilege(caller,current_database(),'TEMP,CREATE')
        OR EXISTS (SELECT FROM pg_namespace WHERE has_schema_privilege(caller,oid,'CREATE'))
        OR EXISTS (SELECT FROM pg_roles WHERE rolname=caller AND
            (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls)) THEN
        RAISE EXCEPTION 'owner CAS caller must have no DDL or temporary-object authority' USING ERRCODE='23514';
    END IF;
    PERFORM ledger.owner_cas_guard_base(p_scope);
END $$;
REVOKE ALL ON FUNCTION ledger.owner_cas_guard(jsonb),ledger.owner_cas_guard_base(jsonb)
    FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_owner_verifier,ledger_owner_controller;
COMMIT;
