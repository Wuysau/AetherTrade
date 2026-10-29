-- Isolated migration administrator only. No runtime login is enrolled.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_order_closure_resolver') THEN
  CREATE ROLE ledger_order_closure_resolver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
   NOINHERIT NOREPLICATION NOBYPASSRLS;
 ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ledger_order_closure_resolver' AND
   (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
   OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles
     WHERE rolname='ledger_order_closure_resolver')) THEN
  RAISE EXCEPTION 'unsafe pre-existing order closure resolver role';
 END IF;
END $$;
GRANT USAGE ON SCHEMA ledger TO ledger_order_closure_resolver;
GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO ledger_order_closure_resolver;
GRANT INSERT ON ledger.audit_events,ledger.aggregate_heads,ledger.transaction_observations TO ledger_order_closure_resolver;
GRANT UPDATE(sequence) ON ledger.aggregate_heads TO ledger_order_closure_resolver;
GRANT UPDATE(group_id) ON ledger.collateral_mutex TO ledger_order_closure_resolver;
GRANT UPDATE(collateral_group_id) ON ledger.account_bindings TO ledger_order_closure_resolver;
GRANT UPDATE(ownership_epoch) ON ledger.active_owners TO ledger_order_closure_resolver;

CREATE TABLE ledger.order_closure_approvals (
 scope_id text NOT NULL, program_id text NOT NULL, record_id text NOT NULL,
 sequence bigint NOT NULL, before_payload jsonb NOT NULL, payload jsonb NOT NULL,
 context jsonb NOT NULL, transaction_id text NOT NULL,
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(scope_id,record_id,sequence)
);
REVOKE ALL ON ledger.order_closure_approvals FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_order_closure_resolver;
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.order_closure_approvals
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

CREATE FUNCTION ledger.order_reservation_before(p_scope text,p_id text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT COALESCE(
  (SELECT payload FROM ledger.audit_events WHERE scope_id=p_scope AND record_id='ip07:reservation:'||p_id ORDER BY sequence DESC LIMIT 1),
  (SELECT jsonb_build_object('reservation',payload,'held',payload->'amount','reflected','0',
   'release_pending','0','sequence',0) FROM ledger.reservations WHERE scope_id=p_scope AND record_id=p_id ORDER BY sequence DESC LIMIT 1))
$$;
REVOKE ALL ON FUNCTION ledger.order_reservation_before(text,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_order_closure_resolver;

CREATE FUNCTION ledger.order_closure_context_current(p_scope text,p_program text,p_context jsonb) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT COALESCE(
  p_context->>'program_id'=p_program
  AND p_context->'binding'=(SELECT payload FROM ledger.order_program_bindings WHERE scope_id=p_scope AND record_id=p_program)
  AND p_context->'state'=(SELECT payload FROM ledger.order_program_states WHERE scope_id=p_scope AND record_id=p_program ORDER BY sequence DESC LIMIT 1)
  AND p_context->'book'=(SELECT payload FROM ledger.book_snapshots WHERE scope_id=p_scope AND record_id='book' ORDER BY sequence DESC LIMIT 1)
  AND p_context->'owner'=(SELECT jsonb_build_array(owner_strategy_id,ownership_epoch,policy_revision)
   FROM ledger.active_owners WHERE environment=p_context#>>'{binding,scope,environment}'
   AND account_id=p_context#>>'{binding,scope,venue_account_id}' AND product=p_context#>>'{binding,scope,product}'
   AND instrument_uid=p_context#>>'{binding,scope,instrument_uid}' AND venue_leg=p_context#>>'{binding,scope,venue_leg}')
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_context->'protection_plans') plan
   WHERE plan IS DISTINCT FROM (SELECT payload FROM ledger.protection_plans
      WHERE scope_id=p_scope AND record_id=plan->>'plan_id' ORDER BY sequence DESC LIMIT 1))
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_context->'fills') fill
   WHERE fill IS DISTINCT FROM (SELECT payload FROM ledger.fills WHERE scope_id=p_scope AND record_id=fill->>'fill_id')
     OR EXISTS (SELECT 1 FROM ledger.fills WHERE correction_of=fill->>'fill_id'))
  AND NOT EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (record_id) payload FROM ledger.risk_locks
      WHERE scope_id=p_scope ORDER BY record_id,sequence DESC) latest WHERE latest.payload->>'state'='ACTIVE'),false)
$$;
REVOKE ALL ON FUNCTION ledger.order_closure_context_current(text,text,jsonb) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_order_closure_resolver;

