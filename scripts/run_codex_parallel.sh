#!/usr/bin/env bash
# Author-only target, partitioned across three workers; default 200 tasks in total.
set -euo pipefail
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export WGFORGE_PARALLEL_MODE=1
unset WGFORGE_PARALLEL_RESUME
if [[ "${1:-}" == '--resume' && $# -eq 2 ]]; then
  export WGFORGE_PARALLEL_RESUME="$(cd -- "$2" && pwd -P)"
  task_count="$(cd -- "$task_project_root" && "$task_project_root/.venv/bin/python" -m wgforge.parallel count --out "$WGFORGE_PARALLEL_RESUME" --author-only)"
elif [[ $# -le 1 ]]; then
  task_count="${1:-200}"
else
  echo 'Usage: bash scripts/run_codex_parallel.sh [total-count; default 200] OR --resume BATCH_DIRECTORY' >&2
  exit 2
fi
exec bash "$task_project_root/scripts/run_codex_subscription.sh" "$task_count"
