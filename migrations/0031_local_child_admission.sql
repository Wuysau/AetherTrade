-- CANDIDATE additive local-child admission boundary. NOT APPLIED; NOT DB-VERIFIED.
-- Apply only through the approved shared-DEV migration process after the
-- dormant 0030 storage cases and this boundary's SQL cases pass. This file
-- adds no transport, no native-parent command and no funds release.
BEGIN;

CREATE TABLE ledger.local_child_writes(
 scope_id text NOT NULL,command_id text NOT NULL,sequence bigint NOT NULL CHECK(sequence BETWEEN 1 AND 3),
 payload jsonb NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id,sequence));
CREATE TABLE ledger.local_child_dispatch_marks(
 scope_id text NOT NULL,command_id text NOT NULL,context jsonb NOT NULL,token_hash text NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.local_child_commitments_v1(scope_id,command_id));
CREATE TABLE ledger.local_child_permit_consumptions(
 scope_id text NOT NULL,command_id text NOT NULL,token_hash text NOT NULL,context jsonb NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.local_child_dispatch_marks(scope_id,command_id));
CREATE TABLE ledger.local_child_unsent_aborts(
 scope_id text NOT NULL,command_id text NOT NULL,proof_hash text NOT NULL,payload jsonb NOT NULL,
 transaction_id text NOT NULL,database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,command_id) REFERENCES ledger.local_child_commitments_v1(scope_id,command_id));

DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['local_child_writes','local_child_dispatch_marks',
  'local_child_permit_consumptions','local_child_unsent_aborts'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I '
   'FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
  EXECUTE format('REVOKE ALL ON ledger.%I FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,'
   'ledger_program_dispatch_verifier,ledger_management_dispatch_verifier',tab);
 END LOOP;
END $$;

-- Write rows and commitments are inserted in one transaction: write first,
-- commitment second. The deferred trigger proves both existed at commit.
CREATE FUNCTION ledger.check_local_child_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE prior jsonb;seq bigint;act jsonb;
BEGIN
 SELECT payload,sequence INTO prior,seq FROM ledger.local_child_writes
  WHERE scope_id=NEW.scope_id AND command_id=NEW.command_id ORDER BY sequence DESC LIMIT 1;
 SELECT payload INTO act FROM ledger.audit_events
  WHERE scope_id=NEW.scope_id AND record_id='local-program-activation:'||(NEW.payload->>'activation_id')
   AND sequence=1;
 IF NEW.payload->>'contract_version' IS DISTINCT FROM '2026-10-31'
  OR NEW.payload->>'record_kind' IS DISTINCT FROM 'LOCAL_CHILD_COMMAND_V1'
  OR NEW.payload->>'command_id' IS DISTINCT FROM NEW.command_id
  OR NEW.scope_id IS DISTINCT FROM ledger.program_digest(NEW.payload->'scope')
  OR act IS NULL OR act->>'record_kind' IS DISTINCT FROM 'LOCAL_PROGRAM_ACTIVATION'
  OR NEW.scope_id IS DISTINCT FROM ledger.program_digest(act#>'{authority,scope}')
  OR act->>'bundle_hash' IS DISTINCT FROM NEW.payload->>'activation_bundle_hash'
  OR act#>>'{authority,compilation_hash}' IS DISTINCT FROM NEW.payload->>'parent_hash'
  OR act#>>'{authority,authorization_id}' IS DISTINCT FROM NEW.payload->>'authorization_id'
  OR act#>>'{authority,owner_strategy_id}' IS DISTINCT FROM NEW.payload->>'owner_strategy_id'
  OR (act#>>'{authority,ownership_epoch}')::bigint IS DISTINCT FROM (NEW.payload->>'ownership_epoch')::bigint
  OR (act#>>'{authority,submission_valid_until}')::timestamptz<=(NEW.payload->>'created_at')::timestamptz THEN
  RAISE EXCEPTION 'local child activation source required' USING ERRCODE='23514';
 END IF;
 IF prior IS NULL THEN
  IF NEW.sequence<>1 OR NEW.payload->>'state' IS DISTINCT FROM 'DURABLE_READY' THEN
   RAISE EXCEPTION 'initial durable local child required' USING ERRCODE='23514';END IF;
 ELSE
  IF NEW.sequence<>seq+1 OR (prior-'state') IS DISTINCT FROM (NEW.payload-'state')
   OR NOT ((prior->>'state'='DURABLE_READY' AND NEW.payload->>'state'='MAY_HAVE_SENT' AND EXISTS(
    SELECT 1 FROM ledger.local_child_dispatch_marks m WHERE m.scope_id=NEW.scope_id
     AND m.command_id=NEW.command_id AND m.database_transaction=pg_current_xact_id()))
   OR (prior->>'state'='MAY_HAVE_SENT' AND NEW.payload->>'state'='UNKNOWN')) THEN
   RAISE EXCEPTION 'protected local child transition required' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER local_child_write_guard BEFORE INSERT ON ledger.local_child_writes
 FOR EACH ROW EXECUTE FUNCTION ledger.check_local_child_write();

CREATE FUNCTION ledger.check_local_child_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ledger.local_child_commitments_v1 c WHERE c.scope_id=NEW.scope_id
  AND c.command_id=NEW.command_id AND c.activation_id=NEW.payload->>'activation_id'
  AND c.commitment_hash=NEW.payload->>'commitment_hash') THEN
  RAISE EXCEPTION 'local child commitment missing at transaction end' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER local_child_write_complete AFTER INSERT ON ledger.local_child_writes
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger.check_local_child_complete();

CREATE FUNCTION ledger.check_local_child_commitment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE act jsonb;total numeric;used numeric;q text;unit text;parent_unit text;
BEGIN
 SELECT payload INTO act FROM ledger.audit_events
  WHERE scope_id=NEW.scope_id AND record_id='local-program-activation:'||NEW.activation_id AND sequence=1;
 IF act IS NULL OR act#>>'{authority,compilation_hash}' IS DISTINCT FROM
  (SELECT payload->>'parent_hash' FROM ledger.local_child_writes w WHERE w.scope_id=NEW.scope_id
   AND w.command_id=NEW.command_id AND w.sequence=1) THEN
  RAISE EXCEPTION 'local child parent source required' USING ERRCODE='23514';END IF;
 IF NOT EXISTS(SELECT 1 FROM ledger.local_child_writes w WHERE w.scope_id=NEW.scope_id
  AND w.command_id=NEW.command_id AND w.sequence=1 AND w.payload->>'state'='DURABLE_READY'
  AND w.payload->>'commitment_hash'=NEW.commitment_hash
  AND w.database_transaction=pg_current_xact_id()) THEN
  RAISE EXCEPTION 'same transaction local child command required' USING ERRCODE='23514';END IF;
 q:=NEW.payload#>>'{compilation,node,quantity,value}';
 IF q IS NULL OR q !~ '^[0-9]+(\.[0-9]+)?$' THEN
  RAISE EXCEPTION 'local child base quantity required' USING ERRCODE='23514';END IF;
 unit:=NEW.payload#>>'{compilation,node,quantity_unit}';parent_unit:=act#>>'{authority,units,quantity_unit}';
 IF unit IS DISTINCT FROM parent_unit OR parent_unit IS NULL THEN
  RAISE EXCEPTION 'local child quantity unit changed' USING ERRCODE='23514';END IF;
 total:=(act#>>'{authority,nodes,0,quantity_limit}')::numeric;
 SELECT COALESCE(sum((c.payload#>>'{compilation,node,quantity,value}')::numeric),0) INTO used
  FROM ledger.local_child_commitments_v1 c WHERE c.scope_id=NEW.scope_id AND c.activation_id=NEW.activation_id;
 IF total IS NULL OR used+q::numeric>total THEN
  RAISE EXCEPTION 'local child quantity pool exceeded' USING ERRCODE='23514';END IF;
 IF NEW.payload#>>'{request,activation_id}' IS DISTINCT FROM NEW.activation_id
  OR NEW.payload#>>'{request,request,command_id}' IS DISTINCT FROM NEW.command_id THEN
  RAISE EXCEPTION 'local child request identity changed' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER local_child_commitment_guard BEFORE INSERT ON ledger.local_child_commitments_v1
 FOR EACH ROW EXECUTE FUNCTION ledger.check_local_child_commitment();

CREATE FUNCTION ledger.check_local_child_mark() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE w jsonb;
BEGIN
 SELECT payload INTO w FROM ledger.local_child_writes WHERE scope_id=NEW.scope_id
  AND command_id=NEW.command_id AND sequence=1;
 IF w IS NULL OR w->>'state' IS DISTINCT FROM 'DURABLE_READY'
  OR NEW.context->'command' IS DISTINCT FROM w
  OR length(NEW.token_hash)<>64 OR NEW.expires_at<=clock_timestamp()
  OR NEW.expires_at>clock_timestamp()+interval '5 seconds' THEN
  RAISE EXCEPTION 'current local child command required' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER local_child_mark_guard BEFORE INSERT ON ledger.local_child_dispatch_marks
 FOR EACH ROW EXECUTE FUNCTION ledger.check_local_child_mark();

CREATE FUNCTION ledger.check_local_child_consumption() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ledger.local_child_dispatch_marks m WHERE m.scope_id=NEW.scope_id
  AND m.command_id=NEW.command_id AND m.token_hash=NEW.token_hash AND m.expires_at>clock_timestamp()) THEN
  RAISE EXCEPTION 'unused current local child permit required' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER local_child_consumption_guard BEFORE INSERT ON ledger.local_child_permit_consumptions
 FOR EACH ROW EXECUTE FUNCTION ledger.check_local_child_consumption();

ALTER TABLE ledger.outbox DROP CONSTRAINT outbox_kind_check;
ALTER TABLE ledger.outbox ADD CONSTRAINT outbox_kind_check
 CHECK(kind IN ('COMMAND_READY','DOMAIN_EVENT','PROGRAM_COMMAND_READY','LOCAL_CHILD_COMMAND_READY'));

REVOKE ALL ON FUNCTION ledger.check_local_child_write(),ledger.check_local_child_complete(),
 ledger.check_local_child_commitment(),ledger.check_local_child_mark(),
 ledger.check_local_child_consumption() FROM PUBLIC;
GRANT INSERT ON ledger.local_child_writes,ledger.local_child_dispatch_marks,
 ledger.local_child_permit_consumptions,ledger.local_child_unsent_aborts TO ledger_owner;
GRANT SELECT ON ledger.local_child_writes,ledger.local_child_dispatch_marks,
 ledger.local_child_permit_consumptions,ledger.local_child_unsent_aborts TO ledger_owner;
GRANT INSERT ON ledger.local_child_commitments_v1 TO ledger_owner;
COMMIT;
