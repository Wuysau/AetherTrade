-- SIM-only immutable effective terms, authenticated by consumed bridge authority.
BEGIN;
CREATE TABLE public.paper_effective_orders_v1(
 scope_key text NOT NULL,client_id text NOT NULL,command_id text NOT NULL,
 prior_revision bigint NOT NULL,result_revision bigint NOT NULL,
 original_quantity numeric NOT NULL,prior_quantity numeric NOT NULL,effective_quantity numeric NOT NULL,
 cumulative_quantity numeric NOT NULL,queue_priority bigint NOT NULL,binding jsonb NOT NULL,
 receipt jsonb NOT NULL,PRIMARY KEY(scope_key,command_id),UNIQUE(scope_key,client_id,result_revision),
 CHECK(effective_quantity>cumulative_quantity AND effective_quantity<prior_quantity AND prior_quantity<=original_quantity));
CREATE TRIGGER immutable_effective_orders BEFORE UPDATE OR DELETE OR TRUNCATE ON public.paper_effective_orders_v1
 FOR EACH STATEMENT EXECUTE FUNCTION public.paper_market_immutable();
REVOKE ALL ON public.paper_effective_orders_v1 FROM PUBLIC;
GRANT SELECT,INSERT ON public.paper_effective_orders_v1 TO paper_matcher_definer;

