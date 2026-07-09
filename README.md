# Device Adapter Runner

Device Adapter Runner is a Codex workflow scaffold for unmanned-system device projects. It turns natural language device context plus existing C/C++ source, headers, vendor libraries, SDK files, and configs into a staged package/build/deploy/test pipeline.

The workflow is designed for cases where the project starts without runtime packaging files. The adapter can generate runtime-only files such as `Dockerfile`, `run.sh`, `docker-compose.yml`, `.dockerignore`, and a minimal `CMakeLists.txt` when needed.

## What It Provides

- Context-first packaging from natural language descriptions.
- Manifest-based file selection instead of packaging the whole repository.
- Runtime file generation for C/C++ device projects.
- x86_64 and arm64 Docker image builds with Docker buildx.
- Local package verification with package tree and SHA-256 output.
- Native library architecture inspection for vendor `.so` and `.a` files.
- Remote deploy and test over SSH.
- Stage markers and structured failure output for agent-driven repair loops.

## Directory Layout

```text
.codex/
  config.toml
  agents/
    context-mapper.toml
    package-builder.toml
    docker-builder.toml
    remote-deployer.toml
    remote-tester.toml
    failure-debugger.toml
  skills/
    device_adapter/
      SKILL.md
      scripts/
        context_to_manifest.py
        generate_runtime_files.py
        package_by_manifest.py
        verify_package.py
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
```

## Command Flow

Use the Codex command form:

```text
/device-adapter context infrared_camera
/device-adapter package infrared_camera
/device-adapter docker-package infrared_camera -arm
/device-adapter docker-package infrared_camera -x86
/device-adapter docker-package infrared_camera -all
/device-adapter deploy infrared_camera --host 192.168.1.100 --user root -arm
/device-adapter test infrared_camera --host 192.168.1.100 --user root
/device-adapter loop infrared_camera --host 192.168.1.100 --user root -arm
```

The scripts can also be run directly:

```bash
bash .codex/skills/device-adapter/scripts/stage_runner.sh package infrared_camera
bash .codex/skills/device-adapter/scripts/stage_runner.sh docker-package infrared_camera -arm
```

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

Failures write:

```text
ops/artifacts/last_failure.json
```

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
stage1_context_load
stage2_context_validate
stage3_runtime_generate
stage4_package_create
stage5_package_verify
stage6_native_deps_verify
stage7_docker_build
stage8_image_inspect
stage8_container_smoke_test
stage9_image_save
stage5_transfer
stage6_remote_prepare
stage8_remote_test
stage9_collect_logs
```

## Notes

- Business C/C++ source is not modified by default.
- Runtime-only files may be generated or patched.
- Vendor libraries must match the target architecture. An x86-only `.so` cannot run in an arm64 container unless an arm64 version is provided.
