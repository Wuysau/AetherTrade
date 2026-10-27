-- Isolated migration administrator only, after 0001, roles.sql and 0004.
-- No login, credential or membership is installed. The archive verifier and
-- controller must be separately assigned by the trusted host. Ordinary execution
-- owners cannot attest, revoke, consume authority, or update active ownership.
BEGIN;
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['ledger_owner_verifier','ledger_owner_controller'] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=n) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',n);
        ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname=n AND
            (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
            OR EXISTS (SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=n)) THEN
            RAISE EXCEPTION 'unsafe pre-existing ownership role %',n;
        END IF;
    END LOOP;
END $$;
GRANT USAGE ON SCHEMA ledger TO ledger_owner_verifier,ledger_owner_controller;
GRANT SELECT ON ledger.audit_events,ledger.ownership_transfers,ledger.risk_locks,
    ledger.reconciliation_cases,ledger.active_owners,ledger.account_bindings,
    ledger.collateral_mutex,ledger.aggregate_heads,ledger.domain_events,
    ledger.transaction_observations TO ledger_owner_controller;
GRANT INSERT ON ledger.audit_events,ledger.ownership_transfers,ledger.risk_locks,
    ledger.reconciliation_cases,ledger.domain_events,ledger.outbox,ledger.aggregate_heads,
    ledger.transaction_observations TO ledger_owner_controller;
GRANT UPDATE(sequence) ON ledger.aggregate_heads TO ledger_owner_controller;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_owner_controller;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_owner_controller;
-- For SELECT FOR UPDATE only: the immutable trigger still rejects direct SQL.
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_owner_controller;

