"""Truthful offline wiring demonstration using immutable real USGS bytes.

This programmatic control is not a model solve, semantic audit, or SFT example.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import csv
import shutil
from .common import ForgeError, digest, read_json, sha256, tree_hashes, write_json
from .artifacts import extract_artifact, write_artifact
from .materialize import render_scenario, verify_bundle
from .schema import VERSION


def calculate(csv_path, threshold=Decimal("4.5")):
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(row["net"] != "us" for row in rows):
        raise ForgeError("Demo expects the exact bundled USGS-only catalog snapshot")
    days = defaultdict(list)
    valid = []
    for row in rows:
        try:
            magnitude = Decimal(row["mag"])
        except Exception:
            magnitude = None
        if magnitude is not None and not magnitude.is_finite():
            magnitude = None
        days[row["time"][:10]].append((row, magnitude))
        if magnitude is not None:
            valid.append((row, magnitude))
    if not valid:
        raise ForgeError("No valid magnitudes in source")
    daily = []
    for day, entries in sorted(days.items()):
        magnitudes = [m for _, m in entries if m is not None]
        daily.append([day, len(entries), len(magnitudes), sum(m >= threshold for m in magnitudes),
                      str(max(magnitudes)) if magnitudes else None])
    largest = max(valid, key=lambda pair: (pair[1], pair[0]["id"]))
    facts = {"event_count": len(rows), "valid_magnitude_count": len(valid),
             "mean_magnitude": str((sum(m for _, m in valid) / len(valid)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
             "threshold": str(threshold), "threshold_event_count": sum(m >= threshold for _, m in valid),
             "largest_event_id": largest[0]["id"], "largest_magnitude": str(largest[1]),
             "observed_start": min(row["time"] for row in rows), "observed_end": max(row["time"] for row in rows),
             "catalog_scope": "Earliest 200 USGS catalog=us events returned by the bounded 2026-10-01 to 2026-10-11 query; not a complete census"}
    return facts, daily, rows


def run_demo(out, package_root=None):
    out = Path(out)
    root = Path(package_root or Path(__file__).resolve().parents[1])
    if out.exists() and any(out.iterdir()):
        raise ForgeError("Demo output is not empty; choose a new --out directory")
    manifest_path = root / "examples" / "anchors" / "usgs_manifest.json"
    if package_root is None and not manifest_path.exists():
        from .bundled import packaged_anchor_root
        manifest_path = packaged_anchor_root() / "usgs_manifest.json"
    manifest = read_json(manifest_path)
    source = manifest["sources"][0]
    source_path = manifest_path.parent / source["path"]
    if sha256(source_path) != source["sha256"]:
        raise ForgeError("Bundled source integrity check failed")
    facts, daily, rows = calculate(source_path)
    from .planning import plan_tasks
    plans = plan_tasks({"planning": {"count": 1, "seed": 13, "professions": ["Geoscientists, Except Hydrologists and Geographers"]}})
    plan = plans[0]
    task = {"schema_version": VERSION, "scenario_id": "usgs-offline-control", "lineage_id": "usgs-oct2026-pilot", "revision": 0,
            "plan": plan, "anchor_sources": manifest["sources"],
            "request": {"title": "Prepare a catalog-monitoring briefing for a fictional geoscience team", "role": "Geoscientist",
                "context": "The fictional Meridian Geoscience Desk is testing its daily catalog-monitoring workflow. The attached USGS snapshot is a bounded 200-event response, not a complete earthquake census. This exercise is retrospective analysis and is not a public warning or risk prediction.",
                "instructions": "Read the immutable USGS CSV and the fictional monitoring-policy document. Reconcile counts, valid magnitudes, the two-decimal mean magnitude using half-up rounding, daily totals, threshold counts, and the maximum-magnitude event ID (break ties by lexicographically greatest ID). Create an auditable workbook retaining all source rows, a short PDF briefing that cites the exact catalog scope and distinguishes fictional policy from USGS facts, and a machine-readable JSON summary. Do not extrapolate the bounded sample into regional hazard estimates or treat this as safety advice.",
                "deliverables": [{"path": "catalog_analysis.xlsx", "format": "xlsx", "description": "Source-event ledger, daily aggregates, and summary metrics"},
                                 {"path": "briefing.pdf", "format": "pdf", "description": "Decision-useful briefing with scope limits and source attribution"},
                                 {"path": "summary.json", "format": "json", "description": "Counts, mean, threshold, maximum event, observed timestamps, and catalog scope"}]},
            "companions": [{"id": "fictional_monitoring_policy", "path": "monitoring_policy.docx", "format": "docx", "fictitious": True,
                "disclosure": "Fictional scenario document. Meridian Geoscience Desk and this internal monitoring policy are invented for the exercise.",
                "spec": {"title": "Internal retrospective monitoring policy", "text": "Use magnitude 4.5 as an internal workload-routing threshold. Count events at or above that value. The threshold is fictional and is not a hazard, safety, or evacuation criterion. Preserve the source query's coverage limit. Report missing magnitudes separately and retain source IDs."}}],
            "rubric": {"items": [
                {"id": "c01", "weight": 5, "criterion": "Counts and mean magnitude correctly reconcile with all saved source rows and stated rounding", "verification": "structured", "evidence": ["USGS CSV mag and id columns"]},
                {"id": "c02", "weight": 5, "criterion": "Daily totals and threshold counts apply the fictional policy consistently", "verification": "structured", "evidence": ["USGS CSV time and mag columns", "companions/monitoring_policy.docx threshold 4.5"]},
                {"id": "c03", "weight": 3, "criterion": "Briefing accurately states bounded query scope and attributes USGS", "verification": "text", "evidence": ["Anchor URL limit=200 and source manifest attribution"]},
                {"id": "c04", "weight": -5, "criterion": "Penalize portraying the fictional threshold as an official hazard or evacuation criterion", "verification": "text", "evidence": ["companions/monitoring_policy.docx explicit fictional scope"]}]},
            "private_reference": {"facts": facts, "daily_rows": daily, "reference_kind": "Programmatic offline control, never a teacher trajectory"}}
    render_scenario(task, manifest_path.parent, out / "scenario")
    output = out / "control" / "workspace" / "output"
    output.mkdir(parents=True)
    write_json(output / "summary.json", facts)
    metrics = [[key, value] for key, value in facts.items()]
    write_artifact(output / "catalog_analysis.xlsx", {"title": "Retrospective catalog analysis", "tables": [
        {"name": "Summary", "headers": ["metric", "value"], "rows": metrics},
        {"name": "Daily", "headers": ["date", "events", "valid_magnitudes", "magnitude_at_least_4_5", "maximum_magnitude"], "rows": daily},
        {"name": "Source Events", "headers": list(rows[0]), "rows": [list(row.values()) for row in rows]}]})
    briefing = ("U.S. Geological Survey (USGS) source: " + source["url"] + "\n" + facts["catalog_scope"] + ".\n"
                + "The source snapshot contains " + str(facts["event_count"]) + " events, including " + str(facts["valid_magnitude_count"]) + " valid magnitudes. Mean magnitude is " + facts["mean_magnitude"] + ".\n"
                + str(facts["threshold_event_count"]) + " events meet the fictional internal routing threshold of magnitude 4.5. This threshold is not a hazard, evacuation, or public warning criterion.\n"
                + "The largest recorded magnitude is " + facts["largest_magnitude"] + " for event " + facts["largest_event_id"] + ".\n"
                + "Review daily workload using the workbook. This bounded source cannot establish comprehensive regional frequency or risk. Catalog values may later be revised by USGS.")
    write_artifact(output / "briefing.pdf", {"title": "Fictional Geoscience Desk: Retrospective Briefing", "text": briefing})
    workbook = extract_artifact(output / "catalog_analysis.xlsx")
    extracted_pdf = extract_artifact(output / "briefing.pdf")
    checks = {"real_anchor_unchanged": sha256(out / "scenario" / "public" / "anchors" / (source["id"] + ".csv")) == source["sha256"],
              "json_recomputed": read_json(output / "summary.json") == calculate(source_path)[0],
              "source_rows_preserved": len(next(t for t in workbook["tables"] if t["name"] == "Source Events")["rows"]) == len(rows),
              "daily_counts_reconcile": sum(r[1] for r in daily) == len(rows),
              "pdf_has_scope": "not a complete census" in extracted_pdf["text"],
              "pdf_has_attribution": "U.S. Geological Survey" in extracted_pdf["text"]}
    if not all(checks.values()):
        raise ForgeError("Offline control verification failed")
    verify_bundle(out / "scenario")
    result = {"mode": "offline_programmatic_control", "checks": checks, "checks_passed": True,
              "source_sha256": source["sha256"], "facts": facts, "artifact_hashes": tree_hashes(output),
              "model_called": False, "scenario_semantically_accepted": False, "teacher_trajectory_created": False,
              "sft_export_eligible": False, "visual_layout_reviewed": False,
              "note": "These deterministic checks validate software wiring and selected facts, not independent model capability or professional semantic quality."}
    write_json(out / "verification.json", result)
    return result
