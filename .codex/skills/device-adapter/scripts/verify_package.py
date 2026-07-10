#!/usr/bin/env python3
import argparse
import fnmatch
import hashlib
import json
import tarfile
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


def matches_any(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern) for pattern in patterns)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def is_text_member(name):
    suffix = Path(name).suffix.lower()
    return suffix in {".sh", ".bash", ".env", ".md", ".txt", ".yaml", ".yml", ".json", ".toml"}


def read_member_text(tar, name):
    member = tar.getmember(name)
    extracted = tar.extractfile(member)
    if extracted is None:
        return ""
    return extracted.read().decode("utf-8", errors="ignore")


def runtime_delivery_requirements(manifest):
    delivery = manifest.get("delivery") or {}
    required_root = as_list(delivery.get("required_root_files"))
    required_runtime = as_list(delivery.get("required_runtime_files"))
    if delivery.get("closed_loop_package") and not required_root:
        required_root = ["config.env", "install.sh", "run.sh", "status.sh", "view.sh", "DEPLOY.md"]
    return required_root, required_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    command = "verify_package.py " + args.context_id

    stage("stage5_package_verify", "start")
    manifest_path = Path(f"ops/contexts/{args.context_id}.manifest.json")
    if not manifest_path.exists():
        stage("stage5_package_verify", "fail", 2)
        write_failure("stage5_package_verify", command, 2, f"Manifest not found: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_path = Path(manifest.get("package_artifact", f"ops/artifacts/{args.context_id}_package.tar.gz"))
    package_list_path = Path(manifest.get("package_files_file", f"ops/artifacts/{args.context_id}.package_files.txt"))
    if not package_path.exists():
        stage("stage5_package_verify", "fail", 3)
        write_failure("stage5_package_verify", command, 3, f"Package not found: {package_path}")
        return 3
    if not package_list_path.exists():
        stage("stage5_package_verify", "fail", 4)
        write_failure("stage5_package_verify", command, 4, f"Package file list not found: {package_list_path}")
        return 4

    expected = [line.strip() for line in package_list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = set(expected + [manifest_path.as_posix(), package_list_path.as_posix()])
    excludes = manifest.get("exclude") or []
    tree_path = Path(f"ops/artifacts/{args.context_id}.package_tree.txt")
    sha_path = Path(f"ops/artifacts/{args.context_id}.package.sha256")
    report_path = Path(f"ops/artifacts/{args.context_id}.package_verify.json")

    try:
        with tarfile.open(package_path, "r:gz") as tar:
            members = tar.getmembers()
            names = sorted(member.name for member in members if member.isfile() or member.issym())
            executable_script_violations = []
            for member in members:
                if not (member.isfile() and is_text_member(member.name)):
                    continue
                if member.size > 1024 * 1024:
                    continue
                text = read_member_text(tar, member.name)
                if "docker exec -it" in text or "docker run -it" in text or "docker exec -ti" in text or "docker run -ti" in text:
                    executable_script_violations.append(member.name)
    except tarfile.TarError as exc:
        stage("stage5_package_verify", "fail", 5)
        write_failure("stage5_package_verify", command, 5, f"Invalid tar.gz package: {exc}")
        return 5

    name_set = set(names)
    missing = sorted(required - name_set)
    excluded = sorted(name for name in names if matches_any(name, excludes))
    required_root, required_runtime = runtime_delivery_requirements(manifest)
    missing_root = sorted(item for item in required_root if item not in name_set)
    missing_runtime = sorted(item for item in required_runtime if item not in name_set)
    if missing or excluded or missing_root or missing_runtime or executable_script_violations:
        stage("stage5_package_verify", "fail", 6)
        write_failure(
            "stage5_package_verify",
            command,
            6,
            "Package content verification failed.",
            missing=missing,
            missing_root_entries=missing_root,
            missing_runtime_files=missing_runtime,
            interactive_docker_scripts=executable_script_violations,
            excluded_matches=excluded[:50],
            package_tree=tree_path.as_posix(),
        )
        tree_path.write_text("\n".join(names) + "\n", encoding="utf-8")
        return 6

    digest = sha256_file(package_path)
    tree_path.write_text("\n".join(names) + "\n", encoding="utf-8")
    sha_path.write_text(f"{digest}  {package_path.as_posix()}\n", encoding="utf-8")
    report = {
        "context_id": args.context_id,
        "package": package_path.as_posix(),
        "sha256": digest,
        "file_count": len(names),
        "package_tree": tree_path.as_posix(),
        "required_count": len(required),
        "required_root_entries": required_root,
        "required_runtime_files": required_runtime,
        "excluded_patterns": excludes,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"package: {package_path}")
    print(f"sha256: {digest}")
    print(f"file_count: {len(names)}")
    print(f"tree: {tree_path}")
    stage("stage5_package_verify", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
