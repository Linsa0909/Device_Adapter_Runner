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
before `model`, `adapt`, `plugin-build`, `verify`, `package`, `deploy`, or `test`.

## Commands

```text
/device-adapter context <id>
/device-adapter model-prep <id> [--host <host> --user <user>]
/device-adapter model <id>
/device-adapter sdk-package <id>
/device-adapter target-sdk-package <id> [--host <host> --user <user>]
/device-adapter sdk-check <id>
/device-adapter adapt <id> --allow-code
/device-adapter plugin-build <id>
/device-adapter target-plugin-build <id> [--host <host> --user <user>]
/device-adapter verify <id>
/device-adapter review <id>
/device-adapter approve <id> --by <name>
/device-adapter package <id>
/device-adapter docker-package <id> -arm
/device-adapter deploy <id> --host <host> --user <user>
/device-adapter test <id> --host <host> --user <user>
/device-adapter loop <id> --host <host> --user <user>
/device-adapter logs <id> --host <host> --user <user>
/device-adapter rerun <id>
```

`docker-package` validates or optionally exports the declared HAL runtime image;
it must not compile HAL or create a device-specific business image in plugin mode.

## Default Flow

```text
context -> model-prep -> target-sdk-package -> sdk-check --bootstrap
-> model -> sdk-check -> adapt --allow-code -> target-plugin-build
-> verify -> review -> human approve -> package -> deploy -> test
```

Stop at the first failed gate. Use `systematic-debugging` to create evidence and
a scoped remediation plan. Do not modify code until the user authorizes another
`adapt --allow-code` run.

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

The default package is exactly:

```text
adapters/libhal_adapter_<adapter_type>.so
config/<adapter_type>.json
deps/*.so*                              # private dependencies only, optional
model/devices/<adapter_type>.device.yaml
README.md
```

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

## State And Failure

Every stage emits human-readable markers and machine-readable JSON:

```text
[AGENT_STAGE] stage=<stage> status=start|success|fail
ops/artifacts/stages/<id>/<stage>.json
ops/artifacts/<id>.status.json
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
