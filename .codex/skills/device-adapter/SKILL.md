---
name: device-adapter
description: Use this skill when the user invokes /device-adapter or device-adapter commands for context, package, docker-package, deploy, test, loop, logs, or rerun in unmanned-system device projects.
---

# Device Adapter Skill

## Purpose

Use this skill for context-first packaging, Docker building, remote deployment, remote testing, staged log collection, and runtime-only repair for unmanned-system device projects.

The user may provide free natural language. They should not need to write YAML or JSON manually.

## Command Format

```bash
/device-adapter <action> <context_id> [options]
```

Supported actions:
- `context`: Preserve natural language context/manual locations and generate packaging context.
- `model`: Read device manuals/docs and produce `ops/contexts/<context_id>.device_spec.json`.
- `adapt`: Apply `device_spec.json` to HAL capability YAML, device YAML, deployment YAML, and adapter gap report.
- `verify`: Validate `device_spec.json`, generated HAL YAML, dependency declarations, and HAL adapter registration.
- `package`: Package files based on generated manifest.
- `docker-package`: Build Docker image and save image tar.
- `deploy`: Package and deploy current project to a remote target.
- `test`: Run remote test and collect terminal output.
- `loop`: Deploy, test, collect logs, debug runtime failure, and rerun.
- `full`: Run model gates, adapt, verify, package, Docker build, deploy, and remote test as far as available artifacts permit.
- `logs`: Collect remote logs only.
- `rerun`: Continue from the last failed stage.

Examples:

```bash
/device-adapter context infrared_camera
/device-adapter model infrared_camera
/device-adapter adapt infrared_camera
/device-adapter verify infrared_camera
/device-adapter package infrared_camera
/device-adapter docker-package infrared_camera -arm
/device-adapter docker-package infrared_camera -x86
/device-adapter docker-package infrared_camera -all
/device-adapter docker-package infrared_camera -arm --no-smoke
/device-adapter docker-package infrared_camera -arm --smoke
/device-adapter deploy infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter test infrared_camera --host 192.168.1.100 --user root
/device-adapter loop infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter full infrared_camera --host 192.168.1.100 --user root -arm --allow-code
```

## Context-First Rule

For every action except `context`, first load:

```text
ops/contexts/<context_id>.context.md
ops/contexts/<context_id>.manifest.json
```

If the manifest does not exist, run the context flow first. Do not guess package files from the whole repository.

## Natural Language Context Rule

For `/device-adapter context <context_id>`, preserve the user's free-form context in:

```text
ops/contexts/<context_id>.context.md
```

Then generate:

```text
ops/contexts/<context_id>.manifest.json
ops/artifacts/<context_id>.package_files.txt
```

Use the script when possible:

```bash
python3 .codex/skills/device-adapter/scripts/context_to_manifest.py <context_id> --context-file <path>
```

If the user's context appears in the chat rather than a file, write it to a temporary file or directly to `ops/contexts/<context_id>.context.md`, then run the script.

## HAL/ROS2 Runtime Generation Rule

The primary workflow is docs-first HAL adaptation. The user may provide only a device manual, protocol document, SDK package notes, wiring notes, and deployment context. Do not require preexisting device adapter code.

If the repository contains `src/*/package.xml`, treat it as a ROS2/colcon HAL workspace and preserve its package structure.

For HAL/ROS2 workspaces:
- Include `src/`, top-level Markdown docs, launch files, config/model YAML, msg/srv definitions, and vendored `3rdparty` headers/libs.
- Do not generate a top-level `CMakeLists.txt`.
- Generate Docker/runtime files for `colcon build`, not plain `cmake`.
- Run by sourcing ROS and workspace setup files, then launching the HAL manager or the selected single-device launch.
- For infrared/Mino17 contexts, require the `hardware_abstraction_layer/infrared_push_50fps` executable to be present after build.

Use this workflow when adding a new device to the HAL platform:

```text
/device-adapter context <context_id>
/device-adapter model <context_id>
/device-adapter adapt <context_id>
/device-adapter package <context_id>
/device-adapter docker-package <context_id> -arm
```

