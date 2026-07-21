---
name: device-adapter
description: Use for docs-first unmanned-device integration as an independently built HAL runtime Adapter plugin, including SDK contracts, C++ implementation, ABI/dependency verification, packaging, runtime deployment, hardware testing, and staged repair.
---

# Device Adapter

## Purpose

Turn device manuals, SDK material, and target-board facts into an independently
built HAL Adapter runtime plugin. The default delivery is not an in-tree HAL
source modification and not a per-device Docker image.

Read [references/runtime-plugin-contract.md](references/runtime-plugin-contract.md)
and [references/architecture-v5.md](references/architecture-v5.md) before
`model`, `adapt`, `plugin-build`, `verify`, `package`, `deploy`, or `test`.

`scripts/workflow_definition.json` is the authoritative Stage DAG. Agent maps,
prompts, and Python stage metadata must be derived from it, not maintained as a
second workflow definition.

## Commands

```text
/device-adapter context <id>
/device-adapter adapt <id> --allow-code --host <target> --user <user>
/device-adapter verify <id>
/device-adapter approve <id> --by <name>
/device-adapter package <id>
/device-adapter deploy <id> --host <host> --user <user>
/device-adapter test <id> --host <host> --user <user>
```

These are the normal user-facing commands. `model-prep`, `model`,
`target-sdk-package`, `sdk-check`, `target-plugin-build`, `review`, `rerun`,
`status`, and artifact cleanup are advanced internal/recovery actions. They are
not required in the normal path.

## Default Flow

```text
context
-> adapt --allow-code --host <target>
   [model-prep -> SDK reuse or target-sdk-package -> sdk-check -> model
    -> adapter task/code/tests -> target-plugin-build -> plugin verify]
-> verify [deterministic verification -> verification Agent -> C++ review
           -> differential review]
-> human approve -> package -> deploy -> test
```

Shorter commands do not remove stages. They make the orchestrator own the
handoffs. Every internal stage validates its declared input artifacts and writes
machine-readable stage evidence before the next stage can start.

An Agent-owned stage returns exit code `24` and writes
`ops/artifacts/<id>.agent_handoff.json`. This is an internal continuation, not a
user-visible failure and not a request for another command. The active Codex
run must read the handoff, invoke its owner role and required testing/review
Skill, write only declared outputs within the declared boundary, and rerun the
same top-level command automatically. Stop only on PASS, human approval, a real
BLOCKED/FAIL result, or three unsuccessful handoff attempts.

Do not ask whether planned tests or reports should be created. Host sandbox,
SSH credentials, missing hardware facts, and destructive operations remain
real external approval or blocking boundaries.

Stop at the first failed gate. Use `systematic-debugging` to create evidence and
a scoped remediation plan. Do not modify code until the user authorizes another
`adapt --allow-code` run.

## Command Authorization

Treat one explicit command as authorization for every deterministic substep in
that command's documented boundary. Do not ask for per-file or per-test
confirmation:

- `adapt <id> --allow-code` authorizes create/modify/delete operations under
  `adapter_plugins/<adapter_type>/**`, including CMake, config, device YAML,
  implementation, and tests, plus evidence under `ops/artifacts/**`;
- target build commands authorize their unique remote workspace and ephemeral
  `docker run --rm` build container;
- `test` authorizes a fresh isolated service container, ephemeral client
  containers, log collection, and cleanup according to `cleanup_policy`;
- `deploy` still requires fingerprint-bound release approval before changing the
  remote runtime root.

Write `ops/artifacts/<id>.workflow_authorization.json`. Continue without asking
optional questions. If required facts, credentials, permissions, or evidence are
missing, stop with BLOCKED and a remediation plan; never broaden scope or bypass
the host application's sandbox/approval policy.

Agent-owned report files are planned outputs, not new side effects. Create them
with native file-write/patch tools inside their declared `ops/artifacts/**`
directory. Never use shell heredocs, `cat >`, `tee`, or redirection to write a
report and never ask for a second approval to do so.

## Context And Documents

`context` preserves natural language in `ops/contexts/<id>.context.md`, recursively
inventories `docs/`, extracts native PDF text, and OCRs pages without usable text.
OCR is candidate evidence. Verify protocol bytes, identifiers, units, scaling,
tables, and diagrams against original pages before code generation.

