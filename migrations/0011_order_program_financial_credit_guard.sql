-- Additive correction for databases already on 0009. No history is rewritten.
-- This preserves the trigger identity and resolver permissions; no role is enrolled.
BEGIN;
DO $$ BEGIN
 IF to_regprocedure('ledger.guard_program_reservation_reduction()') IS NULL THEN
  RAISE EXCEPTION '0009 order program closure migration required';
 END IF;
END $$;
-- Serialize the preflight with writes/linking. An already credited old row
-- cannot be made safe merely by replacing the future INSERT trigger.
LOCK TABLE ledger.audit_events,ledger.order_funding_links IN SHARE ROW EXCLUSIVE MODE;
DO $$
DECLARE projection record;
BEGIN
 FOR projection IN
  SELECT f.scope_id,f.record_id,a.sequence,a.payload,a.transaction_id,r.payload AS creation
   FROM ledger.order_funding_links f
   JOIN ledger.reservations r ON r.scope_id=f.scope_id AND r.record_id=f.record_id
   JOIN LATERAL (SELECT sequence,payload,transaction_id FROM ledger.audit_events
     WHERE scope_id=f.scope_id AND record_id='ip07:reservation:'||f.record_id
     ORDER BY sequence DESC LIMIT 1) a ON true
 LOOP
  IF COALESCE(projection.payload->>'held','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
   OR COALESCE(projection.payload->>'reflected','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
   OR COALESCE(projection.payload->>'release_pending','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
   OR COALESCE(projection.payload#>>'{reservation,reflected_amount}','') !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
   OR (projection.payload->>'held')::numeric>(projection.creation->>'amount')::numeric
   OR (projection.payload->>'reflected')::numeric>(projection.payload->>'held')::numeric
   OR (projection.payload->>'release_pending')::numeric>(projection.payload->>'held')::numeric
   OR (projection.payload#>>'{reservation,reflected_amount}')::numeric IS DISTINCT FROM (projection.payload->>'reflected')::numeric THEN
   RAISE EXCEPTION 'invalid existing linked reservation projection requires reconciliation' USING ERRCODE='23514';
  END IF;
  IF ((projection.payload->>'held')::numeric<(projection.creation->>'amount')::numeric
      OR (projection.payload->>'reflected')::numeric>0)
   AND NOT EXISTS (SELECT 1 FROM ledger.order_closure_approvals approval
    WHERE approval.scope_id=projection.scope_id AND approval.record_id=projection.record_id
     AND approval.sequence=projection.sequence AND approval.payload=projection.payload
     AND approval.transaction_id=projection.transaction_id) THEN
   RAISE EXCEPTION 'unreviewed linked financial credit requires reconciliation' USING ERRCODE='23514';
  END IF;
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION ledger.guard_program_reservation_reduction() RETURNS trigger
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
COMMIT;
