# Isolated role runtime

`wgforge.runtime` implements actual opt-in HTTPS Chat Completions calls using
Python's standard library. It does not manufacture model trajectories or turn an
offline fixture into a live-model result. Offline smoke fixtures belong to the
pipeline's explicit fixture mode.

## Role and data boundaries

| Role | Entry point | Model-visible data | Tools |
| --- | --- | --- | --- |
| author | `run_json_role` | Only explicit caller payload, normally real public anchors and a sampled plan | None, JSON only |
| solver | `run_agent` | Public request and public reference snapshot | Bounded artifact broker; optionally Docker Python |
| judge | `run_json_role` | Explicit rubric/artifact evidence payload | None, JSON only |
| attributor | `run_json_role` | Explicit task/rubric/score/failure evidence payload | None, JSON only |
| repairer | `run_json_role` | Explicit before-state, defect evidence and repair contract | None, JSON only |
| repair_reviewer | `run_json_role` | Explicit before/after, evidence and repair policy | None, JSON only |
| teacher | `run_agent` | Public-only accepted task, for final collection | Same public artifact broker |
| student | `run_agent` | Public-only task, independent of teacher | Same public artifact broker |

Each call starts from its own role system message and explicit user payload.
There is no provider conversation ID, server-side conversation continuation,
transcript inheritance, automatic sibling-run discovery or hidden file access.
The same provider/model may fill multiple roles while contexts remain separate.
The caller controls what evidence is deliberately passed to a privileged role.
The role helper validates JSON syntax and object shape; the pipeline must validate
its domain-specific schema, scores, attribution and allowed edits before use.

Solver, teacher and student inputs must be a **public directory**, not the task
root. Its only permitted top-level entries are `task.json`, optional `request.txt`,
`anchors/` and `companions/`. The snapshot includes the exact public file bytes.
Private scenario fields such as `rubric`, `oracle`, `trajectory` and `messages`
are rejected as top-level task-contract keys, as are known private grading and
checkpoint filenames. This guard does not censor ordinary source prose mentioning
rubrics. Correct public/private materialization remains a caller responsibility;
the runtime cannot semantically detect a secret deliberately mislabeled as a
public anchor. Sources are instructions only insofar as the public request says;
otherwise the role prompts explicitly treat them as untrusted data.

## Python API

    receipt = run_agent(public_dir, run_dir, config, role="solver", resume=False)
    outcome = run_json_role(payload, run_dir, config, role="author", resume=False)
    result, receipt = outcome["result"], outcome["receipt"]

Use a different run directory for each role, scenario/revision, candidate and
attempt. `run_agent` creates `workspace/input/` and `workspace/output/`.
`finish` requires at least one actual artifact; the pipeline separately verifies
all requested deliverables and substantive quality. Receipts include actual
absolute output paths, per-file SHA-256 hashes and an aggregate artifact digest.
A chat response alone is not a successful artifact rollout.

## Explicit live configuration

A complete configuration has the following shape; prices here are examples,
**not verified provider prices**. Set rates to the provider's current terms before
any authorized run, and use a dedicated account/project spending cap as well.

```json
{
  "approve_live": true,
  "endpoint": "https://api.openai.com/v1/chat/completions",
  "allowed_hosts": ["api.openai.com"],
  "model": "YOUR_APPROVED_MODEL",
  "api_key_env": "WGFORGE_API_KEY",
  "privacy": {
    "public_upload_approved": true,
    "private_upload_approved": false
  },
  "timeout_seconds": 60,
  "max_run_seconds": 1800,
  "max_retries": 1,
  "max_response_bytes": 4000000,
  "max_payload_bytes": 4000000,
  "output_token_parameter": "max_completion_tokens",
  "budget": {
    "max_cost_usd": 1.0,
    "input_usd_per_million": 1.0,
    "output_usd_per_million": 3.0,
    "max_requests": 10,
    "max_steps": 8,
    "max_input_tokens": 100000,
    "max_output_tokens": 4000,
    "max_total_tokens": 200000
  }
}
```

