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
