#!/usr/bin/env bash
set -u

usage() {
  cat >&2 <<'EOF'
usage: stage_runner.sh <action> <context_id> [options]

actions:
  package
  docker-package
  deploy
  test
  loop
  logs
  rerun
EOF
}

action="${1:-}"
context_id="${2:-}"
if [ -z "$action" ] || [ -z "$context_id" ]; then
  usage
  exit 2
fi
shift 2 || true

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$action" in
  package)
    python3 "$base_dir/generate_runtime_files.py" "$context_id" &&
    python3 "$base_dir/package_by_manifest.py" "$context_id" &&
    python3 "$base_dir/verify_package.py" "$context_id"
    ;;
  docker-package)
    python3 "$base_dir/generate_runtime_files.py" "$context_id" &&
    bash "$base_dir/docker_package.sh" "$context_id" "$@"
    ;;
  deploy)
    python3 "$base_dir/generate_runtime_files.py" "$context_id" &&
      python3 "$base_dir/package_by_manifest.py" "$context_id" &&
      python3 "$base_dir/verify_package.py" "$context_id" &&
      bash "$base_dir/remote_deploy.sh" "$context_id" "$@"
    ;;
  test)
    bash "$base_dir/remote_test.sh" "$context_id" "$@"
    ;;
  loop)
    python3 "$base_dir/generate_runtime_files.py" "$context_id" &&
      python3 "$base_dir/package_by_manifest.py" "$context_id" &&
      python3 "$base_dir/verify_package.py" "$context_id" &&
      bash "$base_dir/docker_package.sh" "$context_id" "$@" &&
      bash "$base_dir/remote_deploy.sh" "$context_id" "$@" &&
      bash "$base_dir/remote_test.sh" "$context_id" "$@"
    ;;
  logs)
    bash "$base_dir/remote_test.sh" "$context_id" "$@" --timeout 1
    ;;
  rerun)
    if [ ! -f ops/artifacts/last_failure.json ]; then
      echo "No ops/artifacts/last_failure.json found." >&2
      exit 2
    fi
    stage="$(python3 - <<'PY'
import json
print(json.load(open("ops/artifacts/last_failure.json", encoding="utf-8")).get("stage", ""))
PY
)"
    case "$stage" in
      stage4_package_create|stage5_package_verify|stage1_context_load|stage2_context_validate|stage3_runtime_generate)
        python3 "$base_dir/generate_runtime_files.py" "$context_id" &&
          python3 "$base_dir/package_by_manifest.py" "$context_id" &&
          python3 "$base_dir/verify_package.py" "$context_id"
        ;;
      stage6_native_deps_verify|stage7_docker_build|stage8_image_inspect|stage8_container_smoke_test|stage9_image_save)
        bash "$base_dir/docker_package.sh" "$context_id" "$@"
        ;;
      stage5_transfer|stage6_remote_prepare)
        bash "$base_dir/remote_deploy.sh" "$context_id" "$@"
        ;;
      stage7_remote_run|stage8_remote_test|stage9_collect_logs)
        bash "$base_dir/remote_test.sh" "$context_id" "$@"
        ;;
      *)
        echo "Unknown failed stage: $stage" >&2
        exit 3
        ;;
    esac
    ;;
  *)
    usage
    exit 2
    ;;
esac
