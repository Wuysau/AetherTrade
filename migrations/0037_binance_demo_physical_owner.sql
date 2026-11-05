-- Apply only through the approved migration process. PostgreSQL qualification NOT_RUN.
-- Existing demo_accounts are intentionally not mapped or treated as clean.
BEGIN;
CREATE TABLE demo_physical_owners (
    physical_key VARCHAR(64) PRIMARY KEY,
    identity JSON NOT NULL,
    account_key VARCHAR(64) NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    state VARCHAR(16) NOT NULL CHECK (state IN ('DIRTY', 'CLEAN')),
    clean_evidence JSON
);
ALTER TABLE demo_observations ADD COLUMN stream_generation INTEGER;
ALTER TABLE demo_decisions ADD COLUMN credential_revision INTEGER;
ALTER TABLE demo_decisions ADD COLUMN stream_generation INTEGER;
COMMIT;
