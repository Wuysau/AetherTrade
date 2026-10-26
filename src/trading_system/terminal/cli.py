"""Terminal CLI entry point: same control API as the Web UI.

The CLI never reads exchange credentials and has no venue order path. It
resolves one revocable application session token (preferably from stdin) and
uses ``TerminalClient`` for every read/write. ``close`` is deliberately a local
UI action: it sends no command, stops no owner and exits no position.

Exit codes
----------
0   completed (a receipt is still not a fill or completed exit)
2   usage, missing token or rejected local input
3   denied / conflict / stale CAS
4   unavailable or write outcome unknown (query the original command)
130 interrupted
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable

from trading_system.contracts import types as t
from .client import (
    ControlToken,
    HttpsControlTransport,
    TerminalClient,
    TerminalConflict,
    TerminalDenied,
    TerminalError,
    TerminalInvalid,
    TerminalOutcomeUnknown,
    TerminalUnavailable,
    decode_cursor,
)
from .tui import (
    AccountExitConfirmation,
    ControlBinding,
    TerminalSession,
)


ENV_CONTROL_TOKEN = 'AETHERTRADE_CONTROL_TOKEN'
# Never read these. Their presence is not a supported terminal configuration.
FORBIDDEN_ENV = ('BINANCE_API_KEY', 'BINANCE_SECRET_KEY', 'BINANCE_API_SECRET',
                 'BINANCE_PRIVATE_KEY', 'BINANCE_SIGNATURE')
FORBIDDEN_OPTIONS = ('--api-key', '--api-secret', '--secret-key',
                     '--binance-key', '--binance-secret', '--signature')

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DENIED = 3
EXIT_UNKNOWN = 4
EXIT_INTERRUPTED = 130


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would print and SystemExit
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog='aethertrade-terminal',
                     description='AetherTrade terminal control client '
                                 '(no venue credentials).')
    parser.add_argument('--control-token-stdin', action='store_true',
                        help='read one revocable control token from stdin')
    parser.add_argument('--base-url', default=None,
                        help='HTTPS base URL of the control API')
    parser.add_argument('--environment', choices=[str(v) for v in t.Environment])
    parser.add_argument('--account', dest='venue_account_id')
    parser.add_argument('--product', choices=[str(v) for v in t.Product])
    parser.add_argument('--instrument', dest='instrument_uid')
    parser.add_argument('--leg', dest='venue_leg',
                        choices=['BOTH', 'LONG', 'SHORT'])
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('close', help='close this terminal view only')
    sub.add_parser('status', help='show positions, orders, locks and freshness')
    sub.add_parser('positions', help='show the position projection')
    sub.add_parser('orders', help='show the order projection')
    events = sub.add_parser('events', help='catch up the scoped event stream')
    events.add_argument('--cursor', default=None,
                        help='lossless cursor string returned by a prior run')

    stop = sub.add_parser('stop', help='submit one explicit control action')
    stop.add_argument('kind', choices=['pause-new', 'cancel-entries',
                                       'exit-owned'])
    stop.add_argument('--key', default=None,
                      help='stable Idempotency-Key (auto-generated if absent)')
    stop.add_argument('--expected-revision', type=int, default=None)
    stop.add_argument('--ownership-epoch', type=int, default=None)
    stop.add_argument('--include-external', action='store_true',
                      help='explicitly request an account exit that includes '
                           'manual/EXTERNAL positions')
    stop.add_argument('--confirm-external', action='store_true',
                      help='second confirmation for --include-external')
    return parser


def _scope_from_args(args) -> t.ScopeKey | None:
    values = (args.environment, args.venue_account_id, args.product,
              args.instrument_uid, args.venue_leg)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise _UsageError('all scope options are required together')
    try:
        return t.ScopeKey(environment=t.Environment(args.environment),
                          venue_account_id=args.venue_account_id,
                          product=t.Product(args.product),
                          instrument_uid=args.instrument_uid,
                          venue_leg=args.venue_leg)
    except ValueError as exc:
        raise _UsageError('invalid scope: ' + str(exc)) from exc


def _resolve_token(args, environ, stdin) -> str:
    if args.control_token_stdin:
        source = sys.stdin if stdin is None else stdin
        line = source.readline()
        value = line.rstrip('\r\n')
    else:
        source = os.environ if environ is None else environ
        value = source.get(ENV_CONTROL_TOKEN)
    if type(value) is not str or not value:
        raise _UsageError('a revocable control session token is required '
                          '(use --control-token-stdin or '
                          + ENV_CONTROL_TOKEN + ')')
    return value


def _make_client(args, token_value, *, client, build_client) -> TerminalClient:
    if client is not None:
        return client
    if build_client is not None:
        return build_client(token_value)
    scope = _scope_from_args(args)
    if scope is None:
        raise _UsageError('scope options are required when building a client')
    if not args.base_url:
        raise _UsageError('--base-url is required when building a client')
    try:
        transport = HttpsControlTransport(args.base_url)
        return TerminalClient(transport=transport,
                              token=ControlToken(value=token_value),
                              scope=scope)
    except ValueError as exc:
        raise _UsageError(str(exc)) from exc


def _check_client_scope(client: TerminalClient, args) -> None:
    requested = _scope_from_args(args)
    if requested is not None and requested != client.scope:
        raise TerminalDenied(403, 'AUTHORIZATION_SCOPE_MISMATCH')


def _binding(args) -> ControlBinding:
    if args.expected_revision is None or args.ownership_epoch is None:
        raise _UsageError('--expected-revision and --ownership-epoch are '
                          'required from the trusted session binding')
    return ControlBinding(expected_revision=args.expected_revision,
                          ownership_epoch=args.ownership_epoch)


def _print(out, message: str = '') -> None:
    print(message, file=out)


def _run(args, *, client, build_client, environ, stdin, out, err) -> int:
    if args.command == 'close':
        # No token, no client and no network are required to close a view.
        _print(out, 'UI closed. No control command was sent; the execution '
                    'owner, protection and positions are unaffected.')
        if client is not None:
            result = TerminalSession(client).close()
            for pending in result.pending_actions:
                _print(out, 'UNFINISHED action={} key={} outcome=UNKNOWN'.format(
                    pending.action, pending.key))
        return EXIT_OK

    # An injected client already carries its own revocable session token; do
    # not read the environment (or fail for a missing token) in that case.
    token_value = (None if client is not None
                   else _resolve_token(args, environ, stdin))
    client = _make_client(args, token_value, client=client,
                          build_client=build_client)
    _check_client_scope(client, args)
    session = TerminalSession(client)

    if args.command in ('status', 'positions', 'orders'):
        resources = {'status': ('positions', 'orders', 'risk-locks'),
                     'positions': ('positions',),
                     'orders': ('orders',)}[args.command]
        for resource in resources:
            if session.refresh(resource) is None:
                _print(err, 'read failed: ' + (session.last_error or 'UNKNOWN'))
                return EXIT_UNKNOWN
        _print(out, session.display())
        return EXIT_OK

    if args.command == 'events':
        cursor = decode_cursor(args.cursor) if args.cursor else None
        session.refresh_events(cursor)
        _print(out, session.display())
        if session.last_error:
            _print(err, 'event read failed: ' + session.last_error)
            return EXIT_UNKNOWN
        return EXIT_OK

    if args.command == 'stop':
        session.set_binding(_binding(args))
        confirmation = None
        if args.include_external:
            if not args.confirm_external:
                _print(err, 'ERROR EXTERNAL_EXIT_CONFIRMATION_REQUIRED: '
                            'account-level exit including manual/EXTERNAL '
                            'positions needs explicit confirmation')
                return EXIT_DENIED
            confirmation = AccountExitConfirmation(
                authorization_scope=(client.scope,), include_external=True)
        result = session.request_stop(
            args.kind, key=args.key, include_external=args.include_external,
            confirmation=confirmation)
        _print(out, 'PLAN {}: {}'.format(result.plan.action,
                                         result.plan.summary))
        for receipt in result.receipts:
            _print(out, 'RECEIPT command_id={} status={} reason={} '
                        'existing_result_ref={}'.format(
                            receipt.command_id, receipt.status,
                            receipt.reason or '-',
                            receipt.existing_result_ref or '-'))
        if result.external_positions_not_covered:
            _print(out, 'WARNING EXTERNAL not covered: ' + ','.join(
                result.external_positions_not_covered))
        for reason in result.incomplete_reasons:
            _print(out, 'INCOMPLETE ' + reason)
        if result.plan.action == 'exit_owned':
            _print(out, 'NOT FLAT: a queued receipt is not a completed exit.')
        else:
            _print(out, 'QUEUED: the control action is not yet applied.')
        return EXIT_OK

    raise _UsageError('unknown command')


def main(argv: list[str] | None = None, *,
         client: TerminalClient | None = None,
         build_client: Callable[[str], TerminalClient] | None = None,
         environ=None, stdin=None, stdout=None, stderr=None) -> int:
    """Run one terminal command. Never raises for expected control failures."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    for token in arguments:
        if token.split('=', 1)[0] in FORBIDDEN_OPTIONS:
            _print(err, 'REFUSED: the terminal does not accept exchange '
                        'credentials; use a revocable control session token')
            return EXIT_USAGE

    parser = _parser()
    try:
        args = parser.parse_args(arguments)
    except _UsageError as exc:
        _print(err, 'usage error: ' + str(exc))
        return EXIT_USAGE
    except SystemExit as exc:  # --help
        code = exc.code
        return int(code) if isinstance(code, int) else EXIT_OK

    try:
        return _run(args, client=client, build_client=build_client,
                    environ=environ, stdin=stdin, out=out, err=err)
    except _UsageError as exc:
        _print(err, 'usage error: ' + str(exc))
        return EXIT_USAGE
    except TerminalOutcomeUnknown as exc:
        _print(err, 'UNKNOWN outcome key={} ({}); query the original command '
                    'before retrying'.format(exc.key, exc.code))
        return EXIT_UNKNOWN
    except TerminalUnavailable as exc:
        _print(err, 'UNAVAILABLE {}; no success is claimed'.format(exc.code))
        return EXIT_UNKNOWN
    except (TerminalDenied, TerminalConflict) as exc:
        _print(err, 'DENIED {} ({})'.format(exc.code, exc.status))
        return EXIT_DENIED
    except TerminalInvalid as exc:
        _print(err, 'INVALID {} ({})'.format(exc.code, exc.status))
        return EXIT_USAGE
    except TerminalError as exc:
        _print(err, 'ERROR {} ({})'.format(exc.code, exc.status))
        return EXIT_UNKNOWN
    except KeyboardInterrupt:
        _print(err, 'interrupted; no success is claimed')
        return EXIT_INTERRUPTED


if __name__ == '__main__':  # pragma: no cover - module entry point
    raise SystemExit(main())
