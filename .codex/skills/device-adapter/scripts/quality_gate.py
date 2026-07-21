#!/usr/bin/env python3
"""Aggregate independent verification reports and enforce human approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACTS = Path("ops/artifacts")
PASS = "PASS"
CHECK_STAGES = {
    "implementation": ("stage10_adapter_codegen", "hal-adapter-builder"),
    "tdd": ("stage10_adapter_codegen", "hal-adapter-builder"),
    "verification": ("stage11a_independent_verification", "verification-agent"),
    "c_review": ("stage11b_cpp_review", "verification-agent"),
    "differential_review": ("stage11c_differential_review", "verification-agent"),
}
FINGERPRINT_EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
FINGERPRINT_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_fingerprint_input(path: Path) -> bool:
    return (
        path.is_file()
        and not any(part in FINGERPRINT_EXCLUDED_DIRS for part in path.parts)
        and path.suffix.lower() not in FINGERPRINT_EXCLUDED_SUFFIXES
    )


def git_changed_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={Path.cwd().resolve()}",
         "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return []
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        name = line[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        path = Path(name)
        if path.parts[:2] == ("ops", "artifacts") or path == Path(".git"):
            continue
        paths.append(path)
    return sorted(set(paths), key=lambda item: item.as_posix())


def source_fingerprint(context_id: str) -> tuple[str, list[str]]:
    candidates: list[Path] = []
    manifest_path = Path("ops/contexts") / f"{context_id}.manifest.json"
    manifest = read_json(manifest_path)
    contract_path = Path("ops/contexts") / f"{context_id}.plugin_contract.json"
    contract = read_json(contract_path)
    for entry in manifest.get("include") or manifest.get("includes") or []:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value or any(token in value for token in ("*", "?", "[")):
            continue
        path = Path(value)
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(item for item in path.rglob("*") if item.is_file())
    generated_list = ARTIFACTS / f"{context_id}.generated_files.txt"
    if generated_list.exists():
        for line in generated_list.read_text(encoding="utf-8", errors="ignore").splitlines():
            path = Path(line.strip())
            if path.is_file():
                candidates.append(path)
    if not candidates:
        candidates.extend(git_changed_files())
    plugin_source = contract.get("plugin_source_dir")
    if isinstance(plugin_source, str) and plugin_source:
        plugin_root = Path(plugin_source)
        if plugin_root.is_dir():
            candidates.extend(path for path in plugin_root.rglob("*") if path.is_file())
    for fixed in (
        Path("ops/contexts") / f"{context_id}.context.md",
        manifest_path,
        Path("ops/contexts") / f"{context_id}.device_spec.json",
        contract_path,
        Path("ops/contexts") / f"{context_id}.sdk_inventory.json",
        Path("ops/contexts") / f"{context_id}.normalized_context.json",
        Path("ops/contexts") / f"{context_id}.capability_mapping.json",
        Path("ops/contexts") / f"{context_id}.transport_bindings.json",
        Path("ops/contexts") / f"{context_id}.adapter_implementation_task.json",
        Path("ops/contexts") / f"{context_id}.functional_chain.json",
        Path("ops/contexts") / f"{context_id}.deployment_plan.json",
        Path("ops/contexts") / f"{context_id}.dependency_plan.json",
    ):
        if fixed.exists():
            candidates.append(fixed)
    candidates = sorted(
        {path for path in candidates if is_fingerprint_input(path)},
        key=lambda item: item.as_posix(),
    )
    digest = hashlib.sha256()
    included: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        included.append(path.as_posix())
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest(), included


def has_cpp_scope(context_id: str) -> bool:
    manifest = read_json(Path("ops/contexts") / f"{context_id}.manifest.json")
    entries = manifest.get("include") or manifest.get("includes") or []
    text = json.dumps(entries, ensure_ascii=False).lower()
    if any(suffix in text for suffix in (".cpp", ".cc", ".cxx", ".c", ".hpp", ".h")):
        return True
    root = Path("src/hardware_abstraction_layer")
    plugin_contract = read_json(Path("ops/contexts") / f"{context_id}.plugin_contract.json")
    plugin_root = Path(str(plugin_contract.get("plugin_source_dir") or ""))
    if plugin_root.is_dir() and any(path.suffix in {".cpp", ".cc", ".cxx", ".c", ".hpp", ".h"} for path in plugin_root.rglob("*")):
        return True
    return root.exists() and any(path.suffix in {".cpp", ".cc", ".cxx", ".c", ".hpp", ".h"} for path in root.rglob("*"))


def report_paths(context_id: str) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    paths["implementation"] = ARTIFACTS / f"{context_id}.implementation_coverage.json"
    if has_cpp_scope(context_id):
        paths["tdd"] = ARTIFACTS / f"{context_id}.tdd_report.json"
    paths["verification"] = ARTIFACTS / f"{context_id}.verification_report.json"
    if has_cpp_scope(context_id):
        paths["c_review"] = ARTIFACTS / f"{context_id}.c_review_report.json"
    paths["differential_review"] = ARTIFACTS / f"{context_id}.differential_review_report.json"
    return paths


def validate_report(name: str, path: Path, expected_fingerprint: str) -> tuple[bool, list[str], dict[str, Any]]:
    report = read_json(path)
    errors: list[str] = []
    if not report:
        return False, [f"missing or invalid report: {path}"], report
    status = str(report.get("status") or "").upper()
    if status != PASS:
        errors.append(f"{name} status is {status or '<missing>'}, expected PASS")
    if report.get("source_fingerprint") != expected_fingerprint:
        errors.append(f"{name} report is missing or stale for the current source fingerprint")
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{name} report has no evidence")
    elif not any(
        (isinstance(item, str) and item.strip())
        or (isinstance(item, dict) and str(item.get("value") or item.get("path") or item.get("command") or "").strip())
        for item in evidence
    ):
        errors.append(f"{name} report evidence is not locatable")
    if name in {"tdd", "verification"}:
        commands = report.get("commands")
        if not isinstance(commands, list) or not commands:
            errors.append(f"{name} report has no executed commands")
        else:
            for index, command in enumerate(commands):
                if not isinstance(command, dict) or command.get("exit_code") != 0:
                    errors.append(f"{name} command[{index}] did not exit 0")
    if report.get("unverified_claims"):
        errors.append(f"{name} contains unverified claims")
    return not errors, errors, report


def evaluate(context_id: str, require_approval: bool) -> tuple[int, dict[str, Any]]:
    fingerprint, files = source_fingerprint(context_id)
    checks: dict[str, Any] = {}
    errors: list[str] = []
    missing = False
    for name, path in report_paths(context_id).items():
        ok, report_errors, report = validate_report(name, path, fingerprint)
        if not path.exists():
            missing = True
        checks[name] = {"ok": ok, "path": path.as_posix(), "status": report.get("status"), "errors": report_errors}
        errors.extend(report_errors)

    approval_path = ARTIFACTS / f"{context_id}.human_approval.json"
    approval = read_json(approval_path)
    approval_ok = bool(
        approval.get("decision") == "APPROVE"
        and approval.get("source_fingerprint") == fingerprint
        and approval.get("approver")
    )
    checks_ok = not missing and all(item["ok"] for item in checks.values())
    if errors:
        status = "FAIL"
    elif approval_ok:
        status = PASS
    else:
        status = "WAITING_APPROVAL"
    payload = {
        "schema_version": "1.0",
        "context_id": context_id,
        "status": status,
        "source_fingerprint": fingerprint,
        "source_files": files,
        "checks": checks,
        "human_approval": {"required": True, "enforced": require_approval, "ok": approval_ok, "path": approval_path.as_posix()},
        "errors": errors,
        "generated_at": now_iso(),
        "next_action": (
            f"Run independent verification/review for {context_id}"
            if status == "FAIL"
            else f"Run /device-adapter approve {context_id} --by <name>"
            if not approval_ok
            else f"Proceed with /device-adapter package {context_id}"
        ),
    }
    if status == PASS:
        return 0, payload
    if status == "WAITING_APPROVAL":
        return 22, payload
    return (20 if missing else 21), payload


def write_failure(context_id: str, report: dict[str, Any], exit_code: int) -> None:
    failed_name = next((name for name, item in report.get("checks", {}).items() if not item.get("ok")), "")
    waiting_approval = not failed_name and report.get("status") == "WAITING_APPROVAL"
    if failed_name:
        stage, owner = CHECK_STAGES.get(failed_name, ("stage11a_independent_verification", "verification-agent"))
    else:
        stage, owner = "stage11d_human_approval", "human"
    write_json(
        ARTIFACTS / "last_failure.json",
        {
            "schema_version": "1.0",
            "context_id": context_id,
            "stage": stage,
            "owner_agent": owner,
            "status": "waiting_approval" if waiting_approval else "fail",
            "exit_code": exit_code,
            "error_code": "QUALITY_GATE_FAILED" if failed_name else "HUMAN_APPROVAL_REQUIRED",
            "category": "verification",
            "summary": report.get("status"),
            "evidence": report.get("errors", []),
            "quality_report": (ARTIFACTS / f"{context_id}.quality_gate_report.json").as_posix(),
            "next_action": report.get("next_action"),
            "automatic_repair": False,
        },
    )


def approve(context_id: str, approver: str) -> int:
    rc, report = evaluate(context_id, require_approval=False)
    checks_ok = all(item.get("ok") for item in report.get("checks", {}).values())
    if rc not in (0, 22) or not checks_ok:
        write_json(ARTIFACTS / f"{context_id}.quality_gate_report.json", report)
        write_failure(context_id, report, rc)
        print("Cannot approve: verification/review reports are not PASS.")
        return rc
    write_json(
        ARTIFACTS / f"{context_id}.human_approval.json",
        {
            "schema_version": "1.0",
            "context_id": context_id,
            "decision": "APPROVE",
            "approver": approver,
            "approved_at": now_iso(),
            "source_fingerprint": report["source_fingerprint"],
        },
    )
    rc, report = evaluate(context_id, require_approval=True)
    write_json(ARTIFACTS / f"{context_id}.quality_gate_report.json", report)
    print(f"quality_gate status={report['status']} approver={approver}")
    return rc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "approve", "status"))
    parser.add_argument("context_id")
    parser.add_argument("--by", default="")
    parser.add_argument("--require-approval", action="store_true")
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if args.action == "approve":
        approver = args.by.strip() or os.environ.get("USER", "").strip()
        if not approver:
            print("--by is required")
            return 2
        return approve(args.context_id, approver)
    rc, report = evaluate(args.context_id, args.require_approval)
    write_json(ARTIFACTS / f"{args.context_id}.quality_gate_report.json", report)
    if rc and args.action == "check":
        write_failure(args.context_id, report, rc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
