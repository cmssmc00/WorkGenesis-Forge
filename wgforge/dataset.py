"""Conservative real-trajectory admission, optional calibration, and SFT export."""
from __future__ import annotations
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
from .common import ForgeError, canonical, digest, now, read_json, sha256, tree_hashes, write_json
from .materialize import verify_bundle
from .workflow import audit_run, solver_role, validate_run_binding, validate_audit_record


def _key(env):
    value = os.environ.get(env, "")
    if len(value) < 32:
        raise ForgeError("Set " + env + " to a private operator review key of at least 32 characters; never put it in model configuration")
    return value.encode()


def _bindings(scenario, run_dir, audit):
    return {"scenario_bundle_digest": read_json(Path(scenario) / "manifest.json")["bundle_digest"],
            "run_receipt_sha256": sha256(Path(run_dir) / "run.json"),
            "trajectory_sha256": sha256(Path(run_dir) / "trajectory.json"), "audit_sha256": sha256(audit)}


def sign_review(scenario, run_dir, audit, out, reviewer, key_env, attestations):
    """Operator action only. No model or fixture can manufacture this approval."""
    if not reviewer or not isinstance(reviewer, str):
        raise ForgeError("An identifiable human reviewer is required")
    required = {"source_rights", "semantic_correctness", "privacy_safety", "no_benchmark_overlap"}
    if set(attestations) != required or not all(value is True for value in attestations.values()):
        raise ForgeError("Human review must explicitly attest source rights, semantic correctness, privacy/safety, and benchmark separation")
    validate_run_binding(scenario, run_dir)
    payload = {"bindings": _bindings(scenario, run_dir, audit), "reviewer": reviewer,
               "attestations": attestations, "created_at": now(), "scope": "Exact scenario, run, trajectory, and audit bytes only"}
    record = {"payload": payload, "signature": hmac.new(_key(key_env), canonical(payload).encode(), hashlib.sha256).hexdigest()}
    write_json(out, record)
    return record


def verify_review(path, bindings, key_env):
    review = read_json(path)
    expected = hmac.new(_key(key_env), canonical(review.get("payload")).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(review.get("signature", ""))) or review["payload"].get("bindings") != bindings:
        raise ForgeError("Human review signature or artifact binding does not match")
    if not all(review["payload"].get("attestations", {}).get(k) is True for k in ("source_rights", "semantic_correctness", "privacy_safety", "no_benchmark_overlap")):
        raise ForgeError("Human review lacks required attestations")
    return review


def _validate_audit(scenario, run_dir, audit_path):
    audit = validate_audit_record(verify_bundle(scenario), read_json(audit_path))
    run = validate_run_binding(scenario, run_dir)
    binding = {"bundle_digest": read_json(Path(scenario) / "manifest.json")["bundle_digest"],
               "run_receipt_sha256": sha256(Path(run_dir) / "run.json"), "artifact_hashes": run["artifact_hashes"]}
    if audit.get("binding") != binding or audit.get("mode") != "live_model_audit":
        raise ForgeError("Audit must be a live independent audit of these exact artifacts")
    if not audit.get("scenario_accepted") or audit.get("defects") or audit.get("missing_deliverables"):
        raise ForgeError("Scenario defects or missing deliverables block trajectory admission")
    if audit.get("unverified_visual_files"):
        raise ForgeError("Unverified chart/image content blocks training admission pending rendered review")
    return audit, run


