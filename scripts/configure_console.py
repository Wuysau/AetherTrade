"""Create dedicated local PostgreSQL credentials without displaying them."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import secrets
import re
from urllib.parse import quote

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def configure(port: int) -> Path:
    if not 1 <= port <= 65535:
        raise ValueError("Invalid PostgreSQL port")
    folder = ROOT / ".runtime"
    folder.mkdir(exist_ok=True)
    target = folder / "postgres.env"
    existing = dotenv_values(target) if target.exists() else {}
    password = existing.get("AETHERTRADE_POSTGRES_PASSWORD") or secrets.token_urlsafe(32)
    # Keep the same Compose project and volume when an existing installation
    # moves between a feature worktree and the main checkout.
    instance = existing.get("AETHERTRADE_INSTANCE")
    if instance is not None and not re.fullmatch(r"[0-9a-f]{10}", instance):
        raise ValueError("Existing PostgreSQL instance is invalid; preserve and repair it explicitly")
    if instance is None:
        instance = sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()[:10]
    dsn = f"postgresql+psycopg://aethertrade:{quote(password, safe='')}@127.0.0.1:{port}/aethertrade"
    text = "\n".join((
        "# Generated local development database credentials. Do not commit.",
        f"AETHERTRADE_INSTANCE={instance}",
        f"AETHERTRADE_POSTGRES_PORT={port}",
        f"AETHERTRADE_POSTGRES_PASSWORD={password}",
        f"AETHERTRADE_DATABASE_URL={dsn}",
        "",
    ))
    if target.exists() and not existing.get("AETHERTRADE_POSTGRES_PASSWORD"):
        raise ValueError("Existing PostgreSQL settings have no password; preserve and repair them explicitly")
    target.write_text(text, encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=55432)
    args = parser.parse_args()
    print(configure(args.port))
