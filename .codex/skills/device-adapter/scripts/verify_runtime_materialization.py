#!/usr/bin/env python3
"""Read-only check that release runtime files were frozen before approval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()

    manifest_path = Path("ops/contexts") / f"{args.context_id}.manifest.json"
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = manifest.get("build") or {}
    delivery = manifest.get("delivery") or {}
    docker = manifest.get("docker") or {}

    required = set(as_list(build.get("generated_runtime_files")))
    required.update(as_list(delivery.get("required_root_files")))
    if docker.get("dockerfile"):
        required.add(str(docker["dockerfile"]))
    if docker.get("compose_file"):
        required.add(str(docker["compose_file"]))

    missing = sorted(path for path in required if not Path(path).exists())
    package_list_path = Path(
        manifest.get("package_files_file") or f"ops/artifacts/{args.context_id}.package_files.txt"
    )
    package_members = set()
    if package_list_path.exists():
        package_members = {
            line.strip()
            for line in package_list_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        }
    absent_from_manifest = sorted(path for path in required if path not in package_members)

    if missing or absent_from_manifest:
        print("Runtime release files are not frozen for review/approval.")
        if missing:
            print("missing_files:")
            for path in missing:
                print(f"- {path}")
        if absent_from_manifest:
            print("missing_from_package_files:")
            for path in absent_from_manifest:
                print(f"- {path}")
        print(f"next_action: /device-adapter adapt {args.context_id}")
        return 23

    print(f"runtime_materialization: PASS ({len(required)} required files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
