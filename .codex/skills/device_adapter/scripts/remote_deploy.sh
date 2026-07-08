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
  mkdir -p ops/artifacts
  python3 - "$stage_name" "$code" "$message" <<'PY'
import json, sys
stage, code, message = sys.argv[1:4]
with open("ops/artifacts/last_failure.json", "w", encoding="utf-8") as f:
    json.dump({"stage": stage, "exit_code": int(code), "message": message}, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

context_id="${1:-}"
shift || true
host=""
user=""
arch="arm64"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) shift; host="${1:-}" ;;
    --user) shift; user="${1:-}" ;;
    -arm) arch="arm64" ;;
    -x86) arch="amd64" ;;
    --arch) shift; arch="${1:-}" ;;
  esac
  shift || true
done

if [ -z "$context_id" ]; then
  echo "usage: remote_deploy.sh <context_id> --host <host> --user <user> [-arm|-x86]" >&2
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
package="$(python3 - "$manifest" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
print(m.get("package_artifact") or f"ops/artifacts/{m.get('context_id')}_package.tar.gz")
PY
)"
stage stage1_context_load success

if [ -z "$host" ] || [ -z "$user" ]; then
  stage stage5_transfer fail 3
  fail_json stage5_transfer 3 "Missing --host or --user"
  exit 3
fi
if [ ! -f "$package" ]; then
  stage stage5_transfer fail 4
  fail_json stage5_transfer 4 "Package not found: $package"
  exit 4
fi

target="${user}@${host}"
stage stage5_transfer start
if ! ssh "$target" "mkdir -p '$remote_dir'"; then
  code=$?
  stage stage5_transfer fail "$code"
  fail_json stage5_transfer "$code" "Failed to create remote directory: $remote_dir"
  exit "$code"
fi

if command -v rsync >/dev/null 2>&1; then
  rsync -av "$package" "$target:$remote_dir/" || transfer_code=$?
else
  scp "$package" "$target:$remote_dir/" || transfer_code=$?
fi
if [ "${transfer_code:-0}" -ne 0 ]; then
  stage stage5_transfer fail "$transfer_code"
  fail_json stage5_transfer "$transfer_code" "Transfer failed"
  exit "$transfer_code"
fi
stage stage5_transfer success

stage stage6_remote_prepare start
base_package="$(basename "$package")"
if ! ssh "$target" "cd '$remote_dir' && tar -xzf '$base_package'"; then
  code=$?
  stage stage6_remote_prepare fail "$code"
  fail_json stage6_remote_prepare "$code" "Remote package extract failed"
  exit "$code"
fi

case "$arch" in
  arm64|aarch64) suffix="arm64" ;;
  amd64|x86_64|x86) suffix="amd64" ;;
  *) suffix="$(echo "$arch" | tr '/:' '__')" ;;
esac
image_tar="ops/artifacts/${context_id}_${suffix}_image.tar"
if [ -f "$image_tar" ]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -av "$image_tar" "$target:$remote_dir/"
  else
    scp "$image_tar" "$target:$remote_dir/"
  fi
  ssh "$target" "cd '$remote_dir' && docker load -i '$(basename "$image_tar")'" || true
fi
stage stage6_remote_prepare success

echo "remote_host: $host"
echo "remote_user: $user"
echo "remote_dir: $remote_dir"
echo "artifact: $package"
