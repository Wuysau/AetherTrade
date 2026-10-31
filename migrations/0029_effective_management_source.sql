-- Independent historical source authority; dispatch consumption is not application.
BEGIN;
CREATE TABLE ledger.management_effective_attestations_v1(
 scope_id text NOT NULL,command_id text NOT NULL,source_hash text NOT NULL,payload jsonb NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id,source_hash),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.management_permit_consumptions);
CREATE TABLE ledger.management_effective_confirmations_v1(
 scope_id text NOT NULL,command_id text NOT NULL,source_hash text NOT NULL,
 PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,command_id,source_hash) REFERENCES ledger.management_effective_attestations_v1);
DO $$ DECLARE tab text;BEGIN
 FOREACH tab IN ARRAY ARRAY['management_effective_attestations_v1','management_effective_confirmations_v1'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
 EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_management_dispatch_verifier,ledger_program_source_verifier',tab);
 EXECUTE format('GRANT SELECT ON ledger.%I TO ledger_owner,ledger_management_dispatch_verifier,ledger_program_source_verifier',tab);
 END LOOP;
END $$;
GRANT SELECT ON ledger.management_bindings,ledger.management_writes,ledger.management_permit_consumptions,
 ledger.management_dispatch_marks TO ledger_program_source_verifier;
CREATE FUNCTION ledger.attest_effective_management_source_v1(s text,id text,p jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE b jsonb;r jsonb;src jsonb;entry jsonb;consumed ledger.management_permit_consumptions;h text;BEGIN
 SELECT payload INTO b FROM ledger.management_bindings WHERE scope_id=s AND record_id=id;
 SELECT * INTO consumed FROM ledger.management_permit_consumptions WHERE scope_id=s AND command_id=id;
 r=p->'receipt';src=p->'source';h=ledger.program_digest(p);
 SELECT e INTO entry FROM jsonb_array_elements(src->'effective_history') e WHERE e->>'command_id'=id;
 IF b IS NULL OR consumed.command_id IS NULL OR consumed.database_transaction=pg_current_xact_id()
 OR p->>'consumption_hash' IS DISTINCT FROM ledger.program_digest(consumed.context)
 OR b#>>'{write,operation}' IS DISTINCT FROM 'KEEP_PRIORITY'
 OR p->>'source_kind' IS DISTINCT FROM 'PAPER_MANAGED_CREATION_SOURCE_V1'
 OR src->>'record_kind' IS DISTINCT FROM 'PAPER_MANAGED_CREATION_REPORT_V1'
 OR src->>'contract_version' IS DISTINCT FROM '2026-10-30.managed-v1'
 OR src#>>'{evidence,source}' IS DISTINCT FROM 'PAPER_MANAGED_CREATION_SOURCE_V1'
 OR src->>'command_id' IS DISTINCT FROM b#>>'{write,original_command_id}'
 OR src#>'{request,scope}' IS DISTINCT FROM b#>'{write,scope}'
 OR src#>>'{request,command_id}' IS DISTINCT FROM b#>>'{write,original_command_id}'
 OR src#>>'{evidence,hash}' IS DISTINCT FROM ledger.program_digest(jsonb_build_object(
 'base',(src-'evidence'-'effective_history')||jsonb_build_object('contract_version','2026-10-30','record_kind','PAPER_CREATION_REPORT_V2'),
 'history',src->'effective_history','record_kind','PAPER_MANAGED_CREATION_REPORT_V1'))
 OR entry IS NULL OR (entry->>'receipt_json')::jsonb IS DISTINCT FROM r
 OR ((entry->>'binding_json')::jsonb#>'{write}')-'state' IS DISTINCT FROM (b->'write')-'state'
 OR r->>'record_kind' IS DISTINCT FROM 'PAPER_KEEP_PRIORITY_RECEIPT_V1'
 OR r->>'command_id' IS DISTINCT FROM id OR r->>'binding_hash' IS DISTINCT FROM b#>>'{write,binding_hash}'
 OR r->>'original_client_id' IS DISTINCT FROM b#>>'{write,original_client_id}'
 OR r->>'target_venue_id' IS DISTINCT FROM b#>>'{write,target_venue_id}'
 OR (r->>'effective_quantity')::numeric IS DISTINCT FROM (b#>>'{plan,instruction,new_quantity}')::numeric
 OR r->'funds_released' IS DISTINCT FROM 'false'::jsonb OR r->'children_terminal' IS DISTINCT FROM 'false'::jsonb THEN
 RAISE EXCEPTION 'independent complete managed source required';END IF;
 INSERT INTO ledger.management_effective_attestations_v1(scope_id,command_id,source_hash,payload) VALUES(s,id,h,p) ON CONFLICT DO NOTHING;
 RETURN h;
END $$;
REVOKE ALL ON FUNCTION ledger.attest_effective_management_source_v1(text,text,jsonb) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_management_dispatch_verifier;
GRANT EXECUTE ON FUNCTION ledger.attest_effective_management_source_v1(text,text,jsonb) TO ledger_program_source_verifier;
CREATE OR REPLACE FUNCTION ledger.resolve_effective_management_v1(s text,id text,p jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE h text;BEGIN
 h=ledger.program_digest(p);
 IF NOT EXISTS(SELECT FROM ledger.management_effective_attestations_v1 a WHERE a.scope_id=s AND a.command_id=id
 AND a.source_hash=h AND a.payload=p AND a.database_transaction<>pg_current_xact_id()) THEN
 RAISE EXCEPTION 'committed independent managed source attestation required';END IF;
 INSERT INTO ledger.management_effective_resolutions_v1 VALUES(s,id,p) ON CONFLICT DO NOTHING;
 INSERT INTO ledger.management_effective_confirmations_v1 VALUES(s,id,h) ON CONFLICT DO NOTHING;
END $$;
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
  AND NOT EXISTS(SELECT FROM ledger.management_effective_confirmations_v1 r JOIN ledger.management_effective_attestations_v1 a USING(scope_id,command_id,source_hash) WHERE r.scope_id=prior.scope_id AND r.command_id=prior.record_id)
  AND NOT EXISTS(SELECT FROM ledger.management_unsent_aborts a WHERE a.scope_id=prior.scope_id AND a.command_id=prior.record_id)) THEN
  RAISE EXCEPTION 'existing management duty requires original-ID reconciliation' USING ERRCODE='23514'; END IF;
 INSERT INTO ledger.management_bindings VALUES(s,id,b,tx);
 INSERT INTO ledger.management_writes(scope_id,record_id,sequence,payload,transaction_id) VALUES(s,id,1,w,tx);
 INSERT INTO ledger.outbox(message_id,scope_id,aggregate_id,sequence,kind,transaction_id)
  VALUES('management-ready:'||id,s,id,1,'PROGRAM_MANAGEMENT_READY',tx);
END $$;

COMMIT;
