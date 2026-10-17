#!/usr/bin/env bash
# Start one NEW campaign from the terminal that holds the exported search key.
set -euo pipefail

if [[ -z "${SERPAPI_API_KEY:-}" ]]; then
  echo 'SERPAPI_API_KEY is missing. Export it in this terminal before running this script; do not paste it into chat or configuration files.' >&2
  exit 2
fi
if [[ $# -ne 0 ]]; then
  echo 'Usage: bash scripts/run_subscription_one.sh (starts one NEW one-task campaign)' >&2
  exit 2
fi

task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
task_python="$task_project_root/.venv/bin/python"
task_config="$task_project_root/configs/claude_opus55_subscription_one.json"
if [[ ! -x "$task_python" ]]; then
  echo 'The project .venv/bin/python is unavailable. Restore the existing project environment before running.' >&2
  exit 2
fi
cd -- "$task_project_root"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo 'Docker is unavailable or its daemon is not ready. Start Docker Desktop and wait for it to be ready.' >&2
  exit 2
fi
task_image_reference="${WGFORGE_PAPER_IMAGE:-workgenesis-paper:local}"
task_image_id=""
task_image_error=""
# Retry only the read-only image lookup, never a campaign or model request.
for task_image_attempt in 1 2 3; do
  if task_image_result="$(docker image inspect --format '{{.Id}}' "$task_image_reference" 2>&1)"; then
    task_image_id="$task_image_result"
    break
  fi
  task_image_error="$task_image_result"
  if [[ "$task_image_attempt" -lt 3 ]]; then
    sleep 1
  fi
done
if [[ -z "$task_image_id" ]]; then
  printf 'Docker could not resolve the local image %s after 3 read-only checks. No campaign was started.\n' "$task_image_reference" >&2
  printf '%s\n' "$task_image_error" >&2
  echo 'Check the active Docker connection and local images before deciding whether a rebuild is needed.' >&2
  exit 2
fi
if [[ ! "$task_image_id" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  echo 'Docker did not return an immutable image ID; no campaign was started.' >&2
  exit 2
fi
export WGFORGE_PAPER_IMAGE="$task_image_id"

if ! task_preflight_json="$("$task_python" -m wgforge preflight --config "$task_config" --count 1)"; then
  echo 'Preflight failed; no campaign was started.' >&2
  exit 2
fi
printf '%s\n' "$task_preflight_json"
if ! printf '%s\n' "$task_preflight_json" | "$task_python" -c '
import json, sys
try:
    report = json.load(sys.stdin)
    ready = isinstance(report, dict) and report.get("ready_for_operator_live_check") is True
except (ValueError, TypeError):
    ready = False
sys.exit(0 if ready else 2)
'; then
  echo 'Preflight is not ready. Resolve the listed setup requirements before starting a new campaign.' >&2
  exit 2
fi

mkdir -p "$task_project_root/runs"
task_run_dir="$(mktemp -d "$task_project_root/runs/opus55-subscription-one-XXXXXXXX")"
printf 'Starting one NEW campaign. Output directory: %s\n' "$task_run_dir"
echo 'This uses Claude subscription and SerpAPI quota. This script never retries a campaign automatically.'
echo 'Keep this directory if interrupted; rerunning this script creates a different campaign.'
exec "$task_python" -m wgforge campaign --config "$task_config" --count 1 --out "$task_run_dir" --approve-live
