"""Seeded occupation/activity planning from real O*NET rows or user inventories.

The bundled subset is an explicitly curated smoke fixture. The `onet` adapter
joins the official downloadable tables and scales with the supplied inventory;
there is no hard-coded production profession whitelist.
"""
from __future__ import annotations

import csv
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import re
from urllib.parse import quote

from .sources import SourceError, download_source, load_source_manifest, require_production_rights
from .bundled import bundled_anchor_root


class PlanningError(ValueError):
    """Invalid inventory, exclusion policy, or sample request."""


_FAMILIES = ("spreadsheet", "document", "presentation")
_BUNDLED = bundled_anchor_root()
_PAPER_CATALOG_SHA256 = "d77911d4618ff3ad980f855ee7e55b60bccb1678e6035cef62a6eb029b9caf60"
_PAPER_SOURCE = "https://arxiv.org/html/2609.39325v1#A1.T2"
_PAPER_PLAYBOOKS = Path(__file__).parent / "data" / "occupation_playbooks.json"


def _json(path: Path) -> dict | list:
    if path.stat().st_size > 20_000_000:
        raise PlanningError("Inventory JSON exceeds size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanningError("Could not read inventory JSON") from exc


def _read_table(path: Path, required: set[str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        # Official 30.0 tab-delimited files; contemporary CSV files are supported.
        sample = handle.read(8192)
        handle.seek(0)
        delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not required.issubset(set(reader.fieldnames or [])):
            raise PlanningError("O*NET table is missing required columns")
        return list(reader)


def _onet_inventory(settings: dict) -> list[dict]:
    """Read verified cached official tables, or fetch the version-pinned tables."""
    version = str(settings.get("onet_version", "30.0"))
    if not re.fullmatch(r"\d{1,2}\.\d", version):
        raise PlanningError("onet_version must be an explicit version such as 30.0")
    if settings.get("onet_manifest_path"):
        path = Path(settings["onet_manifest_path"])
        manifest = load_source_manifest(path)
        base = path.parent
        occupation_id = settings.get("occupation_source_id", "onet30_occupations")
        activity_id = settings.get("activity_source_id", "onet30_work_activities")
        by_id = {record["id"]: record for record in manifest["sources"]}
        try:
            occupations, activities = by_id[occupation_id], by_id[activity_id]
        except KeyError as exc:
            raise PlanningError("O*NET manifest does not contain the configured source IDs") from exc
    else:
        if settings.get("allow_network") is not True:
            raise PlanningError("O*NET network retrieval requires allow_network=true or a cached onet_manifest_path")
        base = Path(settings.get("cache_dir", ".wgforge-cache/onet"))
        official_base = f"https://www.onetcenter.org/dl_files/database/db_{version.replace('.', '_')}_text/"
        base_url = settings.get("onet_base_url", official_base)
        if base_url.rstrip("/") != official_base.rstrip("/"):
            raise PlanningError("O*NET automatic fetch requires the official version-specific database path; use a reviewed manifest for other files")
        network = {"allowed_hosts": ["www.onetcenter.org"], "max_bytes": 20_000_000,
                   "timeout_seconds": 60, "allowed_mime_types": ["text/plain", "text/csv"],
                   **settings.get("network", {})}
        records = []
        for filename, label in (("Occupation Data.txt", "occupations"), ("Work Activities.txt", "work_activities")):
            record = {"id": f"onet_{version}_{label}", "url": base_url.rstrip("/") + "/" + quote(filename),
                      "title": f"O*NET {version} {filename}", "license": "CC-BY-4.0", "rights_status": "approved",
                      "rights_basis": "Official downloadable O*NET Database files, subject to version attribution and change notices.",
                      "rights_evidence": ["https://www.onetcenter.org/license_db.html", "https://creativecommons.org/licenses/by/4.0/"],
                      "rights_permissions": {"training": True, "redistribution": True, "public_access": True},
                      "attribution": f"Includes O*NET {version} Database information from USDOL/ETA, CC BY 4.0. Selection and deliverable mappings are authored modifications, not endorsed by USDOL/ETA.",
                      "synthetic": False}
            # Do not grant the official dataset license to a caller-controlled host.
            if not record["url"].startswith("https://www.onetcenter.org/dl_files/database/"):
                raise PlanningError("Automatic O*NET rights classification is limited to official database downloads")
            downloaded = download_source(record, base, network)
            if not downloaded.get("final_url", "").startswith(official_base):
                raise PlanningError("O*NET download redirected outside the version-specific database directory")
            records.append(downloaded)
        occupations, activities = records
        base.mkdir(parents=True, exist_ok=True)
        (base / "onet_manifest.json").write_text(json.dumps({"schema_version": 1, "sources": records}, indent=2) + "\n", encoding="utf-8")
    for record in (occupations, activities):
        require_production_rights(record)
    occupation_rows = _read_table(base / occupations["path"], {"O*NET-SOC Code", "Title"})
    names = {row["O*NET-SOC Code"]: row["Title"] for row in occupation_rows}
    activity_rows = _read_table(base / activities["path"], {"O*NET-SOC Code", "Element ID", "Element Name", "Scale ID", "Data Value"})
    minimum = float(settings.get("minimum_importance", 0))
    if not math.isfinite(minimum) or not 0 <= minimum <= 5:
        raise PlanningError("minimum_importance must be between 0 and 5")
    inventory = []
    for line, row in enumerate(activity_rows, start=2):
        if row["Scale ID"] != "IM" or row.get("Recommend Suppress") == "Y" or row.get("Not Relevant") == "Y":
            continue
        try:
            importance = float(row["Data Value"])
        except ValueError:
            continue
        if not math.isfinite(importance) or importance < minimum or row["O*NET-SOC Code"] not in names:
            continue
        inventory.append({"profession": names[row["O*NET-SOC Code"]], "occupation_code": row["O*NET-SOC Code"],
                          "activity": row["Element Name"], "activity_id": row["Element ID"],
                          "onet_importance": importance, "inventory_origin": "onet_downloadable_database",
                          "deliverable_families": settings.get("deliverable_families", list(_FAMILIES)),
                          "activity_provenance": {"source_id": activities["id"], "sha256": activities["sha256"],
                                                  "line": line, "version": version, "scale_id": "IM",
                                                  "domain_source": row.get("Domain Source"),
                                                  "recommend_suppress": row.get("Recommend Suppress")},
                          "mapping_note": "Deliverable-family mapping is authored; O*NET supplies the occupation/activity linkage."})
    return inventory


def _inventory(settings: dict) -> list[dict]:
    adapter = settings.get("adapter", "curated_smoke")
    custom = "inventory" in settings or "inventory_path" in settings
    if "inventory" in settings:
        payload = settings["inventory"]
    elif "inventory_path" in settings:
        payload = _json(Path(settings["inventory_path"]))
    elif adapter == "onet":
        return _onet_inventory(settings)
    elif adapter in {"curated_smoke", "smoke"}:
        path = Path(settings.get("curated_smoke_path", _BUNDLED / "onet_curated_smoke.json"))
        payload = _json(path)
        manifest = load_source_manifest(path.parent / payload["source_manifest"])
        by_id = {r["id"]: r for r in manifest["sources"]}
        occupation_record = by_id.get("onet30_occupations")
        if not occupation_record:
            raise PlanningError("Curated O*NET occupation provenance is missing")
        occupation_names = {r["O*NET-SOC Code"]: r["Title"] for r in _read_table(path.parent / occupation_record["path"], {"O*NET-SOC Code", "Title"})}
        # Validate the curated occupational claims against the saved exact rows,
        # rather than treating authored activity names as official provenance.
        tables = {}
        for item in payload.get("inventory", []):
            provenance = item.get("activity_provenance", {})
            record = by_id.get(provenance.get("source_id"))
            if not record or provenance.get("sha256") != record["sha256"]:
                raise PlanningError("Curated O*NET provenance does not match source manifest")
            if record["id"] not in tables:
                tables[record["id"]] = _read_table(path.parent / record["path"], {"O*NET-SOC Code", "Element ID", "Element Name"})
            line = provenance.get("line", 0)
            if type(line) is not int or not 2 <= line <= len(tables[record["id"]]) + 1:
                raise PlanningError("Curated O*NET source row is invalid")
            row = tables[record["id"]][line - 2]
            if occupation_names.get(item.get("occupation_code")) != item.get("profession"):
                raise PlanningError("Curated O*NET profession does not match occupation source")
            if (row["O*NET-SOC Code"], row["Element ID"], row["Element Name"]) != (item.get("occupation_code"), item.get("activity_id"), item.get("activity")):
                raise PlanningError("Curated O*NET activity does not match exact source row")
    else:
        raise PlanningError(f"Unknown planning adapter: {adapter}")
    if isinstance(payload, dict):
        payload = payload.get("inventory", payload.get("occupations"))
    if not isinstance(payload, list) or not payload:
        raise PlanningError("Planning inventory must be a nonempty list")
    # A custom nested occupation inventory can supply any new profession/code.
    flattened = []
    for item in payload:
        if not isinstance(item, dict):
            raise PlanningError("Inventory entries must be objects")
        if "activities" in item:
            for activity in item["activities"]:
                activity = {"activity": activity} if isinstance(activity, str) else activity
                flattened.append({k: v for k, v in item.items() if k != "activities"} | activity)
        else:
            flattened.append(dict(item))
    if custom:
        for item in flattened:
            item["inventory_origin"] = "user_inventory"
            item["inventory_verified"] = False
    return flattened


def _matches(slot: dict, selectors: dict | list | None) -> bool:
    if not selectors:
        return False
    if isinstance(selectors, list):
        return any(str(value) in {slot["profession"], slot["occupation_code"], slot["activity"], slot.get("activity_id"), slot["deliverable_family"]} for value in selectors)
    if not isinstance(selectors, dict):
        raise PlanningError("Exclusions and heldout selectors must be objects or lists")
    fields = {"occupation_codes": "occupation_code", "occupations": "occupation_code", "professions": "profession",
              "activities": "activity", "activity_ids": "activity_id", "deliverable_families": "deliverable_family"}
    if set(selectors) - set(fields):
        raise PlanningError("Unknown exclusion or heldout selector")
    for key, field in fields.items():
        values = selectors.get(key, [])
        if not isinstance(values, list):
            raise PlanningError(f"{key} selector must be a list")
        if slot.get(field) in values:
            return True
    return False


def plan_tasks(config: dict) -> list[dict]:
    """Return reproducible slots, excluding forbidden and heldout data before sampling.

    Accept config.planning or the planning dictionary itself. count defaults to 6;
    a request exceeding eligible inventory fails rather than silently duplicating.
    Custom inventories never acquire O*NET provenance merely by choosing a code.
    """
    settings = config.get("planning", config)
    seed = settings.get("seed", config.get("seed", 0))
    default_count = 100 if settings.get("adapter") == "paper_occupations" else 6
    count = settings.get("count", settings.get("num_tasks", config.get("num_tasks", default_count)))
    if type(seed) is not int or type(count) is not int or not 1 <= count <= 100_000:
        raise PlanningError("seed and count must be integers; count must be 1-100000")
    if settings.get("adapter") == "paper_occupations":
        return _plan_paper_occupations(settings, seed, count)
    families = settings.get("deliverable_families")
    if families is not None and (not isinstance(families, list) or not families):
        raise PlanningError("deliverable_families must be a nonempty list")
    split = settings.get("split", "train")
    if split not in {"train", "heldout"}:
        raise PlanningError("split must be train or heldout")
    fraction = settings.get("heldout_fraction", 0)
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction < 1:
        raise PlanningError("heldout_fraction must be in [0, 1)")
    exclusions = settings.get("exclusions", {})
    heldout = settings.get("heldout", {})
    allowed_codes = settings.get("occupation_codes")
    allowed_professions = settings.get("professions")
    candidates, seen = [], set()
    for row in _inventory(settings):
        for key in ("profession", "occupation_code", "activity"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise PlanningError(f"Inventory entry requires nonempty {key}")
        if allowed_codes and row["occupation_code"] not in allowed_codes:
            continue
        if allowed_professions and row["profession"] not in allowed_professions:
            continue
        row_families = families or row.get("deliverable_families", [row["deliverable_family"]] if "deliverable_family" in row else list(_FAMILIES))
        if not isinstance(row_families, list) or not row_families:
            raise PlanningError("Each activity needs at least one deliverable family")
        for family in row_families:
            if not isinstance(family, str) or not family:
                raise PlanningError("Deliverable families must be nonempty strings")
            slot = {**{k: v for k, v in row.items() if k not in {"deliverable_families", "seed", "plan_id"}},
                    "deliverable_family": family, "inventory_origin": row.get("inventory_origin", "user_inventory")}
            # Activity grouping prevents train/test leakage through different
            # artifact families for the same occupation/activity pair.
            group = json.dumps([row["occupation_code"], row.get("activity_id", row["activity"])], separators=(",", ":"))
            fraction_score = int(hashlib.sha256((str(settings.get("split_seed", 0)) + group).encode()).hexdigest(), 16) / 2**256
            is_heldout = _matches(slot, heldout) or fraction_score < fraction
            if _matches(slot, exclusions) or (split == "train" and is_heldout) or (split == "heldout" and not is_heldout):
                continue
            key = (row["occupation_code"], row.get("activity_id", row["activity"]), family)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(slot)
    candidates.sort(key=lambda r: (r["occupation_code"], r.get("activity_id", r["activity"]), r["deliverable_family"]))
    instances = settings.get("instances_per_slot", 1)
    if type(instances) is not int or not 1 <= instances <= 100:
        raise PlanningError("instances_per_slot must be 1..100; repeated slots still require fresh scenario sources")
    if instances > 1:
        candidates = [{**slot, "instance_index": n} for slot in candidates for n in range(instances)]
    if len(candidates) < count:
        raise PlanningError(f"Requested {count} tasks but only {len(candidates)} eligible distinct slots remain")
    rng = random.Random(seed)
    if settings.get("sampling") == "occupation_balanced":
        groups = {}
        for slot in candidates:
            groups.setdefault(slot["occupation_code"], []).append(slot)
        order = sorted(groups)
        rng.shuffle(order)
        for group in groups.values():
            rng.shuffle(group)
        selected = []
        while len(selected) < count:
            for code in order:
                if groups[code]:
                    selected.append(groups[code].pop())
                    if len(selected) == count:
                        break
    elif settings.get("sampling", "uniform") == "uniform":
        selected = rng.sample(candidates, count)
    else:
        raise PlanningError("sampling must be uniform or occupation_balanced")
    results = []
    for index, slot in enumerate(selected):
        task_seed = rng.randrange(0, 2**32)
        identity = json.dumps({"slot": slot, "seed": task_seed}, sort_keys=True, separators=(",", ":"))
        results.append({**slot, "seed": task_seed, "sampling_seed": seed, "slot_index": index,
                        "plan_id": "plan_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        "split": split, "source_query": f'{slot["profession"]} {slot["activity"]} public data {slot["deliverable_family"]}'})
    return results


def _stable_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _paper_catalog(catalog):
    """Pin the 65 paper labels/sectors independently of reconstructed mappings."""
    if not isinstance(catalog, dict) or catalog.get("source_revision") != "2609.39325v1":
        raise PlanningError("Paper occupation catalog must identify arXiv 2609.39325v1")
    rows = catalog.get("occupations")
    if not isinstance(rows, list) or len(rows) != 65:
        raise PlanningError("Paper planning requires all 65 occupation playbooks; missing coverage is not silently skipped")
    try:
        labels = [{key: row[key] for key in ("sector_code", "sector", "occupation")} for row in rows]
        labels.sort(key=lambda row: row["occupation"])
    except (KeyError, TypeError):
        raise PlanningError("Malformed paper occupation catalog") from None
    if hashlib.sha256(_stable_json(labels).encode()).hexdigest() != _PAPER_CATALOG_SHA256:
        raise PlanningError("Occupation titles or sectors differ from the 65 paper Table 2 entries")
    return rows


def build_paper_playbooks(catalog: dict, manifest_path: Path, source_manifest_reference="anchors/onet_manifest.json") -> dict:
    """Rebuild grounded playbooks from pinned, hash-verified official source rows.

    The authors' playbooks and O*NET version were not published. This is an
    explicitly reconstructed input to their method, not recovered author data.
    Duties are Task Statements, not invented replacements for absent Activity
    rows. No occupation is omitted merely because one O*NET table lacks rows.
    """
    occupations = _paper_catalog(catalog)
    try:
        manifest = load_source_manifest(Path(manifest_path))
    except SourceError as exc:
        raise PlanningError("Paper playbook source manifest failed provenance verification: " + str(exc)) from exc
    filenames = {"onet30_occupations": "Occupation%20Data.txt", "onet30_task_statements": "Task%20Statements.txt",
                 "onet30_work_activities": "Work%20Activities.txt", "onet30_technology_skills": "Technology%20Skills.txt",
                 "onet30_work_context": "Work%20Context.txt"}
    records = {row["id"]: row for row in manifest["sources"]}
    for source_id, filename in filenames.items():
        if source_id not in records:
            raise PlanningError("Paper playbooks require verified O*NET source: " + source_id)
        record = records[source_id]
        expected_url = "https://www.onetcenter.org/dl_files/database/db_30_0_text/" + filename
        if record["url"] != expected_url or record.get("synthetic") is not False:
            raise PlanningError("Paper occupation facts must come from official nonsynthetic O*NET 30.0 tables")
        require_production_rights(record)
    tables = {}
    required_codes = set()
    for occupation in occupations:
        codes = occupation.get("onet_soc_codes")
        if not isinstance(codes, list) or not codes or any(not isinstance(code, str) for code in codes):
            raise PlanningError("Every paper occupation needs an explicit verified O*NET mapping")
        if occupation["occupation"] == "Buyers and Purchasing Agents":
            if (codes != ["13-1021.00", "13-1022.00", "13-1023.00"]
                    or occupation.get("mapping_kind") != "soc_broad_group" or occupation.get("soc_broad_code") != "13-1020"):
                raise PlanningError("Buyers and Purchasing Agents must retain its transparent three-code broad-group mapping")
        elif len(codes) != 1 or occupation.get("mapping_kind") != "exact_onet30_title":
            raise PlanningError("Paper occupation mapping must match its exact O*NET title")
        required_codes.update(codes)
    required_columns = {
        "onet30_occupations": {"O*NET-SOC Code", "Title", "Description"},
        "onet30_task_statements": {"O*NET-SOC Code", "Task ID", "Task", "Task Type"},
        "onet30_work_activities": {"O*NET-SOC Code", "Element ID", "Element Name", "Scale ID", "Data Value"},
        "onet30_technology_skills": {"O*NET-SOC Code", "Example", "Commodity Code", "Commodity Title"},
        "onet30_work_context": {"O*NET-SOC Code", "Element ID", "Element Name", "Scale ID", "Data Value"},
    }
    # Stream the large Context table rather than holding its unrelated rows.
    for source_id in filenames:
        grouped = {code: [] for code in required_codes}
        source_path = Path(manifest_path).parent / records[source_id]["path"]
        with source_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not required_columns[source_id].issubset(set(reader.fieldnames or [])):
                raise PlanningError("O*NET playbook table is missing required columns: " + source_id)
            for line, row in enumerate(reader, start=2):
                code = row["O*NET-SOC Code"]
                if code in grouped:
                    grouped[code].append((line, row))
        tables[source_id] = grouped
    provenance = {source_id: {"source_id": source_id, "url": records[source_id]["url"],
                              "sha256": records[source_id]["sha256"], "version": "30.0"}
                  for source_id in filenames}
    playbooks = []
    for occupation in occupations:
        codes = occupation["onet_soc_codes"]
        descriptions, duties, activities, technology, context, missing = [], [], [], [], [], {}
        for code in codes:
            description_rows = tables["onet30_occupations"][code]
            if len(description_rows) != 1:
                raise PlanningError("Missing or ambiguous official occupation description for " + code)
            line, row = description_rows[0]
            if len(codes) == 1 and row["Title"] != occupation["occupation"]:
                raise PlanningError("Paper occupation title does not match the mapped O*NET row")
            descriptions.append({"occupation_code": code, "title": row["Title"], "description": row["Description"], "source_row": line})
            duty_rows = tables["onet30_task_statements"][code]
            if not duty_rows:
                raise PlanningError("Verified Task Statements duties are missing for " + code + "; no synthetic fallback is permitted")
            for line, row in duty_rows:
                duties.append({"occupation_code": code, "task_id": row["Task ID"], "task": row["Task"],
                               "task_type": row["Task Type"], "source_row": line})
            for line, row in tables["onet30_work_activities"][code]:
                if row["Scale ID"] == "IM" and row.get("Recommend Suppress") != "Y" and row.get("Not Relevant") != "Y":
                    activities.append({"occupation_code": code, "activity_id": row["Element ID"], "activity": row["Element Name"],
                                       "importance": row["Data Value"], "scale_id": "IM", "source_row": line})
            for line, row in tables["onet30_technology_skills"][code]:
                technology.append({"occupation_code": code, "technology": row["Example"], "category": row["Commodity Title"],
                                   "commodity_code": row["Commodity Code"], "source_row": line})
            for line, row in tables["onet30_work_context"][code]:
                if row["Scale ID"] == "CX" and row.get("Recommend Suppress") != "Y" and row.get("Not Relevant") != "Y":
                    context.append({"occupation_code": code, "context_id": row["Element ID"], "context": row["Element Name"],
                                    "value": row["Data Value"], "scale_id": "CX", "source_row": line})
            absent = [source_id for source_id in ("onet30_work_activities", "onet30_technology_skills", "onet30_work_context")
                      if not tables[source_id][code]]
            if absent:
                missing[code] = absent
        playbooks.append({"playbook_id": "occupation_" + hashlib.sha256(occupation["occupation"].encode()).hexdigest()[:16],
                          "occupation": occupation["occupation"], "sector": occupation["sector"], "sector_code": occupation["sector_code"],
                          "onet_soc_codes": codes, "mapping_kind": occupation["mapping_kind"],
                          "mapping_note": occupation.get("mapping_note", "Exact title match in official O*NET 30.0 Occupation Data."),
                          "origin": "reconstructed_from_official_onet30", "original_paper_playbook": False,
                          "descriptions": descriptions, "duties": duties, "work_activities": activities,
                          "technology_skills": technology, "work_context": context, "missing_source_tables": missing,
                          "source_provenance": copy.deepcopy(provenance),
                          "document_guidance": {"origin": "authored_method_guidance_not_an_onet_claim",
                              "instruction": "Use the occupation duties and available real files to choose a realistic work product. The formats are a menu, not a fixed target. Do not require a particular commercial application merely because it appears in O*NET.",
                              "format_menu": ["pdf", "docx", "xlsx", "csv", "pptx", "close variants"],
                              "required_formats": []}})
    return {"schema_version": 1, "source": _PAPER_SOURCE, "source_manifest": source_manifest_reference,
            "paper_occupation_catalog": copy.deepcopy(catalog), "onet_version": "30.0", "playbooks": playbooks,
            "reconstruction_note": "Independently reconstructed occupation playbooks from verified official O*NET rows. The paper does not disclose its original playbooks, O*NET revision, or sampling seed; none is claimed to be recovered."}


def load_paper_playbooks(settings=None):
    """Validate both complete coverage and every generated fact against raw rows."""
    settings = settings or {}
    path = Path(settings.get("playbooks_path", _PAPER_PLAYBOOKS))
    try:
        payload = _json(path)
    except (OSError, PlanningError) as exc:
        raise PlanningError("Paper occupation playbooks are missing or unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("playbooks"), list) or len(payload["playbooks"]) != 65:
        raise PlanningError("Paper planning requires all 65 occupation playbooks; missing coverage is not silently skipped")
    reference = payload.get("source_manifest")
    if not isinstance(reference, str) or not reference or Path(reference).is_absolute() or ".." in Path(reference).parts:
        raise PlanningError("Playbook source_manifest must be a safe relative path")
    manifest_path = Path(settings.get("onet_manifest_path", path.parent / reference))
    expected = build_paper_playbooks(payload.get("paper_occupation_catalog"), manifest_path, reference)
    if payload != expected:
        raise PlanningError("Paper playbooks differ from their verified O*NET source rows or reconstruction contract")
    return payload


def _plan_paper_occupations(settings, seed, count):
    """Balanced occupation rounds, unconstrained deliverables, unique instances."""
    if settings.get("sampling", "occupation_balanced") != "occupation_balanced":
        raise PlanningError("The paper profile uses occupation_balanced sampling")
    for name in ("professions", "occupation_codes", "exclusions", "heldout", "heldout_fraction", "inventory", "inventory_path"):
        if settings.get(name):
            raise PlanningError("Paper planning preserves all 65 occupations; unsupported coverage selector: " + name)
    if settings.get("split", "train") != "train":
        raise PlanningError("The paper generation profile does not create a held-out occupation split")
    if settings.get("deliverable_families") not in (None, [], ["any"]):
        raise PlanningError("Paper planning lets real anchors determine deliverables; no fixed deliverable family is allowed")
    payload = load_paper_playbooks(settings)
    playbooks = sorted(payload["playbooks"], key=lambda row: row["occupation"])
    random.Random(seed).shuffle(playbooks)
    playbook_digests = {row["playbook_id"]: hashlib.sha256(_stable_json(row).encode()).hexdigest() for row in playbooks}
    results = []
    for index in range(count):
        playbook = playbooks[index % len(playbooks)]
        instance = index // len(playbooks)
        playbook_digest = playbook_digests[playbook["playbook_id"]]
        identity = {"adapter": "paper_occupations/v1", "sampling_seed": seed,
                    "playbook_id": playbook["playbook_id"], "playbook_sha256": playbook_digest, "instance_index": instance}
        identity_digest = hashlib.sha256(_stable_json(identity).encode()).hexdigest()
        codes = playbook["onet_soc_codes"]
        results.append({"profession": playbook["occupation"], "sector": playbook["sector"], "sector_code": playbook["sector_code"],
                        "occupation_code": codes[0] if len(codes) == 1 else "13-1020", "occupation_codes": list(codes),
                        "occupation_mapping_kind": playbook["mapping_kind"], "activity": "", "deliverable_family": "any",
                        "required_file_formats": [], "inventory_origin": "paper65_reconstructed_onet_playbooks",
                        "inventory_verified": True, "occupation_playbook": playbook,
                        "playbook_id": playbook["playbook_id"], "playbook_sha256": playbook_digest,
                        "plan_id": "plan_" + identity_digest[:24], "seed": int(identity_digest[24:32], 16),
                        "sampling_seed": seed, "slot_index": index, "instance_index": instance, "split": "train",
                        "source_query": playbook["occupation"] + " real professional documents public files",
                        "sampling_note": "Balanced occupation rounds with independently chosen seed. Each instance requires a fresh source-grounded task; this is not a pre-generated task."})
    return results


def _paper_plan_metadata(playbook):
    """Fields recoverable from a referenced playbook and the paper adapter."""
    codes = playbook["onet_soc_codes"]
    return {"profession": playbook["occupation"], "sector": playbook["sector"], "sector_code": playbook["sector_code"],
            "occupation_code": codes[0] if len(codes) == 1 else "13-1020", "occupation_codes": list(codes),
            "occupation_mapping_kind": playbook["mapping_kind"], "activity": "", "deliverable_family": "any",
            "required_file_formats": [], "inventory_origin": "paper65_reconstructed_onet_playbooks", "inventory_verified": True,
            "source_query": playbook["occupation"] + " real professional documents public files",
            "sampling_note": "Balanced occupation rounds with independently chosen seed. Each instance requires a fresh source-grounded task; this is not a pre-generated task."}


def compact_plans(plans):
    """Store each playbook once and retain small, losslessly recoverable plans.

    Paper metadata that can be derived exactly is omitted; profession, identity,
    seed and instance fields remain directly inspectable. Custom fields and
    ordinary non-paper plans survive unchanged. This makes a 20K schedule a few
    megabytes instead of repeating the same occupation material 20K times.
    """
    if not isinstance(plans, list):
        raise PlanningError("Plans to compact must be a list")
    compact, catalog, verified_objects, seen_ids = [], {}, {}, set()
    for plan in plans:
        if not isinstance(plan, dict):
            raise PlanningError("Each plan must be an object")
        plan_id = plan.get("plan_id")
        if plan_id is not None:
            if not isinstance(plan_id, str) or plan_id in seen_ids:
                raise PlanningError("Plan IDs must be unique strings")
            seen_ids.add(plan_id)
        if "occupation_playbook" not in plan:
            if plan.get("playbook_id"):
                raise PlanningError("Cannot compact an unresolved playbook reference; hydrate it first")
            compact.append(copy.deepcopy(plan))
            continue
        playbook = plan["occupation_playbook"]
        if not isinstance(playbook, dict):
            raise PlanningError("Occupation playbook must be an object")
        playbook_id = playbook.get("playbook_id")
        if not isinstance(playbook_id, str) or plan.get("playbook_id") != playbook_id:
            raise PlanningError("Plan and occupation playbook IDs disagree")
        key = id(playbook)
        if key not in verified_objects:
            verified_objects[key] = hashlib.sha256(_stable_json(playbook).encode()).hexdigest()
        actual_digest = verified_objects[key]
        if plan.get("playbook_sha256") != actual_digest:
            raise PlanningError("Plan playbook digest does not match its occupation playbook")
        if playbook_id in catalog:
            if catalog[playbook_id]["digest"] != actual_digest:
                raise PlanningError("Conflicting playbooks share one ID")
        else:
            catalog[playbook_id] = {"digest": actual_digest, "playbook": copy.deepcopy(playbook)}
        metadata = _paper_plan_metadata(playbook)
        for name, value in metadata.items():
            if name in plan and plan[name] != value:
                raise PlanningError("Plan metadata disagrees with its occupation playbook: " + name)
        record = {name: copy.deepcopy(value) for name, value in plan.items()
                  if name != "occupation_playbook" and (name not in metadata or name == "profession")}
        compact.append(record)
    return {"plans": compact, "playbooks": {name: item["playbook"] for name, item in catalog.items()}}


def hydrate_plan(plan, playbooks):
    """Restore a compact plan after verifying its referenced playbook SHA-256."""
    if not isinstance(plan, dict) or not isinstance(playbooks, dict):
        raise PlanningError("Hydration requires a plan object and playbook catalog")
    playbook_id = plan.get("playbook_id")
    if not playbook_id:
        if "occupation_playbook" in plan:
            raise PlanningError("An embedded occupation playbook must have an explicit ID and digest")
        return copy.deepcopy(plan)
    playbook = playbooks.get(playbook_id)
    if not isinstance(playbook, dict) or playbook.get("playbook_id") != playbook_id:
        raise PlanningError("Referenced occupation playbook is missing or has a different ID")
    actual_digest = hashlib.sha256(_stable_json(playbook).encode()).hexdigest()
    if plan.get("playbook_sha256") != actual_digest:
        raise PlanningError("Referenced occupation playbook SHA-256 verification failed")
    if "occupation_playbook" in plan and plan["occupation_playbook"] != playbook:
        raise PlanningError("Embedded and referenced occupation playbooks disagree")
    try:
        metadata = _paper_plan_metadata(playbook)
    except (KeyError, TypeError, IndexError):
        raise PlanningError("Referenced occupation playbook is malformed") from None
    for name, value in metadata.items():
        if name in plan and plan[name] != value:
            raise PlanningError("Plan metadata disagrees with its occupation playbook: " + name)
    return {**metadata, **copy.deepcopy({name: value for name, value in plan.items() if name != "occupation_playbook"}),
            "occupation_playbook": copy.deepcopy(playbook)}
