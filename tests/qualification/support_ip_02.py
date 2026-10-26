"""Synthetic metadata for OFFLINE validation tests; never native evidence."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def candidate_probe():
    path = Path(__file__).resolve().parents[2] / "qualification/nautilus/probe.py"
    spec = importlib.util.spec_from_file_location("ip02_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate():
    return {
        "review_ref": "96ec148a8249eda918c9ee3219c7030755cabcf0",
        "deployment_ref": "1" * 40,
        "wheel_digest": "sha256:" + "2" * 64,
        "container_digest": "sha256:" + "3" * 64,
        "python_abi": "cp312", "os": "linux", "arch": "x86_64",
        "import_path": "nautilus_trader",
    }


@pytest.fixture
def isolated_env(candidate):
    return {
        **{key: candidate[key] for key in ("python_abi", "os", "arch", "import_path")},
        "isolation": "isolated_linux", "credentials_present": "false",
        "paid_models_enabled": "false", "host_role": "execution-owner",
        "live_node_count": "1", "web_constructs_engine": "false",
        "native_risk_bypass": "false", "flush_on_start": "false",
        "submission_recovery_semantics": "RETAIN_UNRESOLVED",
    }
