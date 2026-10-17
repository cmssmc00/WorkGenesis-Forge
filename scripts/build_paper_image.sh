#!/usr/bin/env bash
set -euo pipefail
task_base_image="${1:-}"
if [[ ! "$task_base_image" =~ @sha256:[a-f0-9]{64}$ ]]; then
  echo 'Usage: bash scripts/build_paper_image.sh python:3.12-slim@sha256:<verified digest>' >&2
  exit 2
fi
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
task_build_args=(--build-arg "BASE_IMAGE=$task_base_image")
task_mirror="${WGFORGE_DEBIAN_MIRROR:-}"
if [[ -n "$task_mirror" ]]; then
  task_mirror_pattern='^https?://([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'
  if [[ ! "$task_mirror" =~ $task_mirror_pattern ]]; then
    echo 'WGFORGE_DEBIAN_MIRROR must be an HTTP(S) origin without credentials, path, query or fragment.' >&2
    exit 2
  fi
  if [[ "$task_mirror" =~ :([0-9]{1,5})$ ]]; then
    task_mirror_port="${BASH_REMATCH[1]}"
    if (( 10#$task_mirror_port < 1 || 10#$task_mirror_port > 65535 )); then
      echo 'WGFORGE_DEBIAN_MIRROR has an invalid port.' >&2
      exit 2
    fi
  fi
  task_build_args+=(--build-arg "DEBIAN_MIRROR=$task_mirror")
fi
task_pip_index="${WGFORGE_PIP_INDEX_URL:-}"
if [[ -n "$task_pip_index" ]]; then
  task_pip_pattern='^https?://([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]([A-Za-z0-9-]*[A-Za-z0-9])?(:[0-9]{1,5})?/(pypi/)?simple/?$'
  if [[ ! "$task_pip_index" =~ $task_pip_pattern ]]; then
    echo 'WGFORGE_PIP_INDEX_URL requires a public HTTP(S) DNS host and /simple or /pypi/simple, without credentials, query or fragment.' >&2
    exit 2
  fi
  task_pip_authority="${task_pip_index#*://}"
  task_pip_authority="${task_pip_authority%%/*}"
  task_pip_host="${task_pip_authority%%:*}"
  if [[ "$task_pip_host" =~ \.[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt]$|\.[Ll][Oo][Cc][Aa][Ll]$|\.[Ii][Nn][Tt][Ee][Rr][Nn][Aa][Ll]$|\.[Tt][Ee][Ss][Tt]$|\.[Ii][Nn][Vv][Aa][Ll][Ii][Dd]$ ]]; then
    echo 'WGFORGE_PIP_INDEX_URL must use a public package-index host.' >&2
    exit 2
  fi
  if [[ "$task_pip_authority" =~ :([0-9]{1,5})$ ]]; then
    task_pip_port="${BASH_REMATCH[1]}"
    if (( 10#$task_pip_port < 1 || 10#$task_pip_port > 65535 )); then
      echo 'WGFORGE_PIP_INDEX_URL has an invalid port.' >&2
      exit 2
    fi
  fi
  task_build_args+=(--build-arg "PIP_INDEX_URL=$task_pip_index")
fi
task_build_proxy="${WGFORGE_BUILD_PROXY:-}"
if [[ -n "$task_build_proxy" ]]; then
  if [[ ! "$task_build_proxy" =~ ^https?://[^[:space:]]+$ ]]; then
    echo 'WGFORGE_BUILD_PROXY must be an HTTP(S) proxy URL without whitespace.' >&2
    exit 2
  fi
  # Docker reads these predefined build arguments from the command environment.
  # Proxy values are not placed in argv, Dockerfile ENV, or this script's output.
  task_build_args+=(--build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg http_proxy --build-arg https_proxy)
  HTTP_PROXY="$task_build_proxy" HTTPS_PROXY="$task_build_proxy" \
    http_proxy="$task_build_proxy" https_proxy="$task_build_proxy" \
    docker build "${task_build_args[@]}" \
      -f "$task_project_root/docker/Dockerfile.paper" -t workgenesis-paper:local "$task_project_root"
else
  docker build "${task_build_args[@]}" \
    -f "$task_project_root/docker/Dockerfile.paper" -t workgenesis-paper:local "$task_project_root"
fi
echo 'Image built. Before a live run, set WGFORGE_PAPER_IMAGE to its immutable image ID:'
docker image inspect --format '{{.Id}}' workgenesis-paper:local
