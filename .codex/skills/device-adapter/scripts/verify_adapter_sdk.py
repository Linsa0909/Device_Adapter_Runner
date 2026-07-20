#!/usr/bin/env python3
"""Build the SDK minimal Adapter as a standalone distribution smoke test."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, resolve_project_path, sdk_arch_dir, write_json


ABI_SYMBOLS = ("hal_get_adapter_sdk_abi_v1", "hal_get_adapter_plugin_v1")


def run(command: list[str], log: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False, env=env)
    output = result.stdout + result.stderr
    with log.open("a", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n" + output)
    return result.returncode, output


def normalized_arch(value: str) -> str:
    value = value.lower().replace("-", "_")
    if value in {"aarch64", "arm64", "linux_arm64"}:
        return "aarch64"
    if value in {"x86_64", "amd64", "linux_amd64"}:
        return "x86_64"
    return value


def target_evidence_arch_matches(evidence: str, target_arch: str) -> bool:
    """Recognize canonical file/readelf architecture names without fuzzy cross-match."""
    target = normalized_arch(target_arch)
    text = evidence.lower()
    if target == "aarch64":
        return bool(
            re.search(r"\bmachine\s*:\s*aarch64\b", text)
            or re.search(r"\barm\s+aarch64\b", text)
            or re.search(r"\belf(?:32|64)?[^\n]*\baarch64\b", text)
        )
    if target == "x86_64":
        return bool(
            re.search(r"\bmachine\s*:\s*(?:advanced micro devices )?x86-64\b", text)
            or re.search(r"\bx86[_-]64\b", text)
        )
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--target-evidence", action="store_true")
    args = parser.parse_args()
    report_path = ARTIFACTS / f"{args.context_id}.sdk_validation.json"
    log_path = ARTIFACTS / "logs" / f"{args.context_id}_sdk_validation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    try:
        contract = load_contract(args.context_id)
    except (FileNotFoundError, ValueError) as exc:
        write_json(report_path, {"status": "FAIL", "error_code": "PLUGIN_CONTRACT_INVALID", "message": str(exc)})
        return 2

    sdk = resolve_project_path(str(contract.get("sdk_root") or "")).resolve()
    example = sdk / "examples/minimal_adapter"
    if not (example / "CMakeLists.txt").is_file():
        write_json(report_path, {
            "schema_version": "1.0", "context_id": args.context_id,
            "status": "FAIL", "error_code": "SDK_MINIMAL_EXAMPLE_MISSING",
            "sdk_root": str(sdk), "expected_example": str(example),
        })
        print("[AGENT_STAGE] stage=stage6b_sdk_validate status=fail")
        return 8

    target = normalized_arch(str(contract.get("target_arch") or ""))
    host = normalized_arch(platform.machine())
    use_target_evidence = args.target_evidence or target != host
    if use_target_evidence:
        target_report = ARTIFACTS / f"{args.context_id}.target_sdk_package.json"
        evidence_path = ARTIFACTS / f"{args.context_id}.target_sdk_validation.txt"
        try:
            import json
            target_result = json.loads(target_report.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            write_json(report_path, {
                "status": "FAIL", "error_code": "TARGET_SDK_EVIDENCE_INVALID",
                "message": str(exc),
            })
            return 8
        evidence = evidence_path.read_text(encoding="utf-8") if evidence_path.is_file() else ""
        required = (*ABI_SYMBOLS, "$ORIGIN/../deps")
        missing_markers = [value for value in required if value not in evidence]
        architecture_match = target_evidence_arch_matches(evidence, target)
        passed = (
            target_result.get("status") == "PASS"
            and not missing_markers
            and architecture_match
            and "not found" not in evidence
        )
        write_json(report_path, {
            "schema_version": "1.0", "context_id": args.context_id,
            "status": "PASS" if passed else "FAIL",
            "error_code": "" if passed else "TARGET_SDK_EVIDENCE_INVALID",
            "sdk_root": str(sdk), "host_arch": host, "target_arch": target,
            "target_report": str(target_report), "target_evidence": str(evidence_path),
            "required_markers": list(required),
            "missing_markers": missing_markers,
            "architecture_match": architecture_match,
        })
        print(f"[AGENT_STAGE] stage=stage6b_sdk_validate status={'success' if passed else 'fail'}")
        return 0 if passed else 8
    toolchain_value = str(contract.get("cmake_toolchain_file") or "")
    toolchain = resolve_project_path(toolchain_value).resolve() if toolchain_value else None

    root = ARTIFACTS / "sdk-validation" / args.context_id
    build = root / "build"
    install = root / "install"
    if root.exists():
        shutil.rmtree(root)
    build.mkdir(parents=True)
    configure = [
        "cmake", "-S", str(example), "-B", str(build),
        f"-DHAL_ADAPTER_SDK_ROOT={sdk}",
        f"-DHAL_ADAPTER_SDK_ARCH={sdk_arch_dir(str(contract['target_arch']))}",
        f"-DCMAKE_INSTALL_PREFIX={install}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    if toolchain:
        configure.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain}")
    commands: list[dict[str, object]] = []
    rc, output = run(configure, log_path)
    commands.append({"command": configure, "exit_code": rc})
    if rc == 0:
        build_command = ["cmake", "--build", str(build), "--target", "install", "--parallel"]
        rc, output = run(build_command, log_path)
        commands.append({"command": build_command, "exit_code": rc})

    binary = install / "adapters/libhal_adapter_minimal.so"
    inspections: dict[str, object] = {}
    if rc == 0 and binary.is_file():
        for name, command in {
            "file": ["file", str(binary)],
            "nm": ["nm", "-D", "--defined-only", str(binary)],
            "dynamic": ["readelf", "-d", str(binary)],
        }.items():
            check_rc, check_output = run(command, log_path)
            inspections[name] = {"exit_code": check_rc, "output": check_output.strip()}
            if check_rc:
                rc = check_rc
        nm_output = str((inspections.get("nm") or {}).get("output") or "")
        dynamic_output = str((inspections.get("dynamic") or {}).get("output") or "")
        if any(re.search(rf"\b{symbol}$", nm_output, re.M) is None for symbol in ABI_SYMBOLS):
            rc = 1
        if "$ORIGIN/../deps" not in dynamic_output:
            rc = 1
        expected_marker = "aarch64" if target == "aarch64" else "x86-64"
        if expected_marker not in str((inspections.get("file") or {}).get("output") or "").lower():
            rc = 1

        if target == host:
            env = dict(os.environ)
            env["LD_LIBRARY_PATH"] = ":".join(filter(None, [
                str(install / "deps"),
                str(sdk / "platform/lib" / sdk_arch_dir(str(contract["target_arch"]))),
                env.get("LD_LIBRARY_PATH", ""),
            ]))
            ldd_rc, ldd_output = run(["ldd", str(binary)], log_path, env=env)
            inspections["ldd"] = {"exit_code": ldd_rc, "output": ldd_output.strip()}
            if ldd_rc or "not found" in ldd_output:
                rc = 1
        else:
            inspections["ldd"] = {"status": "NOT_RUN_CROSS_ARCH"}
    else:
        rc = rc or 1

    status = "PASS" if rc == 0 else "FAIL"
    write_json(report_path, {
        "schema_version": "1.0", "context_id": args.context_id,
        "status": status,
        "error_code": "" if rc == 0 else "SDK_MINIMAL_EXAMPLE_BUILD_FAILED",
        "sdk_root": str(sdk), "host_arch": host, "target_arch": target,
        "example_source": str(example), "install_root": str(install),
        "plugin_binary": str(binary), "commands": commands,
        "inspections": inspections, "log": str(log_path),
    })
    print(f"[AGENT_STAGE] stage=stage6b_sdk_validate status={'success' if rc == 0 else 'fail'}")
    return 0 if rc == 0 else 8


if __name__ == "__main__":
    raise SystemExit(main())
