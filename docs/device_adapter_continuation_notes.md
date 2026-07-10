# device-adapter Continuation Notes

Date: 2026-07-09

This file records the current state of the `device-adapter` work so the next session can continue without relying on chat history.

## Current Goal

Build a Codex skill for docs-first HAL device adaptation:

```text
device manual / SDK notes / natural language context
  -> docs inventory and coverage
  -> HAL capability/device/deployment model
  -> dependency audit
  -> adapter C++ / protocol / SDK integration
  -> CMake and factory registration
  -> colcon build inside Docker
  -> arm64 board deploy/test
  -> staged logs and rerun
```

The workflow must be generic. Do not hard-code Mino17, FFmpeg, ZLM, RTMP, V4L2, serial, CAN, UDP, TCP, MAVSDK, or helper daemons unless the context/manual explicitly requires them.

## Key Decision

This is not a pure fixed-script adapter generator. It is a Codex-driven staged workflow:

```text
Codex main agent:
  reads SKILL.md, interprets user commands, invokes subagents, writes semantic artifacts.

Subagents:
  handle docs understanding, capability modeling, deployment planning, dependency audit, code generation, and failure analysis.

Scripts:
  handle deterministic stage control, validation, packaging, Docker build, deploy, test, logs, and rerun.
```

## Implemented Files

Core skill:

```text
.codex/skills/device-adapter/SKILL.md
.codex/skills/device-adapter/scripts/stage_runner.sh
.codex/skills/device-adapter/scripts/stage_orchestrator.py
.codex/skills/device-adapter/scripts/verify_hal_adapter.py
.codex/skills/device-adapter/scripts/agent_stage_map.json
```

Existing scripts still used:

```text
context_to_manifest.py
adapt_hal_device.py
generate_runtime_files.py
package_by_manifest.py
verify_package.py
verify_native_deps.py
docker_package.sh
docker_smoke_test.sh
remote_deploy.sh
remote_test.sh
```

Agent configs:

```text
.codex/agents/context-mapper.toml
.codex/agents/docs-intake-agent.toml
.codex/agents/capability-modeler-agent.toml
.codex/agents/deployment-planner-agent.toml
.codex/agents/sdk-dependency-auditor-agent.toml
.codex/agents/spec-validator-agent.toml
.codex/agents/yaml-writer-agent.toml
.codex/agents/hal-device-modeler.toml
.codex/agents/hal-adapter-builder.toml
.codex/agents/hal-registration-verifier-agent.toml
.codex/agents/package-builder.toml
.codex/agents/docker-builder.toml
.codex/agents/remote-deployer.toml
.codex/agents/remote-tester.toml
.codex/agents/failure-debugger.toml
```

Documentation:

```text
docs/device_adapter_hal_agent_architecture.md
docs/device_adapter_continuation_notes.md
README.md
AGENTS.md
```

The skill was also synced to:

```text
/root/.codex/skills/device-adapter
```

## Commands

Run from the HAL project root, for example the `yunshu` repository root:

```bash
/device-adapter context <context_id>
/device-adapter model <context_id>
/device-adapter adapt <context_id> --allow-code
/device-adapter verify <context_id>
/device-adapter loop <context_id> --host <board-ip> --user root -arm
/device-adapter rerun <context_id>
```

Script equivalents:

```bash
bash .codex/skills/device-adapter/scripts/stage_runner.sh context <context_id>
bash .codex/skills/device-adapter/scripts/stage_runner.sh model <context_id>
bash .codex/skills/device-adapter/scripts/stage_runner.sh adapt <context_id> --allow-code
bash .codex/skills/device-adapter/scripts/stage_runner.sh verify <context_id>
bash .codex/skills/device-adapter/scripts/stage_runner.sh loop <context_id> --host <board-ip> --user root -arm
```

## Artifact Locations

All workflow artifacts are relative to the current HAL project root:

