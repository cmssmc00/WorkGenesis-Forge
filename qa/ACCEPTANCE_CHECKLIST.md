# WorkGenesis Forge independent acceptance checklist

Prepared 2026-10-13. Scope: the new repository, not an empirical reproduction claim. No paid model calls, publication, credential setup, or training runs are authorized for this QA.

## Paper-specific semantic contract

Primary source: https://arxiv.org/html/2609.39325v1, sections 3, 4.1, Appendix A and D.

A constructed work unit combines an occupational request, real public anchor files, synthesized supporting files, and an itemwise rubric. Anchors remain unchanged and are tracked by URL and byte hash to prevent lineage reuse. The independent execution agent receives the task and references without the authoring rubric. Scoring inspects the actual submitted files. Every sub-full-credit item receives Agent, Task, and/or Rubric attribution. Only Task/Rubric defects drive scenario repair; agent capability failures remain failures. Repaired tasks are executed and audited again. Persistent scenario defects cause discard after the bounded verification sequence. The paper's reported experiment uses 20,000 trajectories over 65 occupations, GLM-5.2, Stirrup, and Qwen3.6-35B-A3B SFT. A local fixture run cannot establish these empirical results.

## Independent engineering gates

1. Installation and CLI
   - Clean virtual environment can install from declared pinned dependencies.
   - Help and documented offline command work from outside repository root.
   - Missing dependencies and absent Docker fail clearly, without claimed success.
   - Paths with spaces, interrupted run, duplicate invocation, and resume are checked.
2. Truthful execution modes
   - Fixture/mock data is explicitly labeled in manifest and result metadata.
   - Production mode cannot silently fall back to templates, fixtures, fake judge decisions, or host execution.
   - Production export rejects mock traces, incomplete attempts, failed audits, and missing artifacts.
   - Training/evaluation profiles are marked unrun; user-requested alternate model is distinguished from the paper model.
3. Input and source handling
   - Fetching honors public-only policy, URL scheme restrictions, redirect validation, byte/time bounds, and private-network protections.
   - Anchor payload is parsed and hashed from actual bytes, with source and download URLs.
   - Provenance, use terms, publisher/date, and unresolved rights are retained.
   - Benchmark tasks, hidden answers, and GPL source code are not bundled into a clean implementation or training export without an appropriate explicit basis.
4. Scenario and artifact quality
   - More than one file/output combination works; tasks are not hard-coded to one document family.
   - Native XLSX/DOCX/PPTX/PDF files open with independent parsers; rendered pages are readable.
   - Independent arithmetic recomputes numeric ground truth from public/reference inputs.
   - Every required output has coverage, every criterion is grounded, and all references resolve.
   - No placeholder text, empty outputs, model-written fake file paths, or score-by-filename heuristics pass.
5. Trust boundary
   - Untrusted generated code runs only in a bounded container, never via host exec/eval/shell.
   - Container has no network, dropped capabilities, no-new-privileges, resource/timeout limits, read-only root, non-root identity, minimal mounts, and no host credentials.
   - Reference/task material is separate from private audit/rubric/state material.
   - Symlinks, path traversal, archive bombs, oversized files, and tool injection are tested.
6. Audit and repair
   - Audit is separate from author/worker and sees actual parsed artifacts plus sources.
   - Required rubric coverage and score bounds are validated by code.
   - Every non-full item gets a nonempty valid attribution set with evidence.
   - Repair cannot change source bytes, silently delete criteria, lower criterion maxima, erase failures, or certify itself.
   - Post-repair artifacts are regenerated and reevaluated; exhausted budget is failure, not acceptance.
7. Trace and export integrity
   - Real tool calls/results, model identity, messages, inputs/outputs, token/cost budgets, and timestamps are recorded.
   - Hashes are verified at resume, audit, and export boundaries.
   - Worker trajectories do not contain hidden rubric/gold/repair metadata or credentials.
   - Export schemas validate and preserve tools; no text-only invented chain is misrepresented as a real trajectory.
   - Split assignment is lineage/source-family aware to avoid leakage; counts and omissions reconcile.
8. Reproducibility and docs
   - Offline runs are repeatable across independent output directories.
   - The accepted/rejected/repair transitions each have local, no-paid-call tests.
   - Budget exhaustion, malformed provider response, download failure, and missing renderer are honest failures.
   - Documentation states exactly what was tested, mocked, unrun, and needed for authorized live operation.

## Status

Verification results and unrun limits are recorded in `ACCEPTANCE_REPORT.md`. This checklist describes the intended checks; it does not independently certify every runtime or empirical result.
