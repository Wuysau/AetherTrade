-- Apply after 0001 and deploy/db/roles.sql (after 0002 where installed).
-- No dependency on the separate PAPER engine migration 0003.
BEGIN;
CREATE TABLE ledger.command_aborts (
    scope_id text NOT NULL, command_id text NOT NULL,
    command_payload jsonb NOT NULL, outcome jsonb NOT NULL,
    transaction_id text NOT NULL,
    PRIMARY KEY(scope_id,command_id),
    CHECK(jsonb_typeof(command_payload)='object' AND jsonb_typeof(outcome)='object')
);
REVOKE ALL ON ledger.command_aborts FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT SELECT ON ledger.command_aborts TO ledger_owner;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_risk_resolver') THEN
        GRANT SELECT ON ledger.command_aborts TO ledger_risk_resolver;
    END IF;
END $$;
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE
    ON ledger.command_aborts FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

CREATE FUNCTION ledger.abort_unsent_command(p_scope text,p_id text,p_command jsonb,
    p_request_json text,p_outcome jsonb,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE prior jsonb; prior_sequence bigint;
BEGIN
    -- Same mutex as UnitOfWork, also acquired by the SQL send guard below.
    -- Acquire it before reading command state so raw SQL cannot race an abort.
    PERFORM 1 FROM ledger.collateral_mutex m JOIN ledger.account_bindings b
        ON b.environment=m.environment AND b.collateral_group_id=m.group_id
        WHERE b.environment=p_command->>'environment'
            AND b.account_id=p_command->>'venue_account_id'
            AND b.product=p_command->>'product' FOR UPDATE OF m;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'command collateral binding required' USING ERRCODE='23514';
    END IF;
    SELECT payload,sequence INTO prior,prior_sequence FROM ledger.commands
        WHERE scope_id=p_scope AND record_id=p_id ORDER BY sequence DESC LIMIT 1;
    IF prior IS NULL OR prior_sequence IS DISTINCT FROM 1::bigint
        OR prior->>'state' IS DISTINCT FROM 'DURABLE_READY'
        OR prior IS DISTINCT FROM p_command OR prior->>'command_id' IS DISTINCT FROM p_id
        OR p_request_json::jsonb IS DISTINCT FROM
            (prior - 'state' - 'durable_record_ref' - 'outcome_provenance')
        OR p_outcome->>'command_id' IS DISTINCT FROM p_id
        OR p_outcome->'receipt'->>'command_id' IS DISTINCT FROM p_id
        OR p_outcome->'receipt'->>'status' IS DISTINCT FROM 'ABORTED_UNSENT'
        OR p_outcome->>'provenance' IS DISTINCT FROM 'LOCAL_UNSENT_ABORT'
        OR p_outcome->'durable_record_ref' IS DISTINCT FROM prior->'durable_record_ref'
        OR p_outcome->'receipt'->'existing_result_ref' IS DISTINCT FROM prior->'durable_record_ref'
        OR COALESCE(p_outcome->>'request_hash','') !~ '^[0-9a-f]{64}$'
        OR p_outcome->>'request_hash' IS DISTINCT FROM
            encode(sha256(convert_to(p_request_json,'UTF8')),'hex')
        OR COALESCE(p_outcome->'receipt'->>'receipt_id','')=''
        OR COALESCE(p_outcome->'receipt'->>'reason','')=''
        OR p_transaction IS NULL OR p_transaction=''
        OR NOT EXISTS (SELECT 1 FROM ledger.command_identities i
            WHERE i.scope_id=p_scope AND i.command_id=p_id
                AND i.environment=prior->>'environment' AND i.account_id=prior->>'venue_account_id'
                AND i.product=prior->>'product' AND i.intent_id=prior->>'intent_id'
                AND i.venue_client_order_id=prior->>'venue_client_order_id') THEN
        RAISE EXCEPTION 'invalid unsent abort binding or send boundary already exists' USING ERRCODE='23514';
    END IF;
    INSERT INTO ledger.command_aborts(scope_id,command_id,command_payload,outcome,transaction_id)
        VALUES(p_scope,p_id,prior,p_outcome,p_transaction);
END $$;
REVOKE ALL ON FUNCTION ledger.abort_unsent_command(text,text,jsonb,text,jsonb,text)
    FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT EXECUTE ON FUNCTION ledger.abort_unsent_command(text,text,jsonb,text,jsonb,text) TO ledger_owner;

CREATE FUNCTION ledger.check_command_abort() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF NEW.payload->>'state'='MAY_HAVE_SENT' THEN
        PERFORM 1 FROM ledger.collateral_mutex m JOIN ledger.account_bindings b
            ON b.environment=m.environment AND b.collateral_group_id=m.group_id
            WHERE b.environment=NEW.payload->>'environment'
                AND b.account_id=NEW.payload->>'venue_account_id'
                AND b.product=NEW.payload->>'product' FOR UPDATE OF m;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'command collateral binding required' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM ledger.command_aborts a
            WHERE a.scope_id=NEW.scope_id AND a.command_id=NEW.record_id) THEN
            RAISE EXCEPTION 'aborted command cannot acquire a send boundary' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.check_command_abort() FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
-- Keep the original command_guard and its immutable-state checks untouched.
CREATE TRIGGER command_abort_guard BEFORE INSERT ON ledger.commands FOR EACH ROW
    EXECUTE FUNCTION ledger.check_command_abort();
COMMIT;
