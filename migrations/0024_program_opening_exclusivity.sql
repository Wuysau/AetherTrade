-- Preserve 0019/0020 proof bytes. Future initializers cannot create two origins.
BEGIN;
LOCK TABLE ledger.program_account_openings,ledger.program_legacy_account_openings IN SHARE ROW EXCLUSIVE MODE;
CREATE FUNCTION ledger.guard_program_opening_exclusivity() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE account jsonb:=NEW.payload->'account'; group_name text;
BEGIN
 IF TG_TABLE_SCHEMA<>'ledger' OR TG_TABLE_NAME NOT IN ('program_account_openings','program_legacy_account_openings')
  OR NEW.account_key IS DISTINCT FROM ledger.program_digest(account) THEN
  RAISE EXCEPTION 'OPENING_EXCLUSIVITY_ACCOUNT_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings
  WHERE environment=account->>'environment' AND account_id=account->>'venue_account_id' AND product=account->>'product' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment=account->>'environment' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND OR group_name IS DISTINCT FROM NEW.payload->>'collateral_group_id' THEN
  RAISE EXCEPTION 'OPENING_EXCLUSIVITY_GROUP_REQUIRED' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='program_account_openings' AND EXISTS(
   SELECT 1 FROM ledger.program_legacy_account_openings WHERE account_key=NEW.account_key)
  OR TG_TABLE_NAME='program_legacy_account_openings' AND EXISTS(
   SELECT 1 FROM ledger.program_account_openings WHERE account_key=NEW.account_key) THEN
  RAISE EXCEPTION 'ACCOUNT_OPENING_ALREADY_ATTESTED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.guard_program_opening_exclusivity() FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_source_verifier,ledger_program_dispatch_verifier,ledger_program_account_provisioner;
CREATE TRIGGER account_opening_exclusivity BEFORE INSERT ON ledger.program_account_openings
 FOR EACH ROW EXECUTE FUNCTION ledger.guard_program_opening_exclusivity();
CREATE TRIGGER account_opening_exclusivity BEFORE INSERT ON ledger.program_legacy_account_openings
 FOR EACH ROW EXECUTE FUNCTION ledger.guard_program_opening_exclusivity();
COMMIT;
