#!/usr/bin/env python3
"""Map requested features to capability groups discovered from the immutable SDK."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

def resolve(normalized: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    mappings=[]; unmapped=[]
    for feature in normalized.get("requested_features",[]):
        candidates=[str(x) for x in feature.get("capability_group_candidates",[])]
        group=next((x for x in candidates if x in capabilities),None)
        if group:
            mappings.append({"feature_id":feature["feature_id"],"group_id":group,
                "implementation_evidence":feature.get("implementation_evidence",[]),
                "source_evidence":feature.get("source_evidence",[]),"tests":feature.get("tests",[])})
        elif feature.get("required",True): unmapped.append(feature["feature_id"])
    return {"schema_version":"1.0","status":"PASS" if not unmapped else "BLOCKED",
            "mappings":mappings,"unmapped_features":unmapped,"unused_capabilities":sorted(set(capabilities)-{m["group_id"] for m in mappings})}

def discover(root: Path) -> dict[str, Any]:
    found={}
    for path in root.rglob("*.capability.yaml"):
        text=path.read_text(encoding="utf-8",errors="replace")
        for line in text.splitlines():
            if line.startswith("id:"):
                found[line.split(":",1)[1].strip().strip("'\"")]={"path":path.as_posix()}; break
    return found

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("context_id"); args=parser.parse_args()
    contexts=Path("ops/contexts"); normalized=json.loads((contexts/f"{args.context_id}.normalized_context.json").read_text())
    contract=json.loads((contexts/f"{args.context_id}.plugin_contract.json").read_text())
    sdk=Path(contract["sdk_root"]); capabilities=discover(sdk/"model_reference/capability_groups")
    result=resolve(normalized,capabilities); output=contexts/f"{args.context_id}.capability_mapping.json"
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(output)
    return 0 if result["status"]=="PASS" else 8
if __name__ == "__main__": raise SystemExit(main())
