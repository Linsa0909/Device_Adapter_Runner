# Repository Agent Notes

This repository includes a local Codex skill for unmanned-system device adaptation workflows.

Use `$device_adapter` commands when the user wants to create a device context, package project files, build Docker artifacts, deploy to a remote board, run remote tests, collect logs, or iterate on deployment/runtime failures.

Default policy:
- Prefer runtime-only changes: Dockerfile, docker-compose.yml, .dockerignore, run.sh, requirements.txt, configs, ops, and device_adapter scripts.
- Do not edit business source code unless the user explicitly passes `--allow-code`.
- Do not package the whole repository. Package only files selected by the generated manifest.
- Preserve stage markers and write `ops/artifacts/last_failure.json` on workflow failure.
