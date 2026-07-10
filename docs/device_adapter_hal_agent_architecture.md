# device-adapter HAL Agent Architecture

This document is the implementation contract for the docs-first HAL device adaptation workflow.

The workflow must not hard-code one device's runtime pattern. A device may use a vendor SDK, serial protocol, CAN, UDP/TCP, kernel driver, helper daemon, media pipeline, embedded service, or no subprocess at all. The user's context and device manuals define the requirements.

## Command Pipeline

```text
/device-adapter context <context_id>
/device-adapter model <context_id>
/device-adapter adapt <context_id> --allow-code
/device-adapter verify <context_id>
/device-adapter loop <context_id> --host <board> --user root -arm
/device-adapter rerun <context_id>
```

`stage_runner.sh` delegates staged actions to `stage_orchestrator.py`. The orchestrator has two stage types:

```text
agent handoff stage: verify that the expected agent artifact exists; fail precisely if missing.
script stage: run a deterministic script such as adapt_hal_device.py, verify_hal_adapter.py, package_by_manifest.py, or docker_package.sh.
```

This keeps manual/document understanding inside Codex agents while giving the pipeline deterministic gates and rerun points.

## Stage Contract

Every stage emits:

```text
[AGENT_STAGE] stage=<stage> status=start
[AGENT_STAGE] stage=<stage> status=success
[AGENT_STAGE] stage=<stage> status=fail exit_code=<code>
```

Every failure writes `ops/artifacts/last_failure.json`.

## Stages

```text
stage0_env_check
stage1_context_intake
stage2_docs_inventory
stage3_docs_coverage
stage4_capability_model
stage5_deployment_plan
stage6_dependency_audit
stage7_spec_validate
stage8_yaml_generate
stage9_yaml_validate
stage10_adapter_codegen
stage11_hal_registration_verify
stage12_package_manifest
stage13_package_verify
stage14_docker_build_x86_optional
stage15_docker_build_arm64
stage16_image_verify
stage17_remote_transfer
stage18_remote_prepare
stage19_remote_device_probe
stage20_remote_run
stage21_remote_test
stage22_collect_logs
stage23_error_summary
```

## Agent Flow

```text
context-mapper
  -> docs-intake-agent
  -> capability-modeler-agent
  -> deployment-planner-agent
  -> sdk-dependency-auditor-agent
  -> spec-validator-agent
  -> yaml-writer-agent
  -> hal-adapter-codegen-agent
  -> hal-registration-verifier-agent
  -> package-builder
  -> docker-builder
  -> remote-deployer
  -> remote-tester
  -> failure-debugger
```

## Context Artifacts

```text
ops/contexts/<context_id>.context.md
ops/contexts/<context_id>.manifest.json
ops/contexts/<context_id>.docs_inventory.json
ops/contexts/<context_id>.docs_coverage.json
ops/contexts/<context_id>.dependency_plan.json
ops/contexts/<context_id>.device_spec.json
ops/artifacts/<context_id>.adapter_gaps.md
ops/artifacts/<context_id>.yaml_validation.json
ops/artifacts/<context_id>.dependency_validation.json
ops/artifacts/<context_id>.registration_report.json
ops/artifacts/<context_id>.build_report.json
ops/artifacts/last_failure.json
```

## Generic Runtime Requirements

`device_spec.json` must describe dependencies generically. Do not assume FFmpeg, ZLM, RTMP, ROS topics, or any single protocol unless the context/manual says so.

```json
{
  "runtime_requirements": {
    "apt_build": [],
    "apt_runtime": [],
    "vendor_headers": [],
    "vendor_libraries": [],
    "library_paths": [],
    "rpath_rules": [],
    "device_nodes": [],
    "udev_rules": [],
    "kernel_modules": [],
    "network_ports": [],
    "environment": {},
    "mounts": [],
    "privileged": false,
    "capabilities": [],
    "subprocesses": [],
    "healthchecks": []
  }
}
```

Legacy `adapter_requirements` remains supported and maps to `runtime_requirements` during validation.

## Gates

```text
Gate 1: docs coverage is sufficient for a spec.
Gate 2: device_spec has required adapter, device, connection, capability, deployment, and dependency fields.
Gate 3: HAL YAML files exist and can be parsed.
Gate 4: required SDK headers/libs exist and target architecture is not contradicted.
Gate 5: adapter is registered in CMake, factory, deployment, and install rules.
Gate 6: package verification passes.
Gate 7: Docker/colcon build passes for the requested target architecture.
Gate 8: remote device probe passes.
Gate 9: runtime healthchecks pass.
```

## Boundaries

Allowed by default:

```text
ops/
.codex/skills/device-adapter/scripts/
Dockerfile
docker-compose.yml
.dockerignore
run.sh
requirements.txt
configs
HAL model YAML
CMakeLists.txt
adapter factory
```

Adapter source code changes require `--allow-code`.

The workflow must not:

```text
invent undocumented protocol fields
invent vendor SDK APIs
package the whole repository
use x86 libraries in arm64 images
ignore missing required SDK/libs
compile on the remote board as the default development loop
hard-code credentials
skip failed stages
```
