#!/usr/bin/env python3
"""Generate an evidence-backed single-device deployment and acceptance plan."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Any

from plugin_common import CONTEXTS, load_contract, load_json, write_json


LAYERS = ("load", "instance", "capability", "functional", "multi_instance")
SCENARIOS = (
    "config_missing", "config_invalid", "instance_mismatch",
    "connect_disconnect_reconnect", "slowpath", "fastpath",
    "fault_injection", "lifecycle_cleanup", "multi_instance",
    "delayed_reload", "soak",
)
HAL_UUID_NAMESPACE = uuid.UUID("87f3a100-1a2b-4c3d-8e5f-6789abcdef01")


def executable(command: str, evidence: str, expect_contains: str = "", expect_exit_code: int = 0,
               executor: str = "client", timeout_sec: int = 120) -> dict[str, Any]:
    value: dict[str, Any] = {
        "status": "EXECUTABLE", "executor": executor, "command": command,
        "expect_exit_code": expect_exit_code, "evidence": evidence, "timeout_sec": timeout_sec,
    }
    if expect_contains:
        value["expect_contains"] = expect_contains
    return value


def not_run(reason: str, evidence: str) -> dict[str, str]:
    return {"status": "NOT_RUN", "reason": reason, "evidence": evidence}


def valid_check(value: object) -> bool:
    if isinstance(value, dict) and value.get("status") in {"NOT_RUN", "BLOCKED"}:
        return bool(value.get("reason") and value.get("evidence"))
    return bool(
        isinstance(value, dict)
        and value.get("executor") in {"host", "client"}
        and isinstance(value.get("command"), str)
        and value["command"].strip() not in {"", "true", ":"}
        and isinstance(value.get("evidence"), str)
        and value["evidence"].strip()
        and ("expect_contains" in value or "expect_exit_code" in value)
    )


def generated_checks(adapter: str, refs: list[str], topic_prefix: str, manager_ns: str) -> dict[str, Any]:
    def get_property(group: str, prop: str = "") -> str:
        return (
            f"ros2 service call {topic_prefix}/get_property hal_interface/srv/GetProperty "
            f"\"{{group_id: '{group}', property_id: '{prop}'}}\""
        )

    camera_topic = f"{topic_prefix}/camera/image_frame"
    imu_topic = f"{topic_prefix}/imu/imu_data"
    capability = []
    functional = []
    if "camera" in refs:
        capability.append(executable(get_property("camera"), "camera SlowPath response", "success: true"))
        functional.append(executable(
            f"timeout 20 ros2 topic echo --once {camera_topic} sensor_msgs/msg/CompressedImage",
            "one real color frame on the HAL FastPath", timeout_sec=25))
    if "imu" in refs:
        capability.append(executable(get_property("imu"), "imu SlowPath response", "success: true"))
        functional.append(executable(
            f"timeout 20 ros2 topic echo --once {imu_topic} hal_interface/msg/ImuData",
            "one real IMU sample on the HAL FastPath", timeout_sec=25))
    if not capability:
        capability.append(not_run("no testable capability reference is declared", "capability mapping"))
    if not functional:
        functional.append(not_run("no generated FastPath check is available", "functional mapping"))

    first_group = refs[0] if refs else "device"
    lifecycle = (
        f"ros2 service call {manager_ns}/device_life_control hal_interface/srv/DeviceLifeControl "
        f"\"{{device_id: '{adapter}_0', action: 0, config_json: ''}}\" && "
        f"ros2 service call {manager_ns}/device_life_control hal_interface/srv/DeviceLifeControl "
        f"\"{{device_id: '{adapter}_0', action: 1, config_json: ''}}\""
    )
    soak_topic = camera_topic if "camera" in refs else imu_topic
    return {
        "load": [executable(
            f"ros2 service list | grep -Fx {topic_prefix}/get_property",
            "plugin ABI load and Adapter creation expose the device service", topic_prefix)],
        "instance": [executable(
            get_property(first_group), f"{adapter}_0 responds on its deterministic topic prefix", "success: true")],
        "capability": capability,
        "functional": functional,
        "multi_instance": [not_run(
            "requires two simultaneously connected physical devices with distinct serial numbers",
            "two-real-device isolation remains a delivery gate")],
        "scenarios": {
            "config_missing": [not_run("requires an isolated negative-start container with config hidden", "negative config test")],
            "config_invalid": [not_run("requires an isolated negative-start container with malformed config", "negative config test")],
            "instance_mismatch": [not_run("requires an isolated config with a missing instance_index", "instance selection test")],
            "connect_disconnect_reconnect": [not_run("requires controlled physical disconnect/reconnect", "hardware reconnect test")],
            "slowpath": capability[:1],
            "fastpath": functional[:1],
            "fault_injection": [not_run("requires a declared SDK/backend fault injection mechanism", "fault injection test")],
            "lifecycle_cleanup": [executable(lifecycle, "manager stop/start lifecycle transition", "success: true")],
            "multi_instance": [not_run("requires two physical devices", "multi-instance hardware test")],
            "delayed_reload": [not_run("requires controlled delayed plugin/device appearance", "Registry refresh test")],
            "soak": [executable(
                f"timeout 60 ros2 topic hz {soak_topic}", "60-second FastPath rate evidence",
                "average rate", 124, timeout_sec=70)],
        },
    }


def write_deployment(path: Path, adapter: str, device_uuid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "deployment:\n"
        "  version: \"1.0\"\n"
        "  network_mode: \"standalone\"\n"
        "  namespace: \"/hal/device\"\n"
        f"  manager_primary_device_local_id: \"{adapter}_0\"\n"
        "  adapter_plugin_dir: \"/hal-runtime/adapters\"\n"
        "  heartbeat_timeout_sec: 5.0\n"
        "  reconnect:\n"
        "    max_attempts: 3\n"
        "    interval_sec: 2.0\n"
        "  devices:\n"
        f"    - adapter_type: {adapter}\n"
        f"      local_id: \"{adapter}_0\"\n"
        f"      device_uuid: \"{device_uuid}\"\n"
        "      instance_index: 0\n"
        f"      device_name: \"{adapter}\"\n"
        "      enabled: true\n"
        "      connection:\n"
        "        protocol: sdk_auto_discovery\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--project-dir")
    args = parser.parse_args()

    contract = load_contract(args.context_id)
    spec = load_json(CONTEXTS / f"{args.context_id}.device_spec.json")
    path = CONTEXTS / f"{args.context_id}.deployment_plan.json"
    existing = load_json(path) if path.is_file() else {}
    target = dict(contract.get("target_build") or {})
    runtime = dict(existing.get("runtime_test") or {})
    ros = dict(existing.get("ros_compatibility") or {})
    checks = dict(existing.get("acceptance_checks") or {})
    adapter = str(contract.get("adapter_type") or spec.get("adapter_type") or args.context_id)
    refs = [str(value) for value in contract.get("capability_group_refs") or []]
    device_uuid = str(uuid.uuid5(HAL_UUID_NAMESPACE, f"{adapter}_0"))
    slug = "u_" + device_uuid.replace("-", "")
    topic_prefix = f"/hal/device/{slug}"
    manager_ns = f"/hal/manager/{slug}"
    deployment_yaml = CONTEXTS / f"{args.context_id}.deployment.yaml"
    write_deployment(deployment_yaml, adapter, device_uuid)

    runtime.setdefault("image", contract.get("runtime_image") or "")
    if args.project_dir:
        runtime["project_dir"] = args.project_dir
    elif not runtime.get("project_dir"):
        runtime["project_dir"] = target.get("runtime_project_dir") or ""
    runtime.setdefault("project_mount", "/workspace/yunshu")
    runtime.setdefault("runtime_root", target.get("runtime_root") or "/etc/vega/access/runtime")
    runtime.setdefault("runtime_mount", "/hal-runtime")
    if not runtime.get("deployment_file"):
        runtime["deployment_file"] = target.get("deployment_file") or f"/etc/vega/access/runtime/deployment/{adapter}-only.yaml"
    if not runtime.get("manager_command"):
        runtime["manager_command"] = target.get("manager_command") or (
            "ros2 launch hardware_abstraction_layer manager_node.launch.py "
            f"deployment_config:=/hal-runtime/deployment/{adapter}-only.yaml "
            "model_base_dir:=/hal-runtime/model plugin_dir:=/hal-runtime/adapters"
        )
    runtime.setdefault("ros_setup", f"/opt/ros/{target.get('ros_distro') or 'humble'}/setup.bash")
    runtime.setdefault("workspace_setup", "/workspace/yunshu/install/setup.bash")
    runtime.setdefault("instance_count", 1)
    runtime.setdefault("enabled_adapter_types", [adapter])
    runtime.setdefault("privileged", True)
    runtime.setdefault("extra_mounts", [])
    runtime.setdefault("cleanup_policy", "keep_on_failure")

    ros.setdefault("domain_id", target.get("ros_domain_id", 0))
    ros.setdefault("localhost_only", target.get("ros_localhost_only", 0))
    ros.setdefault("rmw_implementation", target.get("rmw_implementation") or "rmw_fastrtps_cpp")
    ros.setdefault("hal_interface_version", contract.get("sdk_version") or "")

    generated = generated_checks(adapter, refs, topic_prefix, manager_ns)
    for name in LAYERS:
        if not checks.get(name):
            checks[name] = generated[name]
    checks.setdefault("scenarios", {})
    for name in SCENARIOS:
        if not checks["scenarios"].get(name):
            checks["scenarios"][name] = generated["scenarios"][name]

    gaps: list[str] = []
    for field in ("project_dir", "deployment_file", "manager_command"):
        if not runtime.get(field):
            gaps.append(f"runtime_test.{field}")
    if runtime.get("instance_count") != 1 or runtime.get("enabled_adapter_types") != [adapter]:
        gaps.append("runtime_test.single_device_contract")
    for name in LAYERS:
        if not checks.get(name) or not all(valid_check(item) for item in checks[name]):
            gaps.append(f"acceptance_checks.{name}")
    for name in SCENARIOS:
        if not checks["scenarios"].get(name) or not all(valid_check(item) for item in checks["scenarios"][name]):
            gaps.append(f"acceptance_checks.scenarios.{name}")
    has_not_run = any(
        item.get("status") == "NOT_RUN"
        for values in [*(checks[name] for name in LAYERS), *(checks["scenarios"][name] for name in SCENARIOS)]
        for item in values
    )
    plan: dict[str, Any] = {
        "schema_version": "1.0", "context_id": args.context_id, "adapter_type": adapter,
        "generated_by": "generate_deployment_plan.py",
        "status": "BLOCKED" if gaps else ("READY_WITH_NOT_RUN" if has_not_run else "READY"),
        "ros_compatibility": ros, "runtime_test": runtime,
        "runtime_requirements": spec.get("runtime_requirements") or {},
        "acceptance_checks": checks, "blocking_gaps": gaps,
        "generated_deployment_yaml": str(deployment_yaml),
        "device_identity": {"local_id": f"{adapter}_0", "device_uuid": device_uuid, "topic_prefix": topic_prefix},
    }
    write_json(path, plan)
    if gaps:
        print("deployment plan is BLOCKED: " + ", ".join(gaps))
        return 17
    print(f"deployment plan ready: {path} ({plan['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
