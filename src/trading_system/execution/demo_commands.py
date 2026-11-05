"""Dedicated Demo durable command boundary, single sender and one-shot permit.

No console endpoint creates qualification records or dispatches these commands.
The PostgreSQL runtime must be qualified separately before constructing an owner.
"""
from dataclasses import replace
import time

from trading_system.engine.binance_demo.contracts import DemoError, digest
from trading_system.runtime.demo_host import DemoRuntimeHost
from trading_system.engine.binance_demo.contracts import decimal
from sqlalchemy import select
from trading_system.storage.demo import commands

_OWNER = object()


class DemoSendPermit:
    def __init__(self, *, _owner=None, method=None, request=None, credential_ref=None, preflight=None):
        if _owner is not _OWNER:
            raise DemoError('DEMO_EXECUTION_OWNER_REQUIRED')
        self._method, self._hash, self._ref = method, digest(request), credential_ref
        self._expires, self._used, self._preflight = time.monotonic() + 1, False, preflight

    def consume(self, method, params, credential_ref):
        if self._used or time.monotonic() > self._expires or method != self._method or digest(params) != self._hash or credential_ref != self._ref:
            raise DemoError('DEMO_DURABLE_PERMIT_INVALID')
        self._used = True

    def before_network(self):
        if not self._used or time.monotonic() > self._expires:
            raise DemoError('DEMO_DURABLE_PERMIT_INVALID')
        if self._preflight is not None:
            self._preflight()
        if time.monotonic() > self._expires:
            raise DemoError('DEMO_DURABLE_PERMIT_INVALID')


class DemoCommandBoundary:
    def __init__(self, repository):
        self.repository = repository

    def submit(self, scope, *, context, **intent):
        budget = DemoRuntimeHost.admit(context)
        if scope.product != 'SPOT':
            raise DemoError('DEMO_PRODUCT_NOT_QUALIFIED')
        return self.repository.stage(scope, available=str(budget), now=context.now, **intent)


class DemoExecutionOwner:
    def __init__(self, repository, transport, *, clock=time.time, preflight=None, physical_owner=None):
        self.repository, self.transport, self.clock, self.preflight = repository, transport, clock, preflight
        self.physical_owner = physical_owner

    def dispatch(self, scope, command_id, *, context):
        if self.repository.engine.dialect.name == 'postgresql':
            if self.physical_owner is None: raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
            self.physical_owner.validate(scope)
        with self.repository.owner_guard(scope) as owner_guard:
            def verify_sender():
                if owner_guard is not None: owner_guard.validate()
                if self.physical_owner is not None: self.physical_owner.validate(scope)
                if self.preflight: self.preflight()
                if self.physical_owner is not None: self.physical_owner.validate(scope)
                # Preflight may block on account/strategy IO while the epoch
                # lock session dies; check it again before allowing work.
                if owner_guard is not None: owner_guard.validate()
            verify_sender()
            account = self.repository.account(scope)
            command = self.repository.command(scope, command_id)
            health = self.repository.health(scope)
            evidence = account.get('qualification') or {}
            # Qualification must bind this code, account epoch, key revision,
            # product and exact order type; no blanket ENABLED switch exists.
            required = {'code_sha': context.code_sha, 'account_key': scope.account_key,
                        'credential_revision': account['credential_revision'], 'product': scope.product,
                        'state': 'DEMO_VERIFIED'}
            capability = 'CANCEL' if command['method'] == 'DELETE' else command['request'].get('type')
            proof = (evidence.get('capabilities') or {}).get(capability) or (evidence if evidence.get('order_type') == capability else {})
            if any(evidence.get(k) != v for k, v in required.items()) or proof.get('state') != 'DEMO_VERIFIED' or not proof.get('evidence_sha256') or not proof.get('official_contract_revision'):
                raise DemoError('DEMO_CAPABILITY_NOT_QUALIFIED')
            management = command['purpose'] in {'CANCEL', 'REDUCTION', 'PROTECTION'}
            if health['risk_breaches'] and not management:
                raise DemoError('DEMO_EXECUTION_RISK_BREACH')
            if management:
                if command['method'] == 'DELETE':
                    with self.repository.engine.connect() as conn:
                        targets = list(conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.namespace == 'SPOT', commands.c.client_id == command['request']['origClientOrderId'])).mappings())
                    if len(targets) != 1 or targets[0]['request']['symbol'] != command['request']['symbol'] or targets[0]['state'] not in {'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED'}:
                        raise DemoError('DEMO_CANCEL_TARGET_INVALID')
                    if targets[0]['order_status'] in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'}:
                        raise DemoError('DEMO_CANCEL_TARGET_TERMINAL')
                elif command['request']['side'] != 'SELL' or decimal(command['reserve_amount']) > self.repository.asset_deltas(scope).get('BTC', 0):
                    raise DemoError('DEMO_UNOWNED_EXPOSURE')
            def admit(value):
                return DemoRuntimeHost.admit_management(value, cancel=command['method'] == 'DELETE') if management else DemoRuntimeHost.admit(value)
            current = replace(context, now=int(self.clock()), account_state=account['state'],
                unresolved_unknown=health['unknown'], unprotected=health['unprotected'], pending_inbox=health['pending_inbox'])
            admit(current)
            if self.transport.credential_ref != account['credential_ref']:
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            # Network time validation precedes MAY; it does not create an order.
            self.transport.sync_time()
            current_account = self.repository.account(scope)
            current_health = self.repository.health(scope)
            if current_account['revision'] != account['revision']:
                raise DemoError('DEMO_SEND_AUTHORIZATION_STALE')
            current = replace(current, now=int(self.clock()), account_state=current_account['state'],
                unresolved_unknown=current_health['unknown'], unprotected=current_health['unprotected'], pending_inbox=current_health['pending_inbox'])
            admit(current)
            verify_sender()
            def final_admission(now):
                verify_sender()
                admit(replace(current, now=now))
            command = self.repository.begin_send(scope, command_id, expected_revision=account['revision'], now=current.now, clock=self.clock, validator=final_admission)
            permit = DemoSendPermit(_owner=_OWNER, method=command['method'], request=command['request'], credential_ref=account['credential_ref'], preflight=verify_sender)
            try:
                # Stop/revocation may occur after MAY committed. Never start a
                # new send after observing it; preserve UNKNOWN conservatively.
                verify_sender()
                response = (self.transport.send_order if command['method'] == 'POST' else self.transport.cancel_order)(command['request'], permit=permit)
                # A response does not certify ownership survived in-flight IO.
                # If either fence was lost, retain MAY/UNKNOWN for recovery.
                verify_sender()
                self.repository.acknowledge(scope, command_id, response, validator=verify_sender)
            except Exception:
                # All write failures are conservative UNKNOWN. No generic HTTP
                # retry, new client ID, or reservation release is permitted.
                self.repository.mark_unknown(scope, command_id, 'DEMO_SEND_OUTCOME_UNKNOWN')
                raise DemoError('DEMO_SEND_OUTCOME_UNKNOWN') from None
            return self.repository.command(scope, command_id)
