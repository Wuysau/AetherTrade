-- Strategy memory persistence (console database).
-- Apply only through the approved shared-DEV migration process.
-- Events are append-only; state/theses/plans are rebuildable projections.
BEGIN;

CREATE TABLE public.console_strategy_memory_events (
    scope_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_id text NOT NULL,
    event_type text NOT NULL,
    source text NOT NULL CHECK (source IN ('MODEL','CONTROL','EXECUTION_LEDGER','MANUAL','SYSTEM')),
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    idempotency_key text NOT NULL,
    state_revision integer NOT NULL CHECK (state_revision >= 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    event_hash text NOT NULL CHECK (length(event_hash) = 64),
    schema_revision text NOT NULL CHECK (schema_revision = 'strategy-memory-v1'),
    PRIMARY KEY (scope_id, sequence),
    UNIQUE (scope_id, event_id),
    UNIQUE (scope_id, idempotency_key)
);
CREATE INDEX ix_strategy_memory_event_time ON public.console_strategy_memory_events(scope_id, recorded_at);
CREATE INDEX ix_strategy_memory_event_type ON public.console_strategy_memory_events(scope_id, event_type);

CREATE TABLE public.console_strategy_memory_state (
    scope_id text PRIMARY KEY,
    revision integer NOT NULL CHECK (revision >= 0),
    state_hash text NOT NULL CHECK (length(state_hash) = 64),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    updated_at timestamptz NOT NULL
);

CREATE TABLE public.console_market_theses (
    scope_id text NOT NULL,
    thesis_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    status text NOT NULL CHECK (status IN ('ACTIVE','WEAKENED','INVALIDATED','EXPIRED','SUPERSEDED')),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (scope_id, thesis_id)
);
CREATE INDEX ix_market_theses_status ON public.console_market_theses(scope_id, status);

CREATE TABLE public.console_strategy_plans (
    scope_id text NOT NULL,
    plan_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    status text NOT NULL CHECK (status IN ('PROPOSED','ACCEPTED','ACTIVE','REVISED','CANCEL_PENDING','COMPLETED','EXPIRED','REJECTED')),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (scope_id, plan_id)
);
CREATE INDEX ix_strategy_plans_status ON public.console_strategy_plans(scope_id, status);

CREATE TABLE public.console_decision_checkpoints (
    checkpoint_id text PRIMARY KEY,
    run_id text NOT NULL,
    scope_id text NOT NULL,
    created_at timestamptz NOT NULL,
    input_hash text NOT NULL CHECK (length(input_hash) = 64),
    completeness text NOT NULL CHECK (completeness IN ('COMPLETE','PARTIAL','CONTEXT_INCOMPLETE')),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object')
);
CREATE INDEX ix_decision_checkpoint_scope_time ON public.console_decision_checkpoints(scope_id, created_at);
CREATE INDEX ix_decision_checkpoint_run ON public.console_decision_checkpoints(run_id);

CREATE TABLE public.console_memory_knowledge (
    scope_id text NOT NULL,
    knowledge_id text NOT NULL,
    version integer NOT NULL CHECK (version >= 1),
    status text NOT NULL CHECK (status IN ('PROPOSED','VALIDATED','REJECTED','EXPIRED')),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (scope_id, knowledge_id)
);
CREATE INDEX ix_memory_knowledge_status ON public.console_memory_knowledge(scope_id, status);

CREATE TABLE public.console_prompt_templates (
    template_id text NOT NULL,
    version text NOT NULL,
    role text NOT NULL,
    template_hash text NOT NULL CHECK (length(template_hash) = 64),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    active integer NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    PRIMARY KEY (template_id, version)
);

-- Append-only events. Projections may be updated, but historical events cannot.
CREATE FUNCTION public.reject_memory_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'strategy memory events are append-only';
END $$;
CREATE TRIGGER strategy_memory_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON public.console_strategy_memory_events
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_memory_mutation();

COMMIT;
