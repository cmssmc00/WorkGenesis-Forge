"""Revisioned synthesis, independent execution/audit, and bounded defect repair."""
from __future__ import annotations
from copy import deepcopy
from itertools import count
from pathlib import Path
import json
import shutil
from .common import ForgeError, canonical, digest, now, read_json, sha256, tree_hashes, write_json
from .schema import VERSION, criterion_satisfied, validate_attribution, validate_repair, validate_scores
from .materialize import anchor_base_for_revision, extract_public, inspect_outputs, render_scenario, verify_bundle
from . import prompts


def _runtime():
    from . import runtime
    return runtime


def aggregate_usage(root):
    totals = {"requests": 0, "charged_upper_bound_usd": 0.0, "charged_tokens": 0, "uncertain_requests": 0}
    subscription_invocations = 0
    for path in Path(root).rglob("checkpoint.json"):
        checkpoint = read_json(path)
        inherited = 0
        if checkpoint.get("gateway_recovery"):
            from .recovery import inherited_requests
            inherited = inherited_requests(checkpoint, root)
        for request in checkpoint.get("requests", [])[inherited:]:
            totals["requests"] += 1
            totals["charged_upper_bound_usd"] += request.get("charged_usd", 0)
            totals["charged_tokens"] += request.get("charged_tokens", 0)
            totals["uncertain_requests"] += request.get("status") in {"in_flight", "network_error", "uncertain"}
            subscription_invocations += request.get("provider_api") in {"claude_code_subscription", "codex_cli_subscription"}
    if subscription_invocations:
        totals.update(subscription_cli_invocations=subscription_invocations,
                      request_count_scope="Transport invocations; subscription CLI internal HTTP calls are not fully counted",
                      cost_basis="Includes subscription API-equivalent estimates, not actual subscription charges",
                      cost_guaranteed_upper_bound=False, subscription_quota_remaining=None)
    return totals


