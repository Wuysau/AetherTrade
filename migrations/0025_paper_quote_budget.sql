-- Additive immediate quote attempts. BASE tables and original quantity semantics stay frozen.
CREATE TABLE public.paper_quote_terms_v1 (
 scope_key text NOT NULL,command_id text NOT NULL,client_id text NOT NULL,market_key text NOT NULL,
 terms jsonb NOT NULL, PRIMARY KEY(scope_key,command_id), UNIQUE(scope_key,client_id));
CREATE TABLE public.paper_quote_runtime_v1 (
 scope_key text NOT NULL,command_id text NOT NULL,status text NOT NULL,
 actual_base numeric NOT NULL CHECK(actual_base>=0),gross_quote numeric NOT NULL CHECK(gross_quote>=0),
 remaining_quote numeric NOT NULL CHECK(remaining_quote>=0),reason text NOT NULL,
 PRIMARY KEY(scope_key,command_id), FOREIGN KEY(scope_key,command_id) REFERENCES public.paper_quote_terms_v1);
CREATE TABLE public.paper_quote_allocations_v1 (
 scope_key text NOT NULL,command_id text NOT NULL,fill_id text PRIMARY KEY,
 market_key text NOT NULL,event_id text NOT NULL,side text NOT NULL CHECK(side IN ('ASK','BID')),
 price numeric NOT NULL CHECK(price>0),quantity numeric NOT NULL CHECK(quantity>0),fee numeric NOT NULL CHECK(fee>=0),
 FOREIGN KEY(scope_key,command_id) REFERENCES public.paper_quote_terms_v1,
 FOREIGN KEY(market_key,event_id) REFERENCES public.paper_market_inputs);
CREATE TRIGGER immutable_quote_terms BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_quote_terms_v1
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_quote_runtime BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_quote_runtime_v1
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
CREATE TRIGGER immutable_quote_allocations BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_quote_allocations_v1
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
REVOKE ALL ON public.paper_quote_terms_v1,public.paper_quote_runtime_v1,public.paper_quote_allocations_v1 FROM PUBLIC;
CREATE FUNCTION public.paper_accept_quote_v1(p_scope text,p_command text,p_client text,p_market text,p_terms jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE level record; book record; budget numeric; remaining numeric; step numeric; qty numeric;
 base numeric=0; gross numeric=0; opposite text; seq integer=0; rate numeric;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(p_market,0));
 budget=(p_terms->>'requested_quote')::numeric;remaining=budget;
 step=(p_terms->>'quantity_step')::numeric;rate=(p_terms->>'quote_fee_rate')::numeric;
 IF p_terms->>'record_kind' IS DISTINCT FROM 'PAPER_QUOTE_TERMS_V1' OR budget IS NULL OR budget<=0
 OR budget='NaN' OR step IS NULL OR step<=0 OR step='NaN' OR rate IS NULL OR rate<0 OR rate>1
 OR p_terms->>'side' NOT IN ('BUY','SELL') THEN RAISE EXCEPTION 'invalid quote terms'; END IF;
 SELECT * INTO STRICT book FROM public.paper_market_books WHERE market_key=p_market;
 IF book.product<>'SPOT' OR book.instrument_uid IS DISTINCT FROM p_terms->'scope'->>'instrument_uid'
 THEN RAISE EXCEPTION 'quote market scope changed'; END IF;
 INSERT INTO public.paper_quote_terms_v1 VALUES(p_scope,p_command,p_client,p_market,p_terms);
 opposite=CASE WHEN p_terms->>'side'='BUY' THEN 'ASK' ELSE 'BID' END;
 FOR level IN SELECT * FROM public.paper_market_levels WHERE market_key=p_market AND side=opposite AND available>0
 AND price >= (p_terms->>'minimum_price')::numeric AND price <= (p_terms->>'maximum_price')::numeric
 ORDER BY CASE WHEN opposite='ASK' THEN price ELSE -price END FOR UPDATE LOOP
  qty=least(div(remaining,level.price*step),div(level.available,step))*step;
  IF qty<=0 THEN CONTINUE; END IF;
  seq=seq+1;
  UPDATE public.paper_market_levels SET available=available-qty WHERE market_key=p_market AND side=opposite AND price=level.price;
  INSERT INTO public.paper_quote_allocations_v1 VALUES(p_scope,p_command,
   'paper-quote:'||p_scope||':'||p_command||':'||seq,p_market,book.event_id,opposite,level.price,qty,qty*level.price*rate);
  base=base+qty;gross=gross+qty*level.price;remaining=budget-gross;
  EXIT WHEN remaining=0;
 END LOOP;
 INSERT INTO public.paper_quote_runtime_v1 VALUES(p_scope,p_command,CASE WHEN remaining=0 THEN 'FILLED' ELSE 'EXPIRED' END,
 base,gross,remaining,CASE WHEN remaining=0 THEN 'GROSS_BUDGET_EXECUTED' ELSE 'IMMEDIATE_ATTEMPT_ENDED_WITH_UNUSED_BUDGET' END);
END $$;
REVOKE ALL ON FUNCTION public.paper_accept_quote_v1(text,text,text,text,jsonb) FROM PUBLIC;
