-- Additive isolated venue revision. Frozen0013 and all existing raw events stay unchanged.
BEGIN;
DO $$ BEGIN
 IF NOT starts_with(current_database(),'paper_engine_') THEN RAISE EXCEPTION 'isolated PAPER database required'; END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='paper_matcher_definer' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) THEN
  RAISE EXCEPTION 'unsafe matcher owner';
 END IF;
 IF EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='paper_matcher_definer')
  OR roleid=(SELECT oid FROM pg_roles WHERE rolname='paper_matcher_definer')) THEN RAISE EXCEPTION 'matcher role membership denied'; END IF;
END $$;
ALTER TABLE public.paper_engine_commands ALTER COLUMN target_client_id DROP NOT NULL;
ALTER TABLE public.paper_engine_commands ADD CONSTRAINT paper_command_target_v2 CHECK(target_client_id IS NOT NULL OR coalesce(
 operation='CREATE_REQUEST' AND request->>'namespace' IN ('BINANCE_USD_M_CONDITIONAL_ALGO','BINANCE_SPOT_EXECUTION_ALGO','BINANCE_USD_M_EXECUTION_ALGO')
 AND request->'scope'=scope_key::jsonb AND request->>'command_id'=command_id AND request->'assigned_ids'->>'parent_client_id'=client_id
 AND length(client_id)=32 AND jsonb_array_length(request->'assigned_ids'->'members')=0,false));
