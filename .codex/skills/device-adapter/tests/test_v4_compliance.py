import json
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

    def test_remote_acceptance_has_fixed_v4_scenarios(self) -> None:
        script = (SCRIPTS / "test_plugin_remote.py").read_text()
        for scenario in (
            "config_missing", "config_invalid", "instance_mismatch",
            "connect_disconnect_reconnect", "slowpath", "fastpath",
            "fault_injection", "lifecycle_cleanup", "multi_instance",
            "delayed_reload", "soak",
        ):
            self.assertIn(scenario, script)

    def test_source_contract_verifier_enforces_ros_and_runtime_rules(self) -> None:
        script = (SCRIPTS / "verify_plugin_source.py").read_text()
        for marker in ("rclcpp", "transport_url", "hal.instance_index", "dladdr", "binding_coverage", "fastpath_coverage"):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
