#!/usr/bin/env python3
"""Create an isolated HAL service/client pair and run remote acceptance."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from plugin_common import ARTIFACTS, CONTEXTS, load_contract, load_json, write_json


LAYERS = ("load", "instance", "capability", "functional", "multi_instance")
REQUIRED_SCENARIOS = (
    "config_missing", "config_invalid", "instance_mismatch",
    "connect_disconnect_reconnect", "slowpath", "fastpath", "fault_injection",
    "lifecycle_cleanup", "multi_instance", "delayed_reload", "soak",
)
RUNTIME_FIELDS = ("project_dir", "manager_command", "deployment_file")


def valid_check(check: object) -> bool:
    if not isinstance(check, dict):
        return False
    if check.get("status") in {"NOT_RUN", "BLOCKED"}:
        return bool(check.get("reason") and check.get("evidence"))
    command = check.get("command")
    return (
        isinstance(command, str) and command.strip() not in {"", "true", ":"}
        and check.get("executor") in {"host", "client"}
        and isinstance(check.get("evidence"), str) and bool(check.get("evidence"))
        and ("expect_contains" in check or "expect_exit_code" in check)
    )


def run_local(command: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=timeout,
        )
        return result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return 124, str(output)


def ssh_run(target: str, command: str, timeout: int) -> tuple[int, str]:
    return run_local(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         target, "bash -lc " + shlex.quote(command)],
        timeout,
    )


def env_args(ros: dict[str, Any]) -> list[str]:
    return [
        "-e", f"ROS_DOMAIN_ID={ros['domain_id']}",
        "-e", f"ROS_LOCALHOST_ONLY={ros['localhost_only']}",
        "-e", f"RMW_IMPLEMENTATION={ros['rmw_implementation']}",
    ]


def docker_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--project-dir")
    parser.add_argument("--deployment-file")
    parser.add_argument("--manager-command")
    parser.add_argument("--image")
    parser.add_argument("--domain-id", type=int)
    parser.add_argument("--rmw-implementation")
    args, _ = parser.parse_known_args()
    plan_path = CONTEXTS / f"{args.context_id}.deployment_plan.json"
    report_path = ARTIFACTS / f"{args.context_id}.remote_acceptance.json"
    log_path = ARTIFACTS / "logs" / f"{args.context_id}_service_container.log"
    if not plan_path.is_file():
        write_json(report_path, {"status": "BLOCKED", "error_code": "DEPLOYMENT_PLAN_MISSING", "path": str(plan_path)})
        return 17

    plan = load_json(plan_path)
    contract = load_contract(args.context_id)
    checks = plan.get("acceptance_checks") or {}
    scenarios = checks.get("scenarios") or {}
    ros = plan.get("ros_compatibility") or {}
    runtime_test = plan.get("runtime_test") or {}
    runtime_overrides = {
        "project_dir": args.project_dir,
        "deployment_file": args.deployment_file,
        "manager_command": args.manager_command,
        "image": args.image,
    }
    runtime_overrides = {key: value for key, value in runtime_overrides.items() if value is not None}
    runtime_test = {**runtime_test, **runtime_overrides}
    ros_overrides: dict[str, object] = {}
    if args.domain_id is not None:
        ros_overrides["domain_id"] = args.domain_id
    if args.rmw_implementation is not None:
        ros_overrides["rmw_implementation"] = args.rmw_implementation
    ros = {**ros, **ros_overrides}
    missing_ros = [
        key for key in ("domain_id", "localhost_only", "rmw_implementation", "hal_interface_version")
        if key not in ros or ros.get(key) == ""
    ]
    missing_runtime = [key for key in RUNTIME_FIELDS if not runtime_test.get(key)]
    if runtime_test.get("instance_count") != 1:
        missing_runtime.append("instance_count=1")
    missing_layers = [layer for layer in LAYERS if not checks.get(layer)]
    invalid_layers = [
        layer for layer in LAYERS
        if checks.get(layer) and not all(valid_check(item) for item in checks[layer])
    ]
    missing_scenarios = [name for name in REQUIRED_SCENARIOS if not scenarios.get(name)]
    invalid_scenarios = [
        name for name in REQUIRED_SCENARIOS
        if scenarios.get(name) and not all(valid_check(item) for item in scenarios[name])
    ]
    cleanup_policy = str(runtime_test.get("cleanup_policy") or "keep_on_failure")
    if cleanup_policy not in {"always", "keep_on_failure", "never"}:
        missing_runtime.append("cleanup_policy(always|keep_on_failure|never)")
    if missing_layers or invalid_layers or missing_ros or missing_runtime or missing_scenarios or invalid_scenarios:
        write_json(report_path, {
            "status": "BLOCKED", "error_code": "ACCEPTANCE_CONTRACT_INCOMPLETE",
            "missing_layers": missing_layers, "invalid_layers": invalid_layers,
            "missing_ros_compatibility": missing_ros, "missing_runtime_test": missing_runtime,
            "missing_scenarios": missing_scenarios, "invalid_scenarios": invalid_scenarios,
        })
        return 17

    adapter = str(contract["adapter_type"])
    if runtime_test.get("enabled_adapter_types") != [adapter]:
        missing_runtime.append(f"enabled_adapter_types=[{adapter}]")
    if missing_runtime:
        write_json(report_path, {
            "status": "BLOCKED", "error_code": "SINGLE_DEVICE_RUNTIME_REQUIRED",
            "missing_runtime_test": missing_runtime,
        })
        return 17
    safe_id = re.sub(r"[^a-z0-9_.-]+", "-", args.context_id.lower())
    run_suffix = str(int(time.time()))
    service_name = f"hal_{safe_id}_service_{run_suffix}"
    client_name = f"hal_{safe_id}_client_{run_suffix}"
    image = str(runtime_test.get("image") or contract["runtime_image"])
    project_dir = str(runtime_test["project_dir"])
    project_mount = str(runtime_test.get("project_mount") or "/workspace/yunshu")
    runtime_root = str(runtime_test.get("runtime_root") or "/etc/vega/access/runtime")
    runtime_mount = str(runtime_test.get("runtime_mount") or "/hal-runtime")
    deployment_file = str(runtime_test["deployment_file"])
    manager_command = str(runtime_test["manager_command"])
    ros_setup = str(runtime_test.get("ros_setup") or "/opt/ros/humble/setup.bash")
    workspace_setup = str(runtime_test.get("workspace_setup") or f"{project_mount}/install/setup.bash")
    target = f"{args.user}@{args.host}"
    startup_timeout = int(runtime_test.get("startup_timeout_sec") or 60)
    command_timeout = max(1, args.timeout)
    results: dict[str, Any] = {
        "runtime_prepare": [],
        "runtime_overrides": {**runtime_overrides, **ros_overrides},
        "effective_runtime_test": runtime_test,
        "effective_ros_compatibility": ros,
    }

    preflight = (
        f"test -f {shlex.quote(project_dir + '/install/setup.bash')} && "
        f"test -f {shlex.quote(deployment_file)} && "
        f"docker image inspect {shlex.quote(image)} >/dev/null"
    )
    rc, output = ssh_run(target, preflight, command_timeout)
    results["runtime_prepare"].append({"step": "preflight", "exit_code": rc, "output": output[-8000:]})
    if rc:
        write_json(report_path, {
            "status": "BLOCKED", "error_code": "REMOTE_RUNTIME_INPUT_MISSING",
            "host": args.host, "service_container": service_name, "layers": results,
        })
        return 17

    service_parts = [
        "docker", "run", "-d", "--name", service_name,
        "--network", "host", "--ipc", "host",
        *env_args(ros),
        "-v", f"{project_dir}:{project_mount}:ro",
        "-v", f"{runtime_root}:{runtime_mount}:ro",
    ]
    if runtime_test.get("privileged", True):
        service_parts.append("--privileged")
    for mount in runtime_test.get("extra_mounts") or []:
        if isinstance(mount, str) and mount:
            service_parts.extend(("-v", mount))
    service_shell = (
        "set -euo pipefail; "
        f"source {shlex.quote(ros_setup)}; source {shlex.quote(workspace_setup)}; "
        f"exec {manager_command}"
    )
    service_parts.extend((image, "bash", "-lc", service_shell))
    service_command = docker_command(service_parts)
    rc, output = ssh_run(target, service_command, command_timeout)
    results["runtime_prepare"].append({"step": "docker run -d", "command": service_command, "exit_code": rc, "output": output[-8000:]})
    if rc == 0:
        inspect_command = (
            f"deadline=$((SECONDS+{startup_timeout})); "
            f"while [ $SECONDS -lt $deadline ]; do "
            f"[ \"$(docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(service_name)} 2>/dev/null)\" = true ] && break; sleep 1; done; "
            f"docker inspect -f 'network={{{{.HostConfig.NetworkMode}}}} ipc={{{{.HostConfig.IpcMode}}}}' {shlex.quote(service_name)} "
            "| grep -qx 'network=host ipc=host'; "
            f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {shlex.quote(service_name)} "
            f"| grep -Fx {shlex.quote('ROS_DOMAIN_ID=' + str(ros['domain_id']))}; "
            f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {shlex.quote(service_name)} "
            f"| grep -Fx {shlex.quote('ROS_LOCALHOST_ONLY=' + str(ros['localhost_only']))}; "
            f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {shlex.quote(service_name)} "
            f"| grep -Fx {shlex.quote('RMW_IMPLEMENTATION=' + str(ros['rmw_implementation']))}"
        )
        rc, output = ssh_run(target, inspect_command, startup_timeout + 10)
        results["runtime_prepare"].append({"step": "network_ipc_check", "exit_code": rc, "output": output[-8000:]})

    passed = rc == 0
    blocked_checks: list[dict[str, Any]] = []
    execution_groups = [(layer, checks[layer]) for layer in LAYERS]
    execution_groups.extend((f"scenario:{name}", scenarios[name]) for name in REQUIRED_SCENARIOS)
    for group, group_checks in execution_groups:
        if not passed:
            break
        results[group] = []
        for check in group_checks:
            if check.get("status") in {"NOT_RUN", "BLOCKED"}:
                skipped = {
                    "status": str(check["status"]), "reason": str(check["reason"]),
                    "evidence": str(check["evidence"]), "passed": False,
                }
                results[group].append(skipped)
                blocked_checks.append({"group": group, **skipped})
                continue
            command = str(check["command"])
            command = command.replace("{service_container}", service_name)
            expected = str(check.get("expect_contains") or "")
            expected_exit = int(check.get("expect_exit_code", 0))
            timeout = int(check.get("timeout_sec") or command_timeout)
            if check["executor"] == "client":
                client_shell = (
                    "set -euo pipefail; "
                    f"source {shlex.quote(ros_setup)}; source {shlex.quote(workspace_setup)}; "
                    "export ROS2CLI_NO_DAEMON=1; " + command
                )
                remote_command = docker_command([
                    "docker", "run", "--rm", "--name", client_name,
                    "--network", "host", "--ipc", "host", *env_args(ros),
                    "-e", "ROS2CLI_NO_DAEMON=1",
                    "-v", f"{project_dir}:{project_mount}:ro",
                    image, "bash", "-lc", client_shell,
                ])
            else:
                remote_command = command
            start = time.monotonic()
            exit_code, command_output = ssh_run(target, remote_command, timeout)
            ok = exit_code == expected_exit and (not expected or expected in command_output)
            results[group].append({
                "executor": check["executor"], "command": command,
                "remote_command": remote_command, "exit_code": exit_code,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "expected": expected, "expected_exit_code": expected_exit,
                "evidence": check["evidence"], "passed": ok,
                "output": command_output[-8000:],
            })
            if not ok:
                passed = False
                break

    ssh_run(target, f"docker rm -f {shlex.quote(client_name)} >/dev/null 2>&1 || true", command_timeout)
    logs_rc, service_logs = ssh_run(
        target, f"docker logs {shlex.quote(service_name)} 2>&1", command_timeout
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(service_logs, encoding="utf-8")
    results["log_collection"] = {
        "command": f"docker logs {service_name}", "exit_code": logs_rc,
        "local_file": str(log_path), "bytes": len(service_logs.encode("utf-8")),
    }

    remove_service = cleanup_policy == "always" or (
        cleanup_policy == "keep_on_failure" and passed and not blocked_checks
    )
    cleanup_result: dict[str, Any] = {"policy": cleanup_policy, "removed": False}
    if remove_service:
        cleanup_rc, cleanup_output = ssh_run(
            target, f"docker rm -f {shlex.quote(service_name)}", command_timeout
        )
        cleanup_result.update({"removed": cleanup_rc == 0, "exit_code": cleanup_rc, "output": cleanup_output[-2000:]})
        if cleanup_rc:
            passed = False
    results["cleanup"] = cleanup_result

    final_status = "FAIL" if not passed else ("BLOCKED" if blocked_checks else "PASS")
    write_json(report_path, {
        "schema_version": "1.0", "context_id": args.context_id,
        "status": final_status,
        "error_code": "" if final_status == "PASS" else (
            "REMOTE_ACCEPTANCE_NOT_RUN" if final_status == "BLOCKED" else "REMOTE_PLUGIN_ACCEPTANCE_FAILED"
        ),
        "host": args.host, "adapter_type": adapter, "runtime_image": image,
        "service_container": service_name, "client_container": client_name,
        "deployment_file": deployment_file, "layers": results,
        "blocked_checks": blocked_checks,
        "success_definition": "fresh single-device service container plus isolated ROS client and mandatory v4 acceptance",
    })
    load_checks = results.get("load", [])
    load_pass = bool(load_checks) and all(item.get("passed") is True for item in load_checks)
    write_json(ARTIFACTS / f"{args.context_id}.runtime_load_report.json", {
        "status": "PASS" if load_pass else "FAIL", "checks": load_checks,
        "service_container": service_name,
    })
    return 0 if final_status == "PASS" else (17 if final_status == "BLOCKED" else 18)


if __name__ == "__main__":
    raise SystemExit(main())
