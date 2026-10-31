-- Historical submission queries only. No order, cash, ownership or release grant.
BEGIN;
CREATE TABLE ledger.program_submission_observations(
 sequence bigserial NOT NULL,scope_id text NOT NULL,command_id text NOT NULL,
 probe_id text NOT NULL,phase text NOT NULL CHECK(phase IN ('STARTED','COMPLETED')),
 state text NOT NULL CHECK(state IN ('ACCEPTED','REJECTED','UNKNOWN')),
 observation_id text NOT NULL,payload jsonb NOT NULL,payload_hash text NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope_id,observation_id),UNIQUE(scope_id,command_id,probe_id,phase),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.program_permit_consumptions(scope_id,command_id),
 CHECK(observation_id=payload_hash));
CREATE INDEX program_submission_latest ON ledger.program_submission_observations(scope_id,command_id,phase,sequence DESC);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.program_submission_observations
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.program_submission_observations FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;
REVOKE ALL ON SEQUENCE ledger.program_submission_observations_sequence_seq FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;
GRANT SELECT ON ledger.program_submission_observations TO ledger_owner,ledger_program_source_verifier,
 ledger_program_dispatch_verifier;

CREATE FUNCTION ledger.program_submission_context(p_scope text,p_command text,p jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE group_name text;
BEGIN
 IF p->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR p->>'record_kind' IS DISTINCT FROM 'PROGRAM_SUBMISSION_OBSERVATION_V1'
  OR p->>'command_id' IS DISTINCT FROM p_command OR p->>'probe_id' IS NULL
  OR length(p->>'probe_id') NOT BETWEEN 1 AND 128
  OR p#>>'{scope,environment}' IS DISTINCT FROM 'PAPER'
  OR NOT ledger.program_fill_source_context_current(p_scope,p_command,p)
  THEN RETURN false; END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings WHERE environment='PAPER'
  AND account_id=p#>>'{scope,venue_account_id}' AND product=p#>>'{scope,product}' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment='PAPER' AND group_id=group_name FOR UPDATE;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION ledger.program_submission_context(text,text,jsonb) FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;

CREATE FUNCTION ledger.begin_program_submission_probe(p_scope text,p_command text,p jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE identity text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR NOT ledger.program_submission_context(p_scope,p_command,p)
  OR p->>'phase' IS DISTINCT FROM 'STARTED' OR p->>'state' IS DISTINCT FROM 'UNKNOWN'
  OR p->'source_snapshot_hash' IS DISTINCT FROM 'null'::jsonb OR p->'manifest_ref' IS DISTINCT FROM 'null'::jsonb
  OR p->'members' IS DISTINCT FROM '[]'::jsonb OR p->'raw_refs' IS DISTINCT FROM '[]'::jsonb
  OR p->'reasons' IS DISTINCT FROM '["QUERY_IN_PROGRESS"]'::jsonb THEN
  RAISE EXCEPTION 'original committed submission probe required' USING ERRCODE='23514';
 END IF;
 identity=ledger.program_digest(p);
 INSERT INTO ledger.program_submission_observations(scope_id,command_id,probe_id,phase,state,observation_id,payload,payload_hash,transaction_id)
  VALUES(p_scope,p_command,p->>'probe_id','STARTED','UNKNOWN',identity,p,identity,p_transaction);
 RETURN identity;
END $$;
REVOKE ALL ON FUNCTION ledger.begin_program_submission_probe(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.begin_program_submission_probe(text,text,jsonb,text) TO ledger_program_source_verifier;

CREATE FUNCTION ledger.record_program_submission_observation(p_scope text,p_command text,p jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE started ledger.program_submission_observations; source ledger.program_fill_source_snapshots;
 identity text; expected_members jsonb; expected_state text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR NOT ledger.program_submission_context(p_scope,p_command,p)
  OR p->>'phase' IS DISTINCT FROM 'COMPLETED' OR coalesce(p->>'state','') NOT IN ('ACCEPTED','REJECTED','UNKNOWN') THEN
  RAISE EXCEPTION 'original committed submission observation required' USING ERRCODE='23514';
 END IF;
 SELECT * INTO started FROM ledger.program_submission_observations WHERE scope_id=p_scope AND command_id=p_command
  AND probe_id=p->>'probe_id' AND phase='STARTED';
 IF started.observation_id IS NULL OR started.database_transaction=pg_current_xact_id()
  OR (started.payload-ARRAY['phase','state','source_snapshot_hash','manifest_ref','raw_refs','members','observed_clock','reasons'])
   IS DISTINCT FROM (p-ARRAY['phase','state','source_snapshot_hash','manifest_ref','raw_refs','members','observed_clock','reasons']) THEN
  RAISE EXCEPTION 'independent committed query start required' USING ERRCODE='23514';
 END IF;
 IF p->>'state'='UNKNOWN' THEN
  IF p->'source_snapshot_hash' IS DISTINCT FROM 'null'::jsonb OR p->'members' IS DISTINCT FROM '[]'::jsonb
   OR jsonb_typeof(p->'reasons') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'reasons')<1 THEN
   RAISE EXCEPTION 'UNKNOWN cannot prove absent or known orders' USING ERRCODE='23514';
  END IF;
 ELSE
  SELECT * INTO source FROM ledger.program_fill_source_snapshots WHERE scope_id=p_scope AND command_id=p_command
   AND snapshot_id=p->>'source_snapshot_hash';
  IF source.snapshot_id IS NULL OR ledger.program_digest(source.payload) IS DISTINCT FROM source.snapshot_id
   OR NOT ledger.program_fill_source_context_current(p_scope,p_command,source.payload)
   OR source.payload#>'{verification,source_verified}' IS DISTINCT FROM 'true'::jsonb
   OR coalesce(source.payload#>>'{verification,status}','') NOT IN ('VERIFIED','DISCREPANCY')
   OR jsonb_typeof(source.payload#>'{verification,raw_report,orders}') IS DISTINCT FROM 'array'
   OR jsonb_array_length(source.payload#>'{verification,raw_report,orders}')<1
   OR p#>>'{observed_clock,clock_health}' IS DISTINCT FROM 'HEALTHY'
   OR p->'manifest_ref' IS NULL OR p->'manifest_ref'='null'::jsonb
   OR p#>>'{manifest_ref,environment}' IS DISTINCT FROM 'PAPER'
   OR p#>>'{manifest_ref,source}' IS DISTINCT FROM 'PAPER_MARKET_REPLAY_SYNTHETIC' THEN
   RAISE EXCEPTION 'known submission requires actual protected source snapshot' USING ERRCODE='23514';
  END IF;
  SELECT jsonb_agg(jsonb_build_object('node_id',o->'node_id','client_id',o->'client_id','venue_order_id',o->'venue_order_id',
    'namespace',o->'namespace','lifecycle',o->'status','revision',o->'lifecycle_revision') ORDER BY o->>'client_id'),
   CASE WHEN bool_and(o->>'status'='REJECTED') THEN 'REJECTED' ELSE 'ACCEPTED' END
   INTO expected_members,expected_state FROM jsonb_array_elements(source.payload#>'{verification,raw_report,orders}') o;
  IF expected_members IS DISTINCT FROM p->'members' OR expected_state IS DISTINCT FROM p->>'state'
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(expected_members) m WHERE coalesce(m->>'venue_order_id','')=''
     OR m->>'lifecycle' NOT IN ('NEW','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED'))
   OR jsonb_array_length(expected_members) IS DISTINCT FROM jsonb_array_length(source.payload#>'{verification,raw_report,request,assigned_ids,members}')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(expected_members) m WHERE NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(source.payload#>'{verification,raw_report,request,assigned_ids,members}') a
     WHERE a->>'node_id'=m->>'node_id' AND a->>'client_id'=m->>'client_id')) THEN
   RAISE EXCEPTION 'complete actual submission identities required' USING ERRCODE='23514';
  END IF;
 END IF;
 identity=ledger.program_digest(p);
 INSERT INTO ledger.program_submission_observations(scope_id,command_id,probe_id,phase,state,observation_id,payload,payload_hash,transaction_id)
  VALUES(p_scope,p_command,p->>'probe_id','COMPLETED',p->>'state',identity,p,identity,p_transaction);
 RETURN identity;
END $$;
REVOKE ALL ON FUNCTION ledger.record_program_submission_observation(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.record_program_submission_observation(text,text,jsonb,text) TO ledger_program_source_verifier;
COMMIT;
