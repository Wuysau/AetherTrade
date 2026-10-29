"""Read-only views of existing scoped ledgers. No authority, replay or venue IO.

The connection factory and scope allowlist are trusted host dependencies. A JSONB
canonical digest is labelled as such; stored historical hashes are never rewritten.
Observation never calls a business UoW or produces its commit markers/permits.
"""
from contextlib import contextmanager
import base64
import json
from pathlib import Path
from decimal import InvalidOperation
from jsonschema import ValidationError

from fastapi import Depends, Query
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.order_codec import VersionedOrderCodec, KINDS_BY_VERSION
from trading_system.storage.models import scope_id
from trading_system.storage.models import decode
from trading_system.contracts import types as t
from .errors import ApiError


STAGES = {
    'CAPABILITY': ('FEATURE_NOT_IMPLEMENTED', 'ADAPTER_NOT_QUALIFIED', 'VENUE_NOT_SUPPORTED',
                   'SYMBOL_NOT_ALLOWED', 'ACCOUNT_NOT_ELIGIBLE', 'CURRENT_QUALIFICATION_NOT_REGISTERED',
                   'HOST_ADMISSION_NOT_REGISTERED'),
    'RULE': ('INVALID_PARAMETER_COMBINATION', 'RULE_REVISION_CHANGED'),
    'RISK': ('RISK_REJECTED', 'SAFETY_POLICY_CONFLICT'),
    'DATA': ('SIMULATION_DATA_INSUFFICIENT',),
    'EXECUTION': ('SUBMIT_UNKNOWN', 'CANCEL_UNKNOWN', 'MAY_HAVE_SENT', 'RECONCILIATION_REQUIRED'),
}


def classify_rejection(code):
    return {'code': code, 'stage': next((stage for stage, codes in STAGES.items() if code in codes), 'UNKNOWN')}


def capability_context(snapshot=None):
    # Archived claims are not a current Registry query, even if PAPER_VERIFIED.
    return dict(archived_snapshot=snapshot, current_qualification='UNKNOWN',
                execution_authorized=False,
                admission_blockers=['CURRENT_QUALIFICATION_NOT_REGISTERED', 'HOST_ADMISSION_NOT_REGISTERED'])


def project_reservation(initial, latest):
    result = dict(reservation_id=initial.get('reservation_id'), initial=initial,
                  current_status='NOT_OBSERVED', projection=None, raw_projection=latest)
    if latest is None:
        return result
    try:
        original = decode(t.CapitalReservation, initial)
        projection = decode(t.ReservationProjection, latest['payload'])
        immutable = ('reservation_id','environment','collateral_group_id','venue_account_id',
                     'owner_strategy_id','intent_id','asset','amount','created_at','expires_at','command_refs')
        if (any(getattr(original,k)!=getattr(projection.reservation,k) for k in immutable)
            or type(projection.sequence) is not int or projection.sequence<1
            or projection.sequence!=latest['sequence']
            or not all(v.is_finite() for v in (projection.held,projection.reflected,projection.release_pending))
            or not 0<=projection.held<=original.amount
            or not 0<=projection.reflected<=projection.held
            or not 0<=projection.release_pending<=projection.held):
            raise ValueError('Invalid recorded projection')
        result.update(current_status='RECORDED_PROJECTION', projection=latest['payload'])
    except (ValueError, TypeError, KeyError, InvalidOperation):
        result['current_status']='UNKNOWN'
    return result


def project_program(payload, *, source):
    version = payload.get('contract_version')
    status = 'UNSUPPORTED_VERSION'
    if 'order-program' in KINDS_BY_VERSION.get(version, {}):
        try:
            VersionedOrderCodec().decode('order-program', payload)
            status = 'DECODED'
        except (ValueError, TypeError, KeyError, ValidationError):
            status = 'INVALID_RECORDED_CONTRACT'
    decoded = status == 'DECODED'
    return dict(program_id=payload.get('program_id'), contract_version=version,
        source=source, codec_status=status, canonical_payload_hash=canonical_hash(payload), payload=payload,
        template=payload.get('template') if decoded else None,
        execution_mode=payload.get('execution_mode') if decoded else None,
        nodes=payload.get('nodes', []) if decoded else [],
        execution_state='NOT_OBSERVED', protection_status='NOT_OBSERVED',
        children_complete=None, state_origin='NOT_OBSERVED', execution_authorized=False)


