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
CREATE TABLE public.paper_order_runtime_v2(
 scope_key text NOT NULL,client_id text NOT NULL,active boolean NOT NULL,phase text NOT NULL,
 activation_event_id text,high_water numeric,low_water numeric,trail_started boolean NOT NULL DEFAULT false,
 effective_type text NOT NULL,effective_limit numeric,
 PRIMARY KEY(scope_key,client_id),FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
REVOKE ALL ON public.paper_market_state_v2,public.paper_reference_prices_v2,public.paper_published_rules_v2,
 public.paper_order_runtime_v2 FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON public.paper_market_state_v2,public.paper_reference_prices_v2,
 public.paper_published_rules_v2,public.paper_order_runtime_v2 TO paper_matcher_definer;

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
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER record_market_input_v2 AFTER INSERT ON public.paper_market_inputs
 FOR EACH ROW EXECUTE FUNCTION public.paper_record_market_input_v2();
INSERT INTO public.paper_market_state_v2(market_key,depth_event_id)
 SELECT DISTINCT ON(market_key) market_key,event_id FROM public.paper_market_inputs
 WHERE payload->>'kind' IN ('DEPTH_SNAPSHOT','DEPTH_DELTA') ORDER BY market_key,sequence DESC;

CREATE FUNCTION public.paper_prepare_order_v2(p_scope text,p_client text,p_market text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE terms jsonb;spec jsonb;trigger_spec jsonb;runtime public.paper_order_runtime_v2;
 ref record;price_source text;fired boolean;ready boolean;activation numeric;offset_value numeric;offset_unit text;
 mark_price numeric;contract_price numeric;protect_ratio numeric;
BEGIN
 SELECT t.terms INTO terms FROM public.paper_order_terms t WHERE t.scope_key=p_scope AND t.client_id=p_client;
 IF coalesce((terms->>'model_revision')::int,1)<>2 THEN RETURN true; END IF;
 spec=terms->'spec';trigger_spec=spec->'trigger';
 INSERT INTO public.paper_order_runtime_v2(scope_key,client_id,active,phase,effective_type,effective_limit)
  VALUES(p_scope,p_client,trigger_spec->>'kind'='NONE',CASE WHEN trigger_spec->>'kind'='NONE' THEN 'ACTIVE' ELSE 'WAITING_REFERENCE' END,
   CASE WHEN spec->'price'->>'kind'='MARKET' THEN 'MARKET' ELSE 'LIMIT' END,(spec->'price'->>'value')::numeric)
  ON CONFLICT(scope_key,client_id) DO NOTHING;
 SELECT * INTO runtime FROM public.paper_order_runtime_v2 r WHERE r.scope_key=p_scope AND r.client_id=p_client FOR UPDATE;
 IF runtime.active THEN RETURN true; END IF;
 price_source=trigger_spec->>'source';
 IF price_source='BID_ASK' THEN price_source=CASE WHEN spec->>'side'='BUY' THEN 'ASK' ELSE 'BID' END; END IF;
 SELECT price,event_id INTO ref FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source=price_source;
 IF ref IS NULL THEN RETURN false; END IF;
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
  SELECT trigger_protect_ratio INTO protect_ratio FROM public.paper_published_rules_v2 WHERE market_key=p_market;
  SELECT price INTO mark_price FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source='MARK_PRICE';
  SELECT price INTO contract_price FROM public.paper_reference_prices_v2 WHERE market_key=p_market AND source='CONTRACT_PRICE';
  IF protect_ratio IS NULL OR mark_price IS NULL OR contract_price IS NULL OR abs(mark_price-contract_price)/contract_price>protect_ratio THEN
   UPDATE public.paper_order_runtime_v2 SET phase='WAITING_PRICE_PROTECT_DATA' WHERE scope_key=p_scope AND client_id=p_client;
   RETURN false;
  END IF;
 END IF;
 UPDATE public.paper_order_runtime_v2 SET active=true,phase='ACTIVE',activation_event_id=ref.event_id
  WHERE scope_key=p_scope AND client_id=p_client;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.paper_record_market_input_v2(),public.paper_prepare_order_v2(text,text,text) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_record_market_input_v2() OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_prepare_order_v2(text,text,text) OWNER TO paper_matcher_definer;
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
 is_market boolean;model_revision integer;
BEGIN
 SELECT market_key,(terms->'accepted_clock'->>'utc_now')::timestamptz INTO market_id,activity_at FROM public.paper_order_terms WHERE scope_key=p_scope AND client_id=p_client;
 IF market_id IS NULL THEN RAISE EXCEPTION 'original accepted PAPER terms required'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(market_id,0));
 SELECT * INTO book FROM public.paper_market_books WHERE market_key=market_id FOR UPDATE;
 -- FIFO spans accounts and orders sharing the exact product/instrument source.
 FOR ord IN SELECT o.*,t.terms,t.arrival_sequence FROM public.paper_order_terms t
  JOIN public.paper_engine_orders o ON o.scope_key=t.scope_key AND o.client_id=t.client_id
  WHERE t.market_key=market_id AND o.lifecycle NOT IN ('FILLED','CANCELED','EXPIRED','REJECTED')
  ORDER BY t.arrival_sequence FOR UPDATE OF o LOOP
  IF ord.terms->'scope' IS DISTINCT FROM ord.scope_key::jsonb
   OR ord.terms->>'client_id' IS DISTINCT FROM ord.client_id
   OR ord.terms->>'quantity' IS NULL OR (ord.terms->>'quantity')::numeric<>ord.quantity
   OR (coalesce((ord.terms->>'model_revision')::integer,1)=1 AND ord.terms->>'order_type' NOT IN ('LIMIT','LIMIT_MAKER'))
   OR (coalesce((ord.terms->>'model_revision')::integer,1)=2 AND
    (ord.terms->>'contract_version' IS DISTINCT FROM '2026-10-30' OR ord.terms->'spec' IS DISTINCT FROM ord.terms->'compilation'->'nodes'->0->'original'->'order'))
   OR ord.terms->>'side' NOT IN ('BUY','SELL')
   OR ord.terms->>'tif' NOT IN ('GTC','IOC','FOK','GTX')
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
  limit_price=(ord.terms->>'limit_price')::numeric;need=ord.quantity-ord.filled;reason=NULL;
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
   FOR level IN SELECT * FROM public.paper_market_levels WHERE market_key=market_id AND side=opposite_side AND available>0
    AND (is_market OR CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END)
    ORDER BY CASE WHEN original_side='BUY' THEN price ELSE -price END FOR UPDATE LOOP
    EXIT WHEN need<=0;
    take_quantity=least(need,level.available);
    take_quantity=trunc(take_quantity/(ord.terms->>'quantity_step')::numeric)*(ord.terms->>'quantity_step')::numeric;
    CONTINUE WHEN take_quantity<=0;
    gross_candidate=spent_gross+take_quantity*level.price*(ord.terms->>'multiplier')::numeric;
    fee_candidate=gross_candidate*(ord.terms->>'quote_fee_rate')::numeric;
    IF (envelope_min IS NOT NULL AND level.price<envelope_min) OR (envelope_max IS NOT NULL AND level.price>envelope_max)
     OR gross_candidate>envelope_gross OR fee_candidate>fee_ceiling THEN
     reason='EXECUTION_ENVELOPE_REQUIRES_RECONCILIATION';EXIT;
    END IF;
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
     'execution_envelope',ord.terms->'execution_envelope');
    INSERT INTO public.paper_market_consumptions VALUES(market_id,book.event_id,ord.scope_key,ord.client_id,
     fill_identifier,opposite_side,level.price,take_quantity);
    INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
     VALUES('PAPER',ord.scope_key,fill_identifier,ord.client_id,encode(sha256(convert_to(fill_payload::text,'UTF8')),'hex'),fill_payload,take_quantity,level.price,execution_at);
    UPDATE public.paper_market_levels SET available=available-take_quantity
     WHERE market_key=market_id AND side=opposite_side AND price=level.price;
   END LOOP;
   resulting_status=CASE WHEN reason IS NOT NULL THEN 'EXPIRED' WHEN need=0 THEN 'FILLED' WHEN ord.terms->>'tif' IN ('IOC','FOK') THEN 'EXPIRED'
    WHEN accumulated>0 THEN 'PARTIALLY_FILLED' ELSE 'SUBMITTED' END;
   UPDATE public.paper_engine_orders SET filled=accumulated WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
   IF resulting_status='EXPIRED' AND reason IS NULL THEN reason='IMMEDIATE_RESIDUAL_EXPIRED'; END IF;
  END IF;
  UPDATE public.paper_engine_orders SET lifecycle=resulting_status,revision=revision+1 WHERE scope_key=ord.scope_key AND client_id=ord.client_id;
  IF reason IS NOT NULL THEN
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
END $$;

REVOKE ALL ON FUNCTION public.paper_match_order(text,text) FROM PUBLIC;
COMMIT;
