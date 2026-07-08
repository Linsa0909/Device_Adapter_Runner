#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path


def stage(name, status, exit_code=None):
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def write_failure(stage_name, command, exit_code, message, **extra):
    out = Path("ops/artifacts/last_failure.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"stage": stage_name, "command": command, "exit_code": exit_code, "message": message}
    payload.update(extra)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expected_tokens(arch):
    if arch in {"arm64", "aarch64"}:
        return ["aarch64", "arm64"]
    if arch in {"amd64", "x86_64", "x86"}:
        return ["x86-64", "x86_64", "amd64"]
    return [arch]


def classify_file(path):
    try:
        output = subprocess.check_output(["file", str(path)], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        return str(exc)
    return output.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--arch", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    command = f"verify_native_deps.py {args.context_id} --arch {args.arch}"

    stage("stage6_native_deps_verify", "start")
    manifest_path = Path(f"ops/contexts/{args.context_id}.manifest.json")
    if not manifest_path.exists():
        stage("stage6_native_deps_verify", "fail", 2)
        write_failure("stage6_native_deps_verify", command, 2, f"Manifest not found: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    libs = (manifest.get("build") or {}).get("library_files") or []
    tokens = expected_tokens(args.arch)
    mismatches = []
    inspected = []
    for lib in libs:
        path = Path(lib)
        if not path.exists():
            continue
        info = classify_file(path)
        inspected.append({"path": lib, "file": info})
        lower = info.lower()
        if "elf" in lower and not any(token.lower() in lower for token in tokens):
            mismatches.append({"path": lib, "file": info})

    report_path = Path(f"ops/artifacts/{args.context_id}_{args.arch}_native_deps.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"arch": args.arch, "inspected": inspected, "mismatches": mismatches}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mismatches and args.strict:
        stage("stage6_native_deps_verify", "fail", 7)
        write_failure("stage6_native_deps_verify", command, 7, "Native library architecture mismatch.", mismatches=mismatches, report=report_path.as_posix())
        return 7
    if mismatches:
        print("warning: native library architecture mismatch candidates")
        for item in mismatches:
            print(f"- {item['path']}: {item['file']}")
    print(f"native_deps_report: {report_path}")
    stage("stage6_native_deps_verify", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
