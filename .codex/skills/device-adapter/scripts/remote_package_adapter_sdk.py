#!/usr/bin/env python3
"""Build HAL and package its immutable Adapter SDK on the target architecture."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, resolve_output_path, resolve_project_path, write_json


PLATFORM_LIBS = ("libhal_model.so", "libhal_media.so", "libhal_utils.so")
EXCLUDED_PARTS = {".git", "build", "install", "log", "logs", "__pycache__"}
EXCLUDED_PREFIXES = ("ops/artifacts",)
SENSITIVE_NAMES = {
    "id_rsa", "id_ed25519", "credentials", ".env", ".netrc",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def fail(report_path: Path, code: str, message: str, **details: object) -> int:
    write_json(report_path, {
        "schema_version": "1.0",
        "status": "FAIL",
        "error_code": code,
        "message": message,
        **details,
    })
    print("[AGENT_STAGE] stage=stage6a_target_hal_build status=fail")
    return 8


def run_logged(command: list[str], log, *, stdin: str | None = None) -> int:
    log.write("$ " + " ".join(shlex.quote(item) for item in command) + "\n")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output, _ = process.communicate(stdin)
    print(output, end="")
    log.write(output)
    return process.returncode


def git_text(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={source_root}", "-C", str(source_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def include_source(path: Path, source_root: Path) -> bool:
    relative = path.relative_to(source_root)
    posix = relative.as_posix()
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if any(posix == prefix or posix.startswith(prefix + "/") for prefix in EXCLUDED_PREFIXES):
        return False
    name = relative.name.lower()
    if name in SENSITIVE_NAMES or name.endswith((".pem", ".key", ".p12", ".pfx")):
        return False
    if "hal_adapter_sdk_v" in posix and (name.endswith(".tar.gz") or path.is_dir()):
        return False
    return True


def source_tree_sha256(paths: list[Path], source_root: Path) -> str:
    value = hashlib.sha256()
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source_root).as_posix()
        value.update(relative.encode("utf-8") + b"\0")
        value.update(digest(path).encode("ascii") + b"\n")
    return "sha256:" + value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--host")
    parser.add_argument("--user")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--remote-root", default="/tmp/device-adapter-sdk-builds")
    args = parser.parse_args()

    report_path = ARTIFACTS / f"{args.context_id}.target_sdk_package.json"
    log_path = ARTIFACTS / "logs" / f"{args.context_id}_target_sdk_package.log"
    try:
        contract = load_contract(args.context_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return fail(report_path, "PLUGIN_CONTRACT_INVALID", str(exc))

    remote = contract.get("target_build") or {}
    host = args.host or remote.get("host")
    user = args.user or remote.get("user")
    if not host or not user:
        return fail(
            report_path,
            "TARGET_BUILD_ENDPOINT_MISSING",
            "target-sdk-package requires SSH host and user",
        )
    source_value = contract.get("platform_source_root")
    output_value = contract.get("sdk_output_dir")
    if not source_value or not output_value:
        return fail(
            report_path,
            "SDK_PACKAGE_CONTRACT_INCOMPLETE",
            "platform_source_root and sdk_output_dir are required",
        )
    source_root = resolve_project_path(str(source_value)).resolve()
    try:
        output_dir = resolve_output_path(str(output_value), "sdk_output_dir")
    except ValueError as exc:
        return fail(report_path, "SDK_OUTPUT_PATH_UNSAFE", str(exc))
    native_packager = source_root / "src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh"
    if not native_packager.is_file():
        return fail(
            report_path,
            "PLATFORM_SDK_PACKAGER_MISSING",
            "plugin-enabled platform package_adapter_sdk.sh was not found",
            expected_path=str(native_packager),
        )

    version = str(contract.get("sdk_version") or "")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = f"{user}@{host}"
    ssh = [
        "ssh", "-p", str(args.port), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new", target,
    ]
    scp = [
        "scp", "-P", str(args.port), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    staging = ARTIFACTS / "sdk-source" / args.context_id
    staging.mkdir(parents=True, exist_ok=True)
    source_archive = staging / f"platform-{run_id}.tar.gz"
    source_paths = sorted(
        (path for path in source_root.rglob("*") if include_source(path, source_root)),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    tree_hash = source_tree_sha256(source_paths, source_root)
    fingerprint = tree_hash.removeprefix("sha256:")[:16]
    configured_workspace = str(remote.get("workspace") or "").strip()
    configured_workspace = configured_workspace.replace("<fingerprint>", fingerprint).replace("{fingerprint}", fingerprint)
    remote_run = configured_workspace or f"{args.remote_root.rstrip('/')}/{args.context_id}/{fingerprint}"
    with tarfile.open(source_archive, "w:gz") as archive:
        for path in source_paths:
            relative = path.relative_to(source_root)
            archive.add(path, arcname=relative, recursive=False)

    git_diff = git_text(source_root, "diff", "--binary", "HEAD")
    source_provenance = {
        "git_commit": git_text(source_root, "rev-parse", "HEAD"),
        "worktree_dirty": bool(git_text(source_root, "status", "--porcelain", "-uall")),
        "git_diff_sha256": "sha256:" + hashlib.sha256(git_diff.encode("utf-8")).hexdigest(),
        "submodules": git_text(source_root, "submodule", "status", "--recursive").splitlines(),
        "source_tree_sha256": tree_hash,
        "source_archive_sha256": digest(source_archive),
        "source_file_count": sum(path.is_file() for path in source_paths),
        "excludes": sorted(EXCLUDED_PARTS) + list(EXCLUDED_PREFIXES) + sorted(SENSITIVE_NAMES)
        + ["*.pem", "*.key", "*.p12", "*.pfx", "hal_adapter_sdk_v*"],
    }
    provenance_path = staging / f"source_provenance-{run_id}.json"
    write_json(provenance_path, source_provenance)

    ros_setup = str(remote.get("ros_setup") or "/opt/ros/humble/setup.bash")
    cmake_args = list(remote.get("cmake_args") or ["-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=OFF"])
    sdk_build_image = str(remote.get("sdk_build_image") or "")
    if not sdk_build_image:
        return fail(
            report_path,
            "SDK_BUILD_IMAGE_MISSING",
            "target_build.sdk_build_image is required for target-sdk-package",
        )
    container_script = f"""set -euo pipefail
