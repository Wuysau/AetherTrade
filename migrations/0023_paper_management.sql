-- Additive SIM-only management of the existing protected venue orders.
BEGIN;
CREATE TABLE public.paper_management_authorizations_v4(
 scope_key text NOT NULL,command_id text NOT NULL,binding jsonb NOT NULL,token_hash text NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 seconds',
 PRIMARY KEY(scope_key,command_id));
CREATE TRIGGER immutable_management_authorizations BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_management_authorizations_v4
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
REVOKE ALL ON public.paper_management_authorizations_v4 FROM PUBLIC;
CREATE TABLE public.paper_management_actions_v4(
 scope_key text NOT NULL,command_id text NOT NULL,binding_hash text NOT NULL,
 payload jsonb NOT NULL,observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope_key,command_id));
CREATE TRIGGER immutable_management_actions BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_management_actions_v4
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
REVOKE ALL ON public.paper_management_actions_v4 FROM PUBLIC;
CREATE FUNCTION public.paper_manage_order_v4(s text,id text,b jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w jsonb;target jsonb;ord public.paper_engine_orders;market text;result jsonb;BEGIN
 w=b->'write';target=b#>'{plan,target}';
 IF NOT EXISTS(SELECT FROM public.paper_management_authorizations_v4 a WHERE a.scope_key=s AND a.command_id=id
  AND a.binding=b AND a.expires_at>clock_timestamp()) THEN
  RAISE EXCEPTION 'private consumed management authorization required' USING ERRCODE='23514';END IF;
 SELECT market_key INTO market FROM public.paper_order_terms WHERE scope_key=s AND client_id=w->>'original_client_id';
 IF market IS NULL THEN RAISE EXCEPTION 'existing source order required';END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(market,0));
 SELECT * INTO ord FROM public.paper_engine_orders WHERE scope_key=s AND client_id=w->>'original_client_id' FOR UPDATE;
 IF w->>'contract_version' IS DISTINCT FROM '2026-10-30.management-v4' OR w->>'command_id' IS DISTINCT FROM id
  OR w->'scope' IS DISTINCT FROM s::jsonb OR w#>>'{scope,environment}' IS DISTINCT FROM 'PAPER'
  OR w->>'operation' IS DISTINCT FROM 'CANCEL' OR ord.client_id IS NULL
  OR ord.order_id IS DISTINCT FROM w->>'target_venue_id' OR ord.owner_strategy_id IS DISTINCT FROM w->>'owner_strategy_id'
  OR ord.ownership_epoch IS DISTINCT FROM (w->>'ownership_epoch')::bigint
  OR ord.pending<>'NONE' OR ord.certainty<>'CONFIRMED'
  OR ord.quantity IS DISTINCT FROM (target->>'original_quantity')::numeric
  OR ord.filled IS DISTINCT FROM (target->>'cumulative_quantity')::numeric THEN
  RAISE EXCEPTION 'exact current owned target required';END IF;
 -- Race is recorded independently: cancellation cannot erase a late fill.
 result=jsonb_build_object('record_kind','PAPER_MANAGEMENT_OUTCOME_V4','command_id',id,
  'binding_hash',w->>'binding_hash','original_client_id',ord.client_id,'target_venue_id',ord.order_id,
  'cancel_result',CASE WHEN ord.lifecycle IN ('SUBMITTED','PARTIALLY_FILLED') THEN 'CANCELED' ELSE 'NOT_CANCELED' END,
  'prior_status',ord.lifecycle,'cumulative_quantity',ord.filled::text,'replacement_result','NOT_ATTEMPTED',
  'funds_released',false,'children_terminal',false);
 IF ord.lifecycle IN ('SUBMITTED','PARTIALLY_FILLED') THEN
  UPDATE public.paper_engine_orders SET lifecycle='CANCELED',revision=revision+1 WHERE scope_key=s AND client_id=ord.client_id;
 END IF;
 INSERT INTO public.paper_management_actions_v4(scope_key,command_id,binding_hash,payload)
  VALUES(s,id,w->>'binding_hash',result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.paper_manage_order_v4(text,text,jsonb) FROM PUBLIC;
GRANT SELECT ON public.paper_management_authorizations_v4 TO paper_matcher_definer;
GRANT SELECT,INSERT ON public.paper_management_actions_v4 TO paper_matcher_definer;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_manage_order_v4(text,text,jsonb) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;
-- Runtime cannot author the private cross-database consumed authorization.
COMMIT;
