-- Additive bounded quantity management. Original V4 bytes unchanged.
BEGIN;
CREATE OR REPLACE FUNCTION ledger.admit_management(s text,id text,b jsonb,c jsonb,tx text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE w jsonb;original jsonb;BEGIN
 w=b->'write';
 SELECT payload INTO original FROM ledger.order_writes_v2 WHERE scope_id=s AND record_id=w->>'original_command_id' AND sequence=1;
 IF w->>'command_id' IS DISTINCT FROM id OR w->>'state' IS DISTINCT FROM 'DURABLE_READY'
  OR original IS NULL OR original#>'{scope}' IS DISTINCT FROM w->'scope'
  OR original->>'program_id' IS DISTINCT FROM w->>'program_id'
  OR original->>'service_namespace' IS DISTINCT FROM w->>'service_namespace'
  OR original->>'owner_strategy_id' IS DISTINCT FROM w->>'owner_strategy_id'
  OR ledger.program_digest(original) IS DISTINCT FROM b->>'original_write_hash'
  OR ledger.program_digest(b->'plan') IS DISTINCT FROM w->>'plan_hash'
  OR ledger.program_digest(b#>'{plan,target}') IS DISTINCT FROM w->>'target_hash'
  OR ledger.program_digest(b->'request') IS DISTINCT FROM w->>'request_hash'
  OR encode(sha256(convert_to(b->>'target_source_json','UTF8')),'hex') IS DISTINCT FROM b->>'target_source_hash'
  OR (b->>'target_source_json')::jsonb#>>'{evidence,hash}' IS DISTINCT FROM b#>>'{plan,target,evidence,hash}'
  OR ledger.program_digest((b->>'risk_snapshot_json')::jsonb) IS DISTINCT FROM w->>'risk_hash'
  OR ledger.program_digest((b->>'funding_snapshot_json')::jsonb) IS DISTINCT FROM w->>'funding_hash'
  OR b->'opening_id' IS DISTINCT FROM c#>'{funding,opening_id}'
  OR b->'cash_revision' IS DISTINCT FROM c#>'{funding,cash_revision}'
  OR ledger.program_digest(jsonb_set(b,'{write,binding_hash}',to_jsonb(repeat('0',64)))) IS DISTINCT FROM w->>'binding_hash'
  OR b#>>'{plan,instruction,kind}' IS DISTINCT FROM w->>'operation'
  OR w->>'operation' NOT IN ('CANCEL','KEEP_PRIORITY')
  OR NOT EXISTS(SELECT FROM ledger.program_dispatch_marks WHERE scope_id=s AND command_id=w->>'original_command_id')
  OR NOT EXISTS(SELECT FROM ledger.program_client_identities WHERE scope_id=s AND command_id=w->>'original_command_id'
    AND client_id=w->>'original_client_id' AND service_namespace=w->>'service_namespace')
  OR NOT ledger.management_context_current(s,c,w) THEN
  RAISE EXCEPTION 'complete current management financial binding required' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT FROM ledger.management_bindings prior WHERE prior.scope_id=s
  AND prior.payload#>>'{write,original_client_id}'=w->>'original_client_id'
  AND NOT EXISTS(SELECT FROM ledger.management_unsent_aborts a WHERE a.scope_id=prior.scope_id AND a.command_id=prior.record_id)) THEN
  RAISE EXCEPTION 'existing management duty requires original-ID reconciliation' USING ERRCODE='23514'; END IF;
 INSERT INTO ledger.management_bindings VALUES(s,id,b,tx);
 INSERT INTO ledger.management_writes(scope_id,record_id,sequence,payload,transaction_id) VALUES(s,id,1,w,tx);
 INSERT INTO ledger.outbox(message_id,scope_id,aggregate_id,sequence,kind,transaction_id)
  VALUES('management-ready:'||id,s,id,1,'PROGRAM_MANAGEMENT_READY',tx);
END $$;

COMMIT;
