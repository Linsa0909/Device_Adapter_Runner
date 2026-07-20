#!/usr/bin/env python3
"""Transfer a formal Adapter plugin package to the board runtime tree."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
from pathlib import Path

from plugin_common import ARTIFACTS, CONTEXTS, load_contract, write_json


def normalize_arch(value: str) -> str:
    value = value.strip().lower()
    if value in {"arm64", "aarch64"}:
        return "aarch64"
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    return value


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--runtime-root", default="/etc/vega/access/runtime")
    args, _ = parser.parse_known_args()
    contract = load_contract(args.context_id)
    archive = ARTIFACTS / f"{args.context_id}_adapter_plugin.tar.gz"
    report_path = ARTIFACTS / f"{args.context_id}.remote_deploy.json"
    deployment = CONTEXTS / f"{args.context_id}.deployment.yaml"
    if not archive.is_file():
        write_json(report_path, {"status": "FAIL", "error_code": "PLUGIN_PACKAGE_MISSING", "archive": str(archive)})
        return 4
    if not deployment.is_file():
        write_json(report_path, {"status": "FAIL", "error_code": "TEST_DEPLOYMENT_MISSING", "path": str(deployment)})
        return 4

    target = f"{args.user}@{args.host}"
    remote_temp = f"/tmp/device-adapter-{args.context_id}"
    remote_archive = f"{remote_temp}/{archive.name}"
    remote_deployment_temp = f"{remote_temp}/{deployment.name}"
    archive_sha = sha256(archive)
    deployment_sha = sha256(deployment)
    upload_manifest = {
        "schema_version": "1.0", "context_id": args.context_id,
        "files": [
            {"local_path": str(archive), "remote_temp_path": remote_archive, "sha256": archive_sha},
            {"local_path": str(deployment), "remote_temp_path": remote_deployment_temp, "sha256": deployment_sha},
        ],
    }
    write_json(ARTIFACTS / f"{args.context_id}.upload_manifest.json", upload_manifest)
    root = shlex.quote(args.runtime_root)
    remote_archive_q = shlex.quote(remote_archive)
    image_q = shlex.quote(str(contract["runtime_image"]))
    plugin_q = shlex.quote(f"{args.runtime_root}/adapters/libhal_adapter_{contract['adapter_type']}.so")
    config_q = shlex.quote(f"{args.runtime_root}/config/{contract['adapter_type']}.json")
    remote_deployment = f"{args.runtime_root}/deployment/{contract['adapter_type']}-only.yaml"
    prepare = (f"mkdir -p {shlex.quote(remote_temp)} {root}/adapters {root}/config "
               f"{root}/deps {root}/model/devices {root}/deployment")
    verify_uploads = (
        f"test \"$(sha256sum {shlex.quote(remote_archive)} | awk '{{print $1}}')\" = {shlex.quote(archive_sha)} && "
        f"test \"$(sha256sum {shlex.quote(remote_deployment_temp)} | awk '{{print $1}}')\" = {shlex.quote(deployment_sha)}"
    )
    commands = [
        ["ssh", target, prepare],
        ["scp", str(archive), f"{target}:{remote_archive}"],
        ["scp", str(deployment), f"{target}:{remote_deployment_temp}"],
        ["ssh", target, verify_uploads],
        ["ssh", target, f"tar -xzf {remote_archive_q} -C {root} && cp {shlex.quote(remote_deployment_temp)} {shlex.quote(remote_deployment)}"],
        ["ssh", target, f"docker image inspect {image_q} >/dev/null"],
        ["ssh", target, "uname -m"],
        ["ssh", target, f"test -f {plugin_q}"],
        ["ssh", target, f"test -f {config_q}"],
        ["ssh", target, f"test -f {shlex.quote(remote_deployment)}"],
        ["ssh", target, f"rm -rf {shlex.quote(remote_temp)}"],
    ]
    results = []
    for item in commands:
        result = run(item)
        results.append({"command": item, "exit_code": result.returncode, "output": result.stdout[-4000:]})
        if result.returncode:
            break
    if len(results) >= 4 and results[3]["exit_code"] != 0:
        run(["ssh", target, f"rm -rf {shlex.quote(remote_temp)}"])
        write_json(report_path, {"status":"FAIL","error_code":"UPLOAD_SHA256_MISMATCH",
                   "host":args.host,"upload_manifest":str(ARTIFACTS/f"{args.context_id}.upload_manifest.json"),"commands":results})
        return 16
    actual_arch = normalize_arch(str(results[6]["output"])) if len(results) > 6 else ""
    expected_arch = normalize_arch(str(contract["target_arch"]))
    passed = (
        len(results) == len(commands)
        and all(item["exit_code"] == 0 for item in results)
        and actual_arch == expected_arch
    )
    write_json(report_path, {
        "status": "PASS" if passed else "FAIL",
        "error_code": "" if passed else "REMOTE_PLUGIN_DEPLOY_FAILED",
        "host": args.host, "user": args.user, "runtime_root": args.runtime_root,
        "runtime_image": contract["runtime_image"], "commands": results,
        "upload_manifest": str(ARTIFACTS / f"{args.context_id}.upload_manifest.json"),
        "expected_arch": expected_arch, "actual_arch": actual_arch,
        "container_mounts": {
            f"{args.runtime_root}/adapters": "/hal-runtime/adapters",
            f"{args.runtime_root}/config": "/hal-runtime/config",
            f"{args.runtime_root}/deps": "/hal-runtime/deps",
            f"{args.runtime_root}/model/devices": "/hal-runtime/model/devices",
            f"{args.runtime_root}/deployment": "/hal-runtime/deployment",
        },
        "hal_rebuilt": False, "process_started": False,
    })
    return 0 if passed else 16


if __name__ == "__main__":
    raise SystemExit(main())