`model` is an agent step, not a deterministic parser. It reads manuals/docs and writes:

```text
ops/contexts/<context_id>.device_spec.json
```

The stage orchestrator treats model sub-stages as agent handoffs. It checks for the expected artifacts and fails at the precise missing stage instead of pretending deterministic scripts completed manual understanding. A Codex agent should then run the named subagent, write the missing artifact, and rerun.

The spec is the contract between documentation understanding and deterministic file generation. It must include:
- `adapter_type`
- `device`
- `connection`
- `capability`
- `device_model`
- `deployment_entry`
- `runtime_requirements` or legacy `adapter_requirements`

`runtime_requirements` must capture generic build/runtime dependencies without assuming a specific device type:
- `apt_build`: apt packages required to compile the adapter or helper process.
- `apt_runtime`: apt packages required on the deployed board/container.
- `vendor_headers`: vendor SDK include paths or headers that must be present.
- `vendor_libraries`: vendor `.so`/`.a` files, including expected architecture when known.
- `library_paths`: runtime library lookup directories.
- `rpath_rules`: RPATH/RUNPATH rules required by installed executables/libraries.
- `device_nodes`: `/dev/*` nodes or discovery rules required by the device.
- `udev_rules`: udev rules required for stable names or permissions.
- `kernel_modules`: kernel modules needed by the device.
- `network_ports`: TCP/UDP ports required by the adapter, helper process, or embedded service.
- `environment`: environment variables required at build or runtime.
- `mounts`: Docker/device mounts required by the runtime.
- `privileged`: whether the container needs privileged mode.
- `capabilities`: Linux capabilities required by the container.
- `subprocesses`: helper executables/daemons started by the adapter, their command lines, required devices, and log expectations.
- `healthchecks`: device-specific proof commands or log markers.

Legacy `adapter_requirements` remains supported and maps to runtime requirements:
- `apt_build`: apt packages required to compile the adapter or helper process.
- `apt_runtime`: apt packages required on the deployed board/container.
- `sdk_headers`: vendor SDK include paths or headers that must be present.
- `sdk_libraries`: vendor `.so`/`.a` files, including expected architecture when known.
- `protocol_files`: protocol parser/transport source files that must be implemented.
- `subprocesses`: helper executables started by the adapter, their command lines, required devices, and log expectations.

Then `adapt` applies the spec:

```bash
python3 .codex/skills/device-adapter/scripts/adapt_hal_device.py <context_id>
```

This generates or updates:
- `src/hardware_abstraction_layer/model/capability_groups/<capability>.capability.yaml`
- `src/hardware_abstraction_layer/model/devices/<adapter>.device.yaml`
- `src/hardware_abstraction_layer/config/deployment.yaml`
- `ops/artifacts/<context_id>.adapter_gaps.md`

The gap report is intentional. Device documentation can define the model and deployment shape, but protocol code, SDK libraries, and factory/CMake registration still need concrete implementation or verification.

After `adapt`, use `hal-adapter-builder` to implement or patch C++ adapter code from:
- `ops/contexts/<context_id>.device_spec.json`
- the device manual/protocol documentation
- existing adapter examples in the HAL workspace

The adapter builder may add source/header/protocol files, factory registration, and CMake entries. It must not invent undocumented protocol behavior; unresolved SDK/protocol details go into the gap report.

After `adapt`, run verification:

```bash
python3 .codex/skills/device-adapter/scripts/verify_hal_adapter.py <context_id>
```

This writes:
- `ops/artifacts/<context_id>.spec_validation.json`
- `ops/artifacts/<context_id>.dependency_validation.json`
- `ops/artifacts/<context_id>.yaml_validation.json`
- `ops/artifacts/<context_id>.registration_report.json`

The verification gate checks:
- `device_spec.json` required fields.
- generated HAL capability/device/deployment YAML paths.
- required vendor headers/libraries/protocol files when concrete paths are declared.
- adapter source/header presence.
- generated C++ project includes resolve to real files under `hardware_abstraction_layer/include` or `hardware_abstraction_layer/src`.
- adapter include directory matches `adapter_type` unless the spec explicitly documents a different layout.
- `install(PROGRAMS|FILES ...)` paths in the ROS package CMakeLists are package-local and exist.
- generated automation scripts do not use interactive Docker flags such as `docker exec -it` or `docker run -it`.
- `adapter_factory.hpp`, `CMakeLists.txt`, and `deployment.yaml` registration consistency.

