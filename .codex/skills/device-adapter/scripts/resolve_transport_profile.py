#!/usr/bin/env python3
"""Resolve evidence-backed transport profiles."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

def resolve(normalized: dict[str, Any], profile_dir: Path) -> dict[str, Any]:
    profiles={p.stem:json.loads(p.read_text(encoding="utf-8")) for p in profile_dir.glob("*.json")}
    bindings=[]
    for index,candidate in enumerate(normalized.get("transport_candidates",[])):
        profile_id=str(candidate.get("profile_id") or ""); profile=profiles.get(profile_id)
        config=candidate.get("config") if isinstance(candidate.get("config"),dict) else {}
        missing=[k for k in (profile or {}).get("required_context",[]) if config.get(k) in (None,"",[])]
        bindings.append({"binding_id":str(candidate.get("binding_id") or f"{profile_id or 'unknown'}-{index}"),
            "profile_id":profile_id,"transport_class":(profile or {}).get("transport_class"),
            "config_ref":str(candidate.get("config_ref") or f"config.transport.{profile_id or index}"),
            "config":config,"missing_context":missing if profile else ["known_profile_id"],
            "test_requirements":(profile or {}).get("test_requirements",[])})
    gaps=[f"{x['binding_id']}: {', '.join(x['missing_context'])}" for x in bindings if x["missing_context"]]
    if not bindings: gaps.append("no transport candidate confirmed")
    return {"schema_version":"1.0","status":"PASS" if not gaps else "BLOCKED","bindings":bindings,"gaps":gaps}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("context_id"); args=parser.parse_args()
    source=Path(f"ops/contexts/{args.context_id}.normalized_context.json")
    result=resolve(json.loads(source.read_text(encoding="utf-8")),Path(__file__).resolve().parents[1]/"profiles/transports")
    output=Path(f"ops/contexts/{args.context_id}.transport_bindings.json")
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(output)
    return 0 if result["status"]=="PASS" else 8
if __name__ == "__main__": raise SystemExit(main())
