"""CLI/configuration integration without starting external processes or feeds."""
import os
from pathlib import Path
import subprocess
import sys
import sqlite3

import pytest

from trading_system.server.config import Settings
from trading_system.contracts.resources import contract_path


def test_settings_require_loopback_and_explicit_origins():
    with pytest.raises(ValueError):
        Settings(host="0.0.0.0")
    with pytest.raises(ValueError):
        Settings(allowed_origins=("*",))
    with pytest.raises(ValueError):
        Settings(allowed_origins=("http://example.com",))
    with pytest.raises(ValueError):
        Settings(quote_max_age_seconds=0)
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://implicit-driver")


def test_module_cli_help_works_outside_repository(tmp_path):
    result = subprocess.run([sys.executable, "-m", "trading_system.server.cli", "--help"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    assert "serve" in result.stdout and "init-db" in result.stdout


def test_doctor_initializes_real_database_and_checks_build(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
    db = tmp_path / "console.sqlite3"
    env = {**os.environ, "AETHERTRADE_DATABASE_URL": "sqlite:///" + db.as_posix(),
           "AETHERTRADE_WEB_DIST": str(web), "AETHERTRADE_MARKET_ENABLED": "false"}
    result = subprocess.run([sys.executable, "-m", "trading_system.server.cli", "doctor"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert db.is_file() and "Browser build: ready" in result.stdout
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='console_model_config'").fetchone()
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='console_ai_jobs'").fetchone()
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='console_notifications'").fetchone()


def test_all_packaged_contracts_are_exact_checkout_copies():
    root = Path(__file__).resolve().parents[2]
    for source in (root / "contracts").iterdir():
        if source.suffix in {".json", ".yaml"}:
            packaged = root / "src/trading_system/schemas" / source.name
            assert packaged.read_bytes() == source.read_bytes()
            assert contract_path(source.name).read_bytes() == source.read_bytes()


def test_contract_lookup_rejects_parent_paths():
    with pytest.raises(ValueError):
        contract_path("../pyproject.toml")