```text
ops/contexts/<context_id>.context.md
ops/contexts/<context_id>.manifest.json
ops/contexts/<context_id>.docs_inventory.json
ops/contexts/<context_id>.docs_coverage.json
ops/contexts/<context_id>.dependency_plan.json
ops/contexts/<context_id>.device_spec.json
ops/artifacts/<context_id>.adapter_gaps.md
ops/artifacts/<context_id>.spec_validation.json
ops/artifacts/<context_id>.dependency_validation.json
ops/artifacts/<context_id>.yaml_validation.json
ops/artifacts/<context_id>.registration_report.json
ops/artifacts/<context_id>.stage_checkpoint.json
ops/artifacts/last_failure.json
ops/artifacts/logs/<context_id>_stage_runner.log
```

Monitor a running workflow:

```bash
tail -f ops/artifacts/logs/<context_id>_stage_runner.log
```

`last_failure.json` includes `log_file` when available.

## Current Orchestrator Behavior

`stage_runner.sh` delegates staged actions to `stage_orchestrator.py`.

The orchestrator has two stage types:

```text
agent handoff stage:
  verifies that a subagent-created artifact exists.
  if missing, writes last_failure.json with exact stage and next_action.

script stage:
  runs deterministic scripts and tees output to the stage log.
```

Examples:

```text
model:
  checks context.md/manifest.json
  checks docs_inventory.json/docs_coverage.json
  checks device_spec.json
  checks runtime_requirements or adapter_requirements

adapt:
  requires device_spec.json
  runs adapt_hal_device.py
  then runs verify_hal_adapter.py

verify:
  runs verify_hal_adapter.py

loop:
  verify -> package -> docker-package -> deploy -> test
```

## What verify_hal_adapter.py Checks

```text
device_spec.json required fields
runtime_requirements or adapter_requirements
HAL capability/device/deployment YAML paths
declared vendor headers/libs/protocol files
adapter source/header directory
CMakeLists.txt contains adapter
adapter_factory.hpp contains adapter_type
deployment.yaml contains adapter_type
install(TARGETS ...) exists
```

Outputs:

```text
ops/artifacts/<context_id>.spec_validation.json
ops/artifacts/<context_id>.dependency_validation.json
ops/artifacts/<context_id>.yaml_validation.json
ops/artifacts/<context_id>.registration_report.json
```

## Verified Locally

Static checks passed:

```text
Python AST check
Bash syntax check
TOML parse check
JSON parse check for agent_stage_map.json
```

Behavior checks passed in `/tmp`:

```text
verify with missing device_spec fails at stage7_spec_validate
model with missing docs_inventory fails at stage2_docs_inventory
minimal fake HAL project passes verify gates
stage_runner.log is written
last_failure.json includes log_file
```

## Important Usage Note

Run `/device-adapter` inside the target HAL project root. For the current HAL architecture, that means a local checkout of `yunshu` with:

```text
src/hardware_abstraction_layer/
src/hal_interface/
```

The workflow reads and edits files under that project, and writes `ops/` under that same project.

## Current Limitations

Still missing:

```text
adapter-example-miner-agent
mine_adapter_examples.py
examples/adapters/<adapter>/profile.json generation
adapter_codegen_plan.json
generate_adapter_codegen_plan.py
device_spec.schema.json
runtime_requirements.schema.json
verify_adapter_interface.py
verify_build.py for standalone local colcon/cmake build gate
```

Current `model` is an artifact gate, not a deterministic document parser. Codex/subagents must still create:

```text
docs_inventory.json
docs_coverage.json
device_spec.json
dependency_plan.json
```

Current adapter source generation is still handled by Codex/subagents, not by a deterministic code generator.

## Recommended Next Step

Implement the example mining flow so users do not need to provide rigid context templates.

Target additions:

```text
.codex/agents/adapter-example-miner-agent.toml
.codex/skills/device-adapter/scripts/mine_adapter_examples.py
examples/adapters/<adapter>/profile.json
ops/contexts/<context_id>.adapter_codegen_plan.json
```

Purpose:

```text
Scan existing HAL adapters such as mino17, siyi, px4, dji.
Extract CMake registration style, factory registration style, bindPropertyGetter/bindServiceHandler style, dependency patterns, subprocess patterns, and install rules.
Use the closest profile when generating a new adapter.
```

After that, implement:

```text
generate_adapter_codegen_plan.py
verify_adapter_interface.py
device_spec JSON schema validation
runtime_requirements JSON schema validation
```
