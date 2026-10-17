"""Frozen local paired evaluation; never a substitute for official GDPval scoring."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from .common import ForgeError, digest, read_json, write_json
from .materialize import verify_bundle
from .workflow import audit_run, solver_role


def evaluate_local(eval_manifest, train_manifest, base_config, tuned_config, out):
    evaluation = read_json(eval_manifest)
    training = read_json(train_manifest)
    if evaluation.get("purpose") != "local_heldout_evaluation" or not evaluation.get("scenarios"):
        raise ForgeError("Use an explicit frozen local_heldout_evaluation manifest")
    if training.get("schema_version") != "workgenesis-forge/sft-v1" or "anchor_hashes" not in training or "lineage_ids" not in training:
        raise ForgeError("Training export manifest must include anchor/lineage exclusion lists")
    for role in ("judge", "attributor"):
        if base_config.get("roles", {}).get(role) != tuned_config.get("roles", {}).get(role):
            raise ForgeError("Paired evaluation requires identical frozen judge/attribution configuration")
    if base_config["roles"]["student"] == tuned_config["roles"]["student"]:
        raise ForgeError("Base and tuned student configurations must identify different model checkpoints")
    out = Path(out)
    binding = {"eval": digest(evaluation), "train": digest(training),
               "base": digest({k:v for k,v in base_config.items() if k != "approve_live"}),
               "tuned": digest({k:v for k,v in tuned_config.items() if k != "approve_live"})}
    freeze = out / "freeze.json"
    if freeze.exists() and read_json(freeze) != binding:
        raise ForgeError("Frozen evaluation changed; start a new evaluation rather than mixing runs")
    write_json(freeze, binding)
    rows = []
    for index, entry in enumerate(evaluation["scenarios"]):
        scenario = Path(eval_manifest).parent / entry["path"]
        document = verify_bundle(scenario)
        actual = read_json(scenario / "manifest.json")["bundle_digest"]
        if actual != entry.get("bundle_digest"):
            raise ForgeError("Frozen evaluation scenario bytes changed")
        if document["lineage_id"] in training["lineage_ids"] or {s["sha256"] for s in document["anchor_sources"]} & set(training["anchor_hashes"]):
            raise ForgeError("Evaluation and training share source anchors or scenario lineage")
        pair = {"scenario_id": document["scenario_id"], "bundle_digest": actual}
        # Alternate presentation/execution order without changing any scoring contract.
        order = [("base", base_config), ("tuned", tuned_config)]
        if index % 2:
            order.reverse()
        for label, config in order:
            root = out / ("task_%04d" % index) / label
            solver_role(config, "student", scenario, root / "run", out)
            audit = audit_run(scenario, root / "run", root / "audit", config, out)
            if audit["defects"]:
                raise ForgeError("Frozen evaluation has diagnosed task/rubric defects; report separately, never repair based on benchmark model output")
            pair[label] = audit["normalized_score"]
        pair["delta"] = pair["tuned"] - pair["base"]
        rows.append(pair)
        write_json(out / "partial.json", {"pairs": rows, "completed": False})
    result = {"benchmark": "local_frozen_artifact_rubrics", "official_gdpval": False, "completed": True,
              "pairs": rows, "mean_base": sum(r["base"] for r in rows)/len(rows),
              "mean_tuned": sum(r["tuned"] for r in rows)/len(rows),
              "mean_delta": sum(r["delta"] for r in rows)/len(rows),
              "interpretation": "Paired local rubric scores only. Not official Elo, win rate, or the paper's reported GDPval result."}
    write_json(out / "evaluation.json", result)
    return result
