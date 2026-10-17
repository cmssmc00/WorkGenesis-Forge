# WorkGenesis implementation and reproduction contract

## Current entry — 2026-10-17

The current entry uses `gpt-6-astra / xhigh` for all eight roles through official Codex CLI ChatGPT authentication. A local pilot completed one accepted task and one teacher trajectory; no student training or official benchmark reproduction has been performed. See [README](../README.md) and [Codex transport details](CODEX_SUBSCRIPTION.md). Earlier preparation-only statements and Claude-specific budgets below describe historical profiles, not the current subscription presets.

Appendix D transcriptions are now included with source pages and hashes in `wgforge/data/paper_prompts/source.json`; they retain their upstream provenance and are not assigned the project code's ownership or a new license. The earlier prompt-packaging recommendation below is historical.

Date: 2026-10-13. Scope: independently implement the published WorkGenesis method; make an auditable runnable pipeline; measure whether its data improves a chosen student. This document is an engineering specification, not a claim that training or benchmark reproduction has happened.

## 0. Current paper profile update — 2026-10-15

The native API implementation entry is [`configs/claude_opus55_paper.json`](../configs/claude_opus55_paper.json), with `synthesis.protocol=paper_v1`. The [Chinese operational guide](PAPER_PIPELINE.md) explains its stages, roles, environment setup, commands and remaining limits. Sections 1–19 below preserve the original contract and historical engineering choices. Where their descriptions of legacy inventory coverage, broker-only tools, inserted companion disclosures, repair-review gates or training export differ, the following table describes the **native API paper profile**, not a retroactive change to the earlier MiniMax pilot.

The user subsequently selected Claude subscription access and explicitly accepted documented execution differences. The separate [`claude_opus55_subscription.json`](../configs/claude_opus55_subscription.json) profile retains these stages, eight roles, official Stirrup outer loop and isolated sandbox tools, while official Claude Code generates validated action proposals. Each model turn receives serialized history and genuine image blocks in a fresh CLI invocation. This does not reproduce native message/tool/thinking replay or guarantee the same internal context and retry behavior. Reports identify `official_stirrup_subscription_bridge`, preserve observable CLI events and distinguish invocation counts from internal calls. USD fields are workload estimates, not actual subscription billing or quota. The dataset index labels bridge traces separately, and the legacy native-message SFT exporter rejects them pending a dedicated adapter. See [subscription setup and limits](CLAUDE_SUBSCRIPTION.md).

