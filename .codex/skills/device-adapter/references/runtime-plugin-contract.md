# Runtime Plugin Contract

## Required Contract

`ops/contexts/<context_id>.plugin_contract.json` is the release contract:

```json
{
  "schema_version": "1.0",
  "delivery_mode": "runtime_plugin",
  "context_id": "mino17",
  "adapter_type": "mino17",
  "vendor": "guide",
  "plugin_version": "1.1.0",
  "sdk_root": "/path/to/hal_adapter_sdk_v2.0.0",
  "platform_source_root": "/path/to/plugin-enabled/yunshu_access",
  "platform_install_prefix": "/path/to/install/hardware_abstraction_layer",
  "sdk_output_dir": "build/adapter-sdk",
  "sdk_version": "2.0.0",
  "sdk_abi": 2,
  "plugin_abi": 1,
  "target_arch": "aarch64",
  "target_platform": "RK3588",
  "target_os": "ubuntu22.04",
  "compiler_triplet": "aarch64-linux-gnu-gcc",
  "runtime_image": "registry.example/hal-runtime:immutable-tag",
  "capability_group_refs": ["camera"],
  "fastpath_contract": {
    "required_constants": [
      "ImuData::SOURCE_COMBINED",
      "ImuData::SOURCE_ACCEL",
      "ImuData::SOURCE_GYRO"
    ],
    "fixed_arrays": [
      {
        "domain_type": "ImuData",
        "field": "orientation_covariance",
        "element_type": "double",
        "length": 9
      }
    ]
  },
  "supports_multi_instance": true,
  "plugin_source_dir": "adapter_plugins/mino17",
  "build_dir": "build/mino17-plugin",
  "package_dir": "build/mino17-package"
}
```

Unknown required values block SDK check and build. Use immutable image tags or
digests for release.

`fastpath_contract` is optional when a plugin does not consume generated
strong-type data. When present, it is a hard SDK gate. Constants use
`DomainType::CONSTANT`; fixed arrays name the generated domain type, field,
C++ element type, and exact length.

`sdk-package` invokes exactly
`<platform_source_root>/src/hardware_abstraction_layer/scripts/package_adapter_sdk.sh`.
The platform inputs are read-only, and `sdk_root` must equal
`<sdk_output_dir>/hal_adapter_sdk_v<sdk_version>`.

One formal package declares exactly one `adapter_type`. The Registry scans only
regular `.so` files directly under the configured plugin directory; nested
adapter directories are invalid. Runtime loading is startup-time plus explicit
`refresh()`, not automatic filesystem watching.

## Write Boundaries

- SDK Packager: read platform source/install; write `build/**` and SDK reports.
- Model/YAML agents: write context contracts or one plugin device YAML only.
- Adapter Builder: write `adapter_plugins/<adapter_type>/**` and TDD evidence.
- Plugin Builder: read source/SDK; write build/install output and logs only.
- Verification/review: read product inputs; write `ops/artifacts/**` only.
- Package Builder: read installed plugin package; write formal archive/manifest only.
- Remote agents: write local evidence and declared remote runtime directories only.

Any write outside the stage allowlist is `BOUNDARY_WRITE_VIOLATION`.

## Command Authorization

An explicit `/device-adapter` action authorizes every declared, deterministic
substep of that action. The orchestrator records the scope in
`ops/artifacts/<context_id>.workflow_authorization.json` before running stages.

- `adapt <id> --allow-code` authorizes all plugin source, CMake, config, model,
  test, and TDD evidence changes below the plugin's write allowlist.
- `target-sdk-package` and `target-plugin-build` authorize their unique remote
  workspace and ephemeral build container.
- `test` authorizes one fresh service container, ephemeral client containers,
  evidence collection, and declared cleanup.
- Verification and review agents may write only their predeclared report trees
  under `ops/artifacts/**`.

Agents must use native workspace write/patch APIs for reports. Shell heredocs,
`cat >`, `tee`, and output redirection are forbidden for planned report files,
because they turn an already-authorized workflow output into a new shell side
effect that may trigger a redundant approval prompt.

