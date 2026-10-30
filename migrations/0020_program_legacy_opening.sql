-- An explicit authority attests an old initialization; audit JSON is not capital.
-- Old seed payload/hash remain untouched. No runtime role is enrolled.
BEGIN;
CREATE TABLE ledger.program_legacy_account_openings(
 account_key text PRIMARY KEY,payload jsonb NOT NULL,payload_hash text NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),processed_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.program_legacy_account_openings
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.program_legacy_account_openings FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_source_verifier,ledger_program_dispatch_verifier,ledger_program_account_provisioner;
GRANT SELECT ON ledger.program_legacy_account_openings TO ledger_owner,ledger_program_dispatch_verifier,ledger_program_account_provisioner;
GRANT SELECT ON ledger.audit_events TO ledger_program_account_provisioner;

-- Existing historical seeds still require explicit provisioner review. Future
-- ordinary audit writers cannot create an initialization that resembles one.
CREATE FUNCTION ledger.guard_console_seed_insert() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,ledger AS $$
BEGIN
 IF NEW.record_id='console:seed' AND current_user<>pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
  RAISE EXCEPTION 'CONSOLE_SEED_PROVISIONER_ONLY' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.guard_console_seed_insert() FROM PUBLIC;
CREATE TRIGGER console_seed_provisioner_only BEFORE INSERT ON ledger.audit_events
 FOR EACH ROW EXECUTE FUNCTION ledger.guard_console_seed_insert();

CREATE FUNCTION ledger.attest_program_legacy_opening(p_account text,p_payload jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE account jsonb; sid text; seed jsonb; seed_count bigint; group_name text; prior ledger.program_legacy_account_openings%ROWTYPE;
BEGIN
 account=jsonb_build_object('environment','PAPER','venue_account_id','paper-local','product','SPOT');
 sid=ledger.program_digest(account||jsonb_build_object('instrument_uid','console-paper:BTCUSDT','venue_leg','BOTH'));
 SELECT count(*) INTO seed_count FROM ledger.audit_events WHERE record_id='console:seed';
 IF seed_count<>1 THEN RAISE EXCEPTION 'AMBIGUOUS_LEGACY_SEED' USING ERRCODE='23514'; END IF;
 SELECT payload INTO seed FROM ledger.audit_events WHERE record_id='console:seed' AND scope_id=sid AND sequence=1;
 IF seed IS NULL OR seed->>'source' IS DISTINCT FROM 'EXPLICIT_NEW_VIRTUAL_CAPITAL'
  OR (SELECT count(*) FROM jsonb_object_keys(seed))<>2 OR NOT seed ? 'initial_cash'
  OR jsonb_typeof(seed->'initial_cash') IS DISTINCT FROM 'string'
  OR COALESCE(seed->>'initial_cash','')!~'^[0-9]+(\.[0-9]+)?$' THEN
  RAISE EXCEPTION 'LEGACY_SEED_BINDING_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings
  WHERE environment='PAPER' AND account_id='paper-local' AND product='SPOT' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment='PAPER' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND OR group_name IS DISTINCT FROM 'console-paper-collateral'
  OR p_account IS DISTINCT FROM ledger.program_digest(account) OR p_payload->'account' IS DISTINCT FROM account
  OR p_payload->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR p_payload->>'record_kind' IS DISTINCT FROM 'PROGRAM_LEGACY_ACCOUNT_OPENING_V1'
  OR p_payload->>'source_kind' IS DISTINCT FROM 'EXPLICIT_LEGACY_CONSOLE_INITIALIZATION_ATTESTATION'
  OR p_payload->>'collateral_group_id' IS DISTINCT FROM group_name OR p_payload->>'seed_scope_id' IS DISTINCT FROM sid
  OR p_payload->>'seed_hash' IS DISTINCT FROM ledger.program_digest(seed)
  OR p_payload->'balances' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('currency','USDT','amount',seed->>'initial_cash'))
  OR jsonb_typeof(p_payload->'as_of') IS DISTINCT FROM 'string'
  OR p_payload->>'as_of' IS DISTINCT FROM p_payload#>>'{evidence,visible_at}'
  OR (p_payload->>'as_of')::timestamptz>clock_timestamp()
  OR p_payload#>>'{evidence,environment}' IS DISTINCT FROM 'PAPER'
  OR COALESCE(p_payload#>>'{evidence,hash}','')!~'^[0-9a-f]{64}$'
  OR COALESCE(p_payload#>>'{evidence,id}','')='' OR COALESCE(p_payload#>>'{evidence,source}','')=''
  OR p_transaction IS NULL OR p_transaction=''
  OR EXISTS(SELECT 1 FROM ledger.program_account_openings WHERE account_key=p_account) THEN
  RAISE EXCEPTION 'LEGACY_SEED_ATTESTATION_BINDING_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT * INTO prior FROM ledger.program_legacy_account_openings WHERE account_key=p_account;
 IF prior.account_key IS NOT NULL THEN
  IF prior.payload IS DISTINCT FROM p_payload OR prior.payload_hash IS DISTINCT FROM ledger.program_digest(p_payload) THEN
   RAISE EXCEPTION 'LEGACY_SEED_ATTESTATION_IMMUTABLE' USING ERRCODE='23514'; END IF;
  RETURN prior.payload_hash;
 END IF;
 INSERT INTO ledger.program_legacy_account_openings(account_key,payload,payload_hash,transaction_id)
  VALUES(p_account,p_payload,ledger.program_digest(p_payload),p_transaction);
 RETURN ledger.program_digest(p_payload);
END $$;
REVOKE ALL ON FUNCTION ledger.attest_program_legacy_opening(text,jsonb,text) FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_source_verifier,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.attest_program_legacy_opening(text,jsonb,text) TO ledger_program_account_provisioner;
COMMIT;
