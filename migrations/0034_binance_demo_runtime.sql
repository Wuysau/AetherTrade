-- Native Demo assessment/runtime composition. Apply after 0033 through the
-- approved shared DEV migration workflow. Never applied by console startup.
-- Historical NULL protection plans / zero trade times require reconciliation;
-- this migration does not invent authority or qualifying venue evidence.
BEGIN;
ALTER TABLE demo_commands ADD COLUMN protection_plan JSON;
ALTER TABLE demo_commands ADD COLUMN risk_receipt JSON;
ALTER TABLE demo_commands ADD COLUMN purpose VARCHAR(24) NOT NULL DEFAULT 'ENTRY';
ALTER TABLE demo_observations ADD COLUMN scope JSON;
ALTER TABLE demo_fills ADD COLUMN occurred_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE demo_protection ADD COLUMN plan JSON;
ALTER TABLE demo_protection ADD COLUMN remaining_quantity TEXT;
ALTER TABLE demo_protection ADD COLUMN entry_command_id VARCHAR(100);
ALTER TABLE demo_protection ADD COLUMN coverage_until INTEGER NOT NULL DEFAULT 0;
ALTER TABLE demo_protection ADD COLUMN opened_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE demo_protection ADD COLUMN mitigation_command_id VARCHAR(100);
CREATE TABLE demo_strategies (
	scope_key VARCHAR(64) NOT NULL, 
	account_key VARCHAR(64) NOT NULL, 
	scope JSON NOT NULL, 
	revision INTEGER NOT NULL, 
	config JSON NOT NULL, 
	PRIMARY KEY (scope_key)
);
CREATE TABLE demo_decisions (
	scope_key VARCHAR(64) NOT NULL, 
	run_id VARCHAR(100) NOT NULL, 
	account_key VARCHAR(64) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	binding_hash VARCHAR(64) NOT NULL, 
	snapshot JSON NOT NULL, 
	strategy JSON NOT NULL, 
	result JSON, 
	command_id VARCHAR(100), 
	error VARCHAR(80), 
	PRIMARY KEY (scope_key, run_id)
);
CREATE TABLE demo_account_snapshots (
	account_key VARCHAR(64) NOT NULL, 
	facts_hash VARCHAR(64) NOT NULL, 
	facts JSON NOT NULL, 
	observed_at VARCHAR(40) NOT NULL, 
	PRIMARY KEY (account_key)
);
COMMIT;
