import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ContextDrivenAdaptationTests(unittest.TestCase):
    def test_mapping_uses_context_evidence_not_device_category(self):
        mapper = load("resolve_capability_mapping")
        normalized = {
            "device": {"model": "camera_named_but_not_a_camera"},
            "requested_features": [{
                "feature_id": "set_rotation_rate",
                "description": "set actuator rotation rate",
                "required": True,
                "capability_group_candidates": ["motion_control"],
                "hal_entry_candidates": ["set_rate"],
                "implementation_evidence": ["SDK_SetRate in vendor_api.h"],
                "source_evidence": ["manual section 4.2"],
            }],
        }
        capabilities = {"motion_control": {
            "id": "motion_control",
            "entries": [{"kind": "service", "id": "set_rate", "description": "set rotation rate"}],
        }}
        result = mapper.resolve(normalized, capabilities)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("motion_control", result["mappings"][0]["group_id"])
        self.assertEqual("set_rate", result["mappings"][0]["hal_entries"][0]["id"])

    def test_device_name_never_creates_implicit_mapping(self):
        mapper = load("resolve_capability_mapping")
        result = mapper.resolve({
            "device": {"model": "camera_radar_lidar"},
            "requested_features": [{"feature_id": "measure", "required": True}],
        }, {"camera": {"id": "camera", "entries": []}})
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["measure"], result["unmapped_features"])

    def test_functional_chain_has_no_device_category_dispatch(self):
        source = (ROOT / "scripts/functional_chain_check.py").read_text(encoding="utf-8")
        for marker in ("is_video", "is_radar", "is_pointcloud", "is_bus"):
            self.assertNotIn(marker, source)

    def test_functional_chain_is_generated_per_mapping(self):
        chain_module = load("functional_chain_check")
        mapping = {"mappings": [{
            "feature_id": "set_rotation_rate", "group_id": "motion_control",
            "hal_entries": [{"kind": "service", "id": "set_rate"}],
            "implementation_evidence": ["SDK_SetRate"], "tests": ["set_rate_nominal"],
            "transport_binding": "control",
        }]}
        transports = {"bindings": [{"binding_id": "control", "profile_id": "serial", "missing_context": []}]}
        chain, checklist, gaps = chain_module.build_capability_chains("demo", mapping, transports)
        self.assertFalse(gaps)
        self.assertEqual("set_rotation_rate", chain["chains"][0]["feature_id"])
        self.assertEqual(["hal_entry", "adapter_handler", "device_backend", "transport", "device_response", "hal_output", "test_evidence"],
                         [step["stage"] for step in chain["chains"][0]["steps"]])
        self.assertTrue(checklist["items"])

    def test_deployment_checks_come_from_mapping_metadata(self):
        deployment = load("generate_deployment_plan")
        mappings = [{
            "feature_id": "read_temperature", "group_id": "environment",
            "hal_entries": [{
                "kind": "property", "id": "temperature",
                "topic": {"name": "environment/temperature", "message_type": "sensor_msgs/msg/Temperature"},
            }],
            "tests": ["temperature_freshness"],
        }]
        checks = deployment.generated_checks("demo", mappings, "/hal/device/u_demo", "/hal/manager/u_demo")
        commands = "\n".join(item.get("command", "") for item in checks["capability"] + checks["functional"])
        self.assertIn("group_id: 'environment'", commands)
        self.assertIn("property_id: 'temperature'", commands)
        self.assertIn("/hal/device/u_demo/environment/temperature", commands)
        self.assertNotIn("camera/image_frame", commands)
        self.assertNotIn("imu/imu_data", commands)

    def test_single_device_deployment_uses_resolved_transport_not_vendor_sdk_default(self):
        deployment = load("generate_deployment_plan")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "deployment.yaml"
            deployment.write_deployment(path, "demo", "uuid", [{
                "binding_id": "control", "profile_id": "can",
                "config": {"interface": "can0", "bitrate": 500000},
            }])
            text = path.read_text()
        self.assertIn("protocol: can", text)
        self.assertIn("interface: can0", text)
        self.assertNotIn("sdk_auto_discovery", text)


if __name__ == "__main__":
    unittest.main()
