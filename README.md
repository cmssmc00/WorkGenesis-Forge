# WorkGenesis Forge

A research pipeline for generating source-grounded workplace tasks, testing them with independent agents, diagnosing task and rubric defects, and collecting teacher trajectories.

It implements the disclosed stages of [WorkGenesis](https://arxiv.org/abs/2609.39325), with documented substitutions for the model, model transport, occupation playbooks, and execution environment. It is an independent implementation, not the authors' official code or an exact reproduction of their training and benchmark results.

**Current author-only entry point: Claude Opus 5.5 with `max` effort and official CLI fallback, through your own Claude subscription.** One serial worker generates 200 new tasks by default. Existing GPT-6 Astra/Codex and full-pipeline entry points remain available below.

```bash
claude auth login
bash scripts/run_claude_subscription.sh
```

The launcher checks Docker and SerpAPI (prompts for the search key only if it is missing), then runs only `researcher → companion_author → task_author`. It does not solve, evaluate, revise tasks, or collect teacher trajectories. Saved tasks are `authored_unverified`. Set a different new-task target with `bash scripts/run_claude_subscription.sh 10`. Each new launch uses a fresh output directory and preserves existing runs. Resume a serial Claude campaign with `bash scripts/run_claude_subscription.sh --resume RUN_DIRECTORY`; this does not import or resume an old Codex parallel batch.

Configuration: [`configs/claude_opus55_subscription_serial.json`](configs/claude_opus55_subscription_serial.json). The primary model is `claude-opus-5-5`, effort is `max`, and allowed fallback models are Opus 5 and Opus 4.8. Both official availability fallback and automatic flagged-request fallback are enabled; actual observed models and per-model usage are saved. Subscription quota, authentication, and network failures are not bypassed by fallback. See [Claude subscription setup](docs/CLAUDE_SUBSCRIPTION.md).

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

| Setting | Retained Codex subscription profile |
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

### Changing the model or reasoning effort

**A different model or effort currently requires code adaptation as well as configuration changes.** Even changing only `xhigh` to `high` is rejected by the current Codex profile. The instructions below describe the adaptation points; they do not imply that another combination has already been tested.

1. Copy a profile, for example `cp configs/gpt6_astra_subscription_one.json configs/custom_model.json`. In the copy, update `roles.<role>.model` and `roles.<role>.effort` for **all eight roles**. Use the provider's exact model identifier and a reasoning level that the selected model and client support. For this bridge the JSON field is `effort`; it is translated into Codex's `model_reasoning_effort`. Do not add `reasoning_effort` as a role field. Supported levels vary by model/client, as described in the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
2. Adapt the implementation points below. The current adapter uses one global model/effort pair, so changing its constants also affects existing Codex profiles in that checkout. Use a separate Git branch or checkout for the experiment, or implement explicit per-profile selection with validation.
3. Update the custom profile's description, requested-difference metadata, token/time limits, and rate estimates to match the experiment. Keep `paper_experiment` as the original paper's reported settings. A new model may require different context handling, structured-output parsing, image support, or tool-call handling; changing a name alone does not establish compatibility.
4. Update the relevant regression tests, run preflight, then try **one task in a new output directory** before scaling up. Do not resume an old model's campaign with a changed configuration.

| Location | What needs adaptation |
| --- | --- |
| [`wgforge/codex_subscription.py`](wgforge/codex_subscription.py) | `MODEL` / `EFFORT`, `validate_subscription_config()`, catalog capability checks, CLI arguments, event/model validation, and diagnostics. Preserve structured-action validation and tool isolation. |
| [`wgforge/paper_profile.py`](wgforge/paper_profile.py) | `validate_paper_profile()` currently allows specific provider/model pairs and requires Codex `xhigh`; update these checks and the model-specific preflight description. The current paper profile also requires one model/provider across all roles. |
| [`tests/test_codex_subscription.py`](tests/test_codex_subscription.py), [`tests/test_codex_subscription_pipeline.py`](tests/test_codex_subscription_pipeline.py) | Extend coverage for the intended model/effort, structured output, images, usage records, and incompatible settings. |
| [`scripts/run_codex_subscription.sh`](scripts/run_codex_subscription.sh) | The convenience launcher always selects the original one-task config and prints GPT-6 Astra / xhigh. Update its config selection and messages, or use the explicit custom-config commands below. Its optional argument is a task count, not a model name. |

Changing the model in the Codex desktop app, an interactive `/model` session, or your personal Codex config does **not** change this pipeline's selected model: the bridge ignores user configuration and passes its own model/effort explicitly.

After completing the adaptation, with dependencies, local image, ChatGPT login, and `SERPAPI_API_KEY` already configured in the launching shell:

```bash
export WGFORGE_PAPER_IMAGE="$(docker image inspect --format '{{.Id}}' workgenesis-paper:local)"
.venv/bin/python scripts/check_serpapi.py configs/custom_model.json
.venv/bin/python -m wgforge preflight --config configs/custom_model.json --count 1
```

Inspect the preflight report and resolve pending requirements. Preflight does not prove live model access or successful task execution. Then start the one-task trial:

```bash
mkdir -p runs
WG_CUSTOM_RUN="$(mktemp -d runs/custom-model-XXXXXXXX)"
.venv/bin/python -m wgforge campaign \
  --config configs/custom_model.json --count 1 --out "$WG_CUSTOM_RUN" --approve-live
```

**Switching providers is a separate adaptation.** For Claude, another API provider, or a local model, start from the corresponding provider configuration and review its authentication, request/response schema, reasoning controls, image/tool support, and paper-profile validation. ChatGPT subscription authentication is not interchangeable with an API key or a different provider's subscription. The retained legacy examples do not by themselves establish compatibility with the full `paper_v1` pipeline.

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

### Three workers: author 200 tasks without solving or evaluation

To generate 200 **unevaluated task packets** across three parallel workers, using the same GPT-6 Astra / `xhigh` configuration:

```bash
bash scripts/run_codex_parallel.sh 200
```

The number is the **total authored-task target**, not a per-worker target. Omitting it also defaults to **200**, assigning **67, 67, and 66** plans to the three workers. For example, `30` assigns 10 plans to each worker, and `100` assigns 34, 33, and 33. Counts below three start only the needed workers. The planner builds one occupation-balanced schedule and assigns slots 1/4/7/... to worker 1, 2/5/8/... to worker 2, and 3/6/9/... to worker 3. Their first occupations differ; plan IDs never overlap within the batch.

**This parallel launcher runs only source discovery, companion-file creation, and task/rubric authoring.** It skips solver, judge, attribution, repair and teacher calls. It retains file/schema/provenance checks needed to save usable packets, but performs no independent answer evaluation. Outputs are labeled `authored_unverified`, not accepted or evaluated. The original `run_codex_subscription.sh` command still runs the full pipeline.

All workers use one saved configuration snapshot and separate processes, Docker sessions, campaign budgets, receipts, logs, and output directories. A locked shared source registry prevents two workers in this batch from reserving the same source URL/hash. This exact-source check does not guarantee semantic task uniqueness. Previously completed standalone runs remain separate and are not imported into the new batch's registry.

The launcher prints the batch directory, for example `runs/gpt6-astra-author-only-parallel-200-XXXXXXXX`. Save that path. Progress is shown in the launching terminal; each worker's detailed output is written to `worker-1.log`, `worker-2.log`, or `worker-3.log` inside it. One failed worker does not stop the other workers. All three share the account's service limits, so speedup is not guaranteed to be threefold; their configured local budget caps apply separately.

Check progress or follow one worker (replace the example directory):

```bash
.venv/bin/python -m wgforge.parallel status --out runs/YOUR_PARALLEL_BATCH
tail -f runs/YOUR_PARALLEL_BATCH/worker-1.log
```

To continue the **same batch**, reuse its saved schedule and configuration:

```bash
bash scripts/run_codex_parallel.sh --resume runs/YOUR_PARALLEL_BATCH
```

Completed task packets remain on disk. Completed checkpoints are verified and reused rather than regenerated; logs are appended. The resume command rejects older full-pipeline batches, so it cannot silently start solving tasks. Preserve an old batch and start a new author-only batch instead of changing its saved configuration. An unfinished stage with an uncertain provider/search outcome can still stop for receipt inspection instead of automatic replay. A hard shutdown may leave a worker's `.campaign.lock`; only remove that stale lock after confirming no worker is running. Removing it does not erase or bypass an uncertain-stage checkpoint. Normal Ctrl+C asks all workers to stop and preserves their saved outputs.

Each worker stores `campaign.json`, task bundles under `tasks/`, and an index at `dataset/tasks.json`. Bundles contain the public task and attachments plus the private rubric. `batch_plan.json` stores the immutable assignment; `batch_status.json` summarizes `authored_tasks`, finished plans and worker exit codes. This mode produces no answer trajectories; `accepted_tasks` and `completed_trajectories` stay zero. Run files are local and ignored by Git. No already completed standalone task is deleted or overwritten by the parallel launcher.

For an offline preview of the schedule without model/search calls:

```bash
.venv/bin/python -m wgforge.parallel new \
  --config configs/gpt6_astra_subscription_one.json --count 200 \
  --out runs/parallel-preview --prepare-only
```

### Limits and usage accounting

The subscription presets intentionally retain very high local workload budgets: up to 1 billion cumulative tokens per role and 1 trillion per campaign. Review `roles.*.budget` and the campaign budget before increasing the task count. These settings do **not** increase subscription quotas or bypass service limits. Per-role step limits, timeouts, and source-search limits still apply.

`charged_upper_bound_usd` is an API-equivalent workload estimate for this transport, not a subscription invoice or guaranteed cost bound. Input usage includes cached input once. SerpAPI has separate quota; uncertain requests retain accounting reservations and are not automatically replayed.

### Outputs and restarting

For the full pipeline, inspect `runs/<run-name>/campaign.json`. Completion requires `status: completed` and the requested `counts.accepted_tasks` and `counts.completed_trajectories`. For an author-only parallel batch, use `batch_status.json`: completion requires `status: completed` and `authored_tasks` equal to the total target. Stage messages such as `task_author — Complete` are only one part of the pipeline. `6/256` counts that agent's steps, not completed tasks.

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
