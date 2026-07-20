#!/usr/bin/env python3
"""Normalize evidence-backed device facts without device-name inference."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

def normalize(source: dict[str, Any]) -> dict[str, Any]:
    device = source.get("device") if isinstance(source.get("device"), dict) else {}
    features = []
    for raw in source.get("requested_features", []):
        if not isinstance(raw, dict) or not raw.get("feature_id"): continue
        features.append({
            "feature_id": str(raw["feature_id"]), "description": str(raw.get("description") or raw["feature_id"]),
            "direction": str(raw.get("direction") or "telemetry"), "required": bool(raw.get("required", True)),
            "capability_group_candidates": list(raw.get("capability_group_candidates") or []),
            "implementation_evidence": list(raw.get("implementation_evidence") or []),
            "source_evidence": list(raw.get("source_evidence") or []),
        })
    unknowns = list(source.get("unknowns") or [])
    for field in ("vendor", "model", "adapter_type"):
        if not device.get(field): unknowns.append({"field": f"device.{field}", "reason": "not confirmed by evidence"})
    return {"schema_version":"1.0","device":{k:str(device.get(k) or "") for k in ("vendor","model","adapter_type")},
            "requested_features":features,"protocols":list(source.get("protocols") or []),
            "vendor_sdks":list(source.get("vendor_sdks") or []),
            "transport_candidates":list(source.get("transport_candidates") or []),"unknowns":unknowns}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("context_id"); args=parser.parse_args()
    base=Path("ops/contexts")
    candidates=[base/f"{args.context_id}.device_observation.json",base/f"{args.context_id}.device_spec.json"]
    source=next((json.loads(p.read_text(encoding="utf-8")) for p in candidates if p.is_file()),{})
    output=base/f"{args.context_id}.normalized_context.json"
    output.write_text(json.dumps(normalize(source),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(output); return 0
if __name__ == "__main__": raise SystemExit(main())
