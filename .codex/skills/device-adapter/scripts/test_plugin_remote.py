#!/usr/bin/env python3
"""Execute evidence-driven four-layer remote plugin acceptance checks."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import time
from pathlib import Path

from plugin_common import ARTIFACTS, CONTEXTS, load_json, write_json


LAYERS = ("load", "instance", "capability", "functional", "multi_instance")
REQUIRED_SCENARIOS = (
    "config_missing", "config_invalid", "instance_mismatch",
    "connect_disconnect_reconnect", "slowpath", "fastpath", "fault_injection",
    "lifecycle_cleanup", "multi_instance", "delayed_reload", "soak",
)


def valid_check(check: object) -> bool:
    if not isinstance(check, dict):
        return False
    command = check.get("command")
    return (
        isinstance(command, str) and command.strip() not in {"", "true", ":"}
        and isinstance(check.get("evidence"), str) and bool(check.get("evidence"))
        and ("expect_contains" in check or "expect_exit_code" in check)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    args, _ = parser.parse_known_args()
    plan_path = CONTEXTS / f"{args.context_id}.deployment_plan.json"
    report_path = ARTIFACTS / f"{args.context_id}.remote_acceptance.json"
    if not plan_path.is_file():
        write_json(report_path, {"status": "BLOCKED", "error_code": "DEPLOYMENT_PLAN_MISSING", "path": str(plan_path)})
        return 17
    plan = load_json(plan_path)
    checks = plan.get("acceptance_checks") or {}
    scenarios = checks.get("scenarios") or {}
    ros = plan.get("ros_compatibility") or {}
    missing_ros = [
        key for key in ("domain_id", "localhost_only", "rmw_implementation", "hal_interface_version")
        if key not in ros or ros.get(key) == ""
    ]
    missing_layers = [layer for layer in LAYERS if not checks.get(layer)]
    missing_scenarios = [name for name in REQUIRED_SCENARIOS if not scenarios.get(name)]
    invalid_scenarios = [
        name for name in REQUIRED_SCENARIOS
        if scenarios.get(name) and not all(valid_check(item) for item in scenarios[name])
    ]
    if missing_layers or missing_ros or missing_scenarios or invalid_scenarios:
        write_json(report_path, {
            "status": "BLOCKED", "error_code": "ACCEPTANCE_CONTRACT_INCOMPLETE",
            "missing_layers": missing_layers, "missing_ros_compatibility": missing_ros,
            "missing_scenarios": missing_scenarios, "invalid_scenarios": invalid_scenarios,
        })
        return 17

    target = f"{args.user}@{args.host}"
    results: dict[str, list[dict[str, object]]] = {}
    passed = True
    execution_groups = [(layer, checks[layer]) for layer in LAYERS]
    execution_groups.extend((f"scenario:{name}", scenarios[name]) for name in REQUIRED_SCENARIOS)
    for layer, layer_checks in execution_groups:
        results[layer] = []
        for check in layer_checks:
            command = check.get("command") if isinstance(check, dict) else str(check)
            expected = str(check.get("expect_contains") or "") if isinstance(check, dict) else ""
            expected_exit = int(check.get("expect_exit_code", 0)) if isinstance(check, dict) else 0
            if not isinstance(command, str) or not command.strip():
                results[layer].append({"command": command, "exit_code": -1, "passed": False, "output": "empty acceptance command"})
                passed = False
                break
            start = time.monotonic()
            result = subprocess.run(
                ["ssh", target, "bash -lc " + shlex.quote(command)], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            output = result.stdout or ""
            ok = result.returncode == expected_exit and (not expected or expected in output)
            results[layer].append({
                "command": command, "exit_code": result.returncode,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "expected": expected, "expected_exit_code": expected_exit,
                "evidence": check.get("evidence", "") if isinstance(check, dict) else "",
                "passed": ok, "output": output[-8000:],
            })
            if not ok:
                passed = False
                break
        if not passed:
            break
    write_json(report_path, {
        "status": "PASS" if passed else "FAIL",
        "error_code": "" if passed else "REMOTE_PLUGIN_ACCEPTANCE_FAILED",
        "host": args.host, "layers": results,
        "success_definition": "four-layer runtime checks plus mandatory v4 config/lifecycle/fault/multi-instance/reload/soak scenarios",
    })
    load_checks = results.get("load", [])
    load_pass = bool(load_checks) and all(item.get("passed") is True for item in load_checks)
    load_result = {"status": "PASS" if load_pass else "FAIL", "checks": load_checks}
    write_json(ARTIFACTS / f"{args.context_id}.runtime_load_report.json", load_result)
    return 0 if passed else 18


if __name__ == "__main__":
    raise SystemExit(main())
