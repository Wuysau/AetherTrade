-- Additive quote context dispatch; 0012 and all historical records stay intact.
BEGIN;
DO $$ BEGIN
 IF to_regprocedure('ledger.program_dispatch_context_current(text,jsonb,jsonb)') IS NULL THEN
  RAISE EXCEPTION '0012 program boundary required';
 END IF;
END $$;
CREATE FUNCTION ledger.quote_dispatch_context_current(p_scope text,c jsonb,w jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ledger AS $$
 SELECT COALESCE(c#>>'{assessment,contract_version}'='2026-10-30'
 AND c#>>'{assessment,record_kind}'='QUOTE_ASSESSMENT_ARCHIVE_V1'
 AND c#>>'{assessment,risk_terms,source,kind}'='POSITION_VALUE_BAND_PROPOSAL'
 AND c#>>'{assessment,risk_terms,source,version}'='2026-10-30'
 AND c#>>'{assessment,risk_terms,source,hash}'=c#>>'{assessment,compilation,proposal_hash}'
 AND c#>>'{assessment,risk_facts,target_facts,capabilities,transport}'='PAPER'
 AND c#>>'{assessment,risk_facts,target_facts,capabilities,qualification}'='PAPER_VERIFIED'
 AND c#>'{assessment,book,scope}'=w->'scope'
 AND c#>'{assessment,risk_facts,target_facts,bounds,scope}'=w->'scope'
 AND ledger.program_digest(c#>'{assessment,risk_facts,target_facts,bounds}')=c->>'execution_bounds_hash'
 AND ledger.program_digest(c#>'{assessment,book}')=c->>'financial_book_hash'
 AND c->>'source_book_hash'=(SELECT ledger.program_digest(b.payload) FROM ledger.book_snapshots b
  WHERE b.scope_id=p_scope AND b.record_id='book' ORDER BY b.sequence DESC LIMIT 1),false)
$$;
REVOKE ALL ON FUNCTION ledger.quote_dispatch_context_current(text,jsonb,jsonb) FROM PUBLIC,ledger_owner,ledger_control,ledger_decision,ledger_program_dispatch_verifier;
CREATE OR REPLACE FUNCTION ledger.program_dispatch_context_current(p_scope text,c jsonb,w jsonb) RETURNS boolean
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
 AND EXISTS(SELECT 1 FROM ledger.audit_events original_bundle
  WHERE original_bundle.scope_id=p_scope AND original_bundle.record_id='program-financial-bundle:'||(w->>'intent_id')
   AND original_bundle.sequence=1
   AND original_bundle.payload#>'{archive,record_kind}'=c#>'{assessment,record_kind}'
   AND original_bundle.payload#>'{archive,contract_version}'=c#>'{assessment,contract_version}')
 AND ((c#>>'{assessment,record_kind}'='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE'
 AND c#>>'{assessment,capabilities,transport}'='PAPER'
 AND c#>>'{assessment,capabilities,qualification}'='PAPER_VERIFIED')
 OR ledger.quote_dispatch_context_current(p_scope,c,w))
 AND ledger.program_digest(c#>'{assessment,compilation}')=w->>'compilation_hash'
 AND c#>>'{assessment,intent_id}'=w->>'intent_id'
 AND (c#>>'{clock,utc_now}')::timestamptz<(c#>>'{qualification,valid_until}')::timestamptz
 AND EXISTS(SELECT 1 FROM ledger.active_owners o WHERE o.environment=w#>>'{scope,environment}' AND o.account_id=w#>>'{scope,venue_account_id}'
  AND o.product=w#>>'{scope,product}' AND o.instrument_uid=w#>>'{scope,instrument_uid}' AND o.venue_leg=w#>>'{scope,venue_leg}'
  AND o.owner_strategy_id=w->>'owner_strategy_id' AND o.ownership_epoch=(w->>'ownership_epoch')::bigint AND o.policy_revision=(w->>'policy_revision')::bigint)
 AND c->'fence'=(SELECT payload FROM ledger.audit_events a WHERE a.scope_id=p_scope AND a.record_id=c->>'fence_record_id' ORDER BY sequence DESC LIMIT 1)
 AND c#>>'{fence,enabled}'='true',false)
$$;
COMMIT;
