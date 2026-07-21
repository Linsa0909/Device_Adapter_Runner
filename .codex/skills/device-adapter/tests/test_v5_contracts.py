import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class V5ContractTests(unittest.TestCase):
    def test_workflow_is_valid_dag_with_boundaries(self):
        workflow = json.loads((ROOT / "scripts/workflow_definition.json").read_text())
        stages = workflow["stages"]
        self.assertIn("stage10_adapter_codegen", stages)
        for name, stage in stages.items():
            self.assertTrue(stage["owner"], name)
            self.assertIn("depends_on", stage, name)
            self.assertIn("write_roots", stage, name)
            self.assertIn("deny_roots", stage, name)
            for dependency in stage["depends_on"]:
                self.assertIn(dependency, stages)

        visited, active = set(), set()

        def visit(name):
            self.assertNotIn(name, active, f"workflow cycle at {name}")
            if name in visited:
                return
            active.add(name)
            for dependency in stages[name]["depends_on"]:
                visit(dependency)
            active.remove(name)
            visited.add(name)

        for name in stages:
            visit(name)

        orchestrator = load("stage_orchestrator")
        self.assertEqual(set(stages), set(orchestrator.STAGES))
        source = (ROOT / "scripts/stage_orchestrator.py").read_text()
        self.assertNotIn("STAGES.update", source)
        self.assertNotIn("agent_boundary_policy.json", source)

    def test_legacy_stage_maps_are_not_parallel_sources(self):
        self.assertFalse((ROOT / "scripts/agent_stage_map.json").exists())
        self.assertFalse((ROOT / "scripts/agent_boundary_policy.json").exists())

    def test_platform_profile_is_fixed(self):
        profile = json.loads((ROOT / "profiles/platform/yunshu-aarch64-humble.json").read_text())
        self.assertEqual("aarch64", profile["target_arch"])
        self.assertEqual("humble", profile["ros_distro"])
        self.assertEqual("rmw_cyclonedds_cpp", profile["rmw_implementation"])
        self.assertEqual("/workspace/yunshu", profile["project_mount"])

    def test_all_transport_profiles_are_machine_readable(self):
        expected = {"serial", "can", "udp", "tcp", "usb", "uvc", "vendor-sdk"}
        found = set()
        for path in (ROOT / "profiles/transports").glob("*.json"):
            profile = json.loads(path.read_text())
            found.add(profile["profile_id"])
            self.assertIn(profile["transport_class"], {
                "byte_stream", "datagram", "frame_source", "vendor_runtime"
            })
            self.assertTrue(profile["connection_lifecycle"])
            self.assertTrue(profile["error_model"])
            self.assertTrue(profile["test_requirements"])
        self.assertEqual(expected, found)

    def test_normalization_does_not_guess_unknowns(self):
        module = load("normalize_device_context")
        source = {
            "device": {"vendor": "Acme", "model": "X1", "adapter_type": "x1"},
            "requested_features": [{"feature_id": "distance", "required": True}],
            "transport_candidates": [{"profile_id": "can", "config": {"interface": "auto"}}],
        }
        result = module.normalize(source)
        self.assertEqual("distance", result["requested_features"][0]["feature_id"])
        self.assertIn("source_evidence", result["requested_features"][0])
        self.assertIn("unknowns", result)

    def test_transport_resolution_blocks_missing_required_context(self):
        module = load("resolve_transport_profile")
        normalized = {
            "transport_candidates": [{"profile_id": "serial", "config": {"device": "/dev/ttyUSB0"}}]
        }
        report = module.resolve(normalized, ROOT / "profiles/transports")
        self.assertEqual("BLOCKED", report["status"])
        self.assertIn("baud_rate", report["bindings"][0]["missing_context"])

    def test_capability_mapping_blocks_required_unmapped_feature(self):
        module = load("resolve_capability_mapping")
        normalized = {
            "requested_features": [{
                "feature_id": "unknown_feature", "description": "x", "required": True,
                "capability_group_candidates": [], "source_evidence": ["manual p1"]
            }]
        }
        result = module.resolve(normalized, {"camera": {"id": "camera"}})
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["unknown_feature"], result["unmapped_features"])

    def test_adapter_task_contains_capabilities_transports_and_boundaries(self):
        module = load("generate_adapter_task")
        task = module.build_task(
            "demo",
            {"adapter_type": "demo", "plugin_source_dir": "adapter_plugins/demo"},
            {"mappings": [{"feature_id": "measure", "group_id": "sensor"}]},
            {"bindings": [{"binding_id": "data", "profile_id": "serial"}]},
        )
        self.assertEqual("hal-adapter-builder", task["owner_agent"])
        self.assertTrue(task["implementation_requirements"])
        self.assertIn("adapter_plugins/demo/tests/**", task["write_denylist"])

    def test_adapt_and_verify_define_agent_handoff_stages(self):
        stages = json.loads((ROOT / "scripts/workflow_definition.json").read_text())["stages"]
        for name in (
            "stage9_pre_adapt_verification", "stage9a_test_design",
            "stage10_adapter_codegen", "stage11a_independent_verification",
            "stage11b_cpp_review", "stage11c_differential_review",
        ):
            self.assertEqual(stages[name]["kind"], "agent_handoff")

    def test_review_roles_have_non_overlapping_outputs(self):
        stages = json.loads((ROOT / "scripts/workflow_definition.json").read_text())["stages"]
        self.assertEqual(stages["stage11a_independent_verification"]["owner"], "verification-agent")
        self.assertEqual(stages["stage11b_cpp_review"]["owner"], "c-review")
        self.assertEqual(stages["stage11c_differential_review"]["owner"], "differential-review")
        prompt = (ROOT.parents[1] / "agents/verification-agent.toml").read_text()
        self.assertNotIn("c_review_report.json", prompt)
        self.assertNotIn("differential_review_report.json", prompt)

    def test_builder_prompt_requires_machine_readable_implementation_report(self):
        prompt = (ROOT.parents[1] / "agents/hal-adapter-builder.toml").read_text()
        self.assertIn("adapter_implementation_report.json", prompt)
        self.assertIn("implemented_features", prompt)


if __name__ == "__main__":
    unittest.main()