source {shlex.quote(ros_setup)}
{{
  echo "sdk_build_image={sdk_build_image}"
  echo "uname_m=$(uname -m)"
  echo "ros_distro=${{ROS_DISTRO:-}}"
  echo "os_release_begin"
  cat /etc/os-release
  echo "os_release_end"
  gcc --version | head -n 1
  g++ --version | head -n 1
  cmake --version | head -n 1
  colcon version-check 2>&1 || colcon --help | head -n 1
  ldd --version | head -n 1
  python3 --version
}} > {shlex.quote(remote_run)}/remote_environment.txt 2>&1
test "$(uname -m)" = "aarch64"
test "${{ROS_DISTRO:-}}" = "humble"
cd {shlex.quote(remote_run)}/source
colcon build --packages-up-to hardware_abstraction_layer --cmake-clean-cache \\
  --build-base {shlex.quote(remote_run)}/build \\
  --install-base {shlex.quote(remote_run)}/install \\
  --log-base {shlex.quote(remote_run)}/log \\
  --cmake-args {' '.join(shlex.quote(str(item)) for item in cmake_args)}
PREFIX={shlex.quote(remote_run)}/install/hardware_abstraction_layer
for lib in {' '.join(PLATFORM_LIBS)}; do
  test -f "$PREFIX/lib/$lib"
  {{
    echo "library=$lib"
    echo "path=$PREFIX/lib/$lib"
    stat -c 'file_type=%F' "$PREFIX/lib/$lib"
    readlink "$PREFIX/lib/$lib" 2>/dev/null | sed 's/^/symlink_target=/' || true
    sha256sum "$PREFIX/lib/$lib"
    file "$PREFIX/lib/$lib"
    echo "readelf -h"
    readelf -h "$PREFIX/lib/$lib"
    echo "readelf -d"
    readelf -d "$PREFIX/lib/$lib"
  }} >> {shlex.quote(remote_run)}/platform_library_audit.txt
  file "$PREFIX/lib/$lib" | grep -Eq 'ARM aarch64|AArch64'
done
find {shlex.quote(remote_run)}/source -type f -print0 | sort -z | xargs -0 sha256sum \
  | sha256sum | awk '{{print "source_tree_sha256=sha256:" $1}}' \
  > {shlex.quote(remote_run)}/remote_source_fingerprint.txt
HAL_ADAPTER_SDK_VERSION={shlex.quote(version)} \\
HAL_ADAPTER_SDK_ARCH=aarch64-linux-gnu-gcc \\
HAL_PLATFORM_PREFIX="$PREFIX" \\
bash src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh {shlex.quote(remote_run)}/sdk-output
test -s {shlex.quote(remote_run)}/sdk-output/hal_adapter_sdk_v{shlex.quote(version)}.tar.gz
SDK={shlex.quote(remote_run)}/sdk-output/hal_adapter_sdk_v{shlex.quote(version)}
cmake -S "$SDK/examples/minimal_adapter" -B {shlex.quote(remote_run)}/sdk-minimal-build \\
  -DHAL_ADAPTER_SDK_ROOT="$SDK" \\
  -DHAL_ADAPTER_SDK_ARCH=aarch64-linux-gnu-gcc \\
  -DCMAKE_INSTALL_PREFIX={shlex.quote(remote_run)}/sdk-minimal-install \\
  -DCMAKE_BUILD_TYPE=Release
