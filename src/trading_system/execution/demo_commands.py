"""Dedicated Demo durable command boundary, single sender and one-shot permit.

No console endpoint creates qualification records or dispatches these commands.
The PostgreSQL runtime must be qualified separately before constructing an owner.
"""
from dataclasses import replace
import time

from trading_system.engine.binance_demo.contracts import DemoError, digest
from trading_system.runtime.demo_host import DemoRuntimeHost

_OWNER = object()


class DemoSendPermit:
    def __init__(self, *, _owner=None, method=None, request=None, credential_ref=None):
        if _owner is not _OWNER:
            raise DemoError('DEMO_EXECUTION_OWNER_REQUIRED')
        self._method, self._hash, self._ref = method, digest(request), credential_ref
        self._expires, self._used = time.monotonic() + 1, False

    def consume(self, method, params, credential_ref):
        if self._used or time.monotonic() > self._expires or method != self._method or digest(params) != self._hash or credential_ref != self._ref:
            raise DemoError('DEMO_DURABLE_PERMIT_INVALID')
        self._used = True


class DemoCommandBoundary:
    def __init__(self, repository):
        self.repository = repository

    def submit(self, scope, *, context, **intent):
        budget = DemoRuntimeHost.admit(context)
        if scope.product != 'SPOT':
            raise DemoError('DEMO_PRODUCT_NOT_QUALIFIED')
        return self.repository.stage(scope, available=str(budget), now=context.now, **intent)


class DemoExecutionOwner:
    def __init__(self, repository, transport, *, clock=time.time):
        self.repository, self.transport, self.clock = repository, transport, clock

    def dispatch(self, scope, command_id, *, context):
        with self.repository.owner_guard(scope):
            account = self.repository.account(scope)
            command = self.repository.command(scope, command_id)
            health = self.repository.health(scope)
            evidence = account.get('qualification') or {}
            # Qualification must bind this code, account epoch, key revision,
            # product and exact order type; no blanket ENABLED switch exists.
            required = {'code_sha': context.code_sha, 'account_key': scope.account_key,
                        'credential_revision': account['credential_revision'], 'product': scope.product,
                        'order_type': command['request'].get('type'), 'state': 'DEMO_VERIFIED'}
            if any(evidence.get(k) != v for k, v in required.items()) or not evidence.get('evidence_sha256') or not evidence.get('official_contract_revision'):
                raise DemoError('DEMO_CAPABILITY_NOT_QUALIFIED')
            current = replace(context, now=int(self.clock()), account_state=account['state'],
                unresolved_unknown=health['unknown'], unprotected=health['unprotected'])
            DemoRuntimeHost.admit(current)
            if self.transport.credential_ref != account['credential_ref']:
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            # Network time validation precedes MAY; it does not create an order.
            self.transport.sync_time()
            current_account = self.repository.account(scope)
            current_health = self.repository.health(scope)
            if current_account['revision'] != account['revision']:
                raise DemoError('DEMO_SEND_AUTHORIZATION_STALE')
            current = replace(current, now=int(self.clock()), account_state=current_account['state'],
                unresolved_unknown=current_health['unknown'], unprotected=current_health['unprotected'])
            DemoRuntimeHost.admit(current)
            command = self.repository.begin_send(scope, command_id, expected_revision=account['revision'], now=current.now)
            permit = DemoSendPermit(_owner=_OWNER, method=command['method'], request=command['request'], credential_ref=account['credential_ref'])
            try:
                response = (self.transport.send_order if command['method'] == 'POST' else self.transport.cancel_order)(command['request'], permit=permit)
                self.repository.acknowledge(scope, command_id, response)
            except Exception:
                # All write failures are conservative UNKNOWN. No generic HTTP
                # retry, new client ID, or reservation release is permitted.
                self.repository.mark_unknown(scope, command_id, 'DEMO_SEND_OUTCOME_UNKNOWN')
                raise DemoError('DEMO_SEND_OUTCOME_UNKNOWN') from None
            return self.repository.command(scope, command_id)
