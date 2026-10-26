-- Apply once to a dedicated PostgreSQL database as a migration administrator.
-- Runtime roles never own these objects. No accounts/credentials are provisioned.
BEGIN;
CREATE SCHEMA ledger;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC;

CREATE TABLE ledger.collateral_mutex (
    environment text NOT NULL CHECK (environment IN ('RESEARCH','PAPER','TESTNET','LIVE')),
    group_id text NOT NULL,
    PRIMARY KEY(environment,group_id)
);
CREATE TABLE ledger.account_bindings (
    environment text NOT NULL, account_id text NOT NULL, product text NOT NULL,
    collateral_group_id text NOT NULL, payload jsonb,
    PRIMARY KEY(environment,account_id,product),
    FOREIGN KEY(environment,collateral_group_id) REFERENCES ledger.collateral_mutex(environment,group_id)
);
CREATE TABLE ledger.aggregate_heads (
    scope_id text NOT NULL, aggregate_id text NOT NULL, sequence bigint NOT NULL CHECK(sequence >= 0),
    PRIMARY KEY(scope_id,aggregate_id)
);
CREATE TABLE ledger.transaction_observations (
    transaction_id text PRIMARY KEY, scope_id text NOT NULL,
    phase text NOT NULL CHECK(phase = 'COMMIT_MARKER'),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- A visible marker proves its transaction committed. Its absence alone does
-- not prove that a platform side effect did not occur. External crash probes
-- record before/after process observations separately.

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY[
        'accounts','collateral_groups','instruments','capabilities','policies',
        'authorizations','strategy_revisions','deployments','decision_runs',
        'proposals','intents','reservations','commands','venue_events','fills',
        'positions','protection_plans','risk_locks','reconciliation_cases',
        'ownership_transfers','cashflows','control_commands','audit_events',
        'book_snapshots','order_projections']
    LOOP
        EXECUTE format('CREATE TABLE ledger.%I (
            scope_id text NOT NULL, record_id text NOT NULL,
            sequence bigint NOT NULL CHECK(sequence > 0), payload jsonb NOT NULL,
            transaction_id text NOT NULL, processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(scope_id,record_id,sequence),
            CHECK(jsonb_typeof(payload) = ''object''))', tab);
    END LOOP;
END $$;

-- Intents are immutable contracts; amendments receive a new intent identity.
ALTER TABLE ledger.intents ADD UNIQUE(record_id), ADD CHECK(sequence = 1);
ALTER TABLE ledger.reservations ADD CHECK(sequence = 1);

CREATE TABLE ledger.reservation_assets (
    environment text NOT NULL, intent_id text NOT NULL, asset text NOT NULL CHECK(asset <> ''),
    reservation_id text NOT NULL UNIQUE,
    amount numeric NOT NULL CHECK(amount >= 0 AND amount <> 'NaN'::numeric AND amount <> 'Infinity'::numeric),
    reflected_amount numeric NOT NULL CHECK(reflected_amount >= 0 AND reflected_amount <= amount),
    PRIMARY KEY(environment,intent_id,asset)
);
CREATE TABLE ledger.command_identities (
    environment text NOT NULL, account_id text NOT NULL, product text NOT NULL,
    venue_client_order_id text NOT NULL, command_id text NOT NULL UNIQUE,
    scope_id text NOT NULL, intent_id text NOT NULL,
    PRIMARY KEY(environment,account_id,product,venue_client_order_id)
);
CREATE TABLE ledger.active_owners (
    environment text NOT NULL, account_id text NOT NULL, product text NOT NULL,
    instrument_uid text NOT NULL, venue_leg text NOT NULL,
    owner_strategy_id text NOT NULL, ownership_epoch bigint NOT NULL CHECK(ownership_epoch >= 0),
    policy_revision bigint NOT NULL CHECK(policy_revision >= 0),
    PRIMARY KEY(environment,account_id,product,instrument_uid,venue_leg)
);
CREATE TABLE ledger.domain_events (
    event_id text PRIMARY KEY, scope_id text NOT NULL, aggregate_id text NOT NULL,
    sequence bigint NOT NULL CHECK(sequence > 0), payload jsonb NOT NULL,
    payload_hash text NOT NULL, occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL, processed_at timestamptz NOT NULL,
    transaction_id text NOT NULL,
    UNIQUE(scope_id,aggregate_id,sequence), CHECK(received_at <= processed_at)
);
CREATE TABLE ledger.outbox (
    message_id text PRIMARY KEY, scope_id text NOT NULL, aggregate_id text NOT NULL,
    sequence bigint NOT NULL, kind text NOT NULL CHECK(kind IN ('COMMAND_READY','DOMAIN_EVENT')),
    event_id text REFERENCES ledger.domain_events(event_id), transaction_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(scope_id,aggregate_id,sequence,kind)
);
-- Delivery acknowledgements append; deleting an outbox row is never a send gate.
CREATE TABLE ledger.outbox_receipts (
    message_id text NOT NULL REFERENCES ledger.outbox(message_id), consumer_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(message_id,consumer_id)
);
ALTER TABLE ledger.fills
    ADD UNIQUE(record_id),
    ADD COLUMN venue text NOT NULL,
    ADD COLUMN environment text NOT NULL,
    ADD COLUMN account_id text NOT NULL,
    ADD COLUMN product text NOT NULL,
    ADD COLUMN instrument_uid text NOT NULL,
    ADD COLUMN native_trade_id text NOT NULL,
    ADD COLUMN correction_of text REFERENCES ledger.fills(record_id),
    ADD COLUMN quantity numeric NOT NULL CHECK(quantity NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    ADD COLUMN unit text NOT NULL CHECK(unit <> ''),
    ADD COLUMN price numeric NOT NULL CHECK(price > 0 AND price NOT IN ('NaN'::numeric,'Infinity'::numeric)),
    ADD COLUMN currency text NOT NULL CHECK(currency <> ''),
    ADD COLUMN event_id text NOT NULL UNIQUE REFERENCES ledger.domain_events(event_id),
    ADD UNIQUE(venue,environment,account_id,product,instrument_uid,native_trade_id),
    ADD CHECK(quantity <> 0 AND (correction_of IS NOT NULL OR quantity > 0)),
    ADD CHECK(correction_of IS NULL OR correction_of <> record_id);
CREATE TABLE ledger.fill_fees (
    fill_id text NOT NULL REFERENCES ledger.fills(record_id), fee_index integer NOT NULL,
    amount numeric NOT NULL CHECK(amount NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    currency text NOT NULL CHECK(currency <> ''), PRIMARY KEY(fill_id,fee_index)
);
ALTER TABLE ledger.cashflows ADD COLUMN amount numeric,
    ADD COLUMN currency text,
    ADD CHECK((amount IS NULL AND currency IS NULL) OR
        (amount IS NOT NULL AND currency IS NOT NULL AND currency <> '' AND
         amount NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)));

CREATE TABLE ledger.book_balances (
    scope_id text NOT NULL, sequence bigint NOT NULL, balance_index integer NOT NULL,
    amount numeric NOT NULL CHECK(amount NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    currency text NOT NULL CHECK(currency <> ''), PRIMARY KEY(scope_id,sequence,balance_index)
);

CREATE FUNCTION ledger.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ledger history is append-only' USING ERRCODE='23514'; END $$;
DO $$
DECLARE tab text;
BEGIN
    FOR tab IN SELECT tablename FROM pg_tables WHERE schemaname='ledger'
        AND tablename NOT IN ('aggregate_heads','active_owners','account_bindings','collateral_mutex')
    LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger.%I
            FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()',tab);
    END LOOP;
END $$;
CREATE FUNCTION ledger.check_append() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior jsonb; prior_sequence bigint; original ledger.fills%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'commands' THEN
        SELECT payload, sequence INTO prior, prior_sequence FROM ledger.commands WHERE scope_id=NEW.scope_id
            AND record_id=NEW.record_id ORDER BY sequence DESC LIMIT 1;
        IF prior IS NULL AND (NEW.sequence <> 1 OR
            NEW.payload->>'state' IS DISTINCT FROM 'DURABLE_READY') THEN
            RAISE EXCEPTION 'new command must be DURABLE_READY sequence 1' USING ERRCODE='23514';
        END IF;
        IF prior IS NOT NULL AND (
            (prior - 'state' - 'durable_record_ref' - 'outcome_provenance') IS DISTINCT FROM
            (NEW.payload - 'state' - 'durable_record_ref' - 'outcome_provenance')
            OR NEW.sequence <> prior_sequence + 1
            OR prior->>'state' IS DISTINCT FROM 'DURABLE_READY'
            OR NEW.payload->>'state' IS DISTINCT FROM 'MAY_HAVE_SENT') THEN
            RAISE EXCEPTION 'immutable command or illegal transition' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'risk_locks' THEN
        IF NEW.payload->>'state' IS DISTINCT FROM 'ACTIVE' THEN
            RAISE EXCEPTION 'dedicated lock resolution required' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'fills' THEN
        IF NEW.correction_of IS NOT NULL THEN
            SELECT * INTO original FROM ledger.fills WHERE record_id=NEW.correction_of;
            IF original.scope_id IS DISTINCT FROM NEW.scope_id
                OR original.venue IS DISTINCT FROM NEW.venue
                OR original.unit IS DISTINCT FROM NEW.unit
                OR original.currency IS DISTINCT FROM NEW.currency
                OR original.payload->'order' IS DISTINCT FROM NEW.payload->'order' THEN
                RAISE EXCEPTION 'correction identity or units mismatch' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER command_guard BEFORE INSERT ON ledger.commands FOR EACH ROW EXECUTE FUNCTION ledger.check_append();
CREATE TRIGGER lock_guard BEFORE INSERT ON ledger.risk_locks FOR EACH ROW EXECUTE FUNCTION ledger.check_append();
CREATE TRIGGER correction_guard BEFORE INSERT ON ledger.fills FOR EACH ROW EXECUTE FUNCTION ledger.check_append();
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC;
COMMIT;