CREATE TABLE ledger.owner_cas_authorities (
    authority_id text PRIMARY KEY, scope_id text NOT NULL, scope jsonb NOT NULL,
    transfer jsonb NOT NULL, old_owner jsonb NOT NULL, new_owner jsonb NOT NULL,
    proof jsonb NOT NULL, ledger_snapshot jsonb NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE ledger.owner_cas_revocations (
    authority_id text PRIMARY KEY REFERENCES ledger.owner_cas_authorities,
    revoked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ledger.owner_cas_approvals (
    authority_id text PRIMARY KEY REFERENCES ledger.owner_cas_authorities,
    scope_id text NOT NULL, old_owner jsonb NOT NULL, new_owner jsonb NOT NULL,
    transfer jsonb NOT NULL, transaction_id text NOT NULL,
    database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id()
);
REVOKE ALL ON ledger.owner_cas_authorities,ledger.owner_cas_revocations,ledger.owner_cas_approvals
    FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_owner_controller,ledger_owner_verifier;
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.owner_cas_authorities
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.owner_cas_revocations
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.owner_cas_approvals
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

CREATE FUNCTION ledger.owner_cas_guard(p_scope jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed'
        OR p_scope->>'environment' NOT IN ('PAPER','RESEARCH') THEN
        RAISE EXCEPTION 'offline READ COMMITTED owner transfer required' USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM ledger.account_bindings b JOIN ledger.collateral_mutex m
        ON b.environment=m.environment AND b.collateral_group_id=m.group_id
        WHERE b.environment=p_scope->>'environment' AND b.account_id=p_scope->>'venue_account_id'
            AND b.product=p_scope->>'product' FOR UPDATE OF m;
    IF NOT FOUND THEN RAISE EXCEPTION 'owner collateral binding required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM ledger.active_owners WHERE environment=p_scope->>'environment'
        AND account_id=p_scope->>'venue_account_id' AND product=p_scope->>'product'
        AND instrument_uid=p_scope->>'instrument_uid' AND venue_leg=p_scope->>'venue_leg' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'active owner required' USING ERRCODE='23514'; END IF;
END $$;

-- A trusted verifier reads local archives and attests all facts, including
-- authorization of both deployments and explicit acceptance of the exit plan.
-- A client DTO or reference string can never mint this row. The SQL checks the
-- bindings independently; it cannot qualify a venue or replace that verifier.
CREATE FUNCTION ledger.validate_owner_cas(p_sid text,p_scope jsonb,p_transfer jsonb,
    p_old jsonb,p_new jsonb,p_proof jsonb,p_expiry timestamptz) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE f jsonb := p_proof->'facts'; e jsonb := (p_proof->>'evidence_json')::jsonb;
    r jsonb := p_transfer->'projection'->'request'; pos jsonb; expected jsonb;
    prior jsonb; prior_sequence bigint; ref jsonb; moment timestamptz;
BEGIN
    PERFORM ledger.owner_cas_guard(p_scope);
    -- Time must be sampled AFTER waiting on the group/owner guard.
    moment := clock_timestamp();
    SELECT payload,sequence INTO prior,prior_sequence FROM ledger.audit_events WHERE scope_id=p_sid AND record_id='ip16:owner'
        ORDER BY sequence DESC LIMIT 1;
    IF prior IS DISTINCT FROM p_old OR p_old->'position'->'scope' IS DISTINCT FROM p_scope
        OR prior_sequence IS DISTINCT FROM (p_old->>'sequence')::bigint
        OR p_old->>'mode' NOT IN ('TRANSFER','EXITING') OR p_old->'intervention_ids' IS DISTINCT FROM '[]'::jsonb
        OR p_old->>'protection_responsibility' IS DISTINCT FROM 'SYSTEM_PROTECTION'
        OR p_transfer->'projection'->>'state' IS DISTINCT FROM 'COMMIT_OWNER_EPOCH'
        OR r->'scope' IS DISTINCT FROM p_scope OR r->>'mode' NOT IN ('TRANSFER_OWNER','EXIT_THEN_SWITCH')
        OR r->>'transfer_id' IS DISTINCT FROM p_old->>'transfer_id'
        OR r->>'old_owner' IS DISTINCT FROM p_old->'position'->>'owner_strategy_id'
        OR r->>'new_owner' IS NULL OR r->>'new_owner'='' OR r->>'new_owner'=r->>'old_owner'
        OR r->'ownership_epoch' IS DISTINCT FROM p_old->'position'->'ownership_epoch'
        OR p_transfer->'projection'->'ownership_epoch' IS DISTINCT FROM r->'ownership_epoch'
        OR (p_proof->>'context_json')::jsonb IS DISTINCT FROM p_transfer->'context'
        OR f->>'context_hash' IS DISTINCT FROM encode(sha256(convert_to('{"value":'||(p_proof->>'context_json')||'}','UTF8')),'hex')
        OR f->>'evidence_hash' IS DISTINCT FROM encode(sha256(convert_to('{"value":'||(p_proof->>'evidence_json')||'}','UTF8')),'hex')
        OR f->'scope' IS DISTINCT FROM p_scope OR f->>'transfer_id' IS DISTINCT FROM r->>'transfer_id'
        OR f->'transfer_sequence' IS DISTINCT FROM p_transfer->'sequence'
        OR f->'owner_sequence' IS DISTINCT FROM p_old->'sequence'
        OR e->>'transfer_id' IS DISTINCT FROM r->>'transfer_id'
        OR e->'durable_step_refs' IS DISTINCT FROM p_transfer->'projection'->'durable_steps'
        OR f->'authorization_current' IS DISTINCT FROM 'true'::jsonb
        OR f->'pending_commands_resolved' IS DISTINCT FROM 'true'::jsonb
        OR f->'orders_verified' IS DISTINCT FROM 'true'::jsonb
        OR f->'positions_verified' IS DISTINCT FROM 'true'::jsonb
        OR f->'balances_and_allocations_verified' IS DISTINCT FROM 'true'::jsonb
        OR f->'protection_verified' IS DISTINCT FROM 'true'::jsonb
        OR f->'exit_plan_accepted' IS DISTINCT FROM 'true'::jsonb
        OR COALESCE(f->>'accepted_exit_plan_id','')=''
        OR f->>'accepted_exit_plan_id' IS DISTINCT FROM p_transfer->>'accepted_exit_plan_id'
        OR p_expiry IS NULL OR p_expiry<=moment OR p_expiry>moment+interval '60 seconds'
        OR (f->>'valid_until')::timestamptz IS NULL
        OR p_expiry>(f->>'valid_until')::timestamptz THEN
        RAISE EXCEPTION 'invalid owner CAS transfer or current authority binding' USING ERRCODE='23514';
    END IF;
    SELECT payload,sequence INTO prior,prior_sequence FROM ledger.ownership_transfers WHERE scope_id=p_sid
        AND record_id='ip16:transfer:'||(r->>'transfer_id') ORDER BY sequence DESC LIMIT 1;
    IF prior IS DISTINCT FROM p_transfer OR prior_sequence IS DISTINCT FROM (p_transfer->>'sequence')::bigint
        OR NOT EXISTS (SELECT FROM ledger.active_owners
        WHERE environment=p_scope->>'environment' AND account_id=p_scope->>'venue_account_id'
        AND product=p_scope->>'product' AND instrument_uid=p_scope->>'instrument_uid'
        AND venue_leg=p_scope->>'venue_leg' AND owner_strategy_id=r->>'old_owner'
        AND ownership_epoch=(r->>'ownership_epoch')::bigint
        AND policy_revision=(p_old->>'policy_revision')::bigint) THEN
        RAISE EXCEPTION 'stale owner CAS revision' USING ERRCODE='40001';
    END IF;
    IF p_transfer->'context'->'ownership_epoch' IS DISTINCT FROM r->'ownership_epoch'
        OR p_transfer->'context'->'policy_revision' IS DISTINCT FROM p_old->'policy_revision'
        OR p_transfer->'context'->>'environment' IS DISTINCT FROM p_scope->>'environment'
        OR NOT (p_transfer->'context'->'scopes' @> jsonb_build_array(p_scope))
        OR COALESCE(p_transfer->'context'->>'identity_id','')=''
        OR COALESCE(p_transfer->'context'->>'authorization_id','')=''
        OR COALESCE(p_transfer->'context'->>'deployment_id','')='' THEN
        RAISE EXCEPTION 'owner CAS control context required' USING ERRCODE='23514';
    END IF;
    pos := f->'position';
    IF pos->'scope' IS DISTINCT FROM p_scope
        OR pos->'position_id' IS DISTINCT FROM p_old->'position'->'position_id'
        OR pos->'owner_book_id' IS DISTINCT FROM p_old->'position'->'owner_book_id'
        OR pos->'owner_strategy_id' IS DISTINCT FROM p_old->'position'->'owner_strategy_id'
        OR pos->'ownership_epoch' IS DISTINCT FROM p_old->'position'->'ownership_epoch'
        OR pos->'actual_quantity'->'unit' IS DISTINCT FROM p_old->'position'->'actual_quantity'->'unit'
        OR jsonb_typeof(pos->'actual_quantity'->'value') IS DISTINCT FROM 'string'
        OR (pos->'actual_quantity'->>'value')::numeric IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
        OR e->'reconciliation'->'resolved' IS DISTINCT FROM 'true'::jsonb
        OR e->'reconciliation'->'missing' IS DISTINCT FROM '[]'::jsonb
        OR e->'reconciliation'->>'case_id' IS DISTINCT FROM 'ip16:reconcile:'||(r->>'transfer_id')
        OR COALESCE(jsonb_array_length(e->'reconciliation'->'evidence'),0)<1
        OR e->'protection'->'missing' IS DISTINCT FROM '[]'::jsonb
        OR COALESCE(jsonb_array_length(e->'protection'->'evidence'),0)<1
        OR e->'protection'->'covered_quantity'->'unit' IS DISTINCT FROM pos->'actual_quantity'->'unit'
        OR jsonb_typeof(e->'protection'->'covered_quantity'->'value') IS DISTINCT FROM 'string'
        OR (e->'protection'->'covered_quantity'->>'value')::numeric IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
        OR e->'protection'->>'state' IS NULL
        OR (e->'protection'->'covered_quantity'->>'value')::numeric < abs((pos->'actual_quantity'->>'value')::numeric)
        OR ((pos->'actual_quantity'->>'value')::numeric=0 AND e->'protection'->>'state' IS DISTINCT FROM 'FLAT_VERIFIED')
        OR ((pos->'actual_quantity'->>'value')::numeric<>0 AND e->'protection'->>'state' NOT IN ('VERIFIED_NATIVE','VERIFIED_HOSTED')) THEN
        RAISE EXCEPTION 'owner CAS quantity or protection evidence required' USING ERRCODE='23514';
    END IF;
    IF r->>'mode'='EXIT_THEN_SWITCH' AND ((pos->'actual_quantity'->>'value')::numeric<>0
        OR e->'exit'->>'state' IS DISTINCT FROM 'FLAT_VERIFIED'
        OR e->'exit'->'position_id' IS DISTINCT FROM pos->'position_id'
        OR e->'exit'->'incomplete_reasons' IS DISTINCT FROM '[]'::jsonb
        OR (e->'reconciliation'->'residual'<>'null'::jsonb AND
            (e->'reconciliation'->'residual'->'unit' IS DISTINCT FROM pos->'actual_quantity'->'unit'
            OR (e->'reconciliation'->'residual'->>'value')::numeric IS DISTINCT FROM 0::numeric))) THEN
        RAISE EXCEPTION 'flat exit proof required' USING ERRCODE='23514';
    END IF;
    FOR ref IN SELECT value FROM jsonb_array_elements((e->'reconciliation'->'evidence')||(e->'protection'->'evidence')) LOOP
        IF ref->>'environment' IS DISTINCT FROM p_scope->>'environment'
            OR COALESCE(ref->>'id','')='' OR COALESCE(ref->>'source','')=''
            OR COALESCE(ref->>'hash','') !~ '^[0-9a-f]{64}$'
            OR (ref->>'visible_at')::timestamptz IS NULL
            OR (ref->>'visible_at')::timestamptz>moment
            OR (ref->>'visible_at')::timestamptz<moment-interval '60 seconds' THEN
            RAISE EXCEPTION 'fresh owner CAS evidence required' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT FROM (SELECT DISTINCT ON(record_id) record_id,payload FROM ledger.risk_locks
        WHERE scope_id=p_sid ORDER BY record_id,sequence DESC) l WHERE record_id='ip16:freeze:'||(r->>'transfer_id')
        AND payload->'scope'=p_scope AND payload->>'state'='ACTIVE'
        AND payload->>'cause'='OWNERSHIP_TRANSFER' AND payload->>'permission'='NO_NEW_RISK')
        OR EXISTS (SELECT FROM (SELECT DISTINCT ON(record_id) record_id,payload FROM ledger.risk_locks
            WHERE scope_id=p_sid ORDER BY record_id,sequence DESC) l
            WHERE payload->>'state'='ACTIVE' AND record_id<>'ip16:freeze:'||(r->>'transfer_id'))
        OR NOT EXISTS (SELECT FROM ledger.reconciliation_cases WHERE scope_id=p_sid
            AND record_id='ip16:reconcile:'||(r->>'transfer_id') AND payload->'scope'=p_scope
            AND payload->'position_ids' @> jsonb_build_array(pos->>'position_id')) THEN
        RAISE EXCEPTION 'owner CAS freeze or reconciliation binding required' USING ERRCODE='23514';
    END IF;
    expected := jsonb_set(jsonb_set(jsonb_set(jsonb_set(p_old,'{position}',
        jsonb_set(jsonb_set(jsonb_set(pos,'{owner_strategy_id}',r->'new_owner'),
            '{ownership_epoch}',to_jsonb((r->>'ownership_epoch')::bigint+1)),
            '{active_exit_plan_id}',f->'accepted_exit_plan_id')),'{sequence}',to_jsonb((p_old->>'sequence')::bigint+1)),
            '{mode}','"ACTIVE"'),'{transfer_id}','null');
    IF p_new IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'exact next owner projection required' USING ERRCODE='23514';
    END IF;
END $$;

CREATE FUNCTION ledger.owner_cas_snapshot(p_sid text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
    SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY tab,record_id,sequence,payload),'[]'::jsonb) FROM (
        SELECT 'commands' AS tab,record_id,sequence,payload FROM ledger.commands WHERE scope_id=p_sid
        UNION ALL SELECT 'positions',record_id,sequence,payload FROM ledger.positions WHERE scope_id=p_sid
        UNION ALL SELECT 'protection_plans',record_id,sequence,payload FROM ledger.protection_plans WHERE scope_id=p_sid
        UNION ALL SELECT 'reconciliation_cases',record_id,sequence,payload FROM ledger.reconciliation_cases WHERE scope_id=p_sid
        UNION ALL SELECT 'risk_locks',record_id,sequence,payload FROM ledger.risk_locks WHERE scope_id=p_sid
        UNION ALL SELECT 'command_aborts',command_id,1,outcome FROM ledger.command_aborts WHERE scope_id=p_sid
        UNION ALL SELECT 'order_projections',record_id,sequence,payload FROM ledger.order_projections WHERE scope_id=p_sid
        UNION ALL SELECT 'book_snapshots',record_id,sequence,payload FROM ledger.book_snapshots WHERE scope_id=p_sid
        UNION ALL SELECT 'fills',record_id,sequence,payload FROM ledger.fills WHERE scope_id=p_sid
        -- Authorization/configuration may be archived outside this ScopeKey.
        -- Conservatively invalidate even an unrelated revision; never retain a
        -- previously current approval across a revocation/configuration write.
        UNION ALL SELECT 'authorizations',record_id,sequence,payload FROM ledger.authorizations
        UNION ALL SELECT 'deployments',record_id,sequence,payload FROM ledger.deployments
        UNION ALL SELECT 'policies',record_id,sequence,payload FROM ledger.policies
    ) x
$$;
CREATE FUNCTION ledger.attest_owner_transfer(p_id text,p_sid text,p_scope jsonb,p_transfer jsonb,
    p_old jsonb,p_new jsonb,p_proof jsonb,p_expiry timestamptz) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF COALESCE(p_id,'')='' THEN RAISE EXCEPTION 'authority identity required'; END IF;
    PERFORM ledger.validate_owner_cas(p_sid,p_scope,p_transfer,p_old,p_new,p_proof,p_expiry);
    INSERT INTO ledger.owner_cas_authorities VALUES
        (p_id,p_sid,p_scope,p_transfer,p_old,p_new,p_proof,ledger.owner_cas_snapshot(p_sid),p_expiry);
END $$;
CREATE FUNCTION ledger.revoke_owner_transfer(p_id text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE s jsonb; BEGIN
    SELECT scope INTO STRICT s FROM ledger.owner_cas_authorities WHERE authority_id=p_id;
    PERFORM ledger.owner_cas_guard(s);
    INSERT INTO ledger.owner_cas_revocations(authority_id) VALUES(p_id) ON CONFLICT DO NOTHING;
END $$;
CREATE FUNCTION ledger.commit_owner_transfer(p_id text,p_sid text,p_old jsonb,p_new jsonb,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE a ledger.owner_cas_authorities; changed bigint;
BEGIN
    SELECT * INTO STRICT a FROM ledger.owner_cas_authorities WHERE authority_id=p_id;
    PERFORM ledger.owner_cas_guard(a.scope);
    -- Re-read after waiting: READ COMMITTED plus the group guard serializes
    -- controller commits, verifier revocations, and the ordinary owner send path.
    SELECT * INTO STRICT a FROM ledger.owner_cas_authorities WHERE authority_id=p_id FOR UPDATE;
    IF a.scope_id IS DISTINCT FROM p_sid OR a.old_owner IS DISTINCT FROM p_old
        OR a.new_owner IS DISTINCT FROM p_new OR COALESCE(p_transaction,'')=''
        OR EXISTS (SELECT FROM ledger.owner_cas_revocations WHERE authority_id=p_id)
        OR a.ledger_snapshot IS DISTINCT FROM ledger.owner_cas_snapshot(p_sid) THEN
        RAISE EXCEPTION 'owner CAS authority revoked or stale' USING ERRCODE='23514';
    END IF;
    PERFORM ledger.validate_owner_cas(p_sid,a.scope,a.transfer,p_old,p_new,a.proof,a.expires_at);
    INSERT INTO ledger.owner_cas_approvals(authority_id,scope_id,old_owner,new_owner,transfer,transaction_id)
        VALUES(p_id,p_sid,p_old,p_new,a.transfer,p_transaction);
    UPDATE ledger.active_owners SET owner_strategy_id=p_new->'position'->>'owner_strategy_id',
        ownership_epoch=(p_new->'position'->>'ownership_epoch')::bigint,
        policy_revision=(p_new->>'policy_revision')::bigint
        WHERE environment=a.scope->>'environment' AND account_id=a.scope->>'venue_account_id'
            AND product=a.scope->>'product' AND instrument_uid=a.scope->>'instrument_uid'
            AND venue_leg=a.scope->>'venue_leg' AND owner_strategy_id=p_old->'position'->>'owner_strategy_id'
            AND ownership_epoch=(p_old->'position'->>'ownership_epoch')::bigint
            AND policy_revision=(p_old->>'policy_revision')::bigint;
    GET DIAGNOSTICS changed=ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'stale owner CAS' USING ERRCODE='40001'; END IF;
END $$;

-- Preserve the named immutable statement guard, including denial of zero-row
-- runtime updates. Only the table owner's definer function with a private
-- current-transaction approval may reach the exact old/new row guard.
CREATE FUNCTION ledger.check_owner_cas_statement() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF TG_OP<>'UPDATE' OR current_user IS DISTINCT FROM
        (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='ledger.active_owners'::regclass) THEN
        RAISE EXCEPTION 'append-only ledger: mutation forbidden' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT FROM ledger.owner_cas_approvals WHERE database_transaction=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'private owner CAS approval required' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE FUNCTION ledger.check_owner_cas_row() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM ledger.owner_cas_approvals a
        WHERE a.database_transaction=pg_current_xact_id()
            AND to_jsonb(OLD)=jsonb_build_object('environment',a.old_owner->'position'->'scope'->'environment',
                'account_id',a.old_owner->'position'->'scope'->'venue_account_id',
                'product',a.old_owner->'position'->'scope'->'product',
                'instrument_uid',a.old_owner->'position'->'scope'->'instrument_uid',
                'venue_leg',a.old_owner->'position'->'scope'->'venue_leg',
                'owner_strategy_id',a.old_owner->'position'->'owner_strategy_id',
                'ownership_epoch',a.old_owner->'position'->'ownership_epoch','policy_revision',a.old_owner->'policy_revision')
            AND to_jsonb(NEW)=jsonb_build_object('environment',a.new_owner->'position'->'scope'->'environment',
                'account_id',a.new_owner->'position'->'scope'->'venue_account_id',
                'product',a.new_owner->'position'->'scope'->'product',
                'instrument_uid',a.new_owner->'position'->'scope'->'instrument_uid',
                'venue_leg',a.new_owner->'position'->'scope'->'venue_leg',
                'owner_strategy_id',a.new_owner->'position'->'owner_strategy_id',
                'ownership_epoch',a.new_owner->'position'->'ownership_epoch','policy_revision',a.new_owner->'policy_revision')) THEN
        RAISE EXCEPTION 'exact owner CAS approval required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER owner_immutable ON ledger.active_owners;
CREATE TRIGGER owner_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.active_owners
    FOR EACH STATEMENT EXECUTE FUNCTION ledger.check_owner_cas_statement();
CREATE TRIGGER owner_cas_row BEFORE UPDATE ON ledger.active_owners
    FOR EACH ROW EXECUTE FUNCTION ledger.check_owner_cas_row();

CREATE FUNCTION ledger.check_owner_cas_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE p jsonb; o jsonb;
BEGIN
    SELECT payload INTO o FROM ledger.audit_events WHERE scope_id=NEW.scope_id AND record_id='ip16:owner'
        ORDER BY sequence DESC LIMIT 1;
    SELECT payload INTO p FROM ledger.ownership_transfers WHERE scope_id=NEW.scope_id
        AND record_id='ip16:transfer:'||(NEW.transfer->'projection'->'request'->>'transfer_id')
        ORDER BY sequence DESC LIMIT 1;
    IF o IS DISTINCT FROM NEW.new_owner OR p->'context' IS DISTINCT FROM NEW.transfer->'context'
        OR p->'request_hash' IS DISTINCT FROM NEW.transfer->'request_hash'
        OR p->'accepted_exit_plan_id' IS DISTINCT FROM NEW.transfer->'accepted_exit_plan_id'
        OR p->'projection'->'request' IS DISTINCT FROM NEW.transfer->'projection'->'request'
        OR p->'projection'->>'state' IS DISTINCT FROM 'ACTIVE'
        OR p->'projection'->'blocked_reasons' IS DISTINCT FROM '[]'::jsonb
        OR p->'projection'->'ownership_epoch' IS DISTINCT FROM NEW.new_owner->'position'->'ownership_epoch'
        OR (p->>'sequence')::bigint IS DISTINCT FROM (NEW.transfer->>'sequence')::bigint+1
        OR jsonb_array_length(p->'projection'->'durable_steps') IS DISTINCT FROM
            jsonb_array_length(NEW.transfer->'projection'->'durable_steps')+1
        OR ((p->'projection'->'durable_steps') - (jsonb_array_length(p->'projection'->'durable_steps')-1))
            IS DISTINCT FROM NEW.transfer->'projection'->'durable_steps'
        OR NOT EXISTS (SELECT FROM ledger.audit_events WHERE scope_id=NEW.scope_id AND record_id='ip16:owner'
            AND sequence=(NEW.new_owner->>'sequence')::bigint AND payload=o AND transaction_id=NEW.transaction_id)
        OR NOT EXISTS (SELECT FROM ledger.ownership_transfers WHERE scope_id=NEW.scope_id AND payload=p
            AND sequence=(p->>'sequence')::bigint AND transaction_id=NEW.transaction_id) THEN
        RAISE EXCEPTION 'atomic owner and transfer projections required' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER owner_cas_atomic_commit AFTER INSERT ON ledger.owner_cas_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger.check_owner_cas_commit();

REVOKE ALL ON FUNCTION ledger.owner_cas_guard(jsonb),ledger.validate_owner_cas(text,jsonb,jsonb,jsonb,jsonb,jsonb,timestamptz),
    ledger.owner_cas_snapshot(text),ledger.attest_owner_transfer(text,text,jsonb,jsonb,jsonb,jsonb,jsonb,timestamptz),
    ledger.revoke_owner_transfer(text),ledger.commit_owner_transfer(text,text,jsonb,jsonb,text),
    ledger.check_owner_cas_statement(),ledger.check_owner_cas_row(),ledger.check_owner_cas_commit()
    FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_owner_verifier,ledger_owner_controller;
GRANT EXECUTE ON FUNCTION ledger.attest_owner_transfer(text,text,jsonb,jsonb,jsonb,jsonb,jsonb,timestamptz),
    ledger.revoke_owner_transfer(text) TO ledger_owner_verifier;
GRANT EXECUTE ON FUNCTION ledger.commit_owner_transfer(text,text,jsonb,jsonb,text) TO ledger_owner_controller;
COMMIT;
