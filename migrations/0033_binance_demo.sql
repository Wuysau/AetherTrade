-- Binance Demo isolated tables. Apply only through approved shared DEV migration.
-- Not applied by console startup; PostgreSQL qualification is NOT_RUN.
BEGIN;

CREATE TABLE demo_accounts (
	account_key VARCHAR(64) NOT NULL,
	identity JSON NOT NULL,
	credential_ref VARCHAR(128) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	credential_revision INTEGER NOT NULL,
	state VARCHAR(32) NOT NULL,
	budget TEXT NOT NULL,
	balances JSON,
	frozen BOOLEAN NOT NULL,
	reset_evidence TEXT,
	qualification JSON,
	PRIMARY KEY (account_key)
);

CREATE TABLE demo_commands (
	command_id VARCHAR(100) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	scope_key VARCHAR(64) NOT NULL,
	scope JSON NOT NULL,
	namespace VARCHAR(16) NOT NULL,
	client_id VARCHAR(36) NOT NULL,
	request JSON NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	authorization_ref TEXT NOT NULL,
	credential_revision INTEGER NOT NULL,
	admission_revision INTEGER NOT NULL,
	state VARCHAR(32) NOT NULL,
	method VARCHAR(8) NOT NULL,
	expires_at INTEGER NOT NULL,
	reserve_asset VARCHAR(20) NOT NULL,
	reserve_amount TEXT NOT NULL,
	original_reserve TEXT NOT NULL,
	order_id VARCHAR(64),
	order_status VARCHAR(32),
	cumulative_qty TEXT NOT NULL,
	reason TEXT,
	PRIMARY KEY (command_id),
	CONSTRAINT demo_client_identity UNIQUE (account_key, namespace, client_id)
);

CREATE TABLE demo_fills (
	fill_key VARCHAR(64) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	scope_key VARCHAR(64) NOT NULL,
	symbol VARCHAR(20) NOT NULL,
	trade_id VARCHAR(64) NOT NULL,
	order_id VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	payload JSON NOT NULL,
	PRIMARY KEY (fill_key),
	CONSTRAINT demo_fill_identity UNIQUE (account_key, symbol, trade_id)
);

CREATE TABLE demo_inbox (
	observation_id VARCHAR(64) NOT NULL,
	state VARCHAR(16) NOT NULL,
	attempts INTEGER NOT NULL,
	reason TEXT,
	PRIMARY KEY (observation_id)
);

CREATE TABLE demo_observations (
	observation_id VARCHAR(64) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	scope_key VARCHAR(64) NOT NULL,
	source VARCHAR(32) NOT NULL,
	credential_revision INTEGER NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	payload JSON NOT NULL,
	received_at INTEGER NOT NULL,
	PRIMARY KEY (observation_id)
);

CREATE TABLE demo_outbox (
	event_id VARCHAR(64) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	kind VARCHAR(40) NOT NULL,
	payload JSON NOT NULL,
	delivered BOOLEAN NOT NULL,
	PRIMARY KEY (event_id)
);

CREATE TABLE demo_postings (
	fill_key VARCHAR(64) NOT NULL,
	asset VARCHAR(20) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	scope_key VARCHAR(64) NOT NULL,
	delta TEXT NOT NULL,
	PRIMARY KEY (fill_key, asset)
);

CREATE TABLE demo_protection (
	fill_key VARCHAR(64) NOT NULL,
	account_key VARCHAR(64) NOT NULL,
	scope_key VARCHAR(64) NOT NULL,
	state VARCHAR(24) NOT NULL,
	PRIMARY KEY (fill_key)
);

CREATE TABLE demo_reconciliations (
	account_key VARCHAR(64) NOT NULL,
	symbol VARCHAR(20) NOT NULL,
	cursor JSON NOT NULL,
	complete BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (account_key, symbol)
);

COMMIT;
