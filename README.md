# Device Adapter Runner

Device Adapter Runner is a Codex workflow scaffold for unmanned-system device projects. It supports a docs-first HAL workflow: device manuals, protocol notes, SDK notes, and natural language context are converted into HAL model YAML, adapter code, dependency plans, Docker artifacts, remote deployment, and staged test/fix loops.

The workflow is designed for cases where the project starts without runtime packaging files. The adapter can generate runtime-only files such as `Dockerfile`, `run.sh`, `docker-compose.yml`, `.dockerignore`, and a minimal `CMakeLists.txt` when needed.

## What It Provides

- Context-first and docs-first HAL adaptation from natural language descriptions.
- Manual/document coverage checks before code generation.
- HAL capability/device/deployment YAML generation.
- Adapter implementation and CMake/factory/deployment registration checks.
- Generic dependency planning for apt packages, SDK headers/libs, RPATH, device nodes, udev rules, subprocesses, ports, mounts, and healthchecks.
- Manifest-based file selection instead of packaging the whole repository.
- Runtime file generation for C/C++ device projects.
- x86_64 and arm64 Docker image builds with Docker buildx.
- Local package verification with package tree, SHA-256 output, symlink handling, release-entry checks, and non-interactive script checks.
- Native library architecture inspection for vendor `.so` and `.a` files.
- Native dependency closure checks for adapter-started subprocesses and helper daemons.
- Remote deploy and test over SSH.
- Stage markers and structured failure output for agent-driven repair loops.
- A documented agent/stage ownership model with global status and per-run logs.

## Directory Layout

```text
.codex/
  config.toml
  agents/
    context-mapper.toml
    docs-intake-agent.toml
    capability-modeler-agent.toml
    deployment-planner-agent.toml
    sdk-dependency-auditor-agent.toml
    spec-validator-agent.toml
    yaml-writer-agent.toml
    hal-adapter-builder.toml
    hal-registration-verifier-agent.toml
    package-builder.toml
    docker-builder.toml
    remote-deployer.toml
    remote-tester.toml
    failure-debugger.toml
  skills/
    device-adapter/
      SKILL.md
      scripts/
        context_to_manifest.py
        generate_runtime_files.py
        package_by_manifest.py
        verify_package.py
        verify_hal_adapter.py
        verify_native_deps.py
        docker_package.sh
        docker_smoke_test.sh
        remote_deploy.sh
        remote_test.sh
        stage_runner.sh
ops/
  contexts/
  artifacts/
AGENTS.md
docs/device_adapter_agent_workflow.md
```

## Agent Workflow

The current workflow is a staged Codex-agent pipeline:

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

Detailed ownership, error routing, logs, status files, and maintenance rules are in:

```text
docs/device_adapter_agent_workflow.md
```

Recorded future additions, not implemented yet:

```text
dependency-fetch-agent
vendor-materializer-agent
acceptance-planner-agent
```

## Command Flow

In some Codex CLI builds, `/device-adapter` is not a native slash command. If the CLI says `Unrecognized command`, trigger the skill in natural language:

```text
调用 device-adapter skill：context infrared_camera

<paste natural language context and docs/SDK paths>
```

Use this logical command flow:

```text
/device-adapter context infrared_camera
/device-adapter model infrared_camera
/device-adapter adapt infrared_camera --allow-code
/device-adapter verify infrared_camera
/device-adapter package infrared_camera
/device-adapter docker-package infrared_camera -arm
/device-adapter docker-package infrared_camera -x86
/device-adapter docker-package infrared_camera -all
/device-adapter deploy infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter test infrared_camera --host 192.168.1.100 --user root
/device-adapter loop infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter full infrared_camera --host 192.168.1.100 --user root -arm --allow-code
```

The scripts can also be run directly:

```bash
bash .codex/skills/device-adapter/scripts/stage_runner.sh model infrared_camera
bash .codex/skills/device-adapter/scripts/stage_runner.sh verify infrared_camera
bash .codex/skills/device-adapter/scripts/stage_runner.sh package infrared_camera
bash .codex/skills/device-adapter/scripts/stage_runner.sh docker-package infrared_camera -arm
```

`stage_runner.sh` now delegates to `stage_orchestrator.py` for all staged actions. Agent-owned stages are treated as handoff gates: if a required artifact such as `docs_coverage.json` or `device_spec.json` is missing, the command fails at that exact stage and writes `ops/artifacts/last_failure.json`.

## Natural Language Context Example

```text
这是一个 C++ 红外相机程序，读取 /dev/infrared_camera，然后通过 ffmpeg 推送 RTMP。
当前只提供 C++ 源码、头文件、链接库和配置，没有 Dockerfile、run.sh、docker-compose.yml。
代码在 src/。
头文件在 include/。
厂商库在 libs/。
SDK 在 sdk/。
配置在 configs/infrared_camera.yaml。
不要打包 build、dist、logs、test_videos、.git、__pycache__。
目标架构需要 x86 和 arm64。
远端板子是 arm64，默认用户 root。
```