| Area | Current profile and evidence boundary |
| --- | --- |
| Requested scope | Pipeline changes only. No Claude generation campaign, new 100-task corpus, student training or official benchmark run was executed for this change. Earlier MiniMax paid runs remain separate historical results. |
| Roles and model | Eight independent role contexts: researcher, companion author, task/rubric author, solver, judge, attributor, repairer and final teacher. All use native Anthropic Messages API model `claude-opus-5-5`, as documented by [Anthropic](https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5). Role separation is not eight distinct models. Deterministic planning is outside this count. |
| Occupation coverage | All 65 Table 2 labels; near-balanced deterministic slots; default target 100 accepted tasks. `planning.count` or CLI `--count` changes the target. Official O*NET 30.0 descriptions, Task Statements, Work Activities, Technology Skills and Work Context ground reconstructed playbooks. Missing activity rows are recorded rather than invented; the broad buyer label transparently maps to three official codes. Original playbook wording and exact allocation remain unpublished. |
| Paper stages | Tool-driven acquisition/inspection; actual optional companion creation and reinspection; request then rubric against the frozen reference packet; independent solve; itemwise score; failed-item cause attribution; targeted repair/reverification; final trajectory collection. The module mapping is in `PAPER_PIPELINE.md`. Appendix D text is extracted from the user-supplied PDF, with page ranges and hashes in `wgforge/data/paper_prompts/source.json`, plus explicit execution/serialization bindings. This replaces the baseline's independently written prompt choice for this local profile and does not establish publication rights to third-party material. |
| Execution harness | Official [Stirrup revision `247f24d56b2108235880ed2a2baea5d35b5a67ee`](https://github.com/ArtificialAnalysis/Stirrup/tree/247f24d56b2108235880ed2a2baea5d35b5a67ee), with native code/image tools, isolated Docker/E2B backends, 256,000-token context, 70% model-summary trigger and 600-second command timeout. The selected revision and reconstructed environment are local choices because original pins/image are unpublished. They are not the legacy deterministic compaction implementation. |
| Input/output and provenance | Actual downloaded anchors stay immutable. Generated native companions are copied byte-for-byte through inspection and authoring. Generation assumptions and provenance remain in private metadata; no extra construction-disclosure headings are injected into the worker's ordinary business packet. Solver/teacher receive only public requests and reference files, never rubric, private author notes or repair diagnosis. |
| Scoring | LLM judgment over real reference/deliverable files, with code for parsing/recomputation/rendering and `view_image` for visual evidence. Host checks enforce schema, score range, artifact identity and lineage. They do not replace professional judgment with a fixed domain-answer grader or guarantee that the LLM catches every factual error. |
| Acceptance and repair | Accept when no Task/Rubric defects are diagnosed, even if Agent-only failures remain. Preserve anchors and do not weaken Agent-only requirements. Track criterion identity through permitted changes; discard after the same criterion remains defective in three consecutive audits. The interpretation of consecutive recurrence is explicit because the paper does not fully formalize every recurrence edge case. `repair.independent_repair_review=false`; no additional independent review or student-calibration acceptance gate. |
| Collection | One new independent teacher rollout per accepted task; successful `finish` and actual submitted files required. This is raw authentic trajectory collection, with no invented score-based or human-signature SFT filter. A fresh rollout rather than reuse of the audit trace is a recorded implementation choice. Exact SFT filtering, loss masking and serialization remain unresolved. |
| Completion semantics | Target accepted tasks and requested completed trajectories must be reached before `status=completed`. Discarded construction attempts, source failures, merely planned slots and partial trajectories do not count as success. Default 100 slots allocate two to 35 occupations and one to the other 30; fewer than 65 slots cannot cover every occupation. |
| Recovery | Verify and reuse completed stages. Preserve interrupted Stirrup sessions and prohibit automatic replay after agent entry, any model/search reservation or tool-stage work. A clean setup failure before all such work may retry setup while retaining its failure record. Unknown outcomes retain budget reservations; a retry is never presumed free. |

The two user-requested deviations are **Claude Opus 5.5 instead of GLM-5.2** and **a configurable initial target of 100 instead of the reported 20,000 trajectories**. Locally selected parameters include seed 42, rubric target 40, eight searches per web-enabled role run, ten construction attempts per slot, model effort/turn/output limits and finite budgets. The paper's search/rubric prompt placeholders do not disclose those numerical values. Exhausting a local limit leaves a campaign incomplete or interrupted.

Current campaign model-call caps are $500, 75 million tokens and 25,000 requests, with additional per-role limits. These are conservative operational guards, not an expected cost or a promise of 100 accepted tasks. SerpAPI search and sandbox charges are separate. Provider input accounting includes cache input once; unknown completion remains reserved. The `max_input_tokens` transport-bound setting is a conservative admission bound, not a change from the harness's disclosed 256K context window.

Offline verification is available through `python -m wgforge preflight --config configs/claude_opus55_paper.json --count 100`. It validates all native role configurations and reports missing package/key/sandbox setup without calling providers, searching or starting containers. It does not verify account access, Docker runtime behavior or actual task quality. Optional `pip install -e '.[paper]'`, explicit image construction using `scripts/build_paper_image.sh` with a verified `BASE_IMAGE` digest, and a later `campaign --approve-live` are documented future operations, not operations completed by this revision.

This establishes an implementation of the **disclosed generation/verification/collection flow with explicit substitutions and reconstructed dependencies**. It does not establish exact original-data reconstruction, an exact original training run, or numerical benchmark replication. The missing original playbooks, complete image, original harness revision, generation hyperparameters and SFT preprocessing remain unresolved rather than being filled with claimed paper defaults.

## 1. Evidence, provenance, and reproduction level

Primary sources:
- Paper v1, method: https://arxiv.org/html/2609.39325v1#S3
- Synthesis implementation: https://arxiv.org/html/2609.39325v1#A1.SS1
- Training: https://arxiv.org/html/2609.39325v1#A1.SS2
- Evaluation: https://arxiv.org/html/2609.39325v1#A1.SS3
- Module specifications: https://arxiv.org/html/2609.39325v1#A4
- Released model: https://huggingface.co/endless-frontier/Fx-Work/tree/31892c836bf09caa197201ddc4e6d0e421e3c9e3
- Harness: https://github.com/ArtificialAnalysis/Stirrup/tree/247f24d56b2108235880ed2a2baea5d35b5a67ee
- Current evaluation documentation: https://artificialanalysis.ai/methodology/intelligence-benchmarking

The accompanying `reference_sources.json` pins fetched bytes and repository revisions. The paper has an arXiv perpetual non-exclusive distribution license. It is not an express open-source license for copying its prompts into a package. Use independently written prompts and cite the original appendix; do not package the paper or its verbatim prompts. The model repository declares Apache-2.0, subject to upstream/third-party terms. This does not establish rights to the authors' training data or any retrieved anchor.

Distinguish three outcomes in all CLI output, documentation, and reports:
1. **Implementation conformance:** unit/integration tests establish that code implements the invariants below.
2. **Methodological reproduction:** actual public-source retrieval, independently executed teacher trajectories, verification, and SFT follow the disclosed method, with all departures listed.
3. **Numerical replication:** matching model/data/harness/evaluation evidence produces statistically comparable benchmark results. The public release is insufficient to claim an exact reconstruction of the original training run.

A Qwen3.8-27B student is an adaptation, not the paper's Qwen3.6-35B-A3B experiment. Alternate teachers, local runtimes, alternate search providers, shortened context, LoRA, and newly chosen quality filters are also adaptations. Preserve these choices as machine-readable deviations; do not rename an adaptation to “paper-exact.”

## 2. Published facts that constrain the implementation

The paper's work unit is `(request, reference files, rubric)`, with reference files split into immutable real anchors and generated companions. It uses 65 O*NET occupations, GLM-5.2 for all generation/verification modules, SerpAPI-backed retrieval, and GLM-5.2 in Stirrup for work execution. It reports 20,000 complete task-solving tool trajectories, a 256K collection context, and compaction at 70%. The reference solver is isolated from the rubric. Acceptance follows elimination of scenario/rubric defects, not elimination of solver errors. A recurring defect on the same criterion over three verification rounds causes discard and reconstruction. Source lineage includes discarded work.

The public model repository contains weights, tokenizer/processor/configuration files, a model card, and figures. It does not expose the generation pipeline, 20K work packages, 20K trajectories, SFT implementation, original occupation playbooks, exact sandbox image, benchmark submissions, or original calibration data in its inspected file tree. Absence here is not proof that no separate release can ever exist.

## 3. Configuration and run identity

Every run must resolve to an immutable manifest before paid work starts. Minimum fields:
- Implementation version/commit, configuration SHA-256, random seeds, UTC start time, mode (`mock`, `pilot`, `production`), and declared reproduction level.
- Occupation title, sector, verified O*NET identifier when available, O*NET source/revision/license, request seed, required anchor formats, and lineage identifier.
- Exact model identifier/revision and provider for each role; context length, max output tokens, temperature/top-p, and any provider reasoning settings. Unknown provider revision must be recorded honestly.
- Harness and dependency pins; container/sandbox image digest; tool schemas; enabled network scope; process limits; compaction implementation and threshold.
- Search provider and settings; maximum searches, bytes/download, file count, per-stage turns, total turns, retries, repair rounds, restart count, wall-clock allowance, dollar cap, and concurrency.
- Source rights policy; contamination exclusion manifest; output/trajectory schema versions; retention policy.
- Whether student calibration is enabled, its model and split, and every trajectory/export filter.

Do not fill unpublished values with invented “paper defaults.” Put locally chosen defaults under an explicit adaptation section. Reject missing model/provider/budget fields for live mode. Offline demos can use deterministic fakes but must stamp every result as simulated. A mock run must never be eligible for a real-data export.

### Budget semantics

Published numeric limits include the collection context/compaction settings, the three-round recurring-defect rule, and the solver's 10-minute command timeout. The appendix exposes `MAX_SEARCH_ATTEMPTS` and `RUBRIC_SIZE_TARGET` as placeholders without concrete values. The paper does not publish generation temperature, maximum model turns, token budget per completion, total generation dollars, full retry policy, or a global restart cap. The reported $0.77 is benchmark execution cost per task for the trained model, not synthesis cost per training example.

Implement configurable finite caps. Budget exhaustion is `budget_exhausted`, not accepted, and must preserve partial evidence. A global cap is a safety extension and must not be confused with the paper's same-item defect rule. Record actual usage and cost by provider/role/tool, including failed attempts. No automatic paid execution, downloads requiring payment, secret creation, or configuration of persistent credentials belongs in a smoke test.

## 4. Architecture and visibility boundaries

Use distinct role invocations with explicit payload allowlists. Model identity may be identical across roles; independence means fresh context and restricted information, not merely a different role name in one shared conversation.

| Role | Inputs allowed | Outputs | Critical boundary |
| --- | --- | --- | --- |
| Occupational planner | Instruction, grounded occupation playbook, format request, lineage summary | Search directions and scenario seed | No benchmark task/rubric ingestion |
| Anchor researcher | Planner inputs, lineage, retrieval/inspection tools | Downloaded anchors, metadata, rejected-candidate/search logs | No fabricated anchors; no editing downloaded bytes |
| Companion author | Actual anchors, provenance, occupation request | Necessary companions and scenario manifest | Does not author the request/rubric yet |
| Request/rubric author | Actual combined packet and parsed evidence, solver capabilities | Workplace request, private itemized rubric, manifest | References read-only; writes request before rubric |
| Reference executor | Workplace request, reference files, solver tools | Real artifacts, finish event, complete trace | No rubric, author notes, expected answers, attribution, or repair logs |
| Item evaluator | Current request/rubric, current parsed references/deliverables, artifact/media checks | One validated score per criterion | No mutation and no cause attribution |
| Failure reviewer | Unsatisfied items and their source evidence | Agent/Rubric/Task labels and evidence | No task or rubric edits |
| Scenario repairer | Current package, defects, history | Targeted revised constructed material and repair log | Cannot change anchors or “repair” pure Agent errors |
| Final trajectory collector | Accepted final request and reference packet only | Complete teacher solving trace | New clean context; distinguish from audit trace |

The paper does not establish whether final training trajectories are reused final audit traces or fresh rollouts. Prefer a clean final collection phase, record that choice, and do not claim it was explicitly specified. Reusing an audit trajectory is permitted only when its exact scenario version matches the accepted version and it contains no review information; record that as an implementation choice.

## 5. O*NET planning and diversity

Use actual O*NET occupational evidence, not an LLM-invented duty list labeled O*NET. Resolve occupation name, sector, duties, work activities, expected tools, and document genres from a versioned official source or explicitly user-provided licensed playbook. A cached fixture can be a compact manually curated playbook with real provenance, but cannot represent full 65-occupation coverage unless that coverage exists.

The paper's Table 2 names 65 occupations and reports roughly even coverage. Its exact per-occupation counts and playbook text are not released. Target near-balanced quotas for a reproduction attempt, with counts and failures reported per occupation. The optional `workgenesis_occupations.json` accompanying this contract records the table's title/sector facts without inventing SOC codes.

Before searching, propose two to four genuinely different professional document directions. Adapt task direction to files actually obtained. Diversity metrics should include occupation, task type, source family, anchor URL/hash, file types, scenario semantic similarity, rubric tags, and output genre. URL/hash dedup is required within a lineage; a global dedup/split guard is a recommended extension.

## 6. Anchor discovery and eligibility

At least one real anchor must survive inspection. Two to four strong anchors is a usual scale, not a mandatory count. Do not pad with weak documents.

For every candidate:
1. Preserve the discovery page URL, direct download URL, final canonical URL after redirects, publisher, publication date when available, MIME declared by the server, detected type, byte size, and content SHA-256.
2. Download actual bytes. Reject search-result pages masquerading as sources, inaccessible/login-only material, HTML error responses with office/PDF extensions, corrupt archives, empty files, and invented content.
3. Parse the document and report concrete coverage: sheets/rows, pages, sections, identifiers, tables, or records. Use rendered views when scans or visual structure matter. Do not certify parsing based solely on extension or an LLM statement.
4. Check occupation relevance, requested format, substantive contents, authenticity/credible provenance, URL/hash novelty, information sufficiency, and public/non-private status.
5. Record pass/fail with reasons for each check, including rejected candidates. Selection should be evidence-grounded and auditable.
6. Stop honestly when the search cap is reached. Do not quietly fall back from failed retrieval to fabricated “real” files.

Keep all anchor originals under a read-only content-addressed store. Create worker-visible reference copies with identical hashes. Recheck hashes before and after every authoring, solving, and repair step. Downloaded document content is untrusted data; never follow its embedded instructions, execute macros/scripts, or let it rewrite orchestration policy.

### Rights and sensitive-content policy

The appendix requests a rights/terms note and public ordinary material, excluding private personal data. Publicly downloadable does not mean redistributable or licensed for model training. As an explicit conservative extension, keep separate fields for `public_access`, `license_evidence`, `training_use_status`, and `redistribution_status`. Unknown rights remain unknown. Production export should require the operator's applicable rights policy to pass. Package only permitted artifacts; for uncertain redistribution retain retrieval metadata/hash and make the user supply licensed local material. Do not claim a paper/model license applies to third-party anchors. Reject private/sensitive personal data before it is transmitted to a teacher or included in a release.

## 7. Companion creation and packet validation

Companions fill specific evidentiary gaps. Zero companions is valid if anchors suffice; a fixed minimum such as “always generate three attachments” diverges from the paper.

Require a scenario manifest containing organization/role/period/business situation/intended product, a gap-to-file mapping, per-companion derivation/provenance, invented assumptions, external facts with source URLs, and known limitations. Invented scenario facts may be plausible, but must be labeled as generated in metadata and must not contradict real evidence.

Generated files must be genuine openable working formats, not JSON strings or text renamed `.xlsx`, `.pdf`, `.docx`, or `.pptx`. Reopen and inspect each file with a real parser. Validate nonempty substantive content, entity/identifier consistency, units/currencies, time periods, totals, joins, cross-file references, and intended discrepancies. Validate formulas as formulas when required. Render/inspect visual artifacts when relevant. A successful write API response is not artifact verification.

The packet must feel like one coherent professional situation. Synthetic provenance belongs in private metadata; business-facing attachments should not describe the data construction machinery. Keep authoring metadata out of the solver's reference directory.

## 8. Request and rubric contract

The request should specify the professional situation, actor, audience, objective, constraints, and concrete deliverable without exposing grading rules. Difficulty should come from analysis, transformation, judgment, reconciliation, or production, not gratuitous checklists or attachment counts. One deliverable is common; additional outputs need a scenario reason. Inputs and outputs need not share a format.

Default to closed-book work supported by the packet. Permit bounded public research only where the workplace request actually asks for it; capture research sources. Do not require a particular code library or implementation method merely to simplify the evaluator. Do not leak benchmark, hidden-answer, authoring, or rubric terminology into the request.

Rubric structural minimum:
- Stable criterion identity plus ordered position.
- Nonzero numeric score, criterion, boolean required flag, and tags.
- Mixed importance weights; ordinary positive checks often 1–2, important judgments/calculations often 3–5; optional negative penalties where appropriate.
- Checks for actual artifact existence/format, occupational substance, evidence correctness, necessary calculations/transforms, and relevant compliance/traceability.
- Every criterion traceable to the request, supplied evidence, legitimate requested professional composition, or explicitly permitted public research.
- No hidden gold-answer dependency and no unrequested scope expansion.

The paper uses list positions as item identifiers. An implementation should add stable IDs and retain positional mappings because insertion/removal can otherwise evade persistent-defect detection. This is a strengthening, not a published field requirement. Author-provided validation flags are not enough: independently check schema, referenced filenames, parsed facts, and computed totals. Local target rubric length is configurable; the paper does not publish one universal number.

## 9. Independent execution and artifact checks

Create a fresh sandbox populated only with the accepted reference packet and workplace request. Restrict writable locations to scratch/deliverable outputs. Do not mount the author workspace, rubric, source manifests containing answer-bearing notes, benchmark data, repair history, or credentials. If the implementation's tool executor can list parent directories, the visibility boundary has failed.

The published executor description assumes Linux, non-root UID 1000, independent shell commands, Python 3.12, a substantial document/scientific stack, and a 10-minute command timeout. It uses a finish tool with actual absolute file paths and permits abandonment for genuine impossibility. Absence of finish is execution failure. Natural-language claims about creating a file are not submissions.

Enforce path containment and reject symlinks/path traversal outside the worker sandbox. Preserve tool arguments, tool responses, exit codes, timestamps, file hashes, model outputs, usage, and final submission. A host process is not an isolated sandbox merely because the prompt calls it one.

After finish, check that submitted files exist, are nonempty, parse as the declared types, satisfy basic expected structure, and have no obvious placeholder/failure text. Parse delivered contents for the judge, with stable locations such as workbook sheet/cell, PDF page, slide number, section, table, timecode, or transcript segment. Record parser versions and limitations. Unsupported or missing media must not be hallucinated into evidence.

## 10. Scoring semantics

Score every criterion independently and preserve order and identity. The evaluator response must echo the expected score, report an awarded score within the allowed interval, derive satisfied status correctly, and explain the evidence. Reject missing/duplicate/extra criterion results and inconsistent copied values.

For a positive weight `w > 0`, require `0 <= award <= w`; satisfied means `award == w` within a declared numeric tolerance. For a penalty `w < 0`, require `w <= award <= 0`; satisfied means the prohibited condition is absent and `award == 0`. Do not apply `award < w` to penalty rows when selecting failures. Recompute satisfaction in code and verify it against the evaluator response.

Rubric item scores may be partial. A non-full positive score is unsatisfied even if an overall task pass threshold would be met. A missing media basis cannot justify full credit; record unjudgeable evidence and route the resulting issue to review. Positive credit, penalties, required-item satisfaction, normalized score, artifact validity, and scenario acceptance must remain separate quantities. Do not invent a universal paper pass threshold from the case studies.

The judge must inspect actual deliverables, not merely the solver's finish summary. A strong-looking output does not establish correctness. Reference documents control disputes about their actual contents. Professional recommendations can have multiple defensible answers; the evaluator must not invent a hidden target opinion.

## 11. Attribution and repair state machine

Every unsatisfied criterion receives one or more labels from `{Agent, Rubric, Task}` plus evidence and a primary label. Pure Agent means a fair supported requirement was missed. Rubric means the grading requirement is defective. Task means the request/materials form an incoherent or unsupported situation. Multiple labels are allowed when both defects genuinely apply.

The reviewer rechecks the evaluator's rationale against evidence. Unsupported requirements favor Rubric/Task; a fair clearly supported requirement favors Agent. Do not classify every failure as Agent to conceal defects or every failure as Rubric to make the teacher look successful. Each attribution must retain the criterion's exact identity and score values, cite evidence locations, explain its label choice, and state whether a tie-break was needed. The reviewer classifies; it does not edit.

Only labels containing Rubric or Task enter the defect set. The repairer can edit the workplace request, generated companions, and rubric, using minimal necessary changes and recording before/after evidence, dependent edits, reparsed files, hashes, item replacements, unresolved issues, and next-pass requirements. Real anchors stay immutable. A pure Agent failure cannot justify weakening the task or removing its criterion. Any criterion removal needs an independently defensible reason.

Recommended orchestration pseudocode (independently specified):

```
construct fresh scenario from original request
for each verification pass:
    assert anchor hashes unchanged
    run independent solver on this exact scenario version
    inspect submitted artifacts
    score every rubric item
    attribute every unsatisfied item
    defects = items whose labels contain Task or Rubric
    persist immutable pass record
    if defects is empty:
        mark scenario accepted
        persist lineage outcome
        stop
    update stable-item recurrence history
    if a same-item defect has survived three verification rounds:
        discard this scenario and record lineage
        restart from the original synthesis request, with fresh anchors
    otherwise:
        repair only defects
        validate changed materials and all affected criteria
        create new immutable scenario version
        rerun verification
```

The appendix repair prompt contains a prediction-oriented acceptance instruction that could be misread as allowing the repairer to self-certify. The main method explicitly rerenders/rescores revised work. Implement acceptance only after an actual defect-free verification pass. A “this repair should fix everything” response is never enough.

Do not confuse “three rounds for the same item” with “exactly three total attempts for any reason.” Keep a separate finite global budget to prevent a rotating sequence of new defects from running forever. Document how discontinuous recurring defects count, because the paper does not fully formalize consecutive versus cumulative recurrence. A defensible conservative choice is cumulative appearances of the same unresolved defect identity, with reset only after an audited resolution and an explicit audit trail.

Update lineage on acceptance and discard. A discarded scenario cannot silently reuse the same anchors in its restart. Runtime infrastructure errors are operational failures, not automatically evidence that the scenario itself is defective.

## 12. Trajectory capture and training-data export

Scenario validity and demonstration quality are distinct. The method deliberately accepts valid tasks even when the reference agent has capability failures. Do not label all accepted-scenario trajectories as correct demonstrations.

The paper does not disclose an SFT quality-score cutoff, required-item cutoff, student-success band, best-of-N selection, failed-trace retention rule, reasoning inclusion policy, assistant loss masking, tokenizer details for training, exact serialized format, or compaction-to-training reconstruction. Any such choices must be explicit extensions.

A runnable implementation should provide two separately named outputs:
- **All authentic completed teacher traces:** preservation/research output tied to accepted scenario versions, with quality metrics and rejection flags.
- **Training-eligible export:** an explicitly selected policy over real completed traces, with reasons/counts. Never imply these are the original paper's unknown filters.

Recommended conservative export checks (adaptations): complete tool-call/result pairing; authentic provider-returned messages and execution logs; explicit finish; valid submitted artifacts; final scenario version match; no rubric/author/repair leakage into solver context; no unresolved parser or attribution error; rights and contamination checks; no simulated/mock response; and documented quality criteria. Keep agent errors in task analytics even if those traces are withheld from SFT.

Use the target student's actual tokenizer and chat template, including its tool-call serialization. Tokenizer names and model names are not interchangeable. Verify training examples round-trip to equivalent messages and tools. Validate that assistant-only masks exclude prompts and tool/environment outputs if that mask policy is selected. Preserve available provider-returned reasoning separately from user-visible content; never fabricate hidden reasoning. Training on/omitting it is a declared adaptation. No code should silently concatenate authoring, grading, or repair conversations into a solver demonstration.

For context compaction, record the full pre-compaction conversation, summary message, removed span, remaining context, token-count method, and triggering threshold. Do not treat compaction summaries as literal tool observations. A 256K packing bin is not proof that all raw histories can be naively concatenated without boundaries or loss-mask changes.

## 13. Published training configuration and adaptation limits

Reported Fx-Work training configuration (Table 3):

| Parameter | Reported value |
| --- | --- |
| Base | Qwen3.6-35B-A3B, sparse MoE |
| Update steps | 1,537 |
| Packing bin | 256,000 tokens; 16k/GPU over CP |
| Sampling | Shuffled, length-balanced per rollout |
| Optimizer | Adam; beta1 0.9, beta2 0.98 |
| Learning rate | 5e-5, cosine to 1e-6 |
| Warmup | Linear, 10% of steps |
| Weight decay | 0.1 |
| Optimizer state | Distributed, CPU offload, precision-aware |
| Rollout/global batch | 64 / 64; one sample/prompt |
| Parallelism | TP1, PP2, CP16, EP8, ET1 |
| Sequence parallelism | Enabled; dynamic batching, 16k tokens/GPU |
| Hardware | 32 H100 GPUs, four 8-GPU nodes |
| Activation recompute | Full, uniform, one layer |

Unpublished: exact framework/fork and commit, optimizer epsilon, precision policy for every component, clipping, exact packing implementation, loss normalization/masking, trainable parameter selection (including vision), random seeds, exact dataset mixture/order, steps-to-epoch interpretation, and checkpoint-selection procedure. Do not infer epoch count by multiplying batches without knowing packing semantics.

A 27B dense student cannot inherit MoE expert-parallel settings mechanically. Provide an adaptation recipe with verified model compatibility, expected memory requirements, changed optimizer/batching/context settings, and an explicit label. A LoRA pilot can test end-to-end plumbing but is not the paper's disclosed standard SFT run. A package can ship a training-config template without claiming the expensive training has run. Test template parsing and model/schema compatibility separately from actual GPU execution.

The released Fx-Work configuration has a native 262,144-token context and uses the Qwen3_5MoeForConditionalGeneration architecture identifier despite its Qwen3.6 base name. Its released generation defaults include sampling temperature 1.0, top-k 20, top-p 0.95; those are not evidence for the synthesis or evaluation sampling configuration.

## 14. Student calibration, if desired

Student-based difficulty selection is not described in WorkGenesis v1. Keep it optional and off in a paper-method profile. If enabled, use a development-only task split isolated by anchor/scenario lineage; independently solve with the untrained student, record success and uncertainty, and retain a curriculum only under a named adaptation policy. Never use official benchmark outcomes to select individual training examples or adjust their rubrics.

Report sampling distortions caused by calibration, such as occupation or format imbalance. Do not call teacher-agent-only failures evidence that a task is unsolvable. Do not repair a fair task merely because a weak student fails. Calibration should select or describe tasks, not manipulate grading to create a desired failure rate.

## 15. Evaluation protocol and metric boundaries

The reported base-to-Fx-Work values are GDPvalAA-v2 rubric 82.61 to 86.66 and Elo 1053 to 1352; APEX-Agents-AA 13.4% to 25.2%; JobBench Main 14.99 to 25.19 and Easy 56.02 to 70.06. These are paper results, not results of this implementation.

For the paper comparison:
- Use the official Stirrup/E2B work-execution setup with matching tool and context-compression settings.
- GDPval deliverables are scored using GPT-5.5 and Gemini-3.6-flash, with their two scores averaged. The paper anchors Elo to the Artificial Analysis August 4, 2026 snapshot and uses evaluations of 15 common models plus the AA Elo model for additional entries.
- APEX excludes Investment Banking Worlds 244 and 246, leaving 452 tasks: Law 160, Management Consulting 160, Investment Banking 132. Run three independent attempts per task. Gemini-3.6-Flash evaluates binary rubric criteria; an attempt passes only when all criteria pass. Report total passing attempts divided by 1,356, not pass@3.
- JobBench uses Stirrup and the same two-judge average as GDPval; Main and Easy remain separate.
- The paper's aggregate averages `(Elo - 500)/2000 * 100`, APEX pass percentage, and JobBench Main. It does not average all five reported metrics.

Reproduction blockers include the exact original harness image/commit/settings, original task/evaluator revisions, original 15-model pairwise calibration data and Elo implementation state. Do not transform a rubric percentage into an official Elo with a guessed linear formula: the displayed Elo normalization is for the aggregate, not a rubric-to-Elo conversion.

As of this research, current AA documentation describes GDPval-AA v2.1 and a changed scoring anchor. A fresh run against current documentation is a new protocol. Pin which protocol is being measured and do not compare its raw Elo numerically to the paper's older snapshot as if they were identical.

For a practical own-trial experiment, run the exact same held-out tasks, tools, budgets, context policy, models-as-judges, and paired random seeds for the base and adapted students. Report completion/valid-artifact rates, item scores, occupation/format breakdowns, cost, and uncertainty. Keep proxy/local task metrics clearly separate from benchmark metrics. Use an untouched final test set and do not tune on it. A pilot's role is to estimate signal and operational cost, not guarantee the paper's gain.

## 16. Contamination and split design

The paper's lineage dedup prevents same-lineage anchor reuse; it does not document a benchmark decontamination procedure. No contamination audit can be claimed from that mechanism alone.

Recommended additions:
- Freeze official benchmark manifests before generation; prevent their requests, rubrics, reference files, solutions, and answer-bearing derivatives from becoming retrieval seeds or training material.
- Exclude known benchmark repository/host paths from discovery and downloaded source ingestion.
- Compare normalized URLs, raw-file hashes, extracted-text fingerprints, task/criterion text overlap, and semantic near-duplicates against held-out material. Record algorithms/thresholds and exact findings; a missing manifest is “not checked,” not “clean.”
- Group splits by anchor hash, canonical source family/document, scenario lineage, and near-duplicate clusters. A different task title over the same workbook is not independent held-out work.
- Keep benchmark rubrics accessible only to the evaluator process. Keep training and evaluation storage namespaces separate.
- Recognize the limitation that such checks cannot prove a third-party teacher or base model never saw public benchmark content.

## 17. Runnable package acceptance tests

A developer can implement the contract incrementally, but must advertise the completed level accurately.

### A. Offline mechanics (mandatory before delivery)
1. Clean installation and `--help` work in the documented supported Python version.
2. A no-network/no-key fixture run traverses all stages, writes real fixture artifacts, and clearly labels all model outputs simulated.
3. Strict schemas reject malformed/missing/duplicated judge and attribution rows, invalid scores, empty labels, and mismatched scenario versions.
4. Positive/negative scoring tests cover partial credit and zero-penalty satisfaction.
5. Solver-input tests prove no rubric or author notes appear in messages, mounts, environment variables, or file-list tools.
6. A real-file byte mutation is detected before subsequent use; symlink/path traversal cannot expose private state.
7. Agent-only failure produces accepted scenario without repair; Rubric/Task failures trigger repair; all repairs require fresh audit; third recurrence of the same defect discards.
8. Hash/URL reuse is rejected even after discard; lineage survives crashes and reruns without duplicate acceptance.
9. Tool error, timeout, malformed model output, missing finish, invalid artifact, and spent budget create explicit non-success terminal states.
10. Export refuses mock traces, unmatched tool calls, stale scenario versions, missing rights/contamination checks under strict policy, and rubric leakage.
11. Resume is idempotent, logs are append-only/versioned, and no secret value enters persisted traces.
12. Counts reconcile from candidate search through accepted work, completed traces, exported examples, and each rejection reason.

### B. Authentic end-to-end pilot (required to claim usable live generation)
1. Operator supplies a bounded authorized provider configuration without exposing secrets in artifacts.
2. At least one actual public/licensed artifact is fetched or verified from supplied bytes with provenance and a nonempty parse.
3. A real teacher creates a coherent task with necessary artifacts and a grounded itemwise rubric.
4. An isolated fresh solver performs real tool calls and produces valid submitted files.
5. Real evaluation/attribution executes, with any defect repaired and re-audited.
6. At least one final authentic trajectory is exported with exact version/provenance and explicit quality policy.
7. Report actual tokens, cost, elapsed time, failed attempts, and known deviations. Do not hide failures or claim live validation when only mocked APIs were exercised.

### C. Training/evaluation trial (required to claim learning benefit)
1. Verified exact student identifier/revision and tokenizer; train/dev/test manifests frozen and disjoint.
2. Run a base-model held-out baseline before training under the same executor/evaluator settings.
3. Train on actual eligible traces, not generated task descriptions or author conversations.
4. Save configuration, training logs, checkpoint identity, and run metadata.
5. Evaluate the new checkpoint on the same untouched held-out protocol; report paired deltas and uncertainty.
6. Only a compatible pinned official protocol can produce an official-benchmark comparison; no invented Elo.

## 18. Priority order for the overhaul

P0: strict stage/state schemas; independent solver isolation; authentic filesystem/tool trajectories; signed score handling; multilabel attribution; immutable anchors; actual post-repair reaudit; visible mock/live separation; export gating; clear unsupported-feature errors.

P1: verified O*NET playbooks and provenance; SerpAPI adapter plus declared alternatives; document parsers and render checks; stable criterion identity; persistent lineage; bounded retries/cost controls; compaction trace capture; reproducible manifest; rights/contamination/split checks.

P2: 65-occupation balanced generation; richer artifact formats; distributed throughput; target-student tokenizer/masking integration; adaptation training templates; calibrated local evaluation; official benchmark adapters with pinned external assets.

Do not let a broad CLI conceal unimplemented stages. A provider stub that returns a canned final answer is not teacher solving. A text-only JSON task export is not a tool-interaction SFT corpus. A generic “judge says OK” loop is not itemwise defect attribution. A successful smoke test is not numerical reproduction.

## 19. Explicit unresolved items

The implementer must surface these rather than fabricate them:
- Original generation code and complete 20K dataset/trajectories.
- Original occupation playbook text, O*NET revision, exact per-occupation allocation.
- Concrete search limit, universal rubric length, model sampling/turn settings, retry/restart limits, full token/cost accounting.
- Original SerpAPI query configuration and web-fetch wrapper behavior.
- Original Stirrup commit, sandbox image digest, exact parser/media processing revisions, context compaction implementation/prompt.
- Exact training framework, dataset serialization, assistant/reasoning masks, packing, clipping, epsilon, precision, epoch/checkpoint selection details.
- Demonstration filtering and relationship of audit traces to collected SFT traces.
- Decontamination procedure and held-out split design.
- Historical GDPvalAA-v2 calibration assets and the paper's full 15-model Elo fitting results.
- Availability/terms/configuration of exact named teacher/judge models at experiment time.
- Whether a user-proposed student model ID actually resolves to the intended architecture/tokenizer/context behavior.

A runnable independent implementation can still be useful with explicit choices for these gaps. It cannot honestly guarantee the published numerical result.
