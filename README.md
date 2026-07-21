# Device Adapter Runner

Device Adapter Runner is a docs-first Codex workflow for producing independent
HAL runtime Adapter plugins for unmanned-system devices. It consumes a versioned
HAL Adapter SDK and delivers a plugin `.so`, private dependencies, one device
model, and release evidence without rebuilding or modifying the HAL platform.

## Version Baselines

- `v1.1`: authoritative Stage DAG, context-derived capability/transport mapping,
  bounded Agent continuation, optional private configuration, independent
  verification/C++/differential review gates, and one canonical Skill source.
- `v1.0`: previous runtime-plugin workflow checkpoint retained for rollback and
  behavior comparison.

Inspect a release without moving `main`:

```bash
git switch --detach v1.1
git switch main
```

Create an isolated rollback branch from the earlier baseline:

```bash
git switch -c rollback/v1.0 v1.0
```

The repository copy at `.codex/skills/device-adapter` is authoritative. The
global Codex installation should point to that directory rather than maintain a
second copied Skill, so repository and global behavior cannot drift.

## Default Contract

The default product is:

```text
adapters/libhal_adapter_<adapter_type>.so
model/devices/<adapter_type>.device.yaml
README.md

config/<adapter_type>.json              # optional private configuration
deps/*.so*                              # optional private dependencies
```

`config/<adapter_type>.json` is optional and becomes mandatory only when
`plugin_contract.private_config.required=true`. The deterministic README gate
checks only the HAL Adapter SDK version and SDK ABI against the plugin contract.

The workflow does not create capability YAML, add a Factory branch, modify the
platform main CMake, or build a per-device Docker image. Platform capability
groups and ABI libraries remain platform-owned.

## Prerequisites

- Codex with the global `device-adapter` skill installed.
- Device manuals and SDK material under the target project's `docs/`.
- An immutable HAL Adapter SDK matching the target runtime image.
- CMake and a native or declared cross compiler for the target architecture.
- Docker only when validating/exporting the existing HAL runtime image.
- SSH access for board deployment and hardware acceptance.

For AArch64 targets, all HAL SDK, minimal Adapter, and device-plugin compilation
runs over SSH inside the declared ARM64 build images. The local x86 host only
prepares contracts/source bundles and validates returned evidence; it does not
compile ARM64 C++ artifacts.

PDF extraction and OCR on Ubuntu/WSL:

```bash
sudo apt update
sudo apt install -y poppler-utils tesseract-ocr \
  tesseract-ocr-chi-sim tesseract-ocr-eng
```

## Workflow

```text
/device-adapter context <id>
/device-adapter adapt <id> --allow-code --host <arm64-host> --user root
/device-adapter verify <id>
/device-adapter approve <id> --by <name>
/device-adapter package <id>
/device-adapter deploy <id> --host <host> --user root
/device-adapter test <id> --host <host> --user root
```

`adapt` owns the internal preparation chain: model preparation, reuse or remote
generation of the immutable SDK, SDK validation, formal model mapping, bounded
plugin implementation, target-container build, and static plugin verification.
`verify` owns deterministic verification and the required read-only verification,
C/C++ review, and differential-review gates. Advanced stage commands remain
available for diagnostics and precise reruns, but are not part of normal use.

The normal `adapt` command does not require a board project path. Stage5 first
uses `target_build.runtime_project_dir`; otherwise it reuses the unique remote
workspace recorded by the successful `target-sdk-package` report. Explicit
`--project-dir` remains a stage21 test override for exceptional board layouts,
not a normal Adapter-development input.

Stage5 generates the machine-readable deployment plan and one-device deployment
YAML from the resolved transport bindings. Deploy installs the YAML into the
runtime tree; stage21 mounts the evidence-backed HAL `install/` workspace into
the runtime container.

Advanced recovery commands, also available through the deterministic runner:

```bash
bash .codex/skills/device-adapter/scripts/stage_runner.sh model-prep <id>
bash .codex/skills/device-adapter/scripts/stage_runner.sh target-sdk-package <id> --host <arm64-host> --user root
bash .codex/skills/device-adapter/scripts/stage_runner.sh sdk-check <id>
bash .codex/skills/device-adapter/scripts/stage_runner.sh target-plugin-build <id> --host <arm64-host> --user root
bash .codex/skills/device-adapter/scripts/stage_runner.sh verify <id>
```

