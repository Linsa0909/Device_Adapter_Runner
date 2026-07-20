# Repository Agent Notes

This repository includes a local Codex skill for unmanned-system device adaptation workflows.

Use `/device-adapter` commands to create independent HAL runtime Adapter plugins from manuals, SDK evidence, and board facts.

Default policy:
- Default delivery_mode is `runtime_plugin`; do not fall back to in-tree integration.
- Do not edit business source code unless the user explicitly passes `--allow-code`.
- Write device code only under `adapter_plugins/<adapter_type>/`.
- Do not generate capability YAML, modify adapter_factory, modify platform main CMake, or place private libraries in platform global directories.
- Package only adapters/, optional deps/, model/devices/, and README.md.
- Preserve stage markers and write `ops/artifacts/last_failure.json` on workflow failure.
- Use: context -> model -> sdk-package when SDK is absent/stale -> sdk-check -> adapt --allow-code -> plugin-build -> verify/review -> approve -> package -> deploy/test.
- Generate the immutable Adapter SDK only through the plugin platform's `src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh`; never synthesize SDK files during device adaptation.
- Split docs-first HAL work into staged agent responsibilities: docs intake, capability modeling, deployment planning, dependency audit, spec validation, YAML writing, adapter codegen, registration verification, packaging, Docker build, remote deploy/test, and failure debugging.
- Build only against the immutable HAL Adapter SDK and run `verify_plugin.py` before package/deploy.
- Device-specific dependencies must be represented in `runtime_requirements` or legacy `adapter_requirements`: apt packages, SDK headers/libs, RPATH rules, device nodes, udev rules, kernel modules, environment, mounts, ports, subprocesses, and healthchecks.
- After context/model, generate and validate the functional-chain checklist: `functional_chain.json`, `dependency_checklist.json`, and `dependency_gaps.md`. Packaging must stop if the declared device function lacks receiver/services, offline runtime material, or healthchecks.
- Source bundles, board-test kits, and formal plugin packages are distinct products.
- Treat `verify` as read-only for context/spec/YAML/source files. New hardware facts must flow through context/model and then `adapt --allow-code` before verification.
- A selected CAN/SocketCAN, serial, or network transport must have a concrete I/O backend; protocol-frame decoding alone does not satisfy transport implementation.
- Code-generating adapt stages must use test-driven-development and persist real RED/GREEN/regression evidence in `ops/artifacts/<context_id>.tdd_report.json`.
- After deterministic verification, run the read-only verification Agent with verification-before-completion, c-review for C/C++ scope, and differential-review. Do not package until all required reports are PASS.
- Require explicit `/device-adapter approve <context_id> --by <name>` before package/build/deploy. Approval is bound to the tested source fingerprint and becomes stale after source/context/spec/manifest changes.
- On build/test/review/runtime failure, use systematic-debugging to produce evidence and a remediation plan. Do not modify code or invoke a repair Agent until the user authorizes it.
- Verify plugin ABI symbols, architecture, model lint, `$ORIGIN/../deps`, private dependency closure, runtime loading, real function, and two simultaneous instances.
- `docker-package` validates/exports the declared platform runtime image; it does not rebuild HAL or create a per-device image.
- Do not invent undocumented device protocol behavior. Put missing SDK/protocol facts in `ops/artifacts/<context_id>.adapter_gaps.md`.
- Do not hard-code known-device assumptions such as FFmpeg, ZLM, RTMP, V4L2, serial, CAN, UDP, TCP, or helper daemons unless the user's context or manual states them.
