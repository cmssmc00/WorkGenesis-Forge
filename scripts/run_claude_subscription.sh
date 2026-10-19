#!/usr/bin/env bash
# One serial author-only campaign using Claude subscription, Opus 5.5 / configured effort / fallback.
set -euo pipefail

task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
task_python="$task_project_root/.venv/bin/python"
task_config="$task_project_root/configs/claude_opus55_subscription_serial.json"
task_run_dir=''
task_count="${1:-200}"
if [[ "${1:-}" == '--resume' && $# -eq 2 ]]; then
  task_run_dir="$(cd -- "$2" && pwd -P)"
  task_config="$task_run_dir/config.json"
  task_count="$("$task_python" -c '
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); c=json.loads(p.read_text())
if not (p.parent / "campaign.json").is_file() or c.get("campaign_mode") != "author_only" or any(r.get("provider") != "claude_subscription" for r in c.get("roles", {}).values()):
    sys.exit("Resume requires an existing serial Claude author-only campaign.")
print(c["planning"]["count"])
' "$task_config")"
elif [[ $# -gt 1 ]]; then
  echo 'Usage: bash scripts/run_claude_subscription.sh [new-task-count; default 200] OR --resume RUN_DIRECTORY' >&2
  exit 2
fi
if [[ ! "$task_count" =~ ^[1-9][0-9]{0,5}$ ]] || (( task_count > 100000 )); then
  echo 'Task count must be an integer from 1 to 100000.' >&2
  exit 2
fi
# This entry point always calls one serial campaign, never the parallel runner.
unset WGFORGE_PARALLEL_MODE WGFORGE_PARALLEL_RESUME
export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"
cd -- "$task_project_root"
if [[ ! -x "$task_python" ]]; then
  echo "Create .venv with Python 3.12 and install the project: .venv/bin/python -m pip install -e '.[paper]' (see README.md)." >&2
  exit 2
fi
if ! command -v claude >/dev/null 2>&1; then
  echo 'Claude CLI is unavailable. Install official Claude Code and run claude auth login with your subscription.' >&2
  exit 2
fi

# Reuse the previously selected local HTTP proxy only when it is listening.
# No proxy settings or credentials are written to disk.
if [[ -z "${HTTPS_PROXY:-${https_proxy:-}}" ]]; then
  if "$task_python" -c 'import socket; socket.create_connection(("127.0.0.1", 7890), timeout=1).close()' >/dev/null 2>&1; then
    export HTTP_PROXY='http://127.0.0.1:7890'
    export HTTPS_PROXY="$HTTP_PROXY"
    export http_proxy="$HTTP_PROXY"
    export https_proxy="$HTTPS_PROXY"
    export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
    export no_proxy="$NO_PROXY"
    echo 'Using the available local HTTP proxy on port 7890.'
  fi
fi

# Use the same SDK and environment as Stirrup, not a separate CLI context.
task_docker_ready() {
  "$task_python" -c 'import docker; c = docker.from_env(timeout=2); c.ping(); c.close()' >/dev/null 2>&1
}
if ! task_docker_ready; then
  if [[ "$(uname -s)" != 'Darwin' ]]; then
    echo 'Docker is unavailable. Start your Docker daemon and check access from this terminal, then retry.' >&2
    exit 2
  fi
  echo 'Starting Docker Desktop; checking readiness up to 30 times.'
  open -a Docker
  for task_wait in {1..30}; do
    if task_docker_ready; then break; fi
    sleep 2
  done
fi
if ! task_docker_ready; then
  echo 'Docker is not ready yet. Wait for Docker Desktop, then run this script again.' >&2
  exit 2
fi
# Resolve the image built on THIS machine to an immutable ID before preflight.
# An explicit operator override is respected; no other image is substituted.
task_image_reference="${WGFORGE_PAPER_IMAGE:-workgenesis-paper:local}"
task_image_id=''
task_image_error=''
for task_image_attempt in 1 2 3; do
  if task_image_result="$("$task_python" - "$task_image_reference" 2>&1 <<'PY'
import sys
import docker
client = None
try:
    client = docker.from_env(timeout=10)
    print(client.images.get(sys.argv[1]).id)
except docker.errors.ImageNotFound:
    print('The configured image is absent from the Docker daemon used by Python: ' + sys.argv[1], file=sys.stderr)
    sys.exit(2)
except Exception as exc:
    print('Docker SDK image check failed (' + type(exc).__name__ + ').', file=sys.stderr)
    sys.exit(2)
finally:
    if client is not None:
        client.close()
PY
)"; then
    task_image_id="$task_image_result"
    break
  fi
  task_image_error="$task_image_result"
  if [[ "$task_image_attempt" -lt 3 ]]; then sleep 1; fi
done
if [[ ! "$task_image_id" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  printf 'Local image lookup failed; no generation started.\n%s\n' "$task_image_error" >&2
  echo 'Build or load the image first; see docker/README.md. Set WGFORGE_PAPER_IMAGE for a different local image.' >&2
  exit 2
fi
export WGFORGE_PAPER_IMAGE="$task_image_id"
printf 'Verified local Docker image: %s\n' "$task_image_id"

# Ask for credentials only after local environment checks have succeeded.
if [[ -z "${SERPAPI_API_KEY:-}" ]]; then
  if [[ ! -t 0 ]]; then
    echo 'Export SERPAPI_API_KEY in the launching terminal before running.' >&2
    exit 2
  fi
  printf 'Paste your SerpAPI key and press Enter (input is hidden): ' >&2
  IFS= read -r -s SERPAPI_API_KEY
  printf '\n' >&2
  export SERPAPI_API_KEY
fi
if [[ -z "${SERPAPI_API_KEY:-}" ]]; then
  echo 'SerpAPI key is empty; no campaign was started.' >&2
  exit 2
fi

"$task_python" "$task_project_root/scripts/check_serpapi.py" "$task_config"
task_preflight_json="$("$task_python" -m wgforge preflight --config "$task_config" --count "$task_count")"
printf '%s\n' "$task_preflight_json"
if ! printf '%s\n' "$task_preflight_json" | "$task_python" -c '
import json, sys
report = json.load(sys.stdin)
sys.exit(0 if report.get("ready_for_operator_live_check") is True else 2)
'; then
  echo 'Resolve the requirements above. If Claude subscription login is missing, run claude auth login, then retry.' >&2
  exit 2
fi

mkdir -p "$task_project_root/runs"
# Effort as configured for the roles (shown in the banner and the new run directory name).
task_effort="$("$task_python" -c '
import json, sys
efforts = {r.get("effort", "high") for r in json.load(open(sys.argv[1]))["roles"].values()}
print(efforts.pop() if len(efforts) == 1 else "mixed")
' "$task_config")"
if [[ -z "$task_run_dir" ]]; then
  task_run_dir="$(mktemp -d "$task_project_root/runs/opus55-${task_effort}-author-only-serial-${task_count}-XXXXXXXX")"
  "$task_python" -c '
import json, sys
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text())
cfg["planning"]["count"]=int(sys.argv[3])
Path(sys.argv[2]).write_text(json.dumps(cfg,indent=2)+"\n")
' "$task_config" "$task_run_dir/config.json" "$task_count"
  task_config="$task_run_dir/config.json"
  printf 'New output directory: %s\n' "$task_run_dir"
else
  printf 'Resuming existing campaign: %s\n' "$task_run_dir"
fi
printf 'One serial worker | Claude subscription | Opus 5.5 | %s effort | target %s authored tasks\n' "$task_effort" "$task_count"
echo 'Official fallback enabled: Opus 5 / Opus 4.8. Actual serving models are recorded.'
echo 'Discovery, supporting files, task/rubric only. No solving, evaluation, revision or teacher.'
echo 'Subscription limits still apply; no automatic campaign replay or API billing fallback.'
printf 'Resume: bash scripts/run_claude_subscription.sh --resume "%s"\n' "$task_run_dir"
exec "$task_python" -m wgforge campaign --config "$task_config" --count "$task_count" --out "$task_run_dir" --approve-live
