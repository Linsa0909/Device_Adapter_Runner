#!/usr/bin/env python3
"""Build and validate a device functional-chain checklist.

This gate turns the user's context and device_spec runtime requirements into a
small, auditable checklist before HAL generation and packaging. It is deliberately
generic: it detects producers, processors, receivers, helper daemons, runtime
materials, and healthchecks from documented context/spec facts, without assuming
FFmpeg, ZLM, V4L2, RTMP, serial, CAN, UDP, TCP, lidar, radar, or cameras unless
the context/spec mentions them.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


CONTEXTS = Path("ops/contexts")
ARTIFACTS = Path("ops/artifacts")


def stage(name: str, status: str, exit_code: int | None = None) -> None:
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_failure(context_id: str, exit_code: int, message: str, gaps: list[dict[str, Any]]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    write_json(
        ARTIFACTS / "last_failure.json",
        {
            "schema_version": "1.0",
            "context_id": context_id,
            "stage": "stage4_functional_chain_check",
            "owner_agent": "acceptance-planner-agent",
            "status": "fail",
            "exit_code": exit_code,
            "error_code": "FUNCTIONAL_CHAIN_INCOMPLETE",
            "category": "contract",
            "summary": message,
            "gaps": gaps,
            "recommended_owner": "acceptance-planner-agent",
            "next_action": "Complete functional_chain/dependency_checklist/runtime_requirements, then rerun /device-adapter verify.",
        },
    )


def runtime_requirements(spec: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(spec.get("runtime_requirements") or {})
    legacy = spec.get("adapter_requirements") or {}
    mapping = {
        "sdk_headers": "vendor_headers",
        "sdk_libraries": "vendor_libraries",
        "apt_packages": "apt_runtime",
        "subprocesses": "subprocesses",
    }
    for old, new in mapping.items():
        if new not in runtime and old in legacy:
            runtime[new] = legacy[old]
    return runtime


def lower_blob(*values: Any) -> str:
    text = []
    for value in values:
        if isinstance(value, str):
            text.append(value)
        else:
            text.append(json.dumps(value, ensure_ascii=False))
    return "\n".join(text).lower()


def has_any(blob: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, blob, re.I) for pattern in patterns)


def item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    return json.dumps(item, ensure_ascii=False)


def names_from(items: list[Any]) -> list[str]:
    names = []
    for item in items:
        if isinstance(item, dict):
            for key in ("name", "role", "image", "path", "command", "protocol", "type"):
                value = item.get(key)
                if value:
                    names.append(str(value))
        else:
            names.append(str(item))
    return [name for name in names if name.strip()]


def extract_urls(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:rtmp|rtsp|http|https|udp|tcp)://[^\s，,。'\"`]+", text, re.I)))


def local_endpoint(url: str) -> bool:
    return bool(re.search(r"://(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::|/|$)", url, re.I))


def endpoint_protocol(url: str) -> str:
    return url.split("://", 1)[0].lower()


def declared_receiver(runtime: dict[str, Any], blob: str) -> bool:
    services = as_list(runtime.get("services")) + as_list(runtime.get("runtime_services")) + as_list(runtime.get("receivers"))
    graph = as_list(runtime.get("runtime_graph"))
    subprocesses = as_list(runtime.get("subprocesses"))
    candidates = " ".join(names_from(services + graph + subprocesses)).lower()
    if re.search(r"receiver|ingest|media|stream|rtmp|rtsp|hls|zlm|zlmediakit|server|broker|bridge|subscriber|sink", candidates):
        return True
    return bool(runtime.get("receiver") or runtime.get("stream_receiver"))


def declared_offline_material(runtime: dict[str, Any], blob: str) -> bool:
    keys = (
        "offline_images",
        "image_tars",
        "runtime_assets",
        "delivery_files",
        "vendor_libraries",
        "vendor_headers",
        "services",
        "subprocesses",
    )
    for key in keys:
        if as_list(runtime.get(key)):
            return True
    return False


def declared_healthcheck(runtime: dict[str, Any]) -> bool:
    return bool(as_list(runtime.get("healthchecks")) or as_list(runtime.get("endpoint_healthchecks")) or as_list(runtime.get("process_healthchecks")))


def build_capability_chains(context_id: str, mapping: dict[str, Any],
                            transports: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build chains only from context-derived capability and transport evidence."""
    bindings = {str(item.get("binding_id")): item for item in transports.get("bindings", [])}
    chains: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for item in mapping.get("mappings", []):
        feature_id = str(item.get("feature_id") or "")
        group_id = str(item.get("group_id") or "")
        entry_ids = [str(entry.get("id")) for entry in item.get("hal_entries", [])]
        binding_id = str(item.get("transport_binding") or "")
        binding = bindings.get(binding_id)
        steps = [
            {"stage":"hal_entry","evidence":{"group_id":group_id,"entries":entry_ids}},
            {"stage":"adapter_handler","evidence":{"implementation_required":True}},
            {"stage":"device_backend","evidence":item.get("implementation_evidence",[])},
            {"stage":"transport","evidence":binding or {}},
            {"stage":"device_response","evidence":item.get("source_evidence",[])},
            {"stage":"hal_output","evidence":{"direction":item.get("direction","telemetry")}},
            {"stage":"test_evidence","evidence":item.get("tests",[])},
        ]
        chains.append({"feature_id":feature_id,"group_id":group_id,"steps":steps})
        for step in steps:
            present = bool(step["evidence"])
            items.append({"feature_id":feature_id,"chain_stage":step["stage"],
                          "status":"declared" if present else "needs_evidence","evidence":step["evidence"]})
        missing = []
        if not entry_ids: missing.append("hal_entry")
        if not item.get("implementation_evidence"): missing.append("device_backend")
        if not binding or binding.get("missing_context"): missing.append("transport")
        if not item.get("tests"): missing.append("test_evidence")
        if missing:
            gaps.append({"code":"CAPABILITY_CHAIN_INCOMPLETE","severity":"blocking","feature_id":feature_id,
                         "message":f"{feature_id} lacks evidence for: {', '.join(missing)}",
                         "owner_agent":"acceptance-planner-agent"})
    if mapping.get("unmapped_features"):
        gaps.append({"code":"REQUIRED_FEATURE_UNMAPPED","severity":"blocking",
                     "message":"Required context features are not mapped: " + ", ".join(mapping["unmapped_features"]),
                     "owner_agent":"capability-modeler-agent"})
    if not chains:
        gaps.append({"code":"CAPABILITY_MAPPING_EMPTY","severity":"blocking",
                     "message":"No evidence-backed capability mappings exist.","owner_agent":"capability-modeler-agent"})
    chain_doc={"schema_version":"2.0","context_id":context_id,"generated_by":"functional_chain_check.py",
               "mapping_policy":"context_evidence_only","chains":chains,"assumptions":[]}
    checklist={"schema_version":"2.0","context_id":context_id,"generated_by":"functional_chain_check.py",
               "runtime_requirement_sources":["normalized_context.json","capability_mapping.json","transport_bindings.json"],
               "items":items,"gaps":gaps}
    return chain_doc, checklist, gaps