def role_config(config, role, stage_dir, campaign_root=None):
    if role not in config.get("roles", {}):
        raise ForgeError("Missing configured role: " + role)
    result = deepcopy(config["roles"][role])
    if not config.get("approve_live"):
        raise ForgeError("Live pipeline requires --approve-live and configured provider permissions")
    result["approve_live"] = True
    # Validate before persisting anything: accidental inline secrets must never enter disk state.
    _runtime().validate_config(result, private=role in {"judge", "attributor", "repairer", "repair_reviewer"})
    stage_dir = Path(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    saved = stage_dir / "role_config.json"
    source_digest = digest({k: v for k, v in result.items() if k != "approve_live"})
    if saved.exists():
        previous = read_json(saved)
        if previous["source_config_digest"] != source_digest:
            raise ForgeError("Role configuration changed; use a new campaign directory")
        return previous["effective_config"]
    if campaign_root:
        limits = config.get("campaign_budget", {})
        if not all(name in limits for name in ("max_cost_usd", "max_requests", "max_tokens")):
            raise ForgeError("campaign_budget requires max_cost_usd, max_requests, max_tokens")
        used = aggregate_usage(campaign_root)
        remaining = {"max_cost_usd": limits["max_cost_usd"] - used["charged_upper_bound_usd"],
                     "max_requests": limits["max_requests"] - used["requests"],
                     "max_total_tokens": limits["max_tokens"] - used["charged_tokens"]}
        if any(value <= 0 for value in remaining.values()):
            raise ForgeError("Campaign budget exhausted; all failed and uncertain requests remain charged")
        for key, value in remaining.items():
            result["budget"][key] = min(result["budget"][key], value)
    write_json(saved, {"source_config_digest": source_digest, "effective_config": result})
    return result


def json_role(config, role, payload, stage_dir, campaign_root, input_files=None):
    stage_dir = Path(stage_dir)
    cfg = role_config(config, role, stage_dir, campaign_root)
    if cfg.get("harness") == "stirrup":
        from .stirrup_runtime import run_json_task
        payload = prompts.bind_paper_stage(payload, cfg, input_files or {})
        return run_json_task(payload, input_files or {}, stage_dir, cfg, role=role,
                             resume=(stage_dir / "checkpoint.json").exists())
    return _runtime().run_json_role(payload, stage_dir, cfg, role=role, resume=(stage_dir / "checkpoint.json").exists())


def solver_role(config, role, scenario, stage_dir, campaign_root):
    stage_dir = Path(stage_dir)
    verify_bundle(scenario)
    cfg = role_config(config, role, stage_dir, campaign_root)
    if cfg.get("harness") == "stirrup":
        from .stirrup_runtime import run_stirrup_agent
        return run_stirrup_agent(Path(scenario) / "public", stage_dir, cfg, role=role,
                                 resume=(stage_dir / "checkpoint.json").exists())
    return _runtime().run_agent(Path(scenario) / "public", stage_dir, cfg, role=role,
                                resume=(stage_dir / "checkpoint.json").exists())


def paper_profile(config, document=None):
    """The paper protocol is explicit; old pilot runs retain their original rules."""
    return (config.get("synthesis", {}).get("protocol") == "paper_v1"
            or (document or {}).get("synthesis_protocol") == "paper_v1")


def validate_paper_binding(config, document):
    """A new protocol cannot relabel a legacy pilot bundle as a paper scenario."""
    configured = config.get("synthesis", {}).get("protocol") == "paper_v1"
    constructed = document.get("synthesis_protocol") == "paper_v1"
    if configured and not constructed:
        raise ForgeError("Paper configuration requires a scenario constructed by the paper pipeline; legacy pilot bundles cannot be migrated by changing config")
    if constructed and not configured:
        raise ForgeError("A paper scenario requires its paper pipeline configuration")
    return configured


def validate_run_binding(scenario, run_dir, require_live=True, allow_abandoned=False):
    document = verify_bundle(scenario)
    run_dir = Path(run_dir)
    report = read_json(run_dir / "run.json")
    if require_live and report.get("mode") != "live":
        raise ForgeError("This operation requires a genuine live provider run; fixtures are excluded")
    terminal_abandon = (allow_abandoned and document.get("synthesis_protocol") == "paper_v1"
                       and report.get("harness") in {"official_stirrup", "official_stirrup_subscription_bridge"} and report.get("abandoned") is True
                       and report.get("terminal_status") == "abandoned"
                       and isinstance(report.get("abandon_reason"), str) and bool(report["abandon_reason"].strip()))
    if not report.get("completed") and not terminal_abandon:
        raise ForgeError("Executor did not complete the run and submit actual deliverables")
    public_digest = digest(tree_hashes(Path(scenario) / "public"))
    if report.get("public_digest") != public_digest:
        raise ForgeError("Run does not match current scenario public inputs")
    actual = tree_hashes(run_dir / "workspace" / "output")
    if report.get("artifact_hashes") != actual:
        raise ForgeError("Run artifact bytes changed or receipt does not list actual outputs")
    trajectory = run_dir / "trajectory.json"
    if report.get("trajectory_digest") != sha256(trajectory):
        raise ForgeError("Trajectory was changed after the provider run")
    return report


def validate_audit_record(document, audit):
    expected_seal = digest({k: v for k, v in audit.items() if k != "audit_digest"})
    if audit.get("audit_digest") != expected_seal:
        raise ForgeError("Audit record changed after sealing")
    scores = validate_scores(document, audit.get("scores"))
    attribution = validate_attribution(document, scores, audit.get("attribution"))
    defects = [row for row in attribution if {"Task", "Rubric"} & set(row["labels"])]
    items = {item["id"]: item for item in document["rubric"]["items"]}
    failed = [row for row in scores if not criterion_satisfied(items[row["criterion_id"]], row["score"])]
    positive = sum(item["weight"] for item in items.values() if item["weight"] > 0)
    raw = sum(row["score"] for row in scores)
    if (audit.get("defects") != defects or audit.get("scenario_accepted") != (not defects)
            or audit.get("executor_fully_satisfied") != (not failed) or audit.get("raw_score") != raw
            or audit.get("positive_weight_total") != positive or audit.get("normalized_score") != max(0, raw / positive)):
        raise ForgeError("Audit derived fields disagree with validated scores and attribution")
    return audit


def audit_run(scenario, run_dir, out, config, campaign_root=None):
    scenario, run_dir, out = Path(scenario), Path(run_dir), Path(out)
    campaign_root = Path(campaign_root or out)
    document = verify_bundle(scenario)
    validate_paper_binding(config, document)
    report = validate_run_binding(scenario, run_dir, allow_abandoned=paper_profile(config, document))
    binding = {"bundle_digest": read_json(scenario / "manifest.json")["bundle_digest"],
               "run_receipt_sha256": sha256(run_dir / "run.json"), "artifact_hashes": report["artifact_hashes"]}
    audit_path = out / "audit.json"
    if audit_path.exists():
        previous = read_json(audit_path)
        if previous.get("binding") != binding:
            raise ForgeError("Audit checkpoint does not match scenario/run artifacts")
        return validate_audit_record(document, previous)
    paper = paper_profile(config, document)
    if not paper and any(item["verification"] == "visual" for item in document["rubric"]["items"]):
        raise ForgeError("Visual rubric criteria require a separately rendered review; text-only audit cannot approve them")
    extracted = inspect_outputs(scenario, run_dir)
    payload = {"instructions": prompts.paper_stage("stage_4_scoring", prompts.PAPER_JUDGE) if paper else prompts.JUDGE, "task": read_json(scenario / "public" / "task.json"),
               "reference_files": extract_public(scenario, config.get("evidence_char_limit", 300_000)),
               "rubric": document["rubric"], "private_reference": document.get("private_reference", {}),
               "actual_deliverables": extracted}
    if paper:
        # The paper has no hidden reference answer. The actual input packet, not
        # optional author notes or an oracle from an old pilot, is authoritative.
        payload.pop("private_reference", None)
        payload["executor_status"] = {"abandoned": report.get("abandoned", False),
                                      "abandon_reason": report.get("abandon_reason")}
        payload["round_index"] = document["revision"]
        payload["media_handling_notes"] = (
            "The parsed files report which image/chart/layout content was not inspected. "
            "Only supplied visual evidence can establish a visual property. When required media "
            "evidence is unavailable, score that property as unsatisfied; do not infer it from text."
        )
    if len(canonical(payload)) > config.get("audit_char_limit", 600_000):
        raise ForgeError("Audit context exceeds bound; use a larger approved context or smaller task")
    input_files = None
    if paper:
        input_files = {"reference_files/" + name: scenario / "public" / name
                       for name in tree_hashes(scenario / "public") if name not in {"task.json", "request.txt"}}
        input_files.update({"submitted_files/" + name: run_dir / "workspace" / "output" / name
                            for name in report["artifact_hashes"]})
    judge = json_role(config, "judge", payload, out / "judge", campaign_root, **({"input_files": input_files} if paper else {}))
    scores = validate_scores(document, judge["result"].get("scores"))
    failed = [s["criterion_id"] for s in scores if not criterion_satisfied(next(i for i in document["rubric"]["items"] if i["id"] == s["criterion_id"]), s["score"])]
    attribution_receipt = None
    if failed:
        diagnosis = json_role(config, "attributor", {**payload, "instructions": prompts.paper_stage("stage_5_attribution", prompts.PAPER_ATTRIBUTOR) if paper else prompts.ATTRIBUTOR,
                              "item_scores": scores, "unsatisfied_criterion_ids": failed}, out / "attributor", campaign_root,
                              **({"input_files": input_files} if paper else {}))
        attribution = validate_attribution(document, scores, diagnosis["result"].get("attribution"))
        attribution_receipt = diagnosis["receipt"]
    else:
        attribution = []
    defects = [item for item in attribution if {"Task", "Rubric"} & set(item["labels"])]
    positive_total = sum(i["weight"] for i in document["rubric"]["items"] if i["weight"] > 0)
    raw_score = sum(row["score"] for row in scores)
    result = {"schema_version": VERSION, "mode": "live_model_audit", "binding": binding,
              "scores": scores, "attribution": attribution, "defects": defects,
              "scenario_accepted": not defects, "executor_fully_satisfied": not failed,
              "raw_score": raw_score, "positive_weight_total": positive_total,
              "normalized_score": max(0, raw_score / positive_total),
              "missing_deliverables": extracted["missing"], "unverified_visual_files": extracted["unverified_visual_files"],
              "judge_receipt": judge["receipt"], "attribution_receipt": attribution_receipt,
              "human_reviewed": False, "created_at": now(),
              "scope": "Acceptance means no diagnosed Task/Rubric defects. It does not mean a perfect executor or human certification."}
    if paper:
        result["executor_abandoned"] = report.get("abandoned", False)
        result["abandon_reason"] = report.get("abandon_reason")
    result["audit_digest"] = digest(result)
    write_json(audit_path, result)
    return result


def verify_terminal_workflow(root, state=None):
    root = Path(root)
    state = state or read_json(root / "workflow.json")
    if state.get("status") != "accepted":
        return state
    current = root / state["accepted_scenario"]
    document = verify_bundle(current)
    if read_json(current / "manifest.json")["bundle_digest"] != state.get("accepted_bundle_digest"):
        raise ForgeError("Accepted scenario no longer matches terminal workflow")
    last = state["rounds"][-1]
    audit_path = root / last["audit"]
    run_dir = audit_path.parent.parent / "executor"
    run = validate_run_binding(current, run_dir, allow_abandoned=document.get("synthesis_protocol") == "paper_v1")
    audit = read_json(audit_path)
    expected = {"bundle_digest": state["accepted_bundle_digest"], "run_receipt_sha256": sha256(run_dir / "run.json"), "artifact_hashes": run["artifact_hashes"]}
    if audit.get("binding") != expected or audit.get("scenario_accepted") is not True or audit.get("defects"):
        raise ForgeError("Terminal audit does not certify the exact accepted revision and run")
    document = verify_bundle(current)
    validate_audit_record(document, audit)
    if last.get("audit_content_digest") != digest(audit):
        raise ForgeError("Terminal audit bytes no longer match workflow history")
    return state


def prepare_evolution_recovery(scenario, source_run, out, config, campaign_root):
    """Offline preparation for the narrowly supported HTTP 504 solver recovery.

    The caller chooses this fork explicitly; normal evolve/resume never retries
    an uncertain request. Continue with evolve using this exact output/config.
    """
    from .recovery import prepare_gateway_recovery
    from .sandbox import reject_symlink_ancestry
    scenario, out, budget_root = (Path(value).absolute() for value in (scenario, out, campaign_root))
    for path in (scenario, out, budget_root):
        reject_symlink_ancestry(path)
        if ".." in path.parts:
            raise ForgeError("Recovery paths must not contain parent traversal")
    if not out.is_relative_to(budget_root) or out == budget_root:
        raise ForgeError("Recovery output must be a new directory inside campaign_root")
    if out.exists() and any(out.iterdir()):
        raise ForgeError("Recovery output must be empty; use the existing prepared output directly")
    verify_bundle(scenario)
    stage = out / "round_000" / "executor"
    cfg = role_config(config, "solver", stage, budget_root)
    result = prepare_gateway_recovery(scenario / "public", source_run, stage, cfg, budget_root)
    write_json(out / "workflow.json", {
        "initial_bundle_digest": read_json(scenario / "manifest.json")["bundle_digest"],
        "config_digest": digest({k: v for k, v in config.items() if k != "approve_live"}),
        "campaign_budget_root": str(budget_root), "rounds": [], "defect_streaks": {}, "status": "running"
    })
    return result


def evolve_scenario(scenario, out, config, campaign_root=None):
    """Independent execution and item audit after every revision, including repairs."""
    scenario, out = Path(scenario).absolute(), Path(out).absolute()
    budget_root = Path(campaign_root).absolute() if campaign_root else out
    if not out.resolve().is_relative_to(budget_root.resolve()):
        raise ForgeError("Evolution output must be inside campaign_root so every attempt counts toward the shared budget")
    out.mkdir(parents=True, exist_ok=True)
    initial = verify_bundle(scenario)
    validate_paper_binding(config, initial)
    paper = paper_profile(config, initial)
    policy = config.get("repair", {})
    review_setting = policy.get("independent_repair_review", policy.get("independent_review", not paper))
    if type(review_setting) is not bool:
        raise ForgeError("repair.independent_repair_review must be boolean")
    if ("independent_repair_review" in policy and "independent_review" in policy
            and policy["independent_repair_review"] != policy["independent_review"]):
        raise ForgeError("Conflicting independent repair-review settings; use independent_repair_review only")
    if paper and review_setting:
        raise ForgeError("Paper pipeline does not include an additional independent repair-review stage")
    max_rounds = policy.get("max_rounds", None if paper else 6)
    if max_rounds is not None and (isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= 1000):
        raise ForgeError("repair.max_rounds must be a positive integer up to 1000 or null for paper termination rules")
    if max_rounds is None and not paper:
        raise ForgeError("Unbounded rounds are supported only by the explicit paper profile")
    if policy.get("repeated_defect_limit", 3) != 3:
        raise ForgeError("Paper profile requires discarding a criterion defective in three consecutive audited revisions")
    config_digest = digest({k: v for k, v in config.items() if k != "approve_live"})
    checkpoint = out / "workflow.json"
    if checkpoint.exists():
        state = read_json(checkpoint)
        if state["initial_bundle_digest"] != read_json(scenario / "manifest.json")["bundle_digest"] or state["config_digest"] != config_digest:
            raise ForgeError("Cannot resume with different scenario or campaign configuration")
        if state.get("campaign_budget_root", str(out)) != str(budget_root):
            raise ForgeError("Cannot resume with a different campaign budget root")
    else:
        state = {"initial_bundle_digest": read_json(scenario / "manifest.json")["bundle_digest"],
                 "config_digest": config_digest, "campaign_budget_root": str(budget_root),
                 "rounds": [], "defect_streaks": {}, "status": "running"}
        write_json(checkpoint, state)
    if state["status"] != "running":
        return verify_terminal_workflow(out, state)
    current = scenario if not state["rounds"] else out / state["rounds"][-1].get("next_revision", state["rounds"][-1]["scenario"])
    # Revisit an interrupted round: role checkpoints prevent duplicate paid requests.
    rounds = count(len(state["rounds"])) if max_rounds is None else range(len(state["rounds"]), max_rounds)
    for round_index in rounds:
        round_dir = out / ("round_%03d" % round_index)
        document = verify_bundle(current)
        solver_role(config, "solver", current, round_dir / "executor", budget_root)
        audit = audit_run(current, round_dir / "executor", round_dir / "audit", config, budget_root)
        defect_ids = {row["criterion_id"] for row in audit["defects"]}
        lineage = state.setdefault("criterion_lineage", {item["id"]: item["id"] for item in document["rubric"]["items"]}) if paper else {}
        persistent_defect_ids = {lineage.get(key, key) for key in defect_ids}
        prior = state["defect_streaks"]
        state["defect_streaks"] = {key: prior.get(key, 0) + 1 for key in persistent_defect_ids}
        record = {"round": round_index, "scenario": str(current.relative_to(out)) if current.is_relative_to(out) else str(current),
                  "audit": str((round_dir / "audit" / "audit.json").relative_to(out)), "audit_content_digest": digest(audit), "defect_ids": sorted(defect_ids),
                  "persistent_defect_ids": sorted(persistent_defect_ids),
                  "agent_only_failures": [r["criterion_id"] for r in audit["attribution"] if r["labels"] == ["Agent"]]}
        if not defect_ids:
            state["rounds"].append(record)
            state.update(status="accepted", accepted_scenario=record["scenario"], accepted_bundle_digest=read_json(current / "manifest.json")["bundle_digest"],
                         acceptance="No Task/Rubric defects; Agent failures do not trigger repair", usage=aggregate_usage(budget_root))
            write_json(checkpoint, state)
            return state
        if any(n >= 3 for n in state["defect_streaks"].values()):
            state["rounds"].append(record)
            state.update(status="discarded", reason="Same criterion remains defective in three consecutive audits", usage=aggregate_usage(budget_root))
            write_json(checkpoint, state)
            return state
        if max_rounds is not None and round_index + 1 >= max_rounds:
            state["rounds"].append(record)
            state.update(status="budget_exhausted" if paper else "discarded", reason="Configured overall repair-round budget exhausted", usage=aggregate_usage(budget_root))
            write_json(checkpoint, state)
            return state
        repair_inputs = {"reference_files/" + name: current / "public" / name
                         for name in tree_hashes(current / "public") if name not in {"task.json", "request.txt"}} if paper else None
        repair_payload = {"instructions": prompts.paper_stage("stage_6_repair", prompts.PAPER_REPAIR) if paper else prompts.REPAIR,
                          "scenario": document, "defects": audit["defects"], "anchor_evidence": extract_public(current)}
        if paper:
            repair_payload["round_index"] = round_index
            repair_payload["item_scores"] = audit["scores"]
            repair_payload["reference_deliverable"] = inspect_outputs(current, round_dir / "executor")
            repair_inputs.update({"submitted_files/" + name: round_dir / "executor" / "workspace" / "output" / name
                                  for name in tree_hashes(round_dir / "executor" / "workspace" / "output")})
        repair = json_role(config, "repairer", repair_payload, round_dir / "repair", budget_root,
                           **({"input_files": repair_inputs} if paper else {}))
        if paper and repair["result"].get("status") == "discarded":
            reason = repair["result"].get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ForgeError("An unrepairable scenario requires a concrete source-grounded reason")
            state["rounds"].append(record)
            state.update(status="discarded", reason="Unrepairable with immutable anchors: " + reason, usage=aggregate_usage(budget_root))
            write_json(checkpoint, state)
            return state
        changes = repair["result"].get("changes", [])
        if not isinstance(changes, list) or not changes or any(not isinstance(change, dict) for change in changes):
            raise ForgeError("Repair requires change records tied only to diagnosed Task/Rubric defects")
        change_origins = []
        for change in changes:
            origin = change.get("criterion_id")
            if origin in defect_ids:
                change_origins.append(origin)
                continue
            dependencies = change.get("source_defect_ids", change.get("depends_on", [])) if paper else []
            if (not isinstance(dependencies, list) or not dependencies
                    or any(dependency not in defect_ids for dependency in dependencies)):
                raise ForgeError("Repair requires change records tied only to diagnosed Task/Rubric defects")
            # A dependent requirement inherits an actual diagnosed defect's
            # identity, never its newly invented criterion ID.
            change_origins.append(sorted(dependencies)[0])
        mapping = repair["result"].get("criterion_mapping", {}) if paper else {}
        proposed = repair["result"].get("scenario")
        cache = anchor_base_for_revision(current, out / "anchor_cache")
        if paper and isinstance(proposed, dict):
            from .synthesis import stage_native_companions
            proposed = deepcopy(proposed)
            proposed["companions"], parsed_materials = stage_native_companions(
                proposed.get("companions"), repair.get("artifact_root"), cache, previous_base=cache)
            write_json(round_dir / "repair" / "material_verification.json", {
                "files": {name: {"sha256": data["sha256"], "format": data["format"],
                                  "parser_notes": data.get("parser_notes", [])} for name, data in parsed_materials.items()}})
        revised = validate_repair(document, proposed, defect_ids,
                                  **({"paper": True, "changes": changes, "criterion_mapping": mapping} if paper else {}))
        if paper:
            before_items = {item["id"]: item for item in document["rubric"]["items"]}
            after_items = {item["id"]: item for item in revised["rubric"]["items"]}
            for criterion_id in record["agent_only_failures"]:
                if after_items.get(criterion_id) != before_items[criterion_id]:
                    raise ForgeError("Paper repair must leave an Agent-only failed requirement unchanged, including linked changes")
        if review_setting:
            review = json_role(config, "repair_reviewer", {"instructions": prompts.REPAIR_REVIEW, "before": document,
                              "after": revised, "defects": audit["defects"], "changes": changes}, round_dir / "repair_review", budget_root)
            verdict = review["result"]
            if verdict.get("approved") is not True or verdict.get("weakened_criteria") != [] or not verdict.get("reasons"):
                state["rounds"].append(record)
                state.update(status="discarded", reason="Independent repair review rejected change or could not rule out task weakening", usage=aggregate_usage(budget_root))
                write_json(checkpoint, state)
                return state
        next_lineage = {item["id"]: lineage.get(item["id"], item["id"]) for item in revised["rubric"]["items"]} if paper else {}
        for old_id, new_id in mapping.items():
            if new_id is not None:
                next_lineage[new_id] = lineage.get(old_id, old_id)
        if paper:
            original_ids = {item["id"] for item in document["rubric"]["items"]}
            for change, origin in zip(changes, change_origins):
                for linked_id in [change["criterion_id"], *change.get("linked_criterion_ids", [])]:
                    if linked_id in next_lineage and linked_id not in original_ids:
                        # Removing an item and adding a linked successor must
                        # not circumvent the same-item three-round rule either.
                        next_lineage[linked_id] = lineage.get(origin, origin)
            state["criterion_lineage"] = next_lineage
        record["criterion_mapping"] = mapping
        revision_dir = out / "revisions" / ("%03d" % revised["revision"])
        if (revision_dir / "manifest.json").exists():
            if verify_bundle(revision_dir) != revised:
                raise ForgeError("Existing revision differs from saved repair response")
        else:
            render_scenario(revised, cache, revision_dir)
        record["next_revision"] = str(revision_dir.relative_to(out))
        state["rounds"].append(record)
        write_json(checkpoint, state)
        current = revision_dir
    return state


class UnsuitableSource(ForgeError):
    """A retrieved anchor failed substantive source selection."""


def validate_source_assessment(assessments, evidence, tool_inspection=None, allow_unused=False):
    """Validate the author's per-source verdicts.

    By default any source judged unusable rejects the whole attempt. With
    allow_unused, a source the author explicitly judged unusable (with a stated
    reason) is tolerated as long as at least one source is fully usable; the
    caller must then leave the unused source out of the package.
    """
    by_id = {row["record"]["id"]: row for row in evidence}
    if not isinstance(assessments, list) or len(assessments) != len(by_id):
        raise UnsuitableSource("Author must inspect and judge every source before synthesis")
    assessments = deepcopy(assessments)
    seen = set()
    for assessment in assessments:
        sid = assessment.get("source_id")
        if sid not in by_id or sid in seen:
            raise UnsuitableSource("Source suitability assessment has unknown or duplicate source ID")
        seen.add(sid)
        excerpt = assessment.get("evidence_excerpt", "")
        operations = assessment.get("professional_operations", [])
        if isinstance(operations, str):
            operations = assessment["professional_operations"] = [operations]
        if assessment.get("usable") is not True:
            if not allow_unused:
                raise UnsuitableSource("A source was judged unusable for this profession/activity")
            reason = assessment.get("occupation_fit")
            if assessment.get("usable") is not False or not isinstance(reason, str) or len(reason.strip()) < 40:
                raise UnsuitableSource("An unused source needs an explicit usable=false verdict and a specific reason")
            limitations = assessment.get("limitations")
            if isinstance(limitations, list) and all(isinstance(item, str) for item in limitations):
                assessment["limitations"] = "\n".join(item for item in limitations if item.strip())
            continue
        if len(str(assessment.get("occupation_fit", ""))) < 40 or not isinstance(operations, list) or not operations or any(not isinstance(x, str) or len(x) < 15 for x in operations):
            raise UnsuitableSource("Source assessment lacks concrete occupational operations")
        exact_excerpt = isinstance(excerpt, str) and len(excerpt) >= 24 and excerpt in by_id[sid]["extracted_text"]
        inspected_visual = False
        if by_id[sid].get("unverified_visual_content") and tool_inspection:
            citations = assessment.get("inspection_evidence", [])
            source_path = by_id[sid].get("path")
            inspected_visual = (isinstance(citations, list) and bool(citations)
                and all(isinstance(row, dict) and row.get("source_path") == source_path
                        and row.get("viewed_path") in tool_inspection.get("viewed_paths", set())
                        and isinstance(row.get("finding"), str) and len(row["finding"]) >= 24 for row in citations))
        if not exact_excerpt and not inspected_visual:
            raise UnsuitableSource("Source assessment must quote actual inspected source evidence")
        limitations = assessment.get("limitations")
        if (isinstance(limitations, list) and limitations
                and all(isinstance(item, str) and item.strip() for item in limitations)):
            assessment["limitations"] = "\n".join(limitations)
        if not isinstance(assessment.get("limitations"), str):
            raise UnsuitableSource("Source assessment must state coverage limitations")
    if not any(assessment.get("usable") is True for assessment in assessments):
        raise UnsuitableSource("A source was judged unusable for this profession/activity")
    return assessments


def author_scenario(plan, source_manifest, manifest_base, out, config, scenario_id=None, lineage_id=None, campaign_root=None):
    if paper_profile(config):
        from .synthesis import author_paper_scenario
        return author_paper_scenario(plan, source_manifest, manifest_base, out, config,
                                     scenario_id=scenario_id, lineage_id=lineage_id, campaign_root=campaign_root)
    from .sources import verify_source_record
    from .artifacts import extract_artifact
    out = Path(out)
    sources = source_manifest.get("sources", [])
    if not sources:
        raise ForgeError("Synthesis requires at least one real public anchor")
    evidence = []
    for source in sources:
        verify_source_record(source, Path(manifest_base))
        path = Path(manifest_base) / source["path"]
        parsed = extract_artifact(path)
        if len(parsed["text"].strip()) < config.get("minimum_source_chars", 200) or len(parsed["text"].split()) < 25:
            raise UnsuitableSource("Downloaded file is empty, a stub, or lacks substantive occupational evidence")
        evidence.append({"record": source, "extracted_text": parsed["text"], "tables": parsed["tables"]})
    if len(canonical(evidence)) > config.get("evidence_char_limit", 300_000):
        raise ForgeError("Source evidence exceeds configured synthesis limit; no silent truncation")
    sid = scenario_id or ("wg-" + digest({"plan": plan, "anchors": [s["sha256"] for s in sources]})[:16])
    example = {"schema_version": VERSION, "scenario_id": sid, "lineage_id": lineage_id or sid, "revision": 0, "plan": plan,
               "anchor_sources": sources,
               "request": {"title": "Professional scenario title", "role": "Occupation role", "context": "Fictional workplace context tied to the anchors", "instructions": "Ordinary workplace request", "deliverables": [{"path": "analysis.xlsx", "format": "xlsx", "description": "A suitable professionally useful deliverable"}]},
               "companions": [{"id": "internal_context", "path": "internal_context.docx", "format": "docx", "fictitious": True, "disclosure": "Fictional scenario context created for this exercise.", "spec": {"title": "Internal context", "text": "Only necessary fictional context", "tables": []}}],
               "rubric": {"items": [{"id": "c01", "weight": 5, "criterion": "Specific objectively grounded criterion", "verification": "structured", "evidence": ["anchors/source.csv, exact required facts"]}]},
               "private_reference": {"notes": "Private evidence interpretation; not exposed to solver"}}
    authored = json_role(config, "author", {"instructions": prompts.AUTHOR, "plan": plan, "sources": evidence,
                         "schema_example": example}, out / "author", campaign_root or out)
    assessments = validate_source_assessment(authored["result"].get("source_assessment"), evidence)
    write_json(out / "source_assessment.json", {"assessments": assessments, "source_hashes": [s["sha256"] for s in sources], "plan": plan})
    document = authored["result"].get("scenario")
    if not isinstance(document, dict):
        raise ForgeError("Author response must include a scenario object after source_assessment")
    for key in ("schema_version", "scenario_id", "lineage_id", "revision", "plan", "anchor_sources"):
        document[key] = deepcopy(example[key])
    initial_dir = out / "initial"
    if (initial_dir / "manifest.json").exists():
        if verify_bundle(initial_dir) != document:
            raise ForgeError("Saved author response differs from existing initial scenario")
    else:
        render_scenario(document, manifest_base, initial_dir)
    write_json(out / "generation.json", {"mode": "live", "plan": plan, "source_hashes": [s["sha256"] for s in sources],
                                       "author_receipt": authored["receipt"], "scenario": "initial"})
    return initial_dir
