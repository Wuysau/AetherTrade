-- OP-B additive storage only. Migration administrator; never run against live accounts.
BEGIN;
DO $$
DECLARE tab text;
BEGIN
 FOREACH tab IN ARRAY ARRAY['order_programs','order_program_bindings','order_program_states',
  'order_source_observations','order_observation_dispositions','order_funding_links','order_child_links','order_trade_links','order_reconciliation_cases','order_writes_v2'] LOOP
  EXECUTE format('CREATE TABLE ledger.%I (scope_id text NOT NULL,record_id text NOT NULL,
   sequence bigint NOT NULL CHECK(sequence>0),payload jsonb NOT NULL CHECK(jsonb_typeof(payload)=''object''),
   transaction_id text NOT NULL,processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
   PRIMARY KEY(scope_id,record_id,sequence))',tab);
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I
   FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
  EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_decision,ledger_control',tab);
  EXECUTE format('GRANT SELECT,INSERT ON ledger.%I TO ledger_owner',tab);
 END LOOP;
END $$;
CREATE UNIQUE INDEX immutable_order_program ON ledger.order_programs(scope_id,record_id);
CREATE UNIQUE INDEX immutable_parent_authority ON ledger.order_program_bindings(scope_id,record_id);
CREATE UNIQUE INDEX unique_observation_disposition ON ledger.order_observation_dispositions(scope_id,record_id);
CREATE UNIQUE INDEX unique_native_parent ON ledger.order_program_bindings(scope_id,(payload->>'venue'),(payload->>'namespace'),(payload->>'parent_venue_id'));
CREATE UNIQUE INDEX unique_parent_command ON ledger.order_program_bindings(scope_id,(payload->>'parent_command_id'));
CREATE UNIQUE INDEX immutable_order_source ON ledger.order_source_observations(scope_id,record_id);
CREATE UNIQUE INDEX unique_parent_funding ON ledger.order_funding_links(scope_id,record_id);
CREATE UNIQUE INDEX unique_native_child ON ledger.order_child_links(scope_id,record_id);
CREATE UNIQUE INDEX unique_native_trade ON ledger.order_trade_links(scope_id,record_id);
CREATE FUNCTION ledger.check_order_write_v2() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior jsonb; prior_sequence bigint;
BEGIN
 IF NEW.payload->>'contract_version' IS DISTINCT FROM '2026-10-28' OR
    COALESCE(NEW.payload->>'origin','') NOT IN ('APPLICATION_ACTION','APPLICATION_GENERATED_CHILD') THEN
  RAISE EXCEPTION 'only versioned application writes may create commands' USING ERRCODE='23514';
 END IF;
 SELECT payload,sequence INTO prior,prior_sequence FROM ledger.order_writes_v2
   WHERE scope_id=NEW.scope_id AND record_id=NEW.record_id ORDER BY sequence DESC LIMIT 1;
 IF prior IS NULL AND (NEW.sequence<>1 OR NEW.payload->>'state' IS DISTINCT FROM 'DURABLE_READY') THEN
  RAISE EXCEPTION 'new write must be durable ready' USING ERRCODE='23514';
 END IF;
 IF prior IS NOT NULL AND ((prior-'state'-'durable_record_ref') IS DISTINCT FROM
    (NEW.payload-'state'-'durable_record_ref') OR NEW.sequence<>prior_sequence+1
    OR prior->>'state' IS DISTINCT FROM 'DURABLE_READY' OR NEW.payload->>'state' IS DISTINCT FROM 'MAY_HAVE_SENT') THEN
  RAISE EXCEPTION 'immutable write or invalid transition' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.check_order_write_v2() FROM PUBLIC;
CREATE TRIGGER order_write_guard BEFORE INSERT ON ledger.order_writes_v2 FOR EACH ROW EXECUTE FUNCTION ledger.check_order_write_v2();
COMMIT;
