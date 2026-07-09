# Repository Agent Notes

This repository includes a local Codex skill for unmanned-system device adaptation workflows.

Use `/device-adapter` commands when the user wants to create a device context, extract HAL device models from manuals, generate HAL YAML, implement adapter-layer code, package project files, build Docker artifacts, deploy to a remote board, run remote tests, collect logs, or iterate on deployment/runtime failures.

Default policy:
- Prefer runtime-only changes: Dockerfile, docker-compose.yml, .dockerignore, run.sh, requirements.txt, configs, ops, HAL model YAML, and device-adapter scripts.
- Do not edit business source code unless the user explicitly passes `--allow-code`.
- Do not package the whole repository. Package only files selected by the generated manifest.
- Preserve stage markers and write `ops/artifacts/last_failure.json` on workflow failure.
- For new HAL devices, prefer docs-first flow: context -> model -> adapt -> adapter code -> package -> docker-package -> deploy/test.
- Do not invent undocumented device protocol behavior. Put missing SDK/protocol facts in `ops/artifacts/<context_id>.adapter_gaps.md`.
