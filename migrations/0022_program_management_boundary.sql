-- Business-ledger-only management V4. No creation history or monetary mutation.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_management_dispatch_verifier') THEN
  CREATE ROLE ledger_management_dispatch_verifier NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
 ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_management_dispatch_verifier' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) OR
  EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='ledger_management_dispatch_verifier')
   OR roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_management_dispatch_verifier')) THEN
  RAISE EXCEPTION 'unsafe management verifier role';
 END IF;
END $$;
GRANT USAGE ON SCHEMA ledger TO ledger_management_dispatch_verifier;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO ledger_management_dispatch_verifier;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_management_dispatch_verifier;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_management_dispatch_verifier;
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_management_dispatch_verifier;
GRANT INSERT ON ledger.transaction_observations TO ledger_management_dispatch_verifier;

CREATE TABLE ledger.management_bindings(
 scope_id text NOT NULL,record_id text NOT NULL UNIQUE,payload jsonb NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),PRIMARY KEY(scope_id,record_id));
CREATE TABLE ledger.management_writes(
 scope_id text NOT NULL,record_id text NOT NULL,sequence bigint NOT NULL CHECK(sequence BETWEEN 1 AND 3),
 payload jsonb NOT NULL,transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,record_id,sequence),FOREIGN KEY(scope_id,record_id) REFERENCES ledger.management_bindings);
CREATE TABLE ledger.management_dispatch_marks(
 scope_id text NOT NULL,command_id text NOT NULL,context jsonb NOT NULL,token_hash text NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),FOREIGN KEY(scope_id,command_id) REFERENCES ledger.management_bindings);
CREATE TABLE ledger.management_permit_consumptions(
 scope_id text NOT NULL,command_id text NOT NULL,token_hash text NOT NULL,context jsonb NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),FOREIGN KEY(scope_id,command_id) REFERENCES ledger.management_dispatch_marks);
CREATE TABLE ledger.management_outcomes(
 scope_id text NOT NULL,command_id text NOT NULL,source_hash text NOT NULL,payload jsonb NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id,source_hash),FOREIGN KEY(scope_id,command_id) REFERENCES ledger.management_bindings);
