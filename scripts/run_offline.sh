#!/usr/bin/env bash
# Offline model-free trial. Dependency installation may use the network.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ and rerun this script." >&2
  echo "This script does not install Python, Homebrew, Docker, or model software." >&2
  exit 2
fi
MARKER="$ROOT/.venv/.workgenesis-forge-owner"
if [[ -d "$ROOT/.venv" ]]; then
  if [[ ! -f "$MARKER" ]] || [[ "$(cat "$MARKER")" != "$ROOT" ]]; then
    echo "An existing .venv is not owned by this launcher. Move it aside or use the manual README steps; no environment was changed." >&2
    exit 2
  fi
  if ! "$ROOT/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    echo "The launcher environment is missing or incompatible. Move .venv aside and rerun." >&2
    exit 2
  fi
else
  "$PYTHON" -m venv "$ROOT/.venv"
  printf '%s\n' "$ROOT" > "$MARKER"
fi
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
"$ROOT/.venv/bin/python" -m wgforge doctor
OUT="$ROOT/runs/offline-$(date -u +%Y%m%dT%H%M%SZ)-$$"
"$ROOT/.venv/bin/python" -m wgforge demo --out "$OUT"
printf '\nDone. Open these local files:\n  %s/control/workspace/output/briefing.pdf\n  %s/control/workspace/output/catalog_analysis.xlsx\n\nNo model API was called; these controls are not training data.\n' "$OUT" "$OUT"
