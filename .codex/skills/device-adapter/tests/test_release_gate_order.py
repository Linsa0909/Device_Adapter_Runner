#!/usr/bin/env python3
"""Regression tests for release materialization and approval ordering."""

from __future__ import annotations

import importlib.util
import sys
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
    def test_model_prep_stops_before_capability_model_and_prepares_sdk_contract(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def record(_context_id: str, stage: str, command: list[str], **_kwargs: object) -> int:
            commands.append((stage, command))
            return 0

        with (
            patch.object(orchestrator, "env_check", return_value=0),
            patch.object(orchestrator, "ensure_outputs", return_value=True),
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
            patch.object(orchestrator, "ensure_outputs", return_value=True),
            patch.object(orchestrator, "run_command", side_effect=record),
        ):
            self.assertEqual(orchestrator.run_adapt("NanoRadar", ["--allow-code"]), 0)

        scripts = [Path(command[1]).name for _stage, command in commands]
        self.assertEqual(scripts, [
            "plugin_sdk_check.py",
            "verify_adapter_sdk.py",
            "normalize_device_context.py",
            "resolve_capability_mapping.py",
            "resolve_transport_profile.py",
            "generate_adapter_task.py",
            "adapt_hal_device.py",
        ])
        self.assertNotIn("generate_runtime_files.py", scripts)

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
