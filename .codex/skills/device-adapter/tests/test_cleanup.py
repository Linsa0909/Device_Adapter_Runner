import importlib.util, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cleanup_run",ROOT/"scripts/cleanup_run.py")
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class CleanupTests(unittest.TestCase):
    def test_only_run_children_are_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); run=root/"ops/runs/demo/r1"; doomed=run/"build"; doomed.mkdir(parents=True)
            protected=root/"ops/contexts/demo.context.md"; protected.parent.mkdir(parents=True); protected.write_text("keep")
            report=module.cleanup({"context_id":"demo","run_id":"r1","run_root":"ops/runs/demo/r1",
                "cleanup_paths":["ops/runs/demo/r1/build","ops/contexts/demo.context.md"]},root)
            self.assertFalse(doomed.exists()); self.assertTrue(protected.exists())
            self.assertEqual("PARTIAL",report["status"])

if __name__=="__main__": unittest.main()
