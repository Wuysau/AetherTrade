-- Additive V2: one protected proof snapshot, small per-fill commitments.
-- 0016/V1 history and approval function remain readable and unchanged.
BEGIN;
CREATE TABLE ledger.program_fill_source_snapshots(
 scope_id text NOT NULL,command_id text NOT NULL,snapshot_id text NOT NULL,
 payload jsonb NOT NULL,payload_hash text NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope_id,snapshot_id),CHECK(snapshot_id=payload_hash),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.program_permit_consumptions(scope_id,command_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.program_fill_source_snapshots
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.program_fill_source_snapshots FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,
 ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;
GRANT SELECT ON ledger.program_fill_source_snapshots TO ledger_owner,ledger_program_source_verifier;

CREATE FUNCTION ledger.program_fill_source_context_current(p_scope text,p_command text,p jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT COALESCE(EXISTS(SELECT 1 FROM ledger.program_permit_consumptions c
 JOIN ledger.program_dispatch_marks m USING(scope_id,command_id)
 JOIN ledger.order_writes_v2 w ON w.scope_id=c.scope_id AND w.record_id=c.command_id AND w.sequence=1
 JOIN ledger.program_request_bindings b ON b.scope_id=c.scope_id AND b.record_id=c.command_id AND b.sequence=1
 WHERE c.scope_id=p_scope AND c.command_id=p_command
 AND p_scope=ledger.program_digest(p->'scope') AND p#>>'{scope,environment}'='PAPER'
 AND p->>'command_id'=p_command AND p->'scope'=w.payload->'scope'
 AND c.database_transaction<>pg_current_xact_id() AND m.database_transaction<>pg_current_xact_id()
 AND c.database_transaction<>m.database_transaction AND c.token_hash=m.token_hash
 AND p->>'consumption_hash'=ledger.program_digest(c.context)
 AND p->>'dispatch_mark_hash'=ledger.program_digest(m.context)
 AND p->>'original_write_hash'=ledger.program_digest(w.payload)
 AND p->>'binding_hash'=ledger.program_digest(b.payload) AND p->>'binding_hash'=w.payload->>'binding_hash'
 AND p->>'request_hash'=w.payload->>'request_hash' AND p->>'request_hash'=ledger.program_digest(b.payload->'request')
 AND c.context->>'request_hash'=w.payload->>'request_hash'
 AND m.write_payload=c.context->'write' AND (m.write_payload-'state')=(w.payload-'state')),false)
$$;
REVOKE ALL ON FUNCTION ledger.program_fill_source_context_current(text,text,jsonb) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver,ledger_program_source_verifier;

CREATE FUNCTION ledger.approve_program_fill_source_snapshot(p_scope text,p_command text,p_payload jsonb,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE group_name text; source_hash text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR p_payload->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR p_payload->>'record_kind' IS DISTINCT FROM 'PAPER_PROGRAM_FILL_SOURCE_SNAPSHOT_V1' THEN
  RAISE EXCEPTION 'invalid PAPER source snapshot' USING ERRCODE='23514';
 END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings WHERE environment='PAPER'
  AND account_id=p_payload#>>'{scope,venue_account_id}' AND product=p_payload#>>'{scope,product}' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment='PAPER' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND OR NOT ledger.program_fill_source_context_current(p_scope,p_command,p_payload) THEN
  RAISE EXCEPTION 'snapshot original consumption changed' USING ERRCODE='23514';
 END IF;
 IF p_payload#>>'{verification,command_id}' IS DISTINCT FROM p_command
  OR p_payload#>'{verification,source_verified}' IS DISTINCT FROM 'true'::jsonb
  OR COALESCE(p_payload#>>'{verification,status}','') NOT IN ('VERIFIED','DISCREPANCY')
  OR p_payload#>>'{verification,raw_report,request,command_id}' IS DISTINCT FROM p_command
  OR p_payload#>>'{verification,market_evidence,request_hash}' IS DISTINCT FROM p_payload->>'request_hash'
  OR jsonb_typeof(p_payload->'normalized_fills') IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_payload#>'{verification,fills}') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_payload#>'{verification,raw_refs}') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_payload#>'{verification,raw_refs}')<1
  OR (SELECT count(*) FROM jsonb_each(p_payload->'normalized_fills'))
    IS DISTINCT FROM jsonb_array_length(p_payload#>'{verification,fills}')::bigint
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload#>'{verification,fills}') f
     WHERE p_payload->'normalized_fills'->ledger.program_digest(f) IS DISTINCT FROM f) THEN
  RAISE EXCEPTION 'snapshot exact normalized fill set required' USING ERRCODE='23514';
 END IF;
 source_hash=ledger.program_digest(p_payload);
 INSERT INTO ledger.program_fill_source_snapshots(scope_id,command_id,snapshot_id,payload,payload_hash,transaction_id)
  VALUES(p_scope,p_command,source_hash,p_payload,source_hash,p_transaction) ON CONFLICT DO NOTHING;
 RETURN source_hash;
END $$;
REVOKE ALL ON FUNCTION ledger.approve_program_fill_source_snapshot(text,text,jsonb,text) FROM PUBLIC,ledger_owner,
 ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_order_closure_resolver;
GRANT EXECUTE ON FUNCTION ledger.approve_program_fill_source_snapshot(text,text,jsonb,text) TO ledger_program_source_verifier;

CREATE FUNCTION ledger.approve_program_fill_source_v2(p_scope text,p_command text,p_snapshot text,p_normalized text,p_transaction text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE source jsonb; normalized jsonb; bundle jsonb; payload jsonb; identity text; approval_hash text;
BEGIN
 SELECT s.payload INTO source FROM ledger.program_fill_source_snapshots s
  WHERE s.scope_id=p_scope AND s.command_id=p_command AND s.snapshot_id=p_snapshot;
 IF source IS NULL OR p_transaction IS NULL OR p_transaction=''
  OR NOT ledger.program_fill_source_context_current(p_scope,p_command,source) THEN
  RAISE EXCEPTION 'protected source snapshot required' USING ERRCODE='23514';
 END IF;
 -- The source table is immutable and only the snapshot function can write it.
 -- Lookup a precommitted normalized hash instead of resending/scanning a report
 -- for every fill. Snapshot and per-fill approvals commit together here.
 normalized=source->'normalized_fills'->p_normalized;
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
