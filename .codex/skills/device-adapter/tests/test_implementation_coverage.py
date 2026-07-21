import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coverage", ROOT / "scripts/verify_implementation_coverage.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class ImplementationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            "context_id": "demo", "adapter_type": "demo",
            "capability_tasks": [{
                "feature_id": "read_state", "group_id": "status",
                "hal_entries": [{"kind": "property", "id": "state"}],
                "transport_binding": "control", "tests": ["state_nominal"],
            }],
            "transport_bindings": [{"binding_id": "control", "profile_id": "serial"}],
        }

    def test_missing_feature_is_blocked(self):
        result = module.evaluate(self.task, {"status": "PASS", "implemented_features": []})
        self.assertEqual("FAIL", result["status"])
        self.assertIn("read_state", result["missing_features"])

    def test_complete_feature_requires_entry_backend_source_and_test(self):
        report = {"status": "PASS", "implemented_features": [{
            "feature_id": "read_state", "group_id": "status",
            "hal_entries": ["state"], "transport_binding": "control",
            "backend_method": "SerialBackend::readState",
            "source": {"file": "src/demo_backend.cpp", "function": "readState"},
            "tests": ["state_nominal"],
        }]}
        result = module.evaluate(self.task, report)
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["gaps"])


if __name__ == "__main__":
    unittest.main()
