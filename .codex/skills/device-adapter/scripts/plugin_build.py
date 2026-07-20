#!/usr/bin/env python3
"""Build and install one HAL Adapter plugin against its immutable SDK."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, package_dir, plugin_source_dir, resolve_output_path, resolve_project_path, write_json


def run(command: list[str], log: Path) -> int:
    with log.open("a", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            stream.write(line)
        return process.wait()


def normalized_arch(value: str) -> str:
    value = value.lower().replace("-", "_")
    if value in {"arm64", "aarch64", "linux_arm64"}:
        return "aarch64"
    if value in {"amd64", "x86_64", "linux_amd64"}:
        return "x86_64"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    contract = load_contract(args.context_id)
    sdk = resolve_project_path(str(contract["sdk_root"]))
    source = plugin_source_dir(contract)
    package = package_dir(contract)
    build = resolve_output_path(str(contract.get("build_dir") or f"build/{contract['adapter_type']}-plugin"), "build_dir")
    toolchain = str(contract.get("cmake_toolchain_file") or "")
    target = normalized_arch(str(contract["target_arch"]))
    host = normalized_arch(platform.machine())
    report_path = ARTIFACTS / f"{args.context_id}.plugin_build.json"
    log = ARTIFACTS / "logs" / f"{args.context_id}_plugin_build.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    if target != host and not toolchain:
        write_json(report_path, {
            "status": "BLOCKED", "error_code": "CROSS_TOOLCHAIN_UNDECLARED",
            "host_arch": host, "target_arch": target,
            "message": "Build on the target architecture or declare cmake_toolchain_file.",
        })
        return 13
    if args.clean and build.exists():
        import shutil
        shutil.rmtree(build)

    configure = [
        "cmake", "-S", str(source), "-B", str(build),
        f"-DHAL_ADAPTER_SDK_ROOT={sdk}", "-DCMAKE_BUILD_TYPE=Release",
    ]
    if toolchain:
        configure.append(f"-DCMAKE_TOOLCHAIN_FILE={resolve_project_path(toolchain)}")
    commands = [
        configure,
        ["cmake", "--build", str(build), "-j", str(max(1, os.cpu_count() or 1))],
        ["cmake", "--install", str(build), "--prefix", str(package)],
    ]
    exit_codes: list[int] = []
    for item in commands:
        rc = run(item, log)
        exit_codes.append(rc)
        if rc:
            break
    passed = len(exit_codes) == 3 and all(code == 0 for code in exit_codes)
    write_json(report_path, {
        "status": "PASS" if passed else "FAIL",
        "error_code": "" if passed else "PLUGIN_BUILD_FAILED",
        "source_dir": str(source), "build_dir": str(build), "package_dir": str(package),
        "sdk_root": str(sdk), "host_arch": host, "target_arch": target,
        "commands": commands[:len(exit_codes)], "exit_codes": exit_codes, "log": str(log),
    })
    print(f"[AGENT_STAGE] stage=stage10c_plugin_build status={'success' if passed else 'fail'}")
    return 0 if passed else 14


if __name__ == "__main__":
    raise SystemExit(main())
