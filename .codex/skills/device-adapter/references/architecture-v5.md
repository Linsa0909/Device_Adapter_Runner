# Device Adapter V5 Architecture

The workflow has four planes: workflow control, device knowledge, execution,
and assurance. `scripts/workflow_definition.json` is the authoritative stage
DAG and boundary contract.

Device knowledge is evidence-first. Natural-language and extracted document
facts become `normalized_context.json`; requested features map only to
capabilities present in the immutable HAL Adapter SDK; connections resolve
through one or more machine-readable Transport Profiles. Unknown or unmapped
required facts block generation.

`adapt --allow-code` prepares an `adapter_implementation_task.json`. A coding
Agent consumes the envelope, stays inside its allowlist, and may not modify the
independent tests. Completion requires an AArch64 target build, plugin ABI and
dependency validation, independent verification, C++ review, differential
review, and fingerprint-bound human approval.

The fixed current platform profile is `yunshu-aarch64-humble`: AArch64, ROS 2
Humble, Cyclone DDS, `/workspace/yunshu`, one remote runtime instance. Plugin
code must remain multi-instance capable; two-device hardware acceptance is
recorded as `NOT_RUN` until hardware is available.
