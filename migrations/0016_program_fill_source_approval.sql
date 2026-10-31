-- Additive historical PAPER source attestation, not order or financial authority.
-- Migration administrator only. No runtime principal is automatically enrolled.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_source_verifier') THEN
  CREATE ROLE ledger_program_source_verifier NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
   NOINHERIT NOREPLICATION NOBYPASSRLS;
 ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_source_verifier' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) OR
  EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_source_verifier')
   OR roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_source_verifier')) THEN
  RAISE EXCEPTION 'unsafe pre-existing programme source verifier role';
 END IF;
END $$;

CREATE TABLE ledger.program_fill_source_approvals(
 scope_id text NOT NULL,command_id text NOT NULL,approval_id text NOT NULL,
 identity_hash text NOT NULL,payload jsonb NOT NULL,payload_hash text NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope_id,approval_id),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.program_permit_consumptions(scope_id,command_id),
 CHECK(approval_id=payload_hash));
CREATE INDEX program_fill_source_identity ON ledger.program_fill_source_approvals(scope_id,identity_hash);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.program_fill_source_approvals
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.program_fill_source_approvals FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;
GRANT SELECT ON ledger.program_fill_source_approvals TO ledger_owner,ledger_program_source_verifier;

GRANT USAGE ON SCHEMA ledger TO ledger_program_source_verifier;
GRANT SELECT ON ledger.account_bindings,ledger.collateral_mutex,ledger.program_request_bindings,
 ledger.program_dispatch_marks,ledger.program_permit_consumptions,ledger.order_writes_v2,ledger.audit_events
 TO ledger_program_source_verifier;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_program_source_verifier;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_program_source_verifier;
GRANT INSERT ON ledger.transaction_observations TO ledger_program_source_verifier;

CREATE FUNCTION ledger.approve_program_fill_source(p_scope text,p_command text,p_payload jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE consumed ledger.program_permit_consumptions%ROWTYPE;
 marked ledger.program_dispatch_marks%ROWTYPE; initial jsonb; binding jsonb; bundle jsonb;
 approval_hash text; identity text; group_name text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR p_scope IS DISTINCT FROM ledger.program_digest(p_payload->'scope')
  OR p_payload->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR p_payload->>'record_kind' IS DISTINCT FROM 'PAPER_PROGRAM_FILL_SOURCE_V1'
  OR p_payload#>>'{scope,environment}' IS DISTINCT FROM 'PAPER'
  OR p_payload->>'command_id' IS DISTINCT FROM p_command THEN
  RAISE EXCEPTION 'invalid PAPER source attestation identity' USING ERRCODE='23514';
 END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings
  WHERE environment='PAPER' AND account_id=p_payload#>>'{scope,venue_account_id}'
   AND product=p_payload#>>'{scope,product}' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment='PAPER' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'source collateral binding missing' USING ERRCODE='23514'; END IF;
 SELECT * INTO consumed FROM ledger.program_permit_consumptions WHERE scope_id=p_scope AND command_id=p_command;
 SELECT * INTO marked FROM ledger.program_dispatch_marks WHERE scope_id=p_scope AND command_id=p_command;
 SELECT payload INTO initial FROM ledger.order_writes_v2 WHERE scope_id=p_scope AND record_id=p_command AND sequence=1;
 SELECT payload INTO binding FROM ledger.program_request_bindings WHERE scope_id=p_scope AND record_id=p_command AND sequence=1;
 SELECT payload INTO bundle FROM ledger.audit_events WHERE scope_id=p_scope
  AND record_id='program-financial-bundle:'||(initial->>'intent_id') AND sequence=1;
 IF consumed.command_id IS NULL OR marked.command_id IS NULL OR initial IS NULL OR binding IS NULL OR bundle IS NULL
  OR consumed.database_transaction=pg_current_xact_id() OR marked.database_transaction=pg_current_xact_id()
  OR consumed.database_transaction=marked.database_transaction OR consumed.token_hash IS DISTINCT FROM marked.token_hash
  OR p_payload->>'consumption_hash' IS DISTINCT FROM ledger.program_digest(consumed.context)
  OR p_payload->>'dispatch_mark_hash' IS DISTINCT FROM ledger.program_digest(marked.context)
  OR p_payload->>'original_write_hash' IS DISTINCT FROM ledger.program_digest(initial)
  OR p_payload->>'binding_hash' IS DISTINCT FROM ledger.program_digest(binding)
  OR p_payload->>'binding_hash' IS DISTINCT FROM initial->>'binding_hash'
  OR p_payload->>'request_hash' IS DISTINCT FROM initial->>'request_hash'
  OR p_payload->>'request_hash' IS DISTINCT FROM ledger.program_digest(binding->'request')
  OR consumed.context->>'request_hash' IS DISTINCT FROM initial->>'request_hash'
  OR marked.write_payload IS DISTINCT FROM consumed.context->'write'
  OR (marked.write_payload-'state') IS DISTINCT FROM (initial-'state') THEN
  RAISE EXCEPTION 'source must bind committed original consumption' USING ERRCODE='23514';
 END IF;
 IF p_payload#>'{normalized,fill,scope}' IS DISTINCT FROM p_payload->'scope'
  OR p_payload#>'{normalized,fill,order,scope}' IS DISTINCT FROM p_payload->'scope'
  OR p_payload#>'{normalized,economics,unit}' IS DISTINCT FROM bundle#>'{intent,unit}'
  OR p_payload#>>'{normalized,fill,native_trade_id}' IS NULL
  OR p_payload#>>'{normalized,fill,native_trade_id}' IS DISTINCT FROM p_payload#>>'{normalized,raw,fill_id}'
  OR p_payload#>>'{normalized,fill,evidence,hash}' IS DISTINCT FROM p_payload#>>'{normalized,raw,evidence,hash}'
  OR p_payload#>>'{verification,command_id}' IS DISTINCT FROM p_command
  OR p_payload#>'{verification,source_verified}' IS DISTINCT FROM 'true'::jsonb
  OR COALESCE(p_payload#>>'{verification,status}','') NOT IN ('VERIFIED','DISCREPANCY')
  OR NOT COALESCE(p_payload#>'{verification,fills}' @> jsonb_build_array(p_payload->'normalized'),false)
  OR jsonb_array_length(p_payload#>'{verification,raw_refs}')<1
  OR p_payload#>>'{verification,raw_report,request,command_id}' IS DISTINCT FROM p_command
  OR p_payload#>>'{verification,market_evidence,request_hash}' IS DISTINCT FROM initial->>'request_hash' THEN
  RAISE EXCEPTION 'source attestation normalized facts differ' USING ERRCODE='23514';
 END IF;
 approval_hash=ledger.program_digest(p_payload);
 identity=ledger.program_digest(jsonb_build_object('scope',p_payload->'scope',
  'venue',p_payload#>'{normalized,fill,venue}','native_trade_id',p_payload#>'{normalized,fill,native_trade_id}'));
 INSERT INTO ledger.program_fill_source_approvals(scope_id,command_id,approval_id,identity_hash,payload,payload_hash,transaction_id)
  VALUES(p_scope,p_command,approval_hash,identity,p_payload,approval_hash,p_transaction) ON CONFLICT DO NOTHING;
 RETURN approval_hash;
END $$;
REVOKE ALL ON FUNCTION ledger.approve_program_fill_source(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.approve_program_fill_source(text,text,jsonb,text) TO ledger_program_source_verifier;
COMMIT;
