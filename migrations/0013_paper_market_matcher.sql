-- Isolated synthetic venue only. No business-ledger objects or money totals.
BEGIN;
DO $$ BEGIN
 IF NOT starts_with(current_database(),'paper_engine_') THEN RAISE EXCEPTION 'isolated PAPER database required'; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='paper_matcher_definer') THEN
  CREATE ROLE paper_matcher_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='paper_matcher_definer' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
  OR EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='paper_matcher_definer')
   OR roleid=(SELECT oid FROM pg_roles WHERE rolname='paper_matcher_definer')) THEN RAISE EXCEPTION 'unsafe matcher role'; END IF;
END $$;
CREATE TABLE public.paper_market_inputs(
 market_key text NOT NULL,event_id text NOT NULL,sequence bigint NOT NULL CHECK(sequence>0),
 payload_hash text NOT NULL CHECK(length(payload_hash)=64),payload jsonb NOT NULL,before_levels jsonb NOT NULL,observed_clock jsonb NOT NULL,
 PRIMARY KEY(market_key,event_id),UNIQUE(market_key,sequence));
CREATE TABLE public.paper_market_books(
 market_key text PRIMARY KEY,product text NOT NULL,instrument_uid text NOT NULL,stream_id text NOT NULL,
 sequence bigint NOT NULL CHECK(sequence>0),event_id text NOT NULL,occurred_at timestamptz NOT NULL,observed_clock jsonb NOT NULL,
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id));
CREATE TABLE public.paper_market_levels(
 market_key text NOT NULL REFERENCES public.paper_market_books(market_key),side text NOT NULL CHECK(side IN ('BID','ASK')),
 price numeric NOT NULL CHECK(price>0 AND price<>'NaN'),reported numeric NOT NULL CHECK(reported>=0 AND reported<>'NaN'),
 available numeric NOT NULL CHECK(available>=0 AND available<>'NaN'),PRIMARY KEY(market_key,side,price));
