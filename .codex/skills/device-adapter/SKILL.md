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
- `package`: Package files based on generated manifest.
- `docker-package`: Build Docker image and save image tar.
- `deploy`: Package and deploy current project to a remote target.
- `test`: Run remote test and collect terminal output.
- `loop`: Deploy, test, collect logs, debug runtime failure, and rerun.
- `logs`: Collect remote logs only.
- `rerun`: Continue from the last failed stage.

Examples:

```bash
/device-adapter context infrared_camera
/device-adapter model infrared_camera
/device-adapter adapt infrared_camera
/device-adapter package infrared_camera
/device-adapter docker-package infrared_camera -arm
/device-adapter docker-package infrared_camera -x86
/device-adapter docker-package infrared_camera -all
/device-adapter docker-package infrared_camera -arm --no-smoke
/device-adapter docker-package infrared_camera -arm --smoke
/device-adapter deploy infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter test infrared_camera --host 192.168.1.100 --user root
/device-adapter loop infrared_camera --host 192.168.1.100 --user root -arm
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

The spec is the contract between documentation understanding and deterministic file generation. It must include:
- `adapter_type`
- `device`
- `connection`
- `capability`
- `device_model`
- `deployment_entry`
- `adapter_requirements`

`adapter_requirements` must capture build/runtime dependencies:
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

Required stages:
- stage0_env_check
- stage1_context_load
- stage2_context_validate
- stage3_package_by_manifest
- stage4_docker_package
- stage5_transfer
- stage6_remote_prepare
- stage7_remote_run
- stage8_remote_test
- stage9_collect_logs
- stage10_error_summary

## Script Map

- Context: `python3 .codex/skills/device-adapter/scripts/context_to_manifest.py <context_id>`
- Adapt HAL device: `python3 .codex/skills/device-adapter/scripts/adapt_hal_device.py <context_id>`
- Package: `python3 .codex/skills/device-adapter/scripts/package_by_manifest.py <context_id>`
- Docker: `bash .codex/skills/device-adapter/scripts/docker_package.sh <context_id> -arm`
- Generate runtime files: `python3 .codex/skills/device-adapter/scripts/generate_runtime_files.py <context_id>`
- Deploy: `bash .codex/skills/device-adapter/scripts/remote_deploy.sh <context_id> --host <host> --user <user> -arm`
- Test: `bash .codex/skills/device-adapter/scripts/remote_test.sh <context_id> --host <host> --user <user>`
- Staged loop helper: `bash .codex/skills/device-adapter/scripts/stage_runner.sh <action> <context_id> [options]`

## Subagent Routing

Use focused subagents for complex commands:
- `context-mapper` for context generation.
- `hal-device-modeler` for manual/docs to `device_spec.json`.
- `hal-adapter-builder` for C++ adapter/protocol implementation.
- `package-builder` for manifest packaging.
- `docker-builder` for Docker build/save issues.
- `remote-deployer` for SSH transfer and remote preparation.
- `remote-tester` for remote execution/log collection.
- `failure-debugger` for staged failure analysis and runtime-only fixes.

Wait for required subagents and return one consolidated result.
