-- Dormant local-child source storage. No application INSERT or send authority.
-- Apply only via the approved shared-DEV migration process after real validation.
BEGIN;
CREATE TABLE ledger.local_child_commitments_v1 (
 scope_id text NOT NULL,
 command_id text NOT NULL UNIQUE,
 activation_id text NOT NULL,
 activation_record_id text GENERATED ALWAYS AS ('local-program-activation:'||activation_id) STORED,
 activation_sequence bigint GENERATED ALWAYS AS (1::bigint) STORED,
 commitment_hash text NOT NULL,
 payload jsonb NOT NULL,
 transaction_id text NOT NULL CHECK(length(transaction_id)>0),
 database_transaction xid8 NOT NULL DEFAULT pg_current_xact_id(),
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope_id,command_id),
 FOREIGN KEY(scope_id,activation_record_id,activation_sequence)
   REFERENCES ledger.audit_events(scope_id,record_id,sequence),
 CHECK(COALESCE(
   jsonb_typeof(payload)='object'
   AND payload#>>'{request,contract_version}'='2026-10-31'
   AND payload#>>'{request,record_kind}'='LOCAL_CHILD_REQUEST_V1'
   AND payload#>>'{request,activation_id}'=activation_id
   AND payload#>>'{request,request,command_id}'=command_id
   AND payload#>>'{request,request,scope,environment}' IN ('PAPER','RESEARCH')
   AND scope_id=ledger.program_digest(payload#>'{request,request,scope}')
   AND commitment_hash=ledger.program_digest(payload)
   AND length(command_id) BETWEEN 1 AND 128
   AND length(activation_id) BETWEEN 1 AND 128,false))
);
CREATE INDEX local_child_activation_lookup ON ledger.local_child_commitments_v1(scope_id,activation_id);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE
 ON ledger.local_child_commitments_v1 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.local_child_commitments_v1 FROM PUBLIC,ledger_owner,ledger_control,
 ledger_decision,ledger_program_dispatch_verifier;
GRANT SELECT ON ledger.local_child_commitments_v1 TO ledger_owner,ledger_program_dispatch_verifier;
-- Read access retains obligations; no writer grant or default privilege is installed. A future
-- reviewed admission migration must bind actual parent source, full-set quantity,
-- shared funding, owner/risk and independent command in the same transaction.
COMMIT;