CREATE TABLE public.paper_market_state_v2(
 market_key text PRIMARY KEY,depth_event_id text,
 FOREIGN KEY(market_key,depth_event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_reference_prices_v2(
 market_key text NOT NULL,source text NOT NULL CHECK(source IN ('LAST','CONTRACT_PRICE','MARK_PRICE','BID','ASK')),
 price numeric NOT NULL CHECK(price>0 AND price<>'NaN' AND price<>'Infinity'),
 sequence bigint NOT NULL,event_id text NOT NULL,PRIMARY KEY(market_key,source),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_published_rules_v2(
 market_key text PRIMARY KEY,trigger_protect_ratio numeric CHECK(trigger_protect_ratio>=0 AND trigger_protect_ratio<1),
 event_id text NOT NULL,FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_session_calendars_v2(
 market_key text PRIMARY KEY,event_id text NOT NULL,calendar jsonb NOT NULL,
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_liquidity_owners_v2(
 market_key text NOT NULL,depth_event_id text NOT NULL,side text NOT NULL CHECK(side IN ('BID','ASK')),
 price numeric NOT NULL,source_quantity numeric NOT NULL CHECK(source_quantity>0),venue_account_id text NOT NULL,
 venue_order_id text NOT NULL,event_id text NOT NULL,PRIMARY KEY(market_key,depth_event_id,side,price),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id),
 FOREIGN KEY(market_key,depth_event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_market_preventions_v2(
 market_key text NOT NULL,event_id text NOT NULL,scope_key text NOT NULL,client_id text NOT NULL,prevention_id text PRIMARY KEY,
 depth_event_id text NOT NULL,side text NOT NULL,price numeric NOT NULL,maker_quantity numeric NOT NULL CHECK(maker_quantity>=0),
 taker_quantity numeric NOT NULL CHECK(taker_quantity>=0),mode text NOT NULL,venue_account_id text NOT NULL,venue_order_id text NOT NULL,
 CHECK(maker_quantity>0 OR taker_quantity>0),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id),
 FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
CREATE TABLE public.paper_order_input_dependencies_v2(
 scope_key text NOT NULL,client_id text NOT NULL,market_key text NOT NULL,event_id text NOT NULL,
 PRIMARY KEY(scope_key,client_id,market_key,event_id),
 FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_native_parents_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,venue_parent_id text NOT NULL,command_id text NOT NULL,
 request_hash text NOT NULL,namespace text NOT NULL,template text NOT NULL,status text NOT NULL,revision bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(scope_key,parent_client_id),UNIQUE(scope_key,command_id),UNIQUE(scope_key,venue_parent_id),
 FOREIGN KEY(scope_key,command_id) REFERENCES public.paper_engine_commands(scope_key,command_id));
CREATE TABLE public.paper_native_members_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,client_id text NOT NULL,node_id text NOT NULL,ordinal integer NOT NULL,
 member_kind text NOT NULL DEFAULT 'FIXED_LEG' CHECK(member_kind IN ('FIXED_LEG','ALGORITHM_CHILD')),
 PRIMARY KEY(scope_key,client_id),UNIQUE(scope_key,parent_client_id,ordinal),
 FOREIGN KEY(scope_key,parent_client_id) REFERENCES public.paper_native_parents_v2(scope_key,parent_client_id),
 FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
CREATE UNIQUE INDEX paper_native_fixed_node_v2 ON public.paper_native_members_v2(scope_key,parent_client_id,node_id) WHERE member_kind='FIXED_LEG';
CREATE TABLE public.paper_native_exclusivity_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,winner_client_id text,node_ids jsonb NOT NULL,
 PRIMARY KEY(scope_key,parent_client_id),
 FOREIGN KEY(scope_key,parent_client_id) REFERENCES public.paper_native_parents_v2(scope_key,parent_client_id),
 FOREIGN KEY(scope_key,winner_client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
CREATE TABLE public.paper_native_parent_terms_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,command_id text NOT NULL,market_key text NOT NULL,terms jsonb NOT NULL,
 PRIMARY KEY(scope_key,parent_client_id),UNIQUE(scope_key,command_id),
 FOREIGN KEY(scope_key,command_id) REFERENCES public.paper_engine_commands(scope_key,command_id));
CREATE TABLE public.paper_native_algorithm_progress_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,original_quantity numeric NOT NULL CHECK(original_quantity>0 AND original_quantity<>'NaN' AND original_quantity<>'Infinity'),
 allocated_quantity numeric NOT NULL DEFAULT 0,executed_quantity numeric NOT NULL DEFAULT 0,unused_quantity numeric NOT NULL DEFAULT 0,
 maximum_gross_notional numeric NOT NULL CHECK(maximum_gross_notional>0 AND maximum_gross_notional<>'NaN' AND maximum_gross_notional<>'Infinity'),allocated_gross_notional numeric NOT NULL DEFAULT 0,
 cumulative_gross_notional numeric NOT NULL DEFAULT 0,fee_ceilings jsonb NOT NULL,maximum_live_children integer NOT NULL CHECK(maximum_live_children>0),
 accepted_at timestamptz NOT NULL,ending_at timestamptz,accepted_source_sequence bigint NOT NULL,last_generation_event_id text,
 PRIMARY KEY(scope_key,parent_client_id),FOREIGN KEY(scope_key,parent_client_id) REFERENCES public.paper_native_parents_v2(scope_key,parent_client_id),
 CHECK(allocated_quantity>=0 AND allocated_quantity<=original_quantity AND executed_quantity>=0 AND unused_quantity>=0
  AND executed_quantity+unused_quantity<=allocated_quantity AND cumulative_gross_notional>=0
  AND cumulative_gross_notional<=allocated_gross_notional AND allocated_gross_notional<=maximum_gross_notional));
CREATE TABLE public.paper_native_child_allocations_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,child_client_id text NOT NULL,node_id text NOT NULL,allocation_id text NOT NULL,
 ordinal integer NOT NULL,market_key text NOT NULL,market_event_id text NOT NULL,quantity numeric NOT NULL CHECK(quantity>0),
 gross_ceiling numeric NOT NULL CHECK(gross_ceiling>0),PRIMARY KEY(scope_key,allocation_id),UNIQUE(scope_key,child_client_id),
 UNIQUE(scope_key,parent_client_id,ordinal),FOREIGN KEY(scope_key,parent_client_id) REFERENCES public.paper_native_parents_v2(scope_key,parent_client_id),
 FOREIGN KEY(scope_key,child_client_id) REFERENCES public.paper_engine_orders(scope_key,client_id),
 FOREIGN KEY(market_key,market_event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_native_parent_input_dependencies_v2(
 scope_key text NOT NULL,parent_client_id text NOT NULL,market_key text NOT NULL,event_id text NOT NULL,
 PRIMARY KEY(scope_key,parent_client_id,market_key,event_id),
 FOREIGN KEY(scope_key,parent_client_id) REFERENCES public.paper_native_parents_v2(scope_key,parent_client_id),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_order_runtime_v2(
 scope_key text NOT NULL,client_id text NOT NULL,active boolean NOT NULL,phase text NOT NULL,
 activation_event_id text,high_water numeric,low_water numeric,trail_started boolean NOT NULL DEFAULT false,
 effective_type text NOT NULL,effective_limit numeric,visible_remaining numeric,queue_priority bigint NOT NULL,last_reveal_event_id text,
 calendar_event_id text,day_expires_at timestamptz,prevented_quantity numeric NOT NULL DEFAULT 0 CHECK(prevented_quantity>=0),parent_activation_fill_id text,
 PRIMARY KEY(scope_key,client_id),FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
REVOKE ALL ON public.paper_market_state_v2,public.paper_reference_prices_v2,public.paper_published_rules_v2,
 public.paper_order_runtime_v2,public.paper_session_calendars_v2,public.paper_liquidity_owners_v2,public.paper_market_preventions_v2,
 public.paper_order_input_dependencies_v2,public.paper_native_parents_v2,public.paper_native_members_v2,public.paper_native_exclusivity_v2,
 public.paper_native_parent_terms_v2,public.paper_native_algorithm_progress_v2,public.paper_native_child_allocations_v2,
 public.paper_native_parent_input_dependencies_v2 FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON public.paper_market_state_v2,public.paper_reference_prices_v2,
 public.paper_published_rules_v2,public.paper_order_runtime_v2,public.paper_session_calendars_v2,public.paper_liquidity_owners_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT ON public.paper_market_preventions_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT ON public.paper_order_input_dependencies_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT,UPDATE ON public.paper_native_parents_v2,public.paper_native_exclusivity_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT ON public.paper_native_members_v2 TO paper_matcher_definer;
GRANT SELECT ON public.paper_engine_commands TO paper_matcher_definer;
GRANT SELECT ON public.paper_native_parent_terms_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT,UPDATE ON public.paper_native_algorithm_progress_v2 TO paper_matcher_definer;
GRANT SELECT,INSERT ON public.paper_native_child_allocations_v2,public.paper_native_parent_input_dependencies_v2 TO paper_matcher_definer;
GRANT INSERT ON public.paper_engine_orders,public.paper_order_terms TO paper_matcher_definer;
GRANT USAGE ON SEQUENCE public.paper_order_terms_arrival_sequence_seq TO paper_matcher_definer;
CREATE TRIGGER immutable_market_preventions_v2 BEFORE UPDATE OR DELETE ON public.paper_market_preventions_v2
 FOR EACH ROW EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_input_dependencies_v2 BEFORE UPDATE OR DELETE ON public.paper_order_input_dependencies_v2
 FOR EACH ROW EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_native_members_v2 BEFORE UPDATE OR DELETE ON public.paper_native_members_v2
 FOR EACH ROW EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_native_parent_terms_v2 BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_native_parent_terms_v2
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_native_allocations_v2 BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_native_child_allocations_v2
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_native_parent_dependencies_v2 BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_native_parent_input_dependencies_v2
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();

CREATE FUNCTION public.paper_accept_native_algorithm_v2(p_scope text,p_command text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE command public.paper_engine_commands;terms jsonb;parent text;quantity numeric;envelope jsonb;accepted timestamptz;ending timestamptz;
BEGIN
 SELECT * INTO command FROM public.paper_engine_commands WHERE scope_key=p_scope AND command_id=p_command;
 SELECT t.terms INTO terms FROM public.paper_native_parent_terms_v2 t WHERE t.scope_key=p_scope AND t.command_id=p_command;
 IF command IS NULL OR terms IS NULL OR command.target_client_id IS NOT NULL OR command.operation IS DISTINCT FROM 'CREATE_REQUEST'
  OR terms->'request' IS DISTINCT FROM command.request OR terms->'scope' IS DISTINCT FROM p_scope::jsonb
  OR NOT coalesce(terms->>'record_kind' IN ('PAPER_NATIVE_PARENT_TERMS_V2','PAPER_NATIVE_EXECUTION_TERMS_V2'),false)
  OR terms->>'contract_version' IS DISTINCT FROM '2026-10-30'
  OR NOT coalesce(terms->>'template' IN ('TWAP','VP'),false)
  OR terms->'compilation'->'original_program'->>'execution_mode' IS DISTINCT FROM 'VENUE_NATIVE'
  OR terms->'compilation'->'original_program'->>'template' IS DISTINCT FROM terms->>'template'
  OR command.request->>'operation' IS DISTINCT FROM 'SUBMIT_'||(terms->>'template')
  OR NOT coalesce(command.request->>'namespace' IN ('BINANCE_SPOT_EXECUTION_ALGO','BINANCE_USD_M_EXECUTION_ALGO'),false)
  OR jsonb_array_length(command.request->'assigned_ids'->'members')<>0
  OR terms->'write'->>'state' IS DISTINCT FROM 'MAY_HAVE_SENT'
  OR terms->'write'->>'request_hash' IS DISTINCT FROM command.request_hash
  OR terms->'accepted_clock'->>'clock_health' IS DISTINCT FROM 'HEALTHY'
  THEN RAISE EXCEPTION 'exact native parent source required'; END IF;
 parent=command.request->'assigned_ids'->>'parent_client_id';envelope=terms->'execution_envelope';quantity=(terms->>'quantity')::numeric;
 IF parent IS NULL OR parent IS DISTINCT FROM command.client_id OR parent IS DISTINCT FROM terms->>'parent_client_id'
  OR envelope->>'request_hash' IS DISTINCT FROM command.request_hash OR envelope->>'command_id' IS DISTINCT FROM p_command
  OR envelope->'scope' IS DISTINCT FROM p_scope::jsonb OR quantity IS NULL OR quantity<=0
  OR terms->>'node_id' IS DISTINCT FROM terms->'compilation'->'nodes'->0->'original'->>'node_id'
  OR terms->>'quantity' IS DISTINCT FROM terms->'compilation'->'nodes'->0->'quantity'->>'value'
  OR NOT EXISTS(SELECT FROM jsonb_array_elements(command.request->'fields') f WHERE f->>'name'='quantity' AND f->>'value'=terms->>'quantity')
  OR (envelope->>'maximum_gross_notional')::numeric IS NULL THEN RAISE EXCEPTION 'finite original native parent pool required'; END IF;
 accepted=(terms->'accepted_clock'->>'utc_now')::timestamptz;
 ending=accepted+((terms->'compilation'->'original_program'->'algorithm'->>'duration_seconds')::integer)*interval '1 second';
 INSERT INTO public.paper_native_parents_v2 VALUES(p_scope,parent,'paper-native:'||parent,p_command,command.request_hash,
  command.request->>'namespace',terms->>'template','WAITING_MARKET_SOURCE',1);
 INSERT INTO public.paper_native_algorithm_progress_v2(scope_key,parent_client_id,original_quantity,maximum_gross_notional,
  fee_ceilings,maximum_live_children,accepted_at,ending_at,accepted_source_sequence) VALUES(p_scope,parent,quantity,(envelope->>'maximum_gross_notional')::numeric,
   envelope->'fee_ceilings',(terms->>'maximum_live_children')::integer,accepted,ending,
   coalesce((SELECT sequence FROM public.paper_market_books WHERE market_key=(SELECT market_key FROM public.paper_native_parent_terms_v2 WHERE scope_key=p_scope AND command_id=p_command)),0));
END $$;
CREATE FUNCTION public.paper_check_parent_target_v2() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.target_client_id IS NULL AND NOT EXISTS(SELECT FROM public.paper_native_parents_v2 p
  JOIN public.paper_native_parent_terms_v2 t USING(scope_key,parent_client_id,command_id)
  JOIN public.paper_native_algorithm_progress_v2 a USING(scope_key,parent_client_id)
  WHERE p.scope_key=NEW.scope_key AND p.command_id=NEW.command_id AND p.parent_client_id=NEW.client_id
   AND p.request_hash=NEW.request_hash AND t.terms->'request'=NEW.request AND a.original_quantity=(t.terms->>'quantity')::numeric)
  THEN RAISE EXCEPTION 'native parent command requires its complete genuine pool'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER native_parent_command_target_v2 AFTER INSERT ON public.paper_engine_commands
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.paper_check_parent_target_v2();
REVOKE ALL ON FUNCTION public.paper_accept_native_algorithm_v2(text,text),public.paper_check_parent_target_v2() FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_accept_native_algorithm_v2(text,text) OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_check_parent_target_v2() OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;

CREATE FUNCTION public.paper_accept_native_program_v2(p_scope text,p_command text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE command public.paper_engine_commands;terms jsonb;member jsonb;parent text;item jsonb;ordinal integer=0;
BEGIN
 SELECT * INTO command FROM public.paper_engine_commands WHERE scope_key=p_scope AND command_id=p_command;
 SELECT t.terms INTO terms FROM public.paper_order_terms t WHERE t.scope_key=p_scope AND t.client_id=command.target_client_id;
 IF command IS NULL OR terms IS NULL OR terms->'request' IS DISTINCT FROM command.request
  OR terms->'compilation'->'original_program'->>'template' NOT IN ('OCO','OTO','OTOCO')
  OR terms->'compilation'->'original_program'->>'execution_mode'<>'VENUE_NATIVE'
  OR command.request->>'namespace'<>'BINANCE_SPOT_ORDER_LIST'
  OR command.request->>'operation'<>'SUBMIT_'||(terms->'compilation'->'original_program'->>'template')
  OR jsonb_array_length(command.request->'assigned_ids'->'members') NOT BETWEEN 2 AND 3
  OR jsonb_array_length(terms->'compilation'->'nodes')<>jsonb_array_length(command.request->'assigned_ids'->'members')
  THEN RAISE EXCEPTION 'actual complete native list parent required'; END IF;
 parent=command.request->'assigned_ids'->>'parent_client_id';
 IF parent IS NULL THEN RAISE EXCEPTION 'original native parent identity required'; END IF;
 INSERT INTO public.paper_native_parents_v2 VALUES(p_scope,parent,'paper-list:'||parent,p_command,command.request_hash,
  command.request->>'namespace',terms->'compilation'->'original_program'->>'template','EXECUTING',1);
 FOR member IN SELECT * FROM jsonb_array_elements(command.request->'assigned_ids'->'members') LOOP
  SELECT t.terms INTO item FROM public.paper_order_terms t WHERE t.scope_key=p_scope AND t.client_id=member->>'client_id';
  IF item IS NULL OR item->>'node_id' IS DISTINCT FROM member->>'node_id' OR item->'request' IS DISTINCT FROM command.request
   OR item->'compilation' IS DISTINCT FROM terms->'compilation'
   OR NOT EXISTS(SELECT FROM public.paper_engine_orders WHERE scope_key=p_scope AND client_id=member->>'client_id'
    AND filled=0 AND revision=1 AND lifecycle='SUBMITTED') THEN RAISE EXCEPTION 'actual zero-fill native member required'; END IF;
  INSERT INTO public.paper_native_members_v2 VALUES(p_scope,parent,member->>'client_id',member->>'node_id',ordinal,'FIXED_LEG');ordinal=ordinal+1;
 END LOOP;
 IF jsonb_array_length(terms->'compilation'->'original_program'->'exclusive_groups')=1 THEN
  INSERT INTO public.paper_native_exclusivity_v2 VALUES(p_scope,parent,NULL,terms->'compilation'->'original_program'->'exclusive_groups'->0);
 END IF;
END $$;

CREATE FUNCTION public.paper_claim_exclusive_v2(p_scope text,p_client text,p_market text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE membership public.paper_native_members_v2;exclusive public.paper_native_exclusivity_v2;peer record;book record;
 payload jsonb;event_identifier text;execution_at timestamptz;
BEGIN
 SELECT * INTO membership FROM public.paper_native_members_v2 WHERE scope_key=p_scope AND client_id=p_client;
 IF membership IS NULL THEN RETURN true; END IF;
 SELECT * INTO exclusive FROM public.paper_native_exclusivity_v2 WHERE scope_key=p_scope AND parent_client_id=membership.parent_client_id
  AND node_ids ? membership.node_id FOR UPDATE;
 IF exclusive IS NULL THEN RETURN true; END IF;
 IF exclusive.winner_client_id IS NOT NULL THEN RETURN exclusive.winner_client_id=p_client; END IF;
 UPDATE public.paper_native_exclusivity_v2 SET winner_client_id=p_client WHERE scope_key=p_scope AND parent_client_id=membership.parent_client_id;
 SELECT * INTO book FROM public.paper_market_books WHERE market_key=p_market;
 IF book IS NULL THEN RAISE EXCEPTION 'actual source for native activation required'; END IF;
 FOR peer IN SELECT o.*,t.terms FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id)
  JOIN public.paper_order_terms t USING(scope_key,client_id) WHERE m.scope_key=p_scope AND m.parent_client_id=membership.parent_client_id
   AND m.client_id<>p_client AND exclusive.node_ids ? m.node_id FOR UPDATE OF o LOOP
  IF peer.filled>0 THEN RAISE EXCEPTION 'native exclusive members both executed'; END IF;
  CONTINUE WHEN peer.lifecycle IN ('FILLED','CANCELED','EXPIRED','REJECTED');
  execution_at=greatest((peer.terms->'accepted_clock'->>'utc_now')::timestamptz,(book.observed_clock->>'utc_now')::timestamptz,book.occurred_at);
  payload=jsonb_build_object('record_kind','PAPER_MARKET_DISPOSITION','source','PAPER_MARKET_REPLAY_SYNTHETIC',
   'status','CANCELED','reason','NATIVE_EXCLUSIVE_SIBLING_ACTIVATED','parent_client_id',membership.parent_client_id,'winner_client_id',p_client,
   'market_key',p_market,'market_event_id',book.event_id,
   'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=p_market AND event_id=book.event_id),
   'model',peer.terms->'model','execution_envelope',peer.terms->'execution_envelope','execution_at',execution_at,'market_occurred_at',book.occurred_at);
  event_identifier='paper-native-cancel:'||encode(sha256(convert_to(jsonb_build_array(p_scope::jsonb,peer.client_id,book.event_id,p_client)::text,'UTF8')),'hex');
  INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
   VALUES('PAPER',p_scope,event_identifier,peer.client_id,encode(sha256(convert_to(payload::text,'UTF8')),'hex'),payload,0,NULL,execution_at);
  UPDATE public.paper_engine_orders SET lifecycle='CANCELED',revision=revision+1 WHERE scope_key=p_scope AND client_id=peer.client_id;
  UPDATE public.paper_order_runtime_v2 SET active=false,phase='CANCELED',visible_remaining=0 WHERE scope_key=p_scope AND client_id=peer.client_id;
  INSERT INTO public.paper_order_input_dependencies_v2 VALUES(p_scope,peer.client_id,p_market,book.event_id) ON CONFLICT DO NOTHING;
 END LOOP;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.paper_accept_native_program_v2(text,text),public.paper_claim_exclusive_v2(text,text,text) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_accept_native_program_v2(text,text) OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_claim_exclusive_v2(text,text,text) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;

CREATE FUNCTION public.paper_record_market_input_v2() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE point jsonb;
BEGIN
 IF NEW.payload->>'kind' IN ('DEPTH_SNAPSHOT','DEPTH_DELTA') THEN
  INSERT INTO public.paper_market_state_v2 VALUES(NEW.market_key,NEW.event_id)
   ON CONFLICT(market_key) DO UPDATE SET depth_event_id=excluded.depth_event_id;
 ELSIF NEW.payload->>'contract_version'='2026-10-30' AND NEW.payload->>'kind'='REFERENCE_PRICES' THEN
  IF NEW.payload->>'source' IS DISTINCT FROM 'PAPER_MARKET_REPLAY_SYNTHETIC'
   OR jsonb_array_length(NEW.payload->'prices') NOT BETWEEN 1 AND 5 THEN RAISE EXCEPTION 'bounded reference source required'; END IF;
  FOR point IN SELECT * FROM jsonb_array_elements(NEW.payload->'prices') LOOP
   IF EXISTS(SELECT FROM public.paper_reference_prices_v2 WHERE market_key=NEW.market_key AND source=point->>'source'
    AND sequence>=NEW.sequence) THEN RAISE EXCEPTION 'reference sequence regression'; END IF;
   INSERT INTO public.paper_reference_prices_v2 VALUES(NEW.market_key,point->>'source',(point->>'price')::numeric,NEW.sequence,NEW.event_id)
    ON CONFLICT(market_key,source) DO UPDATE SET price=excluded.price,sequence=excluded.sequence,event_id=excluded.event_id;
  END LOOP;
 ELSIF NEW.payload->>'contract_version'='2026-10-30' AND NEW.payload->>'kind'='SESSION_CALENDAR' THEN
  IF NEW.payload->>'product'<>'US_EQUITY' OR NEW.payload->>'source'<>'PAPER_MARKET_REPLAY_SYNTHETIC'
   OR jsonb_array_length(NEW.payload->'windows') NOT BETWEEN 1 AND 512 THEN RAISE EXCEPTION 'bounded calendar source required'; END IF;
  INSERT INTO public.paper_session_calendars_v2 VALUES(NEW.market_key,NEW.event_id,NEW.payload)
   ON CONFLICT(market_key) DO UPDATE SET event_id=excluded.event_id,calendar=excluded.calendar;
 ELSIF NEW.payload->>'contract_version'='2026-10-30' AND NEW.payload->>'kind'='LIQUIDITY_OWNERSHIP' THEN
  IF NEW.payload->>'source'<>'PAPER_MARKET_REPLAY_SYNTHETIC' OR jsonb_array_length(NEW.payload->'owners') NOT BETWEEN 1 AND 8192
   OR NOT EXISTS(SELECT FROM public.paper_market_state_v2 s JOIN public.paper_market_inputs i
    ON i.market_key=s.market_key AND i.event_id=s.depth_event_id WHERE s.market_key=NEW.market_key
    AND s.depth_event_id=NEW.payload->>'depth_event_id' AND i.payload_hash=NEW.payload->>'depth_hash')
   THEN RAISE EXCEPTION 'exact current liquidity source required'; END IF;
  FOR point IN SELECT * FROM jsonb_array_elements(NEW.payload->'owners') LOOP
   IF NOT EXISTS(SELECT FROM public.paper_market_levels WHERE market_key=NEW.market_key AND side=point->>'side'
    AND price=(point->>'price')::numeric AND reported=(point->>'source_quantity')::numeric)
    THEN RAISE EXCEPTION 'liquidity ownership quantity changed'; END IF;
   INSERT INTO public.paper_liquidity_owners_v2 VALUES(NEW.market_key,NEW.payload->>'depth_event_id',point->>'side',
    (point->>'price')::numeric,(point->>'source_quantity')::numeric,point->>'venue_account_id',point->>'venue_order_id',NEW.event_id);
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER record_market_input_v2 AFTER INSERT ON public.paper_market_inputs
 FOR EACH ROW EXECUTE FUNCTION public.paper_record_market_input_v2();
INSERT INTO public.paper_market_state_v2(market_key,depth_event_id)
 SELECT DISTINCT ON(market_key) market_key,event_id FROM public.paper_market_inputs
 WHERE payload->>'kind' IN ('DEPTH_SNAPSHOT','DEPTH_DELTA') ORDER BY market_key,sequence DESC;

CREATE FUNCTION public.paper_resolve_price_v2(p_market text,p_spec jsonb) RETURNS numeric
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE basis text;book_side text;level_offset integer;result numeric;mode text;
BEGIN
 mode=p_spec->'price'->>'kind';
 IF mode='FIXED_LIMIT' THEN RETURN (p_spec->'price'->>'value')::numeric; END IF;
 IF mode='MARKET' THEN RETURN NULL; END IF;
 IF mode='PEG' THEN
  basis=CASE WHEN p_spec->'price'->>'peg_type'='PRIMARY_PEG' THEN 'SAME' ELSE 'OPPOSITE' END;
  level_offset=(p_spec->'price'->>'offset_levels')::integer;
 ELSIF mode='PRICE_MATCH' THEN
  basis=CASE WHEN starts_with(p_spec->'price'->>'price_match','QUEUE') THEN 'SAME' ELSE 'OPPOSITE' END;
  level_offset=CASE WHEN position('_' IN (p_spec->'price'->>'price_match'))=0 THEN 0
   ELSE split_part(p_spec->'price'->>'price_match','_',2)::integer-1 END;
 ELSE RAISE EXCEPTION 'price mode unsupported'; END IF;
 IF level_offset NOT BETWEEN 0 AND 100 THEN RAISE EXCEPTION 'price level invalid'; END IF;
 book_side=CASE WHEN (p_spec->>'side'='BUY')=(basis='SAME') THEN 'BID' ELSE 'ASK' END;
 SELECT price INTO result FROM public.paper_market_levels WHERE market_key=p_market AND side=book_side AND reported>0
  ORDER BY CASE WHEN book_side='BID' THEN -price ELSE price END OFFSET level_offset LIMIT 1;
 RETURN result;
END $$;

CREATE FUNCTION public.paper_prepare_order_v2(p_scope text,p_client text,p_market text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE terms jsonb;spec jsonb;trigger_spec jsonb;runtime public.paper_order_runtime_v2;
 ref record;price_source text;fired boolean;ready boolean;activation numeric;offset_value numeric;offset_unit text;
 mark_price numeric;contract_price numeric;protect_ratio numeric;original public.paper_engine_orders;arrival bigint;
 current_event text;resolved_price numeric;book record;expired_at timestamptz;payload jsonb;event_identifier text;
 calendar_source record;session_kind text;current_time_at timestamptz;day_close timestamptz;
 incoming_link jsonb;working_order record;full_fill_identifier text;working_sum numeric;
BEGIN
 SELECT t.terms INTO terms FROM public.paper_order_terms t WHERE t.scope_key=p_scope AND t.client_id=p_client;
 IF coalesce((terms->>'model_revision')::int,1)<>2 THEN RETURN true; END IF;
 spec=terms->'spec';trigger_spec=spec->'trigger';
 SELECT * INTO original FROM public.paper_engine_orders WHERE scope_key=p_scope AND client_id=p_client FOR UPDATE;
 IF original.lifecycle IN ('FILLED','CANCELED','EXPIRED','REJECTED') THEN RETURN false; END IF;
 SELECT * INTO book FROM public.paper_market_books WHERE market_key=p_market;
 INSERT INTO public.paper_order_input_dependencies_v2
  SELECT p_scope,p_client,p_market,i.event_id FROM public.paper_market_inputs i WHERE i.market_key=p_market
   AND (i.event_id=book.event_id OR i.event_id=(SELECT depth_event_id FROM public.paper_market_state_v2 WHERE market_key=p_market))
  ON CONFLICT DO NOTHING;
 IF spec->'tif'->>'kind'='GTD' AND book IS NOT NULL
  AND (book.observed_clock->>'utc_now')::timestamptz>=(spec->'tif'->>'expires_at')::timestamptz THEN
  expired_at=greatest((terms->'accepted_clock'->>'utc_now')::timestamptz,(book.observed_clock->>'utc_now')::timestamptz,book.occurred_at);
  payload=jsonb_build_object('record_kind','PAPER_MARKET_DISPOSITION','source','PAPER_MARKET_REPLAY_SYNTHETIC',
   'status','EXPIRED','reason','VENUE_GTD_EXPIRED','market_key',p_market,'market_event_id',book.event_id,
   'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=p_market AND event_id=book.event_id),
   'execution_at',expired_at,'market_occurred_at',book.occurred_at,'model',terms->'model','execution_envelope',terms->'execution_envelope');
  event_identifier='paper-v2-expiry:'||encode(sha256(convert_to(jsonb_build_array(p_scope::jsonb,p_client,book.event_id,'GTD')::text,'UTF8')),'hex');
  INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
   VALUES('PAPER',p_scope,event_identifier,p_client,encode(sha256(convert_to(payload::text,'UTF8')),'hex'),payload,0,NULL,expired_at);
  UPDATE public.paper_engine_orders SET lifecycle='EXPIRED',revision=revision+1 WHERE scope_key=p_scope AND client_id=p_client;
  UPDATE public.paper_order_runtime_v2 SET active=false,phase='EXPIRED',visible_remaining=0 WHERE scope_key=p_scope AND client_id=p_client;
  RETURN false;
 END IF;
 SELECT arrival_sequence INTO arrival FROM public.paper_order_terms WHERE scope_key=p_scope AND client_id=p_client;
 SELECT event_id INTO current_event FROM public.paper_market_books WHERE market_key=p_market;
 INSERT INTO public.paper_order_runtime_v2(scope_key,client_id,active,phase,effective_type,effective_limit,visible_remaining,queue_priority)
  VALUES(p_scope,p_client,trigger_spec->>'kind'='NONE',CASE WHEN trigger_spec->>'kind'='NONE' THEN 'ACTIVE' ELSE 'WAITING_REFERENCE' END,
   CASE WHEN spec->'price'->>'kind'='MARKET' THEN 'MARKET' ELSE 'LIMIT' END,(spec->'price'->>'value')::numeric,
   CASE WHEN spec->>'iceberg_quantity' IS NULL THEN NULL ELSE least((spec->>'iceberg_quantity')::numeric,original.quantity-original.filled) END,arrival)
  ON CONFLICT(scope_key,client_id) DO NOTHING;
 SELECT * INTO runtime FROM public.paper_order_runtime_v2 r WHERE r.scope_key=p_scope AND r.client_id=p_client FOR UPDATE;
 IF coalesce((spec->>'reduce_only')::boolean,false) OR spec->>'position_side'<>'BOTH' THEN
  UPDATE public.paper_order_runtime_v2 SET phase='WAITING_POSITION_SOURCE' WHERE scope_key=p_scope AND client_id=p_client;
  RETURN false;
 END IF;
 SELECT l INTO incoming_link FROM jsonb_array_elements(terms->'compilation'->'original_program'->'links') l WHERE l->>'target'=terms->>'node_id';
 IF incoming_link IS NOT NULL AND runtime.parent_activation_fill_id IS NULL THEN
  IF incoming_link->>'condition'<>'FULL_FILL' THEN RAISE EXCEPTION 'authentic net receipt activation stage required'; END IF;
  SELECT o.* INTO working_order FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id)
   WHERE m.scope_key=p_scope AND m.node_id=incoming_link->>'source'
    AND m.parent_client_id=(SELECT parent_client_id FROM public.paper_native_members_v2 WHERE scope_key=p_scope AND client_id=p_client);
  IF working_order IS NULL OR working_order.lifecycle<>'FILLED' OR working_order.filled<>working_order.quantity THEN
   UPDATE public.paper_order_runtime_v2 SET active=false,phase='WAITING_FULL_WORKING_FILL' WHERE scope_key=p_scope AND client_id=p_client;RETURN false;
  END IF;
  SELECT sum(fill_delta) INTO working_sum FROM public.paper_engine_events WHERE scope_key=p_scope AND client_id=working_order.client_id AND fill_delta>0;
  IF working_sum IS DISTINCT FROM working_order.quantity THEN RAISE EXCEPTION 'complete actual working fills required'; END IF;
  SELECT event_id INTO full_fill_identifier FROM public.paper_engine_events WHERE scope_key=p_scope AND client_id=working_order.client_id AND fill_delta>0
   ORDER BY occurred_at DESC,event_id DESC LIMIT 1;
  UPDATE public.paper_order_runtime_v2 SET parent_activation_fill_id=full_fill_identifier,active=trigger_spec->>'kind'='NONE',
   phase=CASE WHEN trigger_spec->>'kind'='NONE' THEN 'ACTIVE' ELSE 'WAITING_REFERENCE' END WHERE scope_key=p_scope AND client_id=p_client;
  runtime.parent_activation_fill_id=full_fill_identifier;runtime.active=trigger_spec->>'kind'='NONE';
  INSERT INTO public.paper_order_input_dependencies_v2
   SELECT p_scope,p_client,market_key,event_id FROM public.paper_order_input_dependencies_v2
    WHERE scope_key=p_scope AND client_id=working_order.client_id ON CONFLICT DO NOTHING;
 END IF;
 IF terms->'scope'->>'product'='US_EQUITY' THEN
  session_kind=coalesce(spec->>'trading_session','RTH');
  current_time_at=greatest((terms->'accepted_clock'->>'utc_now')::timestamptz,(book.observed_clock->>'utc_now')::timestamptz,book.occurred_at);
  SELECT * INTO calendar_source FROM public.paper_session_calendars_v2 WHERE market_key=p_market;
  IF calendar_source IS NOT NULL THEN
   INSERT INTO public.paper_order_input_dependencies_v2 VALUES(p_scope,p_client,p_market,calendar_source.event_id) ON CONFLICT DO NOTHING;
  END IF;
  IF spec->'tif'->>'kind'='DAY' AND runtime.day_expires_at IS NULL AND calendar_source IS NOT NULL THEN
   SELECT min((w->>'closes_at')::timestamptz) INTO day_close FROM jsonb_array_elements(calendar_source.calendar->'windows') w
    WHERE w->>'session'=session_kind AND (w->>'closes_at')::timestamptz>(terms->'accepted_clock'->>'utc_now')::timestamptz;
   UPDATE public.paper_order_runtime_v2 SET day_expires_at=day_close,calendar_event_id=calendar_source.event_id
    WHERE scope_key=p_scope AND client_id=p_client;
   runtime.day_expires_at=day_close;runtime.calendar_event_id=calendar_source.event_id;
  END IF;
  IF runtime.day_expires_at IS NOT NULL AND current_time_at>=runtime.day_expires_at THEN
   payload=jsonb_build_object('record_kind','PAPER_MARKET_DISPOSITION','source','PAPER_MARKET_REPLAY_SYNTHETIC',
    'status','EXPIRED','reason','VENUE_DAY_EXPIRED','market_key',p_market,'market_event_id',book.event_id,
    'calendar_event_id',runtime.calendar_event_id,'day_expires_at',runtime.day_expires_at,
    'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=p_market AND event_id=book.event_id),
    'execution_at',current_time_at,'market_occurred_at',book.occurred_at,'model',terms->'model','execution_envelope',terms->'execution_envelope');
   event_identifier='paper-v2-expiry:'||encode(sha256(convert_to(jsonb_build_array(p_scope::jsonb,p_client,book.event_id,'DAY')::text,'UTF8')),'hex');
   INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
    VALUES('PAPER',p_scope,event_identifier,p_client,encode(sha256(convert_to(payload::text,'UTF8')),'hex'),payload,0,NULL,current_time_at);
   UPDATE public.paper_engine_orders SET lifecycle='EXPIRED',revision=revision+1 WHERE scope_key=p_scope AND client_id=p_client;
   UPDATE public.paper_order_runtime_v2 SET active=false,phase='EXPIRED',visible_remaining=0 WHERE scope_key=p_scope AND client_id=p_client;
   RETURN false;
  END IF;
  IF calendar_source IS NULL OR book IS NULL OR current_time_at>=(calendar_source.calendar->>'valid_until')::timestamptz THEN
   UPDATE public.paper_order_runtime_v2 SET phase='WAITING_SESSION_CALENDAR' WHERE scope_key=p_scope AND client_id=p_client;RETURN false;
  END IF;
  IF NOT EXISTS(SELECT FROM jsonb_array_elements(calendar_source.calendar->'windows') w WHERE w->>'session'=session_kind
   AND (w->>'opens_at')::timestamptz<=current_time_at AND current_time_at<(w->>'closes_at')::timestamptz) THEN
   UPDATE public.paper_order_runtime_v2 SET phase='WAITING_SESSION' WHERE scope_key=p_scope AND client_id=p_client;RETURN false;
  END IF;
  UPDATE public.paper_order_runtime_v2 SET calendar_event_id=calendar_source.event_id,phase='ACTIVE' WHERE scope_key=p_scope AND client_id=p_client;
 END IF;
 IF runtime.active THEN
  IF spec->>'iceberg_quantity' IS NOT NULL THEN
   IF runtime.visible_remaining<=0 AND runtime.last_reveal_event_id IS DISTINCT FROM current_event THEN
    UPDATE public.paper_order_runtime_v2 SET visible_remaining=least((spec->>'iceberg_quantity')::numeric,original.quantity-original.filled),
     queue_priority=(SELECT coalesce(max(arrival_sequence),0)+1 FROM public.paper_order_terms),last_reveal_event_id=current_event
     WHERE scope_key=p_scope AND client_id=p_client;
   ELSIF runtime.last_reveal_event_id IS NULL AND current_event IS NOT NULL THEN
    UPDATE public.paper_order_runtime_v2 SET last_reveal_event_id=current_event WHERE scope_key=p_scope AND client_id=p_client;
   END IF;
  END IF;
  IF runtime.effective_type='LIMIT' AND runtime.effective_limit IS NULL THEN
   resolved_price=public.paper_resolve_price_v2(p_market,spec);
   UPDATE public.paper_order_runtime_v2 SET effective_limit=resolved_price,
    phase=CASE WHEN resolved_price IS NULL THEN 'WAITING_PRICING_DEPTH' ELSE 'ACTIVE' END WHERE scope_key=p_scope AND client_id=p_client;
   RETURN resolved_price IS NOT NULL;
  END IF;
  RETURN true;
 END IF;
 price_source=trigger_spec->>'source';
 IF price_source='BID_ASK' THEN price_source=CASE WHEN spec->>'side'='BUY' THEN 'ASK' ELSE 'BID' END; END IF;
 SELECT price,event_id INTO ref FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source=price_source;
 IF ref IS NULL THEN RETURN false; END IF;
 INSERT INTO public.paper_order_input_dependencies_v2 VALUES(p_scope,p_client,p_market,ref.event_id) ON CONFLICT DO NOTHING;
 IF trigger_spec->>'kind'='STATIC' THEN
  activation=(trigger_spec->>'price')::numeric;
  fired=CASE WHEN (spec->>'side'='BUY')=(trigger_spec->>'purpose'='STOP_LOSS') THEN ref.price>=activation ELSE ref.price<=activation END;
 ELSIF trigger_spec->>'kind'='TRAILING' THEN
  IF NOT runtime.trail_started THEN
   activation=(trigger_spec->>'activation_price')::numeric;
   ready=activation IS NULL OR CASE WHEN (spec->>'side'='BUY')=(trigger_spec->>'purpose'='STOP_LOSS')
    THEN ref.price>=activation ELSE ref.price<=activation END;
   IF NOT ready THEN RETURN false; END IF;
   UPDATE public.paper_order_runtime_v2 SET trail_started=true,high_water=ref.price,low_water=ref.price,phase='TRAILING_ACTIVE'
    WHERE scope_key=p_scope AND client_id=p_client;
   RETURN false;
  END IF;
  runtime.high_water=greatest(runtime.high_water,ref.price);runtime.low_water=least(runtime.low_water,ref.price);
  UPDATE public.paper_order_runtime_v2 SET high_water=runtime.high_water,low_water=runtime.low_water WHERE scope_key=p_scope AND client_id=p_client;
  offset_value=(trigger_spec->>'offset')::numeric;offset_unit=trigger_spec->>'offset_unit';
  IF offset_unit='BIPS' THEN offset_value=offset_value/10000;ELSIF offset_unit='PERCENT' THEN offset_value=offset_value/100;
  ELSIF offset_unit<>'PRICE' THEN RAISE EXCEPTION 'trailing unit unknown'; END IF;
  fired=CASE WHEN spec->>'side'='SELL' THEN ref.price<=runtime.high_water-CASE WHEN offset_unit='PRICE' THEN offset_value ELSE runtime.high_water*offset_value END
   ELSE ref.price>=runtime.low_water+CASE WHEN offset_unit='PRICE' THEN offset_value ELSE runtime.low_water*offset_value END END;
 ELSE RAISE EXCEPTION 'unsupported V2 trigger'; END IF;
 IF NOT fired THEN RETURN false; END IF;
 IF coalesce((trigger_spec->>'price_protect')::boolean,false) THEN
  INSERT INTO public.paper_order_input_dependencies_v2
   SELECT p_scope,p_client,p_market,event_id FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source IN ('MARK_PRICE','CONTRACT_PRICE')
   UNION SELECT p_scope,p_client,p_market,event_id FROM public.paper_published_rules_v2 WHERE market_key=p_market
   ON CONFLICT DO NOTHING;
  SELECT trigger_protect_ratio INTO protect_ratio FROM public.paper_published_rules_v2 WHERE market_key=p_market;
  SELECT price INTO mark_price FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source='MARK_PRICE';
  SELECT price INTO contract_price FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source='CONTRACT_PRICE';
  IF protect_ratio IS NULL OR mark_price IS NULL OR contract_price IS NULL OR abs(mark_price-contract_price)/contract_price>protect_ratio THEN
   UPDATE public.paper_order_runtime_v2 SET phase='WAITING_PRICE_PROTECT_DATA' WHERE scope_key=p_scope AND client_id=p_client;
   RETURN false;
  END IF;
 END IF;
 IF NOT public.paper_claim_exclusive_v2(p_scope,p_client,p_market) THEN RETURN false; END IF;
 UPDATE public.paper_order_runtime_v2 SET active=true,phase='ACTIVE',activation_event_id=ref.event_id
  WHERE scope_key=p_scope AND client_id=p_client;
 RETURN public.paper_prepare_order_v2(p_scope,p_client,p_market);
END $$;
REVOKE ALL ON FUNCTION public.paper_record_market_input_v2(),public.paper_prepare_order_v2(text,text,text),public.paper_resolve_price_v2(text,jsonb) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_record_market_input_v2() OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_prepare_order_v2(text,text,text) OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_resolve_price_v2(text,jsonb) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;

CREATE FUNCTION public.paper_advance_native_algorithms_v2(p_market text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE book record;parent record;terms jsonb;model jsonb;node jsonb;spec jsonb;envelope jsonb;child_envelope jsonb;child_terms jsonb;
 duration integer;slots integer;due_slots integer;units numeric;target_units numeric;quantity numeric;step numeric;gross numeric;fee numeric;
 ordinal integer;allocation text;client text;current_at timestamptz;elapsed numeric;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(p_market,0));
 SELECT * INTO book FROM public.paper_market_books WHERE market_key=p_market FOR UPDATE;
 IF book IS NULL THEN RETURN; END IF;
 FOR parent IN SELECT p.*,g.*,t.terms FROM public.paper_native_parents_v2 p JOIN public.paper_native_parent_terms_v2 t
  USING(scope_key,parent_client_id,command_id) JOIN public.paper_native_algorithm_progress_v2 g USING(scope_key,parent_client_id)
  WHERE t.market_key=p_market AND p.status IN ('WAITING_MARKET_SOURCE','EXECUTING','WAITING_CHILD_SIZE','WAITING_DEPTH','WAITING_POSITION_SOURCE')
  ORDER BY g.accepted_at,p.scope_key,p.parent_client_id FOR UPDATE OF p,g LOOP
  terms=parent.terms;
  -- An older parent-only record never silently gains child-generation semantics.
  CONTINUE WHEN terms->>'record_kind'<>'PAPER_NATIVE_EXECUTION_TERMS_V2' OR book.sequence<=parent.accepted_source_sequence;
  model=terms->'execution_model';node=terms->'compilation'->'nodes'->0;spec=node->'original'->'order';envelope=terms->'execution_envelope';
  IF parent.original_quantity IS DISTINCT FROM (terms->>'quantity')::numeric
   OR parent.maximum_gross_notional IS DISTINCT FROM (envelope->>'maximum_gross_notional')::numeric
   OR parent.fee_ceilings IS DISTINCT FROM envelope->'fee_ceilings'
   OR parent.maximum_live_children IS DISTINCT FROM (terms->>'maximum_live_children')::integer
   THEN RAISE EXCEPTION 'native pool immutable source changed'; END IF;
  IF terms->>'template' IS DISTINCT FROM 'TWAP' OR model->>'model_kind' IS DISTINCT FROM 'PAPER_NATIVE_TWAP_CLOCK_MODEL'
   OR terms->>'record_revision' IS DISTINCT FROM '2'
   OR model->>'model_revision' IS DISTINCT FROM '1' OR model->>'slice_seconds' IS DISTINCT FROM '60'
   OR model->>'child_tif' IS DISTINCT FROM 'IOC' OR model->>'unused_policy' IS DISTINCT FROM 'NO_AUTOMATIC_RECYCLE'
   THEN RAISE EXCEPTION 'explicit native execution model required'; END IF;
  current_at=(book.observed_clock->>'utc_now')::timestamptz;
  CONTINUE WHEN current_at<parent.accepted_at OR book.observed_clock->>'clock_health'<>'HEALTHY';
  INSERT INTO public.paper_native_parent_input_dependencies_v2 VALUES(parent.scope_key,parent.parent_client_id,p_market,book.event_id) ON CONFLICT DO NOTHING;
  INSERT INTO public.paper_native_parent_input_dependencies_v2
   SELECT parent.scope_key,parent.parent_client_id,p_market,depth_event_id FROM public.paper_market_state_v2
    WHERE market_key=p_market AND depth_event_id IS NOT NULL ON CONFLICT DO NOTHING;
  IF current_at>=parent.ending_at THEN
   UPDATE public.paper_native_parents_v2 SET status='STOPPED_WITH_REMAINING_POOL',revision=revision+1
    WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;CONTINUE;
  END IF;
  IF coalesce((spec->>'reduce_only')::boolean,false) OR spec->>'position_side'<>'BOTH' THEN
   UPDATE public.paper_native_parents_v2 SET status='WAITING_POSITION_SOURCE' WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;
   CONTINUE;
  END IF;
  IF NOT EXISTS(SELECT FROM public.paper_market_state_v2 WHERE market_key=p_market AND depth_event_id IS NOT NULL) THEN
   UPDATE public.paper_native_parents_v2 SET status='WAITING_DEPTH' WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;CONTINUE;
  END IF;
  CONTINUE WHEN parent.last_generation_event_id=book.event_id;
  CONTINUE WHEN (SELECT count(*) FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id)
   WHERE m.scope_key=parent.scope_key AND m.parent_client_id=parent.parent_client_id
    AND o.lifecycle NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED'))>=parent.maximum_live_children;
  duration=(terms->'compilation'->'original_program'->'algorithm'->>'duration_seconds')::integer;
  IF duration NOT BETWEEN 1 AND 86400 THEN RAISE EXCEPTION 'bounded native duration required'; END IF;
  slots=(duration+59)/60;elapsed=extract(epoch FROM current_at-parent.accepted_at);due_slots=least(slots,1+floor(elapsed/60)::integer);
  step=(terms->>'quantity_step')::numeric;units=div(parent.original_quantity,step);
  IF mod(parent.original_quantity,step)<>0 THEN RAISE EXCEPTION 'native pool quantity grid changed'; END IF;
  target_units=div(units*due_slots,slots);quantity=target_units*step-parent.allocated_quantity;
  CONTINUE WHEN quantity<=0;
  IF quantity<(terms->'rules'->>'minimum_quantity')::numeric OR
   quantity*(envelope->>'minimum_price')::numeric*(terms->>'multiplier')::numeric<(terms->'rules'->>'minimum_notional')::numeric THEN
   UPDATE public.paper_native_parents_v2 SET status='WAITING_CHILD_SIZE' WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;CONTINUE;
  END IF;
  gross=quantity*(envelope->>'maximum_price')::numeric*(terms->>'multiplier')::numeric;
  IF parent.allocated_gross_notional+gross>parent.maximum_gross_notional THEN RAISE EXCEPTION 'native pool allocation budget exceeded'; END IF;
  SELECT coalesce(max(m.ordinal)+1,0) INTO ordinal FROM public.paper_native_members_v2 m WHERE m.scope_key=parent.scope_key AND m.parent_client_id=parent.parent_client_id;
  allocation='paper-native-allocation:'||encode(sha256(convert_to(jsonb_build_array(parent.scope_key::jsonb,parent.parent_client_id,ordinal,book.event_id)::text,'UTF8')),'hex');
  client=substr(encode(sha256(convert_to(jsonb_build_array('native-child',parent.scope_key::jsonb,parent.parent_client_id,ordinal,book.event_id)::text,'UTF8')),'hex'),1,32);
  fee=gross*(terms->'model'->>'quote_fee_rate')::numeric;
  child_envelope=envelope||jsonb_build_object('maximum_gross_notional',gross::text,'fee_ceilings',CASE WHEN fee=0 THEN '[]'::jsonb ELSE
   jsonb_build_array(jsonb_build_object('asset',terms->'rules'->>'quote_asset','amount',fee::text)) END);
  child_terms=jsonb_build_object('contract_version','2026-10-30','record_kind','PAPER_NATIVE_CHILD_TERMS_V2','model_revision',2,
   'scope',terms->'scope','client_id',client,'node_id',terms->>'node_id','namespace',node->>'namespace','quantity',quantity::text,
   'quantity_step',terms->>'quantity_step','side',spec->>'side','order_type',node->>'order_type','limit_price',spec->'price'->>'value',
   'post_only',false,'tif','IOC','multiplier',terms->>'multiplier','quote_asset',terms->'rules'->>'quote_asset',
   'quote_fee_rate',terms->'model'->>'quote_fee_rate','accepted_clock',book.observed_clock,'spec',spec,'parent_client_id',parent.parent_client_id,
   'allocation_id',allocation,'generation_event_id',book.event_id,'allocation_ordinal',ordinal,'write',terms->'write','request',terms->'request',
   'compilation',terms->'compilation','rules',terms->'rules','model',terms->'model','execution_envelope',child_envelope);
  INSERT INTO public.paper_engine_orders(environment,scope_key,client_id,order_id,logical_id,owner_strategy_id,ownership_epoch,quantity_unit,price_currency,quantity)
   VALUES('PAPER',parent.scope_key,client,'paper-native-child:'||allocation,client,terms->'write'->>'owner_strategy_id',
    (terms->'write'->>'ownership_epoch')::bigint,terms->>'quantity_unit',terms->'rules'->>'quote_asset',quantity);
  INSERT INTO public.paper_order_terms(scope_key,client_id,market_key,terms) VALUES(parent.scope_key,client,p_market,child_terms);
  INSERT INTO public.paper_native_members_v2 VALUES(parent.scope_key,parent.parent_client_id,client,terms->>'node_id',ordinal,'ALGORITHM_CHILD');
  INSERT INTO public.paper_native_child_allocations_v2 VALUES(parent.scope_key,parent.parent_client_id,client,terms->>'node_id',allocation,
   ordinal,p_market,book.event_id,quantity,gross);
  UPDATE public.paper_native_algorithm_progress_v2 SET allocated_quantity=allocated_quantity+quantity,
   allocated_gross_notional=allocated_gross_notional+gross,last_generation_event_id=book.event_id
   WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;
  UPDATE public.paper_native_parents_v2 SET status='EXECUTING',revision=revision+1 WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;
 END LOOP;
END $$;
CREATE FUNCTION public.paper_refresh_native_algorithms_v2(p_market text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent record;allocated numeric;executed numeric;unused numeric;gross numeric;event_quantity numeric;unresolved boolean;
BEGIN
 FOR parent IN SELECT p.*,g.original_quantity,g.allocated_quantity,g.maximum_gross_notional FROM public.paper_native_parents_v2 p
  JOIN public.paper_native_algorithm_progress_v2 g USING(scope_key,parent_client_id) JOIN public.paper_native_parent_terms_v2 t USING(scope_key,parent_client_id,command_id)
  WHERE t.market_key=p_market FOR UPDATE OF p,g LOOP
  SELECT coalesce(sum(o.quantity),0),coalesce(sum(o.filled),0),
   coalesce(sum(CASE WHEN o.lifecycle IN ('CANCELED','EXPIRED','REJECTED') THEN o.quantity-o.filled ELSE 0 END),0),
   coalesce(bool_or(o.certainty<>'CONFIRMED' OR o.pending<>'NONE' OR o.lifecycle NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED')),false)
   INTO allocated,executed,unused,unresolved FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id)
    WHERE m.scope_key=parent.scope_key AND m.parent_client_id=parent.parent_client_id;
  SELECT coalesce(sum(e.fill_delta),0),coalesce(sum((e.payload->>'gross_notional')::numeric),0) INTO event_quantity,gross
   FROM public.paper_native_members_v2 m JOIN public.paper_engine_events e USING(scope_key,client_id)
    WHERE m.scope_key=parent.scope_key AND m.parent_client_id=parent.parent_client_id AND e.payload->>'record_kind'='PAPER_MARKET_FILL';
  IF allocated<>parent.allocated_quantity OR event_quantity<>executed OR gross>parent.maximum_gross_notional
   THEN RAISE EXCEPTION 'native pool actual child inventory mismatch'; END IF;
  UPDATE public.paper_native_algorithm_progress_v2 SET executed_quantity=executed,unused_quantity=unused,cumulative_gross_notional=gross
   WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;
  IF NOT unresolved AND allocated=parent.original_quantity THEN
   UPDATE public.paper_native_parents_v2 SET status=CASE WHEN executed=parent.original_quantity THEN 'COMPLETED' ELSE 'STOPPED_UNFILLED' END,
    revision=revision+CASE WHEN status=CASE WHEN executed=parent.original_quantity THEN 'COMPLETED' ELSE 'STOPPED_UNFILLED' END THEN 0 ELSE 1 END
    WHERE scope_key=parent.scope_key AND parent_client_id=parent.parent_client_id;
  END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.paper_advance_native_algorithms_v2(text),public.paper_refresh_native_algorithms_v2(text) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_advance_native_algorithms_v2(text) OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_refresh_native_algorithms_v2(text) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;

-- The upgraded allocator follows below. It replaces the one live venue kernel;
-- V1 source types and history remain intact and undergo the same exact bounds.

CREATE OR REPLACE FUNCTION public.paper_match_order(p_scope text,p_client text) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE market_id text; book record; ord record; level record; need numeric; available_total numeric;
 limit_price numeric; take_quantity numeric; accumulated numeric; fill_identifier text; fill_payload jsonb;
 resulting_status text; reason text; fee_amount numeric; fee_vector jsonb; original_side text; opposite_side text;
 activity_at timestamptz;execution_at timestamptz;envelope_min numeric;envelope_max numeric;envelope_gross numeric;
 spent_gross numeric;spent_fee numeric;fee_ceiling numeric;best_price numeric;gross_candidate numeric;fee_candidate numeric;
 preflight_need numeric;preflight_gross numeric;preflight_ok boolean;
 is_market boolean;model_revision integer;prepared record;liquidity_owner record;stp_mode text;depth_identifier text;
 maker_prevented numeric;taker_prevented numeric;prevention_identifier text;prevention_payload jsonb;
BEGIN
 SELECT market_key,(terms->'accepted_clock'->>'utc_now')::timestamptz INTO market_id,activity_at FROM public.paper_order_terms WHERE scope_key=p_scope AND client_id=p_client;
 IF market_id IS NULL THEN RAISE EXCEPTION 'original accepted PAPER terms required'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(market_id,0));
 SELECT * INTO book FROM public.paper_market_books WHERE market_key=market_id FOR UPDATE;
 FOR prepared IN SELECT scope_key,client_id FROM public.paper_order_terms WHERE market_key=market_id
  AND coalesce((terms->>'model_revision')::integer,1)=2 ORDER BY arrival_sequence LOOP
  PERFORM public.paper_prepare_order_v2(prepared.scope_key,prepared.client_id,market_id);
 END LOOP;
 -- FIFO spans accounts and orders sharing the exact product/instrument source.
 FOR ord IN SELECT o.*,t.terms,t.arrival_sequence,r.effective_limit AS model_limit,r.visible_remaining AS model_visible,
  r.prevented_quantity AS model_prevented FROM public.paper_order_terms t
  JOIN public.paper_engine_orders o ON o.scope_key=t.scope_key AND o.client_id=t.client_id
  LEFT JOIN public.paper_order_runtime_v2 r ON r.scope_key=o.scope_key AND r.client_id=o.client_id
  WHERE t.market_key=market_id AND o.lifecycle NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED')
  ORDER BY coalesce(r.queue_priority,t.arrival_sequence),t.arrival_sequence FOR UPDATE OF o LOOP
  IF ord.terms->'scope' IS DISTINCT FROM ord.scope_key::jsonb
   OR ord.terms->>'client_id' IS DISTINCT FROM ord.client_id
   OR ord.terms->>'quantity' IS NULL OR (ord.terms->>'quantity')::numeric<>ord.quantity
   OR (coalesce((ord.terms->>'model_revision')::integer,1)=1 AND ord.terms->>'order_type' NOT IN ('LIMIT','LIMIT_MAKER'))
   OR (coalesce((ord.terms->>'model_revision')::integer,1)=2 AND
    (ord.terms->>'contract_version' IS DISTINCT FROM '2026-10-30' OR ord.terms->'spec' IS DISTINCT FROM
     (SELECT n->'original'->'order' FROM jsonb_array_elements(ord.terms->'compilation'->'nodes') n
      WHERE n->'original'->>'node_id'=ord.terms->>'node_id')))
   OR ord.terms->>'side' NOT IN ('BUY','SELL')
   OR ord.terms->>'tif' NOT IN ('GTC','IOC','FOK','GTX','GTD','DAY')
   OR ord.terms->'accepted_clock'->>'clock_health' IS DISTINCT FROM 'HEALTHY'
   OR (ord.terms->'accepted_clock'->>'utc_now')::timestamptz<(ord.terms->'write'->>'created_at')::timestamptz THEN
    RAISE EXCEPTION 'invalid original PAPER execution terms';
  END IF;
  IF ord.pending<>'NONE' OR ord.certainty<>'CONFIRMED' THEN CONTINUE; END IF;
  model_revision=coalesce((ord.terms->>'model_revision')::integer,1);
  is_market=model_revision=2 AND ord.terms->'spec'->'price'->>'kind'='MARKET';
  IF model_revision=2 AND NOT public.paper_prepare_order_v2(ord.scope_key,ord.client_id,market_id) THEN CONTINUE; END IF;
  IF is_market AND NOT EXISTS(SELECT FROM public.paper_market_state_v2 WHERE market_key=market_id AND depth_event_id IS NOT NULL) THEN
   IF ord.terms->'spec'->'trigger'->>'kind'='NONE' THEN RAISE EXCEPTION 'current market depth required on arrival'; END IF;
   UPDATE public.paper_order_runtime_v2 SET phase='WAITING_DEPTH' WHERE scope_key=ord.scope_key AND client_id=ord.client_id;CONTINUE;
  END IF;
  IF book IS NULL THEN
   IF ord.terms->>'tif' IN ('IOC','FOK','GTX') OR (ord.terms->>'post_only')::boolean
    THEN RAISE EXCEPTION 'current market depth required on arrival'; END IF;
   CONTINUE;
  END IF;
  IF ord.terms->'scope'->>'product' IS DISTINCT FROM book.product
   OR ord.terms->'scope'->>'instrument_uid' IS DISTINCT FROM book.instrument_uid THEN RAISE EXCEPTION 'market scope mismatch'; END IF;
  original_side=ord.terms->>'side';opposite_side=CASE WHEN original_side='BUY' THEN 'ASK' ELSE 'BID' END;
  limit_price=CASE WHEN model_revision=2 THEN ord.model_limit ELSE (ord.terms->>'limit_price')::numeric END;
  need=ord.quantity-ord.filled-coalesce(ord.model_prevented,0);reason=NULL;
  execution_at=greatest(activity_at,(ord.terms->'accepted_clock'->>'utc_now')::timestamptz,
   (book.observed_clock->>'utc_now')::timestamptz,book.occurred_at);
  envelope_min=(ord.terms->'execution_envelope'->>'minimum_price')::numeric;
  envelope_max=(ord.terms->'execution_envelope'->>'maximum_price')::numeric;
  envelope_gross=(ord.terms->'execution_envelope'->>'maximum_gross_notional')::numeric;
  IF envelope_gross IS NULL OR envelope_gross<=0 THEN RAISE EXCEPTION 'actual protected execution envelope required'; END IF;
  SELECT coalesce(sum((payload->>'gross_notional')::numeric),0) INTO spent_gross FROM public.paper_engine_events
   WHERE scope_key=ord.scope_key AND client_id=ord.client_id AND payload->>'record_kind'='PAPER_MARKET_FILL';
  spent_fee=spent_gross*(ord.terms->>'quote_fee_rate')::numeric;
  SELECT coalesce(sum((f->>'amount')::numeric),0) INTO fee_ceiling
   FROM jsonb_array_elements(ord.terms->'execution_envelope'->'fee_ceilings') f WHERE f->>'asset'=ord.terms->>'quote_asset';
  SELECT price INTO best_price FROM public.paper_market_levels WHERE market_key=market_id AND side=opposite_side AND available>0
   AND (is_market OR CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END)
   ORDER BY CASE WHEN original_side='BUY' THEN price ELSE -price END LIMIT 1;
  SELECT coalesce(sum(trunc(available/(ord.terms->>'quantity_step')::numeric)*(ord.terms->>'quantity_step')::numeric),0) INTO available_total FROM public.paper_market_levels
   WHERE market_key=market_id AND side=opposite_side AND available>0
   AND (is_market OR CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END)
   AND (envelope_min IS NULL OR price>=envelope_min) AND (envelope_max IS NULL OR price<=envelope_max);
  preflight_ok=true;
  IF ord.terms->>'tif'='FOK' THEN
   preflight_need=need;preflight_gross=spent_gross;
   FOR level IN SELECT * FROM public.paper_market_levels WHERE market_key=market_id AND side=opposite_side AND available>0
    AND (is_market OR CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END)
    AND (envelope_min IS NULL OR price>=envelope_min) AND (envelope_max IS NULL OR price<=envelope_max)
    ORDER BY CASE WHEN original_side='BUY' THEN price ELSE -price END FOR UPDATE LOOP
    EXIT WHEN preflight_need<=0;
    take_quantity=trunc(least(preflight_need,level.available)/(ord.terms->>'quantity_step')::numeric)*(ord.terms->>'quantity_step')::numeric;
    preflight_gross=preflight_gross+take_quantity*level.price*(ord.terms->>'multiplier')::numeric;
    preflight_need=preflight_need-take_quantity;
   END LOOP;
   preflight_ok=preflight_need=0 AND preflight_gross<=envelope_gross
    AND preflight_gross*(ord.terms->>'quote_fee_rate')::numeric<=fee_ceiling;
  END IF;
  IF ord.revision=1 AND ((ord.terms->>'post_only')::boolean OR ord.terms->>'tif'='GTX') AND available_total>0 THEN
   resulting_status='REJECTED';reason='POST_ONLY_WOULD_TAKE';
  ELSIF best_price IS NOT NULL AND ((envelope_min IS NOT NULL AND best_price<envelope_min) OR (envelope_max IS NOT NULL AND best_price>envelope_max)) THEN
   resulting_status='EXPIRED';reason='EXECUTION_PRICE_ENVELOPE_VIOLATION';
  ELSIF ord.terms->>'tif'='FOK' AND (available_total<need OR NOT preflight_ok) THEN
   -- Preflight before touching any level, fill or consumption record.
   resulting_status='EXPIRED';reason='FOK_INSUFFICIENT_VISIBLE_DEPTH';
  ELSE
   accumulated=ord.filled;
   stp_mode=coalesce(ord.terms->'spec'->>'stp','NONE');
   SELECT depth_event_id INTO depth_identifier FROM public.paper_market_state_v2 WHERE market_key=market_id;
   FOR level IN SELECT * FROM public.paper_market_levels WHERE market_key=market_id AND side=opposite_side AND available>0
    AND (is_market OR CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END)
    ORDER BY CASE WHEN original_side='BUY' THEN price ELSE -price END FOR UPDATE LOOP
    EXIT WHEN need<=0;
    take_quantity=least(need,level.available,coalesce(ord.model_visible,need));
    take_quantity=trunc(take_quantity/(ord.terms->>'quantity_step')::numeric)*(ord.terms->>'quantity_step')::numeric;
    CONTINUE WHEN take_quantity<=0;
    IF stp_mode<>'NONE' THEN
     SELECT * INTO liquidity_owner FROM public.paper_liquidity_owners_v2 WHERE market_key=market_id AND depth_event_id=depth_identifier
      AND side=opposite_side AND price=level.price;
     IF liquidity_owner IS NULL THEN
      UPDATE public.paper_order_runtime_v2 SET phase='WAITING_COUNTERPARTY_DATA' WHERE scope_key=ord.scope_key AND client_id=ord.client_id;EXIT;
     END IF;
     IF liquidity_owner.venue_account_id=ord.terms->'scope'->>'venue_account_id' THEN
      maker_prevented=CASE WHEN stp_mode IN ('EXPIRE_MAKER','EXPIRE_BOTH') THEN level.available WHEN stp_mode='DECREMENT' THEN take_quantity ELSE 0 END;
      taker_prevented=CASE WHEN stp_mode IN ('EXPIRE_TAKER','EXPIRE_BOTH') THEN need WHEN stp_mode='DECREMENT' THEN take_quantity ELSE 0 END;
      prevention_identifier='paper-stp:'||encode(sha256(convert_to(jsonb_build_array(market_id,book.event_id,ord.scope_key::jsonb,
       ord.client_id,depth_identifier,opposite_side,trim_scale(level.price),stp_mode)::text,'UTF8')),'hex');
      INSERT INTO public.paper_market_preventions_v2 VALUES(market_id,book.event_id,ord.scope_key,ord.client_id,prevention_identifier,
       depth_identifier,opposite_side,level.price,maker_prevented,taker_prevented,stp_mode,liquidity_owner.venue_account_id,liquidity_owner.venue_order_id);
      prevention_payload=jsonb_build_object('record_kind','PAPER_MARKET_DISPOSITION','source','PAPER_MARKET_REPLAY_SYNTHETIC',
       'reason','STP_'||stp_mode,'status',CASE WHEN taker_prevented>0 AND (stp_mode<>'DECREMENT' OR taker_prevented>=need) THEN 'EXPIRED' ELSE 'SUBMITTED' END,
       'market_key',market_id,'market_event_id',book.event_id,'depth_event_id',depth_identifier,
       'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=market_id AND event_id=book.event_id),
       'maker_quantity',maker_prevented::text,'taker_quantity',taker_prevented::text,'liquidity_owner',to_jsonb(liquidity_owner),
       'model',ord.terms->'model','execution_envelope',ord.terms->'execution_envelope','market_occurred_at',book.occurred_at,'execution_at',execution_at);
      INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
       VALUES('PAPER',ord.scope_key,prevention_identifier,ord.client_id,encode(sha256(convert_to(prevention_payload::text,'UTF8')),'hex'),
        prevention_payload,0,NULL,execution_at);
      UPDATE public.paper_market_levels SET available=available-maker_prevented WHERE market_key=market_id AND side=opposite_side AND price=level.price;
      UPDATE public.paper_order_runtime_v2 SET prevented_quantity=prevented_quantity+taker_prevented WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
      IF stp_mode='DECREMENT' THEN
       need=need-taker_prevented;
       UPDATE public.paper_order_runtime_v2 SET phase='STP_DECREMENTED' WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
       IF need=0 THEN reason='STP_DECREMENT';EXIT; END IF;
      ELSIF taker_prevented>0 THEN reason='STP_'||stp_mode;EXIT;
      END IF;
      CONTINUE;
     END IF;
    END IF;
    gross_candidate=spent_gross+take_quantity*level.price*(ord.terms->>'multiplier')::numeric;
    fee_candidate=gross_candidate*(ord.terms->>'quote_fee_rate')::numeric;
    IF (envelope_min IS NOT NULL AND level.price<envelope_min) OR (envelope_max IS NOT NULL AND level.price>envelope_max)
     OR gross_candidate>envelope_gross OR fee_candidate>fee_ceiling THEN
     reason='EXECUTION_ENVELOPE_REQUIRES_RECONCILIATION';EXIT;
    END IF;
    IF model_revision=2 AND NOT public.paper_claim_exclusive_v2(ord.scope_key,ord.client_id,market_id) THEN EXIT; END IF;
    accumulated=accumulated+take_quantity;need=need-take_quantity;spent_gross=gross_candidate;spent_fee=fee_candidate;
    fill_identifier='paper-market-fill:'||encode(sha256(convert_to(jsonb_build_array(market_id,book.event_id,
     ord.scope_key::jsonb,ord.client_id,trim_scale(level.price),trim_scale(accumulated))::text,'UTF8')),'hex');
    fee_amount=take_quantity*level.price*(ord.terms->>'multiplier')::numeric*(ord.terms->>'quote_fee_rate')::numeric;
    fee_vector=CASE WHEN fee_amount=0 THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
     'asset',ord.terms->>'quote_asset','amount',fee_amount::text,'receipt_effect','NO_RECEIPT_DEBIT','position_effect','NO_POSITION_DEBIT')) END;
    fill_payload=jsonb_build_object('record_kind','PAPER_MARKET_FILL','source','PAPER_MARKET_REPLAY_SYNTHETIC',
     'event_id',fill_identifier,'market_event_id',book.event_id,'market_key',market_id,
     'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=market_id AND event_id=book.event_id),
     'node_id',ord.terms->>'node_id','namespace',ord.terms->>'namespace','client_id',ord.client_id,
     'quantity',take_quantity::text,'price',level.price::text,'gross_notional',(take_quantity*level.price*(ord.terms->>'multiplier')::numeric)::text,
     'fees',fee_vector,'model',ord.terms->'model','market_occurred_at',book.occurred_at,'execution_at',execution_at,
     'execution_envelope',ord.terms->'execution_envelope','calendar_event_id',
      (SELECT calendar_event_id FROM public.paper_order_runtime_v2 WHERE scope_key=ord.scope_key AND client_id=ord.client_id));
    INSERT INTO public.paper_market_consumptions VALUES(market_id,book.event_id,ord.scope_key,ord.client_id,
     fill_identifier,opposite_side,level.price,take_quantity);
    INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
     VALUES('PAPER',ord.scope_key,fill_identifier,ord.client_id,encode(sha256(convert_to(fill_payload::text,'UTF8')),'hex'),fill_payload,take_quantity,level.price,execution_at);
    UPDATE public.paper_market_levels SET available=available-take_quantity
     WHERE market_key=market_id AND side=opposite_side AND price=level.price;
    IF model_revision=2 AND ord.model_visible IS NOT NULL THEN
     ord.model_visible=ord.model_visible-take_quantity;
     UPDATE public.paper_order_runtime_v2 SET visible_remaining=ord.model_visible WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
     EXIT WHEN ord.model_visible<=0;
    END IF;
   END LOOP;
   resulting_status=CASE WHEN reason IS NOT NULL THEN 'EXPIRED' WHEN need=0 THEN 'FILLED' WHEN ord.terms->>'tif' IN ('IOC','FOK') THEN 'EXPIRED'
    WHEN accumulated>0 THEN 'PARTIALLY_FILLED' ELSE 'SUBMITTED' END;
   UPDATE public.paper_engine_orders SET filled=accumulated WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
   IF resulting_status='EXPIRED' AND reason IS NULL THEN reason='IMMEDIATE_RESIDUAL_EXPIRED'; END IF;
  END IF;
  UPDATE public.paper_engine_orders SET lifecycle=resulting_status,revision=revision+1 WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
  IF model_revision=2 AND resulting_status IN ('FILLED','CANCELED','EXPIRED','REJECTED') THEN
   UPDATE public.paper_order_runtime_v2 SET phase=resulting_status,active=false,visible_remaining=0
    WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
  END IF;
  IF reason IS NOT NULL AND NOT starts_with(reason,'STP_') THEN
   fill_payload=jsonb_build_object('record_kind','PAPER_MARKET_DISPOSITION','source','PAPER_MARKET_REPLAY_SYNTHETIC',
    'reason',reason,'status',resulting_status,'market_event_id',book.event_id,'market_key',market_id,
    'market_hash',(SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=market_id AND event_id=book.event_id),
    'model',ord.terms->'model',
    'execution_envelope',ord.terms->'execution_envelope','observed_best_price',best_price,'market_occurred_at',book.occurred_at,'execution_at',execution_at);
   INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
    VALUES('PAPER',ord.scope_key,'paper-disposition:'||encode(sha256(convert_to(jsonb_build_array(ord.scope_key::jsonb,ord.client_id,book.event_id,reason)::text,'UTF8')),'hex'),ord.client_id,
     encode(sha256(convert_to(fill_payload::text,'UTF8')),'hex'),fill_payload,0,NULL,execution_at);
  END IF;
 END LOOP;
 UPDATE public.paper_native_parents_v2 p SET status='ALL_DONE',revision=revision+1 WHERE p.status<>'ALL_DONE'
  AND p.template IN ('OCO','OTO','OTOCO')
  AND EXISTS(SELECT FROM public.paper_native_members_v2 m JOIN public.paper_order_terms t USING(scope_key,client_id)
   WHERE m.scope_key=p.scope_key AND m.parent_client_id=p.parent_client_id AND t.market_key=market_id)
  AND NOT EXISTS(SELECT FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id)
   WHERE m.scope_key=p.scope_key AND m.parent_client_id=p.parent_client_id AND o.lifecycle NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED'));
 PERFORM public.paper_refresh_native_algorithms_v2(market_id);
END $$;

REVOKE ALL ON FUNCTION public.paper_match_order(text,text) FROM PUBLIC;
COMMIT;