Only an environment-variable **name** is stored in configuration. Literal keys,
custom authorization headers, userinfo/query credentials and insecure endpoints
are rejected. Supply the credential to the process environment through an
approved secret mechanism; never put it in a command example, artifact, dataset
or source file. Explicit public-data upload consent is required for every live
role. Judge, attributor, repairer and repair_reviewer additionally require
`privacy.private_upload_approved=true`; this intentionally gates private rubric
and evaluation-evidence transmission separately. An author normally receives
only public data and creates its private rubric as output.

Endpoints require an exact allowlisted public-style DNS hostname, HTTPS on port
443 and a `/chat/completions` path. Redirects and inherited HTTP proxy settings
are disabled. This is an operator-configured provider adapter, not an arbitrary
web fetcher. The operator must trust the approved provider's DNS, TLS endpoint,
usage accounting and stated retention terms. Provider-supported optional features
are deliberately limited to tool calls and JSON-object response mode.

## Budgets, uncertainty and resume

Before **every** network attempt the runtime durably reserves a conservative
input-token allowance (serialized UTF-8 request bytes plus framing allowance),
the full completion-token allowance, and their cost at the configured rates.
Every retry counts against request, token and dollar budgets. A successful
response must report nonnegative prompt/completion usage within the reservation;
its known reported cost replaces that reservation. A provider limit violation is
recorded and fails the run. These are conservative application controls under
the provider's token/pricing contract, not a guarantee against provider
misbilling or unusual tokenization. Run wall-clock and transport timeouts are
also enforced; provider/socket behavior can affect when an in-flight read exits.

- HTTP 429 may be retried, up to `max_retries`; each attempt retains its charge
  allowance until successful usage is known.
- Timeouts, network failures and uncertain HTTP 408/5xx responses are never
  automatically replayed, even when retries remain.
- An interrupted `in_flight` request retains its reservation and cannot resume
  with another paid submission of the same request.
- Invalid provider responses retain their reservation and block automatic replay.
- A successful provider message is saved with its receipt **before** consumption;
  resume reuses that message without a second call.
- Every tool is checkpointed as started before execution and done afterward.
  An interrupted started tool is not replayed. Completed tool results are reused.

`checkpoint.json` contains the request ledger (`status`, `charged_usd`,
`charged_tokens`, request/response digests), current messages and tool state.
`run.json` contains redacted request receipts and usage/cost totals. The orchestrator
can scan request ledgers between stages to cap a larger campaign; each stage's
exact config must then be persisted rather than silently increased on resume.
The runtime's own budgets apply to one role run, not automatically to a campaign.
Before sending a request, the runtime reserves the conservative input bound plus
the full configured output allowance. A total-token budget error now reports
used tokens, remaining tokens, both bounds, and the next reservation. This can
block a request before the consumed token count reaches the limit; the blocked
request is not sent or added to the ledger.

Resume requires identical configuration, role, payload/public hashes and runtime
implementation hashes. A completed artifact run verifies output hashes; a
completed JSON role verifies its result. A changed budget, model, endpoint,
privacy flag or code version requires a deliberately new run. Wall-clock time
starts at the original attempt and does not reset on resume. Run locks prevent
concurrent writers. A lock remaining after a process crash must only be removed
after the operator has verified no process or named Docker container is active.

Never delete uncertain receipts to regain budget. Reconcile provider billing and
inspect work before an explicitly authorized new run. Logs do not contain API
keys or raw provider error bodies; recognized key echoes are redacted, and HTTP
error bodies are retained only as hashes. Local checkpoints still contain task
and role content, potentially including private rubrics; protect the run
filesystem accordingly.

### Optional streaming and explicit HTTP 504 recovery

