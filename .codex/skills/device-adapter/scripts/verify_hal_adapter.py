#!/usr/bin/env python3
"""Deterministic HAL adapter verification for device-adapter contexts."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


HAL_ROOT = Path("src/hardware_abstraction_layer")
CONTEXTS = Path("ops/contexts")
ARTIFACTS = Path("ops/artifacts")


def stage(name: str, status: str, exit_code: int | None = None) -> None:
    suffix = "" if exit_code is None else f" exit_code={exit_code}"
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_failure(context_id: str, stage_name: str, reason: str, evidence: list[str], next_action: str, exit_code: int) -> None:
    log_file = Path("ops/artifacts/logs") / f"{context_id}_stage_runner.log"
    write_json(
        ARTIFACTS / "last_failure.json",
        {
            "context_id": context_id,
            "stage": stage_name,
            "agent": "verify_hal_adapter.py",
            "status": "fail",
            "exit_code": exit_code,
            "reason": reason,
            "evidence": evidence,
            "log_file": str(log_file),
            "next_action": next_action,
            "rerun_command": f"/device-adapter rerun {context_id}",
        },
    )


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def req_path(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("path", "source", "file", "dir", "include_dir", "library", "name"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def runtime_requirements(spec: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(spec.get("runtime_requirements") or {})
    legacy = spec.get("adapter_requirements") or {}
    mapping = {
        "apt_build": "apt_build",
        "apt_runtime": "apt_runtime",
        "sdk_headers": "vendor_headers",
        "sdk_libraries": "vendor_libraries",
        "protocol_files": "protocol_files",
        "subprocesses": "subprocesses",
    }
    for old, new in mapping.items():
        if new not in runtime and old in legacy:
            runtime[new] = legacy[old]
    if "apt_build" not in runtime and legacy.get("apt_packages"):
        runtime["apt_build"] = legacy["apt_packages"]
    if "apt_runtime" not in runtime and legacy.get("apt_packages"):
        runtime["apt_runtime"] = legacy["apt_packages"]
    return runtime


def token_path_exists(path_text: str) -> bool:
    if not path_text or "$" in path_text or "<" in path_text or ">" in path_text:
        return True
    return Path(path_text).exists()


def find_repo_matches(fragment: str) -> list[str]:
    if not fragment:
        return []
    name = Path(fragment).name
    if not name:
        return []
    matches: list[str] = []
    for path in Path(".").rglob(name):
        if ".git" in path.parts or "ops" in path.parts and "artifacts" in path.parts:
            continue
        matches.append(path.as_posix())
        if len(matches) >= 20:
            break
    return matches


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def capability_id(spec: dict[str, Any]) -> str:
    capability = spec.get("capability") or {}
    if isinstance(capability, dict):
        return str(capability.get("id") or capability.get("group_id") or "").strip()
    return ""


def validate_spec(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    evidence: list[str] = []
    for key in ("adapter_type", "device", "connection", "capability", "device_model", "deployment_entry"):
        if not spec.get(key):
            errors.append(f"missing required field: {key}")
    adapter_type = str(spec.get("adapter_type") or "")
    deployment = spec.get("deployment_entry") or {}
    if isinstance(deployment, dict) and deployment.get("adapter_type") and deployment.get("adapter_type") != adapter_type:
        errors.append("deployment_entry.adapter_type does not match adapter_type")
    runtime = runtime_requirements(spec)
    if not runtime and not spec.get("adapter_requirements"):
        errors.append("missing runtime_requirements or adapter_requirements")
    evidence.append(f"adapter_type={adapter_type or '<missing>'}")
    evidence.append(f"capability_id={capability_id(spec) or '<missing>'}")
    write_json(
        ARTIFACTS / f"{context_id}.spec_validation.json",
        {"ok": not errors, "errors": errors, "evidence": evidence},
    )
    return not errors, errors, evidence


def validate_dependencies(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    runtime = runtime_requirements(spec)
    errors: list[str] = []
    evidence: list[str] = []
    checked: list[dict[str, Any]] = []

    for group in ("vendor_headers", "vendor_libraries", "protocol_files"):
        for item in as_list(runtime.get(group)):
            path_text = req_path(item)
            if not path_text:
                continue
            if re.match(r"^(lib[^/]+\.so|lib[^/]+\.a)$", path_text):
                checked.append({"group": group, "path": path_text, "exists": "unknown_name_only"})
                evidence.append(f"{group}: {path_text} is a library name only")
                continue
            path = Path(path_text)
            exists = path.exists()
            checked.append({"group": group, "path": path_text, "exists": exists})
            if not exists:
                required = not (isinstance(item, dict) and item.get("required") is False)
                if required:
                    errors.append(f"missing required {group}: {path_text}")

    for item in as_list(runtime.get("subprocesses")):
        if isinstance(item, dict):
            command = item.get("command") or item.get("executable") or item.get("name")
            if command:
                evidence.append(f"subprocess declared: {command}")
            for required in as_list(item.get("required_files") or item.get("runtime_files")):
                path_text = req_path(required)
                if path_text and not token_path_exists(path_text):
                    errors.append(f"missing subprocess runtime file: {path_text}")
            executable = str(item.get("executable") or "").strip()
            if not executable and isinstance(command, str):
                executable = command.split()[0] if command.split() else ""
            if executable.startswith(("/app/", "/opt/", "/workspace/")):
                repo_matches = find_repo_matches(executable)
                declared_assets = as_list(runtime.get("delivery_files")) + as_list(runtime.get("runtime_assets")) + as_list(runtime.get("vendor_libraries"))
                declared_text = "\n".join(str(x) for x in declared_assets)
                if not repo_matches and Path(executable).name not in declared_text:
                    errors.append(
                        "subprocess executable is not delivered by repository or runtime_requirements: "
                        f"{executable}"
                    )

    connection = spec.get("connection") or {}
    if isinstance(connection, dict):
        protocol = str(connection.get("protocol") or "").lower()
        device_path = str(connection.get("device_path") or connection.get("port") or "")
        if protocol in {"usb", "uvc", "v4l2", "video", "device_node"} and re.fullmatch(r"/dev/video\d+", device_path):
            discovery = as_list(connection.get("discovery_rules") or runtime.get("device_nodes"))
            if not discovery:
                errors.append("USB/UVC device path is hard-coded without discovery rules")

    write_json(
        ARTIFACTS / f"{context_id}.dependency_validation.json",
        {"ok": not errors, "errors": errors, "checked": checked, "runtime_requirements": runtime, "evidence": evidence},
    )
    return not errors, errors, evidence


def validate_adapter_source_paths(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    adapter_type = str(spec.get("adapter_type") or "")
    errors: list[str] = []
    evidence: list[str] = []
    source_roots = [
        HAL_ROOT / "src" / "adapter" / adapter_type,
        HAL_ROOT / "include" / "hardware_abstraction_layer" / "adapter" / adapter_type,
    ]
    source_files: list[Path] = []
    for root in source_roots:
        if root.exists():
            source_files.extend(p for p in root.rglob("*") if p.is_file() and p.suffix in {".cpp", ".cc", ".cxx", ".hpp", ".h"})

    include_pattern = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)
    checked: list[dict[str, str]] = []
    for path in source_files:
        text = read_text(path)
        for include in include_pattern.findall(text):
            if not include.startswith("hardware_abstraction_layer/"):
                continue
            candidates = [
                HAL_ROOT / "include" / include,
                HAL_ROOT / "src" / include,
            ]
            exists = any(candidate.exists() for candidate in candidates)
            checked.append({"source": path.as_posix(), "include": include, "exists": str(exists)})
            if not exists:
                errors.append(f"project include does not exist: {path}: {include}")
            expected_prefix = f"hardware_abstraction_layer/adapter/{adapter_type}/"
            if "/adapter/" in include and adapter_type and not include.startswith(expected_prefix):
                actual = HAL_ROOT / "include" / include
                if not actual.exists():
                    errors.append(
                        "adapter include directory does not match adapter_type: "
                        f"adapter_type={adapter_type} include={include}"
                    )

    if checked:
        evidence.append(f"project includes checked: {len(checked)}")
    write_json(
        ARTIFACTS / f"{context_id}.source_path_validation.json",
        {"ok": not errors, "errors": errors, "checked": checked, "evidence": evidence},
    )
    return not errors, errors, evidence


def cmake_install_tokens(block: str) -> list[str]:
    tokens: list[str] = []
    cleaned = re.sub(r"#.*", "", block)
    parts = [raw.strip().strip('"') for raw in re.split(r"\s+", cleaned)]
    skip_destination_arg = False
    for item in parts:
        if skip_destination_arg:
            skip_destination_arg = False
            continue
        if item.upper() == "DESTINATION":
            skip_destination_arg = True
            continue
        if not item or item.upper() in {"PROGRAMS", "FILES", "DESTINATION", "RENAME", "OPTIONAL", "PERMISSIONS"}:
            continue
        if item.startswith("${") or item.startswith("$<"):
            continue
        if "/" in item or "." in Path(item).name:
            tokens.append(item)
    return tokens


def resolve_cmake_install_token(token: str) -> Path | None:
    if token.startswith("${CMAKE_CURRENT_SOURCE_DIR}/"):
        return HAL_ROOT / token.removeprefix("${CMAKE_CURRENT_SOURCE_DIR}/")
    if token.startswith("CMAKE_CURRENT_SOURCE_DIR/"):
        return HAL_ROOT / token.removeprefix("CMAKE_CURRENT_SOURCE_DIR/")
    if token.startswith("/"):
        return Path(token)
    if token.startswith(".."):
        return HAL_ROOT / token
    if "/" in token or "." in Path(token).name:
        return HAL_ROOT / token
    return None


def validate_cmake_install_paths(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    del spec
    cmake = HAL_ROOT / "CMakeLists.txt"
    errors: list[str] = []
    evidence: list[str] = []
    checked: list[dict[str, str]] = []
    if not cmake.exists():
        errors.append(f"missing CMakeLists.txt: {cmake}")
    else:
        text = read_text(cmake)
        for match in re.finditer(r"install\s*\(\s*(PROGRAMS|FILES)(.*?)\)", text, re.IGNORECASE | re.DOTALL):
            for token in cmake_install_tokens(match.group(0)):
                path = resolve_cmake_install_token(token)
                if path is None:
                    continue
                resolved = path.resolve(strict=False)
                hal_resolved = HAL_ROOT.resolve(strict=False)
                exists = path.exists()
                package_local = resolved == hal_resolved or hal_resolved in resolved.parents
                checked.append({"token": token, "path": path.as_posix(), "exists": str(exists), "package_local": str(package_local)})
                if not package_local:
                    errors.append(f"CMake install path is outside hardware_abstraction_layer package: {token}")
                elif not exists:
                    errors.append(f"CMake install path does not exist: {token} -> {path}")
    if checked:
        evidence.append(f"CMake install path tokens checked: {len(checked)}")
    write_json(
        ARTIFACTS / f"{context_id}.cmake_install_validation.json",
        {"ok": not errors, "errors": errors, "checked": checked, "evidence": evidence},
    )
    return not errors, errors, evidence


def validate_release_scripts(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    evidence: list[str] = []
    checked: list[str] = []
    candidate_roots = [Path("."), Path("ops"), HAL_ROOT / "scripts"]
    for root in candidate_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.sh") if root != Path(".") else Path(".").glob("*.sh"):
            if not path.is_file():
                continue
            checked.append(path.as_posix())
            text = read_text(path)
            if any(pattern in text for pattern in ("docker exec -it", "docker exec -ti", "docker run -it", "docker run -ti")):
                errors.append(f"interactive docker flag found in automation script: {path}")

    delivery = spec.get("delivery") or {}
    required = as_list(delivery.get("required_root_files"))
    if delivery.get("closed_loop_package") and not required:
        required = ["config.env", "install.sh", "run.sh", "status.sh", "view.sh", "DEPLOY.md"]
    missing = [item for item in required if not Path(item).exists()]
    for item in missing:
        errors.append(f"missing closed-loop release entry: {item}")
    if checked:
        evidence.append(f"shell scripts checked: {len(checked)}")
    write_json(
        ARTIFACTS / f"{context_id}.release_script_validation.json",
        {"ok": not errors, "errors": errors, "checked": checked, "required_root_files": required, "evidence": evidence},
    )
    return not errors, errors, evidence


def validate_yaml(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    adapter_type = str(spec.get("adapter_type") or "")
    cap_id = capability_id(spec)
    paths = {
        "capability": HAL_ROOT / "model" / "capability_groups" / f"{cap_id}.capability.yaml" if cap_id else None,
        "device": HAL_ROOT / "model" / "devices" / f"{adapter_type}.device.yaml" if adapter_type else None,
        "deployment": HAL_ROOT / "config" / "deployment.yaml",
    }
    errors: list[str] = []
    evidence: list[str] = []
    for label, maybe_path in paths.items():
        if maybe_path is None:
            errors.append(f"cannot resolve {label} yaml path")
            continue
        if not maybe_path.exists():
            errors.append(f"missing {label} yaml: {maybe_path}")
        else:
            evidence.append(f"{label} yaml exists: {maybe_path}")
    write_json(
        ARTIFACTS / f"{context_id}.yaml_validation.json",
        {"ok": not errors, "errors": errors, "evidence": evidence},
    )
    return not errors, errors, evidence


def validate_registration(context_id: str, spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    adapter_type = str(spec.get("adapter_type") or "")
    errors: list[str] = []
    evidence: list[str] = []
    cmake = HAL_ROOT / "CMakeLists.txt"
    factory = HAL_ROOT / "include" / "hardware_abstraction_layer" / "adapter" / "adapter_factory.hpp"
    deployment = HAL_ROOT / "config" / "deployment.yaml"

    files = {"cmake": cmake, "factory": factory, "deployment": deployment}
    contents: dict[str, str] = {}
    for label, path in files.items():
        if not path.exists():
            errors.append(f"missing {label} file: {path}")
            contents[label] = ""
        else:
            contents[label] = path.read_text(encoding="utf-8", errors="ignore")
            evidence.append(f"{label} file exists: {path}")

    adapter_source_globs = [
        HAL_ROOT / "src" / "adapter" / adapter_type,
        HAL_ROOT / "include" / "hardware_abstraction_layer" / "adapter" / adapter_type,
    ]
    source_hits = []
    for root in adapter_source_globs:
        if root.exists():
            source_hits.extend(str(p) for p in root.rglob("*") if p.is_file() and p.suffix in {".cpp", ".hpp", ".h", ".cc"})
    if not source_hits:
        errors.append(f"missing adapter source/header directory for adapter_type={adapter_type}")
    else:
        evidence.append(f"adapter files found: {len(source_hits)}")

    if adapter_type:
        if adapter_type not in contents.get("factory", ""):
            errors.append(f"adapter_type not found in adapter_factory.hpp: {adapter_type}")
        if adapter_type not in contents.get("deployment", ""):
            errors.append(f"adapter_type not found in deployment.yaml: {adapter_type}")
        cmake_text = contents.get("cmake", "")
        normalized = adapter_type.replace("-", "_")
        if adapter_type not in cmake_text and normalized not in cmake_text:
            errors.append(f"adapter_type or target name not found in CMakeLists.txt: {adapter_type}")
        if "install(TARGETS" not in cmake_text:
            errors.append("CMakeLists.txt has no install(TARGETS ...) rule")
    else:
        errors.append("missing adapter_type for registration validation")

    write_json(
        ARTIFACTS / f"{context_id}.registration_report.json",
        {"ok": not errors, "errors": errors, "evidence": evidence, "adapter_files": source_hits},
    )
    return not errors, errors, evidence


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_hal_adapter.py <context_id>", file=sys.stderr)
        return 2
    context_id = sys.argv[1]
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    spec_path = CONTEXTS / f"{context_id}.device_spec.json"
    stage("stage7_spec_validate", "start")
    if not spec_path.exists():
        reason = f"missing device spec: {spec_path}"
        write_failure(context_id, "stage7_spec_validate", reason, [reason], f"Run /device-adapter model {context_id}", 2)
        stage("stage7_spec_validate", "fail", 2)
        return 2
    try:
        spec = read_json(spec_path)
    except Exception as exc:
        reason = f"invalid JSON in {spec_path}: {exc}"
        write_failure(context_id, "stage7_spec_validate", reason, [reason], f"Fix {spec_path}", 2)
        stage("stage7_spec_validate", "fail", 2)
        return 2

    ok, errors, evidence = validate_spec(context_id, spec)
    if not ok:
        write_failure(context_id, "stage7_spec_validate", "device_spec validation failed", evidence + errors, f"Fix {spec_path}", 2)
        stage("stage7_spec_validate", "fail", 2)
        return 2
    stage("stage7_spec_validate", "success")

    checks = [
        ("stage6_dependency_audit", validate_dependencies, "Fix dependency_plan/device_spec or provide missing SDK files"),
        ("stage9_yaml_validate", validate_yaml, f"Run /device-adapter adapt {context_id}"),
        ("stage10_adapter_codegen", validate_adapter_source_paths, "Fix generated adapter include paths and source layout"),
        ("stage11_hal_registration_verify", validate_cmake_install_paths, "Move installed scripts/files under the ROS package and fix CMake install paths"),
        ("stage11_hal_registration_verify", validate_registration, "Register adapter in CMake, factory, deployment, and install rules"),
        ("stage12_package_manifest", validate_release_scripts, "Remove interactive docker flags and add required release entry scripts"),
    ]
    for stage_name, func, next_action in checks:
        stage(stage_name, "start")
        ok, errors, evidence = func(context_id, spec)
        if not ok:
            write_failure(context_id, stage_name, f"{stage_name} failed", evidence + errors, next_action, 2)
            stage(stage_name, "fail", 2)
            return 2
        stage(stage_name, "success")

    print(f"HAL adapter verification passed for {context_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
