# Device Adapter Agent Workflow

This document describes the current `device-adapter` agent workflow, stage ownership, logs, status files, and maintenance rules.

## Current Agent Flow

```text
context-mapper
  -> docs-intake-agent
  -> capability-modeler-agent
  -> deployment-planner-agent
  -> sdk-dependency-auditor-agent
  -> spec-validator-agent
  -> yaml-writer-agent
  -> hal-adapter-builder
  -> hal-registration-verifier-agent
  -> package-builder
  -> docker-builder
  -> remote-deployer
  -> remote-tester
  -> failure-debugger
```

## Agent Responsibilities

| Agent | Main responsibility | Primary outputs | Failure owner |
| --- | --- | --- | --- |
| `context-mapper` | Preserve natural language context and generate the initial manifest/package file list. | `ops/contexts/<id>.context.md`, `ops/contexts/<id>.manifest.json`, `ops/artifacts/<id>.package_files.txt` | Missing context, wrong project root, bad package boundary. |
| `docs-intake-agent` | Read manuals, SDK notes, protocol docs, examples, and report coverage. | `ops/contexts/<id>.docs_inventory.json`, `ops/contexts/<id>.docs_coverage.json` | Missing docs, unreadable PDF, incomplete manual coverage. |
| `capability-modeler-agent` | Convert documented functions into HAL properties, services, and events. | `ops/contexts/<id>.device_spec.json` | Wrong capability shape, undocumented controls, missing properties/services/events. |
| `deployment-planner-agent` | Plan manager/device-node launch mode, device discovery, mounts, env, ports, and healthchecks. | Updated `device_spec.json` deployment and runtime sections. | Wrong launch mode, missing device-node rules, missing healthcheck. |
| `sdk-dependency-auditor-agent` | Audit apt packages, SDK headers/libs, native helper executables, library paths, RPATH, device nodes, and subprocess healthchecks. | `runtime_requirements`, dependency reports, gap notes. | Missing SDK/header/lib, missing apt package, incomplete native dependency closure. |
| `spec-validator-agent` | Validate the generated `device_spec.json` contract. | `ops/artifacts/<id>.spec_validation.json` | Missing required spec fields or inconsistent adapter/deployment data. |
| `yaml-writer-agent` | Generate or update HAL capability/device/deployment YAML from the spec. | HAL model/config YAML. | YAML missing, malformed, or inconsistent with spec. |
| `hal-adapter-builder` | Implement adapter C++/headers/protocol glue and subprocess launch code when `--allow-code` is present. | Adapter source/header files, protocol files, gap report. | Compile-level adapter issues, wrong include paths, undocumented protocol assumptions. |
| `hal-registration-verifier-agent` | Verify CMake, factory, deployment, install paths, and adapter file layout. | `registration_report.json`, `cmake_install_validation.json`, `source_path_validation.json` | Missing registration, bad CMake install source path, bad factory entry. |
| `package-builder` | Package only manifest-selected files and verify tar contents. | `<id>_package.tar.gz`, package tree, package verify report. | Missing packaged file, excluded file included, missing release entry, lost `.so` symlink. |
| `docker-builder` | Build/save x86 or arm64 Docker image and run native dependency verification. | image tar, image inspect JSON, native deps report, build log. | Docker build error, wrong image arch, unresolved helper `.so`. |
| `remote-deployer` | Transfer package/image to the board/server and prepare remote runtime directory. | Remote deployment state and logs. | SSH/scp/rsync failure, remote extraction/load failure. |
| `remote-tester` | Run remote tests, collect terminal output, Docker logs, device state, and healthchecks. | remote test logs under `ops/artifacts/logs/`. | Device missing, permission issue, process exits, healthcheck fails. |
| `failure-debugger` | Read failure JSON/logs/reports and route the fix back to the right agent/stage. | failure summary and rerun command. | Misclassified root cause or missing next action. |

## Stage Ownership

