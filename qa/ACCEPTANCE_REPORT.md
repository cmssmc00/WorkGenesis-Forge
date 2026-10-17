# Independent acceptance report

Date: 2026-10-13 UTC. Release: WorkGenesis Forge 0.1.0.

## Decision and scope

The software acceptance checks pass for the tested source, ordinary pip installation, and uv's hardlinked wheel installation. Final verification completed at 11:56 UTC. This is evidence about implementation behavior, not a reproduction of the paper's model results.

- Repository suite: **115 tests; 114 passed, one skipped** (real Docker integration).
- Independent suite: **112 tests; all passed**, including six tests against existing local Qwen tokenizer assets.
- Clean, non-editable pip installation: CLI `doctor`, `demo`, and `plan` passed from outside the checkout. Six seeded plans were produced. Independently located pip, uv, and checkout demo runs had identical artifact hashes. The uv-installed assets had link count 2; the trusted package-only importer made hash-verified private copies with link count 1. Arbitrary source hardlinks remained rejected. Three independent regressions also covered package-copy symlinks, FIFOs, and corrupted hashes.
- Independent recomputation passed for every demo scalar, daily aggregate, source header, and all 200 source rows.

No paid provider request, model-weight loading, training, external publishing, credential setup, or official benchmark execution was performed.

## Reproducible commands

Run these from the repository root, using Python 3.11+ and fresh output paths. `TOKENIZER_DIR` refers to separately supplied, locally pinned assets; they are not included in this release.

```bash
python -m venv /tmp/wg-qa
/tmp/wg-qa/bin/python -m pip install .
/tmp/wg-qa/bin/python -m unittest discover -s tests -v
/tmp/wg-qa/bin/python -m pip install -r recipes/requirements-tokenizer.txt
WGFORGE_TOKENIZER="$TOKENIZER_DIR" WGFORGE_QA_OUTPUT=/tmp/wg-qa-runs \
  /tmp/wg-qa/bin/python qa/run_suite.py
/tmp/wg-qa/bin/python recipes/check_tokenizer.py --tokenizer-dir "$TOKENIZER_DIR"
bash -n scripts/run_offline.sh
REPO="$PWD"
cd /tmp
/tmp/wg-qa/bin/workgenesis-forge doctor
/tmp/wg-qa/bin/workgenesis-forge demo --out /tmp/wg-demo
/tmp/wg-qa/bin/workgenesis-forge plan --config "$REPO/configs/offline.json" --out /tmp/wg-plans.json
/tmp/wg-qa/bin/python "$REPO/qa/recompute_source_control.py" /tmp/wg-demo
```

Without the optional tokenizer dependencies/assets, those six tests explicitly skip rather than download anything. The independent suite creates all required protocol fixtures itself; preexisting `qa/runs/` is unnecessary. Final test logs and selected evidence are in `qa/evidence/`.

Tested environment: Python 3.12.14; openpyxl 3.1.5; python-docx 1.2.0; python-pptx 1.0.2; ReportLab 4.4.9; pypdf 6.10.0; Pillow 12.3.0. Tokenizer-only checks used Transformers 5.18.0, tokenizers 0.23.2, Jinja2 3.1.6, local-only/offline loading, and no Torch or weights.

## End-to-end workflow and separation

A complete two-revision workflow exercised the real orchestration, role-runtime, artifact writers/parsers, audit, attribution, repair review, fresh solve, fresh audit, and qualified-export validation. Its **14 provider responses were deterministic transport mocks**. They covered author, initial solver, judge, attributor, repairer, independent repair reviewer, fresh solver, and fresh judge. The generated XLSX and DOCX files were real native files, and the USGS input was genuine saved source data.

Private-rubric, expected-answer, and repair-history canaries were absent from both solver requests and the public bundle. Privileged roles received separate fresh contexts. A repaired task preserved sources, deliverables, and rubric maxima, and required a fresh execution and audit. Empty or malformed scores/evidence, weakened repairs, stale/tampered cached audits, modified accepted artifacts, incomplete or unordered tool pairs, missing successful finish, wrong public-prompt binding, and mock/offline export modes were rejected. Accepted-workflow resume revalidated evidence without another provider call. Campaign tests cover upfront live approval, bounded attempts, discarded-source reservations, and cross-task URL/hash novelty.