The target build contract keeps build and runtime images separate:

```json
{
  "target_build": {
    "build_in_runtime_container": true,
    "sdk_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0",
    "plugin_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0"
  },
  "runtime_image": "<immutable HAL runtime image>"
}
```

`target-sdk-package` and `target-plugin-build` use SSH only for transfer and
orchestration. Compilation and native ELF/dependency checks run in ephemeral
`docker run --rm` containers on the target board. Build outputs and evidence are
preserved through a mounted fingerprinted workspace and fetched back locally.

`docker-package` no longer compiles HAL. It validates the architecture of the
runtime image declared in `plugin_contract.json`; `--save-runtime-image` exports
that existing platform image for offline board delivery.

## Required Contract

`context` writes a draft:

```text
ops/contexts/<id>.plugin_contract.json
```

`model` must complete:

- adapter type, vendor, and plugin version;
- SDK root, version, and ABI;
- plugin ABI;
- target architecture and platform;
- target OS and compiler triplet;
- immutable HAL runtime image;
- platform capability-group references;
- mandatory multi-instance support.

Unknown required values block `sdk-check`. The skill does not invent them and
does not fall back to old in-tree integration when plugin SDK support is absent.

If the immutable SDK is absent, complete these contract fields first:

```json
{
  "platform_source_root": "/path/to/plugin-enabled/yunshu_access",
  "platform_install_prefix": "/path/to/install/hardware_abstraction_layer",
  "sdk_output_dir": "build/adapter-sdk",
  "sdk_root": "build/adapter-sdk/hal_adapter_sdk_v1.1.0"
}
```

`sdk-package` calls the platform-owned
`src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh`. It does not
assemble SDK files itself. The install prefix must already contain the three HAL
platform libraries built for the declared target architecture.

## Generated Plugin Source

```text
adapter_plugins/<adapter_type>/
  CMakeLists.txt
  README.md
  include/<adapter_type>/<adapter_type>_adapter.hpp
  src/<adapter_type>_adapter.cpp
  src/<adapter_type>_plugin.cpp
  config/<adapter_type>.json    # only when private_config is enabled
  model/devices/<adapter_type>.device.yaml
  third_party/                 # optional
  cmake/VendorizeRuntime.cmake # optional
```

The C ABI entry exports:

```text
hal_get_adapter_sdk_abi_v1
hal_get_adapter_plugin_v1
```

Every `create()` must return an independent instance. Device paths, ports,
threads, processes, handles, callbacks, state, and logs are instance-owned.

## Verification

The deterministic gate checks:

- SDK version, ABI, CMake config, platform libs, model linter, and capabilities;
- standalone plugin build and target ELF architecture;
- both ABI symbols;
- `$ORIGIN/../deps` RPATH/RUNPATH;
- complete private `DT_NEEDED` closure;
- SDK device-model lint;
- forbidden capability/platform files;
- two simultaneous create/destroy instances.

Then the read-only verification Agent runs build/tests,
`verification-before-completion`, C/C++ review, and differential review. Explicit
human approval is bound to the tested source and contract fingerprint.

### V5 Evidence Pipeline

V5 uses `scripts/workflow_definition.json` as the Stage DAG contract and the
fixed `yunshu-aarch64-humble` platform profile. Before source generation,
`adapt --allow-code` produces evidence-backed intermediate contracts:

```text
ops/contexts/<id>.normalized_context.json
ops/contexts/<id>.capability_mapping.json
ops/contexts/<id>.transport_bindings.json
ops/contexts/<id>.adapter_implementation_task.json
```

Required features must map to capability groups present in the immutable HAL
SDK. Connections resolve through Serial, CAN, UDP, TCP, USB, UVC, or Vendor SDK
profiles. Missing facts block coding instead of being inferred from a device
name. Independent tests are owned by the test-design Agent; the implementation
Agent cannot modify those tests or the HAL platform source. Deployment verifies
local and remote SHA-256 values before unpacking the plugin package.

