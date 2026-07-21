#!/usr/bin/env python3
"""Create the evidence-bound task envelope consumed by the Adapter coding Agent."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

def build_task(context_id: str, contract: dict[str,Any], mapping: dict[str,Any], transports: dict[str,Any]) -> dict[str,Any]:
    root=str(contract.get("plugin_source_dir") or f"adapter_plugins/{contract['adapter_type']}")
    return {"schema_version":"1.0","context_id":context_id,"owner_agent":"hal-adapter-builder",
        "adapter_type":contract["adapter_type"],"source_root":root,"capability_tasks":mapping.get("mappings",[]),
        "transport_bindings":transports.get("bindings",[]),
        "implementation_requirements":["resource lifecycle","protocol or SDK translation","HAL capability binding","timeouts and reconnect","instance isolation","health and error mapping"],
        "write_allowlist":[f"{root}/src/**",f"{root}/include/**",f"{root}/CMakeLists.txt",f"{root}/config/**",f"{root}/cmake/**"],
        "write_denylist":["src/hardware_abstraction_layer/**","**/*.capability.yaml",f"{root}/tests/**"],
        "completion_outputs":[f"ops/artifacts/{context_id}.adapter_implementation_report.json"],
        "reasoning_policy":"current_context_and_immutable_sdk_only",
        "forbidden_inference":["device-name category binding","undocumented behavior copied from previous adapters"],
        "blocked_gaps":list(mapping.get("unmapped_features",[]))+list(mapping.get("conflicts",[]))+list(transports.get("gaps",[]))}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("context_id"); args=parser.parse_args(); base=Path("ops/contexts")
    load=lambda suffix:json.loads((base/f"{args.context_id}.{suffix}.json").read_text(encoding="utf-8"))
    task=build_task(args.context_id,load("plugin_contract"),load("capability_mapping"),load("transport_bindings"))
    output=base/f"{args.context_id}.adapter_implementation_task.json"; output.write_text(json.dumps(task,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(output)
    return 0 if not task["blocked_gaps"] else 8
if __name__ == "__main__": raise SystemExit(main())
