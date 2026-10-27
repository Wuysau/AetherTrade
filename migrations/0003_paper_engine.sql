-- Dedicated synthetic venue state. Never business-ledger or exchange evidence.
-- Apply only in an explicitly provisioned isolated PAPER PostgreSQL database.
CREATE TABLE IF NOT EXISTS paper_engine_orders (
    environment TEXT NOT NULL CHECK (environment = 'PAPER'),
    scope_key TEXT NOT NULL,
    client_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    logical_id TEXT NOT NULL,
    owner_strategy_id TEXT NOT NULL,
    ownership_epoch BIGINT NOT NULL CHECK (ownership_epoch > 0),
    quantity_unit TEXT NOT NULL,
    price_currency TEXT NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0 AND quantity <> 'NaN'),
    filled NUMERIC NOT NULL DEFAULT 0 CHECK (filled >= 0 AND filled <> 'NaN'),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN
      ('SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED')),
    pending TEXT NOT NULL DEFAULT 'NONE' CHECK (pending IN ('NONE','CANCEL','AMEND')),
    pending_command_id TEXT,
    certainty TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK (certainty IN ('CONFIRMED','UNKNOWN')),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    PRIMARY KEY (scope_key, client_id),
    UNIQUE (scope_key, order_id),
    CHECK (filled <= quantity),
    CHECK ((pending = 'NONE') = (pending_command_id IS NULL))
);

CREATE TABLE IF NOT EXISTS paper_engine_commands (
    environment TEXT NOT NULL CHECK (environment = 'PAPER'),
    scope_key TEXT NOT NULL,
    command_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    target_client_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request JSONB NOT NULL,
    PRIMARY KEY (scope_key, command_id),
    UNIQUE (scope_key, client_id),
    FOREIGN KEY (scope_key, target_client_id) REFERENCES paper_engine_orders (scope_key, client_id)
);

CREATE TABLE IF NOT EXISTS paper_engine_events (
    environment TEXT NOT NULL CHECK (environment = 'PAPER'),
    scope_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    fill_delta NUMERIC NOT NULL CHECK (fill_delta >= 0 AND fill_delta <> 'NaN'),
    price NUMERIC CHECK (price > 0 AND price <> 'NaN'),
    occurred_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scope_key, event_id),
    FOREIGN KEY (scope_key, client_id) REFERENCES paper_engine_orders (scope_key, client_id),
    CHECK (fill_delta = 0 OR price IS NOT NULL)
);