Required Ubuntu tools:

```bash
sudo apt install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
```

`context` creates a draft `ops/contexts/<id>.plugin_contract.json`. Unknown SDK,
ABI, architecture, runtime-image, vendor, version, or capability references stay
empty and block `sdk-check`; never invent them.

## Model

Modeling is split to avoid a first-run SDK dependency cycle. `model-prep`
completes document evidence, functional-chain inputs, and only the SDK-build
subset of `plugin_contract.json`. It reads SDK version and ABI values from the
selected plugin platform source; it does not generate `device_spec` or select
capability groups. After `target-sdk-package`, bootstrap SDK validation checks
SDK integrity without requiring capability references. Formal `model` then
reads capability groups from the immutable SDK and produces `device_spec` and
final references. Full `sdk-check` remains mandatory before code generation.

The model agents produce facts and decisions separately:

```text
ops/contexts/<id>.device_observation.json
ops/contexts/<id>.device_spec.json
ops/contexts/<id>.functional_chain.json
ops/contexts/<id>.dependency_checklist.json
ops/contexts/<id>.plugin_contract.json
ops/contexts/<id>.sdk_inventory.json
ops/contexts/<id>.normalized_context.json
ops/contexts/<id>.capability_mapping.json
ops/contexts/<id>.transport_bindings.json
```

The plugin contract must declare `delivery_mode=runtime_plugin`, adapter/vendor
identity, plugin and SDK versions, SDK/plugin ABI, target architecture/platform,
target OS/compiler triplet, runtime image, platform capability-group references, SDK root, and mandatory
multi-instance support.

When the Adapter consumes generated strong-type domain contracts, declare
`fastpath_contract.required_constants` and `fastpath_contract.fixed_arrays`
in the plugin contract. These entries come from the selected platform capability
groups and ROS messages; do not infer them from a device category.

Inspect capability groups only from the immutable SDK
`model_reference/capability_groups/`. Generate one device YAML that references
platform groups. If no platform group can express the device, report a platform
maintainer gap and stop. Never generate capability YAML in a plugin package.

The functional chain must cover actual acquisition, transport, decode/process,
publish/control, receiver/service where applicable, and functional health proof.
Every required header, library, helper, daemon, device node, kernel feature,
mount, port, environment variable, and startup dependency must have evidence and
an owner.

Requested features are mapped only to capability groups discovered from the
immutable SDK. Required unmapped features block adaptation. Connection evidence
resolves through the Serial, CAN, UDP, TCP, USB, UVC, or Vendor SDK profiles;
multiple bindings are allowed. Device names never select capabilities or
transports.

The capability-modeler reasons from the current context and cited manual/SDK
evidence. It selects exact SDK-owned properties, services, events and FastPath
topics. It must not bind behavior from camera/radar/lidar/IMU or other device
category names, and must not copy undocumented behavior from an earlier
Adapter. Ambiguous mappings remain BLOCKED.

## Adapt Coding Loop

`adapt --allow-code` is one Codex-level closed loop:

1. Normalize context and resolve capability and transport evidence.
2. Generate `ops/contexts/<id>.adapter_implementation_task.json`.
3. The test-design role writes independent tests and RED evidence but not src.
4. The Adapter builder writes only plugin src/include/CMake/config and may not
   modify those independent tests, HAL platform source, capability YAML, or SDK.
5. Build in the declared AArch64 ROS 2 Humble runtime container and verify the
   resulting `.so` ABI, architecture, RPATH, dependency closure, model and config.
6. Run independent verification, C++ review and differential review.

`ops/artifacts/<id>.implementation_coverage.json` must prove every requested
feature is connected through an exact HAL entry, Adapter handler, Device
Backend, selected Transport, device response/output and independent test. A
scaffold or a report that omits one inferred feature cannot pass quality gates.

Missing protocol/SDK evidence returns `BLOCKED`; missing target build conditions
returns `TARGET_BUILD_ENVIRONMENT_UNAVAILABLE`. A generated fail-closed skeleton
is not completion evidence. Python creates task envelopes and deterministic
reports; Codex performs Agent-owned tests and source changes, then resumes the
same action automatically.

