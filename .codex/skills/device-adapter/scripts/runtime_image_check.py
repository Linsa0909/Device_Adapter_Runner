#!/usr/bin/env python3
"""Validate the declared prebuilt HAL runtime image for plugin deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, write_json


def normalize_arch(value: str) -> str:
    value = value.lower().replace("-", "_")
    if value in {"arm64", "aarch64", "linux_arm64"}:
        return "arm64"
    if value in {"amd64", "x86_64", "linux_amd64"}:
        return "amd64"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--save-runtime-image", action="store_true")
    parser.add_argument("-arm", action="store_true")
    parser.add_argument("-x86", action="store_true")
    args, _ = parser.parse_known_args()
    contract = load_contract(args.context_id)
    image = str(contract["runtime_image"])
    result = subprocess.run(
        ["docker", "image", "inspect", image], text=True, capture_output=True, check=False
    )
    inspect_data = []
    if result.returncode == 0:
        try:
            inspect_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            inspect_data = []
    actual_arch = normalize_arch(str(inspect_data[0].get("Architecture", ""))) if inspect_data else ""
    expected_arch = normalize_arch(str(contract["target_arch"]))
    passed = result.returncode == 0 and actual_arch == expected_arch
    saved = ""
    save_rc = 0
    if passed and args.save_runtime_image:
        output = ARTIFACTS / f"{args.context_id}_hal_runtime_{expected_arch}.tar"
        save = subprocess.run(["docker", "save", "-o", str(output), image], check=False)
        save_rc = save.returncode
        if save_rc == 0:
            saved = str(output)
        else:
            passed = False
    write_json(ARTIFACTS / f"{args.context_id}.runtime_image_validation.json", {
        "status": "PASS" if passed else "FAIL",
        "error_code": "" if passed else "RUNTIME_IMAGE_UNAVAILABLE_OR_ARCH_MISMATCH",
        "runtime_image": image, "expected_arch": expected_arch, "actual_arch": actual_arch,
        "inspect_exit_code": result.returncode, "inspect_error": result.stderr.strip(),
        "saved_image": saved, "save_exit_code": save_rc,
        "note": "The image is platform-owned and was not rebuilt by device-adapter.",
    })
    return 0 if passed else 15


if __name__ == "__main__":
    raise SystemExit(main())