def build_chain(context_id: str, text: str, spec: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    runtime = runtime_requirements(spec)
    blob = lower_blob(text, spec, manifest)
    contract_blob = lower_blob(
        spec.get("adapter_type"),
        spec.get("device"),
        spec.get("connection"),
        spec.get("capability"),
        spec.get("device_model"),
        spec.get("deployment_entry"),
    )
    urls = extract_urls(text + "\n" + json.dumps(spec, ensure_ascii=False))
    chain: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    declared_services = as_list(runtime.get("services")) + as_list(runtime.get("runtime_services"))
    helper_process = bool(as_list(runtime.get("subprocesses")) or declared_services)
    gaps.append({
        "code": "CAPABILITY_MAPPING_REQUIRED",
        "severity": "blocking",
        "message": "Generate normalized context, SDK capability mapping and transport bindings before the functional chain.",
        "owner_agent": "capability-modeler-agent",
    })

    if helper_process:
        chain.append(
            {
                "name": "helper_runtime",
                "role": "processor",
                "purpose": "Run adapter-started helper executable, SDK daemon, media tool, or protocol bridge.",
                "evidence": "runtime_requirements.subprocesses or context mentions a helper process.",
                "requires": ["executable material", "library closure", "startup order", "process healthcheck"],
            }
        )

    endpoint_steps = []
    for url in urls:
        protocol = endpoint_protocol(url)
        endpoint_steps.append({"url": url, "protocol": protocol, "local": local_endpoint(url)})
    if endpoint_steps:
        chain.append(
            {
                "name": "publish_or_receive",
                "role": "sink_or_receiver",
                "purpose": "Deliver the device output to the documented endpoint and verify that endpoint receives data.",
                "evidence": "context/spec contains output URL(s).",
                "requires": ["receiver/service when endpoint is local", "ports", "configs/secrets", "endpoint healthcheck"],
                "endpoints": endpoint_steps,
            }
        )

    if not chain:
        chain.append(
            {
                "name": "device_function",
                "role": "unknown",
                "purpose": "Device function is not specific enough yet.",
                "evidence": "context/spec did not expose a recognizable acquisition, processing, or output chain.",
                "requires": ["manual-derived function stages", "runtime requirements", "success criteria"],
            }
        )
        gaps.append(
            {
                "code": "FUNCTIONAL_CHAIN_UNCLEAR",
                "severity": "blocking",
                "message": "Context/spec does not define what the device must acquire, process, output, or expose.",
                "owner_agent": "acceptance-planner-agent",
            }
        )

    for endpoint in endpoint_steps:
        if endpoint["local"] and endpoint["protocol"] in {"rtmp", "rtsp", "http", "https", "udp", "tcp"} and not declared_receiver(runtime, blob):
            gaps.append(
                {
                    "code": "LOCAL_ENDPOINT_RECEIVER_UNDECLARED",
                    "severity": "blocking",
                    "message": f"Local {endpoint['protocol']} endpoint {endpoint['url']} needs a declared receiver/service/daemon and startup order.",
                    "owner_agent": "acceptance-planner-agent",
                }
            )

    if endpoint_steps and not declared_healthcheck(runtime):
        gaps.append(
            {
                "code": "OUTPUT_HEALTHCHECK_UNDECLARED",
                "severity": "blocking",
                "message": "Output endpoint exists, but runtime_requirements.healthchecks does not prove real data delivery.",
                "owner_agent": "acceptance-planner-agent",
            }
        )

    if helper_process and not declared_offline_material(runtime, blob):
        gaps.append(
            {
                "code": "RUNTIME_MATERIAL_UNDECLARED",
                "severity": "blocking",
                "message": "A helper/runtime process is required, but executable/libs/image/config material is not declared.",
                "owner_agent": "sdk-dependency-auditor-agent",
            }
        )

    checklist_items = []
    for step in chain:
        for requirement in as_list(step.get("requires")):
            checklist_items.append(
                {
                    "chain_stage": step["name"],
                    "requirement": requirement,
                    "status": "needs_evidence",
                    "evidence": [],
                }
            )

    chain_doc = {
        "schema_version": "1.0",
        "context_id": context_id,
        "generated_by": "functional_chain_check.py",
        "chain": chain,
        "endpoints": endpoint_steps,
        "assumptions": [],
    }
    checklist = {
        "schema_version": "1.0",
        "context_id": context_id,
        "generated_by": "functional_chain_check.py",
        "runtime_requirement_sources": ["context.md", "device_spec.json", "manifest.json"],
        "items": checklist_items,
        "gaps": gaps,
    }
    return chain_doc, checklist, gaps


def write_gap_markdown(context_id: str, gaps: list[dict[str, Any]], chain_path: Path, checklist_path: Path) -> Path:
    path = ARTIFACTS / f"{context_id}.dependency_gaps.md"
    lines = [
        f"# Dependency And Functional Chain Gaps: {context_id}",
        "",
        f"- functional chain: `{chain_path.as_posix()}`",
        f"- dependency checklist: `{checklist_path.as_posix()}`",
        "",
    ]
    if not gaps:
        lines.append("No blocking functional-chain gaps detected by deterministic checks.")
    else:
        for gap in gaps:
            lines.append(f"- `{gap.get('code')}` ({gap.get('owner_agent')}): {gap.get('message')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--strict", action="store_true", help="fail on blocking functional-chain gaps")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate without rewriting generated context contracts",
    )
    args = parser.parse_args()

    stage("stage4_functional_chain_check", "start")
    context_path = CONTEXTS / f"{args.context_id}.context.md"
    manifest_path = CONTEXTS / f"{args.context_id}.manifest.json"
    spec_path = CONTEXTS / f"{args.context_id}.device_spec.json"
    if not context_path.exists():
        stage("stage4_functional_chain_check", "fail", 2)
        write_failure(args.context_id, 2, f"Missing context file: {context_path}", [])
        return 2

    text = context_path.read_text(encoding="utf-8", errors="ignore")
    manifest = read_json(manifest_path)
    spec = read_json(spec_path)
    mapping_path = CONTEXTS / f"{args.context_id}.capability_mapping.json"
    transport_path = CONTEXTS / f"{args.context_id}.transport_bindings.json"
    if mapping_path.is_file() and transport_path.is_file():
        chain, checklist, gaps = build_capability_chains(
            args.context_id, read_json(mapping_path), read_json(transport_path))
    else:
        chain, checklist, gaps = build_chain(args.context_id, text, spec, manifest)

    chain_path = CONTEXTS / f"{args.context_id}.functional_chain.json"
    checklist_path = CONTEXTS / f"{args.context_id}.dependency_checklist.json"
    if args.check_only:
        missing_contracts = [path.as_posix() for path in (chain_path, checklist_path) if not path.exists()]
        if missing_contracts:
            gaps.append(
                {
                    "code": "FUNCTIONAL_CHAIN_CONTRACT_MISSING",
                    "severity": "blocking",
                    "message": "Required model-stage contracts are missing: " + ", ".join(missing_contracts),
                    "owner_agent": "acceptance-planner-agent",
                }
            )
    else:
        write_json(chain_path, chain)
        write_json(checklist_path, checklist)
    gaps_path = write_gap_markdown(args.context_id, gaps, chain_path, checklist_path)
    write_json(
        ARTIFACTS / f"{args.context_id}.functional_chain_check.json",
        {
            "schema_version": "1.0",
            "context_id": args.context_id,
            "strict": args.strict,
            "check_only": args.check_only,
            "ok": not gaps,
            "chain": chain_path.as_posix(),
            "checklist": checklist_path.as_posix(),
            "gaps_file": gaps_path.as_posix(),
            "gaps": gaps,
        },
    )

    if gaps and args.strict:
        stage("stage4_functional_chain_check", "fail", 8)
        write_failure(args.context_id, 8, "Functional chain checklist has blocking gaps.", gaps)
        return 8

    print(f"functional_chain: {chain_path}")
    print(f"dependency_checklist: {checklist_path}")
    print(f"dependency_gaps: {gaps_path}")
    if gaps:
        print("blocking_gaps:")
        for gap in gaps:
            print(f"- {gap.get('code')}: {gap.get('message')}")
    stage("stage4_functional_chain_check", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
