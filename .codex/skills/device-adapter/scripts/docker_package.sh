#!/usr/bin/env bash
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stage() {
  local name="$1"
  local status="$2"
  local code="${3:-}"
  if [ -n "$code" ]; then
    printf '[AGENT_STAGE] stage=%s status=%s exit_code=%s\n' "$name" "$status" "$code"
  else
    printf '[AGENT_STAGE] stage=%s status=%s\n' "$name" "$status"
  fi
}

fail_json() {
  local stage_name="$1"
  local code="$2"
  local message="$3"
  local log_file="${4:-}"
  mkdir -p ops/artifacts
  python3 - "$stage_name" "$code" "$message" "$log_file" "$*" <<'PY'
import json, sys
stage, code, message, log_file, command = sys.argv[1:6]
payload = {"stage": stage, "command": command, "exit_code": int(code), "message": message}
if log_file:
    payload["log_file"] = log_file
    try:
        lines = open(log_file, encoding="utf-8", errors="replace").read().splitlines()
        payload["tail"] = "\n".join(lines[-120:])
    except OSError:
        pass
with open("ops/artifacts/last_failure.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

context_id="${1:-}"
shift || true
if [ -z "$context_id" ]; then
  echo "usage: docker_package.sh <context_id> [-arm|-x86|--arch arm64|--arch amd64]" >&2
  exit 2
fi

arch=""
build_all=0
smoke_mode="auto"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -arm) arch="arm64" ;;
    -x86) arch="amd64" ;;
    -all|--all) build_all=1 ;;
    --smoke) smoke_mode="force" ;;
    --no-smoke) smoke_mode="off" ;;
    --arch) shift; arch="${1:-}" ;;
  esac
  shift || true
done

manifest="ops/contexts/${context_id}.manifest.json"

stage stage1_context_load start
if [ ! -f "$manifest" ]; then
  stage stage1_context_load fail 2
  fail_json stage1_context_load 2 "Manifest not found: $manifest"
  exit 2
fi
stage stage1_context_load success

if [ "$build_all" -eq 1 ]; then
  bash "$0" "$context_id" -x86 || exit $?
  bash "$0" "$context_id" -arm || exit $?
  exit 0
fi

stage stage0_env_check start
if ! command -v docker >/dev/null 2>&1; then
  stage stage0_env_check fail 3
  fail_json stage0_env_check 3 "docker command not found"
  exit 3
fi
if ! docker info >/dev/null 2>&1; then
  stage stage0_env_check fail 3
  fail_json stage0_env_check 3 "Docker daemon is unavailable. Start Docker Engine or enable Docker Desktop WSL integration, then verify with: docker info"
  exit 3
fi
if ! docker buildx version >/dev/null 2>&1; then
  stage stage0_env_check fail 3
  fail_json stage0_env_check 3 "Docker buildx is required for architecture-targeted image builds. Install/enable the Docker buildx plugin, then verify with: docker buildx version"
  exit 3
fi
stage stage0_env_check success

dockerfile="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("docker") or {}).get("dockerfile") or "Dockerfile")
PY
)"
if [ ! -f "$dockerfile" ]; then
  stage stage4_docker_package fail 4
  fail_json stage4_docker_package 4 "Dockerfile not materialized before approval: $dockerfile. Rerun /device-adapter adapt $context_id, review, and approve."
  exit 4
fi

if [ -z "$arch" ]; then
  arch="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("docker") or {}).get("default_arch") or "arm64")
PY
)"
fi

case "$arch" in
  arm64|aarch64) platform="linux/arm64"; suffix="arm64" ;;
  amd64|x86_64|x86) platform="linux/amd64"; suffix="amd64" ;;
  *) platform="$arch"; suffix="$(echo "$arch" | tr '/:' '__')" ;;
esac

python3 "$script_dir/verify_native_deps.py" "$context_id" --arch "$suffix" --strict || exit $?

image="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
d=m.get("docker") or {}
print(d.get("image") or m.get("context_id", "device-adapter").replace("_", "-"))
PY
)"
tag="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("docker") or {}).get("tag") or "latest")
PY
)"
saved="ops/artifacts/${context_id}_${suffix}_image.tar"
mkdir -p ops/artifacts
mkdir -p ops/artifacts/logs
build_log="ops/artifacts/logs/${context_id}_docker_build_${suffix}.log"

build_cmd=(docker buildx build --platform "$platform" --load -t "${image}:${tag}" -f "$dockerfile" .)

printf 'build_command:'
printf ' %q' "${build_cmd[@]}"
printf '\n'

