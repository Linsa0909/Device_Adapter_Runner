#!/usr/bin/env python3
"""Build and install one Adapter plugin on its declared target host."""

from __future__ import annotations

import argparse
import json
import shlex
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from plugin_common import (
    ARTIFACTS, load_contract, package_dir, plugin_source_dir,
    resolve_project_path, write_json,
)
from remote_package_adapter_sdk import digest, run_logged


def fail(report: Path, code: str, message: str, **details: object) -> int:
    write_json(report, {
        "schema_version": "1.0", "status": "FAIL", "error_code": code,
        "message": message, **details,
    })
    print("[AGENT_STAGE] stage=stage10c_target_plugin_build status=fail")
    return 14


def archive_tree(source: Path, output: Path) -> None:
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in {".git", "build", "install", "log", "logs", "__pycache__"} for part in relative.parts):
                continue
            archive.add(path, arcname=relative, recursive=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--host")
    parser.add_argument("--user")
    parser.add_argument("--port", type=int, default=22)
    args = parser.parse_args()
    report_path = ARTIFACTS / f"{args.context_id}.target_plugin_build.json"
    log_path = ARTIFACTS / "logs" / f"{args.context_id}_target_plugin_build.log"
    try:
        contract = load_contract(args.context_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return fail(report_path, "PLUGIN_CONTRACT_INVALID", str(exc))

    target_build = contract.get("target_build") or {}
    runtime_image = str(contract.get("runtime_image") or "")
    plugin_build_image = str(target_build.get("plugin_build_image") or "")
    if target_build.get("build_in_runtime_container") is not True or not plugin_build_image:
        return fail(
            report_path, "RUNTIME_CONTAINER_BUILD_REQUIRED",
            "Target plugin must be built inside the declared HAL runtime image",
        )
    host = args.host or target_build.get("host")
    user = args.user or target_build.get("user")
    if not host or not user:
        return fail(report_path, "TARGET_BUILD_ENDPOINT_MISSING", "SSH host and user are required")
    source = plugin_source_dir(contract)
    sdk = resolve_project_path(str(contract["sdk_root"])).resolve()
    if not source.is_dir() or not sdk.is_dir():
        return fail(
            report_path, "TARGET_PLUGIN_INPUT_MISSING",
            "Plugin source and checked HAL Adapter SDK must exist",
            plugin_source=str(source), sdk_root=str(sdk),
        )

    staging = ARTIFACTS / "target-plugin-input" / args.context_id
    staging.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source_archive = staging / f"plugin-source-{run_id}.tar.gz"
    sdk_archive = staging / f"hal-adapter-sdk-{run_id}.tar.gz"
    archive_tree(source, source_archive)
    archive_tree(sdk, sdk_archive)
    fingerprint = digest(source_archive).removeprefix("sha256:")[:16]
    remote_root = str(target_build.get("plugin_workspace") or f"/tmp/device-adapter-plugin-builds/{args.context_id}/{fingerprint}")
    target = f"{user}@{host}"
    ssh = ["ssh", "-p", str(args.port), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", target]
    scp = ["scp", "-P", str(args.port), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

    vendor_inputs: list[dict[str, object]] = list(target_build.get("vendor_inputs") or [])
    vendor_uploads: list[tuple[Path, str, str]] = []
    cmake_args = [str(value) for value in target_build.get("plugin_cmake_args") or []]
    for index, entry in enumerate(vendor_inputs):
        local = resolve_project_path(str(entry.get("path") or "")).resolve()
        cmake_var = str(entry.get("cmake_var") or "")
        if not local.is_file() or not cmake_var:
            return fail(
                report_path, "VENDOR_BUILD_INPUT_INVALID",
                "Every vendor input requires an existing archive and cmake_var",
                index=index, path=str(local), cmake_var=cmake_var,
            )
        remote_name = f"vendor-{index}{''.join(local.suffixes)}"
        extract_dir = f"{remote_root}/vendor/{index}"
        vendor_uploads.append((local, remote_name, extract_dir))
        cmake_args.append(f"-D{cmake_var}=/workspace/vendor/{index}")

    remote_script = f"""set -euo pipefail
mkdir -p {shlex.quote(remote_root)}/source {shlex.quote(remote_root)}/sdk {shlex.quote(remote_root)}/vendor {shlex.quote(remote_root)}/out
tar -xzf {shlex.quote(remote_root)}/plugin-source.tar.gz -C {shlex.quote(remote_root)}/source
tar -xzf {shlex.quote(remote_root)}/hal-adapter-sdk.tar.gz -C {shlex.quote(remote_root)}/sdk
"""
    for _local, remote_name, extract_dir in vendor_uploads:
        remote_script += f"mkdir -p {shlex.quote(extract_dir)}\ntar -xf {shlex.quote(remote_root)}/{shlex.quote(remote_name)} -C {shlex.quote(extract_dir)} --strip-components=1\n"
    container_script = f"""set -euo pipefail
test "$(uname -m)" = "aarch64"
{{
  echo plugin_build_image={shlex.quote(plugin_build_image)}
  echo runtime_image={shlex.quote(runtime_image)}
  uname -a
  cat /etc/os-release
  echo ROS_DISTRO=${{ROS_DISTRO:-}}
  gcc --version | head -n 1
  g++ --version | head -n 1
  cmake --version | head -n 1
  ldd --version | head -n 1
  python3 --version
}} > /workspace/out/container_environment.txt
cmake -S /workspace/source -B /workspace/out/build \\
  -DHAL_ADAPTER_SDK_ROOT=/workspace/sdk \\
  -DCMAKE_BUILD_TYPE=Release {' '.join(shlex.quote(value) for value in cmake_args)}
cmake --build /workspace/out/build -j"$(nproc)"
cmake --install /workspace/out/build --prefix /workspace/out/package
PLUGIN=/workspace/out/package/adapters/libhal_adapter_{str(contract['adapter_type'])}.so
test -f "$PLUGIN"
file "$PLUGIN" | grep -Eq 'ARM aarch64|AArch64'
nm -D --defined-only "$PLUGIN" | grep -q hal_get_adapter_sdk_abi_v1
nm -D --defined-only "$PLUGIN" | grep -q hal_get_adapter_plugin_v1
LD_LIBRARY_PATH=/workspace/out/package/deps:/workspace/sdk/platform/lib/aarch64-linux-gnu-gcc \\
  ldd "$PLUGIN" | tee /workspace/out/plugin-ldd.txt
! grep -q 'not found' /workspace/out/plugin-ldd.txt
"""
    remote_script += f"""
docker image inspect {shlex.quote(plugin_build_image)} --format '{{{{.Id}}}} {{{{.Architecture}}}}' > {shlex.quote(remote_root)}/out/plugin_build_image.txt
docker run --rm --network host \\
  -v {shlex.quote(remote_root)}/source:/workspace/source:ro \\
  -v {shlex.quote(remote_root)}/sdk:/workspace/sdk:ro \\
  -v {shlex.quote(remote_root)}/vendor:/workspace/vendor:ro \\
  -v {shlex.quote(remote_root)}/out:/workspace/out \\
  {shlex.quote(plugin_build_image)} bash -lc {shlex.quote(container_script)}
tar -czf {shlex.quote(remote_root)}/plugin-package.tar.gz -C {shlex.quote(remote_root)}/out/package .
cp {shlex.quote(remote_root)}/out/plugin-ldd.txt {shlex.quote(remote_root)}/plugin-ldd.txt
cp {shlex.quote(remote_root)}/out/container_environment.txt {shlex.quote(remote_root)}/container_environment.txt
"""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        commands = [
            [*ssh, f"mkdir -p {shlex.quote(remote_root)}"],
            [*scp, str(source_archive), f"{target}:{remote_root}/plugin-source.tar.gz"],
            [*scp, str(sdk_archive), f"{target}:{remote_root}/hal-adapter-sdk.tar.gz"],
        ]
        commands.extend([*scp, str(local), f"{target}:{remote_root}/{remote_name}"] for local, remote_name, _ in vendor_uploads)
        for command in commands:
            if run_logged(command, log):
                return fail(report_path, "TARGET_PLUGIN_TRANSFER_FAILED", "Target plugin input transfer failed", log=str(log_path))
        if run_logged([*ssh, "bash -s"], log, stdin=remote_script):
            return fail(report_path, "TARGET_PLUGIN_BUILD_FAILED", "Target plugin configure/build/install failed", log=str(log_path))

        local_archive = staging / f"plugin-package-{run_id}.tar.gz"
        if run_logged([*scp, f"{target}:{remote_root}/plugin-package.tar.gz", str(local_archive)], log):
            return fail(report_path, "TARGET_PLUGIN_FETCH_FAILED", "Plugin package fetch failed", log=str(log_path))
        ldd_report = ARTIFACTS / f"{args.context_id}.target_plugin_ldd.txt"
        if run_logged([*scp, f"{target}:{remote_root}/plugin-ldd.txt", str(ldd_report)], log):
            return fail(report_path, "TARGET_PLUGIN_EVIDENCE_FETCH_FAILED", "Plugin ldd evidence fetch failed")
        container_environment = ARTIFACTS / f"{args.context_id}.container_environment.txt"
        if run_logged([*scp, f"{target}:{remote_root}/container_environment.txt", str(container_environment)], log):
            return fail(report_path, "TARGET_PLUGIN_EVIDENCE_FETCH_FAILED", "Container environment evidence fetch failed")

    package = package_dir(contract)
    package.mkdir(parents=True, exist_ok=True)
    with tarfile.open(local_archive, "r:gz") as archive:
        members = archive.getmembers()
        if any(member.name.startswith("/") or ".." in Path(member.name).parts for member in members):
            return fail(report_path, "TARGET_PLUGIN_ARCHIVE_UNSAFE", "Plugin archive contains unsafe paths")
        archive.extractall(package)
    write_json(report_path, {
        "schema_version": "1.0", "context_id": args.context_id, "status": "PASS",
        "host": host, "user": user, "target_arch": "aarch64",
        "remote_workspace": remote_root, "source_archive_sha256": digest(source_archive),
        "sdk_archive_sha256": digest(sdk_archive),
        "vendor_inputs": [{"path": str(local), "sha256": digest(local)} for local, _, _ in vendor_uploads],
        "package_dir": str(package), "package_archive": str(local_archive),
        "package_archive_sha256": digest(local_archive), "ldd_evidence": str(ldd_report),
        "runtime_image": runtime_image, "plugin_build_image": plugin_build_image,
        "container_environment": str(container_environment),
        "log": str(log_path),
    })
    print("[AGENT_STAGE] stage=stage10c_target_plugin_build status=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
