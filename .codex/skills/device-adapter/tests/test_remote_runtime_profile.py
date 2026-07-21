import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("remote", ROOT / "scripts/test_plugin_remote.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class RemoteRuntimeProfileTests(unittest.TestCase):
    def test_usb_profile_adds_usb_bus_mount(self):
        mounts = module.hardware_mounts({"bindings": [{"profile_id": "usb"}]})
        self.assertIn("/dev/bus/usb:/dev/bus/usb", mounts)

    def test_serial_does_not_inherit_usb_mount(self):
        mounts = module.hardware_mounts({"bindings": [{"profile_id": "serial"}]})
        self.assertNotIn("/dev/bus/usb:/dev/bus/usb", mounts)

    def test_runtime_profile_rejects_non_cyclone_dds(self):
        errors = module.runtime_profile_errors({
            "domain_id": 0, "localhost_only": 0, "rmw_implementation": "rmw_fastrtps_cpp"
        })
        self.assertTrue(any("rmw_implementation" in item for item in errors))

    def test_not_run_reservations_do_not_fail_successful_single_device_acceptance(self):
        self.assertEqual(module.acceptance_status(True, [{"status": "NOT_RUN"}]), "PASS_WITH_NOT_RUN")
        self.assertEqual(module.acceptance_status(True, [{"status": "BLOCKED"}]), "BLOCKED")
        self.assertEqual(module.acceptance_status(False, []), "FAIL")


if __name__ == "__main__":
    unittest.main()
