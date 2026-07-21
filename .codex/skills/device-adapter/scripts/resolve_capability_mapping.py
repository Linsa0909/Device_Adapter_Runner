#!/usr/bin/env python3
"""Map requested features to capability groups discovered from the immutable SDK."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any
import yaml

ENTRY_KINDS = (("properties", "property"), ("services", "service"), ("events", "event"))

def _entry_ids(feature: dict[str, Any]) -> list[str]:
    return [str(value) for value in feature.get("hal_entry_candidates", []) if str(value)]

def _select_entries(feature: dict[str, Any], capability: dict[str, Any]) -> list[dict[str, Any]]:
    requested = set(_entry_ids(feature))
    entries = list(capability.get("entries") or [])
    if requested:
        return [entry for entry in entries if str(entry.get("id")) in requested]
    feature_id = str(feature.get("feature_id") or "")
    return [entry for entry in entries if str(entry.get("id")) == feature_id]

def resolve(normalized: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    mappings=[]; unmapped=[]; conflicts=[]
    for feature in normalized.get("requested_features",[]):
        candidates=[str(x) for x in feature.get("capability_group_candidates",[])]
        group=next((x for x in candidates if x in capabilities),None)
        if group:
            entries = _select_entries(feature, capabilities[group])
            requested_entries = _entry_ids(feature)
            missing_entries = sorted(set(requested_entries) - {str(item.get("id")) for item in entries})
            if missing_entries:
                conflicts.append({"feature_id":feature["feature_id"], "group_id":group,
                                  "missing_hal_entries":missing_entries})
            mappings.append({"feature_id":feature["feature_id"],"group_id":group,
                "direction":feature.get("direction","telemetry"), "hal_entries":entries,
                "implementation_evidence":feature.get("implementation_evidence",[]),
                "source_evidence":feature.get("source_evidence",[]),"tests":feature.get("tests",[]),
                "transport_binding":feature.get("transport_binding","")})
        elif feature.get("required",True): unmapped.append(feature["feature_id"])
    return {"schema_version":"1.0","status":"PASS" if not unmapped and not conflicts else "BLOCKED",
            "mapping_policy":"context_evidence_only","mappings":mappings,"unmapped_features":unmapped,
            "conflicts":conflicts,"unused_capabilities":sorted(set(capabilities)-{m["group_id"] for m in mappings})}

def discover(root: Path) -> dict[str, Any]:
    found={}
    for path in root.rglob("*.capability.yaml"):
        try: payload=yaml.safe_load(path.read_text(encoding="utf-8",errors="replace"))
        except yaml.YAMLError: continue
        if not isinstance(payload,dict) or not payload.get("id"): continue
        entries=[]
        for yaml_key,kind in ENTRY_KINDS:
            for item in payload.get(yaml_key) or []:
                if not isinstance(item,dict) or not item.get("id"): continue
                topic_mode=item.get("topic_mode") if isinstance(item.get("topic_mode"),dict) else {}
                dedicated=topic_mode.get("dedicated_topic") if isinstance(topic_mode.get("dedicated_topic"),dict) else {}
                topic=None
                if topic_mode.get("publish") and dedicated.get("enabled"):
                    topic={"name":str(dedicated.get("topic_name") or f"{payload['id']}/{item['id']}"),
                           "message_type":str(dedicated.get("message_type") or "")}
                entries.append({"kind":kind,"id":str(item["id"]),"name":str(item.get("name") or ""),
                                "description":str(item.get("description") or ""),"topic":topic})
        found[str(payload["id"])]={"id":str(payload["id"]),"name":str(payload.get("name") or ""),
            "description":str(payload.get("description") or ""),"path":path.as_posix(),"entries":entries}
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