def validate_trajectory(trajectory, run, public_task):
    if (trajectory.get("model_transport") in {"claude_subscription", "codex_subscription"}
            or run.get("model_transport") in {"claude_subscription", "codex_subscription"}
            or trajectory.get("native_message_replay") is False
            or run.get("native_message_replay") is False):
        raise ForgeError("Subscription bridge traces require a dedicated training adapter; JSON action proposals are not native model tool messages")
    if trajectory.get("context_events"):
        raise ForgeError("Compacted trajectories need a context-faithful segmented training adapter; flat SFT export is blocked")
    messages = trajectory.get("messages")
    if trajectory.get("mode") != "live" or trajectory.get("role") != run.get("role") or not trajectory.get("isolated_context"):
        raise ForgeError("Trajectory mode, role, or context isolation is invalid")
    if not isinstance(messages, list) or len(messages) < 4 or messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise ForgeError("Trajectory must begin with its isolated system and public task prompts")
    if messages[1].get("content") != canonical(public_task) or digest(messages[:2]) != run.get("prompt_digest"):
        raise ForgeError("Trajectory prompt does not match exact public task")
    pending, used, finished = {}, set(), False
    for message in messages[2:]:
        if finished:
            raise ForgeError("Trajectory contains content after successful finish")
        role = message.get("role")
        if role == "assistant":
            if pending:
                raise ForgeError("Assistant responded before all tool results were recorded")
            for call in message.get("tool_calls", []):
                identifier = call.get("id")
                if not identifier or identifier in used or call.get("type") != "function":
                    raise ForgeError("Duplicate or malformed tool call ID")
                function = call.get("function", {})
                if not isinstance(function.get("arguments"), str) or not function.get("name"):
                    raise ForgeError("Malformed tool call")
                try:
                    if not isinstance(json.loads(function["arguments"]), dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ForgeError("Tool arguments must be a JSON object") from None
                pending[identifier] = function["name"]
                used.add(identifier)
        elif role == "tool":
            identifier = message.get("tool_call_id")
            if not pending or identifier != next(iter(pending)):
                raise ForgeError("Orphan, duplicate, or out-of-order tool result")
            name = pending.pop(identifier)
            try:
                result = json.loads(message.get("content", ""))
            except (ValueError, TypeError):
                raise ForgeError("Tool result must be recorded as JSON") from None
            if name == "finish" and isinstance(result, dict) and result.get("finished") is True:
                if pending or result.get("artifact_hashes") != run.get("artifact_hashes"):
                    raise ForgeError("Successful finish must bind all actual artifacts and end the tool batch")
                finished = True
        else:
            raise ForgeError("Unexpected role inside isolated executor trace")
    if pending or not finished:
        raise ForgeError("Trajectory has unresolved tools or lacks successful finish")
    return True


def qualify(scenario, run_dir, audit_path, review_path, out, key_env="WG_REVIEW_KEY", min_score=None, split="train"):
    from .sources import require_production_rights
    if split not in {"train", "validation", "test"}:
        raise ForgeError("Split must be train, validation, or test")
    document = verify_bundle(scenario)
    audit, run = _validate_audit(scenario, run_dir, audit_path)
    if run.get("role") != "teacher":
        raise ForgeError("Only an independent final teacher collection run may enter this SFT dataset")
    for source in document["anchor_sources"]:
        require_production_rights(source)
    bindings = _bindings(scenario, run_dir, audit_path)
    verify_review(review_path, bindings, key_env)
    if min_score is not None and (not isinstance(min_score, (int, float)) or isinstance(min_score, bool) or not 0 <= min_score <= 1):
        raise ForgeError("Optional minimum score must be between zero and one")
    if min_score is not None and audit["normalized_score"] < min_score:
        raise ForgeError("Teacher trajectory does not meet the configured optional score threshold")
    trajectory = read_json(Path(run_dir) / "trajectory.json")
    if trajectory.get("mode") != "live" or not trajectory.get("messages"):
        raise ForgeError("Fixture, mock, programmatic, or empty trajectories cannot be exported")
    validate_trajectory(trajectory, run, read_json(Path(scenario) / "public" / "task.json"))
    messages = trajectory["messages"]
    if not any(m.get("role") == "tool" for m in messages) or not any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages):
        raise ForgeError("A complete real tool-using trajectory is required")
    if not run.get("request_receipts") or not any(r.get("status") == "succeeded" for r in run["request_receipts"]):
        raise ForgeError("No successful provider receipts were recorded")
    record = {"schema_version": "workgenesis-forge/qualified-v1", "mode": "live_qualified", "scenario_id": document["scenario_id"],
              "lineage_id": document["lineage_id"], "split": split, "bindings": bindings,
              "anchor_hashes": sorted(s["sha256"] for s in document["anchor_sources"]),
              "scenario": str(Path(scenario).absolute()), "run_dir": str(Path(run_dir).absolute()),
              "audit": str(Path(audit_path).absolute()), "review": str(Path(review_path).absolute()),
              "qualification_policy": {"min_score": min_score, "source": "Operator adaptation; paper does not publish a trajectory-score filter"},
              "normalized_score": audit["normalized_score"], "created_at": now()}
    record["signature"] = hmac.new(_key(key_env), canonical(record).encode(), hashlib.sha256).hexdigest()
    write_json(out, record)
    return record


def calibration_summary(scores, low=0.2, high=0.8):
    if not 0 <= low < high <= 1 or not scores or any(isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= 1 for s in scores):
        raise ForgeError("Calibration requires bounded scores and 0 <= low < high <= 1")
    mean = sum(scores) / len(scores)
    return {"scores": scores, "n": len(scores), "mean_score": mean, "low": low, "high": high,
            "selection": "too_hard" if mean < low else "too_easy" if mean > high else "selected",
            "method": "Optional student-aware adaptation; not reported WorkGenesis methodology", "changes_scenario": False}


def calibrate(scenario, out, config, count=4):
    if not isinstance(count, int) or not 1 <= count <= 16:
        raise ForgeError("Student calibration count must be 1..16")
    scores = []
    out = Path(out)
    for index in range(count):
        run_dir = out / ("student_%03d" % index)
        solver_role(config, "student", scenario, run_dir, out)
        audit = audit_run(scenario, run_dir, out / ("audit_%03d" % index), config, out)
        if audit["defects"]:
            raise ForgeError("Calibration discovered scenario defects; repair independently before selection")
        scores.append(audit["normalized_score"])
    policy = config.get("calibration", {})
    result = calibration_summary(scores, policy.get("low", .2), policy.get("high", .8))
    result["scenario_bundle_digest"] = read_json(Path(scenario) / "manifest.json")["bundle_digest"]
    result["model"] = config["roles"]["student"]["model"]
    write_json(out / "calibration.json", result)
    return result


