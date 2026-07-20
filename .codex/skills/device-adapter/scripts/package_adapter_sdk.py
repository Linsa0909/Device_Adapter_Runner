#!/usr/bin/env python3
"""Package the immutable Adapter SDK through the platform-owned script."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

from plugin_common import (
    ARTIFACTS,
    contract_errors,
    load_contract,
    missing_contract_fields,
    resolve_output_path,
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


def fail(report: Path, code: str, message: str, **details: object) -> int:
    write_json(report, {
        "schema_version": "1.0",
        "status": "FAIL",
        "error_code": code,
        "message": message,
        **details,
    })
    print(f"[AGENT_STAGE] stage=stage6a_sdk_package status=fail")
    return 8


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--platform-source-root")
    parser.add_argument("--platform-install-prefix")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    report_path = ARTIFACTS / f"{args.context_id}.sdk_package.json"
    log_path = ARTIFACTS / "logs" / f"{args.context_id}_sdk_package.log"

    try:
        contract = load_contract(args.context_id)
    except (FileNotFoundError, ValueError) as exc:
        return fail(report_path, "PLUGIN_CONTRACT_INVALID", str(exc))
    invalid = contract_errors(contract)
    missing_contract = missing_contract_fields(contract)
    if invalid or missing_contract:
        return fail(
            report_path,
            "PLUGIN_CONTRACT_INVALID",
            "Refusing to execute the platform SDK packager with an invalid contract",
            contract_errors=invalid,
            missing_contract_fields=missing_contract,
        )

    source_value = args.platform_source_root or contract.get("platform_source_root")
    install_value = args.platform_install_prefix or contract.get("platform_install_prefix")
    output_value = args.output_dir or contract.get("sdk_output_dir")
    missing = [
        name for name, value in (
            ("platform_source_root", source_value),
            ("platform_install_prefix", install_value),
            ("sdk_output_dir", output_value),
        ) if not value
    ]
    if missing:
        return fail(
            report_path,
            "SDK_PACKAGE_CONTRACT_INCOMPLETE",
            "SDK packaging requires platform source, installed HAL prefix, and output directory",
            missing_fields=missing,
        )

    platform_root = resolve_project_path(str(source_value)).resolve()
    install_prefix = resolve_project_path(str(install_value)).resolve()
    try:
        output_dir = resolve_output_path(str(output_value), "sdk_output_dir")
    except ValueError as exc:
        return fail(report_path, "SDK_OUTPUT_PATH_UNSAFE", str(exc))
    workspace_build = (Path.cwd() / "build").resolve()
    if not output_dir.is_relative_to(workspace_build):
        return fail(
            report_path,
            "SDK_OUTPUT_PATH_UNSAFE",
            "sdk_output_dir must stay under the project build/ directory",
            sdk_output_dir=str(output_dir),
        )

    package_script = platform_root / "src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh"
    plugin_api = platform_root / (
        "src/hardware_abstraction_layer/include/hardware_abstraction_layer/adapter/"
        "plugin_sdk/adapter_plugin_api.hpp"
    )
    sdk_cmake = platform_root / "src/hardware_abstraction_layer/adapter_sdk/cmake/HalAdapterSdkConfig.cmake"
    plugin_registry = platform_root / (
        "src/hardware_abstraction_layer/src/adapter/plugin_sdk/adapter_plugin_registry.cpp"
    )
    missing_platform_files = [
        str(path) for path in (package_script, plugin_api, sdk_cmake, plugin_registry) if not path.is_file()
    ]
    if missing_platform_files:
        code = "PLATFORM_SDK_PACKAGER_MISSING" if not package_script.is_file() else "PLATFORM_PLUGIN_SDK_SOURCE_INCOMPLETE"
        return fail(
            report_path,
            code,
            "The selected platform source is not a complete plugin-enabled HAL tree",
            platform_source_root=str(platform_root),
            missing_paths=missing_platform_files,
        )

    platform_libs = [install_prefix / "lib" / name for name in (
        "libhal_model.so", "libhal_media.so", "libhal_utils.so"
    )]
    missing_libs = [str(path) for path in platform_libs if not path.is_file()]
    if missing_libs:
        return fail(
            report_path,
            "PLATFORM_HAL_NOT_INSTALLED",
            "Build and install hardware_abstraction_layer before packaging the Adapter SDK",
            platform_install_prefix=str(install_prefix),
            missing_paths=missing_libs,
        )

    version = str(contract.get("sdk_version") or "")
    if not version:
        return fail(report_path, "SDK_PACKAGE_CONTRACT_INCOMPLETE", "sdk_version is required")
    expected_sdk = output_dir / f"hal_adapter_sdk_v{version}"
    declared_sdk = resolve_project_path(str(contract.get("sdk_root") or "")).resolve()
    if declared_sdk != expected_sdk:
        return fail(
            report_path,
            "SDK_ROOT_OUTPUT_MISMATCH",
            "sdk_root must identify the directory generated below sdk_output_dir",
            declared_sdk_root=str(declared_sdk),
            expected_sdk_root=str(expected_sdk),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "HAL_ADAPTER_SDK_VERSION": version,
        "HAL_ADAPTER_SDK_ARCH": sdk_arch_dir(str(contract.get("target_arch") or "")),
        "HAL_PLATFORM_PREFIX": str(install_prefix),
    })
    if contract.get("hal_interface_root"):
        env["HAL_INTERFACE_ROOT"] = str(resolve_project_path(str(contract["hal_interface_root"])).resolve())
    if contract.get("platform_model_dir"):
        env["MODEL_DIR"] = str(resolve_project_path(str(contract["platform_model_dir"])).resolve())
    command = ["bash", str(package_script), str(output_dir)]
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        process = subprocess.Popen(
            command,
            cwd=platform_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        exit_code = process.wait()

    tarball = output_dir / f"hal_adapter_sdk_v{version}.tar.gz"
    if exit_code or not expected_sdk.is_dir() or not tarball.is_file():
        return fail(
            report_path,
            "PLATFORM_SDK_PACKAGE_FAILED",
            "Platform package_adapter_sdk.sh did not produce the declared SDK and archive",
            exit_code=exit_code,
            native_packager=str(package_script),
            expected_sdk_root=str(expected_sdk),
            expected_tarball=str(tarball),
            log=str(log_path),
        )

    write_json(report_path, {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "status": "PASS",
        "error_code": "",
        "native_packager": str(package_script),
        "platform_source_root": str(platform_root),
        "platform_install_prefix": str(install_prefix),
        "sdk_root": str(expected_sdk),
        "sdk_version": version,
        "sdk_arch": env["HAL_ADAPTER_SDK_ARCH"],
        "compiler_triplet": contract["compiler_triplet"],
        "target_os": contract["target_os"],
        "tarball": str(tarball),
        "tarball_sha256": sha256(tarball),
        "command": command,
        "environment": {
            key: env[key] for key in (
                "HAL_ADAPTER_SDK_VERSION", "HAL_ADAPTER_SDK_ARCH", "HAL_PLATFORM_PREFIX",
                "HAL_INTERFACE_ROOT", "MODEL_DIR",
            ) if key in env
        },
        "exit_code": exit_code,
        "log": str(log_path),
    })
    print("[AGENT_STAGE] stage=stage6a_sdk_package status=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
