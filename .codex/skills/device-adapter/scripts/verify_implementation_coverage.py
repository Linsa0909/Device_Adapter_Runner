#!/usr/bin/env python3
"""Verify that Agent implementation evidence covers every inferred task."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

def evaluate(task: dict[str,Any], report: dict[str,Any]) -> dict[str,Any]:
    implemented={str(item.get("feature_id")):item for item in report.get("implemented_features",[]) if isinstance(item,dict)}
    missing=[]; gaps=[]
    bindings={str(item.get("binding_id")) for item in task.get("transport_bindings",[])}
    for expected in task.get("capability_tasks",[]):
        feature=str(expected.get("feature_id") or ""); actual=implemented.get(feature)
        if not actual: missing.append(feature); continue
        expected_entries={str(item.get("id")) for item in expected.get("hal_entries",[])}
        actual_entries={str(item) for item in actual.get("hal_entries",[])}
        if not expected_entries.issubset(actual_entries): gaps.append(f"{feature}: missing HAL entries")
        binding=str(expected.get("transport_binding") or "")
        if binding and (actual.get("transport_binding") != binding or binding not in bindings):
            gaps.append(f"{feature}: transport binding is not implemented")
        if not actual.get("backend_method"): gaps.append(f"{feature}: backend_method missing")
        source=actual.get("source") if isinstance(actual.get("source"),dict) else {}
        if not source.get("file") or not source.get("function"): gaps.append(f"{feature}: source function missing")
        expected_tests={str(item.get("id") if isinstance(item,dict) else item) for item in expected.get("tests",[])}
        if expected_tests and not expected_tests.issubset({str(item) for item in actual.get("tests",[])}):
            gaps.append(f"{feature}: independent tests missing")
    status="PASS" if report.get("status")=="PASS" and not missing and not gaps else "FAIL"
    return {"schema_version":"1.0","context_id":task.get("context_id"),"status":status,
            "mapping_policy":"current_context_only","missing_features":missing,"gaps":gaps}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("context_id"); args=parser.parse_args()
    contexts=Path("ops/contexts"); artifacts=Path("ops/artifacts")
    task_path=contexts/f"{args.context_id}.adapter_implementation_task.json"
    report_path=artifacts/f"{args.context_id}.adapter_implementation_report.json"
    output=artifacts/f"{args.context_id}.implementation_coverage.json"; output.parent.mkdir(parents=True,exist_ok=True)
    if not report_path.is_file():
        result={"schema_version":"1.0","context_id":args.context_id,"status":"WAITING_FOR_ADAPTER_AGENT",
                "task":str(task_path),"required_report":str(report_path)}
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(result))
        return 24
    task=json.loads(task_path.read_text(encoding="utf-8")); report=json.loads(report_path.read_text(encoding="utf-8"))
    result=evaluate(task,report)
    from quality_gate import source_fingerprint
    result["source_fingerprint"]=source_fingerprint(args.context_id)[0]
    result["evidence"]=[{"type":"file","value":str(task_path)},{"type":"file","value":str(report_path)}]
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(output); return 0 if result["status"]=="PASS" else 8
if __name__=="__main__": raise SystemExit(main())
