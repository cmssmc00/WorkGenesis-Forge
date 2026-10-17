# Configuring a small live trial

## 1. Begin with a relevant plan and real source

The offline demo establishes file IO and fact recomputation only. To use the bundled USGS source, edit the planning section of your own configuration:

```json
{"planning":{"count":1,"seed":42,"professions":["Geoscientists, Except Hydrologists and Geographers"],"sampling":"occupation_balanced"}}
```

Generate a plan with `python -m wgforge plan --config configs/my-live.json --out runs/usgs-plan.json`.

The full O*NET adapter reads the version-pinned official database; the compact smoke inventory is explicitly curated from real rows. Full-source use requires `planning.adapter="onet"` and an existing `onet_manifest_path`. Paths in configuration are relative to the shell's current directory, so run these examples from the package root.

## 2. Configure every isolated role

Copy `configs/qwen27b_adaptation.json` or `configs/paper_method.json` to `configs/my-live.json`. Do not put credentials into JSON. Set your authorized provider key in the named environment variable using your normal secure shell/secret manager.

For each role, configure:

- Exact HTTPS `/chat/completions` endpoint and exact allowed hostname
- Actual model alias accepted by your provider
- `privacy.public_upload_approved=true` only after approving the public source upload
- `privacy.private_upload_approved=true` for judge, attributor, repairer, and repair reviewer only after approving rubric/private-reference upload
- Accurate current input/output USD-per-million rates, per-role request/step/token/cost limits, and wall-clock limits

The sample rates are illustrative. They are not a current price quote. The conservative token reservation uses a UTF-8 byte bound, not a native tokenizer estimate; large inputs can be rejected earlier than their exact tokenizer limit. Failed and uncertain requests retain budget reservations. Campaign totals include all stored role attempts.

`--approve-live` is a separate final opt-in. The packaged configurations make no request until deliberately edited and invoked. Start with one scenario and small budgets. Paid search has its own explicit `sources.enable_paid_search=true` and `serpapi_key_env`; it is not covered by model token billing. Search attempt receipts prevent uncertain paid searches from automatic replay.

## 3. Run one end-to-end scenario

```bash
python -m wgforge generate \
  --sources examples/anchors/usgs_manifest.json \
  --plan runs/usgs-plan.json --config configs/my-live.json \
  --out runs/live-usgs --approve-live
```

The author must inspect each source and return a cited suitability assessment. A thin, unrelated, or unusable source is rejected. Its task and supporting files are materialized, then an isolated solver sees only the ordinary public task and files. The judge inspects actual outputs item by item. Every unsatisfied criterion gets Agent, Task, and/or Rubric attribution. Task/Rubric defects alone may drive repair. A separate review rejects unjustified simplification. Every accepted repair is executed and audited again.

Scenario acceptance is independent of executor mastery. A difficult but valid scenario can be accepted when the executor fails only due to its own capabilities. Training admission is a separate process.

## 4. Enable real source acquisition when ready

`discover` writes candidate metadata. `fetch` downloads immutable files and manifests. `campaign` combines acquisition with planning and scenario construction. Use exact approved source hosts; redirects, private IPs, oversized payloads, HTML error pages, and unexpected MIME types fail closed. The strict downloader uses direct pinned TLS and intentionally does not inherit an ambient proxy. Environments requiring a proxy need a separately reviewed egress integration; do not disable SSRF checks.

Example direct source configurations can contain records with scoped `rights_basis`, `rights_evidence`, and explicit `rights_permissions`. An unknown license must remain pending. The source parser does not infer rights from a `.gov` hostname or a search snippet.

A campaign saves its lineage registry, plans, acquisition receipts, rejected material, scenario revisions, provider receipts, and accepted/discarded counts. Original anchors are never rewritten. Accepted and discarded attempts both consume their anchors within a lineage. `repair.max_restarts_per_plan` bounds retries; `repair.max_rounds` bounds alternating defects; three consecutive audited revisions with the same defective criterion trigger discard.

## 5. Docker is optional for the small structured-file trial

With code disabled, the solver can still read source files and write genuine native artifacts through constrained tools. For arbitrary generated Python, follow `docker/README.md`, provide an already built digest-pinned image, and explicitly enable `code.enable_code`. No host execution fallback exists. No image is automatically downloaded, built, or installed.

The real Docker path was not executed in this delivery environment. Mocked command/import tests verify the restrictions; that is not proof of runtime containment on your machine. Use the real integration test before processing untrusted code.

## 6. Resume and diagnose

Rerun the same command, same directory, and identical configuration. Provider completions and tool results are persisted before consumption. Changed task, configuration, artifacts, or runtime code rejects resume. Unknown-outcome calls require inspection; never delete the checkpoint merely to make the command retry. `stats --root RUN_DIRECTORY` reports reserved or reported costs. No raw API keys or provider error bodies are written to reports.

The machine-readable files remain authoritative. Do not treat `checks_passed` in a demo as `scenario_accepted`, or `scenario_accepted` as a qualified training trajectory.
