#!/usr/bin/env python3
"""Create the formal runtime Adapter plugin delivery archive."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import tarfile
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, package_dir, readme_contract_errors, write_json


PLATFORM_LIBS = {"libhal_model.so", "libhal_media.so", "libhal_utils.so"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    contract = load_contract(args.context_id)
    adapter_type = str(contract["adapter_type"])
    root = package_dir(contract)
    forbidden_report = ARTIFACTS / f"{args.context_id}.forbidden_files.json"

    forbidden: list[dict[str, str]] = []
    if root.exists():
        for path in root.rglob("*"):
            rel = path.relative_to(root).as_posix()
            if path.is_dir() and (path.name == "capability_groups" or (root / "adapters") in path.parents):
                forbidden.append({"path": rel, "reason": "forbidden directory in formal plugin package"})
            if path.is_file() and (path.name.endswith(".capability.yaml") or any(path.name.startswith(name) for name in PLATFORM_LIBS)):
                forbidden.append({"path": rel, "reason": "platform-owned capability or ABI library"})

    private_config = contract.get("private_config") or {}
    config_required = private_config.get("required") is True
    config_path = root / f"config/{adapter_type}.json"
    required = [
        root / f"adapters/libhal_adapter_{adapter_type}.so",
        root / f"model/devices/{adapter_type}.device.yaml",
        root / "README.md",
    ]
    if config_required:
        required.append(config_path)
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    readme_errors = readme_contract_errors(root / "README.md", contract)
    status = "PASS" if root.is_dir() and not forbidden and not missing and not readme_errors else "FAIL"
    write_json(ARTIFACTS / f"{args.context_id}.readme_validation.json", {
        "status": "PASS" if not readme_errors else "FAIL",
        "sdk_version": contract.get("sdk_version"),
        "sdk_abi": contract.get("sdk_abi"),
        "errors": readme_errors,
    })
    write_json(forbidden_report, {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "status": status,
        "forbidden": forbidden,
        "missing_required": missing,
        "readme_errors": readme_errors,
    })
    if status != "PASS":
        return 9

    try:
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict) or not config.get("schema_version") or not isinstance(config.get("instances"), list):
                raise ValueError("config requires schema_version and instances[]")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        write_json(forbidden_report, {
            "schema_version": "1.0", "context_id": args.context_id, "status": "FAIL",
            "forbidden": [{"path": f"config/{adapter_type}.json", "reason": f"invalid private config: {exc}"}],
            "missing_required": [],
        })
        return 9

    files = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    exact = {
        "README.md",
        f"adapters/libhal_adapter_{adapter_type}.so",
        f"model/devices/{adapter_type}.device.yaml",
    }
    if config_path.is_file():
        exact.add(f"config/{adapter_type}.json")
    invalid = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in exact or (rel.startswith("deps/") and fnmatch.fnmatch(path.name, "*.so*")):
            continue
        invalid.append(rel)
    if invalid:
        write_json(forbidden_report, {
            "schema_version": "1.0", "context_id": args.context_id, "status": "FAIL",
            "forbidden": [{"path": item, "reason": "outside formal delivery contract"} for item in invalid],
            "missing_required": [],
        })
        return 9

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    archive = ARTIFACTS / f"{args.context_id}_adapter_plugin.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    manifest = {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "delivery_mode": "runtime_plugin",
        "archive": str(archive),
        "archive_sha256": sha256(archive),
        "file_count": len(files),
        "files": [path.relative_to(root).as_posix() for path in files],
        "file_inventory": [
            {
                "path": path.relative_to(root).as_posix(),
                "type": "symlink" if path.is_symlink() else "file",
                "sha256": "" if path.is_symlink() else sha256(path),
                "link_target": path.readlink().as_posix() if path.is_symlink() else "",
            }
            for path in files
        ],
    }
    write_json(ARTIFACTS / f"{args.context_id}.package_manifest.json", manifest)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
