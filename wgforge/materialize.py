"""Create immutable revision bundles with strict public/private separation."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path, PurePosixPath
import shutil
from .common import ForgeError, digest, read_json, safe_path, sha256, tree_hashes, write_json
from .schema import public_contract, validate_scenario
from .artifacts import extract_artifact, write_artifact


def render_companions(companions, out, paper=False, artifact_base=None):
    """Materialize and reopen attachments; paper provenance stays in the private manifest."""
    result = {}
    for companion in companions:
        target = safe_path(Path(out), companion["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        private_provenance = paper and companion.get("disclosure_scope") == "private"
        if companion.get("artifact_path"):
            if not paper or artifact_base is None:
                raise ForgeError("Native companion artifacts require a paper material base")
            source = safe_path(Path(artifact_base), companion["artifact_path"], True)
            if sha256(source) != companion.get("sha256"):
                raise ForgeError("Native companion bytes differ from the inspected material")
            shutil.copyfile(source, target)
        else:
            spec = deepcopy(companion["spec"])
            if private_provenance:
                spec["business_document"] = True
            else:
                spec["text"] = companion["disclosure"] + "\n\n" + str(spec.get("text", ""))
                if companion["format"] == "csv":
                    for table in spec.get("tables", []):
                        table["headers"] = list(table["headers"]) + ["fiction_disclosure"]
                        table["rows"] = [list(row) + [companion["disclosure"]] for row in table["rows"]]
                if companion["format"] == "pptx" and spec.get("slides"):
                    spec["slides"] = [{"title": "Fictional scenario material", "text": companion["disclosure"]}] + spec["slides"]
            write_artifact(target, spec)
        parsed = extract_artifact(target)
        if not private_provenance and " ".join(companion["disclosure"].split()) not in " ".join(parsed["text"].split()):
            raise ForgeError("Companion fiction disclosure was not preserved in the rendered file")
        result[companion["path"]] = {"sha256": sha256(target), **parsed}
    return result


def render_scenario(document, anchor_base, out):
    document = deepcopy(validate_scenario(document))
    out, anchor_base = Path(out), Path(anchor_base)
    if out.exists() and any(out.iterdir()):
        raise ForgeError("Revision output must be empty; revisions are immutable")
    out.mkdir(parents=True, exist_ok=True)
    (out / "private").mkdir()
    (out / "public").mkdir()
    for anchor in document["anchor_sources"]:
        source = safe_path(anchor_base, anchor["path"], True)
        if sha256(source) != anchor["sha256"]:
            raise ForgeError("Anchor bytes changed: " + anchor["id"])
        name = "anchors/" + anchor["id"] + PurePosixPath(anchor["path"]).suffix.lower()
        target = safe_path(out / "public", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(target) != anchor["sha256"]:
            raise ForgeError("Anchor copy failed integrity verification")
    render_companions(document["companions"], out / "public" / "companions",
                      paper=document.get("synthesis_protocol") == "paper_v1", artifact_base=anchor_base)
    task = public_contract(document)
    write_json(out / "public" / "task.json", task)
    lines = [task["title"], "Role: " + task["role"], task["context"], task["instructions"], "Required deliverables:"]
    lines += ["- " + item["path"] + ": " + item["description"] for item in task["deliverables"]]
    lines += ["Reference files:"] + ["- " + item["path"] for item in task["reference_files"]]
    lines += [task["source_instructions"]]
    (out / "public" / "request.txt").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    write_json(out / "private" / "scenario.json", document)
    manifest = {"schema_version": document["schema_version"], "scenario_id": document["scenario_id"],
                "revision": document["revision"], "lineage_id": document["lineage_id"],
                "public_hashes": tree_hashes(out / "public"), "private_hashes": tree_hashes(out / "private"),
                "anchor_hashes": {a["id"]: a["sha256"] for a in document["anchor_sources"]}}
    manifest["bundle_digest"] = digest(manifest)
    write_json(out / "manifest.json", manifest)
    verify_bundle(out)
    return manifest


def verify_bundle(root):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    expected = {k: v for k, v in manifest.items() if k != "bundle_digest"}
    if digest(expected) != manifest.get("bundle_digest"):
        raise ForgeError("Scenario manifest was modified")
    if tree_hashes(root / "public") != manifest["public_hashes"] or tree_hashes(root / "private") != manifest["private_hashes"]:
        raise ForgeError("Scenario bundle bytes changed after sealing")
    document = validate_scenario(read_json(root / "private" / "scenario.json"))
    if read_json(root / "public" / "task.json") != public_contract(document):
        raise ForgeError("Public contract does not match private scenario specification")
    for anchor in document["anchor_sources"]:
        target = safe_path(root / "public", "anchors/" + anchor["id"] + PurePosixPath(anchor["path"]).suffix.lower(), True)
        if sha256(target) != anchor["sha256"]:
            raise ForgeError("Immutable real anchor was changed")
    return document


def anchor_base_for_revision(root, target):
    """Copy only sealed anchor bytes to a new intake root, keeping original manifest paths."""
    document = verify_bundle(root)
    target = Path(target)
    for item in document["anchor_sources"]:
        source = Path(root) / "public" / "anchors" / (item["id"] + PurePosixPath(item["path"]).suffix.lower())
        dest = safe_path(target, item["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and sha256(dest) != item["sha256"]:
            raise ForgeError("Conflicting anchor cache bytes")
        if not dest.exists():
            shutil.copyfile(source, dest)
    for item in document["companions"]:
        if item.get("artifact_path"):
            source = safe_path(Path(root) / "public" / "companions", item["path"], True)
            dest = safe_path(target, item["artifact_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if sha256(source) != item["sha256"]:
                raise ForgeError("Inspected companion bytes changed")
            if dest.exists() and sha256(dest) != item["sha256"]:
                raise ForgeError("Conflicting companion cache bytes")
            if not dest.exists():
                shutil.copyfile(source, dest)
    return target


def extract_public(root, limit=300_000):
    verify_bundle(root)
    result, total = {}, 0
    for name in tree_hashes(Path(root) / "public"):
        if name in {"task.json", "request.txt"}:
            continue
        path = safe_path(Path(root) / "public", name, True)
        try:
            parsed = extract_artifact(path)
        except (ValueError, UnicodeError):
            raise ForgeError("Unsupported or unsafe anchor extraction: " + name) from None
        total += len(parsed["text"])
        if total > limit:
            raise ForgeError("Evidence context exceeds configured character limit; no silent truncation")
        result[name] = {"sha256": sha256(path), **parsed}
    return result


def inspect_outputs(root, run_dir):
    document = verify_bundle(root)
    output = Path(run_dir) / "workspace" / "output"
    result = {"files": {}, "missing": [], "unverified_visual_files": []}
    hashes = tree_hashes(output)
    expected = {item["path"] for item in document["request"]["deliverables"]}
    result["missing"] = sorted(expected - hashes.keys())
    result["extra"] = sorted(hashes.keys() - expected)
    total = 0
    for name in hashes:
        path = safe_path(output, name, True)
        total += path.stat().st_size
        if total > 32_000_000 or len(hashes) > 128:
            raise ForgeError("Output files exceed audit bounds")
        try:
            parsed = extract_artifact(path)
        except (ValueError, UnicodeError, OSError) as error:
            if document.get("synthesis_protocol") != "paper_v1":
                raise ForgeError("Output cannot be safely parsed: " + name) from None
            parsed = {"text": "", "tables": [], "format": path.suffix.lstrip("."),
                      "parse_error": type(error).__name__, "parser_notes": ["Submitted artifact is unreadable by the parser; judge the actual file and do not infer content."],
                      "unverified_visual_content": True}
            result.setdefault("unreadable", []).append(name)
        result["files"][name] = {"sha256": hashes[name], **parsed}
        if parsed.get("unverified_visual_content"):
            result["unverified_visual_files"].append(name)
    return result
