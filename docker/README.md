# Docker environment

The current `paper_v1` pipeline uses `Dockerfile.paper`. Codex and the Python orchestrator run on the host; Stirrup runs task tools in Docker. No model weights, ChatGPT credentials, or SerpAPI key are baked into the image.

## Download and build on your computer

No prebuilt image has been published to Docker Hub or GHCR. Download the official base image and build the supplied recipe from the repository root:

```bash
docker pull python:3.12-slim-bookworm
WG_BASE_IMAGE="$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim-bookworm)"
bash scripts/build_paper_image.sh "$WG_BASE_IMAGE"
docker image inspect --format '{{.Id}} {{.Architecture}}' workgenesis-paper:local
```

The build helper requires a registry digest (`name@sha256:...`), not a mutable base tag. The built image is tagged `workgenesis-paper:local`; the launcher resolves that tag to a local immutable ID. You can also select it explicitly:

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
bash scripts/run_codex_subscription.sh 1
```

Docker must be running. On macOS the launcher can open Docker Desktop; on Linux start Docker yourself and ensure your user can access its daemon. The Python Docker SDK and Docker CLI must refer to the same daemon. If they do not, configure the SDK connection, for example with your own `DOCKER_HOST`, before running.

The image installs Python 3.12, LibreOffice, Poppler, OCR, Chromium, LaTeX, and Python document/data libraries. The first build is large and network-dependent. Later tasks reuse the image. The base digest and Python package versions are pinned; apt packages are resolved at build time, so this is not a byte-for-byte reconstruction of the unpublished paper image.

## Transfer an already-built image

A maintainer can provide an image archive separately from the source repository:

```bash
# On the computer with the image:
docker image save -o workgenesis-paper.tar workgenesis-paper:local
```

After receiving that archive through your chosen file-transfer channel:

```bash
# On the receiving computer:
docker image load -i workgenesis-paper.tar
docker image inspect --format '{{.Id}} {{.Architecture}}' workgenesis-paper:local
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
bash scripts/run_codex_subscription.sh 1
```

[`docker image load`](https://docs.docker.com/reference/cli/docker/image/load/) imports the image and restores its saved tags. This repository does not include an archive download. A local `sha256:...` ID alone cannot be downloaded on another computer: use the archive or rebuild. Use an image matching the target architecture; the original local image is `arm64`, while many Linux PCs are `amd64`. Building on the destination machine avoids relying on cross-architecture emulation. No multi-platform image has been validated here.

## Optional download routing

Docker registry pulls use Docker's own network settings. For downloads during the build, the helper accepts:

```bash
# Example only: use an HTTP proxy reachable FROM the Docker build environment.
export WGFORGE_BUILD_PROXY="http://host.docker.internal:7890"
```

`host.docker.internal` is commonly provided by Docker Desktop; do not assume it exists on Linux. A host's `127.0.0.1` is not the build container's host address. Optional `WGFORGE_DEBIAN_MIRROR` and `WGFORGE_PIP_INDEX_URL` must pass the build helper's URL validation. Leave them unset to use the configured defaults. These settings do not change package signature checks.

## Legacy sandbox

`docker/Dockerfile` belongs to the older bounded-runner path. It is separate from the current Stirrup paper environment; do not substitute it for `Dockerfile.paper`. See the retained runtime documentation for that profile.
