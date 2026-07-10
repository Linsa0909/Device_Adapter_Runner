#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path


def stage(name, status, exit_code=None):
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def write_failure(stage_name, command, exit_code, message, **extra):
    out = Path("ops/artifacts/last_failure.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"stage": stage_name, "command": command, "exit_code": exit_code, "message": message}
    payload.update(extra)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(context_id):
    path = Path(f"ops/contexts/{context_id}.manifest.json")
    if not path.exists():
        raise FileNotFoundError(path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def executable_sources(source_files):
    result = []
    for file_name in source_files:
        path = Path(file_name)
        if path.suffix.lower() not in {".c", ".cc", ".cpp", ".cxx"} or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "int main(" in text or "int main (" in text:
            result.append(path.as_posix())
    return result


def write_if_missing(path, content, mode=None):
    p = Path(path)
    if p.exists():
        return False
    p.write_text(content, encoding="utf-8")
    if mode is not None:
        try:
            os.chmod(p, mode)
        except PermissionError:
            pass
    return True


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def runtime_requirements(spec):
    runtime = dict(spec.get("runtime_requirements") or {})
    legacy = spec.get("adapter_requirements") or {}
    mapping = {
        "apt_build": "apt_build",
        "apt_runtime": "apt_runtime",
        "sdk_headers": "vendor_headers",
        "sdk_libraries": "vendor_libraries",
        "subprocesses": "subprocesses",
    }
    for old, new in mapping.items():
        if new not in runtime and old in legacy:
            runtime[new] = legacy[old]
    if "apt_build" not in runtime and legacy.get("apt_packages"):
        runtime["apt_build"] = legacy["apt_packages"]
    if "apt_runtime" not in runtime and legacy.get("apt_packages"):
        runtime["apt_runtime"] = legacy["apt_packages"]
    return runtime


def apt_lines(packages):
    return "".join(f"    {pkg} \\\n" for pkg in as_list(packages) if str(pkg).strip())


def declared_device_paths(manifest, runtime):
    values = []
    for item in as_list(runtime.get("device_nodes")):
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            for key in ("path", "device", "mount"):
                if item.get(key):
                    values.append(str(item[key]))
                    break
    values.extend((manifest.get("remote") or {}).get("device_paths") or [])
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v.startswith("/dev/")))


