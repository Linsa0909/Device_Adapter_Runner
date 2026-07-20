#!/usr/bin/env python3
"""Verify v4 source boundaries and deterministic Adapter binding evidence."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, load_json, plugin_source_dir, write_json


FORBIDDEN_ROS = (
    r"#\s*include\s*[<\"]rclcpp(?:/|\.hpp)", r"\brclcpp::",
    r"\bcreate_(?:publisher|subscription|service|client)\s*\(",
)


def source_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}
    )


def passed_evidence(context_id: str, name: str) -> tuple[bool, dict]:
    path = ARTIFACTS / f"{context_id}.{name}.json"
    value = load_json(path) if path.is_file() else {"status": "NOT_RUN"}
    return value.get("status") == "PASS", value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    contract = load_contract(args.context_id)
    root = plugin_source_dir(contract)
    text = source_text(root) if root.is_dir() else ""
    ros_hits = [pattern for pattern in FORBIDDEN_ROS if re.search(pattern, text)]
    transport_url_hits = len(re.findall(r"\btransport_url\b", text))
    instance_index = "hal.instance_index" in text
    relative_config = "dladdr" in text
    binding_ok, binding_coverage = passed_evidence(args.context_id, "binding_coverage")
    fastpath_ok, fastpath_coverage = passed_evidence(args.context_id, "fastpath_coverage")
    config_ok, config_validation = passed_evidence(args.context_id, "config_parser_test")
    checks = {
        "no_direct_ros_dependency": not ros_hits,
        "transport_url_ignored": transport_url_hits == 0,
        "instance_selected_by_hal_instance_index": instance_index,
        "config_resolved_with_dladdr": relative_config,
        "binding_coverage": binding_ok,
        "fastpath_coverage": fastpath_ok,
        "config_parser_test": config_ok,
    }
    passed = root.is_dir() and all(checks.values())
    write_json(ARTIFACTS / f"{args.context_id}.source_contract_validation.json", {
        "schema_version": "1.0", "context_id": args.context_id,
        "status": "PASS" if passed else "FAIL", "plugin_source": str(root),
        "checks": checks, "forbidden_ros_patterns": ros_hits,
        "transport_url_occurrences": transport_url_hits,
        "binding_coverage": binding_coverage, "fastpath_coverage": fastpath_coverage,
        "config_parser_test": config_validation,
    })
    return 0 if passed else 12


if __name__ == "__main__":
    raise SystemExit(main())