The mocked integration intentionally exercises acceptance plumbing. Its apparent scores and provider receipts are simulation fixtures, not evidence that any real teacher or judge performed well. No genuine qualified SFT corpus was produced. A raw solver trajectory cannot become qualified merely by finishing; semantic review and integrity gates are required.

## Genuine source and artifact checks

The saved bounded USGS CSV has SHA-256 `4382a1162b5f63c3afcc6eb29b71d2198d6d4793fb07b0efbfde395ed31acd04`. Independent CSV/Decimal calculations confirmed 200 valid magnitudes, mean **4.08**, **88** events at or above the fictional 4.5 routing threshold, maximum **5.9**, and tie-broken event ID `us6000tzer`. All daily aggregates and all source rows/headers match the workbook.

The requested query window was October 1–11, but the 200-row limit yields observed data only through October 6. The generated task and briefing disclose this bounded scope. This is not a complete ten-day census or a hazard prediction.

Native XLSX, DOCX, PPTX, and PDF generation/parsing were exercised. The demo PDF, DOCX companion, and workbook were additionally rendered with Poppler/LibreOffice and visually inspected; a narrow workbook-column defect was corrected. PPTX was parsed but not visually rendered. There is no general visual-judging capability: criteria requiring unverified visual evidence fail closed.

Real O*NET 30.0 source bytes and provenance support the scalable inventory adapter; the bundled small planning map is explicitly a smoke fixture. Rights for training, redistribution, and public access are separate gates; unknown rights remain unknown. Missing heldout-corpus checks remain `not_checked`. No benchmark task collection or GPL benchmark-harness implementation was copied into this release. This review is not a comprehensive legal clearance.

The assets were retrieved through verified TLS using the environment's permitted proxy route, recorded in their provenance. The production DNS-pinned downloader was security-tested with mocked transport, not live-tested through this environment's proxy. Do not interpret successful saved-asset tests as proof of production network compatibility.

## Security and failure checks

Mocked/static container tests checked digest-pinned images, no pull, non-root UID, no network, read-only inputs/root, bounded tmpfs output, dropped capabilities, no-new-privileges, resource/time/output limits, and verified cleanup. Unsafe paths, links, special files, output escape, and source hash/size tampering were rejected. Failed cleanup that cannot establish container absence is fatal.

The actual host had no Docker executable. Attempted generated-code execution failed clearly before running code, with no in-process or host-shell fallback. Real container isolation remains **unverified**; the real-Docker test is the one recorded skip. A separate dependency-empty environment reported missing dependencies and exited nonzero instead of generating a fake successful demo.

Public source retrieval checks cover host allowlists, DNS/private-address rejection, redirects, size/type limits, and manifest integrity. Provider endpoints are separately operator-trusted; provider-side DNS/TLS and metering are not proven by these mocks. Paid acquisition is gated by explicit live approval before discovery.

## Tokenizer, training, and evaluation limits

All local tokenizer files matched their supplied pin: `Qwen/Qwen3.8-27B`, revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`. Native-template checks covered tool calls, JSON-string argument normalization without mutating stored traces, reasoning-field masks, assistant-only loss masks, and a full orchestration-shaped trace. The architecture metadata is `Qwen3_5ForConditionalGeneration`; the recipe exposes the appropriate image-text-to-text loader. Actual weight loading and forward/backward compatibility are **unrun**.

Training dry-run negative checks reject missing/unsafe/unsealed manifests and invalid numeric settings before model loading. Packing is off by default; optional cross-example attention is a disclosed adaptation. Compacted-context traces retain exact request snapshots but are rejected by the flat SFT exporter by default.

The paper's Qwen3.6-35B-A3B profile and the requested Qwen3.8-27B adaptation are kept separate. **Unrun:** live author/teacher/judge calls; 20,000-scenario generation; real Docker execution; model training; GPU scaling; official GDPval/APEX/JobBench evaluation; and published Elo/result reproduction. The local paired-evaluation utility measures local rubric deltas and does not claim official benchmark scores. macOS launcher syntax passed; execution on an actual Mac is unverified.
