#!/usr/bin/env python3
"""Generate an evidence-backed single-device deployment and acceptance plan."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path
from typing import Any

from plugin_common import (CONTEXTS, load_contract, load_json, load_platform_profile,
                           platform_profile_conflicts, write_json)


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


def generated_checks(adapter: str, mappings: list[dict[str, Any]], topic_prefix: str, manager_ns: str) -> dict[str, Any]:
    def get_property(group: str, prop: str = "") -> str:
        return (
            f"ros2 service call {topic_prefix}/get_property hal_interface/srv/GetProperty "
            f"\"{{group_id: '{group}', property_id: '{prop}'}}\""
        )

    capability = []
    functional = []
    soak_topics = []
    for mapping in mappings:
        group = str(mapping.get("group_id") or "")
        feature = str(mapping.get("feature_id") or group)
        explicit_tests = [item for item in mapping.get("tests", []) if isinstance(item, dict)]
        for test in explicit_tests:
            if test.get("command"):
                target = functional if test.get("layer") in {"functional", "stream_or_event"} else capability
                target.append(executable(str(test["command"]), str(test.get("evidence") or feature),
                    str(test.get("expect_contains") or ""), int(test.get("expect_exit_code", 0)),
                    str(test.get("executor") or "client"), int(test.get("timeout_sec", 120))))
        for entry in mapping.get("hal_entries", []):
            entry_id = str(entry.get("id") or "")
            kind = str(entry.get("kind") or "")
            if kind == "property":
                capability.append(executable(get_property(group, entry_id),
                    f"{feature}: property {group}.{entry_id} returns a real value", "success: true"))
            elif kind == "service":
                command = (f"ros2 service call {topic_prefix}/call_service hal_interface/srv/CallService "
                    f"\"{{group_id: '{group}', service_id: '{entry_id}', params_json: '{{}}'}}\"")
                capability.append(executable(command, f"{feature}: service {group}.{entry_id} responds", "success: true"))
            topic = entry.get("topic") if isinstance(entry.get("topic"), dict) else {}
            if topic.get("name") and topic.get("message_type"):
                topic_name = str(topic["name"])
                if not topic_name.startswith("/"):
                    topic_name = f"{topic_prefix}/{topic_name}"
                functional.append(executable(
                    f"timeout 20 ros2 topic echo --once {topic_name} {topic['message_type']}",
                    f"{feature}: one fresh {group}.{entry_id} sample/event", timeout_sec=25))
                soak_topics.append(topic_name)
    if not capability:
        capability.append(not_run("no testable capability reference is declared", "capability mapping"))
    if not functional:
        functional.append(not_run("no generated FastPath check is available", "functional mapping"))

    first_group = str(mappings[0].get("group_id")) if mappings else "device"
    lifecycle = (
        f"ros2 service call {manager_ns}/device_life_control hal_interface/srv/DeviceLifeControl "
        f"\"{{device_id: '{adapter}_0', action: 0, config_json: ''}}\" && "
        f"ros2 service call {manager_ns}/device_life_control hal_interface/srv/DeviceLifeControl "
        f"\"{{device_id: '{adapter}_0', action: 1, config_json: ''}}\""
    )
    soak_topic = soak_topics[0] if soak_topics else ""
    return {
        "load": [executable(
            f"test -n \"$(ros2 node list)\" && ros2 service list | grep -Fx {topic_prefix}/get_property",
            "HAL node exists and plugin ABI load exposes the device service", topic_prefix)],
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
            "soak": ([executable(f"timeout 60 ros2 topic hz {soak_topic}", "60-second data-path rate evidence",
                "average rate", 124, timeout_sec=70)] if soak_topic else
                [not_run("no capability mapping declares a topic", "capability mapping topic metadata")]),
        },
    }


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:+-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def write_deployment(path: Path, adapter: str, device_uuid: str,
                     bindings: list[dict[str, Any]]) -> None:
    primary = next((item for item in bindings if item.get("binding_id") in {"primary", "control"}),
                   bindings[0] if bindings else {})
    protocol = str(primary.get("profile_id") or "unresolved")
    config = primary.get("config") if isinstance(primary.get("config"), dict) else {}
    connection_lines = ["      connection:", f"        protocol: {yaml_scalar(protocol)}"]
    for key, value in sorted(config.items()):
        if isinstance(value, (dict, list)):
            connection_lines.append(f"        {key}_json: {yaml_scalar(json.dumps(value, ensure_ascii=False, sort_keys=True))}")
        else:
            connection_lines.append(f"        {key}: {yaml_scalar(value)}")
    if len(bindings) > 1:
        connection_lines.extend([
            "      params:",
            f"        transport_bindings_json: {yaml_scalar(json.dumps(bindings, ensure_ascii=False, sort_keys=True))}",
        ])
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
        + "\n".join(connection_lines) + "\n",
        encoding="utf-8",
    )


def target_sdk_runtime_project(context_id: str) -> str:
    path = Path("ops/artifacts") / f"{context_id}.target_sdk_package.json"
    if not path.is_file():
        return ""
    try:
        report = load_json(path)
    except (OSError, ValueError):
        return ""
    if str(report.get("status") or "").upper() != "PASS":
        return ""
    return str(report.get("remote_run") or "")


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
    profile = load_platform_profile()
    conflicts = platform_profile_conflicts({**contract, **target})
    if conflicts:
        print("PLATFORM_PROFILE_CONFLICT: " + "; ".join(conflicts))
        return 17
    runtime = dict(existing.get("runtime_test") or {})
    ros = dict(existing.get("ros_compatibility") or {})
    checks = dict(existing.get("acceptance_checks") or {})
    adapter = str(contract.get("adapter_type") or spec.get("adapter_type") or args.context_id)
    refs = [str(value) for value in contract.get("capability_group_refs") or []]
    mapping_path = CONTEXTS / f"{args.context_id}.capability_mapping.json"
    capability_mapping = load_json(mapping_path) if mapping_path.is_file() else {}
    mappings = list(capability_mapping.get("mappings") or [])
    transport_path = CONTEXTS / f"{args.context_id}.transport_bindings.json"
    transport_report = load_json(transport_path) if transport_path.is_file() else {}
    bindings = list(transport_report.get("bindings") or [])
    device_uuid = str(uuid.uuid5(HAL_UUID_NAMESPACE, f"{adapter}_0"))
    slug = "u_" + device_uuid.replace("-", "")
    topic_prefix = f"/hal/device/{slug}"
    manager_ns = f"/hal/manager/{slug}"
    deployment_yaml = CONTEXTS / f"{args.context_id}.deployment.yaml"
    write_deployment(deployment_yaml, adapter, device_uuid, bindings)

    runtime.setdefault("image", contract.get("runtime_image") or "")
    if args.project_dir:
        runtime["project_dir"] = args.project_dir
        runtime["project_dir_source"] = "explicit_model_override"
    elif target.get("runtime_project_dir"):
        runtime["project_dir"] = target["runtime_project_dir"]
        runtime["project_dir_source"] = "plugin_contract.target_build"
    elif target_sdk_runtime_project(args.context_id):
        runtime["project_dir"] = target_sdk_runtime_project(args.context_id)
        runtime["project_dir_source"] = "target_sdk_package.remote_run"
    else:
        runtime.setdefault("project_dir", "")
    runtime.setdefault("project_mount", profile["project_mount"])
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
    runtime.setdefault("instance_count", profile["runtime_instance_count"])
    runtime.setdefault("enabled_adapter_types", [adapter])
    runtime.setdefault("privileged", True)
    runtime.setdefault("extra_mounts", [])
    runtime.setdefault("cleanup_policy", "keep_on_failure")

    ros.setdefault("domain_id", target.get("ros_domain_id", 0))
    ros.setdefault("localhost_only", target.get("ros_localhost_only", 0))
    ros.setdefault("rmw_implementation", profile["rmw_implementation"])
    ros.setdefault("hal_interface_version", contract.get("sdk_version") or "")

    generated = generated_checks(adapter, mappings, topic_prefix, manager_ns)
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
    if not mappings or capability_mapping.get("status") != "PASS":
        gaps.append("capability_mapping.ready")
    if not bindings or transport_report.get("status") != "PASS":
        gaps.append("transport_bindings.ready")
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