## SDK Gate

Generate the SDK only through the plugin-enabled platform's native script:

```text
<platform_source_root>/src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh
```

The contract records `platform_source_root`, `platform_install_prefix`,
`sdk_output_dir`, and final `sdk_root`. The HAL platform must first be built and
installed for the target architecture so its install prefix contains
`libhal_model.so`, `libhal_media.so`, and `libhal_utils.so`. Then run:

```bash
python3 .codex/skills/device-adapter/scripts/package_adapter_sdk.py <id>
```

The wrapper invokes the platform script with the declared version, architecture,
and install prefix; it never synthesizes SDK files. Reuse an immutable SDK and
rerun `sdk-package` only when platform SDK/ABI or target architecture changes.

When the local host does not match the target architecture, use
`target-sdk-package`. Keep local SDK paths separate from remote build fields:

```json
{
  "sdk_root": "build/sdk/hal_adapter_sdk_v2.0.0",
  "sdk_output_dir": "build/sdk",
  "platform_source_root": "/path/to/plugin-platform-source",
  "platform_install_prefix": "/tmp/device-adapter/<id>/<fingerprint>/install/hardware_abstraction_layer",
  "target_build": {
    "host": "172.16.3.80",
    "user": "root",
    "workspace": "/tmp/device-adapter/<id>/<fingerprint>",
    "ros_distro": "humble",
    "compiler_triplet": "aarch64-linux-gnu-gcc",
    "build_in_runtime_container": true,
    "sdk_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0",
    "plugin_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0"
  }
}
```

The target stage transfers a filtered, fingerprinted source bundle. SSH only
starts an ephemeral `docker run --rm` job on the target board; ROS setup,
colcon, the three HAL libraries, the native SDK packager, minimal Adapter build,
and file/nm/readelf/ldd checks all run inside `target_build.sdk_build_image`.
The mounted unique workspace preserves evidence and the SDK after the container
is removed. Record Git commit, dirty
state, diff hash, submodules, source hashes/excludes, remote OS/ROS/toolchain,
and ELF metadata. Never store SSH passwords; use an SSH key or agent. Conflicting
target host facts block execution. The fetched SDK must still pass `sdk-check`
and the minimal Adapter validation. On an x86 host validating an AArch64 SDK,
never build the minimal Adapter locally; consume the signed/fingerprinted target
container evidence produced by `target-sdk-package`.

Run:

```bash
python3 .codex/skills/device-adapter/scripts/plugin_sdk_check.py <id>
```

It validates SDK `VERSION`, `ABI_VERSION`, CMake config, plugin ABI header,
architecture-specific platform libraries, model linter, and referenced platform
capability groups. The version and ABI values in all four SDK sources must agree.
It also verifies every declared fast-path constant and fixed array against
`include/hal/domain_types_generated.hpp`. It then independently configures, builds, installs, and
inspects the SDK `examples/minimal_adapter`; missing ABI symbols, target
architecture, `$ORIGIN/../deps`, or dependency closure fail the gate. A platform without the plugin SDK/Registry is incompatible;
do not fall back to in-tree integration.

## Adapt

`adapt` generates or updates only the independent plugin project:

```text
adapter_plugins/<adapter_type>/
  CMakeLists.txt
  README.md
  include/<adapter_type>/<adapter_type>_adapter.hpp
  src/<adapter_type>_adapter.cpp
  src/<adapter_type>_plugin.cpp
  model/devices/<adapter_type>.device.yaml
  third_party/                 # optional private material
  cmake/VendorizeRuntime.cmake # optional
```

It must implement SDK `IDeviceAdapter` and export:

```text
hal_get_adapter_sdk_abi_v1
hal_get_adapter_plugin_v1
```

Every `create()` returns an independent object and every object is released by
`destroy()`. Instance-owned threads, processes, handles, ports, device endpoints,
callbacks, state, and logs must not use shared mutable globals.

The selected transport must include real discovery, setup, I/O, framing,
timeouts, malformed-input handling, reconnect/shutdown, HAL mapping, and tests.
Do not claim a parser-only or fail-closed placeholder is complete.

MUST NOT:

