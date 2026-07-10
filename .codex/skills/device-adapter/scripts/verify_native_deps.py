#!/usr/bin/env python3
import argparse
import json
import re
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


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def req_path(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("path", "source", "file", "dir", "include_dir", "library", "executable"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def read_json_if_exists(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def runtime_requirements(context_id):
    spec = read_json_if_exists(Path(f"ops/contexts/{context_id}.device_spec.json"))
    runtime = dict(spec.get("runtime_requirements") or {})
    legacy = spec.get("adapter_requirements") or {}
    mapping = {
        "sdk_libraries": "vendor_libraries",
        "subprocesses": "subprocesses",
        "apt_runtime": "apt_runtime",
        "apt_build": "apt_build",
    }
    for old, new in mapping.items():
        if new not in runtime and old in legacy:
            runtime[new] = legacy[old]
    if "apt_runtime" not in runtime and legacy.get("apt_packages"):
        runtime["apt_runtime"] = legacy["apt_packages"]
    return runtime


def package_files(manifest):
    path = Path(manifest.get("package_files_file") or "")
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_repo_matches(fragment):
    if not fragment:
        return []
    candidate = Path(fragment)
    if candidate.exists():
        return [candidate.as_posix()]
    name = candidate.name
    if not name:
        return []
    matches = []
    for path in Path(".").rglob(name):
        if ".git" in path.parts:
            continue
        matches.append(path.as_posix())
        if len(matches) >= 20:
            break
    return matches


def readelf_needed(path):
    try:
        output = subprocess.check_output(["readelf", "-d", str(path)], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return []
    needed = []
    for match in re.finditer(r"Shared library: \[([^\]]+)\]", output):
        needed.append(match.group(1))
    return needed


def candidate_library_dirs(executable, runtime, files):
    dirs = []
    if executable.exists():
        dirs.append(executable.parent.as_posix())
    for item in as_list(runtime.get("library_paths")):
        path = req_path(item)
        if path:
            dirs.extend(find_repo_matches(path) or [path])
    for item in as_list(runtime.get("vendor_libraries")) + as_list(runtime.get("runtime_assets")) + as_list(runtime.get("delivery_files")):
        path = req_path(item)
        if path:
            p = Path(path)
            if p.is_dir():
                dirs.append(p.as_posix())
            elif p.parent.as_posix() not in {".", ""}:
                dirs.append(p.parent.as_posix())
    for file_name in files:
        p = Path(file_name)
        if p.suffix == ".so" or ".so." in p.name:
            dirs.append(p.parent.as_posix())
    return list(dict.fromkeys(dirs))


def lib_exists(lib_name, dirs, files):
    for file_name in files:
        if Path(file_name).name == lib_name:
            return True
    for directory in dirs:
        path = Path(directory) / lib_name
        if path.exists():
            return True
    return False


def declared_system_libraries(runtime, subprocess_spec):
    values = []
    for source in (runtime, subprocess_spec):
        if isinstance(source, dict):
            values.extend(as_list(source.get("system_libraries")))
            values.extend(as_list(source.get("provided_by_system")))
    return {str(item) for item in values if str(item).strip()}


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
    runtime = runtime_requirements(args.context_id)
    files = package_files(manifest)
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

    subprocess_reports = []
    missing_needed = []
    for item in as_list(runtime.get("subprocesses")):
        if not isinstance(item, dict):
            continue
        command = item.get("command") or item.get("executable") or item.get("name")
        executable_text = str(item.get("executable") or "").strip()
        if not executable_text and isinstance(command, str):
            executable_text = command.split()[0] if command.split() else ""
        if not executable_text:
            continue
        matches = find_repo_matches(executable_text)
        report = {"executable": executable_text, "matches": matches, "needed": [], "missing": []}
        if not matches:
            subprocess_reports.append(report)
            continue
        executable = Path(matches[0])
        needed = readelf_needed(executable)
        report["needed"] = needed
        dirs = candidate_library_dirs(executable, runtime, files)
        system_libs = declared_system_libraries(runtime, item)
        for lib in needed:
            if lib in system_libs:
                continue
            if not lib_exists(lib, dirs, files):
                report["missing"].append(lib)
                missing_needed.append({"executable": executable.as_posix(), "library": lib, "searched_dirs": dirs})
        subprocess_reports.append(report)

    report_path = Path(f"ops/artifacts/{args.context_id}_{args.arch}_native_deps.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "arch": args.arch,
                "inspected": inspected,
                "mismatches": mismatches,
                "subprocesses": subprocess_reports,
                "missing_needed": missing_needed,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if (mismatches or missing_needed) and args.strict:
        stage("stage6_native_deps_verify", "fail", 7)
        write_failure(
            "stage6_native_deps_verify",
            command,
            7,
            "Native dependency verification failed.",
            mismatches=mismatches,
            missing_needed=missing_needed,
            report=report_path.as_posix(),
        )
        return 7
    if mismatches:
        print("warning: native library architecture mismatch candidates")
        for item in mismatches:
            print(f"- {item['path']}: {item['file']}")
    if missing_needed:
        print("warning: subprocess shared library closure is incomplete")
        for item in missing_needed:
            print(f"- {item['executable']}: {item['library']} not found")
    print(f"native_deps_report: {report_path}")
    stage("stage6_native_deps_verify", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
