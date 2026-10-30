-- Explicit initial PAPER asset proof. This is not a running balance table.
-- No runtime login is enrolled and an existing traded account cannot be reset.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_account_provisioner') THEN
  CREATE ROLE ledger_program_account_provisioner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
   NOINHERIT NOREPLICATION NOBYPASSRLS;
 ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_account_provisioner' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) OR
  EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_account_provisioner')
   OR roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_account_provisioner')) THEN
  RAISE EXCEPTION 'unsafe pre-existing PAPER account provisioner role';
 END IF;
END $$;
CREATE TABLE ledger.program_account_openings(
 account_key text PRIMARY KEY,environment text NOT NULL CHECK(environment='PAPER'),account_id text NOT NULL,product text NOT NULL,
 collateral_group_id text NOT NULL,payload jsonb NOT NULL,payload_hash text NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(environment,account_id,product));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.program_account_openings
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.program_account_openings FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_dispatch_verifier,ledger_program_source_verifier,ledger_order_closure_resolver,ledger_program_account_provisioner;
GRANT SELECT ON ledger.program_account_openings TO ledger_owner,ledger_program_dispatch_verifier,ledger_program_account_provisioner;
GRANT USAGE ON SCHEMA ledger TO ledger_program_account_provisioner;
GRANT SELECT ON ledger.account_bindings,ledger.collateral_mutex TO ledger_program_account_provisioner;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_program_account_provisioner;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_program_account_provisioner;
GRANT INSERT ON ledger.transaction_observations TO ledger_program_account_provisioner;
-- Current dispatch financial readers need historical source SELECTs, not source
-- approval execution or INSERT. Earlier tables predate dispatch role grants.
GRANT SELECT ON ledger.program_fill_source_approvals,ledger.program_fill_source_snapshots,
 ledger.program_fill_source_headers,ledger.program_fill_source_leaves TO ledger_program_dispatch_verifier;

CREATE FUNCTION ledger.provision_program_account_opening(p_account text,p_payload jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE group_name text; prior ledger.program_account_openings%ROWTYPE; digest text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR p_payload->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR p_payload->>'record_kind' IS DISTINCT FROM 'PROGRAM_ACCOUNT_OPENING_V1'
  OR p_payload->>'source_kind' IS DISTINCT FROM 'EXPLICIT_NEW_PAPER_VIRTUAL_CAPITAL'
  OR p_payload#>>'{account,environment}' IS DISTINCT FROM 'PAPER'
  OR COALESCE(p_payload#>>'{account,product}','') NOT IN ('SPOT','USD_M_PERP','US_EQUITY')
  OR COALESCE(p_payload#>>'{account,venue_account_id}','')=''
  OR p_account IS DISTINCT FROM ledger.program_digest(p_payload->'account')
  OR p_payload#>>'{evidence,environment}' IS DISTINCT FROM 'PAPER'
  OR COALESCE(p_payload#>>'{evidence,id}','')='' OR COALESCE(p_payload#>>'{evidence,source}','')=''
  OR COALESCE(p_payload#>>'{evidence,hash}','')!~'^[0-9a-f]{64}$'
  OR jsonb_typeof(p_payload->'as_of') IS DISTINCT FROM 'string'
  OR jsonb_typeof(p_payload#>'{evidence,visible_at}') IS DISTINCT FROM 'string'
  OR p_payload->>'as_of' IS DISTINCT FROM p_payload#>>'{evidence,visible_at}'
  OR (p_payload->>'as_of')::timestamptz>clock_timestamp()
  OR jsonb_typeof(p_payload->'balances') IS DISTINCT FROM 'array' OR jsonb_array_length(p_payload->'balances')<1 THEN
  RAISE EXCEPTION 'INVALID_EXPLICIT_PAPER_OPENING' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'balances') b WHERE jsonb_typeof(b->'amount') IS DISTINCT FROM 'string'
   OR jsonb_typeof(b->'currency') IS DISTINCT FROM 'string'
   OR COALESCE(b->>'amount','')!~'^[0-9]+(\.[0-9]+)?$' OR COALESCE(b->>'currency','')='')
  OR (SELECT count(DISTINCT b->>'currency') FROM jsonb_array_elements(p_payload->'balances') b)
    <>jsonb_array_length(p_payload->'balances') THEN
  RAISE EXCEPTION 'EXPLICIT_UNIQUE_NONNEGATIVE_INITIAL_ASSETS_REQUIRED' USING ERRCODE='23514';
 END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings
  WHERE environment='PAPER' AND account_id=p_payload#>>'{account,venue_account_id}' AND product=p_payload#>>'{account,product}' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment='PAPER' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND OR group_name IS DISTINCT FROM p_payload->>'collateral_group_id' THEN
  RAISE EXCEPTION 'OPENING_ACCOUNT_BINDING_REQUIRED' USING ERRCODE='23514';
 END IF;
 digest=ledger.program_digest(p_payload);
 SELECT * INTO prior FROM ledger.program_account_openings WHERE account_key=p_account;
 IF prior.account_key IS NOT NULL THEN
  IF prior.payload IS DISTINCT FROM p_payload OR prior.payload_hash IS DISTINCT FROM digest THEN
   RAISE EXCEPTION 'OPENING_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  RETURN prior.payload_hash;
 END IF;
 IF EXISTS(SELECT 1 FROM ledger.intents i WHERE COALESCE(i.payload#>>'{scope,environment}',i.payload->>'environment')='PAPER'
   AND COALESCE(i.payload#>>'{scope,venue_account_id}',i.payload->>'venue_account_id')=p_payload#>>'{account,venue_account_id}'
   AND COALESCE(i.payload#>>'{scope,product}',i.payload->>'product')=p_payload#>>'{account,product}')
  OR EXISTS(SELECT 1 FROM ledger.fills f WHERE f.environment='PAPER' AND f.account_id=p_payload#>>'{account,venue_account_id}'
   AND f.product=p_payload#>>'{account,product}')
  OR EXISTS(SELECT 1 FROM ledger.program_request_bindings b WHERE b.payload#>>'{scope,environment}'='PAPER'
   AND b.payload#>>'{scope,venue_account_id}'=p_payload#>>'{account,venue_account_id}'
   AND b.payload#>>'{scope,product}'=p_payload#>>'{account,product}') THEN
  RAISE EXCEPTION 'OPENING_REQUIRES_FRESH_ACCOUNT' USING ERRCODE='23514';
 END IF;
 INSERT INTO ledger.program_account_openings(account_key,environment,account_id,product,collateral_group_id,payload,payload_hash,transaction_id)
  VALUES(p_account,'PAPER',p_payload#>>'{account,venue_account_id}',p_payload#>>'{account,product}',group_name,p_payload,digest,p_transaction);
 RETURN digest;
END $$;
REVOKE ALL ON FUNCTION ledger.provision_program_account_opening(text,jsonb,text) FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_dispatch_verifier,ledger_program_source_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.provision_program_account_opening(text,jsonb,text) TO ledger_program_account_provisioner;
COMMIT;
