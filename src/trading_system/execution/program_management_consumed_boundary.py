"""Current post-wait guards; a snapshot cannot renew a consumed action."""
from .program_management_commands import ManagementPermit,ManagementRepository,token_hash,require
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.models import scope_id

def recheck_consumed(boundary,permit,lease):
    require(type(permit) is ManagementPermit,'MANAGEMENT_PRIVATE_PERMIT_REQUIRED')
    target=boundary.service.capture_for(permit.command_id)
    with boundary.uow.transaction(boundary.scope) as tx:
        binding=ManagementRepository(tx).read(permit.command_id)
        require(binding is not None and binding.write.state=='MAY_HAVE_SENT'
            and binding.write.binding_hash==permit.binding_hash and binding.write.request_hash==permit.request_hash,
            'MANAGEMENT_CONSUMED_BINDING_CHANGED')
        row=tx.execute('SELECT m.token_hash,m.context,m.expires_at,c.token_hash,c.context FROM ledger.management_dispatch_marks m '
            'JOIN ledger.management_permit_consumptions c USING(scope_id,command_id) WHERE m.scope_id=%s AND m.command_id=%s '
            'AND m.database_transaction<>pg_current_xact_id() AND c.database_transaction<>pg_current_xact_id() AND m.expires_at>clock_timestamp()',
            (scope_id(boundary.scope),permit.command_id)).fetchone()
        require(row is not None and row[0]==row[3]==token_hash(permit.token) and row[1]['lease']==row[4]['lease'],
            'MANAGEMENT_COMMITTED_CONSUMPTION_REQUIRED')
        context,clock,_=boundary.service.current(tx,binding,target,lease)
        require(context['lease']==row[4]['lease'] and utc_timestamp(clock.utc_now)<utc_timestamp(permit.expires_at)
            and clock.monotonic_ns<permit.deadline_monotonic_ns,'MANAGEMENT_CONSUMED_PERMIT_EXPIRED')
        return binding
