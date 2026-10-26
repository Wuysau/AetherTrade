"""Dependency-injected ASGI control API. No server, login or trading client starts.

Frozen OpenAPI supplies routing and request validation. Unbound downstream
queries/proposal ingestion explicitly return unavailable, never fabricated data.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import re

from jsonschema import Draft202012Validator, ValidationError
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload
from trading_system.contracts.wire import ContractCodec
from trading_system.storage.models import decode
from .auth import ControlError
from .commands import ControlCommandService, ControlQuery
from .policies import DeploymentService, PolicyService


class ControlApp:
    def __init__(self, auth, inbox, *, contract_path=None):
        self.auth = auth
        self.commands = ControlCommandService(inbox)
        self.query = ControlQuery(inbox)
        self.policies = PolicyService(inbox)
        self.deployments = DeploymentService(inbox)
        path = contract_path or Path(__file__).resolve().parents[3] / 'contracts/control-openapi.yaml'
        self.contract = json.loads(Path(path).read_text(encoding='utf-8'))
        self.routes = []
        for path, methods in self.contract['paths'].items():
            pattern = '^/api' + re.escape(path).replace(r'\{id\}', r'(?P<id>[^/]+)') + '$'
            self.routes.append((re.compile(pattern), methods))

    def validate(self, schema, value):
        # All schema refs are frozen local component references; no network resolver.
        Draft202012Validator({**schema, 'components': self.contract['components']},
                             format_checker=Draft202012Validator.FORMAT_CHECKER).validate(value)

    def dispatch(self, method, path, *, scheme, headers, body=None, query=None):
        """Synchronous adapter seam, also exercised by offline unit probes."""
        try:
            actor = self.auth.authenticate(scheme, headers.get('authorization', ''))
            operation = None
            match = None
            for pattern, methods in self.routes:
                match = pattern.fullmatch(path)
                if match:
                    operation = methods.get(method.lower())
                    if operation is None:
                        raise ControlError(405, 'METHOD_NOT_ALLOWED')
                    break
            if operation is None:
                raise ControlError(404, 'RESOURCE_NOT_FOUND')
            query = query or {}
            for parameter in operation.get('parameters', []):
                if parameter['in'] == 'header':
                    value = headers.get(parameter['name'].lower())
                elif parameter['in'] == 'query':
                    value = query.get(parameter['name'])
                    if value is not None and parameter['schema'].get('type') == 'integer':
                        value = int(value)
                else:
                    value = match.group(parameter['name'])
                if value is None:
                    if parameter.get('required'):
                        raise ControlError(400, 'MISSING_PARAMETER')
                else:
                    self.validate(parameter['schema'], value)
            name = operation['operationId']
            if method.upper() == 'POST':
                canonical_json(body)  # reject floats, non-JSON and non-finite numbers
                self.validate(operation['requestBody']['content']['application/json']['schema'], body)
                revision = body.get('expected_revision')
                # Proposal has no client authority revision. Unbound ingress stays unavailable.
                if revision is not None and headers['if-match'] != str(revision):
                    raise ControlError(409, 'POLICY_CHANGED')
                key = headers['idempotency-key']
                if name == 'enqueueControl':
                    result = self.commands.enqueue(decode(t.ControlCommandRequest, body), actor, key)
                elif name == 'reviseRiskPolicy':
                    draft = t.RiskPolicyDraft(policy=ContractCodec().decode('risk-policy', body['policy']),
                        expected_revision=body['expected_revision'], author_id=body['author_id'])
                    result = self.policies.revise(match.group('id'), draft, actor, key)
                elif name == 'createDeployment':
                    result = self.deployments.create(match.group('id'), body, actor, key)
                else:
                    raise ControlError(503, 'DOWNSTREAM_UNAVAILABLE')
                return 202, domain_payload(result)
            # Bind query scope before looking up any record or downstream service.
            if 'environment' in query:
                requested = t.ScopeKey(environment=t.Environment(query['environment']),
                    venue_account_id=query['venue_account_id'], product=t.Product(query['product']),
                    instrument_uid=query['instrument_uid'], venue_leg=query['venue_leg'])
                if requested not in actor.scopes or requested.environment != actor.environment:
                    raise ControlError(403, 'AUTHORIZATION_SCOPE_MISMATCH')
            else:
                requested = None
            if name == 'readControl':
                # Authorize only the explicitly requested scope. An unrelated
                # revoked scope must not block this read or be queried first.
                result = self.query.get(match.group('id'), replace(actor, scopes=(requested,)))
                if result.request.scope != requested:
                    raise ControlError(404, 'CONTROL_NOT_FOUND')
                return 200, domain_payload(result)
            raise ControlError(503, 'DOWNSTREAM_UNAVAILABLE')
        except ControlError as exc:
            return self.error(exc.status, exc.code)
        except (ValidationError, ValueError, TypeError, KeyError):
            return self.error(422, 'INVALID_REQUEST')
        except Exception:
            # Never leak SQL/connection strings or claim acceptance after failed commit.
            return self.error(503, 'CONTROL_DEPENDENCY_UNAVAILABLE')

    @staticmethod
    def error(status, code):
        return status, {'reason_code': code, 'message': code,
                        'correctable_fields': [], 'allowed_actions': []}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            raise RuntimeError('only HTTP is supported by this adapter')
        from urllib.parse import parse_qsl
        headers = {}
        invalid = False
        for key, value in scope.get('headers', []):
            key = key.decode('latin-1').lower()
            if key in headers:
                invalid = True
            headers[key] = value.decode('latin-1')
        payload = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            payload.extend(event.get('body', b''))
            if len(payload) > 131072:
                status, result = self.error(413, 'REQUEST_TOO_LARGE')
                break
            if not event.get('more_body', False):
                try:
                    def unique(pairs):
                        values = {}
                        for key, value in pairs:
                            if key in values:
                                raise ValueError('duplicate JSON field')
                            values[key] = value
                        return values
                    query_pairs = parse_qsl(scope.get('query_string', b'').decode('utf-8'),
                                            keep_blank_values=True)
                    query = unique(query_pairs)
                    if invalid:
                        raise ValueError('duplicate header')
                    if scope['method'] == 'POST' and headers.get('content-type', '').split(';')[0] != 'application/json':
                        raise ValueError('JSON content type required')
                    body = json.loads(payload, object_pairs_hook=unique) if payload else None
                    status, result = self.dispatch(scope['method'], scope['path'],
                        scheme=scope.get('scheme', ''), headers=headers, body=body, query=query)
                except (ValueError, UnicodeError):
                    status, result = self.error(400, 'INVALID_HTTP_REQUEST')
                break
        raw = canonical_json(result).encode('utf-8')
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                                (b'strict-transport-security', b'max-age=31536000'),
                                (b'x-content-type-options', b'nosniff')]})
        await send({'type': 'http.response.body', 'body': raw})


def create_app(*, auth, inbox):
    return ControlApp(auth, inbox)
