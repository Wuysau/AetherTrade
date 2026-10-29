-- Additive PAPER/RESEARCH program command boundary. No cash ledger or transport.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier') THEN
  CREATE ROLE ledger_program_dispatch_verifier NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
 ELSIF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) OR
  EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier')
   OR roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier')) THEN
  RAISE EXCEPTION 'unsafe dispatch verifier role';
 END IF;
END $$;
GRANT USAGE ON SCHEMA ledger TO ledger_program_dispatch_verifier;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO ledger_program_dispatch_verifier;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_program_dispatch_verifier;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_program_dispatch_verifier;
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_program_dispatch_verifier;
GRANT INSERT ON ledger.transaction_observations TO ledger_program_dispatch_verifier;

CREATE FUNCTION ledger.program_canonical_json(v jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,ledger AS $$
 SELECT CASE jsonb_typeof(v)
 WHEN 'object' THEN '{'||COALESCE((SELECT string_agg(to_jsonb(key)::text||':'||ledger.program_canonical_json(value),',' ORDER BY key COLLATE "C") FROM jsonb_each(v)),'')||'}'
 WHEN 'array' THEN '['||COALESCE((SELECT string_agg(ledger.program_canonical_json(value),',' ORDER BY n) FROM jsonb_array_elements(v) WITH ORDINALITY a(value,n)),'')||']'
 ELSE v::text END
$$;
CREATE FUNCTION ledger.program_digest(v jsonb) RETURNS text LANGUAGE sql IMMUTABLE STRICT
 SET search_path=pg_catalog,ledger AS $$ SELECT encode(sha256(convert_to(ledger.program_canonical_json(v),'UTF8')),'hex') $$;

CREATE TABLE ledger.program_request_bindings(
 scope_id text NOT NULL,record_id text NOT NULL UNIQUE,sequence bigint NOT NULL CHECK(sequence=1),
 payload jsonb NOT NULL,transaction_id text NOT NULL,processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,record_id,sequence));
CREATE TABLE ledger.program_client_identities(
 environment text NOT NULL,account_id text NOT NULL,product text NOT NULL,service_namespace text NOT NULL,
 client_id text NOT NULL,scope_id text NOT NULL,command_id text NOT NULL,
 PRIMARY KEY(environment,account_id,product,service_namespace,client_id));