cmake --build {shlex.quote(remote_run)}/sdk-minimal-build --target install --parallel
MINIMAL={shlex.quote(remote_run)}/sdk-minimal-install/adapters/libhal_adapter_minimal.so
{{
  file "$MINIMAL"
  nm -D --defined-only "$MINIMAL"
  readelf -d "$MINIMAL"
  LD_LIBRARY_PATH={shlex.quote(remote_run)}/sdk-minimal-install/deps:"$SDK/platform/lib/aarch64-linux-gnu-gcc" ldd "$MINIMAL"
}} > {shlex.quote(remote_run)}/target_sdk_validation.txt
grep -q hal_get_adapter_sdk_abi_v1 {shlex.quote(remote_run)}/target_sdk_validation.txt
grep -q hal_get_adapter_plugin_v1 {shlex.quote(remote_run)}/target_sdk_validation.txt
grep -q '\$ORIGIN/../deps' {shlex.quote(remote_run)}/target_sdk_validation.txt
! grep -q 'not found' {shlex.quote(remote_run)}/target_sdk_validation.txt
"""
    remote_script = f"""set -euo pipefail
mkdir -p {shlex.quote(remote_run)}/source {shlex.quote(remote_run)}/sdk-output
tar -xzf {shlex.quote(remote_run)}/platform.tar.gz -C {shlex.quote(remote_run)}/source
docker image inspect {shlex.quote(sdk_build_image)} \
  --format '{{{{.Id}}}} {{{{.Architecture}}}}' > {shlex.quote(remote_run)}/sdk_container_image.txt
grep -Eq 'arm64|aarch64' {shlex.quote(remote_run)}/sdk_container_image.txt
docker run --rm --network host \
  -v {shlex.quote(remote_run)}:{shlex.quote(remote_run)} \
  {shlex.quote(sdk_build_image)} \
  bash -lc {shlex.quote(container_script)}
"""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        steps = [
            ("stage6a_target_transfer", [*ssh, f"mkdir -p {shlex.quote(remote_run)}"], None),
            ("stage6a_target_transfer", [*scp, str(source_archive), f"{target}:{remote_run}/platform.tar.gz"], None),
            ("stage6a_target_transfer", [*scp, str(provenance_path), f"{target}:{remote_run}/source_provenance.json"], None),
            ("stage6a_target_hal_build", [*ssh, "bash -s"], remote_script),
        ]
        for stage, command, stdin in steps:
            print(f"[AGENT_STAGE] stage={stage} status=start")
            exit_code = run_logged(command, log, stdin=stdin)
            if exit_code:
                return fail(
                    report_path,
                    "TARGET_SDK_BUILD_FAILED",
                    f"Remote target SDK workflow failed during {stage}",
                    failed_stage=stage,
                    exit_code=exit_code,
                    remote_run=remote_run,
                    log=str(log_path),
                )
            print(f"[AGENT_STAGE] stage={stage} status=success")
        output_dir.mkdir(parents=True, exist_ok=True)
        tarball = output_dir / f"hal_adapter_sdk_v{version}.tar.gz"
        exit_code = run_logged(
            [*scp, f"{target}:{remote_run}/sdk-output/{tarball.name}", str(tarball)], log
        )
        if exit_code:
            return fail(
                report_path,
                "TARGET_SDK_FETCH_FAILED",
                "Target SDK was built but could not be fetched",
                exit_code=exit_code,
                remote_run=remote_run,
                log=str(log_path),
            )
        evidence = {
            "remote_environment": ARTIFACTS / f"{args.context_id}.remote_environment.txt",
            "platform_library_audit": ARTIFACTS / f"{args.context_id}.platform_library_audit.txt",
            "remote_source_fingerprint": ARTIFACTS / f"{args.context_id}.remote_source_fingerprint.txt",
            "target_sdk_validation": ARTIFACTS / f"{args.context_id}.target_sdk_validation.txt",
            "sdk_container_image": ARTIFACTS / f"{args.context_id}.sdk_container_image.txt",
        }
        for remote_name, local_path in evidence.items():
            exit_code = run_logged(
                [*scp, f"{target}:{remote_run}/{remote_name}.txt", str(local_path)], log
            )
            if exit_code:
                return fail(
                    report_path,
                    "TARGET_EVIDENCE_FETCH_FAILED",
                    f"Target evidence fetch failed: {remote_name}",
                )

    sdk_root = output_dir / f"hal_adapter_sdk_v{version}"
    with tarfile.open(tarball, "r:gz") as archive:
        archive.extractall(output_dir, filter="data")
    if not sdk_root.is_dir():
        return fail(report_path, "TARGET_SDK_ARCHIVE_INVALID", "Fetched SDK archive has no expected root")
    write_json(report_path, {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "status": "PASS",
        "host": host,
        "user": user,
        "target_arch": "aarch64",
        "build_execution": "remote_docker_container",
        "sdk_build_image": sdk_build_image,
        "remote_run": remote_run,
        "platform_libraries": list(PLATFORM_LIBS),
        "source_provenance": source_provenance,
        "remote_environment": str(evidence["remote_environment"]),
        "platform_library_audit": str(evidence["platform_library_audit"]),
        "remote_source_fingerprint": str(evidence["remote_source_fingerprint"]),
        "target_sdk_validation": str(evidence["target_sdk_validation"]),
        "sdk_container_image": str(evidence["sdk_container_image"]),
        "sdk_root": str(sdk_root),
        "tarball": str(tarball),
        "tarball_sha256": digest(tarball),
        "log": str(log_path),
    })
    print("[AGENT_STAGE] stage=stage6a_target_hal_build status=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