def render_ros2_runtime(context_id, manifest):
    build = manifest.get("build") or {}
    spec = {}
    spec_path = Path(f"ops/contexts/{context_id}.device_spec.json")
    if spec_path.exists():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    runtime = runtime_requirements(spec)
    apt_build_lines = apt_lines(runtime.get("apt_build"))
    apt_runtime_lines = apt_lines(runtime.get("apt_runtime"))
    docker_image = (manifest.get("docker") or {}).get("image") or context_id.replace("_", "-")
    ros_distro = build.get("ros_distro") or "humble"
    ros_launch = (manifest.get("run") or {}).get("ros_launch") or "hardware_abstraction_layer manager_node.launch.py"
    required_executables = build.get("required_ros_executables") or []
    required_elf_executables = build.get("required_elf_executables") or []
    device_paths = declared_device_paths(manifest, runtime)
    device_check_values = " ".join(device_paths) if device_paths else '""'
    required_check = ""
    if required_executables:
        checks = []
        for item in required_executables:
            package, executable = item.split("/", 1)
            checks.append(f"ros2 pkg executables {package} | grep -q ' {executable}$'")
        required_check = " && ".join(checks)
        required_check = f"RUN . /opt/ros/{ros_distro}/setup.sh && . /opt/hal_ws/install/setup.sh && {required_check}\n"
    if required_elf_executables:
        checks = []
        for item in required_elf_executables:
            package, executable = item.split("/", 1)
            checks.append(
                "target=$(ros2 pkg prefix "
                f"{package})/lib/{package}/{executable} && test -x \"$target\" && file \"$target\" | grep -q ELF"
            )
        elf_check = " && ".join(checks)
        required_check += f"RUN . /opt/ros/{ros_distro}/setup.sh && . /opt/hal_ws/install/setup.sh && {elf_check}\n"

    delivery = manifest.get("delivery") or {}
    closed_loop = bool(delivery.get("closed_loop_package"))
    container_entrypoint = "ops/scripts/device_adapter_container_entrypoint.sh" if closed_loop else "run.sh"
    dockerfile = f"""# syntax=docker/dockerfile:1.6
# Generated by device-adapter. Edit only if the generated ROS2 runtime needs project-specific changes.
FROM --platform=$TARGETPLATFORM ros:{ros_distro}-ros-base-jammy AS build
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential cmake git pkg-config curl ca-certificates \\
    python3-pip python3-yaml python3-jinja2 python3-colcon-common-extensions python3-rosdep \\
    libcurl4-openssl-dev libyaml-cpp-dev nlohmann-json3-dev libspdlog-dev \\
{apt_build_lines}\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/hal_ws
COPY src ./src
RUN . /opt/ros/{ros_distro}/setup.sh \\
    && (rosdep update || true) \\
    && (rosdep install -i --from-path src --rosdistro {ros_distro} -y --skip-keys "libcurl-dev" || true) \\
    && colcon build --packages-up-to hardware_abstraction_layer --cmake-args -DCMAKE_BUILD_TYPE=Release
{required_check}FROM ros:{ros_distro}-ros-base-jammy AS runtime
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \\
    curl ca-certificates python3-yaml libcurl4 libyaml-cpp0.7 libspdlog1 \\
{apt_runtime_lines}\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/hal_ws
COPY --from=build /opt/hal_ws/install ./install
COPY src ./src
COPY {container_entrypoint} /opt/hal_ws/container_entrypoint.sh
RUN chmod +x /opt/hal_ws/container_entrypoint.sh
ENV ROS_DISTRO={ros_distro}
ENV LD_LIBRARY_PATH=/opt/hal_ws/install/hardware_abstraction_layer/lib:/opt/hal_ws/install/hardware_abstraction_layer/lib/hardware_abstraction_layer:$LD_LIBRARY_PATH
ENTRYPOINT ["/opt/hal_ws/container_entrypoint.sh"]
"""

    container_entrypoint_sh = f"""#!/usr/bin/env bash
# Generated by device-adapter.
set -euo pipefail

echo "[RUN_STAGE] ros_env start"
source "/opt/ros/${{ROS_DISTRO:-{ros_distro}}}/setup.bash"
source /opt/hal_ws/install/setup.bash

echo "[RUN_STAGE] device_check start"
for dev in {device_check_values}; do
  if [ -e "$dev" ]; then
    ls -l "$dev"
  fi
done

echo "[RUN_STAGE] hal_start start"
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec ros2 launch {ros_launch}
"""

    if closed_loop:
        run_sh = f"""#!/usr/bin/env bash
# Generated by device-adapter. Non-interactive host-side launcher for deploy/test automation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_DIR="${{PROJECT_DIR:-$SCRIPT_DIR}}"
IMAGE="${{IMAGE:-registry.ghostcloud.cn/integration/hal_dev:v1.0}}"
CONTAINER_NAME="${{CONTAINER_NAME:-hal_{context_id}_dev}}"
ROS_DISTRO="${{ROS_DISTRO:-{ros_distro}}}"
DEPLOYMENT_CONFIG="${{DEPLOYMENT_CONFIG:-}}"
SKIP_BUILD="${{SKIP_BUILD:-0}}"

log() {{ printf '[device-adapter][run] %s\\n' "$*"; }}
die() {{ printf '[device-adapter][run][ERROR] %s\\n' "$*" >&2; exit 1; }}

command -v docker >/dev/null 2>&1 || die "docker command not found"
test -f "$PROJECT_DIR/src/hardware_abstraction_layer/package.xml" || die "invalid HAL workspace: $PROJECT_DIR"

if docker ps -a --format '{{{{.Names}}}}' | grep -qx "$CONTAINER_NAME"; then
  log "removing old container: $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null
fi

log "starting container: $CONTAINER_NAME"
docker run -d \\
  --name "$CONTAINER_NAME" \\
  --network host \\
  --privileged \\
  -v /dev:/dev \\
  -v /sys:/sys:ro \\
  -v /run/udev:/run/udev:ro \\
  -v "$PROJECT_DIR:/workspace/yunshu" \\
  -e ROS_DISTRO="$ROS_DISTRO" \\
  "$IMAGE" \\
  tail -f /dev/null >/dev/null

if [[ "$SKIP_BUILD" != "1" ]]; then
  log "building HAL"
  docker exec "$CONTAINER_NAME" bash -lc '
    set -euo pipefail
    cd /workspace/yunshu
    source /opt/ros/${{ROS_DISTRO:-{ros_distro}}}/setup.bash
    colcon build --packages-up-to hardware_abstraction_layer --cmake-clean-cache
  '
else
  log "SKIP_BUILD=1, skip build"
fi

log "launching HAL"
if [[ -n "$DEPLOYMENT_CONFIG" ]]; then
  docker exec "$CONTAINER_NAME" bash -lc "
    set -euo pipefail
    cd /workspace/yunshu
    source /opt/ros/${{ROS_DISTRO:-{ros_distro}}}/setup.bash
    source install/setup.bash
    exec ros2 launch {ros_launch} deployment_config:=$DEPLOYMENT_CONFIG
  "
else
  docker exec "$CONTAINER_NAME" bash -lc '
    set -euo pipefail
    cd /workspace/yunshu
    source /opt/ros/${{ROS_DISTRO:-{ros_distro}}}/setup.bash
    source install/setup.bash
    exec ros2 launch {ros_launch}
  '
fi
"""
    else:
        run_sh = container_entrypoint_sh

    dockerignore = """.git
build
install
log
logs
dist
test_videos
**/__pycache__
*.pyc
ops/artifacts/*.tar
ops/artifacts/*.tar.gz
"""

    device_block = "\n".join([f"      - {dev}:{dev}" for dev in device_paths])
    compose = f"""services:
  {docker_image}:
    image: {docker_image}:latest
    build:
      context: .
      dockerfile: Dockerfile
    privileged: true
    network_mode: host
    devices:
{device_block if device_block else '      []'}
    environment:
      ROS_DOMAIN_ID: "${{ROS_DOMAIN_ID:-0}}"
    restart: "no"
"""
    extra_files = {}
    if closed_loop:
        extra_files["ops/scripts/device_adapter_container_entrypoint.sh"] = (container_entrypoint_sh, 0o755)
        extra_files["config.env"] = (
            f"""# Generated by device-adapter.
IMAGE=registry.ghostcloud.cn/integration/hal_dev:v1.0
CONTAINER_NAME=hal_{context_id}_dev
ROS_DISTRO={ros_distro}
SKIP_BUILD=0
DEPLOYMENT_CONFIG=
""",
            None,
        )
        extra_files["install.sh"] = (
            """#!/usr/bin/env bash
set -euo pipefail
command -v docker >/dev/null 2>&1 || { echo "docker command not found" >&2; exit 1; }
docker version >/dev/null
echo "[device-adapter][install] docker is available"
""",
            0o755,
        )
        extra_files["status.sh"] = (
            f"""#!/usr/bin/env bash
set -euo pipefail
source ./config.env 2>/dev/null || true
CONTAINER_NAME="${{CONTAINER_NAME:-hal_{context_id}_dev}}"
docker ps -a --filter "name=$CONTAINER_NAME"
docker logs --tail 120 "$CONTAINER_NAME" 2>/dev/null || true
if [[ -n "${{STATUS_PORT_PATTERN:-}}" ]]; then
  ss -lntp 2>/dev/null | grep -E "$STATUS_PORT_PATTERN" || true
else
  ss -lntp 2>/dev/null || true
fi
""",
            0o755,
        )
        extra_files["view.sh"] = (
            """#!/usr/bin/env bash
set -euo pipefail
BOARD_IP="${BOARD_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
echo "Set device-specific playback or API URLs in config.env when the adapter exposes a stream."
echo "BOARD_IP=${BOARD_IP:-unknown}"
""",
            0o755,
        )
        extra_files["DEPLOY.md"] = (
            f"""# {context_id} Deployment

Generated by device-adapter.

1. Review `config.env`.
2. Run `./install.sh`.
3. Run `./run.sh`.
4. Check `./status.sh`.

The launcher is non-interactive and must work over SSH automation. Device-specific
ports, media URLs, and health checks come from `ops/contexts/{context_id}.device_spec.json`.
""",
            None,
        )
    return dockerfile, run_sh, dockerignore, compose, extra_files