CREATE TABLE ledger.management_unsent_aborts(
 scope_id text NOT NULL,command_id text NOT NULL,payload jsonb NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.management_bindings);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['management_bindings','management_writes','management_dispatch_marks','management_permit_consumptions','management_outcomes','management_unsent_aborts'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
  EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier,ledger_management_dispatch_verifier',tab);
  EXECUTE format('GRANT SELECT ON ledger.%I TO ledger_owner,ledger_management_dispatch_verifier',tab);
 END LOOP;
END $$;
CREATE FUNCTION ledger.record_management_outcome(s text,id text,p jsonb,tx text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE w jsonb;BEGIN
 SELECT payload INTO w FROM ledger.management_writes WHERE scope_id=s AND record_id=id ORDER BY sequence DESC LIMIT 1;
 IF w->>'state' NOT IN ('MAY_HAVE_SENT','UNKNOWN') OR w->>'binding_hash' IS DISTINCT FROM p->>'binding_hash'
  OR p->>'command_id' IS DISTINCT FROM id OR p->'scope' IS DISTINCT FROM w->'scope'
  OR p->>'original_client_id' IS DISTINCT FROM w->>'original_client_id'
  OR p->>'target_venue_id' IS DISTINCT FROM w->>'target_venue_id'
  OR p->'funds_released' IS DISTINCT FROM 'false'::jsonb OR p->'children_terminal' IS DISTINCT FROM 'false'::jsonb
  OR encode(sha256(convert_to(p->>'raw_payload_json','UTF8')),'hex') IS DISTINCT FROM p->>'source_hash' THEN
  RAISE EXCEPTION 'source-bound management observation required' USING ERRCODE='23514';END IF;
 INSERT INTO ledger.management_outcomes(scope_id,command_id,source_hash,payload,transaction_id)
  VALUES(s,id,p->>'source_hash',p,tx) ON CONFLICT DO NOTHING;
 IF p->>'cancel_result'='UNKNOWN' AND w->>'state'='MAY_HAVE_SENT' THEN
  INSERT INTO ledger.management_writes(scope_id,record_id,sequence,payload,transaction_id)
   VALUES(s,id,3,jsonb_set(w,'{state}','"UNKNOWN"'),tx);
 END IF;
END $$;
REVOKE ALL ON FUNCTION ledger.record_management_outcome(text,text,jsonb,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier;
GRANT EXECUTE ON FUNCTION ledger.record_management_outcome(text,text,jsonb,text) TO ledger_management_dispatch_verifier;
ALTER TABLE ledger.outbox DROP CONSTRAINT outbox_kind_check;
ALTER TABLE ledger.outbox ADD CONSTRAINT outbox_kind_check CHECK(kind IN ('COMMAND_READY','DOMAIN_EVENT','PROGRAM_COMMAND_READY','PROGRAM_MANAGEMENT_READY'));

CREATE FUNCTION ledger.management_context_current(s text,c jsonb,w jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT coalesce(c->'write'=w AND w->>'contract_version'='2026-10-30.management-v4'
 AND w->>'record_kind'='PROGRAM_MANAGEMENT_WRITE_V4' AND w#>>'{scope,environment}'='PAPER'
 AND c#>>'{clock,clock_health}'='HEALTHY' AND (c#>>'{clock,monotonic_ns}')::bigint>=0
 AND (c#>>'{clock,monotonic_ns}')::bigint<(c#>>'{lease,deadline_monotonic_ns}')::bigint
 AND (c#>>'{clock,utc_now}')::timestamptz>=(w->>'created_at')::timestamptz
 AND (c#>>'{clock,utc_now}')::timestamptz<(w->>'valid_until')::timestamptz
 AND (c#>>'{clock,utc_now}')::timestamptz<(c->>'valid_until')::timestamptz
 AND (c#>>'{clock,utc_now}')::timestamptz<(c#>>'{lease,expires_at}')::timestamptz
 AND c#>>'{lease,instance_id}'<>'' AND c#>>'{lease,shard,node_id}'<>''
 AND c#>>'{lease,shard,environment}'=w#>>'{scope,environment}'
 AND c#>>'{lease,shard,venue_account_id}'=w#>>'{scope,venue_account_id}'
 AND c#>>'{lease,shard,product}'=w#>>'{scope,product}'
 AND c#>>'{lease,shard,execution_epoch}'=w->>'execution_epoch'
 AND c->>'request_hash'=w->>'request_hash'
 AND ledger.program_digest(c->'risk')=w->>'risk_hash'
 AND ledger.program_digest(c->'funding')=w->>'funding_hash'
 AND ledger.program_digest(c#>'{risk,target}')=w->>'target_hash'
 AND c#>>'{risk,authorization,allowed}'='true' AND c#>'{risk,locks}'='[]'::jsonb
 AND c->'fence'=(SELECT payload FROM ledger.audit_events WHERE scope_id=s AND record_id=c->>'fence_record_id' ORDER BY sequence DESC LIMIT 1)
 AND c#>>'{fence,enabled}'='true'
 AND EXISTS(SELECT FROM ledger.active_owners o WHERE o.environment=w#>>'{scope,environment}'
  AND o.account_id=w#>>'{scope,venue_account_id}' AND o.product=w#>>'{scope,product}'
  AND o.instrument_uid=w#>>'{scope,instrument_uid}' AND o.venue_leg=w#>>'{scope,venue_leg}'
  AND o.owner_strategy_id=w->>'owner_strategy_id' AND o.ownership_epoch=(w->>'ownership_epoch')::bigint
  AND o.policy_revision=(w->>'policy_revision')::bigint),false)
$$;

CREATE FUNCTION ledger.admit_management(s text,id text,b jsonb,c jsonb,tx text) RETURNS void
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
  OR w->>'operation'<>'CANCEL'
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

CREATE FUNCTION ledger.mark_management_may(s text,id text,c jsonb,token text,ttl integer,tx text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE w jsonb;BEGIN
 SELECT payload INTO w FROM ledger.management_writes WHERE scope_id=s AND record_id=id ORDER BY sequence DESC LIMIT 1;
 IF w->>'state' IS DISTINCT FROM 'DURABLE_READY' OR ttl NOT BETWEEN 1 AND 5000 OR length(token)<>64
  OR EXISTS(SELECT FROM ledger.management_unsent_aborts WHERE scope_id=s AND command_id=id)
  OR NOT ledger.management_context_current(s,c,w) THEN RAISE EXCEPTION 'current management required' USING ERRCODE='23514';END IF;
 INSERT INTO ledger.management_dispatch_marks VALUES(s,id,c,token,clock_timestamp()+ttl*interval '1 millisecond',tx);
 INSERT INTO ledger.management_writes(scope_id,record_id,sequence,payload,transaction_id)
  VALUES(s,id,2,jsonb_set(w,'{state}','"MAY_HAVE_SENT"'),tx);
END $$;

CREATE FUNCTION ledger.consume_management_permit(s text,id text,token text,c jsonb,tx text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE m ledger.management_dispatch_marks;w jsonb;BEGIN
 SELECT * INTO m FROM ledger.management_dispatch_marks WHERE scope_id=s AND command_id=id;
 SELECT payload INTO w FROM ledger.management_writes WHERE scope_id=s AND record_id=id ORDER BY sequence DESC LIMIT 1;
 IF m.command_id IS NULL OR m.database_transaction=pg_current_xact_id() OR m.expires_at<=clock_timestamp()
  OR m.token_hash IS DISTINCT FROM token OR w->>'state' IS DISTINCT FROM 'MAY_HAVE_SENT'
  OR c->'lease' IS DISTINCT FROM m.context->'lease' OR NOT ledger.management_context_current(s,c,w)
  OR (w-'state') IS DISTINCT FROM ((m.context->'write')-'state') THEN
  RAISE EXCEPTION 'committed unused current management permit required' USING ERRCODE='23514';END IF;
 INSERT INTO ledger.management_permit_consumptions VALUES(s,id,token,c,tx);
END $$;
CREATE FUNCTION ledger.mark_management_unknown(s text,id text,tx text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE w jsonb;BEGIN
 SELECT payload INTO w FROM ledger.management_writes WHERE scope_id=s AND record_id=id ORDER BY sequence DESC LIMIT 1;
 IF w->>'state' IS DISTINCT FROM 'MAY_HAVE_SENT' THEN RETURN;END IF;
 INSERT INTO ledger.management_writes(scope_id,record_id,sequence,payload,transaction_id)
  VALUES(s,id,3,jsonb_set(w,'{state}','"UNKNOWN"'),tx);
END $$;
REVOKE ALL ON FUNCTION ledger.mark_management_unknown(text,text,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier;
GRANT EXECUTE ON FUNCTION ledger.mark_management_unknown(text,text,text) TO ledger_management_dispatch_verifier;
CREATE FUNCTION ledger.abort_management_unsent(s text,id text,tx text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE w jsonb;p jsonb;BEGIN
 SELECT payload INTO w FROM ledger.management_writes WHERE scope_id=s AND record_id=id ORDER BY sequence DESC LIMIT 1;
 IF w->>'state' IS DISTINCT FROM 'DURABLE_READY'
  OR EXISTS(SELECT FROM ledger.management_dispatch_marks WHERE scope_id=s AND command_id=id)
  OR EXISTS(SELECT FROM ledger.management_permit_consumptions WHERE scope_id=s AND command_id=id)
  OR NOT EXISTS(SELECT FROM ledger.active_owners o WHERE o.environment=w#>>'{scope,environment}'
   AND o.account_id=w#>>'{scope,venue_account_id}' AND o.product=w#>>'{scope,product}'
   AND o.instrument_uid=w#>>'{scope,instrument_uid}' AND o.venue_leg=w#>>'{scope,venue_leg}'
   AND o.owner_strategy_id=w->>'owner_strategy_id' AND o.ownership_epoch=(w->>'ownership_epoch')::bigint
   AND o.policy_revision=(w->>'policy_revision')::bigint) THEN
  RAISE EXCEPTION 'provably unsent current management required' USING ERRCODE='23514';END IF;
 p=jsonb_build_object('command_id',id,'binding_hash',w->>'binding_hash','state','ABORTED_UNSENT','funds_released',false);
 INSERT INTO ledger.management_unsent_aborts(scope_id,command_id,payload,transaction_id) VALUES(s,id,p,tx) ON CONFLICT DO NOTHING;
 RETURN p;
END $$;
REVOKE ALL ON FUNCTION ledger.abort_management_unsent(text,text,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier;
GRANT EXECUTE ON FUNCTION ledger.abort_management_unsent(text,text,text) TO ledger_management_dispatch_verifier;
REVOKE ALL ON FUNCTION ledger.management_context_current(text,jsonb,jsonb),ledger.admit_management(text,text,jsonb,jsonb,text),
 ledger.mark_management_may(text,text,jsonb,text,integer,text),ledger.consume_management_permit(text,text,text,jsonb,text)
 FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier;
GRANT EXECUTE ON FUNCTION ledger.admit_management(text,text,jsonb,jsonb,text),ledger.mark_management_may(text,text,jsonb,text,integer,text),
 ledger.consume_management_permit(text,text,text,jsonb,text) TO ledger_management_dispatch_verifier;
COMMIT;