stage stage7_docker_build start
printf 'build_command:' >"$build_log"
printf ' %q' "${build_cmd[@]}" >>"$build_log"
printf '\n' >>"$build_log"
set +e
"${build_cmd[@]}" >>"$build_log" 2>&1
code=$?
set +e
if [ "$code" -ne 0 ]; then
  tail -n 120 "$build_log" || true
  stage stage7_docker_build fail "$code"
  fail_json stage7_docker_build "$code" "Docker build failed" "$build_log"
  exit "$code"
fi
stage stage7_docker_build success

stage stage8_image_inspect start
inspect_path="ops/artifacts/${context_id}_${suffix}_image_inspect.json"
docker image inspect "${image}:${tag}" >"$inspect_path"
code=$?
if [ "$code" -ne 0 ]; then
  stage stage8_image_inspect fail "$code"
  fail_json stage8_image_inspect "$code" "Docker image inspect failed" "$inspect_path"
  exit "$code"
fi
image_arch="$(python3 - "$inspect_path" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))[0]
print(d.get("Architecture", ""))
PY
)"
if [ "$suffix" = "arm64" ] && [ "$image_arch" != "arm64" ]; then
  stage stage8_image_inspect fail 8
  fail_json stage8_image_inspect 8 "Built image architecture mismatch: expected arm64 got $image_arch" "$inspect_path"
  exit 8
fi
if [ "$suffix" = "amd64" ] && [ "$image_arch" != "amd64" ]; then
  stage stage8_image_inspect fail 8
  fail_json stage8_image_inspect 8 "Built image architecture mismatch: expected amd64 got $image_arch" "$inspect_path"
  exit 8
fi
stage stage8_image_inspect success

host_arch="$(docker info --format '{{.Architecture}}' 2>/dev/null || uname -m)"
case "$host_arch" in
  x86_64) host_arch="amd64" ;;
  aarch64) host_arch="arm64" ;;
esac
missing_devices="$(python3 - "$manifest" <<'PY'
import json, os, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
missing = [p for p in (m.get("remote") or {}).get("device_paths", []) if not os.path.exists(p)]
print("\n".join(missing))
PY
)"
if [ "$smoke_mode" = "off" ]; then
  stage stage8_container_smoke_test start
  echo "smoke_skipped: disabled_by_option"
  stage stage8_container_smoke_test success
elif [ "$smoke_mode" = "force" ] || [ "$host_arch" = "$suffix" ]; then
  if [ "$smoke_mode" = "auto" ] && [ -n "$missing_devices" ]; then
    stage stage8_container_smoke_test start
    skip_report="ops/artifacts/${context_id}_${suffix}_smoke_skipped.json"
    python3 - "$skip_report" "$host_arch" "$suffix" "$missing_devices" <<'PY'
import json, sys
path, host_arch, image_arch, missing = sys.argv[1:5]
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "status": "skipped",
        "reason": "required_device_missing_on_local_host",
        "host_arch": host_arch,
        "image_arch": image_arch,
        "missing_devices": missing.splitlines(),
        "next_action": "Use /device-adapter deploy and /device-adapter test on the hardware target, or pass --smoke to force local docker run."
    }, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
    echo "smoke_skipped: required_device_missing_on_local_host"
    echo "smoke_skip_report: $skip_report"
    stage stage8_container_smoke_test success
  else
    bash "$script_dir/docker_smoke_test.sh" "$context_id" "${image}:${tag}" "$suffix" || exit $?
  fi
else
  stage stage8_container_smoke_test start
  skip_report="ops/artifacts/${context_id}_${suffix}_smoke_skipped.json"
  python3 - "$skip_report" "$host_arch" "$suffix" <<'PY'
import json, sys
path, host_arch, image_arch = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "status": "skipped",
        "reason": "cross_arch_local_run_disabled",
        "host_arch": host_arch,
        "image_arch": image_arch,
        "next_action": "Use /device-adapter deploy and /device-adapter test on a matching remote target, or pass --smoke if QEMU/binfmt is configured locally."
    }, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "smoke_skipped: cross_arch host=${host_arch} image=${suffix}"
  echo "smoke_skip_report: $skip_report"
  stage stage8_container_smoke_test success
fi

stage stage9_image_save start
docker save "${image}:${tag}" -o "$saved"
code=$?
if [ "$code" -ne 0 ]; then
  stage stage9_image_save fail "$code"
  fail_json stage9_image_save "$code" "Docker save failed"
  exit "$code"
fi
if [ ! -s "$saved" ]; then
  stage stage9_image_save fail 9
  fail_json stage9_image_save 9 "Saved image tar is empty: $saved"
  exit 9
fi

echo "image: ${image}:${tag}"
echo "platform: $platform"
echo "saved_image: $saved"
stage stage9_image_save success