# Names are fixed implementation constants, never request parameters.
TABLES = ('order_programs', 'order_program_intakes', 'order_program_states', 'order_program_bindings',
          'order_writes_v2', 'order_funding_links', 'audit_events', 'program_request_bindings', 'reservations')


class OrderObservationService:
    def __init__(self, connection_factory, scopes):
        self.connect = connection_factory
        self.scopes = {scope_id(s): s for s in scopes}
        if not self.scopes:
            raise ValueError('Explicit observation scopes required')

    def _scope(self, key):
        if key not in self.scopes:
            raise ApiError('NOT_FOUND', '未找到授权范围内的订单计划。', 404)
        return self.scopes[key]

    @contextmanager
    def _read(self):
        conn = self.connect()
        try:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            available = {name for name in TABLES if conn.execute('SELECT to_regclass(%s)', ('ledger.' + name,)).fetchone()[0]}
            yield conn, available
        finally:
            conn.rollback()
            conn.close()

    def scope_views(self):
        return [dict(scope_id=key, scope=domain_payload(scope)) for key, scope in self.scopes.items()]

    def _source_sql(self, available):
        parts = []
        if 'order_programs' in available:
            parts.append("SELECT scope_id,record_id AS program_id,payload,'order_programs' AS source,0 AS rank "
                         'FROM ledger.order_programs WHERE scope_id=ANY(%s)')
        if 'order_program_intakes' in available:
            parts.append("SELECT scope_id,payload->'proposal'->'program'->>'program_id' AS program_id,"
                         "payload->'proposal'->'program' AS payload,'order_program_intakes' AS source,1 AS rank "
                         "FROM ledger.order_program_intakes WHERE scope_id=ANY(%s) "
                         "AND jsonb_typeof(payload->'proposal'->'program')='object'")
        return parts

    def list(self, *, scope=None, limit=10, cursor=None):
        if type(limit) is not int or limit not in (10, 20, 50):
            raise ApiError('INVALID_REQUEST', '每页记录数只支持 10、20 或 50。', 422)
        keys = [scope] if scope is not None else list(self.scopes)
        for key in keys: self._scope(key)
        after = ('', '')
        if cursor:
            try:
                if len(cursor) > 4096: raise ValueError()
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if type(decoded) is not list or len(decoded) != 2 or not all(type(v) is str for v in decoded): raise ValueError()
                if decoded[0] not in keys: raise ValueError()
                after = tuple(decoded)
            except (ValueError, TypeError, UnicodeError):
                raise ApiError('INVALID_CURSOR', '分页位置无效，请从首页重新读取。', 422) from None
        with self._read() as (conn, available):
            parts = self._source_sql(available)
            if not parts:
                return dict(items=[], next_cursor=None, scopes=self.scope_views(), read_only=True,
                            observation_status='LEDGER_NOT_MIGRATED')
            # Stable keyset order. Intake duplicates are deterministic; persisted
            # program takes precedence. No temporal guess at missing venue state.
            query = ('WITH sources AS (' + ' UNION ALL '.join(parts) +
                '), programs AS (SELECT DISTINCT ON (scope_id,program_id) scope_id,program_id,payload,source '
                'FROM sources WHERE program_id IS NOT NULL ORDER BY scope_id,program_id,rank,payload::text) '
                'SELECT scope_id,program_id,payload,source FROM programs WHERE (scope_id,program_id)>(%s,%s) '
                'ORDER BY scope_id,program_id LIMIT %s')
            rows = conn.execute(query, tuple([keys] * len(parts)) + (*after, limit + 1)).fetchall()
            items = [dict(project_program(row[2], source=row[3]), scope_id=row[0],
                          scope=domain_payload(self.scopes[row[0]])) for row in rows[:limit]]
            next_cursor = (base64.urlsafe_b64encode(json.dumps(list(rows[limit-1][:2])).encode()).decode()
                           if len(rows) > limit else None)
            return dict(items=items, next_cursor=next_cursor, scopes=self.scope_views(), read_only=True,
                        observation_status='AVAILABLE')

    def _records(self, conn, available, table, key, condition, params):
        if table not in available: return [], False
        rows = conn.execute(f'SELECT DISTINCT ON (record_id) record_id,sequence,payload,transaction_id '
            f'FROM ledger.{table} WHERE scope_id=%s AND ({condition}) '
            'ORDER BY record_id,sequence DESC LIMIT 501', (key, *params)).fetchall()
        return [dict(record_id=r[0], sequence=r[1], payload=r[2], transaction_id=r[3],
                     canonical_payload_hash=canonical_hash(r[2])) for r in rows[:500]], len(rows)>500

    def detail(self, program_id, *, scope):
        self._scope(scope)
        with self._read() as (conn, available):
            programs, _ = self._records(conn, available, 'order_programs', scope, 'record_id=%s', (program_id,))
            intakes, intake_truncated = self._records(conn, available, 'order_program_intakes', scope,
                "payload->'proposal'->'program'->>'program_id'=%s", (program_id,))
            if not programs and not intakes:
                raise ApiError('NOT_FOUND', '未找到授权范围内的订单计划。', 404)
            raw = programs[0]['payload'] if programs else intakes[0]['payload']['proposal']['program']
            view = project_program(raw, source='order_programs' if programs else 'order_program_intakes')
            records, truncated = {}, {'intakes': intake_truncated}
            for label, table in (('states','order_program_states'), ('bindings','order_program_bindings'),
                                 ('commands','order_writes_v2'), ('funding','order_funding_links')):
                records[label], truncated[label] = self._records(conn, available, table, scope,
                    'record_id=%s' if label in ('states','bindings') else "payload->>'program_id'=%s", (program_id,))
            records['requests'], truncated['requests'] = self._records(conn, available, 'program_request_bindings', scope,
                'record_id=ANY(%s)', ([r['record_id'] for r in records['commands']],))
            intents = sorted({r['payload']['intent_id'] for r in intakes if r['payload'].get('intent_id')} |
                             {r['payload']['funding_intent_id'] for r in records['bindings'] if r['payload'].get('funding_intent_id')} |
                             {r['payload']['intent_id'] for r in records['commands'] if r['payload'].get('intent_id')})
            records['financial'], truncated['financial'] = self._records(conn, available, 'audit_events', scope,
                "record_id=ANY(%s) OR payload->>'program_id'=%s OR payload->'authority'->>'program_id'=%s", ([prefix+i for i in intents for prefix in (
                    'generic-program-admission:', 'program-financial-bundle:', 'program-financial-decision:',
                    'program-residual-duty:')], program_id, program_id))
            records['reservations'], truncated['reservations'] = self._records(conn, available, 'reservations', scope,
                "payload->>'intent_id'=ANY(%s)", (intents,))
            records['reservation_projections'], truncated['reservation_projections'] = self._records(
                conn, available, 'audit_events', scope, 'record_id=ANY(%s)',
                (['ip07:reservation:'+r['record_id'] for r in records['reservations']],))
            projection_by_id={r['record_id']:r for r in records['reservation_projections']}
            view['reservation_observations']=[project_reservation(r['payload'],projection_by_id.get(
                'ip07:reservation:'+r['record_id'])) for r in records['reservations']]
            activations = [r for r in records['financial'] if r['payload'].get('record_kind')=='LOCAL_PROGRAM_ACTIVATION']
            records['local_states'], truncated['local_states'] = self._records(conn, available, 'audit_events', scope,
                'record_id=ANY(%s)', (['local-program-state:'+r['payload']['activation_id'] for r in activations],))
            state = records['states'][0]['payload'] if records['states'] else None
            state_origin = 'VENUE_LIFECYCLE_ARCHIVE' if state else 'NOT_OBSERVED'
            if not state and len(records['local_states'])==1:
                state = records['local_states'][0]['payload']
                state_origin = 'LOCAL_ACTIVATION'
            view['state_observation'] = None
            if state:
                try:
                    VersionedOrderCodec().decode('program-state', state)
                    view.update(execution_state=state['status'],
                        state_observation=state, state_origin=state_origin,
                        protection_status=state.get('protection_status', 'UNVERIFIED_DUTY'),
                        children_complete=(state.get('parent_observation') or {}).get('child_inventory'))
                except (ValueError, TypeError, KeyError, ValidationError):
                    view['state_codec_status'] = 'UNSUPPORTED_OR_INVALID_RECORDED_STATE'
            archives = [r['payload']['archive'] for r in records['financial'] if isinstance(r['payload'].get('archive'), dict)]
            snapshot = archives[0].get('capabilities') if len(archives)==1 else None
            restrictions = (state or {}).get('restriction_codes', [])
            view.update(scope_id=scope, scope=domain_payload(self.scopes[scope]), intakes=intakes,
                records=records, truncated=truncated, capability=capability_context(snapshot),
                rejection_reasons=[classify_rejection(code) for code in restrictions],
                observation_notice='COMMITTED_RECEIPT_IS_NOT_EXECUTION; EMPTY_CHILDREN_IS_NOT_FLAT')
            return view


