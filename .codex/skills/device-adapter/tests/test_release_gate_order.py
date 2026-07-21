#!/usr/bin/env python3
"""Regression tests for release materialization and approval ordering."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("stage_orchestrator", SCRIPT_DIR / "stage_orchestrator.py")
assert SPEC and SPEC.loader
orchestrator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orchestrator
SPEC.loader.exec_module(orchestrator)


class ReleaseGateOrderTests(unittest.TestCase):
    def test_context_manifest_has_no_legacy_device_or_business_image_defaults(self) -> None:
        source = (SCRIPT_DIR / "context_to_manifest.py").read_text(encoding="utf-8")
        for forbidden in ("mino17_pusher_required", "infrared_push_50fps", "runtime_generated_files"):
            self.assertNotIn(forbidden, source)

    def test_model_prep_stops_before_capability_model_and_prepares_sdk_contract(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "ensure_outputs", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_model_prep("demo", ["--host", "board"]), 0)

        self.assertIn("prepare_plugin_contract.py", [Path(command[1]).name for _, command in commands])
        self.assertNotIn("stage4_capability_model", [stage for stage, _ in commands])

    def test_target_sdk_package_has_remote_build_and_local_validation_stages(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_target_sdk_package("demo", ["--host", "board"]), 0)

        self.assertEqual(
            [(stage, Path(command[1]).name) for stage, command in commands],
            [
                ("stage6a_target_hal_build", "remote_package_adapter_sdk.py"),
                ("stage6a_sdk_check", "plugin_sdk_check.py"),
                ("stage6b_sdk_validate", "verify_adapter_sdk.py"),
            ],
        )
        self.assertIn("--bootstrap", commands[1][1])

    def test_target_plugin_build_uses_sdk_gate_remote_build_and_plugin_verify(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_target_plugin_build("demo", []), 0)

        self.assertEqual(
            [(stage, Path(command[1]).name) for stage, command in commands],
            [
                ("stage6a_sdk_check", "plugin_sdk_check.py"),
                ("stage6b_sdk_validate", "verify_adapter_sdk.py"),
                ("stage10c_target_plugin_build", "remote_plugin_build.py"),
                ("stage11_plugin_verify", "verify_plugin.py"),
            ],
        )

    def test_remote_sdk_packager_never_accepts_or_persists_passwords(self) -> None:
        script = (SCRIPT_DIR / "remote_package_adapter_sdk.py").read_text(encoding="utf-8")
        self.assertNotIn("--password", script)
        self.assertNotIn("sshpass", script)
        self.assertIn("BatchMode=yes", script)
        self.assertIn("libhal_model.so", script)
        self.assertIn("libhal_media.so", script)
        self.assertIn("libhal_utils.so", script)

    def test_remote_sdk_packager_records_provenance_environment_and_elf_audit(self) -> None:
        script = (SCRIPT_DIR / "remote_package_adapter_sdk.py").read_text(encoding="utf-8")
        for evidence in (
            "source_provenance",
            "remote_environment",
            "platform_library_audit",
            "source_tree_sha256",
            "source_archive_sha256",
            "git_commit",
            "git_diff_sha256",
            "submodules",
            "readelf -h",
            "readelf -d",
            "sha256sum",
        ):
            self.assertIn(evidence, script)
        for excluded in ("ops/artifacts", ".git", "build", "install", "id_rsa", "id_ed25519"):
            self.assertIn(excluded, script)

    def test_sdk_package_has_its_own_orchestrator_stage(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_sdk_package("demo", []), 0)

        self.assertEqual(commands[0][0], "stage6a_sdk_package")
        self.assertEqual(Path(commands[0][1][1]).name, "package_adapter_sdk.py")
        self.assertEqual(
            [Path(command[1]).name for _stage, command in commands[1:]],
            ["plugin_sdk_check.py", "verify_adapter_sdk.py"],
        )

    def test_boundary_detects_changes_to_already_dirty_files(self) -> None:
        changed = orchestrator.stage_changed_files(
            {"adapter_plugins/demo/src/demo.cpp"},
            {"adapter_plugins/demo/src/demo.cpp": "sha256:before"},
            {"adapter_plugins/demo/src/demo.cpp"},
            {"adapter_plugins/demo/src/demo.cpp": "sha256:after"},
        )
        self.assertEqual(changed, {"adapter_plugins/demo/src/demo.cpp"})

    def test_adapt_checks_sdk_then_generates_plugin_source_only(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "ensure_outputs", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_adapt_codegen("demo_device", ["--allow-code"]), 0)

        scripts = [Path(command[1]).name for _stage, command in commands]
        self.assertEqual(scripts, [
            "plugin_sdk_check.py",
            "verify_adapter_sdk.py",
            "normalize_device_context.py",
            "resolve_capability_mapping.py",
            "resolve_transport_profile.py",
            "functional_chain_check.py",
            "generate_adapter_task.py",
            "adapt_hal_device.py",
            "verify_implementation_coverage.py",
        ])
        self.assertNotIn("generate_runtime_files.py", scripts)

    def test_adapt_runs_the_complete_internal_workflow_with_target_host(self) -> None:
        calls: list[tuple[str, list[str]]] = []

        def record(name: str):
            def invoke(_context_id: str, extra: list[str] | None = None) -> int:
                calls.append((name, list(extra or [])))
                return 0
            return invoke

        with (
            patch.object(orchestrator, "run_model_prep", side_effect=record("model-prep")),
            patch.object(orchestrator, "sdk_material_present", return_value=False),
            patch.object(orchestrator, "run_target_sdk_package", side_effect=record("target-sdk-package")),
            patch.object(orchestrator, "run_model", side_effect=record("model")),
            patch.object(orchestrator, "run_adapt_codegen", side_effect=record("codegen")),
            patch.object(orchestrator, "run_target_plugin_build", side_effect=record("target-plugin-build")),
        ):
            result = orchestrator.run_adapt(
                "demo", ["--allow-code", "--host", "board", "--user", "root"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [name for name, _extra in calls],
            ["model-prep", "target-sdk-package", "model", "codegen", "target-plugin-build"],
        )

    def test_adapt_reuses_existing_sdk_instead_of_repackaging_it(self) -> None:
        calls: list[str] = []

        with (
            patch.object(orchestrator, "run_model_prep", return_value=0),
            patch.object(orchestrator, "sdk_material_present", return_value=True),
            patch.object(orchestrator, "run_sdk_check", side_effect=lambda _id: calls.append("sdk-check") or 0),
            patch.object(orchestrator, "run_target_sdk_package", side_effect=lambda *_args: calls.append("target-sdk-package") or 0),
            patch.object(orchestrator, "run_model", return_value=0),
            patch.object(orchestrator, "run_adapt_codegen", return_value=0),
            patch.object(orchestrator, "run_target_plugin_build", return_value=0),
        ):
            result = orchestrator.run_adapt("demo", ["--allow-code", "--host", "board"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["sdk-check"])

    def test_missing_agent_output_returns_waiting_handoff_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            previous = Path.cwd()
            try:
                os.chdir(td)
                orchestrator.ARTIFACTS = Path("ops/artifacts")
                orchestrator.CONTEXTS = Path("ops/contexts")
                rc = orchestrator.ensure_outputs("demo", "stage9_pre_adapt_verification")
                handoff = json.loads(Path("ops/artifacts/demo.agent_handoff.json").read_text())
            finally:
                os.chdir(previous)
        self.assertEqual(rc, orchestrator.WAITING_FOR_AGENT)
        self.assertEqual(handoff["status"], "WAITING_FOR_AGENT")
        self.assertEqual(handoff["owner_agent"], "adapter-test-designer-agent")
        self.assertIn("baseline_changed_files", handoff)
        self.assertIn("baseline_file_states", handoff)

    def test_resumed_agent_handoff_enforces_workflow_write_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            previous = Path.cwd()
            try:
                os.chdir(td)
                orchestrator.ARTIFACTS = Path("ops/artifacts")
                orchestrator.CONTEXTS = Path("ops/contexts")
                handoff = orchestrator.ARTIFACTS / "demo.agent_handoff.json"
                handoff.parent.mkdir(parents=True)
                handoff.write_text(json.dumps({
                    "context_id": "demo", "stage": "stage9_pre_adapt_verification",
                    "status": "WAITING_FOR_AGENT", "baseline_changed_files": [],
                    "baseline_file_states": {},
                }))
                with (
                    patch.object(orchestrator, "git_changed_files", return_value={"src/forbidden.cpp"}),
                    patch.object(orchestrator, "changed_file_states", return_value={"src/forbidden.cpp": "sha256:new"}),
                    patch.object(orchestrator, "enforce_boundary", return_value=(False, {
                        "changed_files": ["src/forbidden.cpp"],
                        "violations": [{"path": "src/forbidden.cpp", "reason": "write_denylist"}],
                    })),
                ):
                    rc = orchestrator.validate_agent_handoff_boundary(
                        "demo", "stage9_pre_adapt_verification"
                    )
            finally:
                os.chdir(previous)
        self.assertEqual(rc, 11)

    def test_verify_reopens_stale_review_report_as_agent_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            previous = Path.cwd()
            try:
                os.chdir(td)
                orchestrator.ARTIFACTS = Path("ops/artifacts")
                orchestrator.CONTEXTS = Path("ops/contexts")
                report = orchestrator.ARTIFACTS / "demo.verification_report.json"
                report.parent.mkdir(parents=True)
                report.write_text(json.dumps({"status": "PASS", "source_fingerprint": "sha256:old"}))
                with (
                    patch.object(orchestrator, "current_source_fingerprint", return_value="sha256:new"),
                    patch.object(orchestrator, "git_changed_files", return_value=set()),
                ):
                    rc = orchestrator.ensure_outputs("demo", "stage11a_independent_verification")
                handoff = json.loads((orchestrator.ARTIFACTS / "demo.agent_handoff.json").read_text())
            finally:
                os.chdir(previous)
        self.assertEqual(rc, orchestrator.WAITING_FOR_AGENT)
        self.assertEqual(handoff["stage"], "stage11a_independent_verification")
        self.assertIn("stale", handoff["missing_outputs"][0])

    def test_sdk_check_runs_static_contract_then_minimal_adapter_smoke(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_sdk_check("demo"), 0)

        self.assertEqual(
            [(stage, Path(command[1]).name) for stage, command in commands],
            [
                ("stage6a_sdk_check", "plugin_sdk_check.py"),
                ("stage6b_sdk_validate", "verify_adapter_sdk.py"),
            ],
        )

    def test_package_is_read_only_until_archive_creation(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_package("NanoRadar"), 0)

        scripts = [Path(command[1]).name for _stage, command in commands]
        self.assertNotIn("generate_runtime_files.py", scripts)
        self.assertLess(scripts.index("quality_gate.py"), scripts.index("package_plugin.py"))
        self.assertNotIn("package_by_manifest.py", scripts)

    def test_advanced_runner_actions_match_documented_internal_workflow(self) -> None:
        source = (SCRIPT_DIR / "stage_runner.sh").read_text(encoding="utf-8")
        for action in ("model-prep", "target-sdk-package", "target-plugin-build"):
            self.assertIn(f"  {action})", source)

    def test_docker_builder_never_materializes_runtime_files(self) -> None:
        docker_script = (SCRIPT_DIR / "docker_package.sh").read_text(encoding="utf-8")
        self.assertNotIn("generate_runtime_files.py", docker_script)

    def test_docker_package_validates_platform_runtime_image_without_building(self) -> None:
        commands: list[list[str]] = []

        def record(_context_id: str, _stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append(command)
            return 0

        with (
            patch.object(orchestrator, "run_package", return_value=0),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_docker_package("demo", ["-arm"]), 0)
        scripts = [Path(command[1]).name for command in commands]
        self.assertEqual(scripts, ["runtime_image_check.py"])
        self.assertNotIn("docker_package.sh", scripts)

    def test_docker_builder_resolves_helpers_from_its_own_directory(self) -> None:
        docker_script = (SCRIPT_DIR / "docker_package.sh").read_text(encoding="utf-8")
        self.assertIn('script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', docker_script)
        self.assertIn('"$script_dir/verify_native_deps.py"', docker_script)
        self.assertIn('"$script_dir/docker_smoke_test.sh"', docker_script)
        self.assertNotIn(".codex/skills/device-adapter/scripts/", docker_script)

    def test_docker_builder_requires_daemon_and_buildx_before_build(self) -> None:
        docker_script = (SCRIPT_DIR / "docker_package.sh").read_text(encoding="utf-8")
        self.assertIn("docker info", docker_script)
        self.assertIn("Docker daemon is unavailable", docker_script)
        self.assertIn("docker buildx version", docker_script)
        self.assertIn("Docker buildx is required", docker_script)
        self.assertNotIn("build_cmd=(docker build -t", docker_script)


if __name__ == "__main__":
    unittest.main()