| Stage | Owner | Typical failure |
| --- | --- | --- |
| `stage0_env_check` | `stage_orchestrator.py` | Missing local directories or environment setup. |
| `stage1_context_intake` | `context-mapper` | Missing `context.md` or `manifest.json`. |
| `stage2_docs_inventory` | `docs-intake-agent` | Missing/unreadable docs. |
| `stage3_docs_coverage` | `docs-intake-agent` | Manual lacks protocol/SDK/deployment facts. |
| `stage4_capability_model` | `capability-modeler-agent` | Missing `device_spec.json`. |
| `stage5_deployment_plan` | `deployment-planner-agent` | Missing deployment entry, mounts, ports, device rules. |
| `stage6_dependency_audit` | `sdk-dependency-auditor-agent` | Missing SDK, `.so`, apt package, native closure, RPATH. |
| `stage7_spec_validate` | `spec-validator-agent` | Invalid or incomplete spec. |
| `stage8_yaml_generate` | `yaml-writer-agent` / `adapt_hal_device.py` | YAML generation failed. |
| `stage9_yaml_validate` | `verify_hal_adapter.py` | Missing HAL YAML. |
| `stage10_adapter_codegen` | `hal-adapter-builder` | Bad adapter code/include/protocol implementation. |
| `stage11_hal_registration_verify` | `hal-registration-verifier-agent` | CMake/factory/deployment/install issue. |
| `stage12_package_manifest` | `package-builder` | Manifest generation/runtime file generation failed. |
| `stage13_package_verify` | `package-builder` | Package content incomplete. |
| `stage14_docker_build_x86_optional` | `docker-builder` | x86 build failure. |
| `stage15_docker_build_arm64` | `docker-builder` | arm64 build failure. |
| `stage16_image_verify` | `docker-builder` | Image inspect/architecture issue. |
| `stage17_remote_transfer` | `remote-deployer` | Transfer failure. |
| `stage18_remote_prepare` | `remote-deployer` | Remote extraction/load/prep failure. |
| `stage19_remote_device_probe` | `remote-tester` | Device node missing or permissions wrong. |
| `stage20_remote_run` | `remote-tester` | Runtime process fails to start or exits. |
| `stage21_remote_test` | `remote-tester` | Healthcheck/function proof fails. |
| `stage22_collect_logs` | `remote-tester` | Log collection failure. |
| `stage23_error_summary` | `failure-debugger` | No actionable failure summary. |

## Logs And Status

Every staged command appends to:

```text
ops/artifacts/logs/<context_id>_stage_runner.log
```

Watch progress:

```bash
tail -f ops/artifacts/logs/<context_id>_stage_runner.log
```

Quick global status:

```bash
cat ops/artifacts/<context_id>.status.json
```

Generated-file trail:

```bash
cat ops/artifacts/<context_id>.generated_files.txt
```

Last failure:

```bash
cat ops/artifacts/last_failure.json
```

Important reports:

```text
ops/artifacts/<context_id>.spec_validation.json
ops/artifacts/<context_id>.dependency_validation.json
ops/artifacts/<context_id>.yaml_validation.json
ops/artifacts/<context_id>.registration_report.json
ops/artifacts/<context_id>.source_path_validation.json
ops/artifacts/<context_id>.cmake_install_validation.json
ops/artifacts/<context_id>.release_script_validation.json
ops/artifacts/<context_id>_<arch>_native_deps.json
ops/artifacts/<context_id>.package_verify.json
```

## Error Routing

Use the failed stage first. If there is no stage marker, route by error pattern:

| Error pattern | Stage | Fix owner |
| --- | --- | --- |
| PDF/manual missing or unreadable | `stage2_docs_inventory` | `docs-intake-agent` |
| Missing protocol fields or unclear capability | `stage3_docs_coverage` / `stage4_capability_model` | `docs-intake-agent`, `capability-modeler-agent` |
| Missing SDK/header/lib/apt package | `stage6_dependency_audit` | `sdk-dependency-auditor-agent` |
| `libxxx.so => not found` or unresolved `NEEDED` | `stage6_native_deps_verify` / `stage15_docker_build_arm64` | `sdk-dependency-auditor-agent`, `docker-builder` |
| Bad C++ include or adapter compile issue | `stage10_adapter_codegen` | `hal-adapter-builder` |
| CMake/factory/deployment registration issue | `stage11_hal_registration_verify` | `hal-registration-verifier-agent` |
| Missing package file, root script, symlink | `stage13_package_verify` | `package-builder` |
| Docker build apt/build failure | `stage15_docker_build_arm64` | `docker-builder` |
| SSH/scp/rsync failure | `stage17_remote_transfer` | `remote-deployer` |
| Device node missing/permission denied | `stage19_remote_device_probe` | `deployment-planner-agent`, `remote-tester` |
| Process starts then exits, no data stream/response | `stage20_remote_run` / `stage21_remote_test` | `remote-tester`, then `failure-debugger` routes back. |

## Maintenance Rules

- Keep `SKILL.md`, `README.md`, `AGENTS.md`, and this workflow document consistent when adding stages or agents.
- Keep `agent_stage_map.json` aligned with `stage_orchestrator.py`.
- Every new deterministic check must write a JSON report under `ops/artifacts/`.
- Every workflow failure must write `ops/artifacts/last_failure.json`.
- Do not add device-specific defaults to generic runtime generation. Device-specific dependencies must come from context/manual/spec.
- Known-device checks are allowed only when context explicitly identifies that device.

## Recorded Future Agent Additions

These are recorded design items and are not implemented yet:

```text
dependency-fetch-agent
vendor-materializer-agent
acceptance-planner-agent
```

Intended responsibilities:

- `dependency-fetch-agent`: fetch/download declared public SDK archives, apt metadata, model files, or other external materials when the workflow is allowed to access the network.
- `vendor-materializer-agent`: unpack/copy SDK headers, `.so`, `.a`, helper binaries, configs, and checksums into repo-local `vendor/` or HAL `3rdparty/` layout, then update manifest includes.
- `acceptance-planner-agent`: convert the user's success criteria into deterministic `runtime_requirements.healthchecks`, including process, device, endpoint, and data-proof checks.