def export_sft(records, out, key_env="WG_REVIEW_KEY", heldout_manifest=None):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ForgeError("Export directory must be empty")
    heldout = read_json(heldout_manifest) if heldout_manifest else {"anchor_hashes": [], "lineage_ids": [], "ngram_texts": []}
    heldout_hashes, heldout_lineages = set(heldout.get("anchor_hashes", [])), set(heldout.get("lineage_ids", []))
    if any(not isinstance(t, str) for t in heldout.get("ngram_texts", [])):
        raise ForgeError("Heldout decontamination texts must be strings")
    def shingles(text):
        words = re.findall(r"[a-z0-9]+", text.casefold())
        return {" ".join(words[i:i+13]) for i in range(max(0, len(words)-12))}
    heldout_ngrams = set().union(*(shingles(t) for t in heldout.get("ngram_texts", []))) if heldout.get("ngram_texts") else set()
    admitted, group_splits, seen_trajectories = [], {}, set()
    for record_path in records:
        record = read_json(record_path)
        unsigned = {k: v for k, v in record.items() if k != "signature"}
        expected = hmac.new(_key(key_env), canonical(unsigned).encode(), hashlib.sha256).hexdigest()
        if record.get("mode") != "live_qualified" or not hmac.compare_digest(expected, str(record.get("signature", ""))):
            raise ForgeError("Invalid qualification signature or fixture record")
        scenario, run_dir = Path(record["scenario"]), Path(record["run_dir"])
        document = verify_bundle(scenario)
        _validate_audit(scenario, run_dir, record["audit"])
        bindings = _bindings(scenario, run_dir, record["audit"])
        if bindings != record["bindings"]:
            raise ForgeError("Qualified source artifacts changed")
        verify_review(record["review"], bindings, key_env)
        from .sources import require_production_rights
        for source in document["anchor_sources"]:
            require_production_rights(source)
        if set(record["anchor_hashes"]) & heldout_hashes or record["lineage_id"] in heldout_lineages:
            raise ForgeError("Held-out benchmark/source overlap blocks export")
        groups = ["lineage:" + record["lineage_id"]] + ["anchor:" + x for x in record["anchor_hashes"]]
        for group in groups:
            if group in group_splits and group_splits[group] != record["split"]:
                raise ForgeError("Source/lineage group appears across different dataset splits")
            group_splits[group] = record["split"]
        trajectory = read_json(run_dir / "trajectory.json")
        validate_trajectory(trajectory, read_json(run_dir / "run.json"), read_json(scenario / "public" / "task.json"))
        text = canonical(trajectory["messages"])
        blocked = ("gdpval-aa", "aa-briefcase-lite", "halberd consulting")
        if any(word in text.casefold() for word in blocked) or shingles(text) & heldout_ngrams:
            raise ForgeError("Possible benchmark contamination blocks export; investigate independently")
        fingerprint = digest(trajectory["messages"])
        if fingerprint in seen_trajectories:
            raise ForgeError("Duplicate trajectory in collection")
        seen_trajectories.add(fingerprint)
        admitted.append({"messages": trajectory["messages"], "tools": trajectory.get("tools", []),
                         "metadata": {"scenario_id": record["scenario_id"], "lineage_id": record["lineage_id"],
                                      "split": record["split"], "bindings": bindings, "anchor_hashes": record["anchor_hashes"],
                                      "mode": "live_qualified", "loss_policy": "Assistant tokens only; tool and prompt tokens masked in supplied recipe"}})
    if not admitted:
        raise ForgeError("No real qualified trajectories; demo controls cannot become training data")
    out.mkdir(parents=True)
    for split in ("train", "validation", "test"):
        rows = [row for row in admitted if row["metadata"]["split"] == split]
        (out / (split + ".jsonl")).write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"schema_version": "workgenesis-forge/sft-v1", "counts": {s: sum(r["metadata"]["split"] == s for r in admitted) for s in ("train", "validation", "test")},
                "files": tree_hashes(out), "anchor_hashes": sorted({h for r in admitted for h in r["metadata"]["anchor_hashes"]}),
                "lineage_ids": sorted({r["metadata"]["lineage_id"] for r in admitted}), "heldout_manifest_sha256": sha256(heldout_manifest) if heldout_manifest else None,
                "contamination_limitations": "Exact anchor/lineage and supplied 13-gram checks only. No access to private benchmark tasks; no-match is not proof of absence.",
                "contamination_status": "checked_against_supplied_exclusions_only" if heldout_manifest and (heldout_hashes or heldout_lineages or heldout_ngrams) else "not_checked_no_heldout_corpus",
                "training_executed": False, "evaluation_executed": False}
    write_json(out / "manifest.json", manifest)
    return manifest
