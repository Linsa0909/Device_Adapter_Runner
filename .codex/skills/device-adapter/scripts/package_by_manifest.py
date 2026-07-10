#!/usr/bin/env python3
import argparse
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    command = "package_by_manifest.py " + args.context_id

    stage("stage1_context_load", "start")
    manifest_path = Path(f"ops/contexts/{args.context_id}.manifest.json")
    if not manifest_path.exists():
        stage("stage1_context_load", "fail", 2)
        write_failure("stage1_context_load", command, 2, f"Manifest not found: {manifest_path}", next_action=f"/device-adapter context {args.context_id}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stage("stage1_context_load", "success")

    stage("stage2_context_validate", "start")
    generated_missing = (manifest.get("generated") or {}).get("missing_paths") or []
    if generated_missing:
        stage("stage2_context_validate", "fail", 4)
        write_failure(
            "stage2_context_validate",
            command,
            4,
            "Manifest contains missing include paths.",
            missing_path=generated_missing[0],
            missing_paths=generated_missing,
            next_action=f"Create missing files or rerun /device-adapter context {args.context_id} with corrected context.",
        )
        return 4
    package_files_path = Path(manifest.get("package_files_file", f"ops/artifacts/{args.context_id}.package_files.txt"))
    if not package_files_path.exists():
        stage("stage2_context_validate", "fail", 3)
        write_failure("stage2_context_validate", command, 3, f"Package file list missing: {package_files_path}", next_action=f"Regenerate context for {args.context_id}.")
        return 3
    files = [line.strip() for line in package_files_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    missing = [item for item in files if not (Path(item).is_file() or Path(item).is_symlink())]
    if missing:
        stage("stage2_context_validate", "fail", 4)
        write_failure("stage2_context_validate", command, 4, "Manifest contains missing files.", missing_path=missing[0], missing_paths=missing)
        return 4
    stage("stage2_context_validate", "success")

    stage("stage4_package_create", "start")
    artifact = Path(manifest.get("package_artifact", f"ops/artifacts/{args.context_id}_package.tar.gz"))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(artifact, "w:gz") as tar:
        tar.add(manifest_path, arcname=manifest_path.as_posix())
        tar.add(package_files_path, arcname=package_files_path.as_posix())
        for file_name in files:
            tar.add(file_name, arcname=file_name)
    print(f"package: {artifact}")
    print(f"size_bytes: {artifact.stat().st_size}")
    print(f"file_count: {len(files)}")
    for item in files[:30]:
        print(item)
    if not artifact.exists() or artifact.stat().st_size == 0:
        stage("stage4_package_create", "fail", 5)
        write_failure("stage4_package_create", command, 5, "Package artifact was not created or is empty.", package=artifact.as_posix())
        return 5
    stage("stage4_package_create", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
