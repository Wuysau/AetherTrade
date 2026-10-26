"""Offline candidate preflight, never a native runner or qualification authority.

Inputs are explicit metadata, NOT os.environ. No imports, file paths, commands,
credentials or runtime assertions supplied by a caller are executed or trusted.
Even complete metadata cannot establish build provenance or enable deployment.
"""
from __future__ import annotations

import json
import re


REVIEW_REF = "96ec148a8249eda918c9ee3219c7030755cabcf0"
CANDIDATE_FIELDS = (
    "review_ref", "deployment_ref", "wheel_digest", "container_digest",
    "python_abi", "os", "arch", "import_path",
)
ENV_FIELDS = (
    "python_abi", "os", "arch", "import_path", "isolation",
    "credentials_present", "paid_models_enabled", "host_role",
    "live_node_count", "web_constructs_engine", "native_risk_bypass",
    "flush_on_start", "submission_recovery_semantics",
)
REQUIRED_SETTINGS = {
    "native_risk_bypass": False,
    "flush_on_start": False,
    "submission_recovery_semantics": "RETAIN_UNRESOLVED",
}
CHECKS = (
    "exact_build_imports", "single_live_node_host", "default_overrides",
    "all_side_effect_paths_interceptable", "upgrade_id_protection_recovery",
    "old_state_migration",
)


def _metadata(value: object, fields: tuple[str, ...], label: str,
              errors: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected metadata object")
        return {}
    # Do not echo unknown keys or values: they may contain credentials.
    if set(value) - set(fields):
        errors.append(f"{label}: unsupported fields")
    result = {}
    for field in fields:
        item = value.get(field)
        if not isinstance(item, str) or not item or item != item.strip():
            errors.append(f"{label}.{field}: required nonempty string")
        elif len(item) > 256:
            errors.append(f"{label}.{field}: oversized metadata")
        else:
            result[field] = item
    return result


def probe(candidate: dict[str, str], env: dict[str, str]) -> dict[str, object]:
    """Validate declared offline constraints; native evidence stays NOT_RUN.

    wheel_digest and container_digest use sha256:<64 lowercase hex>. Refs
    must be full commit hashes; deployment_ref is never inferred from review_ref.
    Matching ABI/OS/path declarations are not an observed import or provenance.
    """
    errors: list[str] = []
    spec = _metadata(candidate, CANDIDATE_FIELDS, "candidate", errors)
    environment = _metadata(env, ENV_FIELDS, "env", errors)
    for field in ("review_ref", "deployment_ref"):
        if not re.fullmatch(r"[0-9a-f]{40}", spec.get(field, "")):
            errors.append(f"candidate.{field}: exact commit required")
    for field in ("wheel_digest", "container_digest"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.get(field, "")):
            errors.append(f"candidate.{field}: exact sha256 required")
    for field in ("python_abi", "os", "arch", "import_path"):
        if field not in spec or spec.get(field) != environment.get(field):
            errors.append(f"binding.{field}: missing or mismatched declaration")
    if not re.fullmatch(r"cp[0-9]{2,3}[a-z]*", spec.get("python_abi", "")):
        errors.append("candidate.python_abi: CPython ABI required")
    if spec.get("import_path") != "nautilus_trader":
        errors.append("candidate.import_path: expected nautilus_trader")
    if spec.get("arch") not in ("x86_64", "aarch64"):
        errors.append("candidate.arch: unsupported target")
    expected = {
        "os": "linux", "isolation": "isolated_linux",
        "credentials_present": "false", "paid_models_enabled": "false",
        "host_role": "execution-owner", "live_node_count": "1",
        "web_constructs_engine": "false", "native_risk_bypass": "false",
        "flush_on_start": "false",
        "submission_recovery_semantics": "RETAIN_UNRESOLVED",
    }
    for field, required in expected.items():
        if environment.get(field) != required:
            errors.append(f"env.{field}: required offline constraint not met")
    return {
        "report_version": 1,
        "mode": "OFFLINE_PREFLIGHT",
        "status": "BLOCKED_QUALIFICATION",
        "metadata_status": "INVALID" if errors else "COMPLETE_UNVERIFIED",
        "errors": errors,
        "live_enabled": False,
        "native_adapter_ready": False,
        "required_settings": dict(REQUIRED_SETTINGS),
        "observed_settings": None,
        "native_checks": {name: {"status": "NOT_RUN", "evidence": []}
                          for name in CHECKS},
        "blockers": ["EXACT_BUILD_NOT_OBSERVED", "NATIVE_RUNTIME_NOT_RUN",
                     "WRITE_PATH_COVERAGE_NOT_VERIFIED", "MIGRATION_NOT_RUN"],
        "migration_policy": "Preserve old state; no automatic migration or activation",
    }


def main() -> int:
    """Read only JSON on stdin, write one JSON report; never read host environment."""
    import sys

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or set(payload) != {"candidate", "env"}:
            raise ValueError("expected candidate and env")
        report = probe(payload["candidate"], payload["env"])
    except (ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "BLOCKED_QUALIFICATION", "live_enabled": False,
                          "error": "Invalid JSON input envelope"}))
        return 2
    print(json.dumps(report, sort_keys=True))
    # Nonzero deliberately prevents a preflight from acting as a deployment gate.
    return 2 if report["errors"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
