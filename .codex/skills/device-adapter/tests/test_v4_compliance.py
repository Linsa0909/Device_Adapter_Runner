import json
import importlib.util
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], cwd=cwd,
        text=True, capture_output=True, check=False,
    )


class V4ComplianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ops/contexts").mkdir(parents=True)
        (self.root / "ops/artifacts").mkdir(parents=True)
        contract = {
            "delivery_mode": "runtime_plugin", "context_id": "demo",
            "adapter_type": "demo", "vendor": "acme", "plugin_version": "1.0.0",
            "sdk_root": "sdk", "sdk_version": "2.0.0", "sdk_abi": 2,
            "plugin_abi": 1, "target_arch": "aarch64", "target_platform": "RK3588",
            "target_os": "ubuntu22.04", "compiler_triplet": "aarch64-linux-gnu-gcc",
            "runtime_image": "hal:tested", "capability_group_refs": ["camera"],
            "supports_multi_instance": True, "plugin_source_dir": "adapter_plugins/demo",
            "package_dir": "build/demo-package",
            "private_config": {"path": "config/demo.json", "schema_version": "1.0"},
            "target_build": {"build_in_runtime_container": True},
        }
        (self.root / "ops/contexts/demo.plugin_contract.json").write_text(json.dumps(contract))
        (self.root / "ops/contexts/demo.device_spec.json").write_text(json.dumps({
            "adapter_type": "demo",
            "device_model": {"schema_version": "2.0", "profile": {"adapter_type": "demo"},
                             "capability_groups": [{"group_id": "camera", "enabled": True}]},
            "private_config": {"schema_version": "1.0", "instances": [{"instance_index": 0, "enabled": True}]},
        }))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_adapt_generates_and_installs_private_config_with_hidden_symbols(self) -> None:
        result = run("adapt_hal_device.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.root / "adapter_plugins/demo"
        self.assertTrue((plugin / "config/demo.json").is_file())
        cmake = (plugin / "CMakeLists.txt").read_text()
        self.assertIn("CXX_VISIBILITY_PRESET hidden", cmake)
        self.assertIn("install(FILES config/demo.json", cmake)

    def test_formal_package_requires_and_contains_private_config(self) -> None:
        package = self.root / "build/demo-package"
        for directory in ("adapters", "config", "deps", "model/devices"):
            (package / directory).mkdir(parents=True, exist_ok=True)
        (package / "adapters/libhal_adapter_demo.so").write_text("binary")
        (package / "config/demo.json").write_text('{"schema_version":"1.0","instances":[]}')
        (package / "model/devices/demo.device.yaml").write_text("profile:\n  adapter_type: demo\n")
        (package / "README.md").write_text("demo")
        result = run("package_plugin.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.root / "ops/artifacts/demo_adapter_plugin.tar.gz") as archive:
            self.assertIn("config/demo.json", archive.getnames())

    def test_target_plugin_build_runs_inside_declared_runtime_image(self) -> None:
        script = (SCRIPTS / "remote_plugin_build.py").read_text()
        self.assertIn("docker run", script)
        self.assertIn("runtime_image", script)
        self.assertIn("plugin_build_image", script)
        self.assertIn("container_environment", script)

    def test_target_sdk_build_runs_inside_declared_sdk_build_image(self) -> None:
        script = (SCRIPTS / "remote_package_adapter_sdk.py").read_text()
        self.assertIn("docker run", script)
        self.assertIn("sdk_build_image", script)
        self.assertIn("sdk_container_image", script)

    def test_cross_arch_sdk_validation_automatically_uses_target_evidence(self) -> None:
        script = (SCRIPTS / "verify_adapter_sdk.py").read_text()
        self.assertIn("use_target_evidence = args.target_evidence or target != host", script)
        self.assertNotIn("SDK_EXAMPLE_TOOLCHAIN_REQUIRED", script)

    def test_target_sdk_architecture_accepts_readelf_machine_names(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location("verify_adapter_sdk", SCRIPTS / "verify_adapter_sdk.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        try:
            spec.loader.exec_module(module)
            self.assertTrue(module.target_evidence_arch_matches("Machine: AArch64", "aarch64"))
            self.assertTrue(module.target_evidence_arch_matches("Machine: Advanced Micro Devices X86-64", "x86_64"))
            self.assertFalse(module.target_evidence_arch_matches("Machine: AArch64", "x86_64"))
        finally:
            sys.path.remove(str(SCRIPTS))

    def test_preserved_child_failure_refreshes_last_failure_stage(self) -> None:
        spec = importlib.util.spec_from_file_location("stage_orchestrator", SCRIPTS / "stage_orchestrator.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            module.init_logging("demo", "sdk-check", [])
            child = (
                "from pathlib import Path; import json; "
                "p=Path('ops/artifacts/demo.sdk_validation.json'); p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_text(json.dumps({'status':'FAIL','error_code':'TARGET_SDK_EVIDENCE_INVALID'})); "
                "raise SystemExit(8)"
            )
            rc = module.run_command(
                "demo", "stage6b_sdk_validate", [sys.executable, "-c", child],
                preserve_child_failure=True,
            )
            self.assertEqual(rc, 8)
            failure = json.loads((self.root / "ops/artifacts/last_failure.json").read_text())
            self.assertEqual(failure["stage"], "stage6b_sdk_validate")
            self.assertEqual(failure["error_code"], "TARGET_SDK_EVIDENCE_INVALID")
        finally:
            os.chdir(previous)
            sys.modules.pop(spec.name, None)

    def test_success_only_clears_failure_owned_by_that_action(self) -> None:
        spec = importlib.util.spec_from_file_location("stage_orchestrator", SCRIPTS / "stage_orchestrator.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            failure_path = self.root / "ops/artifacts/last_failure.json"
            failure_path.write_text(json.dumps({"context_id": "demo", "stage": "stage6b_sdk_validate"}))
            module.clear_resolved_failure("demo", "model")
            self.assertTrue(failure_path.exists())
            module.clear_resolved_failure("demo", "sdk-check")
            self.assertFalse(failure_path.exists())
        finally:
            os.chdir(previous)
            sys.modules.pop(spec.name, None)

    def test_quality_fingerprint_ignores_python_cache_files(self) -> None:
        spec = importlib.util.spec_from_file_location("quality_gate", SCRIPTS / "quality_gate.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            plugin = self.root / "adapter_plugins/demo"
            cache = plugin / "tests/__pycache__"
            cache.mkdir(parents=True)
            source = plugin / "src/demo.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int demo = 1;\n")
            pyc = cache / "test_demo.cpython-310.pyc"
            pyc.write_bytes(b"cache-one")
            first, included = module.source_fingerprint("demo")
            pyc.write_bytes(b"cache-two")
            second, included_after = module.source_fingerprint("demo")
            self.assertEqual(first, second)
            self.assertEqual(included, included_after)
            self.assertFalse(any("__pycache__" in value or value.endswith((".pyc", ".pyo")) for value in included))
            source.write_text("int demo = 2;\n")
            third, _ = module.source_fingerprint("demo")
            self.assertNotEqual(second, third)
        finally:
            os.chdir(previous)
            sys.modules.pop(spec.name, None)

    def test_remote_acceptance_has_fixed_v4_scenarios(self) -> None:
        script = (SCRIPTS / "test_plugin_remote.py").read_text()
        for scenario in (
            "config_missing", "config_invalid", "instance_mismatch",
            "connect_disconnect_reconnect", "slowpath", "fastpath",
            "fault_injection", "lifecycle_cleanup", "multi_instance",
            "delayed_reload", "soak",
        ):
            self.assertIn(scenario, script)

    def test_remote_acceptance_creates_isolated_service_and_client_containers(self) -> None:
        script = (SCRIPTS / "test_plugin_remote.py").read_text()
        for marker in (
            "runtime_test", "docker run -d", '"--network", "host"', '"--ipc", "host"',
            "ROS2CLI_NO_DAEMON", "docker logs", "cleanup_policy",
            "project_dir", "manager_command", "deployment_file",
        ):
            self.assertIn(marker, script)
        for option in ("--project-dir", "--deployment-file", "--manager-command", "runtime_overrides"):
            self.assertIn(option, script)

    def test_model_stage5_requires_a_real_deployment_plan(self) -> None:
        orchestrator = (SCRIPTS / "stage_orchestrator.py").read_text()
        stage_map = json.loads((SCRIPTS / "agent_stage_map.json").read_text())
        expected = "ops/contexts/{context_id}.deployment_plan.json"
        stage5_block = orchestrator.split('"stage5_deployment_plan": Stage(', 1)[1].split("),", 1)[0]
        self.assertIn(expected, stage5_block)
        self.assertNotIn("device_spec.json", stage5_block)
        self.assertEqual(
            stage_map["stage5_deployment_plan"]["outputs"],
            [
                "ops/contexts/<context_id>.deployment_plan.json",
                "ops/contexts/<context_id>.deployment.yaml",
            ],
        )
        self.assertIn('"stage5_deployment_plan"', orchestrator)
        self.assertIn('"generate_deployment_plan.py"', orchestrator)

    def test_deployment_plan_generator_materializes_blocking_gaps(self) -> None:
        result = run("generate_deployment_plan.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        plan = json.loads((self.root / "ops/contexts/demo.deployment_plan.json").read_text())
        self.assertEqual(plan["status"], "BLOCKED")
        self.assertIn("runtime_test.project_dir", plan["blocking_gaps"])
        self.assertEqual(plan["runtime_test"]["enabled_adapter_types"], ["demo"])

    def test_deployment_plan_generator_creates_single_device_yaml_and_checks(self) -> None:
        result = run(
            "generate_deployment_plan.py", "demo",
            "--project-dir", "/home/Demo", cwd=self.root,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = json.loads((self.root / "ops/contexts/demo.deployment_plan.json").read_text())
        self.assertEqual(plan["status"], "READY_WITH_NOT_RUN")
        self.assertEqual(plan["runtime_test"]["project_dir"], "/home/Demo")
        self.assertTrue(plan["acceptance_checks"]["load"])
        self.assertEqual(plan["acceptance_checks"]["multi_instance"][0]["status"], "NOT_RUN")
        deployment = self.root / "ops/contexts/demo.deployment.yaml"
        self.assertIn("adapter_type: demo", deployment.read_text())

    def test_remote_deploy_installs_single_device_deployment_yaml(self) -> None:
        script = (SCRIPTS / "deploy_plugin.py").read_text()
        self.assertIn("deployment.yaml", script)
        self.assertIn("/deployment/", script)

    def test_source_contract_verifier_enforces_ros_and_runtime_rules(self) -> None:
        script = (SCRIPTS / "verify_plugin_source.py").read_text()
        for marker in ("rclcpp", "transport_url", "hal.instance_index", "dladdr", "binding_coverage", "fastpath_coverage"):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