CREATE UNIQUE INDEX program_request_semantic_identity ON ledger.program_request_bindings
 (scope_id,(payload->>'intent_id'),(payload#>>'{request,request_index}'));
CREATE TABLE ledger.program_dispatch_marks(
 scope_id text NOT NULL,command_id text NOT NULL,write_payload jsonb NOT NULL,context jsonb NOT NULL,
 token_hash text NOT NULL UNIQUE,expires_at timestamptz NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id));
CREATE TABLE ledger.program_permit_consumptions(
 scope_id text NOT NULL,command_id text NOT NULL,token_hash text NOT NULL,
 context jsonb NOT NULL,transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),FOREIGN KEY(scope_id,command_id) REFERENCES ledger.program_dispatch_marks);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['program_request_bindings','program_client_identities','program_dispatch_marks','program_permit_consumptions'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
  EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier',tab);
  EXECUTE format('GRANT SELECT ON ledger.%I TO ledger_owner,ledger_program_dispatch_verifier',tab);
 END LOOP;
END $$;
GRANT INSERT ON ledger.program_request_bindings,ledger.program_client_identities TO ledger_owner;

CREATE FUNCTION ledger.program_binding_current(p_scope text,p_id text,p_write jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT EXISTS(SELECT 1 FROM ledger.program_request_bindings b
 JOIN ledger.intents i ON i.scope_id=b.scope_id AND i.record_id=b.payload->>'intent_id'
 JOIN ledger.audit_events f ON f.scope_id=b.scope_id AND f.record_id='program-financial-bundle:'||(b.payload->>'intent_id')
 JOIN ledger.order_programs p ON p.scope_id=b.scope_id AND p.record_id=p_write->>'program_id'
 LEFT JOIN ledger.order_writes_v2 ow ON ow.scope_id=b.scope_id AND ow.record_id=b.record_id AND ow.sequence=1
 WHERE b.scope_id=p_scope AND b.record_id=p_id AND b.payload->>'command_id'=p_id
 AND p_write->>'command_id'=p_id AND p_write->'scope'=b.payload->'scope'
 AND p_write->>'intent_id'=b.payload->>'intent_id'
 AND ledger.program_digest(b.payload)=p_write->>'binding_hash'
 AND ledger.program_digest(b.payload->'request')=p_write->>'request_hash'
 AND b.payload->>'request_hash'=p_write->>'request_hash'
 AND ledger.program_digest(f.payload)=b.payload->>'bundle_hash'
 AND ledger.program_digest(f.payload->'intent')=p_write->>'intent_hash' AND f.payload->'intent'=i.payload
 AND ledger.program_digest(f.payload->'basis')=p_write->>'basis_hash'
 AND ledger.program_digest(f.payload->'decision')=p_write->>'decision_hash'
 AND ledger.program_digest(f.payload->'archive')=p_write->>'archive_hash'
 AND ledger.program_digest(f.payload#>'{archive,compilation}')=p_write->>'compilation_hash'
 AND ledger.program_digest(f.payload#>'{archive,allocation}')=p_write->>'allocation_hash'
 AND ledger.program_digest(p.payload)=p_write->>'canonical_program_hash'
 AND p.payload=f.payload#>'{intent,proposal,program}'
 AND f.payload#>>'{decision,verdict}'='ALLOW'
 AND f.transaction_id=b.transaction_id AND i.transaction_id=b.transaction_id
 AND f.xmin=b.xmin AND i.xmin=b.xmin AND (ow.record_id IS NULL OR ow.xmin=b.xmin)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(f.payload->'reservations') v
  LEFT JOIN ledger.reservations r ON r.scope_id=b.scope_id AND r.record_id=v#>>'{reservation,reservation_id}'
  WHERE r.record_id IS NULL OR r.payload IS DISTINCT FROM v->'reservation' OR r.xmin IS DISTINCT FROM b.xmin)
 AND p_write->>'owner_strategy_id'=i.payload->>'owner_strategy_id'
 AND p_write->>'ownership_epoch'=i.payload->>'ownership_epoch'
 AND p_write->>'policy_revision'=i.payload->>'risk_policy_revision')
$$;

CREATE FUNCTION ledger.guard_program_client_identity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ledger AS $$ BEGIN
 IF TG_TABLE_NAME='program_client_identities' THEN
  IF EXISTS(SELECT 1 FROM ledger.command_identities l WHERE l.command_id=NEW.command_id OR
   (l.environment=NEW.environment AND l.account_id=NEW.account_id AND l.product=NEW.product
    AND l.venue_client_order_id=NEW.client_id AND NEW.service_namespace IN ('BINANCE_SPOT_ORDINARY','BINANCE_USD_M_ORDINARY'))) THEN
   RAISE EXCEPTION 'legacy command/client identity collision' USING ERRCODE='23514'; END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM ledger.program_client_identities p WHERE p.command_id=NEW.command_id OR
   (p.environment=NEW.environment AND p.account_id=NEW.account_id AND p.product=NEW.product
    AND p.client_id=NEW.venue_client_order_id AND p.service_namespace IN ('BINANCE_SPOT_ORDINARY','BINANCE_USD_M_ORDINARY'))) THEN
   RAISE EXCEPTION 'program command/client identity collision' USING ERRCODE='23514'; END IF;
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER program_identity_collision BEFORE INSERT ON ledger.program_client_identities FOR EACH ROW EXECUTE FUNCTION ledger.guard_program_client_identity();
CREATE TRIGGER legacy_program_identity_collision BEFORE INSERT ON ledger.command_identities FOR EACH ROW EXECUTE FUNCTION ledger.guard_program_client_identity();

-- Original V2 function stays byte-for-byte unchanged, including its old MAY rule.
DROP TRIGGER order_write_guard ON ledger.order_writes_v2;
CREATE TRIGGER order_write_guard BEFORE INSERT ON ledger.order_writes_v2 FOR EACH ROW
 WHEN(NEW.payload->>'contract_version' IS DISTINCT FROM '2026-10-29') EXECUTE FUNCTION ledger.check_order_write_v2();
CREATE FUNCTION ledger.check_order_write_v3() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,ledger AS $$ DECLARE prior jsonb; seq bigint; BEGIN
 SELECT payload,sequence INTO prior,seq FROM ledger.order_writes_v2 WHERE scope_id=NEW.scope_id AND record_id=NEW.record_id ORDER BY sequence DESC LIMIT 1;
 IF NEW.payload->>'record_kind' IS DISTINCT FROM 'PROGRAM_APPLICATION_WRITE' OR
  NEW.payload->>'operation' IS DISTINCT FROM 'CREATE_REQUEST' OR
  COALESCE(NEW.payload#>>'{scope,environment}','') NOT IN ('PAPER','RESEARCH') OR
  NEW.payload->>'origin' IS DISTINCT FROM 'APPLICATION_ACTION' OR NEW.payload->'parent_command_id' IS DISTINCT FROM 'null'::jsonb OR
  NOT ledger.program_binding_current(NEW.scope_id,NEW.record_id,NEW.payload) THEN
  RAISE EXCEPTION 'complete financial program binding required' USING ERRCODE='23514'; END IF;
 IF prior IS NULL THEN
  IF NEW.sequence<>1 OR NEW.payload->>'state' IS DISTINCT FROM 'DURABLE_READY' OR
   NOT EXISTS(SELECT 1 FROM ledger.program_request_bindings b WHERE b.scope_id=NEW.scope_id AND b.record_id=NEW.record_id
    AND b.transaction_id=NEW.transaction_id AND b.database_transaction=pg_current_xact_id()) THEN
   RAISE EXCEPTION 'same transaction initial ready required' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.sequence<>seq+1 OR (prior-'state') IS DISTINCT FROM (NEW.payload-'state') OR
   NOT ((prior->>'state'='DURABLE_READY' AND NEW.payload->>'state'='MAY_HAVE_SENT' AND EXISTS(
    SELECT 1 FROM ledger.program_dispatch_marks m WHERE m.scope_id=NEW.scope_id AND m.command_id=NEW.record_id
     AND m.write_payload=NEW.payload AND m.database_transaction=pg_current_xact_id())) OR
    (prior->>'state'='MAY_HAVE_SENT' AND NEW.payload->>'state'='UNKNOWN')) THEN
   RAISE EXCEPTION 'protected immutable program send boundary required' USING ERRCODE='23514'; END IF;
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER order_write_v3_guard BEFORE INSERT ON ledger.order_writes_v2 FOR EACH ROW
 WHEN(NEW.payload->>'contract_version'='2026-10-29') EXECUTE FUNCTION ledger.check_order_write_v3();
ALTER TABLE ledger.outbox DROP CONSTRAINT outbox_kind_check;
ALTER TABLE ledger.outbox ADD CONSTRAINT outbox_kind_check CHECK(kind IN ('COMMAND_READY','DOMAIN_EVENT','PROGRAM_COMMAND_READY'));

-- Only the server verifier identity executes these; ordinary owner SQL cannot
-- create protected mark/consume rows, and no role membership is granted.
CREATE FUNCTION ledger.program_dispatch_context_current(p_scope text,c jsonb,w jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT COALESCE(c->'write'=w AND c->>'request_hash'=w->>'request_hash'
 AND c#>>'{clock,clock_health}'='HEALTHY'
 AND (c#>>'{clock,monotonic_ns}')::bigint>=0
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
 AND ledger.program_digest(c->'assessment')=c->>'assessment_hash'
 AND ledger.program_digest(c->'qualification')=c->>'qualification_hash'
 AND c#>>'{assessment,assessment,verdict}'='ALLOW'
 AND c#>>'{assessment,record_kind}'='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE'
 AND c#>>'{assessment,capabilities,transport}'='PAPER'
 AND c#>>'{assessment,capabilities,qualification}'='PAPER_VERIFIED'
 AND ledger.program_digest(c#>'{assessment,compilation}')=w->>'compilation_hash'
 AND c#>>'{assessment,intent_id}'=w->>'intent_id'
 AND (c#>>'{clock,utc_now}')::timestamptz<(c#>>'{qualification,valid_until}')::timestamptz
 AND EXISTS(SELECT 1 FROM ledger.active_owners o WHERE o.environment=w#>>'{scope,environment}' AND o.account_id=w#>>'{scope,venue_account_id}'
  AND o.product=w#>>'{scope,product}' AND o.instrument_uid=w#>>'{scope,instrument_uid}' AND o.venue_leg=w#>>'{scope,venue_leg}'
  AND o.owner_strategy_id=w->>'owner_strategy_id' AND o.ownership_epoch=(w->>'ownership_epoch')::bigint AND o.policy_revision=(w->>'policy_revision')::bigint)
 AND c->'fence'=(SELECT payload FROM ledger.audit_events a WHERE a.scope_id=p_scope AND a.record_id=c->>'fence_record_id' ORDER BY sequence DESC LIMIT 1)
 AND c#>>'{fence,enabled}'='true',false)
$$;
CREATE FUNCTION ledger.mark_program_may(p_scope text,p_id text,p_context jsonb,p_token_hash text,p_ttl integer,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE w jsonb; BEGIN
 SELECT payload INTO w FROM ledger.order_writes_v2 WHERE scope_id=p_scope AND record_id=p_id ORDER BY sequence DESC LIMIT 1;
 IF p_ttl NOT BETWEEN 1 AND 5000 OR length(p_token_hash)<>64 OR w->>'state' IS DISTINCT FROM 'DURABLE_READY'
  OR NOT ledger.program_binding_current(p_scope,p_id,w) OR NOT ledger.program_dispatch_context_current(p_scope,p_context,w)
  OR p_context->>'request_hash' IS DISTINCT FROM w->>'request_hash'
  OR NOT EXISTS(SELECT 1 FROM ledger.active_owners o WHERE o.environment=w#>>'{scope,environment}' AND o.account_id=w#>>'{scope,venue_account_id}'
   AND o.product=w#>>'{scope,product}' AND o.instrument_uid=w#>>'{scope,instrument_uid}' AND o.venue_leg=w#>>'{scope,venue_leg}'
   AND o.owner_strategy_id=w->>'owner_strategy_id' AND o.ownership_epoch=(w->>'ownership_epoch')::bigint AND o.policy_revision=(w->>'policy_revision')::bigint) THEN
  RAISE EXCEPTION 'current exact program boundary required' USING ERRCODE='23514'; END IF;
 INSERT INTO ledger.program_dispatch_marks VALUES(p_scope,p_id,jsonb_set(w,'{state}','"MAY_HAVE_SENT"'),p_context,p_token_hash,
  clock_timestamp()+p_ttl*interval '1 millisecond',p_transaction,pg_current_xact_id());
 UPDATE ledger.aggregate_heads SET sequence=2 WHERE scope_id=p_scope
  AND aggregate_id='order_writes_v2:'||p_id AND sequence=1;
 IF NOT FOUND THEN RAISE EXCEPTION 'stale program command aggregate' USING ERRCODE='23514'; END IF;
 INSERT INTO ledger.order_writes_v2(scope_id,record_id,sequence,payload,transaction_id)
  VALUES(p_scope,p_id,2,jsonb_set(w,'{state}','"MAY_HAVE_SENT"'),p_transaction);
END $$;
CREATE FUNCTION ledger.consume_program_permit(p_scope text,p_id text,p_token_hash text,p_context jsonb,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$ DECLARE m ledger.program_dispatch_marks; w jsonb; BEGIN
 SELECT * INTO m FROM ledger.program_dispatch_marks WHERE scope_id=p_scope AND command_id=p_id;
 SELECT payload INTO w FROM ledger.order_writes_v2 WHERE scope_id=p_scope AND record_id=p_id ORDER BY sequence DESC LIMIT 1;
 IF m.command_id IS NULL OR m.database_transaction=pg_current_xact_id() OR m.expires_at<=clock_timestamp()
  OR m.token_hash IS DISTINCT FROM p_token_hash OR w IS DISTINCT FROM m.write_payload
  OR p_context->'lease' IS DISTINCT FROM m.context->'lease'
  OR NOT ledger.program_dispatch_context_current(p_scope,p_context,w) OR NOT ledger.program_binding_current(p_scope,p_id,w) THEN
  RAISE EXCEPTION 'committed unused current permit required' USING ERRCODE='23514'; END IF;
 INSERT INTO ledger.program_permit_consumptions VALUES(p_scope,p_id,p_token_hash,p_context,p_transaction,pg_current_xact_id());
END $$;
REVOKE ALL ON FUNCTION ledger.mark_program_may(text,text,jsonb,text,integer,text),ledger.consume_program_permit(text,text,text,jsonb,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT EXECUTE ON FUNCTION ledger.mark_program_may(text,text,jsonb,text,integer,text),ledger.consume_program_permit(text,text,text,jsonb,text) TO ledger_program_dispatch_verifier;
REVOKE ALL ON FUNCTION ledger.check_order_write_v3(),ledger.guard_program_client_identity(),ledger.program_binding_current(text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.program_dispatch_context_current(text,jsonb,jsonb) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT EXECUTE ON FUNCTION ledger.program_binding_current(text,text,jsonb) TO ledger_owner,ledger_program_dispatch_verifier;
COMMIT;
