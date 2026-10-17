"""Paper stages 2a and 2b, separated by real native-file construction and inspection.

The authoring agents use Stirrup code/view tools. This module verifies file
provenance and stage ordering; it does not substitute templates or a fixed answer
generator for the professional work performed by those agents.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PurePosixPath
import json
import shutil

from .artifacts import extract_artifact
from .common import ForgeError, canonical, digest, read_json, safe_path, sha256, write_json
from .materialize import render_companions, render_scenario, verify_bundle
from .schema import VERSION, FORMATS, finite_number, relative_file, validate_scenario
from .sources import verify_source_record
from . import prompts


def _copy_verified(source, target, expected):
    source, target = Path(source), Path(target)
    if source.is_symlink() or not source.is_file() or sha256(source) != expected:
        raise ForgeError("Material file is missing or differs from its recorded SHA-256")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink() or sha256(target) != expected:
            raise ForgeError("An existing material cache entry has different bytes")
    else:
        shutil.copyfile(source, target)
    if sha256(target) != expected:
        raise ForgeError("Material copy failed integrity verification")


def stage_native_companions(companions, artifact_root, intake, previous_base=None):
    """Reopen native outputs and cache by content, preserving bytes across stages.

    A repair may reference an unchanged companion in previous_base. A changed
    native attachment must actually have been submitted by the repair tool run.
    """
    if not isinstance(companions, list) or len(companions) > 32:
        raise ForgeError("Paper companion manifest must contain zero to 32 files")
    normalized, parsed, seen = [], {}, set()
    for source_record in companions:
        if not isinstance(source_record, dict):
            raise ForgeError("Every native companion requires a manifest record")
        record = deepcopy(source_record)
        name = relative_file(record.get("path"))
        artifact_path = relative_file(record.get("artifact_path"))
        if name in seen or record.get("format") not in FORMATS or PurePosixPath(name).suffix.lower() != "." + record["format"]:
            raise ForgeError("Native companion filename/format must be unique and supported")
        seen.add(name)
        expected = record.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ForgeError("Native companion requires the computed SHA-256 of its actual bytes")
        source = None
        for base in (artifact_root, previous_base):
            if base is None:
                continue
            candidate = safe_path(Path(base), artifact_path)
            if candidate.exists() and not candidate.is_symlink() and sha256(candidate) == expected:
                source = candidate
                break
        if source is None:
            raise ForgeError("Declared native companion was not submitted, or its byte digest is wrong: " + name)
        content_path = "generated/" + expected + "/" + PurePosixPath(name).name
        destination = safe_path(Path(intake), content_path)
        _copy_verified(source, destination, expected)
        extracted = extract_artifact(destination)
        if destination.stat().st_size == 0:
            raise ForgeError("A companion is empty: " + name)
        # The creation origin is private bookkeeping, never an inserted heading,
        # spreadsheet column, or artificial instruction in the worker's packet.
        record.update(artifact_path=content_path, sha256=expected, fictitious=True,
                      disclosure_scope="private",
                      disclosure="Fictional companion material constructed for this occupational scenario; provenance is private metadata.")
        record.pop("spec", None)
        normalized.append(record)
        parsed[name] = {"sha256": expected, **extracted}
    return normalized, parsed


def _source_evidence(source_manifest, manifest_base, intake, config):
    from .workflow import UnsuitableSource
    sources = source_manifest.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise ForgeError("Paper synthesis requires at least one real public anchor")
    evidence, input_files = [], {}
    for source in sources:
        verify_source_record(source, Path(manifest_base))
        path = safe_path(Path(manifest_base), source["path"], True)
        parsed = extract_artifact(path)
        # Thin parsed text is not proof a scanned/image/media source is empty.
        # Such files must be inspected with the code/view tools, not excluded
        # from occupations whose real evidence is naturally visual or audiovisual.
        if (not parsed.get("unverified_visual_content")
                and len(parsed["text"].strip()) < config.get("minimum_source_chars", 200)
                and not any(table.get("rows") for table in parsed.get("tables", []))):
            raise UnsuitableSource("Downloaded evidence is empty or lacks substantive occupational content")
        _copy_verified(path, safe_path(intake, source["path"]), source["sha256"])
        name = "anchors/" + source["id"] + path.suffix.lower()
        input_files[name] = path
        evidence.append({"record": source, "path": name, "extracted_text": parsed["text"],
                         "tables": parsed["tables"], "parser_notes": parsed.get("parser_notes", []),
                         "unverified_visual_content": parsed.get("unverified_visual_content", False)})
    if len(canonical(evidence)) > config.get("evidence_char_limit", 300_000):
        raise ForgeError("Source evidence exceeds configured synthesis limit; no silent truncation")
    return sources, evidence, input_files


def _prompt_anchor_manifest(source_manifest):
    """Keep source evidence while leaving discovery execution receipts on disk.

    Project at the prompt boundary so previously cached manifests are safe too.
    In particular, the discovery run can contain per-request CLI catalogues and
    accounting metadata that do not help construct workplace attachments.
    Never mutate that original provenance/audit record or the source bytes.
    """
    projected = deepcopy(source_manifest)
    discovery = projected.get("discovery")
    if isinstance(discovery, dict):
        discovery.pop("receipt", None)
    return projected


def _recorded_visual_inspections(stage, receipt):
    """Accept visual citations only when an image was actually returned to the agent."""
    trajectory = Path(stage) / "trajectory.json"
    if not trajectory.exists() or sha256(trajectory) != receipt.get("trajectory_digest"):
        return {}
    messages = read_json(trajectory).get("messages", [])
    calls, viewed = {}, set()
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") == "view_image":
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                    except (TypeError, ValueError):
                        continue
                    if isinstance(arguments.get("path"), str):
                        calls[call.get("id")] = arguments["path"]
        elif message.get("role") == "tool" and message.get("tool_call_id") in calls:
            content = message.get("content")
            if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") in {"image_url", "image"} for part in content):
                viewed.add(calls[message["tool_call_id"]])
    return {"viewed_paths": viewed}


def _validate_material_manifest(result, sources, evidence, tool_inspection=None):
    from .workflow import validate_source_assessment
    if not isinstance(result, dict):
        raise ForgeError("Companion construction must return its private material manifest")
    if any(name in result for name in ("request", "rubric", "scenario")):
        raise ForgeError("Paper stage 2a must finish reference materials before the request/rubric is authored")
    assessment = validate_source_assessment(result.get("source_assessment"), evidence, tool_inspection=tool_inspection)
    context = result.get("scenario_context")
    fields = ("organization", "worker_role", "period", "business_situation", "intended_work_product")
    if not isinstance(context, dict) or any(not isinstance(context.get(key), str) or not context[key].strip() for key in fields):
        raise ForgeError("Material construction must define the occupational scenario context")
    if not isinstance(result.get("gap_analysis"), list) or not isinstance(result.get("known_limitations"), list):
        raise ForgeError("Material manifest requires explicit gaps and limitations, including empty lists")
    if not isinstance(result.get("companions"), list):
        raise ForgeError("Material manifest requires a companions list; zero is valid")
    for companion in result["companions"]:
        for field in ("purpose", "provenance_note"):
            if not isinstance(companion.get(field), str) or not companion[field].strip():
                raise ForgeError("Native companions require a purpose and grounding provenance")
        for field in ("derived_from", "assumptions", "external_facts_used"):
            if not isinstance(companion.get(field), list):
                raise ForgeError("Native companion manifest requires " + field)
        verified = companion.get("verified", {})
        if any(verified.get(key) is not True for key in ("opens_and_parses", "internally_consistent", "cross_file_consistent")):
            raise ForgeError("Companion creator must finish its parse and consistency checks before task authoring")
    return assessment


def author_paper_scenario(plan, source_manifest, manifest_base, out, config,
                          scenario_id=None, lineage_id=None, campaign_root=None):
    from .workflow import json_role
    out, manifest_base = Path(out), Path(manifest_base)
    intake = out / "material_intake"
    synthesis = config.get("synthesis", {})
    target = synthesis.get("rubric_size_target", 40)
    if isinstance(target, bool) or not isinstance(target, int) or not 20 <= target <= 100:
        raise ForgeError("Paper synthesis.rubric_size_target must be an integer from 20 to 100")
    for role in ("companion_author", "task_author"):
        if config.get("roles", {}).get(role, {}).get("harness") != "stirrup":
            raise ForgeError("Paper authoring requires Stirrup code/view tools for role " + role)
    sources, evidence, input_files = _source_evidence(source_manifest, manifest_base, intake, config)
    synthesis_request = {"instruction": plan.get("instruction") or "Create one grounded " + str(plan.get("profession", "professional")) + " task; construct evidence, then optional attachments, then the request and rubric.",
                         "occupation_playbook": plan.get("occupation_playbook", plan),
                         "plan": plan, "lineage_memory": plan.get("lineage_memory", source_manifest.get("lineage_memory", []))}
    made = json_role(config, "companion_author", {
        "instructions": prompts.paper_stage("stage_2a_companions", prompts.PAPER_COMPANIONS),
        "synthesis_request": synthesis_request, "anchor_manifest": _prompt_anchor_manifest(source_manifest),
        "sources": evidence, "supported_native_formats": sorted(FORMATS)},
        out / "companions", campaign_root or out, input_files=input_files)
    assessment = _validate_material_manifest(made["result"], sources, evidence,
                                             _recorded_visual_inspections(out / "companions", made["receipt"]))
    companions, parsed_companions = stage_native_companions(made["result"]["companions"], made.get("artifact_root"), intake)
    # Reparse the exact packet that will be handed to the task author. Generated
    # attachments are copied, never reconstructed from the language-model prose.
    packet = out / "material_packet"
    rendered_companions = render_companions(companions, packet / "companions", paper=True, artifact_base=intake)
    if {key: value["sha256"] for key, value in parsed_companions.items()} != {key: value["sha256"] for key, value in rendered_companions.items()}:
        raise ForgeError("A material changed between construction and task authoring")
    material_files = dict(input_files)
    material_files.update({"companions/" + name: packet / "companions" / name for name in rendered_companions})
    material_manifest = {key: deepcopy(value) for key, value in made["result"].items() if key != "companions"}
    material_manifest["companions"] = companions
    material_manifest["source_hashes"] = [item["sha256"] for item in sources]
    material_manifest["file_hashes"] = {name: sha256(path) for name, path in material_files.items()}
    write_json(out / "material_manifest.json", material_manifest)
    write_json(out / "source_assessment.json", {"assessments": assessment, "source_hashes": material_manifest["source_hashes"], "plan": plan})
    request_schema = {"title": "Professional work title", "role": "Worker role", "context": "Workplace context and requester/audience",
                      "instructions": "Ordinary workplace request, with concrete scope and constraints",
                      "deliverables": [{"path": "work_product.xlsx", "format": "xlsx", "description": "A useful completed work product"}]}
    authored = json_role(config, "task_author", {
        "instructions": prompts.paper_stage("stage_2b_task_rubric", prompts.PAPER_TASK_AUTHOR),
        "synthesis_request": synthesis_request, "reference_file_manifest": material_manifest,
        "reference_files_parsed": {**{item["path"]: item for item in evidence},
                                   **{"companions/" + key: value for key, value in rendered_companions.items()}},
        "rubric_size_target": target, "request_schema": request_schema,
        "supported_native_formats": sorted(FORMATS),
        "solver_capabilities": config.get("solver_capabilities", {"harness": "Stirrup", "tools": ["code_exec", "view_image"], "public_research": False})},
        out / "task_author", campaign_root or out, input_files=material_files)
    result = authored["result"]
    if not isinstance(result, dict) or "companions" in result or "anchor_sources" in result:
        raise ForgeError("Paper stage 2b may write only request, rubric and private authoring metadata")
    rubric = deepcopy(result.get("rubric", {}))
    if not isinstance(rubric, dict) or len(rubric.get("items", [])) != target:
        raise ForgeError("Task author must return exactly the configured rubric_size_target criteria")
    # Appendix D calls the criterion's maximum/penalty points "score";
    # Forge calls them "weight". Keep the submitted author artifacts unchanged.
    for item in rubric["items"]:
        if isinstance(item, dict) and "score" in item:
            score = finite_number(item["score"], "Criterion score")
            if "weight" in item and finite_number(item["weight"], "Criterion weight") != score:
                raise ForgeError("Criterion score and weight disagree")
            item["weight"] = item.pop("score")
    sid = scenario_id or ("wg-" + digest({"plan": plan, "anchors": [s["sha256"] for s in sources]})[:16])
    research = result.get("research", {"enabled": False, "scope": ""})
    task_manifest = result.get("task_manifest", {})
    if not isinstance(task_manifest, dict) or not isinstance(research, dict):
        raise ForgeError("Task author requires a task manifest and explicit research policy")
    if bool(task_manifest.get("research_required", False)) != bool(research.get("enabled", False)):
        raise ForgeError("Research policy and task manifest must agree")
    if research.get("enabled") and not config.get("solver_capabilities", {}).get("public_research"):
        raise ForgeError("Task asks for public research unavailable in configured solver capabilities")
    document = {"schema_version": VERSION, "synthesis_protocol": "paper_v1", "scenario_id": sid,
                "lineage_id": lineage_id or sid, "revision": 0, "plan": deepcopy(plan),
                "anchor_sources": deepcopy(sources), "request": result.get("request"),
                "companions": companions, "rubric": rubric, "research": research,
                "authoring_metadata": {"task_manifest": task_manifest, "fact_sheet": result.get("fact_sheet", {}),
                                       "material_manifest": material_manifest}}
    validate_scenario(document)
    # The independent task author receives a read-only copy. Check the local
    # packet again too, so neither a helper nor a resumed stage can replace it.
    if {name: sha256(path) for name, path in material_files.items()} != material_manifest["file_hashes"]:
        raise ForgeError("Task authoring changed a reference file")
    initial = out / "initial"
    if (initial / "manifest.json").exists():
        if verify_bundle(initial) != document:
            raise ForgeError("Saved task-author response differs from the existing initial scenario")
    else:
        render_scenario(document, intake, initial)
    for name, expected in material_manifest["file_hashes"].items():
        if sha256(initial / "public" / name) != expected:
            raise ForgeError("The worker's reference packet differs from the files inspected by the author")
    write_json(out / "generation.json", {"mode": "live", "synthesis_protocol": "paper_v1", "plan": plan,
                                         "source_hashes": material_manifest["source_hashes"], "scenario": "initial",
                                         "companion_receipt": made["receipt"], "author_receipt": authored["receipt"],
                                         "stages": ["anchor_inspection", "native_companion_construction_and_reinspection", "request_then_rubric"],
                                         "reference_file_hashes": material_manifest["file_hashes"]})
    return initial
