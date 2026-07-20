#!/usr/bin/env python3
"""Generate the deterministic skeleton of an independent HAL Adapter plugin."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from plugin_common import contract_errors, load_contract, missing_contract_fields, plugin_source_dir


def stage(name: str, status: str, exit_code: int | None = None) -> None:
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def write_failure(context_id: str, code: int, message: str) -> None:
    path = Path("ops/artifacts/last_failure.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "1.0", "context_id": context_id,
        "stage": "stage8_yaml_generate", "owner_agent": "yaml-writer-agent",
        "status": "fail", "exit_code": code, "error_code": "PLUGIN_ADAPT_CONTRACT_INVALID",
        "message": message, "next_action": f"Complete model and plugin contract for {context_id}",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_spec(context_id: str) -> dict[str, Any]:
    path = Path(f"ops/contexts/{context_id}.device_spec.json")
    if not path.is_file():
        raise FileNotFoundError(f"device spec not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def to_yaml(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.extend((f"{pad}{key}:", to_yaml(item, indent + 2)))
            elif item == []:
                lines.append(f"{pad}{key}: []")
            elif item == {}:
                lines.append(f"{pad}{key}: {{}}")
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.extend((f"{pad}-", to_yaml(item, indent + 2)))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def class_name(adapter: str) -> str:
    return "".join(part.capitalize() for part in adapter.split("_")) + "Adapter"


def render_cmake(contract: dict[str, Any]) -> str:
    adapter = contract["adapter_type"]
    return f'''cmake_minimum_required(VERSION 3.16)
project(hal_{adapter}_adapter_plugin LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN YES)
set(HAL_ADAPTER_SDK_ROOT "" CACHE PATH "Path to the immutable HAL Adapter SDK")
if(NOT HAL_ADAPTER_SDK_ROOT)
  message(FATAL_ERROR "Set -DHAL_ADAPTER_SDK_ROOT=/path/to/hal_adapter_sdk")
endif()
include("${{HAL_ADAPTER_SDK_ROOT}}/cmake/HalAdapterSdkConfig.cmake")
hal_adapter_sdk_require_abi({contract["sdk_abi"]})

add_library(hal_adapter_{adapter} SHARED
  src/{adapter}_adapter.cpp
  src/{adapter}_plugin.cpp
)
target_include_directories(hal_adapter_{adapter} PRIVATE "${{CMAKE_CURRENT_SOURCE_DIR}}/include")
target_link_libraries(hal_adapter_{adapter} PRIVATE HAL::model HAL::media)
set_target_properties(hal_adapter_{adapter} PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN YES
  OUTPUT_NAME "hal_adapter_{adapter}"
  BUILD_RPATH "${{HAL_ADAPTER_SDK_PLATFORM_LIB_DIR}}"
  INSTALL_RPATH "$ORIGIN/../deps"
)
install(TARGETS hal_adapter_{adapter} LIBRARY DESTINATION adapters)
install(FILES config/{adapter}.json DESTINATION config)
install(FILES model/devices/{adapter}.device.yaml DESTINATION model/devices)
install(FILES README.md DESTINATION .)
'''


def render_header(contract: dict[str, Any]) -> str:
    adapter = contract["adapter_type"]
    cls = class_name(adapter)
    return f'''#pragma once
#include "hardware_abstraction_layer/adapter/adapter_interface.hpp"
#include <functional>
#include <string>
#include <unordered_set>

namespace hal::adapters::{adapter} {{
class {cls} final : public IDeviceAdapter {{
public:
    ~{cls}() override;
    bool connect(const ConnectionConfig& config) override;
    void disconnect() override;
    bool isConnected() const override;
    ConnectionState getConnectionState() const override;
    AdapterHealth getHealth() const override;
    AdapterStats getStats() const override;
    DeviceInfo getDeviceInfo() const override;
    bool hasCapability(const std::string& capability) const override;
    std::unordered_set<std::string> getCapabilities() const override;
    void onConnectionStateChanged(std::function<void(ConnectionState)> callback) override;
    void onHealthChanged(std::function<void(AdapterHealth)> callback) override;
    void bindToEntity(hal::model::DeviceEntity& entity) override;
    void registerFastTelemetryCallback(const std::string& key, FastTelemetryCallback callback) override;
    bool pushFastCommand(const std::string& key, const std::any& command) override;
private:
    bool connected_ = false;
    ConnectionConfig config_;
    AdapterStats stats_;
    std::function<void(ConnectionState)> connection_callback_;
    std::function<void(AdapterHealth)> health_callback_;
}};
}}  // namespace hal::adapters::{adapter}
'''


def render_adapter(contract: dict[str, Any]) -> str:
    adapter = contract["adapter_type"]
    cls = class_name(adapter)
    groups = ", ".join(f'"{item}"' for item in contract["capability_group_refs"])
    return f'''#include "{adapter}/{adapter}_adapter.hpp"
#include <utility>

namespace hal::adapters::{adapter} {{
{cls}::~{cls}() {{ disconnect(); }}
bool {cls}::connect(const ConnectionConfig& config)
{{
    config_ = config;
    // Fail closed until the Agent implements and tests the documented transport.
    connected_ = false;
    return false;
}}
void {cls}::disconnect() {{ connected_ = false; if (connection_callback_) connection_callback_(ConnectionState::Disconnected); }}
bool {cls}::isConnected() const {{ return connected_; }}
ConnectionState {cls}::getConnectionState() const {{ return connected_ ? ConnectionState::Connected : ConnectionState::Disconnected; }}
AdapterHealth {cls}::getHealth() const {{ return connected_ ? AdapterHealth::Healthy : AdapterHealth::Unknown; }}
AdapterStats {cls}::getStats() const {{ return stats_; }}
DeviceInfo {cls}::getDeviceInfo() const {{ DeviceInfo info; info.manufacturer = "{contract["vendor"]}"; info.model = "{adapter}"; return info; }}
bool {cls}::hasCapability(const std::string& value) const {{ return getCapabilities().count(value) != 0; }}
std::unordered_set<std::string> {cls}::getCapabilities() const {{ return {{{groups}}}; }}
void {cls}::onConnectionStateChanged(std::function<void(ConnectionState)> value) {{ connection_callback_ = std::move(value); }}
void {cls}::onHealthChanged(std::function<void(AdapterHealth)> value) {{ health_callback_ = std::move(value); }}
void {cls}::bindToEntity(hal::model::DeviceEntity& value) {{ (void)value; }}
void {cls}::registerFastTelemetryCallback(const std::string& key, FastTelemetryCallback value) {{ (void)key; (void)value; }}
bool {cls}::pushFastCommand(const std::string& key, const std::any& value) {{ (void)key; (void)value; return false; }}
}}  // namespace hal::adapters::{adapter}
'''


def render_entry(contract: dict[str, Any]) -> str:
    adapter = contract["adapter_type"]
    cls = class_name(adapter)
    return f'''#include "{adapter}/{adapter}_adapter.hpp"
#include "hardware_abstraction_layer/adapter/plugin_sdk/adapter_plugin_api.hpp"
#include <cstdint>
#include <string>

namespace {{
std::size_t typeCount() {{ return 1; }}
const char* typeAt(std::size_t index) {{ return index == 0 ? "{adapter}" : nullptr; }}
bool supports(const char* type) {{ return type && std::string(type) == "{adapter}"; }}
hal::adapters::IDeviceAdapter* create(const char* type) {{ return supports(type) ? new hal::adapters::{adapter}::{cls}() : nullptr; }}
void destroy(hal::adapters::IDeviceAdapter* value) {{ delete value; }}
}}
extern "C" std::uint32_t hal_get_adapter_sdk_abi_v1() {{ return hal::adapters::plugin_sdk::kAdapterSdkAbiVersion; }}
extern "C" const hal::adapters::plugin_sdk::AdapterPluginApiV1* hal_get_adapter_plugin_v1()
{{
    static const hal::adapters::plugin_sdk::AdapterPluginApiV1 api{{
        {contract["plugin_abi"]}, "{contract["vendor"]}.{adapter}", "{contract["vendor"]}", "{contract["plugin_version"]}",
        &typeCount, &typeAt, &supports, &create, &destroy,
    }};
    return &api;
}}
'''


def render_readme(contract: dict[str, Any]) -> str:
    return f'''# {contract["adapter_type"]} HAL Adapter Plugin

- Plugin version: `{contract["plugin_version"]}`
- HAL Adapter SDK / ABI: `{contract["sdk_version"]}` / `{contract["sdk_abi"]}`
- Plugin ABI: `{contract["plugin_abi"]}`
- Target: `{contract["target_arch"]}` / `{contract["target_platform"]}`
- Runtime image: `{contract["runtime_image"]}`
- Platform capability groups: `{", ".join(contract["capability_group_refs"])}`
- Multi-instance: required

Complete transport, private dependencies, hardware requirements, instance
resource allocation, healthchecks, known limitations, and upgrade notes from
device evidence before release.
'''


def render_private_config(contract: dict[str, Any], spec: dict[str, Any]) -> str:
    declared = contract.get("private_config") or {}
    payload = spec.get("private_config")
    if not isinstance(payload, dict):
        payload = {
            "schema_version": str(declared.get("schema_version") or "1.0"),
            "instances": [],
        }
    payload.setdefault("schema_version", str(declared.get("schema_version") or "1.0"))
    payload.setdefault("instances", [])
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_output(path: Path, content: str, force: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--force", action="store_true")
    args, _ = parser.parse_known_args()
    stage("stage8_yaml_generate", "start")
    try:
        contract = load_contract(args.context_id)
        missing = missing_contract_fields(contract)
        invalid = contract_errors(contract)
        if missing or invalid:
            raise ValueError("invalid plugin contract: " + ", ".join(missing + invalid))
        spec = load_spec(args.context_id)
        adapter = str(contract["adapter_type"])
        if spec.get("adapter_type") != adapter:
            raise ValueError("device_spec.adapter_type must match plugin contract")
        model = spec.get("device_model")
        if not isinstance(model, dict):
            raise ValueError("device_spec.device_model is required")
        model.setdefault("schema_version", "2.0")
        model.setdefault("profile", {})["adapter_type"] = adapter
        refs = [item.get("group_id") for item in model.get("capability_groups", []) if isinstance(item, dict)]
        expected = list(contract["capability_group_refs"])
        if len(refs) != len(expected) or set(refs) != set(expected):
            raise ValueError("device model must reference exactly the contract capability groups")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        write_failure(args.context_id, 8, str(exc))
        stage("stage8_yaml_generate", "fail", 8)
        return 8

    root = plugin_source_dir(contract)
    outputs = {
        root / "CMakeLists.txt": render_cmake(contract),
        root / "README.md": render_readme(contract),
        root / f"include/{adapter}/{adapter}_adapter.hpp": render_header(contract),
        root / f"src/{adapter}_adapter.cpp": render_adapter(contract),
        root / f"src/{adapter}_plugin.cpp": render_entry(contract),
        root / f"config/{adapter}.json": render_private_config(contract, spec),
        root / f"model/devices/{adapter}.device.yaml": "# References platform-owned capability groups.\n" + to_yaml(model) + "\n",
    }
    changed = [path for path, content in outputs.items() if write_output(path, content, args.force)]
    generated = Path(f"ops/artifacts/{args.context_id}.generated_files.txt")
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("\n".join(sorted(path.as_posix() for path in outputs)) + "\n", encoding="utf-8")
    gaps = Path(f"ops/artifacts/{args.context_id}.adapter_gaps.md")
    gaps.write_text(
        f"# Runtime Plugin Gaps: {args.context_id}\n\n"
        "- Generated transport is intentionally fail-closed.\n"
        "- Implement documented transport/protocol/HAL behavior with TDD.\n"
        "- Materialize private dependencies and two-instance tests.\n"
        "- Do not modify platform capability groups, Factory, Registry, or main CMake.\n",
        encoding="utf-8",
    )
    print(f"plugin_source: {root}")
    for path in changed:
        print(f"generated_or_updated: {path}")
    stage("stage8_yaml_generate", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
