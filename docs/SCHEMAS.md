# Contracts, states, and trust boundaries

## Scenario bundle

A sealed revision contains:

```
scenario/
  manifest.json
  public/
    task.json
    request.txt
    anchors/<source-id>.<extension>
    companions/<task-specific filenames>
  private/
    scenario.json
```

`schema_version` is `workgenesis-forge/v1`. The private scenario carries `scenario_id`, `lineage_id`, `revision`, `plan`, `anchor_sources`, `request`, `companions`, `rubric.items`, and optional `private_reference`.

The public request has an allowlist of `title`, `role`, `context`, `instructions`, and `deliverables`. Private rubric/reference/transcript keys cannot be copied through arbitrary model fields. A deliverable has `path`, `format`, and `description`; paths are relative to the output directory. There is no fixed filename list, file count, or reasoning graph. The current safety bound is 1–24 outputs.

Anchors are exact raw public bytes with URL and SHA-256 lineage. Companions are generated scenario context with `fictitious=true` and an explicit fiction disclosure rendered into each file, including CSV and slide decks. They are never described as downloaded documents. A model still needs semantic review to establish that all fictional assertions are properly distinguished from real-source facts.

`manifest.json` seals the public and private file trees plus anchor digests. Seals detect later changes; they are not signatures proving an honest original author or diligent reviewer. Trusted operator/host assumptions remain.

## Source selection

Before synthesis, every source is parsed and must contain substantive material. The author returns an envelope:

```json
{
  "source_assessment": [
    {
      "source_id": "actual-source-id",
      "usable": true,
      "occupation_fit": "A specific explanation tied to the planned profession and activity.",
      "professional_operations": ["Concrete professional analysis supported by this evidence"],
      "evidence_excerpt": "An exact excerpt from the supplied extracted source text",
      "limitations": "Coverage, reliability, and scope limits"
    }
  ],
  "scenario": {"schema_version": "workgenesis-forge/v1"}
}
```

The abbreviated scenario above is not a complete usable example; `demo` writes a full one. Assessments must cover every source exactly once, cite real inspected text, and identify concrete occupational operations. A positive judgment is model evidence, not a human certification. Rejected candidate data and consumed anchor lineage remain recorded.

## Rubric

Each item has stable `id`, signed nonzero `weight`, `criterion`, nonempty `evidence`, and `verification` of `text`, `structured`, or `visual`.

- Positive weight: award in `[0, weight]`; full satisfaction is `score == weight`.
- Negative weight: award in `[weight, 0]`; full satisfaction is `score == 0`.
- The audit must cover every item exactly once with nonempty rationale and evidence.
- Every unsatisfied item receives one or more of `Agent`, `Task`, `Rubric` plus explanation/evidence.
- The normalized local score is `max(0, sum(awarded_scores) / sum(positive_weights))`. It is not an official benchmark score or Elo.

Audit uses actual parsed output contents. Missing files and unsupported parsers fail appropriately. Visual criteria cannot be certified from text; this implementation stops rather than inventing a visual review.

## State machine

1. Parse sources and inspect suitability.
2. Author and materialize revision zero.
3. Execute with an independent solver using public inputs only.
4. Audit actual output files item by item.
5. Attribute all non-full criteria.
6. If no Task/Rubric labels remain, accept the scenario even if Agent failures remain.
7. Otherwise repair only Task/Rubric defects, obtain independent anti-weakening review, materialize a new revision, then return to step 3.
8. Discard after the same criterion remains defective through three consecutive audits, after the configured total round limit, or if independent repair review rejects the revision.
9. A bounded campaign restart uses the same occupational plan and new source anchors. Both accepted and discarded attempts consume their anchors within the lineage.

Repairs cannot change anchor records, lineage/identity, occupational plan, criterion IDs/weights, or deliverable contracts. Pure Agent-failure and satisfied rubric items cannot change. A reviewer additionally checks semantic weakening and oracle leakage that mechanical equality checks cannot establish.

Acceptance of a repaired revision is impossible without re-execution and re-audit. Completed workflows are revalidated on resume, including revision bytes, executor outputs, trajectory digest, and final audit binding.

## Role separation

The author receives only the occupational plan and source evidence. The solver/student/teacher receives the materialized public request and files, with a separate workspace. The judge and attributor receive private criteria plus parsed references and actual deliverables under a separate upload permission. The repairer sees diagnosed scenario defects; the independent repair reviewer compares before/after. No role inherits another role's messages automatically. The author does not produce a fake teacher trace.

## Real run and SFT records

Runtime writes `checkpoint.json`, `trajectory.json`, `run.json`, and actual files under `workspace/output/`. Receipts include model/endpoint identity, public and artifact digests, successful/failed/uncertain requests, token accounting, and conservative cost reservations. Receipts are locally produced audit evidence, not remote provider signatures.

Training admission requires a real complete final-teacher tool trace, exact run/source/audit bindings, explicit source rights, and an operator-signed semantic/safety/contamination review. Tool-call IDs, ordering, pairings, public prompt, and successful final artifact-bound `finish` are revalidated. A compacted trace is rejected by flat export because the full-history training context would differ from what the teacher actually saw.

Qualified records retain paths to their reviewed originals and are signed using an operator key that never enters model tools. Moving the source runs requires a deliberate requalification; editing paths or bytes invalidates the signature. Export JSONL contains `messages`, native tool schemas in `tools`, and metadata with source/lineage groups. It does not concatenate author, judge, repair, or oracle conversations into training examples.
