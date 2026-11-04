-- Persist quote-side cumulative execution for Demo MARKET reconciliation.
-- No venue capability is enabled by this schema change. Apply explicitly after 0035.
BEGIN;
ALTER TABLE demo_commands ADD COLUMN cumulative_quote TEXT NOT NULL DEFAULT '0';
COMMIT;
