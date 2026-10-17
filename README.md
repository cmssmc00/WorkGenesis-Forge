# WorkGenesis Forge

A research pipeline for generating source-grounded workplace tasks, testing them with independent agents, diagnosing task and rubric defects, and collecting teacher trajectories.

It implements the disclosed stages of [WorkGenesis](https://arxiv.org/abs/2609.39325), with documented substitutions for the model, model transport, occupation playbooks, and execution environment. It is an independent implementation, not the authors' official code or an exact reproduction of their training and benchmark results.

**Current entry point: GPT-6 Astra with extra-high reasoning, through your own ChatGPT login in the official Codex CLI.** Start with one accepted task and one teacher trajectory. Task count is adjustable.

## Pipeline

```text
O*NET occupation planning
  → researcher: search, download, and inspect public source documents
  → companion_author: create and inspect optional supporting files
  → task_author: write the task request and evaluation rubric
  → solver: independently attempt the task
  → judge: score the submitted files criterion by criterion
  → attributor: distinguish Agent, Task, and Rubric failures
  → repairer: repair Task/Rubric defects and repeat verification when needed
  → accept task
  → teacher: collect a fresh independent solution trajectory
```

These are **eight separate agent roles**, using the same configured model in separate contexts. O*NET supplies occupation descriptions and work activities; SerpAPI discovers real public documents to ground a new task. GDPval tasks are not used as source documents in this workflow.

Acceptance means no Task/Rubric defects remain according to the audit. It does not require a perfect solver score. Failed construction attempts do not count toward the accepted-task target.

## Model, reasoning effort, and authentication

| Setting | Current Codex subscription profile |
| --- | --- |
| Provider | `codex_subscription` |
| Model identifier | `gpt-6-astra` |
| Reasoning effort | `xhigh` = extra high |
| Roles | All eight roles above |
| Authentication | Your own ChatGPT account, via `codex login` |
| API key / model fallback | Neither is used by this profile |
| Tool execution | Pinned Stirrup revision, with tools executed in local Docker containers |

**The model and effort are enforced in the adapter**, not just suggested defaults. Editing the JSON to another model or effort will fail validation. Supporting another combination requires an adapter change and compatibility testing. This is separate from authentication: no account, subscription token, or author's login is embedded in the repository. Every user authenticates locally with their own eligible account and model access.

The adapter requires Codex CLI **0.154.0 or newer**, its checked isolation/structured-output flags, and a bundled model entry supporting `gpt-6-astra`, `xhigh`, and images. A version number alone does not guarantee compatibility. Preflight reports missing capabilities. Each step invokes the CLI with serialized stage history; Stirrup validates proposed actions and executes the tools. This differs from native API message/tool replay. Logs record the requested model; CLI events do not independently attest the server-side model identity.

The repository retains Claude subscription, native Anthropic, and legacy OpenAI-compatible examples. They use their own adapters and setup; changing the current profile's `model` field does not switch providers. See [Codex details](docs/CODEX_SUBSCRIPTION.md), [Claude subscription](docs/CLAUDE_SUBSCRIPTION.md), and [native API setup](docs/PAPER_PIPELINE.md).

## Requirements

- macOS or Linux, Bash, Git, and **Python 3.12** for the paper profile.
- Docker Desktop on macOS, or a working Docker Engine on Linux, accessible from your terminal and the Python Docker SDK.
- Official Codex CLI and a ChatGPT account with access to the requested model.
- Your own [SerpAPI key](https://serpapi.com/manage-api-key) and available search quota.
- Network access to GitHub, the model service, SerpAPI, public documents, and container/package registries.

The model runs remotely; local GPU hardware and model weights are not required. The Docker image contains office, PDF, plotting, OCR, and document-rendering tools, so its first build needs substantial disk space and download time. macOS has been exercised locally; a fresh Linux installation has not been validated end to end.

## Install

### 1. Clone and install Python dependencies

```bash
git clone https://github.com/cmssmc00/WorkGenesis-Forge.git
cd WorkGenesis-Forge
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[paper]'
```

The `paper` extra installs [Stirrup at revision `247f24d56b2108235880ed2a2baea5d35b5a67ee`](https://github.com/ArtificialAnalysis/Stirrup/tree/247f24d56b2108235880ed2a2baea5d35b5a67ee). A failure cloning that dependency is a GitHub connectivity problem; installing only the base package does not install the paper runtime.

### 2. Install Codex and sign in

Install the official CLI using the [OpenAI installation guide](https://learn.chatgpt.com/docs/codex/cli). Its macOS/Linux installer is:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
codex login
codex login status
```

Choose ChatGPT authentication. If the browser flow is unavailable, the official alternative is `codex login --device-auth`; see the [CLI authentication reference](https://learn.chatgpt.com/docs/cli/reference#codex-login). Do not copy another user's Codex credentials into the repository or Docker image.

### 3. Download the Docker base image and build the task environment

**There is currently no published prebuilt WorkGenesis Forge image to pull.** The supported setup downloads an official Python base image, pins its registry digest, and builds this repository's environment on your computer:

```bash
docker pull python:3.12-slim-bookworm
WG_BASE_IMAGE="$(docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim-bookworm)"
bash scripts/build_paper_image.sh "$WG_BASE_IMAGE"
docker image inspect --format '{{.Id}} {{.Architecture}}' workgenesis-paper:local
```

Build once, then reuse it for subsequent tasks. The launcher resolves `workgenesis-paper:local` to the immutable image ID on **your** Docker daemon. A hash from another computer is not a downloadable image address. For an explicit override:

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
```

See [Docker setup](docker/README.md) for sharing a built image as an archive, loading it on another computer, architecture compatibility, and optional build proxies. The image contains tools, not the model or anyone's subscription login.

## Generate tasks

Run this in a normal shell terminal, from the repository directory:

```bash
bash scripts/run_codex_subscription.sh 1
```

The launcher checks the Python environment, Codex executable, Docker image, SerpAPI account, and pipeline configuration. If `SERPAPI_API_KEY` is absent, it prompts for the key with hidden input. The key is held in the process environment, not saved to a file. An existing exported key is also supported; `.env` files are not automatically loaded.

This command **starts live model and search use** and creates a new directory such as `runs/gpt6-astra-1-XXXXXXXX`. No other start command is needed.

To target 100 accepted tasks:

```bash
bash scripts/run_codex_subscription.sh 100
```

The argument overrides `planning.count`. The checked-in configurations are [one task](configs/gpt6_astra_subscription_one.json) and [100 tasks](configs/gpt6_astra_subscription.json). Each accepted task requests one fresh teacher trajectory. A small run does not cover all 65 occupation labels.

For a configuration/readiness check without generation:

```bash
.venv/bin/python -m wgforge preflight \
  --config configs/gpt6_astra_subscription_one.json --count 1
```

If your network requires a proxy, export your own `HTTP_PROXY` / `HTTPS_PROXY` first. For compatibility with the original local setup, the launcher also detects an HTTP proxy listening at `127.0.0.1:7890` when no HTTPS proxy is set. Docker image pulls/builds may require separate Docker proxy settings.

### Limits and usage accounting

The subscription presets intentionally retain very high local workload budgets: up to 1 billion cumulative tokens per role and 1 trillion per campaign. Review `roles.*.budget` and the campaign budget before increasing the task count. These settings do **not** increase subscription quotas or bypass service limits. Per-role step limits, timeouts, and source-search limits still apply.

`charged_upper_bound_usd` is an API-equivalent workload estimate for this transport, not a subscription invoice or guaranteed cost bound. Input usage includes cached input once. SerpAPI has separate quota; uncertain requests retain accounting reservations and are not automatically replayed.

### Outputs and restarting

Inspect `runs/<run-name>/campaign.json`. Completion requires `status: completed` and the requested `counts.accepted_tasks` and `counts.completed_trajectories`. Stage messages such as `task_author — Complete` are only one part of the pipeline. `6/256` counts that agent's steps, not completed tasks.

Each campaign directory preserves task packets, private rubrics, solver deliverables, audit/attribution results, repair history, teacher trajectories, provenance, and usage receipts. Keep private rubrics and author metadata separate from solver-facing packets.

**Running the launcher again starts a new campaign.** To reuse completed checkpoints, use the original output directory and the same configuration, image, and task count:

```bash
# Replace the example path with your existing campaign directory.
.venv/bin/python -m wgforge campaign \
  --config configs/gpt6_astra_subscription_one.json \
  --count 1 --out runs/YOUR_EXISTING_RUN --approve-live
```

An interrupted stage with model/search activity can require inspection before resuming; this command does not promise automatic recovery of unknown outcomes. Preserve failure receipts instead of deleting them to force a retry.

## Reproduction scope and validation

The paper-stage sequence, separate role contexts, source grounding, independent solve/score/attribution, targeted repair, and final teacher collection are implemented. Model choice, subscription transport, reconstructed playbooks, locally selected harness revision, and Docker environment differ from the original experiment. See the [reproduction contract](docs/REPRODUCTION_CONTRACT.md).

A local GPT-6 Astra / `xhigh` pilot completed **one accepted task and one teacher trajectory**. This establishes a completed pilot, not a 100-task reliability estimate, a trained student, or reproduced GDPval/other benchmark scores. Subscription bridge traces are labeled separately; the native-message training exporter does not treat them as lossless API training records. Local run artifacts and credentials are excluded from this source release.

Offline regression tests:

```bash
mkdir -p build/test-tmp
TMPDIR="$(pwd -P)/build/test-tmp" .venv/bin/python -m unittest discover -s tests
```

Use a physical temporary directory as above: the sandbox rejects symlink ancestry, including macOS's default `/var` temporary-path alias.

A separate deterministic legacy demo is available without model/search calls or Docker after installing the base package:

```bash
.venv/bin/python -m wgforge demo --out runs/offline-demo
```

## Repository contents

| Path | Purpose |
| --- | --- |
| `wgforge/` | Pipeline, provider adapters, validation, and bundled source metadata |
| `configs/` | Current and retained example profiles, occupation inventory |
| `scripts/` | Launchers, account check, image build, playbook construction |
| `docker/` | Reconstructed paper environment and legacy sandbox recipe |
| `tests/`, `qa/` | Regression tests and historical acceptance checks |
| `examples/` | Attributed O*NET and USGS source fixtures |
| `docs/`, `recipes/` | Implementation details, historical profiles, and training/evaluation scaffolding |

The release excludes `.venv`, `runs`, build/cache directories, local presentations, backups, login credentials, API keys, and Docker image archives. See [third-party notices](THIRD_PARTY_NOTICES.md) for provenance and component terms. No blanket software license has been assigned in this repository; upstream data and dependencies retain their own terms.