def capability_catalog():
    path = Path(__file__).resolve().parents[3] / 'qualification/binance_orders/reference/binance-order-capability-catalog.json'
    if not path.is_file():
        return dict(capability_context(), catalog_status='UNAVAILABLE', features=[])
    original = json.loads(path.read_text(encoding='utf-8'))
    return dict(capability_context(), catalog_status='REFERENCE_ONLY', as_of=original['as_of'],
        reference_hash=canonical_hash(original),
        features=[dict(id=f['id'], product=f['product'], feature=f['feature'],
                       target_required=f['target_required'], official_documented=f['official_documented'],
                       compilation_status='UNKNOWN', engineering_test_status='NOT_EVALUATED',
                       venue_qualification='NOT_RUN') for f in original['features']],
        expression_versions={v: sorted(k) for v,k in KINDS_BY_VERSION.items()}, read_only=True)


def observer_for_runtime(runtime):
    if runtime is None:
        return None
    from .paper_runtime import UnifiedPaperRuntime, scope_for, SYMBOLS
    # This host only owns the existing explicitly provisioned console scopes.
    # Other hosts inject their own trusted account grants at composition time.
    if not isinstance(runtime, UnifiedPaperRuntime):
        return None
    return OrderObservationService(runtime.connect, tuple(scope_for(s) for s in SYMBOLS))


def install_order_observation_routes(app, *, authenticated, observer):
    @app.get('/api/order-capabilities')
    def capabilities(_=Depends(authenticated)):
        return capability_catalog()

    @app.get('/api/order-programs')
    def programs(scope: str | None = Query(None, max_length=256), limit: int = Query(10),
                 cursor: str | None = Query(None, max_length=4096), _=Depends(authenticated)):
        if limit not in (10, 20, 50):
            raise ApiError('INVALID_REQUEST', '每页记录数只支持 10、20 或 50。', 422)
        service = observer()
        if service is None:
            return dict(items=[], scopes=[], next_cursor=None, read_only=True, observation_status='UNCONFIGURED')
        return service.list(scope=scope, limit=limit, cursor=cursor)

    @app.get('/api/order-programs/{program_id}')
    def program(program_id: str, scope: str = Query(..., max_length=256), _=Depends(authenticated)):
        service = observer()
        if service is None:
            raise ApiError('OBSERVATION_UNCONFIGURED', '只读订单账本尚未配置。', 503)
        return service.detail(program_id, scope=scope)
