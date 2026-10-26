"""Launch the composed local console, without any import-time service startup."""
from __future__ import annotations

import argparse
from dataclasses import replace
import sys

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aethertrade", description="AetherTrade local PAPER console")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the API and built browser UI")
    serve.add_argument("--port", type=int, help="Loopback HTTP port (default 8787)")
    serve.add_argument("--no-market", action="store_true", help="Disable public quote polling")
    commands.add_parser("init-db", help="Initialize console-owned tables without resetting data")
    commands.add_parser("doctor", help="Check database connectivity and browser build")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
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
