#!/usr/bin/env python3
"""Stage orchestration for the device-adapter skill.

The orchestrator intentionally does not pretend that deterministic Python can
perform LLM-only work such as reading manuals and generating adapter code. For
agent-owned stages it verifies the expected handoff artifact and fails with a
precise stage if the artifact is missing. Deterministic stages call the existing
scripts and preserve their stage markers.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CONTEXTS = Path("ops/contexts")
ARTIFACTS = Path("ops/artifacts")
LOGS = ARTIFACTS / "logs"
CURRENT_CONTEXT_ID = ""
CURRENT_LOG_FILE: Path | None = None


@dataclass(frozen=True)
class Stage:
    name: str
    owner: str
    outputs: tuple[str, ...] = ()
    next_action: str = ""


STAGES: dict[str, Stage] = {
    "stage0_env_check": Stage("stage0_env_check", "stage_orchestrator.py"),
    "stage1_context_intake": Stage(
        "stage1_context_intake",
        "context-mapper",
        ("ops/contexts/{context_id}.context.md", "ops/contexts/{context_id}.manifest.json"),
        "Run /device-adapter context {context_id}",
    ),
    "stage2_docs_inventory": Stage(
        "stage2_docs_inventory",
        "docs-intake-agent",
        ("ops/contexts/{context_id}.docs_inventory.json",),
        "Run the docs-intake-agent for /device-adapter model {context_id}",
    ),
    "stage3_docs_coverage": Stage(
        "stage3_docs_coverage",
        "docs-intake-agent",
        ("ops/contexts/{context_id}.docs_coverage.json",),
        "Run the docs-intake-agent coverage pass for /device-adapter model {context_id}",
    ),
    "stage4_capability_model": Stage(
        "stage4_capability_model",
        "capability-modeler-agent",
        ("ops/contexts/{context_id}.device_spec.json",),
        "Run capability-modeler-agent and write ops/contexts/{context_id}.device_spec.json",
    ),
    "stage5_deployment_plan": Stage(
        "stage5_deployment_plan",
        "deployment-planner-agent",
        ("ops/contexts/{context_id}.device_spec.json",),
        "Run deployment-planner-agent and update deployment_entry/runtime requirements",
    ),
    "stage6_dependency_audit": Stage(
        "stage6_dependency_audit",
        "sdk-dependency-auditor-agent",
        ("ops/contexts/{context_id}.device_spec.json",),
        "Run sdk-dependency-auditor-agent or provide runtime_requirements in device_spec.json",
    ),
    "stage7_spec_validate": Stage(
        "stage7_spec_validate",
        "spec-validator-agent",
        ("ops/contexts/{context_id}.device_spec.json",),
        "Fix ops/contexts/{context_id}.device_spec.json",
    ),
    "stage8_yaml_generate": Stage(
        "stage8_yaml_generate",
        "adapt_hal_device.py",
        next_action="Run /device-adapter adapt {context_id}",
    ),
    "stage9_yaml_validate": Stage("stage9_yaml_validate", "verify_hal_adapter.py"),
    "stage10_adapter_codegen": Stage(
        "stage10_adapter_codegen",
        "hal-adapter-builder",
        next_action="Run hal-adapter-builder with --allow-code or report adapter code gaps",
    ),
    "stage11_hal_registration_verify": Stage("stage11_hal_registration_verify", "verify_hal_adapter.py"),
    "stage12_package_manifest": Stage("stage12_package_manifest", "package_by_manifest.py"),
    "stage13_package_verify": Stage("stage13_package_verify", "verify_package.py"),
    "stage14_docker_build_x86_optional": Stage("stage14_docker_build_x86_optional", "docker_package.sh"),
    "stage15_docker_build_arm64": Stage("stage15_docker_build_arm64", "docker_package.sh"),
    "stage16_image_verify": Stage("stage16_image_verify", "docker_package.sh"),
    "stage17_remote_transfer": Stage("stage17_remote_transfer", "remote_deploy.sh"),
    "stage18_remote_prepare": Stage("stage18_remote_prepare", "remote_deploy.sh"),
    "stage19_remote_device_probe": Stage("stage19_remote_device_probe", "remote_test.sh"),
    "stage20_remote_run": Stage("stage20_remote_run", "remote_test.sh"),
    "stage21_remote_test": Stage("stage21_remote_test", "remote_test.sh"),
    "stage22_collect_logs": Stage("stage22_collect_logs", "remote_test.sh"),
    "stage23_error_summary": Stage("stage23_error_summary", "failure-debugger"),
}


MODEL_STAGES = (
    "stage0_env_check",
    "stage1_context_intake",
    "stage2_docs_inventory",
    "stage3_docs_coverage",
    "stage4_capability_model",
    "stage5_deployment_plan",
    "stage6_dependency_audit",
    "stage7_spec_validate",
)


RERUN_STARTS = {
    "stage0_env_check": "model",
    "stage1_context_intake": "model",
    "stage2_docs_inventory": "model",
    "stage3_docs_coverage": "model",
    "stage4_capability_model": "model",
    "stage5_deployment_plan": "model",
    "stage6_dependency_audit": "verify",
    "stage7_spec_validate": "verify",
    "stage8_yaml_generate": "adapt",
    "stage9_yaml_validate": "verify",
    "stage10_adapter_codegen": "adapt",
    "stage11_hal_registration_verify": "verify",
    "stage12_package_manifest": "package",
    "stage13_package_verify": "package",
    "stage14_docker_build_x86_optional": "docker-package",
    "stage15_docker_build_arm64": "docker-package",
    "stage16_image_verify": "docker-package",
    "stage17_remote_transfer": "deploy",
    "stage18_remote_prepare": "deploy",
    "stage19_remote_device_probe": "test",
    "stage20_remote_run": "test",
    "stage21_remote_test": "test",
    "stage22_collect_logs": "test",
    "stage23_error_summary": "test",
}


def marker(stage: str, status: str, exit_code: int | None = None) -> None:
    suffix = "" if exit_code is None else f" exit_code={exit_code}"
    log_line(f"[AGENT_STAGE] stage={stage} status={status}{suffix}")
    update_status(stage, status, exit_code)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_file_for(context_id: str) -> Path:
    return LOGS / f"{context_id}_stage_runner.log"


def init_logging(context_id: str, action: str, extra: list[str]) -> None:
    global CURRENT_CONTEXT_ID, CURRENT_LOG_FILE
    CURRENT_CONTEXT_ID = context_id
    CURRENT_LOG_FILE = log_file_for(context_id)
    CURRENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CURRENT_LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write(f"[{now_iso()}] [AGENT_RUN] action={action} context_id={context_id} cwd={Path.cwd()} pid={os.getpid()} extra={json.dumps(extra, ensure_ascii=False)}\n")


def log_line(message: str, *, stream: Any = None) -> None:
    line = f"[{now_iso()}] {message}"
    print(line, file=stream or sys.stdout, flush=True)
    if CURRENT_LOG_FILE is not None:
        CURRENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CURRENT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def append_raw_line(line: str) -> None:
    print(line, end="", flush=True)
    if CURRENT_LOG_FILE is not None:
        CURRENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CURRENT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)


def json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def status_path(context_id: str) -> Path:
    return ARTIFACTS / f"{context_id}.status.json"


def generated_files_path(context_id: str) -> Path:
    return ARTIFACTS / f"{context_id}.generated_files.txt"


def update_status(stage_name: str, status: str, exit_code: int | None = None) -> None:
    if not CURRENT_CONTEXT_ID:
        return
    path = status_path(CURRENT_CONTEXT_ID)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    completed = list(existing.get("completed_stages") or [])
    failed = list(existing.get("failed_stages") or [])
    if status == "success" and stage_name not in completed:
        completed.append(stage_name)
    if status == "fail" and stage_name not in failed:
        failed.append(stage_name)
    json_write(
        path,
        {
            "context_id": CURRENT_CONTEXT_ID,
            "current_stage": stage_name,
            "status": status,
            "exit_code": exit_code,
            "last_update": now_iso(),
            "log_file": str(log_file_for(CURRENT_CONTEXT_ID)),
            "completed_stages": completed,
            "failed_stages": failed,
        },
    )


def fail(context_id: str, stage_name: str, reason: str, evidence: list[str], next_action: str, exit_code: int = 2) -> int:
    stage = STAGES.get(stage_name, Stage(stage_name, "stage_orchestrator.py"))
    json_write(
        ARTIFACTS / "last_failure.json",
        {
            "context_id": context_id,
            "stage": stage_name,
            "agent": stage.owner,
            "status": "fail",
            "exit_code": exit_code,
            "reason": reason,
            "evidence": evidence,
            "log_file": str(log_file_for(context_id)),
            "next_action": next_action.format(context_id=context_id),
            "rerun_command": f"/device-adapter rerun {context_id}",
        },
    )
    marker(stage_name, "fail", exit_code)
    return exit_code


def checkpoint(context_id: str, stage_name: str, outputs: list[str] | None = None) -> None:
    generated = outputs or []
    json_write(
        ARTIFACTS / f"{context_id}.stage_checkpoint.json",
        {"context_id": context_id, "stage": stage_name, "status": "success", "outputs": generated},
    )
    if generated:
        with generated_files_path(context_id).open("a", encoding="utf-8") as fh:
            for item in generated:
                fh.write(f"{stage_name} -> {item}\n")


def fmt_output(template: str, context_id: str) -> Path:
    return Path(template.format(context_id=context_id))


def ensure_outputs(context_id: str, stage_name: str) -> bool:
    stage = STAGES[stage_name]
    marker(stage_name, "start")
    missing = [str(fmt_output(item, context_id)) for item in stage.outputs if not fmt_output(item, context_id).exists()]
    if missing:
        fail(
            context_id,
            stage_name,
            "required stage artifact is missing",
            [f"missing: {item}" for item in missing],
            stage.next_action or "Create the missing stage artifact, then rerun.",
        )
        return False
    marker(stage_name, "success")
    checkpoint(context_id, stage_name, [str(fmt_output(item, context_id)) for item in stage.outputs])
    return True


def run_command(context_id: str, stage_name: str, command: list[str], *, preserve_child_failure: bool = False) -> int:
    marker(stage_name, "start")
    log_line("[AGENT_COMMAND] " + " ".join(command))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        append_raw_line(line)
    returncode = process.wait()
    if returncode != 0:
        if preserve_child_failure:
            marker(stage_name, "fail", returncode)
            log_line(f"[AGENT_COMMAND_FAIL] preserved child failure for command: {' '.join(command)} exit_code={returncode}")
            return returncode
        return fail(
            context_id,
            stage_name,
            "stage command failed",
            ["command: " + " ".join(command), f"exit_code: {returncode}", f"log_file: {log_file_for(context_id)}"],
            f"Inspect ops/artifacts/last_failure.json and rerun /device-adapter rerun {context_id}",
            returncode,
        )
    marker(stage_name, "success")
    checkpoint(context_id, stage_name)
    return 0


def read_spec(context_id: str) -> dict[str, Any]:
    spec_path = CONTEXTS / f"{context_id}.device_spec.json"
    if not spec_path.exists():
        return {}
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def spec_has_runtime_requirements(context_id: str) -> bool:
    spec = read_spec(context_id)
    runtime = spec.get("runtime_requirements")
    legacy = spec.get("adapter_requirements")
    return bool(runtime or legacy)


def run_model(context_id: str) -> int:
    for stage_name in MODEL_STAGES:
        if stage_name == "stage0_env_check":
            rc = env_check(context_id)
            if rc:
                return rc
            continue
        if stage_name == "stage6_dependency_audit" and spec_has_runtime_requirements(context_id):
            marker(stage_name, "start")
            marker(stage_name, "success")
            checkpoint(context_id, stage_name, [str(CONTEXTS / f"{context_id}.device_spec.json")])
            continue
        if not ensure_outputs(context_id, stage_name):
            return 2
    return 0


def env_check(context_id: str) -> int:
    marker("stage0_env_check", "start")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    marker("stage0_env_check", "success")
    checkpoint(context_id, "stage0_env_check")
    return 0


def run_adapt(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    if not ensure_outputs(context_id, "stage7_spec_validate"):
        return 2
    rc = run_command(context_id, "stage8_yaml_generate", [sys.executable, str(SCRIPT_DIR / "adapt_hal_device.py"), context_id, *extra])
    if rc:
        return rc
    return run_verify(context_id)


def run_verify(context_id: str) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    # verify_hal_adapter.py emits its own detailed stage7/stage6/stage9/stage11 markers.
    return run_command(
        context_id,
        "stage7_spec_validate",
        [sys.executable, str(SCRIPT_DIR / "verify_hal_adapter.py"), context_id],
        preserve_child_failure=True,
    )


def run_package(context_id: str) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    commands = [
        ("stage12_package_manifest", [sys.executable, str(SCRIPT_DIR / "generate_runtime_files.py"), context_id]),
        ("stage12_package_manifest", [sys.executable, str(SCRIPT_DIR / "package_by_manifest.py"), context_id]),
        ("stage13_package_verify", [sys.executable, str(SCRIPT_DIR / "verify_package.py"), context_id]),
    ]
    for stage_name, command in commands:
        rc = run_command(context_id, stage_name, command)
        if rc:
            return rc
    return 0


def run_docker_package(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(context_id, "stage12_package_manifest", [sys.executable, str(SCRIPT_DIR / "generate_runtime_files.py"), context_id])
    if rc:
        return rc
    arch_stage = "stage15_docker_build_arm64"
    if any(item in {"-x86", "--arch=amd64", "--arch=x86_64"} for item in extra):
        arch_stage = "stage14_docker_build_x86_optional"
    rc = run_command(context_id, arch_stage, ["bash", str(SCRIPT_DIR / "docker_package.sh"), context_id, *extra])
    if rc:
        return rc
    marker("stage16_image_verify", "start")
    marker("stage16_image_verify", "success")
    checkpoint(context_id, "stage16_image_verify")
    return 0


def run_deploy(context_id: str, extra: list[str]) -> int:
    rc = run_package(context_id)
    if rc:
        return rc
    rc = run_command(context_id, "stage17_remote_transfer", ["bash", str(SCRIPT_DIR / "remote_deploy.sh"), context_id, *extra])
    if rc:
        return rc
    marker("stage18_remote_prepare", "start")
    marker("stage18_remote_prepare", "success")
    checkpoint(context_id, "stage18_remote_prepare")
    return 0


def run_test(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(context_id, "stage21_remote_test", ["bash", str(SCRIPT_DIR / "remote_test.sh"), context_id, *extra])
    if rc:
        return rc
    marker("stage22_collect_logs", "start")
    marker("stage22_collect_logs", "success")
    checkpoint(context_id, "stage22_collect_logs")
    return 0


def run_loop(context_id: str, extra: list[str]) -> int:
    rc = run_verify(context_id)
    if rc:
        return rc
    rc = run_package(context_id)
    if rc:
        return rc
    rc = run_docker_package(context_id, extra)
    if rc:
        return rc
    rc = run_command(context_id, "stage17_remote_transfer", ["bash", str(SCRIPT_DIR / "remote_deploy.sh"), context_id, *extra])
    if rc:
        return rc
    marker("stage18_remote_prepare", "start")
    marker("stage18_remote_prepare", "success")
    checkpoint(context_id, "stage18_remote_prepare")
    return run_test(context_id, extra)


def run_logs(context_id: str, extra: list[str]) -> int:
    return run_test(context_id, [*extra, "--timeout", "1"])


def run_rerun(context_id: str, extra: list[str]) -> int:
    failure_path = ARTIFACTS / "last_failure.json"
    if not failure_path.exists():
        log_line("No ops/artifacts/last_failure.json found.", stream=sys.stderr)
        return 2
    try:
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log_line(f"Invalid last_failure.json: {exc}", stream=sys.stderr)
        return 2
    stage_name = str(failure.get("stage") or "")
    action = RERUN_STARTS.get(stage_name)
    if not action:
        log_line(f"Unknown failed stage: {stage_name}", stream=sys.stderr)
        return 3
    log_line(f"[AGENT_RERUN] failed_stage={stage_name} action={action}")
    return dispatch(action, context_id, extra)


def dispatch(action: str, context_id: str, extra: list[str]) -> int:
    if action == "model":
        return run_model(context_id)
    if action == "adapt":
        return run_adapt(context_id, extra)
    if action == "verify":
        return run_verify(context_id)
    if action == "package":
        return run_package(context_id)
    if action == "docker-package":
        return run_docker_package(context_id, extra)
    if action == "deploy":
        return run_deploy(context_id, extra)
    if action == "test":
        return run_test(context_id, extra)
    if action == "loop":
        return run_loop(context_id, extra)
    if action == "logs":
        return run_logs(context_id, extra)
    if action == "rerun":
        return run_rerun(context_id, extra)
    if action == "full":
        rc = run_model(context_id)
        if rc:
            return rc
        rc = run_adapt(context_id, extra)
        if rc:
            return rc
        return run_loop(context_id, extra)
    log_line(f"Unsupported action for stage_orchestrator.py: {action}", stream=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action")
    parser.add_argument("context_id")
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    init_logging(args.context_id, args.action, args.extra)
    return dispatch(args.action, args.context_id, args.extra)


if __name__ == "__main__":
    raise SystemExit(main())
