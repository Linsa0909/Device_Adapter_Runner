#!/usr/bin/env python3
"""Prepare the SDK-build subset of a runtime plugin contract from platform facts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from plugin_common import CONTEXTS, load_json, write_json


def match(text: str, pattern: str, label: str) -> str:
    result = re.search(pattern, text, re.M)
    if not result:
        raise ValueError(f"platform source does not declare {label}")
    return result.group(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--platform-source-root")
    parser.add_argument("--host")
    parser.add_argument("--user")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    contract_path = CONTEXTS / f"{args.context_id}.plugin_contract.json"
    if not contract_path.is_file():
        print("[AGENT_STAGE] stage=stage4a_sdk_contract_prepare status=fail")
        return 2
    contract = load_json(contract_path)
    candidates = [
        Path(args.platform_source_root).expanduser() if args.platform_source_root else None,
        Path(str(contract.get("platform_source_root") or "")).expanduser(),
        Path.cwd(),
    ]
    source = next(
        (path.resolve() for path in candidates if path and (
            path / "src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh"
        ).is_file()),
        None,
    )
    if source is None:
        print("[AGENT_STAGE] stage=stage4a_sdk_contract_prepare status=fail")
        return 3
    cmake_path = source / "src/hardware_abstraction_layer/adapter_sdk/cmake/HalAdapterSdkConfig.cmake"
    api_path = source / (
        "src/hardware_abstraction_layer/include/hardware_abstraction_layer/adapter/"
        "plugin_sdk/adapter_plugin_api.hpp"
    )
    cmake = cmake_path.read_text(encoding="utf-8")
    api = api_path.read_text(encoding="utf-8")
    sdk_version = match(cmake, r'HAL_ADAPTER_SDK_VERSION\s+"([^"]+)"', "SDK version")
    sdk_abi = int(match(api, r"kAdapterSdkAbiVersion\s*=\s*([0-9]+)", "SDK ABI"))
    plugin_abi = int(match(api, r"kAdapterPluginAbiVersion\s*=\s*([0-9]+)", "plugin ABI"))
    output_dir = str(contract.get("sdk_output_dir") or "build/sdk")
    target_build = dict(contract.get("target_build") or {})
    if args.host:
        target_build["host"] = args.host
    if args.user:
        target_build["user"] = args.user
    target_build.setdefault("user", "root")
    target_build["workspace"] = args.workspace or target_build.get("workspace") or (
        f"/tmp/device-adapter/{args.context_id}/<fingerprint>"
    )
    target_build["ros_distro"] = "humble"
    target_build["compiler_triplet"] = "aarch64-linux-gnu-gcc"
    target_build["build_in_runtime_container"] = True
    target_build.setdefault(
        "sdk_build_image", "registry.ghostcloud.cn/integration/hal_dev:v1.0"
    )
    target_build.setdefault("plugin_build_image", target_build["sdk_build_image"])
    contract.update({
        "platform_source_root": str(source),
        "platform_install_prefix": (
            f"/tmp/device-adapter/{args.context_id}/<fingerprint>/install/"
            "hardware_abstraction_layer"
        ),
        "sdk_output_dir": output_dir,
        "sdk_root": f"{output_dir.rstrip('/')}/hal_adapter_sdk_v{sdk_version}",
        "sdk_version": sdk_version,
        "sdk_abi": sdk_abi,
        "plugin_abi": plugin_abi,
        "target_arch": contract.get("target_arch") or "aarch64",
        "compiler_triplet": "aarch64-linux-gnu-gcc",
        "target_build": target_build,
        "private_config": contract.get("private_config") or {
            "path": f"config/{contract['adapter_type']}.json", "schema_version": "1.0"
        },
    })
    resolved = {"sdk_root", "platform_source_root", "platform_install_prefix", "sdk_output_dir", "sdk_version", "sdk_abi", "plugin_abi"}
    contract["unknown_fields"] = [value for value in contract.get("unknown_fields", []) if value not in resolved]
    write_json(contract_path, contract)
    print("[AGENT_STAGE] stage=stage4a_sdk_contract_prepare status=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
