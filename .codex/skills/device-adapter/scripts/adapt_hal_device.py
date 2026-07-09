#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


HAL_ROOT = Path("src/hardware_abstraction_layer")
MODEL_ROOT = HAL_ROOT / "model"
CONFIG_PATH = HAL_ROOT / "config" / "deployment.yaml"


def stage(name, status, exit_code=None):
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def write_failure(stage_name, command, exit_code, message, **extra):
    out = Path("ops/artifacts/last_failure.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"stage": stage_name, "command": command, "exit_code": exit_code, "message": message}
    payload.update(extra)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slug(value):
    value = value.strip().lower().replace("-", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_") or "device"


def read_context(context_id):
    path = Path(f"ops/contexts/{context_id}.context.md")
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def load_spec(context_id):
    path = Path(f"ops/contexts/{context_id}.device_spec.json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def to_yaml(value, indent=0):
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}"
        lines = []
        for key, item in value.items():
            if item == []:
                lines.append(f"{pad}{key}: []")
            elif item == {}:
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def infer_fields(context_id, text, spec=None):
    adapter_type = slug(context_id)
    spec = spec or {}
    if spec.get("adapter_type"):
        adapter_type = slug(spec["adapter_type"])
    m = re.search(r"adapter_type\s*[:=]+\s*([A-Za-z0-9_-]+)", text)
    if m:
        adapter_type = slug(m.group(1))
    elif re.search(r"\bmino17\b", text, re.I):
        adapter_type = "mino17"
    capability_id = adapter_type
    if re.search(r"infrared|红外|thermal", text, re.I):
        capability_id = "infrared_camera"
    elif re.search(r"gimbal|云台", text, re.I):
        capability_id = "gimbal"
    elif re.search(r"camera|相机|摄像", text, re.I):
        capability_id = "camera"

    manufacturer = ""
    model = context_id
    if isinstance(spec.get("device"), dict):
        manufacturer = spec["device"].get("manufacturer", manufacturer)
        model = spec["device"].get("model", model)
    if isinstance(spec.get("capability"), dict) and spec["capability"].get("id"):
        capability_id = slug(spec["capability"]["id"])
    m = re.search(r"(?:manufacturer|厂商|厂家|制造商)\s*(?:是|为|:|：)?\s*([^\n，,。]+)", text, re.I)
    if m:
        manufacturer = m.group(1).strip()
    m = re.search(r"(?:model|型号|设备型号)\s*(?:是|为|:|：)?\s*([^\n，,。]+)", text, re.I)
    if m:
        model = m.group(1).strip()

    device_path = "/dev/infrared_camera" if capability_id == "infrared_camera" else ""
    paths = re.findall(r"/dev/[A-Za-z0-9_.-]+", text)
    if paths:
        device_path = paths[0]
    if isinstance(spec.get("connection"), dict) and spec["connection"].get("device_path"):
        device_path = spec["connection"]["device_path"]

    output_url = "rtmp://127.0.0.1:1935/ghostapp/uav0"
    m = re.search(r"(rtmp://[^\s，,。]+)", text, re.I)
    if m:
        output_url = m.group(1).strip()
    if isinstance(spec.get("connection"), dict) and spec["connection"].get("output_url"):
        output_url = spec["connection"]["output_url"]

    protocol = "device_node" if device_path else "custom"
    if re.search(r"udp", text, re.I):
        protocol = "udp"
    elif re.search(r"serial|串口", text, re.I):
        protocol = "serial"
    if isinstance(spec.get("connection"), dict) and spec["connection"].get("protocol"):
        protocol = spec["connection"]["protocol"]

    return {
        "adapter_type": adapter_type,
        "capability_id": capability_id,
        "manufacturer": manufacturer,
        "model": model,
        "device_path": device_path,
        "output_url": output_url,
        "protocol": protocol,
        "spec": spec,
    }


def write_if_missing(path, content, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def render_capability(fields):
    spec_capability = (fields.get("spec") or {}).get("capability")
    if isinstance(spec_capability, dict):
        capability = dict(spec_capability)
        capability.setdefault("id", fields["capability_id"])
        capability.setdefault("name", fields["capability_id"].replace("_", " ").title())
        capability.setdefault("version", "1.0")
        return "# Generated by device-adapter from device_spec.json.\n" + to_yaml(capability) + "\n"

    cap = fields["capability_id"]
    title = cap.replace("_", " ").title()
    return f"""# Generated by device-adapter from natural language context.
id: {cap}
name: "{title}"
description: "{title} capability model generated for HAL adapter {fields['adapter_type']}"
version: "1.0"

properties:
  - id: connection_state
    name: "Connection State"
    required: true
    access: read_only
    spec:
      type: enum
      enum_values:
        - {{ key: "disconnected", label: "Disconnected", int_value: 0 }}
        - {{ key: "connecting", label: "Connecting", int_value: 1 }}
        - {{ key: "connected", label: "Connected", int_value: 2 }}
        - {{ key: "error", label: "Error", int_value: 3 }}
  - id: health
    name: "Health"
    required: true
    access: read_only
    spec:
      type: enum
      enum_values:
        - {{ key: "unknown", label: "Unknown", int_value: 0 }}
        - {{ key: "healthy", label: "Healthy", int_value: 1 }}
        - {{ key: "degraded", label: "Degraded", int_value: 2 }}
        - {{ key: "unhealthy", label: "Unhealthy", int_value: 3 }}

services:
  - id: start
    name: "Start"
    required: false
    is_async: false
    input_params: []
    output_params:
      - id: success
        spec: {{ type: bool }}
      - id: message
        spec: {{ type: string }}
  - id: stop
    name: "Stop"
    required: false
    is_async: false
    input_params: []
    output_params:
      - id: success
        spec: {{ type: bool }}
      - id: message
        spec: {{ type: string }}

events:
  - id: device_error
    name: "Device Error"
    trigger: event
    enabled: true
    output_params:
      - id: error_code
        spec: {{ type: int32 }}
      - id: message
        spec: {{ type: string }}
"""


def render_device(fields):
    spec_model = (fields.get("spec") or {}).get("device_model")
    if isinstance(spec_model, dict):
        model = dict(spec_model)
        model.setdefault("schema_version", "2.0")
        model.setdefault("profile", {})
        model["profile"].setdefault("adapter_type", fields["adapter_type"])
        model.setdefault("capability_groups", [{"group_id": fields["capability_id"], "enabled": True}])
        return "# Generated by device-adapter from device_spec.json.\n" + to_yaml(model) + "\n"

    connection_extra = ""
    if fields["device_path"]:
        connection_extra += f'  address: "{fields["device_path"]}"\n'
    if fields["output_url"]:
        connection_extra += f'  output_url: "{fields["output_url"]}"\n'
    return f"""# Generated by device-adapter from natural language context.
schema_version: "2.0"

profile:
  manufacturer: "{fields['manufacturer']}"
  model: "{fields['model']}"
  adapter_type: "{fields['adapter_type']}"
  protocol: "{fields['protocol']}"
  firmware_version: ""
  hardware_version: ""
  serial_number: ""

connection:
  transport: "{fields['protocol']}"
{connection_extra}  command_timeout_ms: 1000
  heartbeat_interval_ms: 500
  auto_reconnect: true
  max_reconnect_attempts: 5

capability_groups:
  - group_id: {fields['capability_id']}
    enabled: true
    property_overrides: {{}}
    service_overrides: {{}}
    event_overrides: {{}}
"""


def deployment_entry(fields):
    spec_deployment = (fields.get("spec") or {}).get("deployment_entry")
    if isinstance(spec_deployment, dict):
        entry = dict(spec_deployment)
        entry.setdefault("adapter_type", fields["adapter_type"])
        entry.setdefault("instance_index", 0)
        entry.setdefault("enabled", True)
        return to_yaml([entry], 4).rstrip() + "\n"

    lines = [
        f"    - adapter_type: {fields['adapter_type']}",
        "      instance_index: 0",
        f'      device_name: "{fields["model"]}"',
        "      enabled: true",
        "      connection:",
        f"        protocol: {fields['protocol']}",
    ]
    if fields["device_path"]:
        lines.append(f'        port: "{fields["device_path"]}"')
    lines.extend(["      params:"])
    if fields["device_path"]:
        lines.append(f'        camera_device: "{fields["device_path"]}"')
    if fields["output_url"]:
        lines.append(f'        output_url: "{fields["output_url"]}"')
    return "\n".join(lines) + "\n"


def update_deployment(fields, force=False):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            'deployment:\n  version: "1.0"\n  namespace: "/hal/device"\n  devices:\n'
            + deployment_entry(fields),
            encoding="utf-8",
        )
        return True
    text = CONFIG_PATH.read_text(encoding="utf-8")
    if re.search(rf"adapter_type:\s*{re.escape(fields['adapter_type'])}\b", text) and not force:
        return False
    if "  devices:" not in text:
        text = text.rstrip() + "\n  devices:\n"
    text = text.rstrip() + "\n" + deployment_entry(fields)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    return True


def adapter_exists(adapter_type):
    return any((HAL_ROOT / "src" / "adapter").glob(f"**/{adapter_type}*")) or any(
        (HAL_ROOT / "include" / "hardware_abstraction_layer" / "adapter").glob(f"**/{adapter_type}*")
    )


def write_gap_report(context_id, fields, created):
    report = Path(f"ops/artifacts/{context_id}.adapter_gaps.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    exists = adapter_exists(fields["adapter_type"])
    requirements = (fields.get("spec") or {}).get("adapter_requirements") or {}
    content = [
        f"# Adapter Gap Report: {context_id}",
        "",
        f"- adapter_type: `{fields['adapter_type']}`",
        f"- capability_id: `{fields['capability_id']}`",
        f"- adapter_code_exists: `{str(exists).lower()}`",
        "",
        "## Generated Or Updated",
    ]
    content.extend(f"- `{item}`" for item in created)
    content.extend([
        "",
        "## Adapter Requirements",
        "",
        f"- apt_build: `{requirements.get('apt_build') or requirements.get('apt_packages') or []}`",
        f"- apt_runtime: `{requirements.get('apt_runtime') or requirements.get('apt_packages') or []}`",
        f"- sdk_headers: `{requirements.get('sdk_headers') or []}`",
        f"- sdk_libraries: `{requirements.get('sdk_libraries') or []}`",
        f"- protocol_files: `{requirements.get('protocol_files') or []}`",
        f"- subprocesses: `{requirements.get('subprocesses') or []}`",
        "",
        "## Remaining Work",
    ])
    if not exists:
        content.extend([
            "- Add adapter header/source under `include/hardware_abstraction_layer/adapter/<adapter>/` and `src/adapter/<adapter>/`.",
            "- Register the adapter in `adapter_factory.hpp`.",
            "- Add the adapter target and source list in `hardware_abstraction_layer/CMakeLists.txt`.",
            "- Add protocol parser/transport implementation from the device documentation.",
            "- Add required SDK headers/libraries under `3rdparty/` with architecture-specific directories when needed.",
        ])
    else:
        content.append("- Adapter code already exists. Verify protocol behavior and hardware runtime logs.")
    report.write_text("\n".join(content) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    command = "adapt_hal_device.py " + args.context_id

    stage("stage2_context_validate", "start")
    if not (HAL_ROOT / "package.xml").exists():
        stage("stage2_context_validate", "fail", 2)
        write_failure("stage2_context_validate", command, 2, "Run from HAL ROS2 workspace root; src/hardware_abstraction_layer/package.xml not found.")
        return 2
    try:
        context = read_context(args.context_id)
    except FileNotFoundError as exc:
        stage("stage2_context_validate", "fail", 3)
        write_failure("stage2_context_validate", command, 3, f"Context not found: {exc}", next_action=f"/device-adapter context {args.context_id}")
        return 3
    spec = load_spec(args.context_id)
    stage("stage2_context_validate", "success")

    stage("stage3_hal_adapt", "start")
    fields = infer_fields(args.context_id, context, spec)
    cap_path = MODEL_ROOT / "capability_groups" / f"{fields['capability_id']}.capability.yaml"
    dev_path = MODEL_ROOT / "devices" / f"{fields['adapter_type']}.device.yaml"
    created = []
    if write_if_missing(cap_path, render_capability(fields), args.force):
        created.append(cap_path.as_posix())
    if write_if_missing(dev_path, render_device(fields), args.force):
        created.append(dev_path.as_posix())
    if update_deployment(fields, args.force):
        created.append(CONFIG_PATH.as_posix())
    report = write_gap_report(args.context_id, fields, created)
    print(f"adapter_type: {fields['adapter_type']}")
    print(f"capability_id: {fields['capability_id']}")
    if spec:
        print(f"device_spec: ops/contexts/{args.context_id}.device_spec.json")
    print(f"gap_report: {report}")
    for item in created:
        print(f"generated_or_updated: {item}")
    stage("stage3_hal_adapt", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