CREATE TABLE public.paper_order_terms(
 scope_key text NOT NULL,client_id text NOT NULL,market_key text NOT NULL,
 arrival_sequence bigint GENERATED ALWAYS AS IDENTITY,terms jsonb NOT NULL,
 PRIMARY KEY(scope_key,client_id),UNIQUE(arrival_sequence),
 FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
CREATE TABLE public.paper_market_consumptions(
 market_key text NOT NULL,event_id text NOT NULL,scope_key text NOT NULL,client_id text NOT NULL,
 fill_id text PRIMARY KEY,side text NOT NULL CHECK(side IN ('BID','ASK')),price numeric NOT NULL CHECK(price>0 AND price<>'NaN'),
 quantity numeric NOT NULL CHECK(quantity>0 AND quantity<>'NaN'),
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs(market_key,event_id),
 FOREIGN KEY(scope_key,client_id) REFERENCES public.paper_engine_orders(scope_key,client_id));
CREATE FUNCTION public.paper_market_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable PAPER market source'; END $$;
REVOKE ALL ON FUNCTION public.paper_market_immutable() FROM PUBLIC;
CREATE TRIGGER immutable_market_input BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_market_inputs
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_market_consumption BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_market_consumptions
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_market_terms BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_order_terms
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
REVOKE ALL ON public.paper_market_inputs,public.paper_market_books,public.paper_market_levels,
 public.paper_order_terms,public.paper_market_consumptions FROM PUBLIC;

CREATE FUNCTION public.paper_match_order(p_scope text,p_client text) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE market_id text; book record; ord record; level record; need numeric; available_total numeric;
 limit_price numeric; take_quantity numeric; accumulated numeric; fill_identifier text; fill_payload jsonb;
 resulting_status text; reason text; fee_amount numeric; fee_vector jsonb; original_side text; opposite_side text;
 activity_at timestamptz;execution_at timestamptz;envelope_min numeric;envelope_max numeric;envelope_gross numeric;
 spent_gross numeric;spent_fee numeric;fee_ceiling numeric;best_price numeric;gross_candidate numeric;fee_candidate numeric;
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
   OR ord.terms->>'order_type' NOT IN ('LIMIT','LIMIT_MAKER')
   OR ord.terms->>'side' NOT IN ('BUY','SELL')
   OR ord.terms->>'tif' NOT IN ('GTC','IOC','FOK','GTX')
   OR ord.terms->'accepted_clock'->>'clock_health' IS DISTINCT FROM 'HEALTHY'
   OR (ord.terms->'accepted_clock'->>'utc_now')::timestamptz<(ord.terms->'write'->>'created_at')::timestamptz THEN
    RAISE EXCEPTION 'invalid original PAPER execution terms';
  END IF;
  IF ord.pending<>'NONE' OR ord.certainty<>'CONFIRMED' THEN CONTINUE; END IF;
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
   AND CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END
   ORDER BY CASE WHEN original_side='BUY' THEN price ELSE -price END LIMIT 1;
  SELECT coalesce(sum(trunc(available/(ord.terms->>'quantity_step')::numeric)*(ord.terms->>'quantity_step')::numeric),0) INTO available_total FROM public.paper_market_levels
   WHERE market_key=market_id AND side=opposite_side AND available>0
   AND CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END
   AND (envelope_min IS NULL OR price>=envelope_min) AND (envelope_max IS NULL OR price<=envelope_max);
  IF ord.revision=1 AND ((ord.terms->>'post_only')::boolean OR ord.terms->>'tif'='GTX') AND available_total>0 THEN
   resulting_status='REJECTED';reason='POST_ONLY_WOULD_TAKE';
  ELSIF best_price IS NOT NULL AND ((envelope_min IS NOT NULL AND best_price<envelope_min) OR (envelope_max IS NOT NULL AND best_price>envelope_max)) THEN
   resulting_status='EXPIRED';reason='EXECUTION_PRICE_ENVELOPE_VIOLATION';
  ELSIF ord.terms->>'tif'='FOK' AND available_total<need THEN
   -- Preflight before touching any level, fill or consumption record.
   resulting_status='EXPIRED';reason='FOK_INSUFFICIENT_VISIBLE_DEPTH';
  ELSE
   accumulated=ord.filled;
   FOR level IN SELECT * FROM public.paper_market_levels WHERE market_key=market_id AND side=opposite_side AND available>0
    AND CASE WHEN original_side='BUY' THEN price<=limit_price ELSE price>=limit_price END
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
    'reason',reason,'status',resulting_status,'market_event_id',book.event_id,'model',ord.terms->'model',
    'execution_envelope',ord.terms->'execution_envelope','observed_best_price',best_price,'market_occurred_at',book.occurred_at,'execution_at',execution_at);
   INSERT INTO public.paper_engine_events(environment,scope_key,event_id,client_id,payload_hash,payload,fill_delta,price,occurred_at)
    VALUES('PAPER',ord.scope_key,'paper-disposition:'||encode(sha256(convert_to(jsonb_build_array(ord.scope_key::jsonb,ord.client_id,book.event_id,reason)::text,'UTF8')),'hex'),ord.client_id,
     encode(sha256(convert_to(fill_payload::text,'UTF8')),'hex'),fill_payload,0,NULL,execution_at);
  END IF;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.paper_match_order(text,text) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_match_order(text,text) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;
GRANT SELECT,UPDATE ON public.paper_engine_orders,public.paper_market_levels TO paper_matcher_definer;
GRANT SELECT ON public.paper_order_terms,public.paper_market_books,public.paper_market_inputs TO paper_matcher_definer;
-- Row-lock privilege only; the NOLOGIN function owner has no exposed book writer.
GRANT UPDATE(sequence) ON public.paper_market_books TO paper_matcher_definer;
GRANT INSERT ON public.paper_engine_events,public.paper_market_consumptions TO paper_matcher_definer;
GRANT SELECT ON public.paper_engine_events TO paper_matcher_definer;
COMMIT;