- create or modify capability YAML;
- add device branches to `adapter_factory`;
- modify the platform main CMake to compile the device;
- modify the platform deployment as plugin source generation;
- place private dependencies in platform global `3rdparty/lib`;
- bundle platform ABI libraries in plugin `deps/`;
- invent undocumented protocol or SDK behavior.

Use `test-driven-development` for Adapter, transport, protocol, dependency, and
build changes. Persist real RED/GREEN/regression commands and exit codes in
`ops/artifacts/<id>.tdd_report.json`.

## Build

Run `sdk-check`, then:

```bash
python3 .codex/skills/device-adapter/scripts/plugin_build.py <id>
```

This performs standalone CMake configure/build/install against
`HAL_ADAPTER_SDK_ROOT`. Cross-architecture builds require an explicit CMake
toolchain; otherwise build on the target architecture. It never rebuilds HAL.

For ARM64 plugins developed on x86, use `target-plugin-build`. It uploads the
checked ARM64 HAL SDK, plugin source, and only vendor archives declared in
`target_build.vendor_inputs`. After SSH transfer, configure/build/install,
`file`, ABI symbol checks, and native `ldd` run inside the exact HAL runtime
image declared by `runtime_image`; the formal package and container environment evidence are fetched locally
and passed to `verify_plugin.py`. This stage never searches the board for
undeclared dependencies and never rebuilds HAL.

## Verify And Review

`verify` is read-only for context, SDK contract, source, YAML, and package. It
writes evidence only under `ops/artifacts/` and checks:

- target ELF architecture;
- both exported ABI symbols and declared ABI versions;
- RPATH/RUNPATH includes `$ORIGIN/../deps`;
- every private `DT_NEEDED` library is in `deps/` with no `not found`;
- SDK model lint passes;
- adapter type agrees across contract, `.so`, plugin declaration, and YAML;
- package contains no capability YAML, nested adapter directory, or platform ABI library;
- private `config/<adapter_type>.json` parses strictly and selects instances by `hal.instance_index`;
- plugin source has no direct ROS dependency, ignores historical `transport_url`, and resolves private config relative to the loaded `.so`;
- model binding and FastPath keys/types have complete deterministic coverage;
- two simultaneous instances can coexist and destroying one does not affect the other.

After deterministic checks, the read-only verification Agent must use
`verification-before-completion`, `c-review` for C/C++, and
`differential-review`. Hardware checks not executed are `NOT_RUN`, never PASS.
These are separate stage11a, stage11b, and stage11c Agent handoffs. The same
`/device-adapter verify <id>` command resumes between them without another user
command.

Reports include:

```text
ops/artifacts/<id>.model_lint.json
ops/artifacts/<id>.abi_validation.json
ops/artifacts/<id>.plugin_declaration_validation.json
ops/artifacts/<id>.elf_arch_validation.json
ops/artifacts/<id>.dependency_closure.json
ops/artifacts/<id>.config_validation.json
ops/artifacts/<id>.source_contract_validation.json
ops/artifacts/<id>.binding_coverage.json
ops/artifacts/<id>.fastpath_coverage.json
ops/artifacts/<id>.forbidden_files.json
ops/artifacts/<id>.multi_instance_validation.json
ops/artifacts/<id>.verification_report.json
ops/artifacts/<id>.c_review_report.json
ops/artifacts/<id>.differential_review_report.json
```

Package/build/deploy requires all technical gates PASS plus explicit approval
bound to the current source/contract/package fingerprint:

```text
/device-adapter approve <id> --by <name>
```

Any source, context, contract, SDK inventory, device model, dependency, or
manifest change invalidates approval.

## Formal Package

The default package requires:

```text
adapters/libhal_adapter_<adapter_type>.so
model/devices/<adapter_type>.device.yaml
README.md
```

Optional entries are:

```text
config/<adapter_type>.json              # private configuration, when enabled
deps/*.so*                              # private dependencies only
```

`config/<adapter_type>.json` is generated, installed, and required only when
`plugin_contract.private_config.required=true` or the contract explicitly sets
`private_config.enabled=true`.
README validation is intentionally limited to the declared HAL Adapter SDK
version and SDK ABI.