Do not proceed to package/build/deploy when this gate fails.

## Deliverable Package Rule

For deployable hardware adapters, the output must be a closed-loop release package, not only a HAL source patch. When the user's context asks for board-side direct run, deployment, or a complete delivery package, the manifest must set:

```json
{
  "delivery": {
    "closed_loop_package": true,
    "required_root_files": ["config.env", "install.sh", "run.sh", "status.sh", "view.sh", "DEPLOY.md"]
  }
}
```

The package root should then include:
- `config.env`: image/container/runtime variables.
- `install.sh`: deterministic dependency/image/runtime validation.
- `run.sh`: non-interactive startup for SSH automation.
- `status.sh`: container/process/port/log status.
- `view.sh`: device-specific playback/API URL hints when available.
- `DEPLOY.md`: operator instructions and healthcheck notes.

The root `run.sh` is a host-side launcher. If a container entrypoint is needed, place it under a package-local path such as `ops/scripts/device_adapter_container_entrypoint.sh` or `src/hardware_abstraction_layer/scripts/`; do not make one file serve both roles.

Package verification must treat shared-library symlinks as package members. Do not strip `.so` symlinks from SDK/native runtime directories unless an install step recreates them.

Native runtime verification must check the dynamic library closure of every executed native binary, not only the HAL package libraries. For every `runtime_requirements.subprocesses[*].executable` that is delivered in the repository/package, run a target-architecture-safe dependency scan such as `readelf -d` and verify each `NEEDED` shared library is satisfied by:
- a packaged library file or symlink,
- a declared `runtime_requirements.library_paths` entry,
- a declared `runtime_requirements.vendor_libraries` entry,
- or an explicit `system_libraries` entry when the library is expected from the base OS/container.

Do not proceed to Docker build or remote deploy when a child process has unresolved libraries. This rule is generic for cameras, lidars, radars, serial/CAN daemons, vendor SDK tools, media pipelines, and any other helper executable.

## Runtime Material Rule

Runtime dependencies must be delivered, not only declared.

If `runtime_requirements.subprocesses` references a helper binary, the package must include one of:
- the binary itself,
- a vendored runtime directory,
- an offline image tar containing it,
- a deterministic install/load script,
- or a blocking gap report explaining why it cannot be delivered.

If a device context explicitly requires media services, protocol daemons, SDK runtimes, or helper processes, represent them in `runtime_requirements` with their binaries, config files, ports, mounts, environment, and healthchecks. Do not assume FFmpeg, ZLM, RTMP, V4L2, serial, CAN, TCP, UDP, or any helper daemon unless the context/manual says so.

Separate runtime requirements by role when possible:
- `apt_build`: packages needed to compile HAL/adapter/helper code.
- `apt_runtime`: packages needed in the board/container runtime.
- `vendor_libraries`: SDK or helper libraries delivered with the package.
- `library_paths`: lookup directories used by HAL or helper subprocesses.
- `subprocesses`: external executables started by the adapter, including their executable path, library paths, system libraries, process healthcheck, and endpoint healthcheck.
- `healthchecks`: proof that the real device function works, not only that a process was started.

For USB/UVC devices, do not hard-code `/dev/videoX`. Discovery should prefer:
- configured non-auto device path, if it exists.
- stable udev symlink such as `/dev/infrared_camera`, when shipped and documented.
- `/dev/v4l/by-id/*video-index0`.
- `/dev/v4l/by-path/*`.
- `/dev/video*`.

Udev rules may be shipped, but should be optional unless VID/PID stability is confirmed by documentation.

When an adapter needs vendor libraries or external tools, prefer this order:
- Install public dependencies through apt in the generated Dockerfile.
- Keep vendor SDK headers/libs under HAL-owned directories such as `src/hardware_abstraction_layer/3rdparty/`.
- Preserve separate architecture directories for `.so`/`.a` files, for example `x86_64-linux-gnu-gcc` and `aarch64-linux-gnu-gcc`.
- If the adapter starts a child process for a camera/SDK pipeline, surface the command, stdout/stderr, exit code, and restart policy through HAL logs/events.

