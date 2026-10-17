"""Resumable occupation-to-scenario campaigns and anchor novelty registry."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import os
from urllib.parse import urlsplit
from .common import ForgeError, digest, now, read_json, write_json
from .planning import plan_tasks
from .sources import discover_sources, download_source, load_source_manifest
from .workflow import UnsuitableSource, aggregate_usage, author_scenario, evolve_scenario, verify_terminal_workflow


def register_anchors(registry_path, lineage_id, scenario_id, sources, status="reserved", scenario_summary=None):
    registry_path = Path(registry_path)
    registry = read_json(registry_path) if registry_path.exists() else {"version": 1, "entries": []}
    hashes = {s["sha256"] for s in sources}
    urls = {value for s in sources for value in (s["url"], s.get("final_url"), s.get("canonical_url")) if value}
    for entry in registry["entries"]:
        if entry["lineage_id"] != lineage_id:
            continue
        if entry["scenario_id"] == scenario_id:
            if set(entry["sha256"]) != hashes or set(entry["urls"]) != urls:
                raise ForgeError("Reserved scenario anchor set changed")
            entry["status"] = status
            if scenario_summary is not None:
                entry["scenario_summary"] = scenario_summary
            write_json(registry_path, registry)
            return entry
        if hashes & set(entry["sha256"]) or urls & set(entry["urls"]):
            raise ForgeError("Anchor URL/hash already consumed by another scenario in this lineage, including discarded attempts")
    entry = {"lineage_id": lineage_id, "scenario_id": scenario_id, "sha256": sorted(hashes), "urls": sorted(urls),
             "source_summaries": [{"id": s["id"], "title": s.get("title", ""), "rights_status": s["rights_status"]} for s in sources],
             "source_entries": [{"source_page_url": s.get("source_page_url", s["url"]), "download_url": s["url"],
                                 "canonical_url": s.get("final_url", s["url"]), "sha256": s["sha256"],
                                 "domain": urlsplit(s.get("final_url", s["url"])).hostname}
                                for s in sources],
             "status": status, "registered_at": now()}
    if scenario_summary is not None:
        entry["scenario_summary"] = scenario_summary
    registry["entries"].append(entry)
    write_json(registry_path, registry)
    return entry


def collect_anchors(plan, out, config, registry_path, lineage_id, scenario_id):
    out = Path(out)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = load_source_manifest(manifest_path)
        register_anchors(registry_path, lineage_id, scenario_id, manifest["sources"])
        return manifest_path
    policy = config.get("acquisition", {})
    max_searches = policy.get("max_searches", 4)
    max_anchors = policy.get("max_anchors", 2)
    if not isinstance(max_searches, int) or not 1 <= max_searches <= 12 or not isinstance(max_anchors, int) or not 1 <= max_anchors <= 4:
        raise ForgeError("Acquisition bounds require 1..12 searches and 1..4 anchors")
    query_templates = policy.get("query_templates", ["{profession} {activity} public dataset report filetype:pdf", "{profession} {activity} annual report spreadsheet"])
    if not isinstance(query_templates, list) or not 2 <= len(query_templates) <= 4:
        raise ForgeError("Use two to four occupational search directions")
    out.mkdir(parents=True, exist_ok=True)
    registry = read_json(registry_path) if Path(registry_path).exists() else {"entries": []}
    lineage = [entry for entry in registry["entries"] if entry["lineage_id"] == lineage_id and entry["scenario_id"] != scenario_id]
    used_urls = {url for entry in lineage for url in entry["urls"]}
    used_hashes = {value for entry in lineage for value in entry["sha256"]}
    errors, gathered = [], []
    for index in range(min(max_searches, len(query_templates))):
        query = query_templates[index].format(profession=plan.get("profession", ""), activity=plan.get("activity", ""), deliverable_family=plan.get("deliverable_family", ""))
        receipt = out / ("search_%02d.json" % index)
        if receipt.exists():
            data = read_json(receipt)
            if data.get("status") == "in_flight":
                raise ForgeError("Search request outcome is uncertain; inspect saved receipt rather than replaying a potentially paid request")
            candidates = data.get("candidates", [])
        else:
            write_json(receipt, {"query": query, "status": "in_flight"})
            try:
                candidates = discover_sources(query, config)
            except (ValueError, OSError):
                # Keep an explicit failure rather than silently constructing a source.
                write_json(receipt, {"query": query, "status": "failed", "candidates": []})
                raise
            write_json(receipt, {"query": query, "status": "complete", "candidates": candidates})
        for candidate in candidates:
            if candidate["url"] in used_urls or candidate["url"] in {s["url"] for s in gathered}:
                continue
            try:
                source = download_source(candidate, out, config)
            except (ValueError, OSError) as exc:
                errors.append({"url": candidate["url"], "error_type": type(exc).__name__})
                continue
            if source["sha256"] in used_hashes or source["sha256"] in {s["sha256"] for s in gathered}:
                continue
            gathered.append(source)
            if len(gathered) >= max_anchors:
                break
        if len(gathered) >= max_anchors:
            break
    write_json(out / "acquisition_report.json", {"download_errors": errors, "anchors_collected": len(gathered), "search_budget": max_searches})
    if not gathered:
        raise ForgeError("No new valid public anchor was acquired within the bounded search directions; configure approved source hosts or supply new source manifests")
    register_anchors(registry_path, lineage_id, scenario_id, gathered)
    write_json(manifest_path, {"sources": gathered})
    return manifest_path


def run_campaign(config, out):
    if config.get("synthesis", {}).get("protocol") == "paper_v1":
        from .paper_campaign import run_paper_campaign
        return run_paper_campaign(config, out)
    if config.get("approve_live") is not True:
        raise ForgeError("Campaigns require --approve-live before any potentially metered search or model operation")
    out = Path(out).absolute()
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".campaign.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ForgeError("Campaign is locked; verify no process is running before removing a stale .campaign.lock") from None
    os.close(descriptor)
    try:
        fingerprint = digest({k: v for k, v in config.items() if k != "approve_live"})
        path = out / "campaign.json"
        if path.exists():
            state = read_json(path)
            if state["config_digest"] != fingerprint:
                raise ForgeError("Campaign configuration changed; use a new output directory")
        else:
            plans = plan_tasks(config)
            lineage_id = config.get("lineage_id", "campaign-" + fingerprint[:16])
            state = {"version": 1, "config_digest": fingerprint, "lineage_id": lineage_id, "plans": plans,
                     "attempts": [], "finished_plan_ids": [], "status": "running", "created_at": now()}
            write_json(path, state)
        if state["status"] == "completed":
            for attempt in state["attempts"]:
                if attempt["status"] == "accepted":
                    verify_terminal_workflow((out / attempt["workflow"]).parent)
            return state
        max_restarts = config.get("repair", {}).get("max_restarts_per_plan", 1)
        if not isinstance(max_restarts, int) or not 0 <= max_restarts <= 4:
            raise ForgeError("max_restarts_per_plan must be 0..4")
        for plan in state["plans"]:
            plan_id = plan["plan_id"]
            if plan_id in state["finished_plan_ids"]:
                continue
            prior_attempts = [a for a in state["attempts"] if a["plan_id"] == plan_id]
            for attempt in range(len(prior_attempts), max_restarts + 1):
                sid = "wg-" + digest({"plan_id": plan_id, "attempt": attempt, "lineage": state["lineage_id"]})[:16]
                task_root = out / "tasks" / sid
                explicit = config.get("source_manifests", [])
                index = len(state["attempts"])
                if explicit:
                    if index >= len(explicit):
                        raise ForgeError("No unused source manifest remains; a discarded task still consumes its anchors")
                    manifest_path = Path(explicit[index])
                    manifest = load_source_manifest(manifest_path)
                    register_anchors(out / "lineage_registry.json", state["lineage_id"], sid, manifest["sources"])
                else:
                    manifest_path = collect_anchors(plan, task_root / "acquisition", config, out / "lineage_registry.json", state["lineage_id"], sid)
                    manifest = load_source_manifest(manifest_path)
                try:
                    initial = author_scenario(plan, manifest, manifest_path.parent, task_root / "synthesis", config,
                                              scenario_id=sid, lineage_id=state["lineage_id"], campaign_root=out)
                    result = evolve_scenario(initial, task_root / "evolution", config, campaign_root=out)
                except UnsuitableSource as error:
                    result = {"status": "discarded", "reason": str(error), "stage": "source_suitability"}
                    write_json(task_root / "evolution" / "workflow.json", result)
                register_anchors(out / "lineage_registry.json", state["lineage_id"], sid, manifest["sources"], result["status"])
                record = {"plan_id": plan_id, "scenario_id": sid, "attempt": attempt, "status": result["status"],
                          "workflow": str((task_root / "evolution" / "workflow.json").relative_to(out))}
                state["attempts"].append(record)
                if result["status"] == "accepted" or attempt == max_restarts:
                    state["finished_plan_ids"].append(plan_id)
                    write_json(path, state)
                    break
                write_json(path, state)
        state.update(status="completed", usage=aggregate_usage(out), counts={kind: sum(a["status"] == kind for a in state["attempts"]) for kind in ("accepted", "discarded")})
        write_json(path, state)
        return state
    finally:
        lock.unlink(missing_ok=True)