One formal package declares exactly one `adapter_type`.

`package` writes `<id>_adapter_plugin.tar.gz` and `<id>.package_manifest.json`.
Source bundles and board-test kits are separate, explicitly named products and
must never be represented as the formal plugin package.

## Deploy And Test

Deploy the formal package to the host runtime root and mount it into the declared
HAL runtime image:

```text
host /etc/vega/access/runtime/adapters  -> container /hal-runtime/adapters
host /etc/vega/access/runtime/config    -> container /hal-runtime/config
host /etc/vega/access/runtime/deps      -> container /hal-runtime/deps
host /etc/vega/access/runtime/model/devices -> container /hal-runtime/model/devices
```

Do not compile HAL during deployment. Test four levels:

1. Load: plugin scanned, SDK/plugin ABI accepted, adapter type registered.
2. Instance: manager creates the child, lifecycle becomes active, list_devices returns it.
3. Capability: declared properties/services/events work with real responses.
4. Function: real device data/control, output validity, restart behavior, and soak criteria pass.

For ROS 2 cross-container/host tests also verify `ROS_DOMAIN_ID`,
`ROS_LOCALHOST_ONLY=0`, `RMW_IMPLEMENTATION`, and `hal_interface` compatibility.
Service discovery alone is not a functional pass.

Multi-instance tests create two live instances, allocate distinct endpoints and
resources, run both concurrently, and destroy either one without affecting the
other.

Remote acceptance is contract-driven but not optional. The deployment plan must
provide executable checks and evidence for missing/invalid private config,
instance mismatch, connect-disconnect-reconnect, SlowPath, FastPath, fault
injection, lifecycle cleanup, two real simultaneous instances, delayed reload,
and soak. A missing scenario is BLOCKED before SSH execution.

`test` always starts a fresh single-device HAL service container; it never finds
or reuses an existing one. The service and each ephemeral ROS client use host
networking, host IPC, identical DDS environment, the declared HAL image, and the
target project mounted read-only at `/workspace/yunshu`. ROS clients set
`ROS2CLI_NO_DAEMON=1`. The service log is collected before `cleanup_policy` is
applied. `runtime_test.project_dir`, `deployment_file`, and `manager_command`
must be evidence-backed in `deployment_plan.json`; missing values are BLOCKED.
Board facts that are not reliable during model may be supplied explicitly to
stage21 with `--project-dir`, `--deployment-file`, `--manager-command`,
`--image`, `--domain-id`, or `--rmw-implementation`. These values apply only to
that test run, are recorded in `remote_acceptance.json`, and never rewrite the
context contract.

`model <id> --project-dir <board-path>` records the board HAL workspace only in
the deployment plan. Stage5 generates both `<id>.deployment_plan.json` and a
single-device `<id>.deployment.yaml`. `deploy` installs that YAML under the
runtime `deployment/` directory. Generated acceptance checks are executable
where current hardware permits; checks requiring a second physical device,
manual disconnect, or an undeclared fault injector remain explicit `NOT_RUN`.
Stage21 executes all runnable checks. If they pass while reserved hardware
checks remain, it returns success with status `PASS_WITH_NOT_RUN`; the report
retains every `NOT_RUN` item and does not represent it as verified.

## State And Failure

Every stage emits human-readable markers and machine-readable JSON:

```text
[AGENT_STAGE] stage=<stage> status=start|success|waiting_agent|waiting_approval|fail
ops/artifacts/stages/<id>/<stage>.json
ops/artifacts/<id>.status.json
ops/artifacts/<id>.agent_handoff.json
ops/artifacts/logs/<id>_stage_runner.log
```

Monitor with:

```bash
tail -f ops/artifacts/logs/<id>_stage_runner.log
```

On failure write `ops/artifacts/last_failure.json` with stable error code,
evidence, owner Agent, allowed repair scope, resume stage, and retry count.
`failure-debugger` only writes `<id>.remediation_plan.json`; the owning Agent
applies fixes after user authorization.

Success means ABI, architecture, model, dependency closure, runtime loading,
capability behavior, real device function, and required multi-instance tests all
pass. Archive presence, compilation, container startup, process existence, or
ROS service discovery alone is insufficient.