For non-ROS C/C++ projects, the user may provide only C/C++ source files, headers, vendor libraries, and natural language notes. In that case the context flow must inspect the repository and infer package inputs from existing files instead of requiring preexisting Dockerfile/run.sh.

When Docker/runtime files are missing, generate them before package/build:

```bash
python3 .codex/skills/device-adapter/scripts/generate_runtime_files.py <context_id>
```

Generated runtime files may include:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `run.sh`
- `CMakeLists.txt` only when the project is not ROS2/colcon and has C/C++ files but no known build file

Do not modify C++ business logic by default. Build-system and runtime files are allowed.

For native libraries and SDKs:
- Keep vendor `.so`, `.a`, headers, and SDK directories in the manifest when they are part of the repository.
- Build inside the target platform container with Docker buildx so apt packages, OpenCV, ffmpeg, and native dependencies resolve for `linux/amd64` or `linux/arm64`.
- For HAL workspaces, use the repository's existing architecture-specific library directories such as `3rdparty/lib/x86_64-linux-gnu-gcc` and `3rdparty/lib/aarch64-linux-gnu-gcc`; do not flatten or rename them.
- Prefer one single-platform image build per architecture, then save one image tar per architecture. Do not rely on a multi-platform `--load`.
- On x86 WSL, arm64 packaging builds and saves an arm64 image tar locally, but does not run the arm64 container locally unless `--smoke` is passed and QEMU/binfmt is configured. The normal arm64 runtime proof is `/device-adapter deploy` plus `/device-adapter test` on an arm64 remote target.

## Generic Dependency Rule

Device-specific runtime patterns must come from context/manuals/spec. Do not assume a device uses FFmpeg, ZLM, RTMP, MAVSDK, V4L2, serial, CAN, TCP, UDP, or a helper daemon unless the context or documentation says so.

Known-device SOPs may be used as examples only. Reuse their patterns, not their concrete ports, library names, protocols, or subprocess commands.

Known-device acceptance checks may be stricter when the context explicitly identifies that device. For example, a Mino17 SOP may require `infrared_push_50fps` to be an ELF executable and may require RTMP/HLS probing, but those checks must not be applied to unrelated infrared cameras, millimeter-wave radars, lidars, serial devices, or network sensors.

## Default Edit Policy

Default mode is runtime-only.

Allowed by default:
- Dockerfile
- docker-compose.yml
- .dockerignore
- run.sh
- start.sh
- CMakeLists.txt
- requirements.txt
- package files
- config files
- ops/
- .codex/skills/device-adapter/scripts/

Avoid editing business source code unless the user passes `--allow-code`.

## Stage Protocol

Every workflow command must emit stage markers:

```text
[AGENT_STAGE] stage=<stage_name> status=start
[AGENT_STAGE] stage=<stage_name> status=success
[AGENT_STAGE] stage=<stage_name> status=fail exit_code=<code>
```

Every failure must write:

```text
ops/artifacts/last_failure.json
```

Every staged command should also append live logs to:

```text
ops/artifacts/logs/<context_id>_stage_runner.log
```

Users can monitor progress with:

```bash
tail -f ops/artifacts/logs/<context_id>_stage_runner.log
```

`last_failure.json` should include `log_file` when available.

Stage markers are for humans and logs. They are not the only source of truth. Every staged run must also write machine-readable stage results under:

```text
ops/artifacts/stages/<context_id>/<stage_name>.json
```

Each stage result should include:
- `stage`
- `owner_agent`
- `status`
- `started_at`
- `finished_at`
- `duration_ms`
- `exit_code`
- `outputs`
- `output_hashes`
- `evidence`
- `next_action` when failed

The global status file is:

```text
ops/artifacts/<context_id>.status.json
```

## Boundary Policy

Agent ownership must be enforced by deterministic policy, not only by prompts. Stage write boundaries are declared in:

```text
.codex/skills/device-adapter/scripts/agent_boundary_policy.json
```

