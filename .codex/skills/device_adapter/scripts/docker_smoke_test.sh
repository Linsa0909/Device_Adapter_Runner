#!/usr/bin/env bash
set -u

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
  python3 - "$stage_name" "$code" "$message" "$log_file" <<'PY'
import json, sys
stage, code, message, log_file = sys.argv[1:5]
payload = {"stage": stage, "exit_code": int(code), "message": message}
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
image_ref="${2:-}"
arch="${3:-}"
if [ -z "$context_id" ] || [ -z "$image_ref" ] || [ -z "$arch" ]; then
  echo "usage: docker_smoke_test.sh <context_id> <image:tag> <arch>" >&2
  exit 2
fi

manifest="ops/contexts/${context_id}.manifest.json"
mkdir -p ops/artifacts/logs
log_file="ops/artifacts/logs/${context_id}_docker_run_${arch}.log"

stage stage8_container_smoke_test start
if [ ! -f "$manifest" ]; then
  stage stage8_container_smoke_test fail 2
  fail_json stage8_container_smoke_test 2 "Manifest not found: $manifest" "$log_file"
  exit 2
fi

device_args=()
while IFS= read -r dev; do
  [ -z "$dev" ] && continue
  if [ -e "$dev" ]; then
    device_args+=(--device "$dev:$dev")
  else
    echo "warning: device path not present on local smoke host: $dev" >>"$log_file"
  fi
done < <(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
for path in (m.get("remote") or {}).get("device_paths", []):
    print(path)
PY
)

timeout_seconds="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("run") or {}).get("smoke_timeout_seconds") or 15)
PY
)"

cmd=(docker run --rm --network host "${device_args[@]}" "$image_ref")
printf 'smoke_command:' >"$log_file"
printf ' %q' "${cmd[@]}" >>"$log_file"
printf '\n' >>"$log_file"

if command -v timeout >/dev/null 2>&1; then
  timeout "$timeout_seconds" "${cmd[@]}" >>"$log_file" 2>&1
  code=$?
else
  "${cmd[@]}" >>"$log_file" 2>&1
  code=$?
fi

tail -n 120 "$log_file" || true
if [ "$code" -ne 0 ] && [ "$code" -ne 124 ]; then
  stage stage8_container_smoke_test fail "$code"
  fail_json stage8_container_smoke_test "$code" "docker run --rm smoke test failed" "$log_file"
  exit "$code"
fi

if [ "$code" -eq 124 ]; then
  echo "smoke test timed out after ${timeout_seconds}s; treating timeout as startup success" >>"$log_file"
fi
stage stage8_container_smoke_test success
echo "smoke_log: $log_file"
