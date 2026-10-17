"""Accepted-task target campaigns for the disclosed WorkGenesis construction loop."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import os

from .common import ForgeError, canonical, digest, now, read_json, sha256, write_json
from .planning import plan_tasks, compact_plans, hydrate_plan
from .materialize import verify_bundle
from .workflow import (UnsuitableSource, aggregate_usage, author_scenario, evolve_scenario,
                       solver_role, validate_run_binding, verify_terminal_workflow)
from .paper_acquisition import collect_paper_anchors, lineage_memory
from .sources import load_source_manifest


def _counts(state):
    return {"target_tasks": len(state["plans"]),
            "accepted_tasks": sum(row["status"] == "accepted" for row in state["attempts"]),
            "discarded_scenarios": sum(row["status"] == "discarded" for row in state["attempts"]),
            "acquisition_failures": sum(row["status"] == "source_unavailable" for row in state["attempts"]),
            "completed_trajectories": sum(bool(row.get("trajectory")) for row in state["attempts"]),
            "finished_plans": len(state["finished_plan_ids"])}


def _accepted_bundle(out, record):
    evolution = (out / record["workflow"]).parent
    result = verify_terminal_workflow(evolution)
    relative = Path(result["accepted_scenario"])
    scenario = relative if relative.is_absolute() else evolution / relative
    verify_bundle(scenario)
    return scenario


def collect_demonstration(config, out, record):
    """Collect one independent complete trace after scenario acceptance.

    No unpublished success-score, HMAC review, or student-calibration filter is
    silently imposed. This is authentic raw research data, not a claim that the
    paper's undisclosed SFT serialization has been reproduced.
    """
    scenario = _accepted_bundle(out, record)
    run_dir = out / "tasks" / record["scenario_id"] / "demonstration"
    if record.get("trajectory"):
        run = validate_run_binding(scenario, run_dir)
        if sha256(run_dir / "trajectory.json") != record["trajectory_sha256"]:
            raise ForgeError("A collected demonstration changed after it was recorded")
        return run
    solver_role(config, "teacher", scenario, run_dir, out)
    run = validate_run_binding(scenario, run_dir)
    if not run.get("completed"):
        raise ForgeError("A demonstration must finish and submit actual files")
    record.update(trajectory=str((run_dir / "trajectory.json").relative_to(out)),
                  trajectory_sha256=sha256(run_dir / "trajectory.json"),
                  demonstration_run=str((run_dir / "run.json").relative_to(out)))
    return run


def _write_dataset_index(out, state):
    entries = []
    for record in state["attempts"]:
        if record.get("trajectory"):
            scenario = _accepted_bundle(out, record)
            run_receipt = read_json(out / record["demonstration_run"])
            subscription = run_receipt.get("model_transport") in {"claude_subscription", "codex_subscription"}
            entries.append({"scenario_id": record["scenario_id"], "plan_id": record["plan_id"],
                            "scenario": str(scenario.relative_to(out)),
                            "bundle_digest": read_json(scenario / "manifest.json")["bundle_digest"],
                            "trajectory": record["trajectory"], "sha256": record["trajectory_sha256"],
                            "run_receipt": record["demonstration_run"],
                            "quality_filter": "none_disclosed_in_paper",
                            "mode": "subscription_bridge_trace" if subscription else "authentic_provider_trace",
                            "model_transport": run_receipt.get("model_transport", "anthropic_messages"),
                            "native_message_replay": not subscription})
    destination = out / "dataset"
    destination.mkdir(exist_ok=True)
    (destination / "trajectories.jsonl").write_text("".join(canonical(entry) + "\n" for entry in entries), encoding="utf-8")
    write_json(destination / "manifest.json", {"count": len(entries), "target": len(state["plans"]),
               "format": "References to run-specific model transport, observable messages, tool histories and context segments",
               "subscription_bridge_note": "Subscription traces contain serialized history/action bridges and observable CLI events, not lossless native API messages",
               "training_executed": False, "official_benchmark_executed": False,
               "sft_tokenization": "Not inferred: the paper does not disclose exact serialization, filtering or loss masks"})


def run_paper_campaign(config, out):
    from .campaign import register_anchors
    if config.get("approve_live") is not True:
        raise ForgeError("Paper campaigns require --approve-live; planning/preflight are offline")
    from .paper_profile import validate_paper_profile
    validate_paper_profile(config)
    out = Path(out).absolute()
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".campaign.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ForgeError("Campaign is locked; verify that no process is running before removing a stale lock") from None
    os.close(descriptor)
    state = None
    path = out / "campaign.json"
    try:
        fingerprint = digest({key: value for key, value in config.items() if key != "approve_live"})
        if path.exists():
            state = read_json(path)
            if state["config_digest"] != fingerprint:
                raise ForgeError("Campaign configuration changed; use a new output directory")
        else:
            schedule = compact_plans(plan_tasks(config))
            state = {"version": 2, "protocol": "paper_v1", "config_digest": fingerprint,
                     "lineage_id": config.get("lineage_id", "campaign-" + fingerprint[:16]),
                     "plans": schedule["plans"], "playbooks": schedule["playbooks"],
                     "attempts": [], "finished_plan_ids": [], "active_attempt": None,
                     "status": "running", "created_at": now()}
            transport = next(iter(config.get("roles", {}).values()), {}).get("provider")
            if transport in {"claude_subscription", "codex_subscription"}:
                cli_name = "Claude Code" if transport == "claude_subscription" else "Codex CLI"
                state.update(model_transport=transport, outer_harness="official_stirrup",
                    reproduction_note="Paper stages retained; official " + cli_name + " subscription uses serialized-history/structured-action bridging",
                    billing_note="USD workload estimates do not measure subscription bills or remaining quota")
            write_json(path, state)
        collection = config.get("trajectory_collection", {}).get("enabled", True)
        if state["status"] == "completed":
            for record in state["attempts"]:
                if record["status"] == "accepted":
                    _accepted_bundle(out, record)
                    if collection:
                        collect_demonstration(config, out, record)
            return state
        state["status"] = "running"
        registry = out / "lineage_registry.json"
        maximum = config.get("construction", {}).get("max_attempts_per_plan")
        if maximum is not None and (type(maximum) is not int or maximum < 1):
            raise ForgeError("max_attempts_per_plan must be null or a positive operational limit")
        for saved_plan in state["plans"]:
            plan = hydrate_plan(saved_plan, state.get("playbooks", {}))
            if plan["plan_id"] in state["finished_plan_ids"]:
                continue
            previous = [row for row in state["attempts"] if row["plan_id"] == plan["plan_id"]]
            accepted = next((row for row in previous if row["status"] == "accepted"), None)
            while accepted is None:
                number = len(previous)
                if maximum is not None and number >= maximum:
                    state.update(status="incomplete", stop_reason="construction_attempt_budget_exhausted")
                    break
                sid = "wg-" + digest({"plan_id": plan["plan_id"], "attempt": number, "lineage": state["lineage_id"]})[:16]
                task_root = out / "tasks" / sid
                if state.get("active_attempt"):
                    active = state["active_attempt"]
                    if active["scenario_id"] != sid:
                        raise ForgeError("Saved active attempt does not match the next uncompleted plan")
                    construction_plan = active["construction_plan"]
                else:
                    construction_plan = deepcopy(plan)
                    construction_plan["lineage_memory"] = lineage_memory(registry, state["lineage_id"], sid)
                    state["active_attempt"] = {"scenario_id": sid, "construction_plan": construction_plan}
                    write_json(path, state)
                result = None
                sources = []
                try:
                    manifest_path = collect_paper_anchors(construction_plan, task_root / "acquisition", config,
                                                         registry, state["lineage_id"], sid)
                    manifest = load_source_manifest(manifest_path)
                    sources = manifest["sources"]
                    initial = author_scenario(construction_plan, manifest, manifest_path.parent,
                                              task_root / "synthesis", config, scenario_id=sid,
                                              lineage_id=state["lineage_id"], campaign_root=out)
                    result = evolve_scenario(initial, task_root / "evolution", config, campaign_root=out)
                    if result["status"] not in {"accepted", "discarded"}:
                        state.update(status="incomplete", stop_reason=result.get("reason", result["status"]))
                        break
                except UnsuitableSource as error:
                    result = {"status": "discarded" if sources else "source_unavailable", "reason": str(error)}
                    write_json(task_root / "evolution" / "workflow.json", result)
                summary = {"occupation": plan["profession"], "instruction": plan.get("instruction", ""),
                           "outcome": result["status"]}
                summary_scenario = task_root / "synthesis" / "initial"
                if result["status"] == "accepted":
                    accepted_path = Path(result["accepted_scenario"])
                    summary_scenario = accepted_path if accepted_path.is_absolute() else task_root / "evolution" / accepted_path
                if (summary_scenario / "manifest.json").exists():
                    document = verify_bundle(summary_scenario)
                    summary.update(title=document["request"]["title"], context=document["request"]["context"],
                                   deliverables=document["request"]["deliverables"])
                if sources:
                    register_anchors(registry, state["lineage_id"], sid, sources, result["status"], scenario_summary=summary)
                record = {"plan_id": plan["plan_id"], "scenario_id": sid, "attempt": number,
                          "status": result["status"], "workflow": str((task_root / "evolution/workflow.json").relative_to(out))}
                state["attempts"].append(record)
                state["active_attempt"] = None
                previous.append(record)
                state["counts"] = _counts(state)
                write_json(path, state)
                if result["status"] == "accepted":
                    accepted = record
            if accepted is None:
                break
            if collection:
                collect_demonstration(config, out, accepted)
            state["finished_plan_ids"].append(plan["plan_id"])
            state["counts"] = _counts(state)
            write_json(path, state)
            _write_dataset_index(out, state)
        if len(state["finished_plan_ids"]) == len(state["plans"]):
            state.update(status="completed", stop_reason="accepted_target_and_requested_trajectories_reached")
        state.update(counts=_counts(state), usage=aggregate_usage(out))
        write_json(path, state)
        _write_dataset_index(out, state)
        return state
    except Exception as error:
        if state is not None and state.get("status") != "completed":
            state.update(status="interrupted", error_type=type(error).__name__, counts=_counts(state), usage=aggregate_usage(out))
            write_json(path, state)
        raise
    finally:
        lock.unlink(missing_ok=True)
