-- Program provenance is authored only by bounded intake, never the execution owner.
BEGIN;
CREATE TABLE ledger.order_program_intakes (
 scope_id text NOT NULL,record_id text NOT NULL,sequence bigint NOT NULL CHECK(sequence=1),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),transaction_id text NOT NULL,
 processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(scope_id,record_id)
);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.order_program_intakes
 FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
REVOKE ALL ON ledger.order_program_intakes FROM PUBLIC,ledger_owner,ledger_control,ledger_decision;
GRANT SELECT,INSERT ON ledger.order_program_intakes TO ledger_intake;
GRANT SELECT ON ledger.order_program_intakes TO ledger_owner;
COMMIT;
