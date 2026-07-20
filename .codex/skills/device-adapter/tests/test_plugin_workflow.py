import json
import shutil
import tarfile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def run_script(script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class PluginWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "ops/contexts").mkdir(parents=True)
        (self.root / "ops/artifacts").mkdir(parents=True)
        (self.root / "src/hardware_abstraction_layer").mkdir(parents=True)
        (self.root / "src/hardware_abstraction_layer/package.xml").write_text("<package/>")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_contract(self, **overrides: object) -> None:
        payload = {
            "schema_version": "1.0",
            "delivery_mode": "runtime_plugin",
            "context_id": "demo",
            "adapter_type": "demo",
            "vendor": "acme",
            "plugin_version": "1.0.0",
            "sdk_root": "sdk",
            "sdk_version": "1.1.0",
            "sdk_abi": 1,
            "plugin_abi": 1,
            "target_arch": "x86_64",
            "target_platform": "test",
            "target_os": "ubuntu22.04",
            "compiler_triplet": "x86_64-linux-gnu-gcc",
            "runtime_image": "hal:test",
            "capability_group_refs": ["camera"],
            "supports_multi_instance": True,
            "private_config": {"path": "config/demo.json", "schema_version": "1.0"},
            "target_build": {"build_in_runtime_container": True},
            "plugin_source_dir": "adapter_plugins/demo",
            "package_dir": "build/demo-package",
        }
        payload.update(overrides)
        (self.root / "ops/contexts/demo.plugin_contract.json").write_text(json.dumps(payload))

    def make_sdk(self) -> None:
        sdk = self.root / "sdk"
        (sdk / "cmake").mkdir(parents=True)
        (sdk / "include/hardware_abstraction_layer/adapter/plugin_sdk").mkdir(parents=True)
        (sdk / "platform/lib/x86_64-linux-gnu-gcc").mkdir(parents=True)
        (sdk / "model_reference/capability_groups").mkdir(parents=True)
        (sdk / "tools").mkdir(parents=True)
        (sdk / "docs").mkdir(parents=True)
        (sdk / "examples/minimal_adapter").mkdir(parents=True)
        (sdk / "include/hal").mkdir(parents=True)
        (sdk / "include/hardware_abstraction_layer/adapter").mkdir(parents=True, exist_ok=True)
        (sdk / "VERSION").write_text("1.1.0\n")
        (sdk / "ABI_VERSION").write_text("1\n")
        (sdk / "cmake/HalAdapterSdkConfig.cmake").write_text(
            'set(HAL_ADAPTER_SDK_VERSION "1.1.0")\n'
            'set(HAL_ADAPTER_SDK_ABI_VERSION "1")\n'
        )
        (
            sdk
            / "include/hardware_abstraction_layer/adapter/plugin_sdk/"
            "adapter_plugin_api.hpp"
        ).write_text(
            "inline constexpr unsigned kAdapterSdkAbiVersion = 1;\n"
        )
        for name in ("libhal_model.so", "libhal_media.so", "libhal_utils.so"):
            (sdk / "platform/lib/x86_64-linux-gnu-gcc" / name).write_text("fixture")
        (sdk / "model_reference/capability_groups/camera.capability.yaml").write_text("id: camera\n")
        (sdk / "tools/hal_adapter_model_lint.py").write_text("#!/usr/bin/env python3\n")
        (sdk / "README.md").write_text("sdk\n")
        (sdk / "requirements.txt").write_text("")
        (sdk / "docs/migrating_in_tree_adapter.md").write_text("migration\n")
        (sdk / "examples/minimal_adapter/CMakeLists.txt").write_text("# fixture\n")
        (sdk / "include/hal/domain_types_generated.hpp").write_text("// fixture\n")
        (sdk / "include/hal/fastpath_keys_generated.hpp").write_text("// fixture\n")
        (sdk / "include/hardware_abstraction_layer/adapter/adapter_interface.hpp").write_text("// fixture\n")

    def add_private_config(self, package: Path) -> None:
        (package / "config").mkdir(parents=True, exist_ok=True)
        (package / "config/demo.json").write_text('{"schema_version":"1.0","instances":[]}')

    def add_source_contract_evidence(self) -> None:
        source = self.root / "adapter_plugins/demo/src/demo_adapter.cpp"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('const char *k="hal.instance_index"; void *p=(void*)&dladdr;\n')
        for name in ("binding_coverage", "fastpath_coverage", "config_parser_test"):
            (self.root / f"ops/artifacts/demo.{name}.json").write_text('{"status":"PASS"}')

    def test_sdk_check_reports_complete_matching_sdk(self) -> None:
        self.write_contract()
        self.make_sdk()
        result = run_script("plugin_sdk_check.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_check.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["sdk_version"], "1.1.0")

    def test_sdk_check_rejects_internal_version_and_abi_disagreement(self) -> None:
        self.write_contract(sdk_version="2.0.0", sdk_abi=2)
        self.make_sdk()
        sdk = self.root / "sdk"
        (sdk / "VERSION").write_text("2.0.0\n")
        (sdk / "ABI_VERSION").write_text("2\n")
        (sdk / "cmake/HalAdapterSdkConfig.cmake").write_text(
            'set(HAL_ADAPTER_SDK_VERSION "1.1.0")\n'
            'set(HAL_ADAPTER_SDK_ABI_VERSION "1")\n'
        )
        (
            sdk
            / "include/hardware_abstraction_layer/adapter/plugin_sdk/"
            "adapter_plugin_api.hpp"
        ).write_text(
            "inline constexpr unsigned kAdapterSdkAbiVersion = 1;\n"
        )

        result = run_script("plugin_sdk_check.py", "demo", cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        report = json.loads(
            (self.root / "ops/artifacts/demo.sdk_check.json").read_text()
        )
        self.assertEqual(report["error_code"], "SDK_INTERNAL_CONTRACT_MISMATCH")
        self.assertGreaterEqual(len(report["internal_contract_mismatches"]), 3)

    def test_sdk_check_validates_declared_fastpath_contract(self) -> None:
        self.write_contract(
            fastpath_contract={
                "required_constants": [
                    "ImuData::SOURCE_COMBINED",
                    "ImuData::SOURCE_ACCEL",
                    "ImuData::SOURCE_GYRO",
                ],
                "fixed_arrays": [
                    {
                        "domain_type": "ImuData",
                        "field": "orientation_covariance",
                        "element_type": "double",
                        "length": 9,
                    }
                ],
            }
        )
        self.make_sdk()

        result = run_script("plugin_sdk_check.py", "demo", cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        report = json.loads(
            (self.root / "ops/artifacts/demo.sdk_check.json").read_text()
        )
        self.assertEqual(report["error_code"], "SDK_FASTPATH_CONTRACT_MISSING")
        self.assertIn(
            "ImuData::SOURCE_ACCEL", report["missing_fastpath_constants"]
        )
        self.assertEqual(
            report["missing_fixed_arrays"][0]["field"],
            "orientation_covariance",
        )

    def test_sdk_package_invokes_platform_native_packager(self) -> None:
        platform = self.root / "platform"
        script = platform / "src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh"
        script.parent.mkdir(parents=True)
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "out=$1\n"
            "sdk=$out/hal_adapter_sdk_v${HAL_ADAPTER_SDK_VERSION}\n"
            "mkdir -p \"$sdk\"\n"
            "printf '%s\\n' \"$HAL_ADAPTER_SDK_ARCH\" > \"$sdk/test-arch\"\n"
            "printf '%s\\n' \"$HAL_PLATFORM_PREFIX\" > \"$sdk/test-prefix\"\n"
            "tar -czf \"$out/hal_adapter_sdk_v${HAL_ADAPTER_SDK_VERSION}.tar.gz\" "
            "-C \"$out\" \"hal_adapter_sdk_v${HAL_ADAPTER_SDK_VERSION}\"\n"
            "printf '%s\\n' \"$out/hal_adapter_sdk_v${HAL_ADAPTER_SDK_VERSION}.tar.gz\"\n"
        )
        install = self.root / "platform-install"
        (install / "lib").mkdir(parents=True)
        for name in ("libhal_model.so", "libhal_media.so", "libhal_utils.so"):
            (install / "lib" / name).write_text("fixture")
        plugin_api = platform / (
            "src/hardware_abstraction_layer/include/hardware_abstraction_layer/adapter/"
            "plugin_sdk/adapter_plugin_api.hpp"
        )
        plugin_api.parent.mkdir(parents=True)
        plugin_api.write_text("// fixture\n")
        sdk_cmake = platform / "src/hardware_abstraction_layer/adapter_sdk/cmake/HalAdapterSdkConfig.cmake"
        sdk_cmake.parent.mkdir(parents=True)
        sdk_cmake.write_text("# fixture\n")
        registry = platform / (
            "src/hardware_abstraction_layer/src/adapter/plugin_sdk/adapter_plugin_registry.cpp"
        )
        registry.parent.mkdir(parents=True)
        registry.write_text("// fixture\n")
        self.write_contract(
            platform_source_root=str(platform),
            platform_install_prefix=str(install),
            sdk_output_dir="build/adapter-sdk",
            sdk_root="build/adapter-sdk/hal_adapter_sdk_v1.1.0",
        )

        result = run_script("package_adapter_sdk.py", "demo", cwd=self.root)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        sdk = self.root / "build/adapter-sdk/hal_adapter_sdk_v1.1.0"
        self.assertEqual((sdk / "test-arch").read_text().strip(), "x86_64-linux-gnu-gcc")
        self.assertEqual((sdk / "test-prefix").read_text().strip(), str(install))
        report = json.loads((self.root / "ops/artifacts/demo.sdk_package.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["native_packager"].endswith("scripts/package_adapter_sdk.sh"))

    def test_sdk_package_rejects_missing_native_packager(self) -> None:
        self.write_contract(
            platform_source_root=str(self.root / "not-plugin-platform"),
            platform_install_prefix=str(self.root / "platform-install"),
            sdk_output_dir="build/adapter-sdk",
            sdk_root="build/adapter-sdk/hal_adapter_sdk_v1.1.0",
        )

        result = run_script("package_adapter_sdk.py", "demo", cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_package.json").read_text())
        self.assertEqual(report["error_code"], "PLATFORM_SDK_PACKAGER_MISSING")

    def test_sdk_package_rejects_unsafe_contract_before_external_script(self) -> None:
        self.write_contract(
            sdk_version="../../escape",
            platform_source_root=str(self.root / "platform"),
            platform_install_prefix=str(self.root / "platform-install"),
            sdk_output_dir="build/adapter-sdk",
            sdk_root="build/adapter-sdk/hal_adapter_sdk_v1.1.0",
        )

        result = run_script("package_adapter_sdk.py", "demo", cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_package.json").read_text())
        self.assertEqual(report["error_code"], "PLUGIN_CONTRACT_INVALID")
        self.assertTrue(any("sdk_version" in item for item in report["contract_errors"]))

    def test_sdk_validation_rejects_sdk_without_minimal_adapter(self) -> None:
        self.write_contract()
        self.make_sdk()
        (self.root / "sdk/examples/minimal_adapter/CMakeLists.txt").unlink()

        result = run_script("verify_adapter_sdk.py", "demo", cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_validation.json").read_text())
        self.assertEqual(report["error_code"], "SDK_MINIMAL_EXAMPLE_MISSING")

    def test_context_writes_blocking_plugin_contract_draft(self) -> None:
        context_file = self.root / "input.md"
        context_file.write_text(
            "adapter_type: demo\n厂商: Acme\n目标架构: aarch64\n目标平台: RK3588\n"
        )
        result = run_script(
            "context_to_manifest.py", "demo", "--context-file", str(context_file),
            "--skip-docs-extract", cwd=self.root,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        contract = json.loads((self.root / "ops/contexts/demo.plugin_contract.json").read_text())
        self.assertEqual(contract["delivery_mode"], "runtime_plugin")
        self.assertEqual(contract["target_arch"], "aarch64")
        self.assertIn("sdk_root", contract["unknown_fields"])
        self.assertIn("runtime_image", contract["unknown_fields"])

    def test_sdk_check_blocks_unknown_runtime_contract(self) -> None:
        self.write_contract(runtime_image="")
        self.make_sdk()
        result = run_script("plugin_sdk_check.py", "demo", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_check.json").read_text())
        self.assertIn("runtime_image", report["missing_contract_fields"])

    def test_sdk_check_rejects_non_integer_abi_and_unsafe_output_path(self) -> None:
        self.write_contract(sdk_abi="1", plugin_source_dir="../outside/demo")
        self.make_sdk()
        result = run_script("plugin_sdk_check.py", "demo", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.sdk_check.json").read_text())
        self.assertIn("sdk_abi must be a positive integer", report["contract_errors"])
        self.assertTrue(any("plugin_source_dir" in item for item in report["contract_errors"]))

    def test_adapt_scaffolds_plugin_without_touching_platform_registration(self) -> None:
        self.write_contract()
        self.make_sdk()
        (self.root / "ops/contexts/demo.context.md").write_text("demo plugin")
        (self.root / "ops/contexts/demo.device_spec.json").write_text(json.dumps({
            "adapter_type": "demo",
            "device": {"manufacturer": "Acme", "model": "Demo"},
            "device_model": {
                "schema_version": "2.0",
                "profile": {"adapter_type": "demo"},
                "capability_groups": [{"group_id": "camera", "enabled": True}],
            },
        }))
        factory = self.root / "src/hardware_abstraction_layer/adapter_factory.cpp"
        cmake = self.root / "src/hardware_abstraction_layer/CMakeLists.txt"
        factory.write_text("platform factory\n")
        cmake.write_text("platform cmake\n")
        result = run_script("adapt_hal_device.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        plugin = self.root / "adapter_plugins/demo"
        self.assertTrue((plugin / "src/demo_plugin.cpp").exists())
        self.assertTrue((plugin / "model/devices/demo.device.yaml").exists())
        self.assertFalse((self.root / "src/hardware_abstraction_layer/model/capability_groups/demo.capability.yaml").exists())
        self.assertEqual(factory.read_text(), "platform factory\n")
        self.assertEqual(cmake.read_text(), "platform cmake\n")

    def test_package_rejects_forbidden_capability_yaml(self) -> None:
        self.write_contract()
        package = self.root / "build/demo-package"
        (package / "adapters").mkdir(parents=True)
        (package / "model/devices").mkdir(parents=True)
        (package / "model/capability_groups").mkdir(parents=True)
        (package / "adapters/libhal_adapter_demo.so").write_text("binary")
        (package / "model/devices/demo.device.yaml").write_text("profile:\n  adapter_type: demo\n")
        (package / "model/capability_groups/camera.capability.yaml").write_text("id: camera\n")
        (package / "README.md").write_text("demo\n")
        self.add_private_config(package)
        result = run_script("package_plugin.py", "demo", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.root / "ops/artifacts/demo.forbidden_files.json").read_text())
        self.assertEqual(report["status"], "FAIL")

    def test_package_rejects_non_library_file_in_deps(self) -> None:
        self.write_contract()
        package = self.root / "build/demo-package"
        (package / "adapters").mkdir(parents=True)
        (package / "deps").mkdir(parents=True)
        (package / "model/devices").mkdir(parents=True)
        (package / "adapters/libhal_adapter_demo.so").write_text("binary")
        (package / "deps/config.ini").write_text("not a shared library")
        (package / "model/devices/demo.device.yaml").write_text("profile:\n  adapter_type: demo\n")
        (package / "README.md").write_text("demo\n")
        self.add_private_config(package)
        result = run_script("package_plugin.py", "demo", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)

    def test_package_contains_only_formal_contract_and_preserves_symlinks(self) -> None:
        self.write_contract()
        package = self.root / "build/demo-package"
        (package / "adapters").mkdir(parents=True)
        (package / "deps").mkdir(parents=True)
        (package / "model/devices").mkdir(parents=True)
        (package / "adapters/libhal_adapter_demo.so").write_text("binary")
        (package / "deps/libvendor_demo.so.1").write_text("dependency")
        (package / "deps/libvendor_demo.so").symlink_to("libvendor_demo.so.1")
        (package / "model/devices/demo.device.yaml").write_text("profile:\n  adapter_type: demo\n")
        (package / "README.md").write_text("demo\n")
        self.add_private_config(package)
        result = run_script("package_plugin.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        archive = self.root / "ops/artifacts/demo_adapter_plugin.tar.gz"
        with tarfile.open(archive, "r:gz") as stream:
            members = {item.name: item for item in stream.getmembers()}
        self.assertEqual(set(members), {
            "README.md", "adapters/libhal_adapter_demo.so", "deps/libvendor_demo.so",
            "config/demo.json", "deps/libvendor_demo.so.1", "model/devices/demo.device.yaml",
        })
        self.assertTrue(members["deps/libvendor_demo.so"].issym())

    @unittest.skipUnless(shutil.which("cc") and shutil.which("readelf") and shutil.which("nm"), "native ELF tools required")
    def test_plugin_verify_checks_symbols_arch_and_rpath(self) -> None:
        self.write_contract()
        self.make_sdk()
        package = self.root / "build/demo-package"
        (package / "adapters").mkdir(parents=True)
        (package / "deps").mkdir(parents=True)
        (package / "model/devices").mkdir(parents=True)
        source = self.root / "plugin.c"
        source.write_text(
            "unsigned hal_get_adapter_sdk_abi_v1(void){return 1;}\n"
            "#include <stddef.h>\n"
            "struct api { unsigned abi; const char *id,*vendor,*version; size_t(*count)(void); "
            "const char*(*at)(size_t); _Bool(*supports)(const char*); void*(*create)(const char*); void(*destroy)(void*); };\n"
            "static size_t count(void){return 1;} static const char* at(size_t i){return i?0:\"demo\";}\n"
            "static _Bool supports(const char*t){return t&&t[0]=='d';} static void* create(const char*t){(void)t;return 0;} "
            "static void destroy(void*p){(void)p;}\n"
            "void *hal_get_adapter_plugin_v1(void){static struct api a={1,\"acme.demo\",\"acme\",\"1.0.0\",count,at,supports,create,destroy};return &a;}\n"
        )
        built = subprocess.run([
            "cc", "-shared", "-fPIC", str(source),
            "-Wl,-rpath,$ORIGIN/../deps",
            "-o", str(package / "adapters/libhal_adapter_demo.so"),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(built.returncode, 0, built.stderr)
        (package / "model/devices/demo.device.yaml").write_text(
            "schema_version: '2.0'\nprofile:\n  adapter_type: demo\n"
            "capability_groups:\n  - group_id: camera\n    enabled: true\n"
        )
        (package / "README.md").write_text("demo\n")
        self.add_private_config(package)
        self.add_source_contract_evidence()
        (self.root / "ops/artifacts/demo.multi_instance_test.json").write_text(json.dumps({
            "status": "PASS",
            "simultaneous_instances": 2,
            "independent_destroy_verified": True,
        }))
        result = run_script("verify_plugin.py", "demo", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.root / "ops/artifacts/demo.abi_validation.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["exported_symbols"]), 2)

    @unittest.skipUnless(shutil.which("cc") and shutil.which("readelf"), "native ELF tools required")
    def test_plugin_verify_rejects_missing_transitive_private_dependency(self) -> None:
        self.write_contract()
        self.make_sdk()
        package = self.root / "build/demo-package"
        (package / "adapters").mkdir(parents=True)
        (package / "deps").mkdir(parents=True)
        (package / "model/devices").mkdir(parents=True)
        missing_source = self.root / "missing.c"
        middle_source = self.root / "middle.c"
        missing_source.write_text("int missing(void){return 1;}\n")
        middle_source.write_text("extern int missing(void); int middle(void){return missing();}\n")
        subprocess.run(["cc", "-shared", "-fPIC", str(missing_source), "-Wl,-soname,libmissing.so.1", "-o", str(self.root / "libmissing.so.1")], check=True)
        subprocess.run([
            "cc", "-shared", "-fPIC", str(middle_source), str(self.root / "libmissing.so.1"),
            "-Wl,-soname,libmiddle.so.1", "-o", str(package / "deps/libmiddle.so.1"),
        ], check=True)
        plugin_source = self.root / "plugin.c"
        plugin_source.write_text("unsigned hal_get_adapter_sdk_abi_v1(void){return 1;} void* hal_get_adapter_plugin_v1(void){return 0;}\n")
        subprocess.run([
            "cc", "-shared", "-fPIC", str(plugin_source), "-Wl,-rpath,$ORIGIN/../deps",
            "-o", str(package / "adapters/libhal_adapter_demo.so"),
        ], check=True)
        (package / "model/devices/demo.device.yaml").write_text("profile:\n  adapter_type: demo\n")
        (package / "README.md").write_text("demo\n")
        self.add_private_config(package)
        self.add_source_contract_evidence()
        result = run_script("verify_plugin.py", "demo", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        closure = json.loads((self.root / "ops/artifacts/demo.dependency_closure.json").read_text())
        self.assertEqual(closure["status"], "FAIL")
        self.assertIn("libmissing.so.1", closure["unresolved"])


if __name__ == "__main__":
    unittest.main()
