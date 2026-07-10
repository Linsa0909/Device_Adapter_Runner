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
with open("ops/artifacts/last_failure.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

context_id="${1:-}"
shift || true
host=""
user=""
timeout_seconds=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) shift; host="${1:-}" ;;
    --user) shift; user="${1:-}" ;;
    --timeout) shift; timeout_seconds="${1:-}" ;;
  esac
  shift || true
done

if [ -z "$context_id" ]; then
  echo "usage: remote_test.sh <context_id> --host <host> --user <user> [--timeout 60]" >&2
  exit 2
fi

manifest="ops/contexts/${context_id}.manifest.json"
stage stage1_context_load start
if [ ! -f "$manifest" ]; then
  stage stage1_context_load fail 2
  fail_json stage1_context_load 2 "Manifest not found: $manifest"
  exit 2
fi
if [ -z "$user" ]; then
  user="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("remote") or {}).get("default_user") or "")
PY
)"
fi
if [ -z "$host" ]; then
  host="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("remote") or {}).get("default_host") or "")
PY
)"
fi
remote_dir="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("remote") or {}).get("remote_dir") or ("/opt/device_adapter/" + m.get("context_id", "device")))
PY
)"
entrypoint="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("run") or {}).get("entrypoint") or "run.sh")
PY
)"
if [ -z "$timeout_seconds" ]; then
  timeout_seconds="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print((m.get("run") or {}).get("test_timeout_seconds") or 60)
PY
)"
fi
stage stage1_context_load success

if [ -z "$host" ] || [ -z "$user" ]; then
  stage stage8_remote_test fail 3
  fail_json stage8_remote_test 3 "Missing --host or --user"
  exit 3
fi

mkdir -p ops/artifacts/logs
log_file="ops/artifacts/logs/${context_id}_remote_test_$(date +%Y%m%d_%H%M%S).log"
target="${user}@${host}"

stage stage8_remote_test start
remote_script="
set -u
cd '$remote_dir'
echo '[REMOTE_STAGE] device_check start'
python3 - '$manifest' <<'PY'
import json, sys, subprocess
m=json.load(open(sys.argv[1], encoding='utf-8'))
for path in (m.get('remote') or {}).get('device_paths', []):
    subprocess.run(['ls', '-l', path], check=False)
PY
echo '[REMOTE_STAGE] docker_state start'
docker ps -a || true
echo '[REMOTE_STAGE] process_run start'
if [ -f docker-compose.yml ]; then
  docker compose up --build --abort-on-container-exit
elif [ -f '$entrypoint' ]; then
  chmod +x '$entrypoint'
  timeout '$timeout_seconds' ./'$entrypoint'
else
  echo 'No docker-compose.yml or entrypoint found: $entrypoint' >&2
  exit 42
fi
echo '[REMOTE_STAGE] healthcheck start'
python3 - '$context_id' <<'PY'
import json, subprocess, sys
from pathlib import Path

context_id = sys.argv[1]
spec_path = Path('ops/contexts') / f'{context_id}.device_spec.json'
if not spec_path.exists():
    print(f'healthcheck_skipped: missing {spec_path}')
    raise SystemExit(0)
spec = json.loads(spec_path.read_text(encoding='utf-8'))
runtime = spec.get('runtime_requirements') or spec.get('adapter_requirements') or {}
checks = runtime.get('healthchecks') or []
failed = []
for idx, check in enumerate(checks, 1):
    if isinstance(check, str):
        command = check
        expect = ''
    elif isinstance(check, dict):
        command = check.get('command') or check.get('cmd') or ''
        expect = check.get('expect_contains') or check.get('success_contains') or ''
    else:
        continue
    if not command:
        continue
    print(f'[HEALTHCHECK] {idx}: {command}')
    proc = subprocess.run(command, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout or ''
    print(output[-4000:])
    if proc.returncode != 0:
        failed.append({'index': idx, 'command': command, 'exit_code': proc.returncode})
    elif expect and expect not in output:
        failed.append({'index': idx, 'command': command, 'missing': expect})
if failed:
    print(json.dumps({'healthcheck_failed': failed}, ensure_ascii=False, indent=2))
    raise SystemExit(51)
print('healthcheck_success')
PY
"
ssh "$target" "bash -lc $(printf '%q' "$remote_script")" >"$log_file" 2>&1
code=$?
tail -n 80 "$log_file" || true
if [ "$code" -ne 0 ]; then
  stage stage8_remote_test fail "$code"
  fail_json stage8_remote_test "$code" "Remote test failed" "$log_file"
  exit "$code"
fi
stage stage8_remote_test success

stage stage9_collect_logs start
ssh "$target" "cd '$remote_dir' && docker ps -a && docker logs \$(docker ps -aq | head -n 1) --tail 120 2>/dev/null || true && dmesg | tail -n 80 || true" >>"$log_file" 2>&1 || true
stage stage9_collect_logs success
echo "log_file: $log_file"
