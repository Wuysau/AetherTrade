"""Read-only paper fixtures. No credentials or trading acceptance drivers."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def contract_fixtures(repo_root: Path) -> Path:
    return repo_root / "tests/fixtures/contracts"


@pytest.fixture
def paper_fixtures(contract_fixtures: Path) -> dict[str, dict]:
    return {
        path.name.removesuffix(".paper.json"): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((contract_fixtures / "examples").glob("*.paper.json"))
    }