This contract does not bypass Codex host security. An external-network request,
SSH operation, privileged Docker operation, credential access, or write outside
the workspace may still require one platform-level approval. Such approval
should use a narrowly scoped reusable command prefix where the host supports it.
The skill must not ask optional design questions during execution: missing
required facts produce `BLOCKED` plus a remediation plan.

## Product Separation

Formal plugin package:

```text
adapters/libhal_adapter_<type>.so
deps/*.so*
model/devices/<type>.device.yaml
README.md
```

Source bundle and board test kit are separately named optional artifacts. A HAL
workspace, deployment YAML, Docker scripts, logs, test data, SDK headers, and
platform libraries are not formal plugin-package members.

## Ownership

- Platform owns SDK ABI, plugin loader, capability groups, common libraries,
  manager, device node, and runtime image.
- Plugin owns one device YAML, Adapter logic, protocol/transport logic, private
  dependencies, instance resources, and device-specific tests.
- Deployment owns plugin directory mounts, instance configuration, runtime
  environment, and board-side acceptance evidence.

## Dependency Rules

Private dependencies use device/vendor/version-identifiable names where needed,
retain required symlink chains, and resolve from `$ORIGIN/../deps`. Do not copy
`libhal_model`, `libhal_media`, or `libhal_utils` into `deps/`. Record SONAME,
`DT_NEEDED`, architecture, source, license, checksum, and runtime owner.

For cross-architecture verification, parse `file`, `readelf`, and `nm`; do not
execute AArch64 binaries on x86 unless an explicit emulation test is requested.

## Runtime Acceptance

Required evidence:

- Registry scan and ABI acceptance logs.
- Plugin declaration and adapter type registration.
- Two concurrent create/destroy instances.
- Manager child process lifecycle and list_devices result.
- Real property/service/event behavior.
- Device-specific data/control health proof and soak/restart results.
- ROS middleware/domain/interface compatibility when tests cross containers or hosts.

Classify missing hardware tests as `NOT_RUN` or `BLOCKED`, not PASS.

`deployment_plan.json` declares `ros_compatibility` (`domain_id`,
`localhost_only`, `rmw_implementation`, `hal_interface_version`) and
evidence-backed `acceptance_checks` lists for `load`, `instance`, `capability`,
`functional`, and `multi_instance`.

The runtime portion is explicit and board-specific. Example:

```json
{
  "ros_compatibility": {
    "domain_id": 0,
    "localhost_only": 0,
    "rmw_implementation": "rmw_fastrtps_cpp",
    "hal_interface_version": "2.0.0"
  },
  "runtime_test": {
    "image": "registry.ghostcloud.cn/integration/hal_dev:v1.0",
    "project_dir": "/home/gemini335/yunshu_access",
    "project_mount": "/workspace/yunshu",
    "runtime_root": "/etc/vega/access/runtime",
    "runtime_mount": "/hal-runtime",
    "deployment_file": "/etc/vega/access/runtime/deployment/gemini335-only.yaml",
    "manager_command": "ros2 launch hardware_abstraction_layer manager_node.launch.py deployment_config:=/hal-runtime/deployment/gemini335-only.yaml",
    "ros_setup": "/opt/ros/humble/setup.bash",
    "workspace_setup": "/workspace/yunshu/install/setup.bash",
    "instance_count": 1,
    "enabled_adapter_types": ["gemini335"],
    "privileged": true,
    "cleanup_policy": "keep_on_failure"
  }
}
```

`test` never discovers or reuses an existing service container. It starts a
uniquely named single-device service container with host network and host IPC,
then runs each ROS CLI check in an ephemeral client container using the same
image, DDS environment, project mount, and `ROS2CLI_NO_DAEMON=1`. The service log
is collected before cleanup. The host-side `deployment_file` must already be
materialized by the deployment or board-test-kit flow; the manager command uses
its corresponding container path.
