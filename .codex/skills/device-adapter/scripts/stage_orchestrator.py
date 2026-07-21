#!/usr/bin/env python3
"""Stage orchestration for the device-adapter skill.

The orchestrator owns deterministic execution and emits explicit handoff
contracts for reasoning stages. The Codex skill consumes those handoffs in the
same user command, runs the bounded Agent role, and resumes this state machine.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import shlex
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
CURRENT_ACTION = ""
CURRENT_EXTRA: list[str] = []
STAGE_START_TIMES: dict[str, datetime] = {}
STAGE_OUTPUTS: dict[str, list[str]] = {}
WAITING_FOR_AGENT = 24

ACTION_AUTHORIZATION: dict[str, list[str]] = {
    "model": ["ops/contexts/**", "ops/artifacts/**"],
    "model-prep": ["ops/contexts/**", "ops/artifacts/**"],
    "adapt": ["adapter_plugins/<adapter_type>/**", "ops/artifacts/**"],
    "target-sdk-package": ["remote unique workspace", "ephemeral sdk build container", "build/sdk/**", "ops/artifacts/**"],
    "target-plugin-build": ["remote unique workspace", "ephemeral plugin build container", "build/<adapter>-package/**", "ops/artifacts/**"],
    "verify": ["ops/artifacts/**"],
    "review": ["ops/artifacts/**"],
    "package": ["ops/artifacts/**"],
    "deploy": ["remote runtime root", "remote temporary archive"],
    "test": ["fresh remote service container", "ephemeral remote client containers", "ops/artifacts/logs/**"],
    "loop": ["adapt/deploy/test scopes explicitly authorized by command flags and release approval"],
}


def record_command_authorization(action: str, context_id: str, extra: list[str]) -> None:
    scopes = ACTION_AUTHORIZATION.get(action, ["ops/artifacts/**"])
    payload = {
        "schema_version": "1.0", "context_id": context_id,
        "action": action, "authorization_source": "explicit_user_command",
        "allow_code": "--allow-code" in extra,
        "scopes": scopes,
        "no_per_file_confirmation": True,
        "safety_boundaries_remain_enforced": True,
        "arguments": extra,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = ARTIFACTS / f"{context_id}.workflow_authorization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Stage:
    name: str
    owner: str
    kind: str = "deterministic"
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    deny_roots: tuple[str, ...] = ()
    diff_budget: tuple[tuple[str, int], ...] = ()
    next_action: str = ""


def load_workflow_definition() -> dict[str, Any]:
    path = SCRIPT_DIR / "workflow_definition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    definitions = payload.get("stages")
    if not isinstance(definitions, dict):
        raise RuntimeError("workflow_definition.json must contain a stages object")
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        raise RuntimeError("workflow_definition.json must contain an actions object")
    for name, item in definitions.items():
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list) or any(value not in definitions for value in dependencies):
            raise RuntimeError(f"invalid workflow dependencies for {name}")
        if not item.get("owner") or "write_roots" not in item or "deny_roots" not in item:
            raise RuntimeError(f"incomplete workflow boundary for {name}")
    for action, stage_names in actions.items():
        if not isinstance(stage_names, list) or any(value not in definitions for value in stage_names):
            raise RuntimeError(f"invalid workflow action sequence for {action}")
    return payload


def load_workflow_stages(definition: dict[str, Any]) -> dict[str, Stage]:
    result: dict[str, Stage] = {}
    for name, item in definition["stages"].items():
        budget = item.get("diff_budget") or {}
        result[name] = Stage(
            name=name,
            owner=str(item["owner"]),
            kind=str(item.get("kind") or "deterministic"),
            inputs=tuple(item.get("required_inputs") or ()),
            outputs=tuple(item.get("required_outputs") or ()),
            write_roots=tuple(item.get("write_roots") or ()),
            deny_roots=tuple(item.get("deny_roots") or ()),
            diff_budget=tuple((str(key), int(value)) for key, value in budget.items()),
            next_action=str(item.get("failure_action") or ""),
        )
    return result


WORKFLOW = load_workflow_definition()
STAGES = load_workflow_stages(WORKFLOW)
MODEL_STAGES = tuple(WORKFLOW["actions"]["model"])
RERUN_STARTS = {name: stage.next_action for name, stage in STAGES.items() if stage.next_action}


def marker(stage: str, status: str, exit_code: int | None = None) -> None:
    suffix = "" if exit_code is None else f" exit_code={exit_code}"
    log_line(f"[AGENT_STAGE] stage={stage} status={status}{suffix}")
    update_stage_result(stage, status, exit_code)
    update_status(stage, status, exit_code)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def log_file_for(context_id: str) -> Path:
    return LOGS / f"{context_id}_stage_runner.log"


def init_logging(context_id: str, action: str, extra: list[str]) -> None:
    global CURRENT_CONTEXT_ID, CURRENT_LOG_FILE, CURRENT_ACTION, CURRENT_EXTRA
    CURRENT_CONTEXT_ID = context_id
    CURRENT_LOG_FILE = log_file_for(context_id)
    CURRENT_ACTION = action
    CURRENT_EXTRA = list(extra)
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


def stage_result_path(context_id: str, stage_name: str) -> Path:
    return ARTIFACTS / "stages" / context_id / f"{stage_name}.json"


def stage_outputs(stage_name: str) -> list[str]:
    return STAGE_OUTPUTS.get(stage_name, [])


def hash_outputs(outputs: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in outputs:
        path = Path(item)
        if path.is_file():
            result[item] = sha256_file(path)
    return result


def update_stage_result(stage_name: str, status: str, exit_code: int | None = None, *, evidence: list[str] | None = None) -> None:
    if not CURRENT_CONTEXT_ID:
        return
    now = datetime.now(timezone.utc)
    if status == "start":
        STAGE_START_TIMES[stage_name] = now
    started = STAGE_START_TIMES.get(stage_name, now)
    outputs = stage_outputs(stage_name)
    stage = STAGES.get(stage_name, Stage(stage_name, "stage_orchestrator.py"))
    payload = {
        "schema_version": "1.0",
        "context_id": CURRENT_CONTEXT_ID,
        "stage": stage_name,
        "owner_agent": stage.owner,
        "status": "running" if status == "start" else status,
        "started_at": started.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "finished_at": None if status == "start" else now.isoformat(timespec="seconds"),
        "duration_ms": None if status == "start" else int((now - started).total_seconds() * 1000),
        "exit_code": exit_code,
        "outputs": outputs,
        "output_hashes": hash_outputs(outputs),
        "evidence": evidence or [str(log_file_for(CURRENT_CONTEXT_ID))],
        "log_file": str(log_file_for(CURRENT_CONTEXT_ID)),
    }
    if status == "fail":
        payload["next_action"] = {
            "owner_agent": "failure-debugger",
            "resume_from": stage_name,
        }
    json_write(stage_result_path(CURRENT_CONTEXT_ID, stage_name), payload)


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
            "schema_version": "1.0",
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


def supersede_pre_sdk_model_failure(context_id: str) -> None:
    failure_path = ARTIFACTS / "last_failure.json"
    if failure_path.is_file():
        try:
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failure = {}
        if (
            failure.get("context_id") == context_id
            and failure.get("stage") == "stage4_capability_model"
            and failure.get("error_code") == "REQUIRED_ARTIFACT_MISSING"
        ):
            failure_path.unlink()
    path = status_path(context_id)
    if not path.is_file():
        return
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    status["failed_stages"] = [
        stage for stage in status.get("failed_stages", [])
        if stage != "stage4_capability_model"
    ]
    status.setdefault("superseded_stages", []).append("stage4_capability_model")
    json_write(path, status)


def fail(
    context_id: str,
    stage_name: str,
    reason: str,
    evidence: list[str],
    next_action: str,
    exit_code: int = 2,
    error_code_override: str = "",
) -> int:
    stage = STAGES.get(stage_name, Stage(stage_name, "stage_orchestrator.py"))
    failure_id = f"f-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{stage_name}"
    error_code = error_code_override or "STAGE_FAILED"
    if not error_code_override and "BOUNDARY_WRITE_VIOLATION" in reason:
        error_code = "BOUNDARY_WRITE_VIOLATION"
    elif not error_code_override and "missing" in reason.lower():
        error_code = "REQUIRED_ARTIFACT_MISSING"
    json_write(
        ARTIFACTS / "last_failure.json",
        {
            "schema_version": "1.0",
            "failure_id": failure_id,
            "context_id": context_id,
            "stage": stage_name,
            "owner_agent": stage.owner,
            "status": "fail",
            "exit_code": exit_code,
            "error_code": error_code,
            "category": "workflow",
            "summary": reason,
            "evidence": evidence,
            "log_file": str(log_file_for(context_id)),
            "recommended_owner": stage.owner,
            "allowed_repair_scope": boundary_allowed_paths(stage_name, context_id),
            "next_action": next_action.format(context_id=context_id),
            "rerun_command": f"/device-adapter rerun {context_id}",
            "retry_policy": {"attempt": 1, "max_attempts": 3},
        },
    )
    write_remediation_plan(context_id, failure_id, stage_name, stage.owner, reason, evidence, next_action)
    marker(stage_name, "fail", exit_code)
    return exit_code


def write_remediation_plan(
    context_id: str,
    failure_id: str,
    stage_name: str,
    owner_agent: str,
    reason: str,
    evidence: list[str],
    next_action: str,
) -> None:
    json_write(
        ARTIFACTS / f"{context_id}.remediation_plan.json",
        {
            "schema_version": "1.0",
            "failure_id": failure_id,
            "context_id": context_id,
            "failed_stage": stage_name,
            "error_class": "BOUNDARY_WRITE_VIOLATION" if "BOUNDARY_WRITE_VIOLATION" in reason else "STAGE_FAILURE",
            "probable_root_cause": reason,
            "owner_agent": owner_agent,
            "allowed_files": boundary_allowed_paths(stage_name, context_id),
            "proposed_changes": [],
            "required_revalidation_stages": [stage_name],
            "evidence": evidence,
            "next_action": next_action.format(context_id=context_id),
        },
    )


def checkpoint(context_id: str, stage_name: str, outputs: list[str] | None = None) -> None:
    generated = outputs or []
    STAGE_OUTPUTS[stage_name] = generated
    json_write(
        ARTIFACTS / f"{context_id}.stage_checkpoint.json",
        {
            "schema_version": "1.0",
            "context_id": context_id,
            "stage": stage_name,
            "status": "success",
            "outputs": generated,
            "output_hashes": hash_outputs(generated),
        },
    )
    if generated:
        with generated_files_path(context_id).open("a", encoding="utf-8") as fh:
            for item in generated:
                fh.write(f"{stage_name} -> {item}\n")


def adapter_type_for(context_id: str) -> str:
    path = CONTEXTS / f"{context_id}.plugin_contract.json"
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("adapter_type")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (OSError, json.JSONDecodeError):
            pass
    return context_id.lower().replace("-", "_")


def format_stage_value(template: str, context_id: str) -> str:
    return template.format(context_id=context_id, adapter_type=adapter_type_for(context_id))


def fmt_output(template: str, context_id: str) -> Path:
    return Path(format_stage_value(template, context_id))


def current_source_fingerprint(context_id: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "_device_adapter_quality_gate", SCRIPT_DIR / "quality_gate.py"
    )
    if spec is None or spec.loader is None:
        return ""
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ARTIFACTS = ARTIFACTS
    return str(module.source_fingerprint(context_id)[0])


def stale_agent_outputs(context_id: str, stage: Stage) -> list[str]:
    if stage.name not in {
        "stage11a_independent_verification", "stage11b_cpp_review",
        "stage11c_differential_review",
    }:
        return []
    fingerprint = current_source_fingerprint(context_id)
    stale: list[str] = []
    for template in stage.outputs:
        path = fmt_output(template, context_id)
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stale.append(f"{path} (stale or invalid report)")
            continue
        if report.get("source_fingerprint") != fingerprint or str(report.get("status") or "").upper() != "PASS":
            stale.append(f"{path} (stale source fingerprint or non-PASS status)")
    return stale


def write_agent_handoff(context_id: str, stage_name: str, missing: list[str]) -> int:
    stage = STAGES[stage_name]
    baseline_files = git_changed_files() or set()
    baseline_states = changed_file_states(baseline_files)
    resume_action = CURRENT_ACTION or stage.next_action
    resume_arguments = list(CURRENT_EXTRA)
    resume_command = " ".join(
        shlex.quote(value) for value in ["/device-adapter", resume_action, context_id, *resume_arguments]
        if value
    )
    payload = {
        "schema_version": "1.0",
        "context_id": context_id,
        "stage": stage_name,
        "status": "WAITING_FOR_AGENT",
        "owner_agent": stage.owner,
        "required_inputs": [format_stage_value(item, context_id) for item in stage.inputs],
        "required_outputs": [format_stage_value(item, context_id) for item in stage.outputs],
        "missing_outputs": missing,
        "write_allowlist": [format_stage_value(item, context_id) for item in stage.write_roots],
        "write_denylist": [format_stage_value(item, context_id) for item in stage.deny_roots],
        "diff_budget": dict(stage.diff_budget),
        "baseline_changed_files": sorted(baseline_files),
        "baseline_file_states": baseline_states,
        "resume_action": resume_action,
        "resume_arguments": resume_arguments,
        "resume_command": resume_command,
        "instruction": "Run the bounded owner Agent, write every required output, then resume the same user command automatically.",
        "created_at": now_iso(),
    }
    path = ARTIFACTS / f"{context_id}.agent_handoff.json"
    json_write(path, payload)
    STAGE_OUTPUTS[stage_name] = [str(path)]
    marker(stage_name, "waiting_agent", WAITING_FOR_AGENT)
    log_line(f"[AGENT_HANDOFF] stage={stage_name} owner={stage.owner} contract={path}")
    return WAITING_FOR_AGENT


def ensure_outputs(context_id: str, stage_name: str) -> int:
    stage = STAGES[stage_name]
    marker(stage_name, "start")
    missing = [str(fmt_output(item, context_id)) for item in stage.outputs if not fmt_output(item, context_id).exists()]
    missing.extend(stale_agent_outputs(context_id, stage))
    if missing:
        if stage.kind == "agent_handoff":
            return write_agent_handoff(context_id, stage_name, missing)
        return fail(
            context_id,
            stage_name,
            "required stage artifact is missing",
            [f"missing: {item}" for item in missing],
            (f"/device-adapter {stage.next_action} {{context_id}}" if stage.next_action else
             "Create the missing stage artifact, then rerun."),
        )
    boundary_rc = validate_agent_handoff_boundary(context_id, stage_name)
    if boundary_rc:
        return boundary_rc
    marker(stage_name, "success")
    checkpoint(context_id, stage_name, [str(fmt_output(item, context_id)) for item in stage.outputs])
    return 0


def git_changed_files() -> set[str] | None:
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={Path.cwd().resolve()}", "status", "--porcelain", "-uall"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    files: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            files.add(path)
    return files


def path_state(path_text: str) -> str:
    path = Path(path_text)
    if not path.exists() and not path.is_symlink():
        return "missing"
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if path.is_file():
        return sha256_file(path)
    return "other"


def changed_file_states(paths: set[str] | None) -> dict[str, str]:
    if paths is None:
        return {}
    return {path: path_state(path) for path in paths}


def stage_changed_files(
    before_files: set[str], before_states: dict[str, str],
    after_files: set[str], after_states: dict[str, str],
) -> set[str]:
    candidates = before_files | after_files
    return {
        path for path in candidates
        if (path in before_files) != (path in after_files)
        or before_states.get(path) != after_states.get(path)
    }


def validate_agent_handoff_boundary(context_id: str, stage_name: str) -> int:
    path = ARTIFACTS / f"{context_id}.agent_handoff.json"
    if not path.is_file():
        return 0
    try:
        handoff = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if (
        handoff.get("context_id") != context_id
        or handoff.get("stage") != stage_name
        or handoff.get("status") != "WAITING_FOR_AGENT"
    ):
        return 0
    current_files = git_changed_files()
    if current_files is None:
        return fail(
            context_id, stage_name, "AGENT_BOUNDARY_AUDIT_UNAVAILABLE",
            ["git status could not be read after Agent handoff"],
            f"/device-adapter {STAGES[stage_name].next_action} {{context_id}}", 11,
            "AGENT_BOUNDARY_AUDIT_UNAVAILABLE",
        )
    baseline_files = set(str(value) for value in handoff.get("baseline_changed_files") or [])
    baseline_states = {
        str(key): str(value) for key, value in (handoff.get("baseline_file_states") or {}).items()
    }
    current_states = changed_file_states(current_files)
    changed = stage_changed_files(baseline_files, baseline_states, current_files, current_states)
    ok, report = enforce_boundary(context_id, stage_name, changed)
    if not ok:
        return fail(
            context_id, stage_name, "BOUNDARY_WRITE_VIOLATION",
            [f"changed: {item}" for item in report.get("changed_files", [])]
            + [f"violation: {item['path']} {item['reason']}" for item in report.get("violations", [])],
            "Inspect the boundary report and rerun the bounded owner Agent.", 11,
        )
    handoff["status"] = "COMPLETED"
    handoff["completed_at"] = now_iso()
    handoff["changed_files"] = sorted(changed)
    handoff["boundary_report"] = str(ARTIFACTS / f"{context_id}.{stage_name}.boundary_check.json")
    json_write(path, handoff)
    return 0


def boundary_policy_for(stage_name: str, context_id: str) -> dict[str, Any]:
    stage = STAGES.get(stage_name)
    if stage is None:
        return {"write_allowlist": ["ops/artifacts/**"], "write_denylist": [".git/**"], "diff_budget": {}}
    return {
        "owner_agent": stage.owner,
        "write_allowlist": [format_stage_value(item, context_id) for item in stage.write_roots],
        "write_denylist": [format_stage_value(item, context_id) for item in stage.deny_roots],
        "diff_budget": dict(stage.diff_budget),
    }


def boundary_allowed_paths(stage_name: str, context_id: str) -> list[str]:
    return list(boundary_policy_for(stage_name, context_id).get("write_allowlist") or [])


def path_matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern) for pattern in patterns)


def changed_line_count(paths: set[str]) -> int | None:
    if not paths:
        return 0
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={Path.cwd().resolve()}", "diff", "--numstat", "--", *sorted(paths)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    total = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for value in parts[:2]:
            if value.isdigit():
                total += int(value)
    return total


def enforce_boundary(context_id: str, stage_name: str, changed_files: set[str]) -> tuple[bool, dict[str, Any]]:
    policy = boundary_policy_for(stage_name, context_id)
    allow = list(policy.get("write_allowlist") or [])
    deny = list(policy.get("write_denylist") or [])
    budget = dict(policy.get("diff_budget") or {})
    violations = []
    for path in sorted(changed_files):
        if deny and path_matches(path, deny):
            violations.append({"path": path, "reason": "write_denylist"})
        elif allow and not path_matches(path, allow):
            violations.append({"path": path, "reason": "write_allowlist"})
    max_files = budget.get("max_modified_files")
    if isinstance(max_files, int) and len(changed_files) > max_files:
        violations.append({"path": "*", "reason": f"diff_budget.max_modified_files>{max_files}"})
    lines_changed = changed_line_count(changed_files)
    max_lines = budget.get("max_changed_lines")
    if isinstance(max_lines, int) and lines_changed is not None and lines_changed > max_lines:
        violations.append({"path": "*", "reason": f"diff_budget.max_changed_lines>{max_lines}"})
    report = {
        "schema_version": "1.0",
        "context_id": context_id,
        "stage": stage_name,
        "policy": policy,
        "changed_files": sorted(changed_files),
        "changed_lines": lines_changed,
        "violations": violations,
        "ok": not violations,
    }
    json_write(ARTIFACTS / f"{context_id}.{stage_name}.boundary_check.json", report)
    return not violations, report


def run_command(context_id: str, stage_name: str, command: list[str], *, preserve_child_failure: bool = False) -> int:
    marker(stage_name, "start")
    log_line("[AGENT_COMMAND] " + " ".join(command))
    before_changes = git_changed_files()
    before_states = changed_file_states(before_changes)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        append_raw_line(line)
    process.stdout.close()
    returncode = process.wait()
    if returncode != 0:
        if returncode == 22 and stage_name == "stage11d_human_approval":
            marker(stage_name, "waiting_approval", returncode)
            log_line(
                f"[AGENT_WAIT] human approval required for command: {' '.join(command)}"
            )
            return returncode
        if preserve_child_failure:
            failure_path = ARTIFACTS / "last_failure.json"
            existing_failure: dict[str, Any] = {}
            if failure_path.is_file():
                try:
                    existing_failure = json.loads(failure_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    existing_failure = {}
            if existing_failure.get("context_id") == context_id and existing_failure.get("stage") == stage_name:
                marker(stage_name, "fail", returncode)
                log_line(f"[AGENT_COMMAND_FAIL] child failure already recorded for command: {' '.join(command)} exit_code={returncode}")
                return returncode

            child_report: dict[str, Any] = {}
            child_report_path = ""
            for template in STAGES.get(stage_name, Stage(stage_name, "stage_orchestrator.py")).outputs:
                candidate = fmt_output(template, context_id)
                if not candidate.is_file() or candidate.suffix != ".json":
                    continue
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if str(payload.get("status") or "").upper() in {"FAIL", "FAILED", "BLOCKED"}:
                    child_report = payload
                    child_report_path = str(candidate)
                    break
            child_error = str(child_report.get("error_code") or "CHILD_STAGE_FAILED")
            child_summary = str(child_report.get("message") or child_report.get("summary") or "stage command failed")
            evidence = [
                "command: " + " ".join(command),
                f"exit_code: {returncode}",
                f"log_file: {log_file_for(context_id)}",
            ]
            if child_report_path:
                evidence.append(f"child_report: {child_report_path}")
            return fail(
                context_id, stage_name, child_summary, evidence,
                f"Inspect the child report and rerun /device-adapter rerun {context_id}",
                returncode, child_error,
            )
        return fail(
            context_id,
            stage_name,
            "stage command failed",
            ["command: " + " ".join(command), f"exit_code: {returncode}", f"log_file: {log_file_for(context_id)}"],
            f"Inspect ops/artifacts/last_failure.json and rerun /device-adapter rerun {context_id}",
            returncode,
        )
    after_changes = git_changed_files()
    if before_changes is not None and after_changes is not None:
        after_states = changed_file_states(after_changes)
        stage_changes = stage_changed_files(before_changes, before_states, after_changes, after_states)
        ok, report = enforce_boundary(context_id, stage_name, stage_changes)
        if not ok:
            return fail(
                context_id,
                stage_name,
                "BOUNDARY_WRITE_VIOLATION",
                [
                    f"changed: {item}" for item in report.get("changed_files", [])
                ]
                + [f"violation: {item['path']} {item['reason']}" for item in report.get("violations", [])],
                "Inspect boundary report and route the fix to the stage owner.",
                11,
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


def run_context_reasoning_contracts(context_id: str) -> int:
    steps = (
        ("stage3_context_normalize", "normalize_device_context.py"),
        ("stage4a_capability_mapping", "resolve_capability_mapping.py"),
        ("stage5_transport_resolve", "resolve_transport_profile.py"),
    )
    for stage_name, script in steps:
        rc = run_command(context_id, stage_name, [sys.executable, str(SCRIPT_DIR / script), context_id],
                         preserve_child_failure=True)
        if rc:
            return rc
    return run_command(context_id, "stage4_functional_chain_check",
        [sys.executable, str(SCRIPT_DIR / "functional_chain_check.py"), context_id, "--strict"],
        preserve_child_failure=True)


def run_model(context_id: str, extra: list[str] | None = None) -> int:
    extra = extra or []
    for stage_name in MODEL_STAGES:
        if stage_name == "stage0_env_check":
            rc = env_check(context_id)
            if rc:
                return rc
            continue
        if stage_name == "stage4_functional_chain_check":
            # V5 generates the capability-driven chain after the formal device
            # and plugin contracts are available.
            continue
        if stage_name == "stage5_deployment_plan":
            rc = run_context_reasoning_contracts(context_id)
            if rc:
                return rc
            project_args: list[str] = []
            if "--project-dir" in extra:
                index = extra.index("--project-dir")
                if index + 1 < len(extra):
                    project_args = ["--project-dir", extra[index + 1]]
            rc = run_command(
                context_id,
                stage_name,
                [sys.executable, str(SCRIPT_DIR / "generate_deployment_plan.py"), context_id, *project_args],
            )
            if rc:
                return rc
            continue
        if stage_name == "stage6_dependency_audit" and spec_has_runtime_requirements(context_id):
            marker(stage_name, "start")
            marker(stage_name, "success")
            checkpoint(context_id, stage_name, [str(CONTEXTS / f"{context_id}.device_spec.json")])
            continue
        rc = ensure_outputs(context_id, stage_name)
        if rc:
            return rc
    return 0


def run_model_prep(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    for stage_name in (
        "stage1_context_intake", "stage2_docs_inventory", "stage2_docs_extract",
        "stage3_docs_coverage",
    ):
        if stage_name == "stage2_docs_extract":
            rc = ensure_outputs(context_id, stage_name)
            if rc:
                return rc
        else:
            rc = ensure_outputs(context_id, stage_name)
            if rc:
                return rc
    rc = run_command(
        context_id,
        "stage4a_sdk_contract_prepare",
        [sys.executable, str(SCRIPT_DIR / "prepare_plugin_contract.py"), context_id, *extra],
    )
    if rc == 0:
        supersede_pre_sdk_model_failure(context_id)
    return rc


def env_check(context_id: str) -> int:
    marker("stage0_env_check", "start")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    CONTEXTS.mkdir(parents=True, exist_ok=True)
    marker("stage0_env_check", "success")
    checkpoint(context_id, "stage0_env_check")
    return 0


def option_args(extra: list[str], names: tuple[str, ...]) -> list[str]:
    selected: list[str] = []
    index = 0
    while index < len(extra):
        item = extra[index]
        matched = next((name for name in names if item == name or item.startswith(name + "=")), None)
        if matched:
            selected.append(item)
            if item == matched and index + 1 < len(extra):
                selected.append(extra[index + 1])
                index += 1
        index += 1
    return selected


def has_option(arguments: list[str], name: str) -> bool:
    return any(item == name or item.startswith(name + "=") for item in arguments)


def sdk_material_present(context_id: str) -> bool:
    path = CONTEXTS / f"{context_id}.plugin_contract.json"
    if not path.is_file():
        return False
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    sdk_root = Path(str(contract.get("sdk_root", "")))
    return bool(str(sdk_root)) and all(
        (sdk_root / relative).is_file()
        for relative in ("VERSION", "ABI_VERSION", "cmake/HalAdapterSdkConfig.cmake")
    )


def run_adapt_codegen(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = ensure_outputs(context_id, "stage7_spec_validate")
    if rc:
        return rc
    rc = run_sdk_check(context_id)
    if rc:
        return rc
    rc = run_context_reasoning_contracts(context_id)
    if rc:
        return rc
    rc = run_command(context_id, "stage8_adapter_task",
        [sys.executable, str(SCRIPT_DIR / "generate_adapter_task.py"), context_id], preserve_child_failure=True)
    if rc:
        return rc
    for handoff_stage in ("stage9_pre_adapt_verification", "stage9a_test_design"):
        rc = ensure_outputs(context_id, handoff_stage)
        if rc:
            return rc
    rc = run_command(context_id, "stage8_yaml_generate", [sys.executable, str(SCRIPT_DIR / "adapt_hal_device.py"), context_id, *extra])
    if rc:
        return rc
    rc = ensure_outputs(context_id, "stage10_adapter_codegen")
    if rc:
        return rc
    return run_command(context_id, "stage10_implementation_coverage",
        [sys.executable, str(SCRIPT_DIR / "verify_implementation_coverage.py"), context_id],
        preserve_child_failure=True)


def run_adapt(context_id: str, extra: list[str]) -> int:
    if "--allow-code" not in extra:
        return fail(
            context_id, "stage10_adapter_codegen",
            "CODE_MODIFICATION_NOT_AUTHORIZED: bounded plugin implementation was not authorized",
            [],
            "Run /device-adapter adapt {context_id} --allow-code",
            23,
        )

    remote_args = option_args(extra, ("--host", "--user", "--port"))
    model_args = option_args(extra, ("--project-dir",))
    prep_args = [*remote_args, *model_args]
    rc = run_model_prep(context_id, prep_args)
    if rc:
        return rc

    if sdk_material_present(context_id):
        rc = run_sdk_check(context_id)
    elif has_option(remote_args, "--host"):
        rc = run_target_sdk_package(context_id, remote_args)
    else:
        return fail(
            context_id, "stage6a_sdk_check",
            "TARGET_SDK_MISSING: no validated Adapter SDK is available",
            ["sdk_root is absent or incomplete", "target host was not supplied"],
            "Rerun /device-adapter adapt {context_id} --allow-code --host <board> --user root",
            17,
        )
    if rc:
        return rc

    rc = run_model(context_id, model_args)
    if rc:
        return rc
    rc = run_adapt_codegen(context_id, extra)
    if rc:
        return rc

    if has_option(remote_args, "--host"):
        return run_target_plugin_build(context_id, remote_args)
    return run_plugin_build(context_id, [])


def run_sdk_check(context_id: str) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage6a_sdk_check",
        [sys.executable, str(SCRIPT_DIR / "plugin_sdk_check.py"), context_id],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    return run_command(
        context_id,
        "stage6b_sdk_validate",
        [sys.executable, str(SCRIPT_DIR / "verify_adapter_sdk.py"), context_id],
        preserve_child_failure=True,
    )


def run_target_sdk_package(context_id: str, extra: list[str]) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage6a_target_hal_build",
        [sys.executable, str(SCRIPT_DIR / "remote_package_adapter_sdk.py"), context_id, *extra],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage6a_sdk_check",
        [sys.executable, str(SCRIPT_DIR / "plugin_sdk_check.py"), context_id, "--bootstrap"],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    return run_command(
        context_id,
        "stage6b_sdk_validate",
        [sys.executable, str(SCRIPT_DIR / "verify_adapter_sdk.py"), context_id, "--target-evidence"],
        preserve_child_failure=True,
    )


def run_sdk_package(context_id: str, extra: list[str]) -> int:
    rc = run_command(
        context_id,
        "stage6a_sdk_package",
        [sys.executable, str(SCRIPT_DIR / "package_adapter_sdk.py"), context_id, *extra],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    return run_sdk_check(context_id)


def run_plugin_build(context_id: str, extra: list[str]) -> int:
    rc = run_sdk_check(context_id)
    if rc:
        return rc
    return run_command(
        context_id,
        "stage10c_plugin_build",
        [sys.executable, str(SCRIPT_DIR / "plugin_build.py"), context_id, *extra],
        preserve_child_failure=True,
    )


def run_target_plugin_build(context_id: str, extra: list[str]) -> int:
    rc = run_sdk_check(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage10c_target_plugin_build",
        [sys.executable, str(SCRIPT_DIR / "remote_plugin_build.py"), context_id, *extra],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    return run_command(
        context_id,
        "stage11_plugin_verify",
        [sys.executable, str(SCRIPT_DIR / "verify_plugin.py"), context_id],
        preserve_child_failure=True,
    )


def run_deterministic_verify(context_id: str) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage4_functional_chain_check",
        [sys.executable, str(SCRIPT_DIR / "functional_chain_check.py"), context_id, "--strict", "--check-only"],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    rc = run_sdk_check(context_id)
    if rc:
        return rc
    return run_command(
        context_id,
        "stage11_plugin_verify",
        [sys.executable, str(SCRIPT_DIR / "verify_plugin.py"), context_id],
        preserve_child_failure=True,
    )


def run_verify(context_id: str) -> int:
    rc = run_deterministic_verify(context_id)
    if rc:
        return rc
    for handoff_stage in (
        "stage11a_independent_verification",
        "stage11b_cpp_review",
        "stage11c_differential_review",
    ):
        rc = ensure_outputs(context_id, handoff_stage)
        if rc:
            return rc
    return run_command(
        context_id,
        "stage11d_human_approval",
        [sys.executable, str(SCRIPT_DIR / "quality_gate.py"), "check", context_id],
        preserve_child_failure=True,
    )


def run_approve(context_id: str, extra: list[str]) -> int:
    approver = ""
    for index, item in enumerate(extra):
        if item == "--by" and index + 1 < len(extra):
            approver = extra[index + 1]
        elif item.startswith("--by="):
            approver = item.split("=", 1)[1]
    command = [sys.executable, str(SCRIPT_DIR / "quality_gate.py"), "approve", context_id]
    if approver:
        command.extend(["--by", approver])
    return run_command(context_id, "stage11d_human_approval", command)


def run_package(context_id: str) -> int:
    rc = env_check(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage4_functional_chain_check",
        [sys.executable, str(SCRIPT_DIR / "functional_chain_check.py"), context_id, "--strict", "--check-only"],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    rc = run_deterministic_verify(context_id)
    if rc:
        return rc
    rc = run_command(
        context_id,
        "stage11d_human_approval",
        [sys.executable, str(SCRIPT_DIR / "quality_gate.py"), "check", context_id, "--require-approval"],
        preserve_child_failure=True,
    )
    if rc:
        return rc
    commands = [
        ("stage12_package_manifest", [sys.executable, str(SCRIPT_DIR / "package_plugin.py"), context_id]),
        ("stage13_package_verify", [sys.executable, str(SCRIPT_DIR / "verify_plugin.py"), context_id]),
    ]
    for stage_name, command in commands:
        rc = run_command(context_id, stage_name, command)
        if rc:
            return rc
    return 0


def run_docker_package(context_id: str, extra: list[str]) -> int:
    rc = run_package(context_id)
    if rc:
        return rc
    return run_command(
        context_id,
        "stage16_image_verify",
        [sys.executable, str(SCRIPT_DIR / "runtime_image_check.py"), context_id, *extra],
        preserve_child_failure=True,
    )


def run_deploy(context_id: str, extra: list[str]) -> int:
    rc = run_package(context_id)
    if rc:
        return rc
    rc = run_command(context_id, "stage17_remote_transfer", [sys.executable, str(SCRIPT_DIR / "deploy_plugin.py"), context_id, *extra])
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
    rc = run_command(context_id, "stage21_remote_test", [sys.executable, str(SCRIPT_DIR / "test_plugin_remote.py"), context_id, *extra])
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
    rc = run_command(context_id, "stage17_remote_transfer", [sys.executable, str(SCRIPT_DIR / "deploy_plugin.py"), context_id, *extra])
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


def clear_resolved_failure(context_id: str, action: str) -> None:
    failure_path = ARTIFACTS / "last_failure.json"
    if not failure_path.is_file():
        return
    try:
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if failure.get("context_id") != context_id:
        return
    failed_stage = str(failure.get("stage") or "")
    owning_action = RERUN_STARTS.get(failed_stage)
    if action not in {owning_action, "rerun", "full"}:
        return
    failure_path.unlink()
    status_file = status_path(context_id)
    if status_file.is_file():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
        status["failed_stages"] = [value for value in status.get("failed_stages", []) if value != failed_stage]
        status.setdefault("resolved_failures", []).append({"stage": failed_stage, "resolved_by": action, "resolved_at": now_iso()})
        json_write(status_file, status)
    log_line(f"[AGENT_FAILURE_RESOLVED] stage={failed_stage} action={action}")


def dispatch(action: str, context_id: str, extra: list[str]) -> int:
    if action == "model":
        return run_model(context_id, extra)
    if action == "model-prep":
        return run_model_prep(context_id, extra)
    if action == "adapt":
        return run_adapt(context_id, extra)
    if action == "sdk-check":
        return run_sdk_check(context_id)
    if action == "sdk-package":
        return run_sdk_package(context_id, extra)
    if action == "target-sdk-package":
        return run_target_sdk_package(context_id, extra)
    if action == "plugin-build":
        return run_plugin_build(context_id, extra)
    if action == "target-plugin-build":
        return run_target_plugin_build(context_id, extra)
    if action == "verify":
        return run_verify(context_id)
    if action == "review":
        return run_verify(context_id)
    if action == "approve":
        return run_approve(context_id, extra)
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
        rc = run_model(context_id, extra)
        if rc:
            return rc
        rc = run_adapt(context_id, extra)
        if rc:
            return rc
        rc = run_plugin_build(context_id, [item for item in extra if item == "--clean"])
        if rc:
            return rc
        return run_verify(context_id)
    log_line(f"Unsupported action for stage_orchestrator.py: {action}", stream=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action")
    parser.add_argument("context_id")
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    init_logging(args.context_id, args.action, args.extra)
    record_command_authorization(args.action, args.context_id, args.extra)
    rc = dispatch(args.action, args.context_id, args.extra)
    if rc == 0:
        clear_resolved_failure(args.context_id, args.action)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
