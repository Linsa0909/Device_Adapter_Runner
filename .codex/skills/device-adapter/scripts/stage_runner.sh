#!/usr/bin/env bash
set -u

usage() {
  cat >&2 <<'EOF'
usage: stage_runner.sh <action> <context_id> [options]

actions:
  context
  package
  model
  sdk-package
  sdk-check
  adapt
  plugin-build
  verify
  review
  approve
  full
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
  context)
    python3 "$base_dir/context_to_manifest.py" "$context_id" "$@"
    ;;
  package)
    python3 "$base_dir/stage_orchestrator.py" package "$context_id" "$@"
    ;;
  model)
    python3 "$base_dir/stage_orchestrator.py" model "$context_id" "$@"
    ;;
  sdk-package)
    python3 "$base_dir/stage_orchestrator.py" sdk-package "$context_id" "$@"
    ;;
  sdk-check)
    python3 "$base_dir/stage_orchestrator.py" sdk-check "$context_id" "$@"
    ;;
  adapt)
    python3 "$base_dir/stage_orchestrator.py" adapt "$context_id" "$@"
    ;;
  plugin-build)
    python3 "$base_dir/stage_orchestrator.py" plugin-build "$context_id" "$@"
    ;;
  verify)
    python3 "$base_dir/stage_orchestrator.py" verify "$context_id" "$@"
    ;;
  review)
    python3 "$base_dir/stage_orchestrator.py" review "$context_id" "$@"
    ;;
  approve)
    python3 "$base_dir/stage_orchestrator.py" approve "$context_id" "$@"
    ;;
  full)
    python3 "$base_dir/stage_orchestrator.py" full "$context_id" "$@"
    ;;
  docker-package)
    python3 "$base_dir/stage_orchestrator.py" docker-package "$context_id" "$@"
    ;;
  deploy)
    python3 "$base_dir/stage_orchestrator.py" deploy "$context_id" "$@"
    ;;
  test)
    python3 "$base_dir/stage_orchestrator.py" test "$context_id" "$@"
    ;;
  loop)
    python3 "$base_dir/stage_orchestrator.py" loop "$context_id" "$@"
    ;;
  logs)
    python3 "$base_dir/stage_orchestrator.py" logs "$context_id" "$@"
    ;;
  rerun)
    python3 "$base_dir/stage_orchestrator.py" rerun "$context_id" "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