`stream: true` selects Chat Completions SSE and requests final usage with
`stream_options.include_usage`. The decoder joins text, indexed function-call
arguments and MiniMax reasoning metadata. It requires a terminal finish reason
and valid final usage, reported at or after that terminal choice. A clean EOF
without `[DONE]` is accepted with this evidence; tool arguments must also be
complete JSON objects (except a `length` response, which is saved and rejected
by the truncation guard). MiniMax's official M3 test
[`test_02_03_sse_done_marker`](https://github.com/MiniMax-AI/MiniMax-Provider-Verifier/blob/main/m3_format_check/m3_text_tests.py)
documents missing `[DONE]` as a known issue. Incomplete/error streams execute no
partial tool calls, retain their reservation, and block automatic replay. Decoded output
is bounded by `max_response_bytes`; SSE framing has a separate bound of sixteen
times that limit. Streaming does not guarantee against an upstream HTTP 504.

While receiving data, bounded redacted partial messages and stream diagnostics
are periodically checkpointed (at most once every five seconds, plus terminal
updates). Diagnostics include bytes/events received, wire digest, finish reason,
observed usage and termination type. Partial messages are diagnostic only: they
are not executed or added to the solver transcript and are excluded from
published request receipts. Successful responses replace the partial message
with the complete provider message. Observed partial usage never releases an
uncertain request's reservation.

`workflow.prepare_evolution_recovery(scenario, source_run, out, config,
campaign_root)` prepares an explicit offline fork for one narrow case: a solver
with a completely consumed read-only prefix, no outputs, and one final unknown
HTTP 504 request. Only transport may change from non-streaming to streaming;
task, model, budgets, permissions, prompts and tool schemas must match. This
helper is not invoked by ordinary resume. It requires an empty new output path
and both attempts within the same campaign root.

The fork retains all old messages, cached read results and request receipts,
including the unknown charge. `gateway_recovery` records the parent checkpoint's
absolute path and hash, old runtime/config hashes and the acknowledged request.
The shared ledger counts inherited receipts once at their original location;
source deletion or mutation rejects recovery/accounting. The new attempt's wall
clock starts at its first execution and later resume does not reset it. A new
unknown request blocks the fork as usual. This migration cannot chain recovery
forks or migrate pending writes, completed artifacts, teachers or students.

## Artifact broker

The broker permits only input/output listing, bounded text/table extraction,
text-file writes and trusted structured DOCX/XLSX/PPTX/PDF writers. It rejects
absolute paths, traversal, symlinks, hard links, special filesystem entries,
unsafe archive expansion and quota violations. Only `output/` is writable.
CSV/TSV formula-like data must be explicitly escaped as text (plain signed
numbers are allowed). Spreadsheet formula evaluation remains in the project's
restricted formula implementation; there is no host `eval` or shell tool.

Default limits include 4 MB per file, 16 MB total output, 32 MB public input,
256 files, 40,000 characters per read, 1 MB tool arguments and 128 tool calls.
All limits can be set explicitly under `limits` and are part of the resume
fingerprint. Trusted writer/reader dependencies must be installed through the
project's pinned requirements before starting. Missing dependencies produce an
actionable error rather than installing packages during a run.

The native writer tools advertise and validate nested artifact schemas. Table
headers are arrays of strings, such as `["Field", "Value"]`; rows are arrays of
scalar cells, and each row must match the header width. Invalid arguments return
the exact schema field that needs correction, without echoing submitted values.
Every write replaces the complete file, so include all worksheets or slides in
one call. A workbook containing only a title is rejected before any file write.
`read_file` pagination counts characters; `read_table` pagination counts data
rows and returns the headers separately.

## Optional actual Docker sandbox

Enable only after approving and preloading a specific image:

```json
{
  "code": {
    "enable_code": true,
    "image": "YOUR_REVIEWED_REPOSITORY@sha256:YOUR_64_CHARACTER_LOWERCASE_DIGEST",
    "timeout_seconds": 60,
    "memory_mb": 512,
    "cpus": 1,
    "pids_limit": 32,
    "tmp_mb": 32,
    "max_code_bytes": 100000
  }
}
```

This is additive to the live configuration. `execute_python` is not advertised
unless enabled. Its code is passed as data to a trusted launcher **inside Docker**;
there is never host execution of generated code. Docker uses no network, all
capabilities dropped, no new privileges, a read-only root filesystem, non-root
UID/GID, PID/memory/swap/CPU limits and no host secrets or private inputs.

`/input` is the sole read-only host bind. `/output` is a bounded writable tmpfs,
not an unbounded writable host directory. Existing output is seeded into that
container and validated regular-file data is returned over capped standard I/O
for import into `workspace/output/`. `/tmp` is independently size-limited.
This design enforces output-byte limits before the untrusted program can fill
host storage. Container stdout is treated as untrusted input, even if generated
code tampers with the launcher. Output deletion is not propagated to the host.
Cleanup is verified; if it cannot be verified the entire agent run fails closed.

See [Docker setup and integration checks](../docker/README.md). Missing Docker,
missing images and unsupported code environments are explicit blockers. The
structured broker is a logical capability boundary; Docker provides actual OS
process isolation for generated code. Neither substitutes for a patched dedicated
host/VM and trusted dependency/image supply chain.

## Verification boundary

Unit tests exercise mocked HTTPS and Docker transports, private-data exclusion,
all role gates, path attacks, output quotas, real structured artifact creation,
request/tool interruption, response reuse, cost reservations and cleanup failures.
They make no paid calls. The real Docker isolation test is skipped unless an
operator supplies `WGFORGE_DOCKER_TEST_IMAGE` after preloading its pinned image.
No mocked trajectory, static command assertion or offline fixture establishes
live provider quality, billing correctness or real-host container isolation.

## Optional explicit context compaction and paper divergence

Compaction is **off by default**. An opt-in artifact-agent configuration may add:

```json
{
  "context_compaction": {
    "strategy": "deterministic_tool_results",
    "window_tokens": 256000,
    "trigger_fraction": 0.7,
    "keep_recent_messages": 4,
    "minimum_result_chars": 2000
  }
}
```

The trigger is 70% of the smaller of the configured input allowance and the
context window after reserving completion capacity. The runtime uses its
conservative byte-derived token bound, not an exact proprietary tokenizer.
A declared 256,000-token window does not verify that the chosen model supports
that window; the operator must choose a supported model and corresponding budget.

Only sufficiently large historical **tool results** outside the retained recent
message window may be replaced in the next provider request. Replacement is
explicit: tool name, safe retrieval arguments, content digest, original size,
retained metadata and a notice telling the model to reread files as needed.
The public request, system role, assistant messages and recent tool evidence are
not silently rewritten. If this is insufficient to fit the input/context budget,
the runtime stops rather than discarding more context or increasing its budget.

The full original transcript and tool cache remain unchanged. Before a compacted
request can be sent, its exact replay messages and original/context digests are
durably checkpointed. `trajectory.json` contains:

- `messages`: complete canonical conversation, including original tool results
- `context_events`: each compaction's original/context digests, changed message
  indices, byte-derived bounds, strategy and explicit lossiness metadata
- `provider_contexts`: exact compacted message snapshots plus their provider
  request indices and context digests
- `request_message_map`: mapping from provider receipt indices to original
  assistant-message positions

Provider receipts bind the actual request digest and context digest, with a
`context_compaction_id` when applicable. Prepared contexts that failed a budget
check may have no corresponding request index; they are not a model call.
No paid summary request or synthetic model message is generated by this strategy.
Resume restores successful responses and the exact recorded context event
without paying again. The full context strategy and settings are fingerprinted.

This is a deliberately conservative **implementation divergence** from a
paper-style agent framework's model-based automatic summarization. It is not a
claim of Stirrup equivalence, exact 256K tokenizer behavior or reproduction of a
reported training dataset. It may lose useful historical detail and require
additional reads. Plain chat-format training exporters must reject trajectories
with nonempty `context_events` by default: flattening the full retained transcript
would falsely imply the model saw content omitted from its actual context.
A future specialized exporter may produce exact-context segments using the
recorded provider snapshots; this runtime does not silently perform that conversion.
