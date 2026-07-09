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
- `context`: Convert natural language context into context.md, manifest.json, and package file list.
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

## C++ Runtime Generation Rule

The user may provide only C/C++ source files, headers, vendor libraries, and natural language notes. In that case the context flow must inspect the repository and infer package inputs from existing files instead of requiring preexisting Dockerfile/run.sh.

When Docker/runtime files are missing, generate them before package/build:

```bash
python3 .codex/skills/device-adapter/scripts/generate_runtime_files.py <context_id>
```

Generated runtime files may include:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `run.sh`
- `CMakeLists.txt` only when the project has C/C++ files but no known build file

Do not modify C++ business logic by default. Build-system and runtime files are allowed.

For native libraries and SDKs:
- Keep vendor `.so`, `.a`, headers, and SDK directories in the manifest when they are part of the repository.
- Build inside the target platform container with Docker buildx so apt packages, OpenCV, ffmpeg, and native dependencies resolve for `linux/amd64` or `linux/arm64`.
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
- Package: `python3 .codex/skills/device-adapter/scripts/package_by_manifest.py <context_id>`
- Docker: `bash .codex/skills/device-adapter/scripts/docker_package.sh <context_id> -arm`
- Generate runtime files: `python3 .codex/skills/device-adapter/scripts/generate_runtime_files.py <context_id>`
- Deploy: `bash .codex/skills/device-adapter/scripts/remote_deploy.sh <context_id> --host <host> --user <user> -arm`
- Test: `bash .codex/skills/device-adapter/scripts/remote_test.sh <context_id> --host <host> --user <user>`
- Staged loop helper: `bash .codex/skills/device-adapter/scripts/stage_runner.sh <action> <context_id> [options]`

## Subagent Routing

Use focused subagents for complex commands:
- `context-mapper` for context generation.
- `package-builder` for manifest packaging.
- `docker-builder` for Docker build/save issues.
- `remote-deployer` for SSH transfer and remote preparation.
- `remote-tester` for remote execution/log collection.
- `failure-debugger` for staged failure analysis and runtime-only fixes.

Wait for required subagents and return one consolidated result.