Mapping is based on the current context and cited protocol/SDK evidence down to
individual HAL properties, services, events and topics. Device category names
and behavior from previous Adapters are not mapping inputs. The implementation
coverage gate then requires a source function, Backend method, Transport binding
and independent test for every mapped feature.

Agent-owned stages write `ops/artifacts/<id>.agent_handoff.json` and return an
internal continuation code. Codex runs the bounded owner Agent and resumes the
same top-level command; users do not issue separate model, SDK, build, or review
commands during the normal path. The handoff records Git/file-state baselines,
and the resumed stage rejects any write outside the workflow DAG boundary.

### Workflow Authorization

Each explicit `/device-adapter` command is one complete workflow authorization
within its documented boundary. For example, `adapt gemini335 --allow-code`
authorizes the plugin implementation, CMake, configuration, tests, and evidence
files under `adapter_plugins/gemini335/**` and `ops/artifacts/**`; agents must not
pause for separate approval before creating each planned test or review report.
The orchestrator records the effective scope in:

```text
ops/artifacts/<context_id>.workflow_authorization.json
```

Planned reports must be written with workspace file APIs. Agents are explicitly
forbidden from using `cat >`, heredocs, `tee`, or shell redirection for those
files, which avoids turning an ordinary report write into another shell approval
request. Missing required information stops the workflow as `BLOCKED` and emits
a remediation plan instead of opening an interactive design loop.

Codex's own security boundary remains authoritative. SSH, restricted network,
privileged Docker, credentials, and writes outside the workspace may still cause
a platform-level approval prompt. The Skill cannot and must not silently bypass
that protection; reusable approvals should be narrowly scoped to the required
command family.

Primary reports:

```text
ops/artifacts/<id>.sdk_check.json
ops/artifacts/<id>.plugin_build.json
ops/artifacts/<id>.abi_validation.json
ops/artifacts/<id>.plugin_declaration_validation.json
ops/artifacts/<id>.elf_arch_validation.json
ops/artifacts/<id>.dependency_closure.json
ops/artifacts/<id>.config_validation.json
ops/artifacts/<id>.source_contract_validation.json
ops/artifacts/<id>.binding_coverage.json
ops/artifacts/<id>.fastpath_coverage.json
ops/artifacts/<id>.model_lint.json
ops/artifacts/<id>.multi_instance_validation.json
ops/artifacts/<id>.quality_gate_report.json
ops/artifacts/<id>.package_manifest.json
```

## Runtime Deployment

The formal package is extracted to:

```text
/etc/vega/access/runtime/adapters
/etc/vega/access/runtime/config
/etc/vega/access/runtime/deps
/etc/vega/access/runtime/model/devices
```

The platform runtime mounts those paths at `/hal-runtime`. Deployment never
compiles HAL or the plugin.

Remote acceptance has five required groups in
`ops/contexts/<id>.deployment_plan.json`:

```text
load -> instance -> capability -> functional -> multi_instance
```

The plan also requires fixed scenarios for invalid/missing config, instance
selection mismatch, reconnect, SlowPath, FastPath, fault injection, lifecycle
cleanup, two real instances, delayed reload, and soak. Every scenario carries an
executable command, expected result, and evidence path; missing coverage is
BLOCKED before remote execution.

When all executable single-device checks pass but reserved physical checks
(such as a second real device or controlled disconnect) cannot run, stage21
returns `PASS_WITH_NOT_RUN`. The report retains those items and does not count
them as verified delivery evidence.

Each group contains evidence-driven commands and expected output. Service
discovery, process existence, and container startup alone are not success.

## Status And Logs

```bash
cat ops/artifacts/<id>.status.json
cat ops/artifacts/last_failure.json
tail -f ops/artifacts/logs/<id>_stage_runner.log
```

Every failure identifies the stage, stable error code, evidence, responsible
Agent, repair scope, and resume point. The failure debugger writes a remediation
plan only; code repair requires user authorization.

## Important Baseline Check

A compatible platform source or SDK must contain the Adapter SDK CMake config,
plugin ABI header, architecture-specific platform libraries, model references,
and model lint tool. A legacy platform that only calls
`DeviceAdapterFactory::create()` is not plugin-compatible and is blocked by
`sdk-check`.
