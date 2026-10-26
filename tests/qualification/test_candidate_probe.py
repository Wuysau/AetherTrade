"""Offline contract/negative tests only. These do not qualify T65 or native APIs."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from support_ip_02 import candidate, candidate_probe, isolated_env


ROOT = Path(__file__).resolve().parents[2]


def test_exact_build_imports(candidate_probe, candidate, isolated_env):
    report = candidate_probe.probe(candidate, isolated_env)
    assert report["metadata_status"] == "COMPLETE_UNVERIFIED"
    assert report["native_checks"]["exact_build_imports"] == {"status": "NOT_RUN", "evidence": []}
    assert report["native_adapter_ready"] is False
    assert report["live_enabled"] is False


@pytest.mark.parametrize("field", ["python_abi", "os", "arch", "import_path"])
def test_binding_mismatch(candidate_probe, candidate, isolated_env, field):
    isolated_env[field] = "mismatch"
    assert f"binding.{field}: missing or mismatched declaration" in candidate_probe.probe(candidate, isolated_env)["errors"]


@pytest.mark.parametrize("field", ["review_ref", "deployment_ref", "wheel_digest", "container_digest",
                                  "python_abi", "os", "arch", "import_path"])
@pytest.mark.parametrize("value", [None, "", "latest", " ", False, {}, []])
def test_invalid_candidate_metadata(candidate_probe, candidate, isolated_env, field, value):
    candidate[field] = value
    assert candidate_probe.probe(candidate, isolated_env)["metadata_status"] == "INVALID"


@pytest.mark.parametrize("field", ["host_role", "live_node_count", "web_constructs_engine"])
def test_single_live_node_host(candidate_probe, candidate, isolated_env, field):
    isolated_env[field] = {"host_role": "web", "live_node_count": "2", "web_constructs_engine": "true"}[field]
    report = candidate_probe.probe(candidate, isolated_env)
    assert report["errors"]
    assert report["native_checks"]["single_live_node_host"]["status"] == "NOT_RUN"


def test_default_overrides(candidate_probe, candidate, isolated_env):
    report = candidate_probe.probe(candidate, isolated_env)
    assert report["required_settings"] == {
        "native_risk_bypass": False, "flush_on_start": False,
        "submission_recovery_semantics": "RETAIN_UNRESOLVED"}
    assert report["observed_settings"] is None
    assert report["native_checks"]["default_overrides"]["status"] == "NOT_RUN"


@pytest.mark.parametrize("field,value", [
    ("native_risk_bypass", "true"), ("flush_on_start", "true"),
    ("submission_recovery_semantics", "RESOLVE_LOCALLY"),
    ("credentials_present", "true"), ("paid_models_enabled", "true"),
    ("isolation", "host"), ("os", "windows"),
])
def test_unsafe_declarations_rejected(candidate_probe, candidate, isolated_env, field, value):
    isolated_env[field] = value
    report = candidate_probe.probe(candidate, isolated_env)
    assert report["metadata_status"] == "INVALID"
    assert report["live_enabled"] is False


@pytest.mark.parametrize("side", ["candidate", "env"])
def test_every_required_field_is_required(candidate_probe, candidate, isolated_env, side):
    original = candidate if side == "candidate" else isolated_env
    for field in original:
        modified = dict(original)
        del modified[field]
        report = candidate_probe.probe(modified if side == "candidate" else candidate,
                                       modified if side == "env" else isolated_env)
        assert report["metadata_status"] == "INVALID", field


def test_all_side_effect_paths_interceptable(candidate_probe, candidate, isolated_env):
    inventory = json.loads((ROOT / "qualification/nautilus/write-path-inventory.json").read_text())
    paths = {row["path"] for row in inventory["paths"]}
    assert {"SUBMIT", "CANCEL", "AMEND", "CLOSE", "SET_PROTECTION", "REPLACE_PROTECTION",
            "ALGORITHM_CHILD", "ALGORITHM_CANCEL", "ALGORITHM_AMEND", "BATCH_SUBMIT",
            "CANCEL_ALL", "EMERGENCY_CLOSE", "RETRY_RESEND", "RECONCILIATION_WRITE",
            "ADAPTER_DIRECT", "STOCKS_WRITE"} <= paths
    assert len(paths) == len(inventory["paths"])
    assert inventory["unknown_path_policy"] == "DISABLED"
    for row in inventory["paths"]:
        assert row["enabled"] is False
        assert row["gate_hook"] is None
        assert row["disabled_reason"] and row["independent_command_required"]
        assert row["evidence"] == [] and row["status"] == "NOT_RUN"
    mapping = json.loads((ROOT / "qualification/nautilus/compatibility-map.json").read_text())
    assert {row["product_port"] for row in mapping["mappings"]} == paths
    for row in mapping["mappings"]:
        assert row["python_api"] is None and row["rust_api"] is None
        assert row["runtime_evidence"] == []
    report = candidate_probe.probe(candidate, isolated_env)
    assert report["native_checks"]["all_side_effect_paths_interceptable"]["status"] == "NOT_RUN"


def test_upgrade_does_not_unlock_or_migrate(candidate_probe, candidate, isolated_env):
    before = copy.deepcopy((candidate, isolated_env))
    first = candidate_probe.probe(candidate, isolated_env)
    assert (candidate, isolated_env) == before
    candidate["deployment_ref"] = "4" * 40
    candidate["wheel_digest"] = "sha256:" + "5" * 64
    upgraded = candidate_probe.probe(candidate, isolated_env)
    for report in (first, upgraded):
        assert report["status"] == "BLOCKED_QUALIFICATION"
        assert report["live_enabled"] is False
        assert all(check["status"] == "NOT_RUN" for check in report["native_checks"].values())
        assert "Preserve old state" in report["migration_policy"]


def test_unknown_fields_and_secrets_never_echo(candidate_probe, candidate, isolated_env):
    isolated_env["API_SECRET"] = "synthetic-secret-do-not-echo"
    candidate["evidence"] = "PASS"
    report = candidate_probe.probe(candidate, isolated_env)
    serialized = json.dumps(report)
    assert "synthetic-secret" not in serialized and "API_SECRET" not in serialized
    assert report["metadata_status"] == "INVALID"


@pytest.mark.parametrize("value", [None, [], "input", 1, True])
def test_malformed_objects_fail_closed(candidate_probe, value):
    assert candidate_probe.probe(value, value)["metadata_status"] == "INVALID"


@pytest.mark.parametrize("payload", ["{", "[]", '{"candidate": {}}'])
def test_cli_invalid_input(payload):
    result = subprocess.run([sys.executable, str(ROOT / "qualification/nautilus/probe.py")],
                            input=payload, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert json.loads(result.stdout)["live_enabled"] is False


def test_cli_complete_metadata_still_blocks(candidate, isolated_env):
    result = subprocess.run([sys.executable, str(ROOT / "qualification/nautilus/probe.py")],
                            input=json.dumps({"candidate": candidate, "env": isolated_env}),
                            text=True, capture_output=True, check=False)
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "BLOCKED_QUALIFICATION"
