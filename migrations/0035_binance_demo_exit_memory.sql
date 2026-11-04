-- Demo-only projected memory and hosted exit-management lineage.
-- Apply after 0034 through the approved migration workflow; never on startup.
BEGIN;
ALTER TABLE demo_outbox ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE demo_decisions ADD COLUMN memory_context JSON;
CREATE TABLE demo_memory_projection (
	scope_key VARCHAR(64) NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	account_key VARCHAR(64) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	payload JSON, 
	created_at INTEGER NOT NULL, 
	PRIMARY KEY (scope_key, event_id)
);
CREATE TABLE demo_plan_heads (
	scope_key VARCHAR(64) NOT NULL, 
	account_key VARCHAR(64) NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	proposal JSON NOT NULL, 
	source_command_id VARCHAR(100) NOT NULL, 
	predecessor_hash VARCHAR(64), 
	PRIMARY KEY (scope_key)
);
CREATE TABLE demo_plan_updates (
	management_id VARCHAR(100) NOT NULL, 
	scope_key VARCHAR(64) NOT NULL, 
	account_key VARCHAR(64) NOT NULL, 
	run_id VARCHAR(100) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	receipt JSON NOT NULL, 
	PRIMARY KEY (management_id), 
	CONSTRAINT demo_exit_update_identity UNIQUE (scope_key, run_id)
);
COMMIT;
