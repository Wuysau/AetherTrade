-- Protected materialized indices over unchanged source snapshots. No new cash
-- or order authority. Old snapshot/approval payloads and hashes are untouched.
BEGIN;
CREATE TABLE ledger.program_fill_source_headers(
 scope_id text NOT NULL,command_id text NOT NULL,snapshot_id text NOT NULL,
 context jsonb NOT NULL,leaf_count integer NOT NULL CHECK(leaf_count>=0),
 derivation_version text NOT NULL DEFAULT '2026-10-30',
 PRIMARY KEY(scope_id,snapshot_id),
 FOREIGN KEY(scope_id,snapshot_id) REFERENCES ledger.program_fill_source_snapshots(scope_id,snapshot_id));
CREATE TABLE ledger.program_fill_source_leaves(
 scope_id text NOT NULL,snapshot_id text NOT NULL,normalized_hash text NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(scope_id,snapshot_id,normalized_hash),
 FOREIGN KEY(scope_id,snapshot_id) REFERENCES ledger.program_fill_source_headers(scope_id,snapshot_id));
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['program_fill_source_headers','program_fill_source_leaves'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
  EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier',tab);
  EXECUTE format('GRANT SELECT ON ledger.%I TO ledger_owner,ledger_program_source_verifier',tab);
 END LOOP;
END $$;

CREATE FUNCTION ledger.materialize_program_fill_source(p_scope text,p_snapshot text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE source ledger.program_fill_source_snapshots%ROWTYPE; header jsonb;
BEGIN
 IF EXISTS(SELECT 1 FROM ledger.program_fill_source_headers WHERE scope_id=p_scope AND snapshot_id=p_snapshot) THEN RETURN; END IF;
 SELECT * INTO source FROM ledger.program_fill_source_snapshots WHERE scope_id=p_scope AND snapshot_id=p_snapshot;
 IF source.snapshot_id IS NULL OR ledger.program_digest(source.payload) IS DISTINCT FROM p_snapshot
  OR source.payload->>'record_kind' IS DISTINCT FROM 'PAPER_PROGRAM_FILL_SOURCE_SNAPSHOT_V1'
  OR source.payload#>>'{scope,environment}' IS DISTINCT FROM 'PAPER'
  OR source.payload->>'command_id' IS DISTINCT FROM source.command_id
  OR jsonb_typeof(source.payload->'normalized_fills') IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'immutable approved snapshot required for materialization' USING ERRCODE='23514';
 END IF;
 -- Remove the two unbounded proof bodies. The remaining context contains only
 -- immutable scope/original/consumption hashes, never an owner/book/ALLOW cache.
 header=source.payload-'verification'-'normalized_fills';
 INSERT INTO ledger.program_fill_source_headers(scope_id,command_id,snapshot_id,context,leaf_count)
  VALUES(p_scope,source.command_id,p_snapshot,header,(SELECT count(*) FROM jsonb_each(source.payload->'normalized_fills')));
 INSERT INTO ledger.program_fill_source_leaves(scope_id,snapshot_id,normalized_hash,payload)
  SELECT p_scope,p_snapshot,key,value FROM jsonb_each(source.payload->'normalized_fills');
END $$;
REVOKE ALL ON FUNCTION ledger.materialize_program_fill_source(text,text) FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;

-- Preserve the previous validator, including its hash/output semantics. Rename
-- retains its ACL, so revoke the runtime grant explicitly before wrapping it.
ALTER FUNCTION ledger.approve_program_fill_source_snapshot(text,text,jsonb,text)
 RENAME TO approve_program_fill_source_snapshot_v1;
REVOKE ALL ON FUNCTION ledger.approve_program_fill_source_snapshot_v1(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;
CREATE FUNCTION ledger.approve_program_fill_source_snapshot(p_scope text,p_command text,p_payload jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE source_hash text;
BEGIN
 source_hash=ledger.approve_program_fill_source_snapshot_v1(p_scope,p_command,p_payload,p_transaction);
 PERFORM ledger.materialize_program_fill_source(p_scope,source_hash);
 RETURN source_hash;
END $$;
REVOKE ALL ON FUNCTION ledger.approve_program_fill_source_snapshot(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.approve_program_fill_source_snapshot(text,text,jsonb,text) TO ledger_program_source_verifier;

-- Existing V2 snapshots receive new indices, never rewritten historical facts.
DO $$ DECLARE source record; BEGIN
 FOR source IN SELECT scope_id,snapshot_id FROM ledger.program_fill_source_snapshots LOOP
  PERFORM ledger.materialize_program_fill_source(source.scope_id,source.snapshot_id);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION ledger.approve_program_fill_source_v2(p_scope text,p_command text,p_snapshot text,p_normalized text,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE source jsonb; normalized jsonb; bundle jsonb; payload jsonb; identity text; approval_hash text;
BEGIN
 SELECT h.context,l.payload INTO source,normalized FROM ledger.program_fill_source_headers h
  JOIN ledger.program_fill_source_leaves l USING(scope_id,snapshot_id)
  WHERE h.scope_id=p_scope AND h.command_id=p_command AND h.snapshot_id=p_snapshot AND l.normalized_hash=p_normalized;
 IF source IS NULL OR p_transaction IS NULL OR p_transaction=''
  OR NOT ledger.program_fill_source_context_current(p_scope,p_command,source) THEN
  RAISE EXCEPTION 'protected source snapshot required' USING ERRCODE='23514';
 END IF;
 SELECT f.payload INTO bundle FROM ledger.audit_events f JOIN ledger.order_writes_v2 w
  ON w.scope_id=f.scope_id AND f.record_id='program-financial-bundle:'||(w.payload->>'intent_id') AND f.sequence=1
  WHERE w.scope_id=p_scope AND w.record_id=p_command AND w.sequence=1;
 IF normalized IS NULL OR ledger.program_digest(normalized) IS DISTINCT FROM p_normalized
  OR normalized#>'{fill,scope}' IS DISTINCT FROM source->'scope'
  OR normalized#>'{fill,order,scope}' IS DISTINCT FROM source->'scope'
  OR bundle IS NULL OR normalized#>'{economics,unit}' IS DISTINCT FROM bundle#>'{intent,unit}'
  OR normalized#>>'{fill,native_trade_id}' IS NULL
  OR normalized#>>'{fill,native_trade_id}' IS DISTINCT FROM normalized#>>'{raw,fill_id}'
  OR normalized#>>'{fill,evidence,hash}' IS DISTINCT FROM normalized#>>'{raw,evidence,hash}' THEN
  RAISE EXCEPTION 'snapshot normalized native facts differ' USING ERRCODE='23514';
 END IF;
 payload=jsonb_build_object('contract_version','2026-10-30','record_kind','PAPER_PROGRAM_FILL_SOURCE_V2',
  'scope',source->'scope','command_id',p_command,'source_snapshot_hash',p_snapshot,'normalized_hash',p_normalized);
 approval_hash=ledger.program_digest(payload);
 identity=ledger.program_digest(jsonb_build_object('scope',source->'scope',
  'venue',normalized#>'{fill,venue}','native_trade_id',normalized#>'{fill,native_trade_id}'));
 INSERT INTO ledger.program_fill_source_approvals(scope_id,command_id,approval_id,identity_hash,payload,payload_hash,transaction_id)
  VALUES(p_scope,p_command,approval_hash,identity,payload,approval_hash,p_transaction) ON CONFLICT DO NOTHING;
 RETURN approval_hash;
END $$;
REVOKE ALL ON FUNCTION ledger.approve_program_fill_source_v2(text,text,text,text,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.approve_program_fill_source_v2(text,text,text,text,text) TO ledger_program_source_verifier;
COMMIT;
