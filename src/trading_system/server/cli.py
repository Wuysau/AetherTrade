"""Launch the composed local console, without any import-time service startup."""
from __future__ import annotations

import argparse
from dataclasses import replace
import sys
import json
import os
from pathlib import Path

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aethertrade", description="AetherTrade local PAPER console")
    parser.add_argument('--paper-config', type=Path, help='Explicit isolated PAPER JSON configuration (contains secrets)')
    parser.add_argument('--resolver-profile', type=Path, help='Separate trusted review profile; never loaded by serve/paper-run')
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the API and built browser UI")
    serve.add_argument("--port", type=int, help="Loopback HTTP port (default 8787)")
    serve.add_argument("--no-market", action="store_true", help="Disable public quote streams and polling")
    commands.add_parser("init-db", help="Initialize console-owned tables without resetting data")
    commands.add_parser("doctor", help="Check database connectivity and browser build")
    provision = commands.add_parser('paper-provision', help='Create fresh isolated virtual PAPER databases; never migrates existing accounts')
    provision.add_argument('--isolated-paper', action='store_true', required=True)
    provision.add_argument('--initial-cash', default='100000')
    provision.add_argument('--output', type=Path, required=True)
    commands.add_parser('paper-status', help='Read the same durable PAPER projection as the browser')
    daily = commands.add_parser('paper-daily-review', help='Explicit next-risk-day flat-account review')
    daily.add_argument('lock_id')
    daily.add_argument('--operator-review-id', required=True)
    commands.add_parser('paper-run', help='Run the independent local PAPER execution/reconciliation worker')
    for operation in ('dispatch', 'reconcile', 'cancel', 'protect', 'recover'):
        command = commands.add_parser('paper-' + operation, help='Local synthetic PAPER ' + operation)
        command.add_argument('command_id')
        if operation in ('cancel', 'protect'):
            command.add_argument('--key', required=True)
    event = commands.add_parser('paper-event', help='Explicitly inject a virtual venue event; never a real venue')
    event.add_argument('command_id')
    event.add_argument('--event-id', required=True)
    event.add_argument('--status', required=True, choices=('SUBMITTED', 'PARTIALLY_FILLED', 'FILLED',
        'CANCEL_UNKNOWN', 'CANCELED', 'EXPIRED', 'REJECTED', 'PROTECTION_VERIFIED', 'PROTECTION_FAILED'))
    event.add_argument('--cumulative', required=True)
    event.add_argument('--price')
    args = parser.parse_args(argv)
    try:
        if args.command == 'paper-provision':
            from .paper_provision import provision
            admin = os.environ.get('PAPER_RUNTIME_ADMIN_DSN')
            if not admin:
                raise ValueError('PAPER_RUNTIME_ADMIN_DSN is required only for explicit provisioning')
            provision(admin, initial_cash=args.initial_cash,
                journal_directory=args.output.resolve().parent / 'paper-owner',
                output_path=args.output, isolated_paper=args.isolated_paper)
            print('Fresh virtual PAPER databases created. Secret configuration: ' + str(args.output.resolve()))
            print('Set up the new console using the SAME initial cash: ' + args.initial_cash)
            return 0
        settings = Settings.from_env()
        if args.paper_config:
            config = json.loads(args.paper_config.read_text(encoding='utf-8'))
            settings = replace(settings, database_url=config['database_url'],
                paper_ledger_dsn=config['ledger_dsn'], paper_venue_dsn=config['venue_dsn'],
                paper_event_dsn=config['event_dsn'], paper_journal_directory=Path(config['journal_directory']))
        if args.command.startswith('paper-'):
            if not settings.paper_ledger_dsn:
                raise ValueError('Explicit unified PAPER configuration is required')
            from .paper_runtime import UnifiedPaperRuntime, attach_console_runtime
            from .store import Store
            from trading_system.storage.models import decode
            from .paper_runtime import RuntimeRequest
            runtime = UnifiedPaperRuntime(ledger_dsn=settings.paper_ledger_dsn,
                venue_dsn=settings.paper_venue_dsn, event_dsn=settings.paper_event_dsn,
                journal_directory=settings.paper_journal_directory)
            store = Store(settings.database_url)
            store.initialize()
            def saved_quotes():
                with runtime.connect() as connection:
                    records = connection.execute("SELECT DISTINCT ON (payload->>'symbol') payload FROM ledger.audit_events "
                        "WHERE record_id LIKE 'console:request:%%' AND sequence=1 ORDER BY payload->>'symbol',processed_at DESC").fetchall()
                requests = [decode(RuntimeRequest, raw) for raw, in records]
                return {'status': 'PARTIAL', 'quotes': [dict(symbol=r.symbol, price=r.quote_price,
                    as_of=r.quote_at, stale=False) for r in requests]}
            attach_console_runtime(store, runtime, saved_quotes)
            try:
                if args.command == 'paper-run':
                    from .paper_maintenance import run_owner
                    print('Independent local PAPER owner running; explicit virtual events only.', flush=True)
                    run_owner(runtime, report=lambda value: print(value, flush=True))
                if args.command == 'paper-daily-review':
                    if not args.resolver_profile:
                        raise ValueError('A separate trusted resolver profile is required')
                    from .paper_maintenance import review_daily
                    reviewer = json.loads(args.resolver_profile.read_text(encoding='utf-8'))
                    result = review_daily(runtime, args.lock_id, resolver_dsn=reviewer['resolver_dsn'],
                        operator_review_id=args.operator_review_id)
                elif args.command == 'paper-status':
                    result = runtime.projection()
                elif args.command == 'paper-event':
                    result = runtime.inject(args.command_id, dict(event_id=args.event_id, status=args.status,
                        cumulative_quantity=args.cumulative, price=args.price))
                elif args.command in ('paper-cancel', 'paper-protect'):
                    result = getattr(runtime, args.command.removeprefix('paper-'))(args.command_id, args.key)
                elif args.command == 'paper-recover':
                    if not args.resolver_profile:
                        raise ValueError('A separate trusted resolver profile is required')
                    reviewer = json.loads(args.resolver_profile.read_text(encoding='utf-8'))
                    result = runtime.recover(args.command_id, resolver_dsn=reviewer['resolver_dsn'])
                else:
                    result = getattr(runtime, args.command.removeprefix('paper-'))(args.command_id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            finally:
                store.close()
        if args.command == "serve":
            if args.port:
                settings = replace(settings, port=args.port,
                    allowed_origins=tuple(dict.fromkeys((*settings.allowed_origins,
                        f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"))))
            if args.no_market:
                settings = replace(settings, market_enabled=False)
            from .app import create_app
            import uvicorn
            backend = "PostgreSQL" if settings.database_url.startswith("postgresql") else "SQLite (local mode)"
            print(f"AetherTrade PAPER | {backend} | http://{settings.host}:{settings.port}", flush=True)
            if not (settings.web_dist / "index.html").is_file():
                print("Browser build missing. Run: npm --prefix web run build", file=sys.stderr)
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                        proxy_headers=False, server_header=False, log_level="info")
            return 0
        from .store import Store
        from . import model_repository  # Register additive model tables for init-db/doctor too.
        from . import automation  # Register schedules/jobs/notifications without starting a worker.
        from . import market_config  # Register additive source settings, without connecting to a provider.
        store = Store(settings.database_url)
        try:
            store.initialize()
            print("Database: ready (console tables initialized; existing data retained)")
            if args.command == "doctor":
                built = (settings.web_dist / "index.html").is_file()
                print("Browser build: " + ("ready" if built else "missing; run npm --prefix web run build"))
                return 0 if built else 1
        finally:
            store.close()
        return 0
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # SQL errors may contain DSNs and parameters. Print the error type only.
        print(f"Startup failed ({type(exc).__name__}). Check .env.runtime and database availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