For script-driven stages, `stage_orchestrator.py` compares the Git working-tree change set before and after the stage. If a stage writes outside its allowlist or touches a denylisted path, the stage must fail with:

```text
BOUNDARY_WRITE_VIOLATION
```

Failure reports should include the boundary report path and the files that violated the policy.

`failure-debugger` must not directly perform broad repairs. It should classify the failure and write:

```text
ops/artifacts/<context_id>.remediation_plan.json
```

The remediation plan must route the actual repair to the owner agent for the failed stage.

## Context Artifact Layers

Avoid turning `device_spec.json` into a catch-all object. The workflow should distinguish:

- Observation facts: `ops/contexts/<context_id>.device_observation.json`
- Adaptation contract: `ops/contexts/<context_id>.device_spec.json`
- Deployment decision: `ops/contexts/<context_id>.deployment_plan.json`

Current scripts still support legacy consolidated `device_spec.json`, but new agents should prefer the split artifacts when data is available.

Required stages:
- stage0_env_check
- stage1_context_intake
- stage2_docs_inventory
- stage3_docs_coverage
- stage4_capability_model
- stage5_deployment_plan
- stage6_dependency_audit
- stage7_spec_validate
- stage8_yaml_generate
- stage9_yaml_validate
- stage10_adapter_codegen
- stage11_hal_registration_verify
- stage12_package_manifest
- stage13_package_verify
- stage14_docker_build_x86_optional
- stage15_docker_build_arm64
- stage16_image_verify
- stage17_remote_transfer
- stage18_remote_prepare
- stage19_remote_device_probe
- stage20_remote_run
- stage21_remote_test
- stage22_collect_logs
- stage23_failure_classification

## Script Map

- Context: `python3 .codex/skills/device-adapter/scripts/context_to_manifest.py <context_id>`
- Stage orchestrator: `python3 .codex/skills/device-adapter/scripts/stage_orchestrator.py <action> <context_id> [options]`
- Adapt HAL device: `python3 .codex/skills/device-adapter/scripts/adapt_hal_device.py <context_id>`
- Verify HAL adapter: `python3 .codex/skills/device-adapter/scripts/verify_hal_adapter.py <context_id>`
- Package: `python3 .codex/skills/device-adapter/scripts/package_by_manifest.py <context_id>`
- Docker: `bash .codex/skills/device-adapter/scripts/docker_package.sh <context_id> -arm`
- Generate runtime files: `python3 .codex/skills/device-adapter/scripts/generate_runtime_files.py <context_id>`
- Deploy: `bash .codex/skills/device-adapter/scripts/remote_deploy.sh <context_id> --host <host> --user <user> -arm`
- Test: `bash .codex/skills/device-adapter/scripts/remote_test.sh <context_id> --host <host> --user <user>`
- Staged loop helper: `bash .codex/skills/device-adapter/scripts/stage_runner.sh <action> <context_id> [options]`

## Subagent Routing

Use focused subagents for complex commands:
- `context-mapper` for context generation.
- `docs-intake-agent` for manuals, SDK notes, protocol docs, and coverage reports.
- `capability-modeler-agent` for documented HAL properties, services, and events.
- `deployment-planner-agent` for deployment entries, device discovery, mounts, env, ports, and launch mode.
- `sdk-dependency-auditor-agent` for apt, SDK headers/libs, RPATH, device nodes, subprocesses, and healthchecks.
- `spec-validator-agent` for `device_spec.json` gate validation.
- `yaml-writer-agent` for HAL capability/device/deployment YAML generation.
- `hal-device-modeler` for legacy/manual docs to `device_spec.json` when the finer agents are not required.
- `hal-adapter-builder` for C++ adapter/protocol implementation.
- `hal-registration-verifier-agent` for CMake/factory/deployment/install registration checks.
- `package-builder` for manifest packaging.
- `docker-builder` for Docker build/save issues.
- `remote-deployer` for SSH transfer and remote preparation.
- `remote-tester` for remote execution/log collection.
- `failure-debugger` for staged failure analysis and runtime-only fixes.

Wait for required subagents and return one consolidated result.