CREATE FUNCTION public.paper_effective_quantity_v1(s text,c text,original numeric) RETURNS numeric
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e public.paper_effective_orders_v1;BEGIN
 SELECT * INTO e FROM public.paper_effective_orders_v1 WHERE scope_key=s AND client_id=c ORDER BY result_revision DESC LIMIT 1;
 IF e.command_id IS NULL THEN RETURN original;END IF;
 IF e.original_quantity<>original OR NOT EXISTS(SELECT FROM public.paper_management_authorizations_v4 a
   WHERE a.scope_key=s AND a.command_id=e.command_id AND a.binding=e.binding)
 OR NOT EXISTS(SELECT FROM public.paper_management_actions_v4 a WHERE a.scope_key=s AND a.command_id=e.command_id
   AND a.binding_hash=e.binding#>>'{write,binding_hash}' AND a.payload=e.receipt) THEN
  RAISE EXCEPTION 'authenticated effective terms required';END IF;
 RETURN e.effective_quantity;
END $$;
REVOKE ALL ON FUNCTION public.paper_effective_quantity_v1(text,text,numeric) FROM PUBLIC;

ALTER FUNCTION public.paper_manage_order_v4(text,text,jsonb) RENAME TO paper_cancel_order_v4;
CREATE FUNCTION public.paper_manage_order_v4(s text,id text,b jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w jsonb;target jsonb;ord public.paper_engine_orders;terms public.paper_order_terms;runtime public.paper_order_runtime_v2;
 qty numeric;result jsonb;BEGIN
 w=b->'write';target=b#>'{plan,target}';
 IF w->>'operation'='CANCEL' THEN RETURN public.paper_cancel_order_v4(s,id,b);END IF;
 IF NOT public.paper_management_authorization_current_v4(s,id,b) THEN
  RAISE EXCEPTION 'private consumed management authorization required' USING ERRCODE='23514';END IF;
 SELECT * INTO terms FROM public.paper_order_terms WHERE scope_key=s AND client_id=w->>'original_client_id';
 IF terms.client_id IS NULL THEN RAISE EXCEPTION 'original terms required';END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(terms.market_key,0));
 IF NOT public.paper_management_authorization_current_v4(s,id,b) THEN RAISE EXCEPTION 'authorization expired after market lock';END IF;
 SELECT * INTO ord FROM public.paper_engine_orders WHERE scope_key=s AND client_id=terms.client_id FOR UPDATE;
 IF NOT public.paper_management_authorization_current_v4(s,id,b) THEN RAISE EXCEPTION 'authorization expired after row lock';END IF;
 SELECT * INTO runtime FROM public.paper_order_runtime_v2 WHERE scope_key=s AND client_id=terms.client_id;
 qty=(b#>>'{plan,instruction,new_quantity}')::numeric;
 IF w->>'operation' IS DISTINCT FROM 'KEEP_PRIORITY' OR w->>'command_id' IS DISTINCT FROM id
 OR w->>'contract_version' IS DISTINCT FROM '2026-10-30.management-v4'
 OR w->'scope' IS DISTINCT FROM s::jsonb OR w#>>'{scope,environment}' IS DISTINCT FROM 'PAPER'
 OR w#>>'{scope,product}' IS DISTINCT FROM 'SPOT'
 OR ord.order_id IS DISTINCT FROM w->>'target_venue_id' OR ord.owner_strategy_id IS DISTINCT FROM w->>'owner_strategy_id'
 OR ord.ownership_epoch IS DISTINCT FROM (w->>'ownership_epoch')::bigint
 OR ord.lifecycle NOT IN ('SUBMITTED','PARTIALLY_FILLED') OR ord.pending<>'NONE' OR ord.certainty<>'CONFIRMED'
 OR ord.quantity IS DISTINCT FROM (target->>'original_quantity')::numeric
 OR ord.filled IS DISTINCT FROM (target->>'cumulative_quantity')::numeric
 OR ord.revision IS DISTINCT FROM ((b->>'target_source_json')::jsonb#>>'{orders,0,lifecycle_revision}')::bigint
 OR qty IS NULL OR qty<=ord.filled OR qty>=ord.quantity
 OR terms.terms#>>'{compilation,original_program,template}' IS DISTINCT FROM 'ATOMIC'
 OR terms.terms#>>'{spec,price,kind}' IS DISTINCT FROM 'FIXED_LIMIT'
 OR terms.terms#>>'{spec,trigger,kind}' IS DISTINCT FROM 'NONE'
 OR terms.terms#>>'{spec,reduce_only}' IS DISTINCT FROM 'false'
 OR terms.terms#>>'{spec,iceberg_quantity}' IS NOT NULL
 OR terms.terms#>>'{spec,stp}' IS DISTINCT FROM 'NONE'
 OR runtime.prevented_quantity IS DISTINCT FROM 0::numeric
 OR public.paper_effective_quantity_v1(s,ord.client_id,(terms.terms->>'quantity')::numeric)<>ord.quantity THEN
 RAISE EXCEPTION 'exact current reducible atomic target required';END IF;
 result=jsonb_build_object('record_kind','PAPER_KEEP_PRIORITY_RECEIPT_V1','command_id',id,
 'binding_hash',w->>'binding_hash','original_client_id',ord.client_id,'target_venue_id',ord.order_id,
 'prior_revision',ord.revision,'result_revision',ord.revision+1,'effective_quantity',qty::text,
 'original_quantity',terms.terms->>'quantity','cumulative_quantity',ord.filled::text,
 'queue_priority',runtime.queue_priority,'funds_released',false,'children_terminal',false);
 IF NOT public.paper_management_authorization_current_v4(s,id,b) THEN RAISE EXCEPTION 'authorization expired before mutation';END IF;
 INSERT INTO public.paper_effective_orders_v1 VALUES(s,ord.client_id,id,ord.revision,ord.revision+1,
 (terms.terms->>'quantity')::numeric,ord.quantity,qty,ord.filled,runtime.queue_priority,b,result);
 UPDATE public.paper_engine_orders SET quantity=qty,revision=revision+1 WHERE scope_key=s AND client_id=ord.client_id;
 INSERT INTO public.paper_management_actions_v4(scope_key,command_id,binding_hash,payload) VALUES(s,id,w->>'binding_hash',result);
 IF NOT public.paper_management_authorization_current_v4(s,id,b) THEN RAISE EXCEPTION 'authorization expired before return';END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.paper_manage_order_v4(text,text,jsonb) FROM PUBLIC;
GRANT USAGE,CREATE ON SCHEMA public TO paper_matcher_definer;
ALTER FUNCTION public.paper_effective_quantity_v1(text,text,numeric) OWNER TO paper_matcher_definer;
ALTER FUNCTION public.paper_manage_order_v4(text,text,jsonb) OWNER TO paper_matcher_definer;
REVOKE CREATE ON SCHEMA public FROM paper_matcher_definer;
-- Preserve the installed 0026 matcher, including shared quote/native depth and
-- position limits. Replace only its original/effective quantity proof.
DO $$ DECLARE body text;needle text;BEGIN
 body=pg_get_functiondef('public.paper_match_order(text,text)'::regprocedure);
 needle='(ord.terms->>''quantity'')::numeric<>ord.quantity';
 IF strpos(body,needle)=0 THEN RAISE EXCEPTION 'expected shared matcher quantity guard missing';END IF;
 body=replace(body,needle,'public.paper_effective_quantity_v1(ord.scope_key,ord.client_id,(ord.terms->>''quantity'')::numeric)<>ord.quantity');
 EXECUTE body;
END $$;
COMMIT;
