# Repository Agent Notes

This repository includes a local Codex skill for unmanned-system device adaptation workflows.

Use `/device-adapter` commands when the user wants to create a device context, extract HAL device models from manuals, generate HAL YAML, implement adapter-layer code, package project files, build Docker artifacts, deploy to a remote board, run remote tests, collect logs, or iterate on deployment/runtime failures.

Default policy:
- Prefer runtime-only changes: Dockerfile, docker-compose.yml, .dockerignore, run.sh, requirements.txt, configs, ops, HAL model YAML, and device-adapter scripts.
- Do not edit business source code unless the user explicitly passes `--allow-code`.
- Do not package the whole repository. Package only files selected by the generated manifest.
- Preserve stage markers and write `ops/artifacts/last_failure.json` on workflow failure.
- For new HAL devices, prefer docs-first flow: context -> model -> adapt -> adapter code -> package -> docker-package -> deploy/test.
- Split docs-first HAL work into staged agent responsibilities: docs intake, capability modeling, deployment planning, dependency audit, spec validation, YAML writing, adapter codegen, registration verification, packaging, Docker build, remote deploy/test, and failure debugging.
- After generating or modifying HAL adapter files, run `/device-adapter verify <context_id>` or `python3 .codex/skills/device-adapter/scripts/verify_hal_adapter.py <context_id>` before package/build/deploy.
- Device-specific dependencies must be represented in `runtime_requirements` or legacy `adapter_requirements`: apt packages, SDK headers/libs, RPATH rules, device nodes, udev rules, kernel modules, environment, mounts, ports, subprocesses, and healthchecks.
- Deployable hardware adapters must be closed-loop release packages when the context asks for board-side direct run: include `config.env`, `install.sh`, `run.sh`, `status.sh`, `view.sh`, and `DEPLOY.md`, and make scripts non-interactive for SSH automation.
- Verify generated C++ includes, CMake install paths, package-local runtime scripts, and delivered helper binaries before packaging.
- Verify native dependency closure for every adapter-started executable or daemon; missing `.so` files must fail before Docker build/deploy unless explicitly declared as system-provided.
- Do not invent undocumented device protocol behavior. Put missing SDK/protocol facts in `ops/artifacts/<context_id>.adapter_gaps.md`.
- Do not hard-code known-device assumptions such as FFmpeg, ZLM, RTMP, V4L2, serial, CAN, UDP, TCP, or helper daemons unless the user's context or manual states them.