def update_package_files(manifest_path, manifest):
    includes = manifest.get("include") or []
    excludes = set(manifest.get("exclude") or [])
    package_files = []
    missing = []
    for item in includes:
        path = Path(item)
        if not path.exists():
            missing.append(item)
            continue
        if path.is_file() or path.is_symlink():
            package_files.append(path.as_posix())
            continue
        for child in sorted(path.rglob("*")):
            if (child.is_file() or child.is_symlink()) and not any(child.match(pattern) for pattern in excludes):
                package_files.append(child.as_posix())
    package_files = list(dict.fromkeys(package_files))
    generated_files = set((manifest.get("build") or {}).get("generated_runtime_files") or [])
    manifest.setdefault("generated", {})["missing_paths"] = [item for item in missing if item not in generated_files]
    manifest["generated"]["package_file_count"] = len(package_files)
    manifest.setdefault("docker", {})["dockerfile"] = "Dockerfile" if Path("Dockerfile").exists() else manifest.get("docker", {}).get("dockerfile")
    manifest["docker"]["compose_file"] = "docker-compose.yml" if Path("docker-compose.yml").exists() else manifest["docker"].get("compose_file")
    package_list = Path(manifest.get("package_files_file") or f"ops/artifacts/{manifest['context_id']}.package_files.txt")
    package_list.parent.mkdir(parents=True, exist_ok=True)
    package_list.write_text("\n".join(package_files) + ("\n" if package_files else ""), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    command = "generate_runtime_files.py " + args.context_id

    stage("stage2_context_validate", "start")
    try:
        manifest_path, manifest = load_manifest(args.context_id)
    except FileNotFoundError as exc:
        stage("stage2_context_validate", "fail", 2)
        write_failure("stage2_context_validate", command, 2, f"Manifest not found: {exc}", next_action=f"/device-adapter context {args.context_id}")
        return 2

    build = manifest.get("build") or {}
    spec = {}
    spec_path = Path(f"ops/contexts/{args.context_id}.device_spec.json")
    if spec_path.exists():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    runtime = runtime_requirements(spec)
    apt_build_lines = apt_lines(runtime.get("apt_build"))
    apt_runtime_lines = apt_lines(runtime.get("apt_runtime"))
    build_system = build.get("build_system") or "unknown"
    source_files = build.get("source_files") or []
    main_sources = executable_sources(source_files)
    binary_name = build.get("binary_name") or args.context_id.replace("-", "_")
    has_build_file = any(Path(name).exists() for name in ["CMakeLists.txt", "Makefile", "makefile"])
    if build_system == "ros2_colcon":
        if not Path("src").exists() or not list(Path(".").glob("src/*/package.xml")):
            stage("stage2_context_validate", "fail", 3)
            write_failure("stage2_context_validate", command, 3, "No ROS2 packages found under src/*/package.xml.", next_action="Run /device-adapter context from the ROS2 workspace root.")
            return 3
    elif not source_files and not has_build_file:
        stage("stage2_context_validate", "fail", 3)
        write_failure("stage2_context_validate", command, 3, "No C/C++ source files or build file found.", next_action="Run /device-adapter context after adding C++ source files or build instructions.")
        return 3
    if build_system != "ros2_colcon" and source_files and not main_sources and not has_build_file:
        stage("stage2_context_validate", "fail", 3)
        write_failure("stage2_context_validate", command, 3, "No C++ main() source or build file found.", next_action="Add build instructions or identify the executable source in context.")
        return 3
    stage("stage2_context_validate", "success")

    stage("stage3_runtime_generate", "start")
    created = []
    if build_system == "ros2_colcon":
        dockerfile, run_sh, dockerignore, compose, extra_files = render_ros2_runtime(args.context_id, manifest)
        if write_if_missing("run.sh", run_sh, 0o755):
            created.append("run.sh")
        if write_if_missing(".dockerignore", dockerignore):
            created.append(".dockerignore")
        if write_if_missing("Dockerfile", dockerfile):
            created.append("Dockerfile")
        if write_if_missing("docker-compose.yml", compose):
            created.append("docker-compose.yml")
        for path, item in extra_files.items():
            content, mode = item
            if write_if_missing(path, content, mode):
                created.append(path)
        update_package_files(manifest_path, manifest)
        print("created_files:")
        for item in created:
            print(f"- {item}")
        stage("stage3_runtime_generate", "success")
        return 0

    compile_sources = [p for p in source_files if Path(p).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}]
    source_list = "\n  ".join(compile_sources)
    if source_files and write_if_missing(
        "CMakeLists.txt",
        f"""cmake_minimum_required(VERSION 3.16)
project({binary_name} LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(PkgConfig QUIET)
find_package(OpenCV QUIET)

add_executable({binary_name}
  {source_list}
)

target_include_directories({binary_name} PRIVATE
  ${{CMAKE_SOURCE_DIR}}
  ${{CMAKE_SOURCE_DIR}}/include
  ${{CMAKE_SOURCE_DIR}}/inc
  ${{CMAKE_SOURCE_DIR}}/src
)

if(OpenCV_FOUND)
  target_link_libraries({binary_name} PRIVATE ${{OpenCV_LIBS}})
  target_include_directories({binary_name} PRIVATE ${{OpenCV_INCLUDE_DIRS}})
endif()

target_link_directories({binary_name} PRIVATE
  ${{CMAKE_SOURCE_DIR}}/lib
  ${{CMAKE_SOURCE_DIR}}/libs
  ${{CMAKE_SOURCE_DIR}}/sdk/lib
)

target_link_libraries({binary_name} PRIVATE pthread dl)
install(TARGETS {binary_name} RUNTIME DESTINATION bin)
""",
    ):
        created.append("CMakeLists.txt")

    device_paths = (manifest.get("remote") or {}).get("device_paths") or []
    device_env = device_paths[0] if device_paths else ""
    if write_if_missing(
        "run.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail

echo "[RUN_STAGE] env_check start"
if [ -n "${{DEVICE_PATH:-{device_env}}}" ] && [ ! -e "${{DEVICE_PATH:-{device_env}}}" ]; then
  echo "Device path not found: ${{DEVICE_PATH:-{device_env}}}" >&2
  exit 20
fi

export LD_LIBRARY_PATH="/opt/app/lib:/opt/app/libs:/opt/app/sdk/lib:${{LD_LIBRARY_PATH:-}}"
echo "[RUN_STAGE] app_start start"
exec /opt/app/bin/{binary_name} "$@"
""",
        0o755,
    ):
        created.append("run.sh")

    if write_if_missing(
        ".dockerignore",
        """.git
build
dist
logs
test_videos
**/__pycache__
*.pyc
ops/artifacts/*.tar
ops/artifacts/*.tar.gz
""",
    ):
        created.append(".dockerignore")

    if write_if_missing(
        "Dockerfile",
        f"""# syntax=docker/dockerfile:1.6
FROM --platform=$BUILDPLATFORM ubuntu:22.04 AS build
ARG TARGETPLATFORM
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential cmake pkg-config ca-certificates \\
{apt_build_lines}\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \\
    && cmake --build build -j"$(nproc)" \\
    && cmake --install build --prefix /opt/app

FROM ubuntu:22.04 AS runtime
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates \\
{apt_runtime_lines}\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/app
COPY --from=build /opt/app /opt/app
COPY --from=build /src /opt/app/source
COPY run.sh /opt/app/run.sh
RUN chmod +x /opt/app/run.sh
ENV LD_LIBRARY_PATH=/opt/app/lib:/opt/app/libs:/opt/app/sdk/lib:/opt/app/source/lib:/opt/app/source/libs:/opt/app/source/sdk/lib
ENTRYPOINT ["/opt/app/run.sh"]
""",
    ):
        created.append("Dockerfile")

    image = (manifest.get("docker") or {}).get("image") or args.context_id.replace("_", "-")
    device_block = "\n".join([f"      - {p}:{p}" for p in device_paths])
    if write_if_missing(
        "docker-compose.yml",
        f"""services:
  {image}:
    image: {image}:latest
    build:
      context: .
      dockerfile: Dockerfile
    privileged: true
    network_mode: host
    devices:
{device_block if device_block else '      []'}
    environment:
      DEVICE_PATH: "{device_env}"
    restart: "no"
""",
    ):
        created.append("docker-compose.yml")

    update_package_files(manifest_path, manifest)
    print("created_files:")
    for item in created:
        print(f"- {item}")
    stage("stage3_runtime_generate", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