CREATE FUNCTION ledger.order_closure_approved(p_scope text,p_id text,p_payload jsonb,p_transaction text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT EXISTS (SELECT 1 FROM ledger.order_closure_approvals a WHERE a.scope_id=p_scope AND a.record_id=p_id
  AND a.payload=p_payload AND a.transaction_id=p_transaction AND a.database_transaction=pg_current_xact_id()
  AND (a.context#>>'{review,expires_at}')::timestamptz>clock_timestamp()
  AND a.before_payload=ledger.order_reservation_before(p_scope,p_id)
  AND ledger.order_closure_context_current(p_scope,a.program_id,a.context))
$$;
REVOKE ALL ON FUNCTION ledger.order_closure_approved(text,text,jsonb,text) FROM PUBLIC,ledger_control,ledger_decision;
GRANT EXECUTE ON FUNCTION ledger.order_closure_approved(text,text,jsonb,text) TO ledger_owner,ledger_order_closure_resolver;

CREATE FUNCTION ledger.approve_order_program_closure(p_scope text,p_program text,p_context jsonb,p_transaction text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE target jsonb; prior jsonb; after_payload jsonb; group_name text;
BEGIN
 IF p_transaction IS NULL OR p_transaction='' OR
  COALESCE(p_context#>>'{binding,scope,environment}','') NOT IN ('PAPER','RESEARCH') THEN
  RAISE EXCEPTION 'closure environment disabled' USING ERRCODE='23514';
 END IF;
 SELECT collateral_group_id INTO group_name FROM ledger.account_bindings
  WHERE environment=p_context#>>'{binding,scope,environment}' AND account_id=p_context#>>'{binding,scope,venue_account_id}'
  AND product=p_context#>>'{binding,scope,product}' FOR SHARE;
 PERFORM group_id FROM ledger.collateral_mutex WHERE environment=p_context#>>'{binding,scope,environment}' AND group_id=group_name FOR UPDATE;
 IF NOT FOUND OR NOT ledger.order_closure_context_current(p_scope,p_program,p_context) THEN
  RAISE EXCEPTION 'stale closure context or collateral binding' USING ERRCODE='23514';
 END IF;
 IF COALESCE(p_context#>>'{state,status}','') NOT IN ('STOPPED','COMPLETED')
  OR (p_context#>>'{state,unallocated_quantity}')::numeric IS DISTINCT FROM 0
  OR p_context#>>'{state,parent_observed_at}' IS NULL
  OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_context#>'{state,children}') c
      WHERE COALESCE(c->>'status','') NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED'))
  OR jsonb_typeof(p_context#>'{review,obligations}') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_context#>'{review,obligations}')<>7
  OR jsonb_typeof(p_context->'targets') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_context->'targets')=0 THEN
  RAISE EXCEPTION 'terminal closure and independent review required' USING ERRCODE='23514';
 END IF;
 IF p_context#>>'{review,reviewed_at}' IS NULL OR p_context#>>'{review,expires_at}' IS NULL
  OR (p_context#>>'{review,reviewed_at}')::timestamptz>clock_timestamp()
  OR (p_context#>>'{review,expires_at}')::timestamptz<=clock_timestamp()
  OR (p_context#>>'{review,expires_at}')::timestamptz>(p_context#>>'{review,reviewed_at}')::timestamptz+interval '60 seconds' THEN
  RAISE EXCEPTION 'expired or invalid closure review' USING ERRCODE='23514';
 END IF;
 IF (SELECT count(DISTINCT v->>'reservation_id') FROM jsonb_array_elements(p_context->'targets') v)
     <>jsonb_array_length(p_context#>'{binding,reservation_ids}')
  OR jsonb_array_length(p_context->'targets')<>jsonb_array_length(p_context#>'{binding,reservation_ids}') THEN
  RAISE EXCEPTION 'all unique shared reservations required' USING ERRCODE='23514';
 END IF;
 FOR target IN SELECT value FROM jsonb_array_elements(p_context->'targets') LOOP
  prior:=ledger.order_reservation_before(p_scope,target->>'reservation_id');
  after_payload:=target->'after';
  IF prior IS NULL OR prior IS DISTINCT FROM target->'before'
   OR NOT (p_context#>'{binding,reservation_ids}') ? (target->>'reservation_id')
   OR NOT EXISTS (SELECT 1 FROM ledger.order_funding_links WHERE scope_id=p_scope AND record_id=target->>'reservation_id'
       AND payload->>'program_id'=p_program) THEN
   RAISE EXCEPTION 'stale or invalid closure reservation target' USING ERRCODE='23514';
  END IF;
  IF after_payload=prior THEN CONTINUE; END IF;
  IF (after_payload->>'sequence')::bigint IS DISTINCT FROM (prior->>'sequence')::bigint+1
   OR (after_payload->>'held')::numeric<0 OR (after_payload->>'held')::numeric>=(prior->>'held')::numeric THEN
   RAISE EXCEPTION 'stale or invalid closure reservation target' USING ERRCODE='23514';
  END IF;
  INSERT INTO ledger.order_closure_approvals(scope_id,program_id,record_id,sequence,before_payload,payload,context,transaction_id)
   VALUES(p_scope,p_program,target->>'reservation_id',(after_payload->>'sequence')::bigint,prior,after_payload,p_context,p_transaction);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION ledger.approve_order_program_closure(text,text,jsonb,text) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT EXECUTE ON FUNCTION ledger.approve_order_program_closure(text,text,jsonb,text) TO ledger_order_closure_resolver;

CREATE FUNCTION ledger.guard_program_reservation_reduction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
DECLARE reservation_id text; prior jsonb; original jsonb; financial_credit boolean;
BEGIN
 IF NEW.record_id NOT LIKE 'ip07:reservation:%' THEN RETURN NEW; END IF;
 reservation_id:=substr(NEW.record_id,length('ip07:reservation:')+1);
 IF NOT EXISTS (SELECT 1 FROM ledger.order_funding_links WHERE scope_id=NEW.scope_id AND record_id=reservation_id) THEN RETURN NEW; END IF;
 prior:=ledger.order_reservation_before(NEW.scope_id,reservation_id);
 SELECT payload INTO original FROM ledger.reservations WHERE scope_id=NEW.scope_id AND record_id=reservation_id ORDER BY sequence DESC LIMIT 1;
 IF prior IS NULL OR original IS NULL OR NEW.payload#>>'{reservation,reservation_id}' IS DISTINCT FROM reservation_id
  OR ((NEW.payload->'reservation')-'state'-'reflected_amount'-'evidence_watermark') IS DISTINCT FROM (original-'state'-'reflected_amount'-'evidence_watermark')
  OR COALESCE(NEW.payload->>'held','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
  OR COALESCE(prior->>'held','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
  OR COALESCE(NEW.payload->>'reflected','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
  OR COALESCE(NEW.payload->>'release_pending','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
  OR COALESCE(NEW.payload#>>'{reservation,reflected_amount}','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
  OR (NEW.payload->>'held')::numeric>(original->>'amount')::numeric
  OR (NEW.payload->>'reflected')::numeric>(NEW.payload->>'held')::numeric
  OR (NEW.payload->>'release_pending')::numeric>(NEW.payload->>'held')::numeric
  OR (NEW.payload#>>'{reservation,reflected_amount}')::numeric IS DISTINCT FROM (NEW.payload->>'reflected')::numeric
  OR (NEW.payload->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
  OR NEW.sequence IS DISTINCT FROM (prior->>'sequence')::bigint+1 THEN
  RAISE EXCEPTION 'invalid linked reservation projection' USING ERRCODE='23514';
 END IF;
 -- Public watermark hashes attest identity, not authority. Same-held reflection
 -- or refreshed released credit must not bypass independent closure approval.
 financial_credit := (NEW.payload->>'held')::numeric<(prior->>'held')::numeric
  OR GREATEST((NEW.payload->>'held')::numeric-(NEW.payload->>'reflected')::numeric,
              (NEW.payload->>'release_pending')::numeric)
     < GREATEST((prior->>'held')::numeric-(prior->>'reflected')::numeric,
                (prior->>'release_pending')::numeric)
  OR (NEW.payload->>'reflected')::numeric>(prior->>'reflected')::numeric
  OR (NEW.payload#>>'{reservation,reflected_amount}')::numeric>(prior#>>'{reservation,reflected_amount}')::numeric
  OR (NEW.payload#>>'{reservation,evidence_watermark}' IS DISTINCT FROM prior#>>'{reservation,evidence_watermark}'
      AND ((NEW.payload->>'held')::numeric<(original->>'amount')::numeric
           OR (NEW.payload->>'reflected')::numeric>0));
 IF financial_credit AND
  NOT ledger.order_closure_approved(NEW.scope_id,reservation_id,NEW.payload,NEW.transaction_id) THEN
  RAISE EXCEPTION 'dedicated program closure required' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ledger.guard_program_reservation_reduction() FROM PUBLIC;
CREATE TRIGGER program_reservation_closure_guard BEFORE INSERT ON ledger.audit_events
 FOR EACH ROW EXECUTE FUNCTION ledger.guard_program_reservation_reduction();
COMMIT;
