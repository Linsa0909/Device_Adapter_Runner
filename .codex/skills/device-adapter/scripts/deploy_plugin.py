#!/usr/bin/env python3
"""Transfer a formal Adapter plugin package to the board runtime tree."""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, write_json


def normalize_arch(value: str) -> str:
    value = value.strip().lower()
    if value in {"arm64", "aarch64"}:
        return "aarch64"
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    return value


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


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
    if not archive.is_file():
        write_json(report_path, {"status": "FAIL", "error_code": "PLUGIN_PACKAGE_MISSING", "archive": str(archive)})
        return 4

    target = f"{args.user}@{args.host}"
    remote_archive = f"/tmp/{archive.name}"
    root = shlex.quote(args.runtime_root)
    remote_archive_q = shlex.quote(remote_archive)
    image_q = shlex.quote(str(contract["runtime_image"]))
    plugin_q = shlex.quote(f"{args.runtime_root}/adapters/libhal_adapter_{contract['adapter_type']}.so")
    config_q = shlex.quote(f"{args.runtime_root}/config/{contract['adapter_type']}.json")
    prepare = f"mkdir -p {root}/adapters {root}/config {root}/deps {root}/model/devices"
    commands = [
        ["ssh", target, prepare],
        ["scp", str(archive), f"{target}:{remote_archive}"],
        ["ssh", target, f"tar -xzf {remote_archive_q} -C {root}"],
        ["ssh", target, f"docker image inspect {image_q} >/dev/null"],
        ["ssh", target, "uname -m"],
        ["ssh", target, f"test -f {plugin_q}"],
        ["ssh", target, f"test -f {config_q}"],
    ]
    results = []
    for item in commands:
        result = run(item)
        results.append({"command": item, "exit_code": result.returncode, "output": result.stdout[-4000:]})
        if result.returncode:
            break
    actual_arch = normalize_arch(str(results[4]["output"])) if len(results) > 4 else ""
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
        "expected_arch": expected_arch, "actual_arch": actual_arch,
        "container_mounts": {
            f"{args.runtime_root}/adapters": "/hal-runtime/adapters",
            f"{args.runtime_root}/config": "/hal-runtime/config",
            f"{args.runtime_root}/deps": "/hal-runtime/deps",
            f"{args.runtime_root}/model/devices": "/hal-runtime/model/devices",
        },
        "hal_rebuilt": False, "process_started": False,
    })
    return 0 if passed else 16


if __name__ == "__main__":
    raise SystemExit(main())
