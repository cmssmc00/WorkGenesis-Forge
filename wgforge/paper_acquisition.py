"""Paper stage 1: tool-driven search/inspection with controller-verified provenance."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from .artifacts import extract_artifact
from .common import ForgeError, digest, read_json, safe_path, sha256, write_json
from .sources import load_source_manifest, verify_source_record

CHECKS = ("occupation_fit", "format_fit", "substance", "veracity", "novelty", "solvability", "safety")


def lineage_memory(registry_path, lineage_id, scenario_id):
    registry = read_json(registry_path) if Path(registry_path).exists() else {"entries": []}
    return [entry for entry in registry["entries"]
            if entry["lineage_id"] == lineage_id and entry["scenario_id"] != scenario_id]


def validate_anchor_selection(response, out, lineage, required_formats=None):
    """An LLM may select a download; it cannot manufacture a download receipt."""
    result = response.get("result", {})
    if result.get("status") == "failure":
        from .workflow import UnsuitableSource
        raise UnsuitableSource("No usable real anchors: " + str(result.get("failure_reason", "search directions exhausted")))
    selected = result.get("anchors")
    if result.get("status") != "success" or not isinstance(selected, list) or not selected:
        raise ForgeError("Discovery must return success with at least one inspected anchor")
    downloads = response.get("downloads", [])
    download_root = Path(response["download_root"])
    by_hash = {}
    for record in downloads:
        verify_source_record(record, download_root)
        group = by_hash.setdefault(record["sha256"], {})
        group[record["url"]] = record
    used_hashes = {value for row in lineage for value in row.get("sha256", [])}
    used_urls = {value for row in lineage for value in row.get("urls", [])}
    records, seen = [], set()
    for selection in selected:
        if not isinstance(selection, dict):
            raise ForgeError("Selected anchors must be manifest objects")
        matches = list(by_hash.get(selection.get("sha256"), {}).values())
        direct = selection.get("download_url")
        matches = [row for row in matches if row["url"] == direct]
        if len(matches) != 1:
            raise ForgeError("Selected anchor does not match an actual controller-recorded download")
        source = deepcopy(matches[0])
        if source["sha256"] in used_hashes | seen or {source["url"], source.get("final_url")} & used_urls:
            raise ForgeError("An anchor URL/hash has already been consumed by this lineage")
        if required_formats and Path(source["path"]).suffix.lower().lstrip(".") not in required_formats:
            raise ForgeError("Selected anchor does not match the requested file formats")
        checks = selection.get("suitability", {})
        if set(checks) != set(CHECKS) or any(not isinstance(checks[name], dict)
                or checks[name].get("pass") is not True or not str(checks[name].get("reason", "")).strip() for name in CHECKS):
            raise ForgeError("Every accepted anchor must pass all seven documented suitability checks with reasons")
        if not str(selection.get("content_summary", "")).strip() or not selection.get("key_fields"):
            raise ForgeError("Accepted anchors require a content summary and concrete fields/sections")
        original = safe_path(download_root, source["path"], True)
        try:
            parsed = extract_artifact(original)
        except ValueError as exc:
            from .workflow import UnsuitableSource
            raise UnsuitableSource("Selected source cannot be parsed: " + str(exc)) from exc
        target = safe_path(out, source["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha256(target) != source["sha256"]:
            raise ForgeError("Existing anchor cache differs from downloaded evidence")
        if not target.exists():
            shutil.copyfile(original, target)
        source.update(source_page_url=source.get("source_page_url", source["url"]),
                      download_url=source["url"], canonical_url=source.get("final_url", source["url"]),
                      domain=urlsplit(source.get("final_url", source["url"])).hostname,
                      content_summary=selection["content_summary"], key_fields=selection["key_fields"],
                      suitability=checks, license_or_terms=selection.get("license_or_terms", "unknown"),
                      parser_notes=parsed.get("parser_notes", []), parsed_verified=True)
        verify_source_record(source, out)
        seen.add(source["sha256"])
        records.append(source)
    return {"sources": records, "discovery": {"status": "success", "search_log": result.get("search_log", []),
            "rejected_candidates": result.get("rejected_candidates", []),
            "notes_for_next_stage": result.get("notes_for_next_stage", ""),
            "receipt": response.get("receipt"), "lineage_memory_digest": digest(lineage)}}


def collect_paper_anchors(plan, out, config, registry_path, lineage_id, scenario_id, *, campaign_root=None):
    from .campaign import register_anchors
    from .workflow import json_role
    out = Path(out)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = load_source_manifest(manifest_path)
        register_anchors(registry_path, lineage_id, scenario_id, manifest["sources"])
        return manifest_path
    memory = lineage_memory(registry_path, lineage_id, scenario_id)
    prompt = (Path(__file__).parent / "data/paper_prompts/stage_1_discovery.txt").read_text()
    adapter = """Execution/serialization bindings for this implementation:
Use the supplied occupation_playbook, synthesis instruction and lineage_memory. The
actual sandbox paths and tool capabilities appear in your system message. Read
and inspect downloaded files with code_exec and view_image; iterate search and
suitability judgment instead of selecting solely from snippets. Return your final
summary in output/result.json and submit it with finish. Its shape is:
{status: success|failure, anchors: [{download_url: <the exact web_fetch URL>,
sha256: <actual controller-provided hash>, content_summary: <specific content>,
key_fields: [<actual fields/sections>], license_or_terms: <terms note>,
suitability: {occupation_fit:{pass:true,reason:...}, format_fit:{pass:true,reason:...},
substance:{pass:true,reason:...}, veracity:{pass:true,reason:...},
novelty:{pass:true,reason:...}, solvability:{pass:true,reason:...},
safety:{pass:true,reason:...}}}], search_log: [...], rejected_candidates: [...],
notes_for_next_stage: ..., failure_reason: <if failed>}.
Controller download receipts, not your declarations, establish the real bytes and
provenance. Never alter downloaded anchors. A failure needs no fabricated anchor.
There is no fixed minimum above one usable real file; two to four strong files
are typical, but never pad the set. URLs/hashes already in lineage are forbidden.
"""
    payload = {"instructions": prompt + "\n" + adapter, "plan": plan,
               "synthesis_instruction": plan.get("instruction", "Construct one grounded occupational work scenario."),
               "occupation_playbook": plan.get("occupation_playbook", plan), "lineage_memory": memory,
               "required_file_formats": config.get("acquisition", {}).get("required_formats", []),
               "max_search_attempts": config.get("acquisition", {}).get("max_searches", 8)}
    budget_root = Path(campaign_root) if campaign_root is not None else Path(registry_path).parent
    response = json_role(config, "researcher", payload, out / "researcher", budget_root, input_files={})
    manifest = validate_anchor_selection(response, out, memory, payload["required_file_formats"])
    register_anchors(registry_path, lineage_id, scenario_id, manifest["sources"])
    write_json(out / "metadata.json", manifest["discovery"] | {"anchors": manifest["sources"]})
    write_json(manifest_path, manifest)
    return manifest_path
