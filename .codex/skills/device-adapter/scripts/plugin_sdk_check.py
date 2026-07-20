#!/usr/bin/env python3
"""Validate the immutable HAL Adapter SDK against a plugin contract."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from plugin_common import (
    ARTIFACTS,
    CONTEXTS,
    contract_errors,
    load_contract,
    missing_contract_fields,
    resolve_project_path,
    sdk_arch_dir,
    write_json,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.M)
    return match.group(1) if match else ""


def struct_body(header: str, name: str) -> str:
    match = re.search(
        rf"\bstruct\s+{re.escape(name)}\s*\{{(?P<body>.*?)\}};",
        header,
        re.S,
    )
    return match.group("body") if match else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    report_path = ARTIFACTS / f"{args.context_id}.sdk_check.json"

    try:
        contract = load_contract(args.context_id)
    except (FileNotFoundError, ValueError) as exc:
        write_json(report_path, {"status": "FAIL", "error_code": "PLUGIN_CONTRACT_INVALID", "message": str(exc)})
        return 2

    if args.bootstrap:
        required_bootstrap = (
            "adapter_type", "sdk_root", "sdk_output_dir", "sdk_version",
            "sdk_abi", "plugin_abi", "target_arch", "compiler_triplet",
        )
        missing_fields = [field for field in required_bootstrap if contract.get(field) in (None, "", [])]
        validation_errors = []
    else:
        missing_fields = missing_contract_fields(contract)
        validation_errors = contract_errors(contract)
    sdk = resolve_project_path(str(contract.get("sdk_root") or ""))
    arch_dir = sdk_arch_dir(str(contract.get("target_arch") or ""))
    required = [
        sdk / "VERSION",
        sdk / "ABI_VERSION",
        sdk / "README.md",
        sdk / "requirements.txt",
        sdk / "docs/migrating_in_tree_adapter.md",
        sdk / "cmake/HalAdapterSdkConfig.cmake",
        sdk / "include/hardware_abstraction_layer/adapter/adapter_interface.hpp",
        sdk / "include/hardware_abstraction_layer/adapter/plugin_sdk/adapter_plugin_api.hpp",
        sdk / "include/hal/domain_types_generated.hpp",
        sdk / "include/hal/fastpath_keys_generated.hpp",
        sdk / f"platform/lib/{arch_dir}/libhal_model.so",
        sdk / f"platform/lib/{arch_dir}/libhal_media.so",
        sdk / f"platform/lib/{arch_dir}/libhal_utils.so",
        sdk / "tools/hal_adapter_model_lint.py",
        sdk / "examples/minimal_adapter/CMakeLists.txt",
    ]
    missing_paths = [str(path) for path in required if not path.is_file()]
    sdk_version = (sdk / "VERSION").read_text().strip() if (sdk / "VERSION").is_file() else ""
    sdk_abi_text = (sdk / "ABI_VERSION").read_text().strip() if (sdk / "ABI_VERSION").is_file() else ""
    mismatches: list[str] = []
    if sdk_version and sdk_version != str(contract.get("sdk_version")):
        mismatches.append(f"sdk_version expected={contract.get('sdk_version')} actual={sdk_version}")
    if sdk_abi_text and sdk_abi_text != str(contract.get("sdk_abi")):
        mismatches.append(f"sdk_abi expected={contract.get('sdk_abi')} actual={sdk_abi_text}")

    internal_mismatches: list[str] = []
    cmake_path = sdk / "cmake/HalAdapterSdkConfig.cmake"
    api_path = sdk / (
        "include/hardware_abstraction_layer/adapter/plugin_sdk/"
        "adapter_plugin_api.hpp"
    )
    cmake_text = cmake_path.read_text(encoding="utf-8") if cmake_path.is_file() else ""
    api_text = api_path.read_text(encoding="utf-8") if api_path.is_file() else ""
    cmake_version = first_match(
        cmake_text, r'HAL_ADAPTER_SDK_VERSION\s+"([^"]+)"'
    )
    cmake_abi = first_match(
        cmake_text, r'HAL_ADAPTER_SDK_ABI_VERSION\s+"([^"]+)"'
    )
    header_abi = first_match(
        api_text, r"kAdapterSdkAbiVersion\s*=\s*([0-9]+)"
    )
    for source, actual, expected in (
        ("cmake sdk version", cmake_version, sdk_version),
        ("cmake sdk abi", cmake_abi, sdk_abi_text),
        ("public header sdk abi", header_abi, sdk_abi_text),
    ):
        if not actual:
            internal_mismatches.append(f"{source} is not declared")
        elif expected and actual != expected:
            internal_mismatches.append(
                f"{source} expected={expected} actual={actual}"
            )

    domain_path = sdk / "include/hal/domain_types_generated.hpp"
    domain_text = (
        domain_path.read_text(encoding="utf-8") if domain_path.is_file() else ""
    )
    fastpath = contract.get("fastpath_contract")
    fastpath = fastpath if isinstance(fastpath, dict) else {}
    missing_constants: list[str] = []
    for qualified in fastpath.get("required_constants", []):
        domain_type, constant = str(qualified).split("::", 1)
        body = struct_body(domain_text, domain_type)
        if not re.search(
            rf"\bstatic\s+constexpr\b[^;]*\b{re.escape(constant)}\s*=",
            body,
        ):
            missing_constants.append(str(qualified))

    missing_arrays: list[dict[str, object]] = []
    for requirement in fastpath.get("fixed_arrays", []):
        body = struct_body(domain_text, str(requirement["domain_type"]))
        element = re.escape(str(requirement["element_type"]))
        length = int(requirement["length"])
        field = re.escape(str(requirement["field"]))
        if not re.search(
            rf"std::array\s*<\s*{element}\s*,\s*{length}\s*>\s*{field}\s*;",
            body,
        ):
            missing_arrays.append(requirement)

    capability_dir = sdk / "model_reference/capability_groups"
    missing_groups = [] if args.bootstrap else [
        group for group in contract.get("capability_group_refs", [])
        if not (capability_dir / f"{group}.capability.yaml").is_file()
    ]
    forbidden_files: list[str] = []
    sensitive_names = {"id_rsa", "id_ed25519", ".env", ".netrc", "credentials"}
    for path in sdk.rglob("*") if sdk.is_dir() else []:
        relative = path.relative_to(sdk)
        name = relative.name.lower()
        if any(part in {".git", "build", "install", "log", "logs", "ops"} for part in relative.parts):
            forbidden_files.append(relative.as_posix())
        elif name in sensitive_names or name.endswith((".pem", ".key", ".p12", ".pfx")):
            forbidden_files.append(relative.as_posix())
        elif path.is_file() and name.startswith("libhal_adapter_") and name.endswith(".so"):
            forbidden_files.append(relative.as_posix())
    fastpath_missing = bool(missing_constants or missing_arrays)
    passed = not (
        missing_fields
        or validation_errors
        or missing_paths
        or mismatches
        or missing_groups
        or internal_mismatches
        or fastpath_missing
        or forbidden_files
    )
    if missing_fields or validation_errors:
        error_code = "PLUGIN_CONTRACT_INCOMPLETE"
    elif missing_paths:
        error_code = "PLATFORM_PLUGIN_SDK_MISSING"
    elif missing_groups:
        error_code = "PLATFORM_CAPABILITY_GROUP_MISSING"
    elif mismatches:
        error_code = "SDK_CONTRACT_MISMATCH"
    elif internal_mismatches:
        error_code = "SDK_INTERNAL_CONTRACT_MISMATCH"
    elif fastpath_missing:
        error_code = "SDK_FASTPATH_CONTRACT_MISSING"
    elif forbidden_files:
        error_code = "SDK_FORBIDDEN_FILES"
    else:
        error_code = ""
    inventory = {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "sdk_root": str(sdk),
        "sdk_version": sdk_version,
        "sdk_abi": int(sdk_abi_text) if sdk_abi_text.isdigit() else sdk_abi_text,
        "target_arch": contract.get("target_arch"),
        "platform_lib_dir": str(sdk / f"platform/lib/{arch_dir}"),
        "capability_group_refs": contract.get("capability_group_refs", []),
        "missing_paths": missing_paths,
        "missing_capability_groups": missing_groups,
        "forbidden_files": forbidden_files,
        "input_hashes": {str(path): sha256(path) for path in required if path.is_file()},
    }
    write_json(CONTEXTS / f"{args.context_id}.sdk_inventory.json", inventory)
    write_json(report_path, {
        **inventory,
        "status": "PASS" if passed else "FAIL",
        "error_code": error_code,
        "missing_contract_fields": missing_fields,
        "contract_errors": validation_errors,
        "mismatches": mismatches,
        "internal_contract_mismatches": internal_mismatches,
        "missing_fastpath_constants": missing_constants,
        "missing_fixed_arrays": missing_arrays,
        "bootstrap": args.bootstrap,
    })
    print(f"[AGENT_STAGE] stage=stage6a_sdk_check status={'success' if passed else 'fail'}")
    return 0 if passed else 8


if __name__ == "__main__":
    raise SystemExit(main())
