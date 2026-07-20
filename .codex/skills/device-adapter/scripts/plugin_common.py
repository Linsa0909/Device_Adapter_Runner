#!/usr/bin/env python3
"""Shared runtime-plugin contract helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CONTEXTS = Path("ops/contexts")
ARTIFACTS = Path("ops/artifacts")

REQUIRED_CONTRACT_FIELDS = (
    "adapter_type",
    "vendor",
    "plugin_version",
    "sdk_root",
    "sdk_version",
    "sdk_abi",
    "plugin_abi",
    "target_arch",
    "target_platform",
    "target_os",
    "compiler_triplet",
    "runtime_image",
    "capability_group_refs",
    "supports_multi_instance",
    "private_config",
    "target_build",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def contract_path(context_id: str) -> Path:
    return CONTEXTS / f"{validate_context_id(context_id)}.plugin_contract.json"


def load_contract(context_id: str) -> dict[str, Any]:
    path = contract_path(context_id)
    if not path.exists():
        raise FileNotFoundError(f"plugin contract not found: {path}")
    contract = load_json(path)
    if contract.get("delivery_mode") != "runtime_plugin":
        raise ValueError("delivery_mode must be runtime_plugin")
    return contract


def missing_contract_fields(contract: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_CONTRACT_FIELDS:
        value = contract.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    if contract.get("supports_multi_instance") is not True:
        missing.append("supports_multi_instance")
    return sorted(set(missing))


def validate_context_id(context_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context_id):
        raise ValueError("context_id may contain only letters, digits, dot, underscore, and hyphen")
    return context_id


def contract_errors(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(contract.get("adapter_type") or "")):
        errors.append("adapter_type must match [a-z][a-z0-9_]*")
    for field in ("vendor", "plugin_version", "sdk_version"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(contract.get(field) or "")):
            errors.append(f"{field} contains unsupported characters")
    for field in ("sdk_abi", "plugin_abi"):
        value = contract.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"{field} must be a positive integer")
    if sdk_arch_dir(str(contract.get("target_arch") or "")) not in {
        "aarch64-linux-gnu-gcc", "x86_64-linux-gnu-gcc"
    }:
        errors.append("target_arch must be aarch64/arm64 or x86_64/amd64")
    refs = contract.get("capability_group_refs")
    if isinstance(refs, list):
        for ref in refs:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", str(ref)):
                errors.append(f"invalid capability_group_ref: {ref}")
    private_config = contract.get("private_config")
    if not isinstance(private_config, dict):
        errors.append("private_config must be an object")
    else:
        expected_path = f"config/{contract.get('adapter_type', '')}.json"
        if private_config.get("path") != expected_path:
            errors.append(f"private_config.path must be {expected_path}")
        if not private_config.get("schema_version"):
            errors.append("private_config.schema_version is required")
    target_build = contract.get("target_build")
    if isinstance(target_build, dict) and target_build.get("build_in_runtime_container") is not True:
        errors.append("target_build.build_in_runtime_container must be true")
    fastpath = contract.get("fastpath_contract")
    if fastpath is not None:
        if not isinstance(fastpath, dict):
            errors.append("fastpath_contract must be an object")
        else:
            constants = fastpath.get("required_constants", [])
            if not isinstance(constants, list):
                errors.append("fastpath_contract.required_constants must be a list")
            else:
                for value in constants:
                    if not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*",
                        str(value),
                    ):
                        errors.append(f"invalid fastpath constant: {value}")
            arrays = fastpath.get("fixed_arrays", [])
            if not isinstance(arrays, list):
                errors.append("fastpath_contract.fixed_arrays must be a list")
            else:
                for index, item in enumerate(arrays):
                    if not isinstance(item, dict):
                        errors.append(
                            f"fastpath_contract.fixed_arrays[{index}] must be an object"
                        )
                        continue
                    for field in ("domain_type", "field", "element_type"):
                        if not re.fullmatch(
                            r"[A-Za-z_][A-Za-z0-9_:]*", str(item.get(field) or "")
                        ):
                            errors.append(
                                f"fastpath_contract.fixed_arrays[{index}].{field} is invalid"
                            )
                    length = item.get("length")
                    if (
                        not isinstance(length, int)
                        or isinstance(length, bool)
                        or length < 1
                        or length > 4294967295
                    ):
                        errors.append(
                            f"fastpath_contract.fixed_arrays[{index}].length must be a positive uint32 integer"
                        )
    for field in ("plugin_source_dir", "package_dir", "build_dir", "sdk_output_dir"):
        value = contract.get(field)
        if value:
            try:
                resolve_output_path(str(value), field)
            except ValueError as exc:
                errors.append(str(exc))
    return errors


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def resolve_output_path(value: str, field: str) -> Path:
    root = Path.cwd().resolve()
    path = resolve_project_path(value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{field} must stay inside the project workspace")
    return path


def plugin_source_dir(contract: dict[str, Any]) -> Path:
    value = contract.get("plugin_source_dir") or f"adapter_plugins/{contract['adapter_type']}"
    return resolve_output_path(str(value), "plugin_source_dir")


def package_dir(contract: dict[str, Any]) -> Path:
    value = contract.get("package_dir") or f"build/{contract['adapter_type']}-package"
    return resolve_output_path(str(value), "package_dir")


def sdk_arch_dir(target_arch: str) -> str:
    normalized = target_arch.lower().replace("-", "_")
    if normalized in {"aarch64", "arm64", "linux_arm64"}:
        return "aarch64-linux-gnu-gcc"
    if normalized in {"x86_64", "amd64", "linux_amd64"}:
        return "x86_64-linux-gnu-gcc"
    return target_arch
