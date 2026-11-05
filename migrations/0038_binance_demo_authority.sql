-- Candidate only. Apply through approved DEV migration workflow; NOT_RUN here.
BEGIN;
CREATE TABLE demo_authority_proofs (
    nonce VARCHAR(100) PRIMARY KEY,
    account_key VARCHAR(64) NOT NULL,
    purpose VARCHAR(32) NOT NULL,
    envelope JSON NOT NULL,
    revoked BOOLEAN NOT NULL,
    consumed_at INTEGER NOT NULL
);
COMMIT;