For docs-first HAL adaptation, context should name the manual/protocol/SDK paths and the expected integration target:

```text
设备是某型号雷达。
设备手册在 docs/lidar_manual.pdf。
协议文档在 docs/lidar_protocol.md。
SDK 在 vendor/lidar_sdk/。
目标是接入 yunshu HAL 平台，生成 capability/device/deployment 三个 YAML。
adapter 需要通过 UDP 接收点云，启动一个 vendor daemon，并暴露状态、启动、停止、重连服务。
需要识别 apt、SDK header、arm64 .so、设备节点、端口、healthcheck。
```

The context step generates:

```text
ops/contexts/<context_id>.context.md
ops/contexts/<context_id>.manifest.json
ops/artifacts/<context_id>.package_files.txt
```

## Generated Artifacts

Package verification creates:

```text
ops/artifacts/<context_id>_package.tar.gz
ops/artifacts/<context_id>.package_tree.txt
ops/artifacts/<context_id>.package.sha256
ops/artifacts/<context_id>.package_verify.json
```

Docker packaging creates:

```text
ops/artifacts/<context_id>_amd64_image.tar
ops/artifacts/<context_id>_arm64_image.tar
ops/artifacts/<context_id>_<arch>_image_inspect.json
ops/artifacts/<context_id>_<arch>_native_deps.json
ops/artifacts/logs/<context_id>_docker_build_<arch>.log
```

HAL verification creates:

```text
ops/artifacts/<context_id>.spec_validation.json
ops/artifacts/<context_id>.dependency_validation.json
ops/artifacts/<context_id>.yaml_validation.json
ops/artifacts/<context_id>.registration_report.json
ops/artifacts/<context_id>.source_path_validation.json
ops/artifacts/<context_id>.cmake_install_validation.json
ops/artifacts/<context_id>.release_script_validation.json
```

Failures write:

```text
ops/artifacts/last_failure.json
ops/artifacts/<context_id>.stage_checkpoint.json
ops/artifacts/<context_id>.status.json
ops/artifacts/<context_id>.generated_files.txt
```

Staged commands append a live log:

```text
ops/artifacts/logs/<context_id>_stage_runner.log
```

Watch it while a workflow runs:

```bash
tail -f ops/artifacts/logs/<context_id>_stage_runner.log
```

`last_failure.json` includes `log_file` when available, so you can send the exact failing log back to Codex.

`status.json` is the quick progress view while Codex is running:

```bash
cat ops/artifacts/<context_id>.status.json
```

`generated_files.txt` records which stage produced each handoff artifact or generated file.

For a full stage-to-agent map and common error routing table, see:

```text
docs/device_adapter_agent_workflow.md
```

## Closed-Loop Delivery Packages

When the context asks for a complete board-side delivery package, `manifest.json` should include:

```json
{
  "delivery": {
    "closed_loop_package": true,
    "required_root_files": ["config.env", "install.sh", "run.sh", "status.sh", "view.sh", "DEPLOY.md"]
  }
}
```

Package verification then fails if these root entries are missing, if a declared runtime file is missing, or if any packaged script uses interactive Docker flags such as `docker exec -it`.

For ROS2/HAL packages, the root `run.sh` is a host-side launcher. Container entrypoints should live under `ops/scripts/` or a package-local path and must be non-interactive for SSH automation.

## Cross-Architecture Behavior

On an x86 WSL host, `docker-package -arm` builds and saves an arm64 image tar locally, but does not run the arm64 container locally by default. Runtime proof for arm64 should happen on the arm64 board or server:

```text
/device-adapter deploy infrared_camera --host <arm64-host> --user root -arm
/device-adapter test infrared_camera --host <arm64-host> --user root
```

Use `--smoke` only if QEMU/binfmt is configured locally and you intentionally want to force local cross-architecture `docker run --rm`.

## Stage Contract

Workflow scripts emit stage markers:

```text
[AGENT_STAGE] stage=<stage_name> status=start
[AGENT_STAGE] stage=<stage_name> status=success
[AGENT_STAGE] stage=<stage_name> status=fail exit_code=<code>
```

Current core stages include:

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

## Notes

- Business C/C++ source is not modified by default.
- Adapter source generation or modification requires `--allow-code`.
- Runtime-only files may be generated or patched.
- Vendor libraries must match the target architecture. An x86-only `.so` cannot run in an arm64 container unless an arm64 version is provided.
- Every executed native helper must have its shared-library closure satisfied by packaged libs, declared library paths, or explicit system libraries before Docker build/deploy.
- Device-specific runtime assumptions must come from context/manuals. Do not assume FFmpeg, ZLM, RTMP, V4L2, serial, CAN, UDP, TCP, or a helper daemon unless specified.
