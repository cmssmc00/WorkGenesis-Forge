"""Offline protocol regression tests; no provider calls or task generation run."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from wgforge.common import ForgeError, digest, read_json, sha256, tree_hashes, write_json
from wgforge.materialize import render_scenario, verify_bundle
from wgforge.schema import VERSION
from wgforge.synthesis import author_paper_scenario, stage_native_companions, _recorded_visual_inspections, _prompt_reference_previews
from wgforge.workflow import evolve_scenario, author_scenario, audit_run, json_role, validate_run_binding, validate_source_assessment, UnsuitableSource
from wgforge import prompts


ROOT = Path(__file__).resolve().parents[1]


class PaperWorkflowTests(unittest.TestCase):
    def test_small_reference_prompts_remain_unchanged(self):
        refs = {"anchors/source.csv": {"extracted_text": "small source", "tables": [{"rows": [[1, 2]]}]}}
        projected = _prompt_reference_previews(refs)
        self.assertEqual(projected, refs)
        projected["anchors/source.csv"]["tables"].clear()
        self.assertEqual(len(refs["anchors/source.csv"]["tables"]), 1)

    def test_long_source_is_previewed_in_both_stages_but_full_bytes_and_excerpt_validation_remain(self):
        from wgforge.common import canonical
        source = self.manifest["sources"][0]
        path = self.base / source["path"]
        tail = "Final audited observation outside the prompt preview"
        path.write_text("observation,value\n" + ("Long occupational source observation,12345\n" * 12000) + tail + ",67890\n")
        original_bytes = path.read_bytes()
        source.update(sha256=sha256(path), bytes=path.stat().st_size)
        updated_path = path.with_name(source["sha256"] + path.suffix)
        path.rename(updated_path)
        path = updated_path
        source["path"] = str(path.relative_to(self.base))
        # The complete extraction exceeds this limit; the explicit preview does
        # not. Long original files must remain available and fully validated.
        self.config["evidence_char_limit"] = 80_000
        stages = []
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            stages.append(name)
            self.assertEqual(next(iter(input_files.values())).read_bytes(), original_bytes)
            self.assertLess(len(canonical(payload)), 80_000)
            refs = payload["sources"] if name == "companion_author" else list(payload["reference_files_parsed"].values())
            self.assertTrue(refs[0]["preview"]["partial"])
            self.assertNotIn(tail, refs[0]["extracted_text"])
            if name == "companion_author":
                result = self.material_result(payload)
                # The coordinator must validate against the complete extraction,
                # not reject a real quote merely because it lies past the preview.
                result["source_assessment"][0]["evidence_excerpt"] = tail
                return {"result": result, "receipt": {}, "artifact_root": self.native}
            return {"result": self.task_result(), "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            output = author_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(stages, ["companion_author", "task_author"])
        doc = verify_bundle(output)
        self.assertEqual(doc["anchor_sources"][0]["sha256"], source["sha256"])
        self.assertEqual(path.read_bytes(), original_bytes)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "anchors"
        self.manifest = read_json(ROOT / "examples" / "anchors" / "usgs_manifest.json")
        source = self.manifest["sources"][0]
        path = self.base / source["path"]
        path.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "examples" / "anchors" / source["path"], path)
        self.plan = {"profession": "Geoscientists, Except Hydrologists and Geographers", "plan_id": "test-plan",
                     "occupation_playbook": {"title": "Geoscientists", "duties": ["Analyze geoscience observations"]}}
        self.config = {"synthesis": {"protocol": "paper_v1", "rubric_size_target": 20},
                       "roles": {"companion_author": {"harness": "stirrup"}, "task_author": {"harness": "stirrup"}},
                       "repair": {"repeated_defect_limit": 3}}
        self.native = self.root / "native_outputs"
        self.native.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def request(self):
        return {"title": "Prepare the weekly catalog briefing", "role": "Geoscientist",
                "context": "The team lead needs an internal briefing before the weekly operations meeting.",
                "instructions": "Analyze the attached observations and explain the catalog's coverage limits in briefing.md.",
                "deliverables": [{"path": "briefing.md", "format": "md", "description": "Completed internal briefing"}]}

    def rubric(self, count=20):
        return {"items": [{"id": "c%02d" % (i + 1), "weight": 2 if i < 15 else 5,
                           "criterion": "Evidence-based requirement %d" % (i + 1), "verification": "structured",
                           "evidence": ["anchors/usgs_earthquakes_oct2026_200.csv"],
                           "required": i < 5, "tags": ["source-grounded"]} for i in range(count)]}

    def document(self):
        return {"schema_version": VERSION, "synthesis_protocol": "paper_v1", "scenario_id": "paper-test",
                "lineage_id": "paper-lineage", "revision": 0, "plan": self.plan,
                "anchor_sources": self.manifest["sources"], "request": self.request(),
                "companions": [], "rubric": self.rubric()}

    def material_result(self, payload, companions=None):
        return {"source_assessment": [{"source_id": payload["sources"][0]["record"]["id"], "usable": True,
                                         "occupation_fit": "The real catalog supports occupational analysis of observed seismicity and coverage.",
                                         "professional_operations": ["Reconcile event totals and observed date coverage"],
                                         "evidence_excerpt": payload["sources"][0]["extracted_text"][:150],
                                         "limitations": "The actual snapshot is bounded to 200 events."}],
                "scenario_context": {"organization": "Regional Analysis Desk", "worker_role": "Geoscientist",
                                     "period": "October 2026", "business_situation": "A weekly internal observations review.",
                                     "intended_work_product": "An internal catalog briefing"},
                "gap_analysis": [], "companions": companions or [], "package_summary": "An observation catalog packet.",
                "known_limitations": ["Bounded snapshot"], "notes_for_task_synthesis": "Do not infer a complete global census."}

    def task_result(self):
        return {"request": self.request(), "rubric": self.rubric(), "fact_sheet": {"private_author_notes": "Do not expose this."},
                "task_manifest": {"occupation": self.plan["profession"], "research_required": False}}

    def native_record(self):
        path = self.native / "companions" / "policy.md"
        path.parent.mkdir(parents=True)
        path.write_text("Internal review scope\nUse the attached snapshot for the weekly operations meeting.\n", encoding="utf-8")
        return {"id": "policy", "path": "policy.md", "format": "md", "artifact_path": "companions/policy.md",
                "sha256": sha256(path), "purpose": "Define the audience and timing of the internal review.",
                "derived_from": ["anchors/usgs_earthquakes_oct2026_200.csv"], "assumptions": ["Internal team timing"],
                "external_facts_used": [], "provenance_note": "The catalog observation period grounds this work context.",
                "verified": {"opens_and_parses": True, "internally_consistent": True, "cross_file_consistent": True}}

    def add_unrelated_anchor(self):
        source = deepcopy(self.manifest["sources"][0])
        original = self.base / source["path"]
        data = original.read_bytes() + b"# unrelated second snapshot\n"
        digest_value = __import__("hashlib").sha256(data).hexdigest()
        path = original.with_name(digest_value + original.suffix)
        path.write_bytes(data)
        source.update(id="unrelated_second_anchor", path=str(path.relative_to(self.base)), sha256=digest_value,
                      bytes=len(data), url=source["url"] + "&unrelated=1", final_url=source["final_url"] + "&unrelated=1",
                      title="Unrelated second snapshot")
        self.manifest["sources"].append(source)
        return source

    def unused_assessment(self, source):
        return {"source_id": source["id"], "usable": False,
                "occupation_fit": "Real and relevant in general, but it describes a different organisation and period.",
                "limitations": ["Not included in this package."]}

    def test_source_judged_unusable_is_left_out_instead_of_discarding_finished_work(self):
        second = self.add_unrelated_anchor()
        seen = {}
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            seen[name] = sorted(input_files)
            if name == "companion_author":
                self.assertEqual(len(payload["sources"]), 2)
                result = self.material_result(payload)
                result["source_assessment"].append(self.unused_assessment(second))
                return {"result": result, "receipt": {}, "artifact_root": self.native}
            self.assertEqual(len(payload["reference_files_parsed"]), 1)
            return {"result": self.task_result(), "receipt": {}}
        with patch("wgforge.workflow.json_role", side_effect=role):
            output = author_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(len(seen["companion_author"]), 2)
        self.assertEqual(seen["task_author"], ["anchors/usgs_earthquakes_oct2026_200.csv"])
        doc = verify_bundle(output)
        self.assertEqual([s["id"] for s in doc["anchor_sources"]], ["usgs_earthquakes_oct2026_200"])
        self.assertFalse((output / "public" / "anchors" / ("unrelated_second_anchor.csv")).exists())
        saved = read_json(self.root / "generation" / "source_assessment.json")
        self.assertEqual([row["usable"] for row in saved["assessments"]], [True, False])
        self.assertEqual(saved["source_hashes"], [self.manifest["sources"][0]["sha256"]])

    def test_companion_built_on_an_unusable_source_still_rejects_the_attempt(self):
        second = self.add_unrelated_anchor()
        record = self.native_record()
        record["derived_from"] = ["anchors/" + second["id"] + ".csv"]
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            result = self.material_result(payload, [record])
            result["source_assessment"].append(self.unused_assessment(second))
            return {"result": result, "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            with self.assertRaisesRegex(UnsuitableSource, "derived from a source the author judged unusable"):
                author_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)

    def test_all_sources_unusable_still_rejects_the_attempt(self):
        second = self.add_unrelated_anchor()
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            result = self.material_result(payload)
            result["source_assessment"] = [self.unused_assessment(self.manifest["sources"][0]), self.unused_assessment(second)]
            return {"result": result, "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            with self.assertRaisesRegex(UnsuitableSource, "judged unusable"):
                author_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)

    def test_zero_companions_still_runs_two_ordered_stages(self):
        calls = []
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            calls.append(name)
            self.assertIn("code_exec", payload["instructions"])
            self.assertEqual(len(input_files), 1)
            if name == "companion_author":
                return {"result": self.material_result(payload), "receipt": {"stage": name}, "artifact_root": self.native}
            self.assertEqual(name, "task_author")
            self.assertTrue((self.root / "generation" / "material_manifest.json").is_file())
            self.assertEqual(payload["reference_file_manifest"]["companions"], [])
            return {"result": self.task_result(), "receipt": {"stage": name}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            path = author_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(calls, ["companion_author", "task_author"])
        document = verify_bundle(path)
        self.assertEqual(document["companions"], [])
        self.assertEqual(document["synthesis_protocol"], "paper_v1")
        self.assertNotIn("fact_sheet", read_json(path / "public" / "task.json"))
        self.assertEqual(len(document["rubric"]["items"]), 20)

    def test_task_author_reads_exact_materialized_native_files(self):
        companion = self.native_record()
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            if name == "companion_author":
                return {"result": self.material_result(payload, [companion]), "receipt": {}, "artifact_root": self.native}
            actual = input_files["companions/policy.md"]
            self.assertEqual(sha256(actual), companion["sha256"])
            self.assertEqual(payload["reference_files_parsed"]["companions/policy.md"]["sha256"], sha256(actual))
            self.assertNotIn("fictional", actual.read_text().lower())
            self.assertNotIn("rubric", actual.read_text().lower())
            return {"result": self.task_result(), "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            path = author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(sha256(path / "public" / "companions" / "policy.md"), companion["sha256"])
        private = verify_bundle(path)["companions"][0]
        self.assertEqual(private["disclosure_scope"], "private")
        public = read_json(path / "public" / "task.json")
        self.assertNotIn("synthetic", public["reference_files"][-1])
        self.assertNotIn("disclosure", public["reference_files"][-1])

    def test_paper_score_fields_preserve_points_and_original_author_result(self):
        authored = self.task_result()
        for item in authored["rubric"]["items"]:
            item["score"] = item.pop("weight")
        authored["rubric"]["items"][-1]["score"] = -3
        original = deepcopy(authored)
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            result = self.material_result(payload) if name == "companion_author" else authored
            return {"result": result, "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role):
            path = author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        items = verify_bundle(path)["rubric"]["items"]
        self.assertEqual([item["weight"] for item in items], [item["score"] for item in original["rubric"]["items"]])
        self.assertTrue(all("score" not in item for item in items))
        self.assertEqual(authored, original)

    def test_paper_score_alias_rejects_conflicts_and_invalid_points(self):
        for index, fields in enumerate(({"score": 3, "weight": 2}, {"score": "2"},
                                        {"score": None}, {"score": True}, {"score": float("inf")},
                                        {"score": float("nan")}, {"score": 0}, {"score": 101})):
            authored = self.task_result()
            item = authored["rubric"]["items"][0]
            item.pop("weight")
            item.update(fields)
            def role(config, name, payload, stage_dir, campaign_root, input_files=None):
                result = self.material_result(payload) if name == "companion_author" else authored
                return {"result": result, "receipt": {}, "artifact_root": self.native}
            with self.subTest(fields=fields), patch("wgforge.workflow.json_role", side_effect=role), self.assertRaises(ForgeError):
                author_paper_scenario(self.plan, self.manifest, self.base, self.root / str(index), self.config)

    def test_both_native_authors_receive_original_instruction_and_correct_lineage(self):
        companion = self.native_record()
        source_memory = [{"url": "https://example.org/old-source.csv", "sha256": "a" * 64}]
        planned_memory = [{"url": "https://example.org/planned-source.csv", "sha256": "b" * 64,
                           "scenario_summary": "An earlier catalog reconciliation already used this business situation."}]
        cases = [("plan_wins", planned_memory, planned_memory),
                 ("empty_plan_wins", [], []),
                 ("manifest_fallback", None, source_memory)]
        runtime_config = {"harness": "stirrup", "stirrup": {"sandbox": {"backend": "docker"}}}
        for label, plan_memory, expected_memory in cases:
            with self.subTest(case=label):
                plan = deepcopy(self.plan)
                plan["instruction"] = "Construct one geoscientist work unit focused on reconciling observation coverage for the weekly operations review."
                if plan_memory is not None:
                    plan["lineage_memory"] = plan_memory
                manifest = deepcopy(self.manifest)
                manifest["lineage_memory"] = source_memory
                seen = []
                def tool_stage(payload, input_files, run_dir, config, role, resume=False):
                    seen.append((role, deepcopy(payload), {name: sha256(path) for name, path in input_files.items()}))
                    self.assertEqual(payload["synthesis_request"]["instruction"], plan["instruction"])
                    self.assertEqual(payload["synthesis_request"]["lineage_memory"], expected_memory)
                    self.assertEqual(payload["synthesis_request"]["occupation_playbook"], plan["occupation_playbook"])
                    self.assertNotRegex(payload["instructions"], r"\{\s*[A-Z_]+\s*\}")
                    if role == "companion_author":
                        # These are the instructions the native tool agent really
                        # receives, after json_role has applied its path adapter.
                        self.assertRegex(payload["instructions"], r"place the combined package into /workspace/output/reference_files/")
                        self.assertNotRegex(payload["instructions"], r"place the combined package into /workspace/input/")
                        self.assertEqual(payload["reference_path_adapter"]["anchors"], "/workspace/input/anchors/")
                        self.assertEqual(len(input_files), 1)
                        return {"result": self.material_result(payload, [companion]), "receipt": {}, "artifact_root": self.native}
                    self.assertEqual(role, "task_author")
                    self.assertEqual(sha256(input_files["companions/policy.md"]), companion["sha256"])
                    self.assertEqual(payload["reference_file_manifest"]["file_hashes"]["companions/policy.md"], companion["sha256"])
                    return {"result": self.task_result(), "receipt": {}, "artifact_root": self.native}
                with patch("wgforge.workflow.role_config", return_value=runtime_config), patch("wgforge.stirrup_runtime.run_json_task", side_effect=tool_stage):
                    final = author_paper_scenario(plan, manifest, self.base, self.root / label, self.config)
                self.assertEqual([item[0] for item in seen], ["companion_author", "task_author"])
                self.assertEqual(seen[0][1]["synthesis_request"], seen[1][1]["synthesis_request"])
                self.assertEqual(sha256(final / "public" / "companions" / "policy.md"), companion["sha256"])
                source_name = "anchors/" + self.manifest["sources"][0]["id"] + ".csv"
                self.assertEqual(seen[0][2][source_name], seen[1][2][source_name])

    def test_researcher_tool_stage_receives_actual_instruction_and_search_limit(self):
        instruction = "Find public source files supporting occupational reconciliation without reusing the earlier lineage's anchors."
        payload = {"instructions": prompts.paper_stage("stage_1_discovery", "Save discovery metadata and source files."),
                   "synthesis_instruction": instruction, "lineage_memory": [{"url": "https://example.org/already-used.csv"}],
                   "occupation_playbook": self.plan["occupation_playbook"]}
        runtime_config = {"harness": "stirrup", "stirrup": {"sandbox": {"backend": "docker"},
                                                            "max_searches": 7, "max_search_attempts": 99}}
        observed = []
        def researcher(bound_payload, input_files, run_dir, config, role, resume=False):
            observed.append(deepcopy(bound_payload))
            self.assertEqual(role, "researcher")
            self.assertEqual(input_files, {})
            self.assertIn(instruction, bound_payload["instructions"])
            self.assertIn("If 7 searches pass", bound_payload["instructions"])
            self.assertNotIn("If 99 searches pass", bound_payload["instructions"])
            self.assertEqual(bound_payload["lineage_memory"], payload["lineage_memory"])
            self.assertEqual(bound_payload["reference_path_adapter"]["anchors"], "/workspace/output/anchors/")
            self.assertNotRegex(bound_payload["instructions"], r"\{\s*[A-Z_]+\s*\}")
            return {"result": {"status": "offline-test"}, "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.role_config", return_value=runtime_config), patch("wgforge.stirrup_runtime.run_json_task", side_effect=researcher):
            result = json_role(self.config, "researcher", payload, self.root / "researcher", self.root)
        self.assertEqual(len(observed), 1)
        self.assertEqual(result["result"]["status"], "offline-test")

    def test_invalid_native_hash_stops_before_task_author(self):
        companion = self.native_record()
        companion["sha256"] = "0" * 64
        calls = []
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            calls.append(name)
            return {"result": self.material_result(payload, [companion]), "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role), self.assertRaisesRegex(ForgeError, "digest is wrong"):
            author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(calls, ["companion_author"])

    def test_material_stage_must_not_author_request_or_rubric(self):
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            result = self.material_result(payload)
            result["request"] = self.request()
            return {"result": result, "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role), self.assertRaisesRegex(ForgeError, "before the request"):
            author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)

    def test_task_author_cannot_change_reference_packet(self):
        companion = self.native_record()
        def role(config, name, payload, stage_dir, campaign_root, input_files=None):
            if name == "companion_author":
                return {"result": self.material_result(payload, [companion]), "receipt": {}, "artifact_root": self.native}
            input_files["companions/policy.md"].write_text("Changed contents", encoding="utf-8")
            return {"result": self.task_result(), "receipt": {}, "artifact_root": self.native}
        with patch("wgforge.workflow.json_role", side_effect=role), self.assertRaisesRegex(ForgeError, "changed a reference"):
            author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)

    def test_native_cache_preserves_old_bytes_across_repairs(self):
        companion = self.native_record()
        cache = self.root / "intake"
        original, _ = stage_native_companions([companion], self.native, cache)
        old_path = cache / original[0]["artifact_path"]
        path = self.native / companion["artifact_path"]
        path.write_text("Internal review scope\nThe internal review now occurs on Tuesday.\n", encoding="utf-8")
        changed = {**companion, "sha256": sha256(path)}
        revised, _ = stage_native_companions([changed], self.native, cache, previous_base=cache)
        self.assertNotEqual(original[0]["artifact_path"], revised[0]["artifact_path"])
        self.assertEqual(sha256(old_path), companion["sha256"])
        again, _ = stage_native_companions(original, None, cache, previous_base=cache)
        self.assertEqual(again, original)

    def _evolution(self, audit_fn, repair_fn=None, config=None):
        scenario = self.root / "scenario"
        render_scenario(self.document(), self.base, scenario)
        def solver(config, role, current, stage, campaign):
            (Path(stage) / "workspace" / "output").mkdir(parents=True, exist_ok=True)
            return {}
        def audit(current, executor, out, config, campaign):
            result = audit_fn(verify_bundle(current))
            write_json(Path(out) / "audit.json", result)
            return result
        with patch("wgforge.workflow.solver_role", side_effect=solver), patch("wgforge.workflow.audit_run", side_effect=audit), patch("wgforge.workflow.json_role", side_effect=repair_fn) as roles:
            state = evolve_scenario(scenario, self.root / "evolution", config or self.config)
        return state, roles

    def _defective_audit(self, document, agent_only=False):
        criterion = document["rubric"]["items"][0]["id"]
        row = {"criterion_id": criterion, "labels": ["Agent"] if agent_only else ["Rubric"],
               "explanation": "Offline test diagnosis", "evidence": ["Test material location"]}
        return {"scores": [{"criterion_id": item["id"], "score": 0 if item["id"] == criterion else item["weight"],
                            "rationale": "Offline test", "evidence": ["Test"]} for item in document["rubric"]["items"]],
                "defects": [] if agent_only else [row], "attribution": [row]}

    def test_replacement_id_cannot_reset_three_round_defect_streak(self):
        repairs = []
        def repair(config, role, payload, stage, campaign, input_files=None):
            self.assertEqual(role, "repairer")
            revised = deepcopy(payload["scenario"])
            old_id = revised["rubric"]["items"][0]["id"]
            new_id = old_id + "r"
            revised["rubric"]["items"][0]["id"] = new_id
            revised["rubric"]["items"][0]["criterion"] = "A replacement that the next audit still finds defective"
            revised["revision"] += 1
            repairs.append((old_id, new_id))
            return {"result": {"scenario": revised, "changes": [{"criterion_id": old_id,
                                "reason": "Correct an unsupported requirement without reducing occupational scope",
                                "changed_paths": ["rubric.items[0]"]}], "criterion_mapping": {old_id: new_id}},
                    "receipt": {}, "artifact_root": self.native}
        state, roles = self._evolution(self._defective_audit, repair)
        self.assertEqual(state["status"], "discarded")
        self.assertEqual(len(state["rounds"]), 3)
        self.assertEqual(state["defect_streaks"], {"c01": 3})
        self.assertEqual(repairs, [("c01", "c01r"), ("c01r", "c01rr")])
        self.assertEqual(roles.call_count, 2)  # No extra repair-review stage in the paper.

    def test_agent_only_failure_accepts_scenario_without_weakening_it(self):
        state, roles = self._evolution(lambda document: self._defective_audit(document, agent_only=True))
        self.assertEqual(state["status"], "accepted")
        self.assertEqual(state["rounds"][0]["agent_only_failures"], ["c01"])
        roles.assert_not_called()

    def test_removal_plus_linked_successor_preserves_defect_identity(self):
        def repair(config, role, payload, stage, campaign, input_files=None):
            revised = deepcopy(payload["scenario"])
            old_id = revised["rubric"]["items"][0]["id"]
            new_id = old_id + "new"
            revised["rubric"]["items"][0]["id"] = new_id
            revised["revision"] += 1
            return {"result": {"scenario": revised, "changes": [{"criterion_id": old_id,
                                "reason": "The old requirement is invalid; replace its coverage with this linked requirement.",
                                "changed_paths": ["rubric.items[0]"], "linked_criterion_ids": [new_id]}],
                                "criterion_mapping": {old_id: None}}, "receipt": {}, "artifact_root": self.native}
        state, roles = self._evolution(self._defective_audit, repair)
        self.assertEqual(state["status"], "discarded")
        self.assertEqual(state["defect_streaks"], {"c01": 3})
        self.assertEqual(roles.call_count, 2)

    def test_dependent_new_item_inherits_actual_source_defect_identity(self):
        def repair(config, role, payload, stage, campaign, input_files=None):
            revised = deepcopy(payload["scenario"])
            old_id = revised["rubric"]["items"][0]["id"]
            new_id = old_id + "next"
            revised["rubric"]["items"][0]["id"] = new_id
            revised["revision"] += 1
            return {"result": {"scenario": revised, "changes": [
                {"criterion_id": old_id, "reason": "Remove this unsupported criterion while preserving its professional coverage.",
                 "changed_paths": ["rubric.items[0]"]},
                {"criterion_id": new_id, "source_defect_ids": [old_id], "reason": "Add its source-grounded dependent replacement.",
                 "changed_paths": ["rubric.items[0]"]}], "criterion_mapping": {old_id: None}},
                    "receipt": {}, "artifact_root": self.native}
        state, roles = self._evolution(self._defective_audit, repair)
        self.assertEqual(state["status"], "discarded")
        self.assertEqual(state["defect_streaks"], {"c01": 3})
        self.assertEqual(roles.call_count, 2)

    def test_paper_config_cannot_relabel_legacy_pilot_before_model_calls(self):
        document = self.document()
        document.pop("synthesis_protocol")
        scenario = self.root / "legacy_scenario"
        render_scenario(document, self.base, scenario)
        with patch("wgforge.workflow.solver_role") as solver, self.assertRaisesRegex(ForgeError, "legacy pilot"):
            evolve_scenario(scenario, self.root / "generation", self.config)
        solver.assert_not_called()
        with patch("wgforge.workflow.json_role") as role, self.assertRaisesRegex(ForgeError, "legacy pilot"):
            audit_run(scenario, self.root / "missing_run", self.root / "audit", self.config)
        role.assert_not_called()

    def test_both_review_setting_names_cannot_add_nonpaper_stage(self):
        scenario = self.root / "scenario"
        render_scenario(self.document(), self.base, scenario)
        for setting in ("independent_review", "independent_repair_review"):
            config = deepcopy(self.config)
            config["repair"][setting] = True
            with self.subTest(setting=setting), patch("wgforge.workflow.solver_role") as solver, self.assertRaisesRegex(ForgeError, "additional independent"):
                evolve_scenario(scenario, self.root / setting, config)
            solver.assert_not_called()

    def test_operational_round_limit_is_not_paper_defect_discard(self):
        config = deepcopy(self.config)
        config["repair"]["max_rounds"] = 1
        state, roles = self._evolution(self._defective_audit, config=config)
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual(state["defect_streaks"], {"c01": 1})
        roles.assert_not_called()

    def test_paper_authoring_refuses_text_only_role(self):
        self.config["roles"]["companion_author"]["harness"] = "legacy"
        with self.assertRaisesRegex(ForgeError, "Stirrup code/view"):
            author_paper_scenario(self.plan, self.manifest, self.base, self.root / "generation", self.config)

    def test_all_paper_placeholders_resolve_to_payload_and_sandbox_paths(self):
        files = {"anchors/source.pdf": self.base / self.manifest["sources"][0]["path"]}
        for stage in ("stage_1_discovery", "stage_2a_companions", "stage_2b_task_rubric",
                      "stage_4_scoring", "stage_5_attribution", "stage_6_repair"):
            with self.subTest(stage=stage):
                payload = {"instructions": prompts.paper_stage(stage, "Return the required JSON schema."), "rubric_size_target": 40}
                cfg = {"stirrup": {"sandbox": {"backend": "e2b"}}}
                bound = prompts.bind_paper_stage(payload, cfg, {} if stage == "stage_1_discovery" else files)
                self.assertNotRegex(bound["instructions"], r"\{\s*[A-Z_]+\s*\}")
                if stage in {"stage_2a_companions", "stage_6_repair"}:
                    self.assertIn("/home/user/input/anchors/", bound["instructions"])
                    self.assertIn("/home/user/output/companions/", bound["instructions"])
                if stage == "stage_1_discovery":
                    self.assertIn("/home/user/output/anchors/", bound["instructions"])

    def test_abandon_is_auditable_but_never_a_completed_training_solve(self):
        scenario = self.root / "scenario"
        render_scenario(self.document(), self.base, scenario)
        run = self.root / "abandoned"
        (run / "workspace" / "output").mkdir(parents=True)
        write_json(run / "trajectory.json", {"messages": [], "mode": "live"})
        report = {"mode": "live", "completed": False, "abandoned": True, "terminal_status": "abandoned",
                  "abandon_reason": "The referenced required period is absent from the attached file.",
                  "harness": "official_stirrup", "public_digest": digest(tree_hashes(scenario / "public")),
                  "artifact_hashes": {}, "trajectory_digest": sha256(run / "trajectory.json")}
        write_json(run / "run.json", report)
        self.assertTrue(validate_run_binding(scenario, run, allow_abandoned=True)["abandoned"])
        with self.assertRaisesRegex(ForgeError, "did not complete"):
            validate_run_binding(scenario, run)
        report["harness"] = "unverified_adapter"
        write_json(run / "run.json", report)
        with self.assertRaisesRegex(ForgeError, "did not complete"):
            validate_run_binding(scenario, run, allow_abandoned=True)

    def test_nontext_source_requires_recorded_visual_evidence_not_invented_excerpt(self):
        evidence = [{"record": {"id": "scan"}, "path": "anchors/scan.pdf", "extracted_text": "",
                     "unverified_visual_content": True}]
        assessment = [{"source_id": "scan", "usable": True,
                       "occupation_fit": "The scanned ledger supports substantive occupation-specific reconciliation work.",
                       "professional_operations": ["Reconcile the observed transaction lines"], "limitations": "The source is scanned.",
                       "inspection_evidence": [{"source_path": "anchors/scan.pdf", "viewed_path": "/workspace/output/scan-page.png",
                                                "finding": "The displayed page contains transaction identifiers and amounts."}]}]
        with self.assertRaises(UnsuitableSource):
            validate_source_assessment(assessment, evidence)
        stage = self.root / "visual_stage"
        trajectory = stage / "trajectory.json"
        write_json(trajectory, {"messages": [
            {"role": "assistant", "tool_calls": [{"id": "view1", "function": {"name": "view_image", "arguments": '{"path":"/workspace/output/scan-page.png"}'}}]},
            {"role": "tool", "tool_call_id": "view1", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}]}]})
        observed = _recorded_visual_inspections(stage, {"trajectory_digest": sha256(trajectory)})
        self.assertEqual(validate_source_assessment(assessment, evidence, tool_inspection=observed), assessment)
        altered = _recorded_visual_inspections(stage, {"trajectory_digest": "0" * 64})
        with self.assertRaises(UnsuitableSource):
            validate_source_assessment(assessment, evidence, tool_inspection=altered)

    def test_audit_routes_only_unsatisfied_items_and_never_uploads_hidden_oracle(self):
        document = self.document()
        document["private_reference"] = {"hidden_answer": "This must not be a paper scoring source."}
        document["rubric"]["items"][-1]["weight"] = -2
        scenario, run = self.root / "audit_scenario", self.root / "audit_run"
        render_scenario(document, self.base, scenario)
        output = run / "workspace" / "output"
        output.mkdir(parents=True)
        (output / "briefing.md").write_text("Internal briefing\nThe observations are from the bounded saved catalog.\n")
        write_json(run / "trajectory.json", {"messages": [], "mode": "live", "fixture": True})
        write_json(run / "run.json", {"mode": "live", "completed": True,
                   "public_digest": digest(tree_hashes(scenario / "public")), "artifact_hashes": tree_hashes(output),
                   "trajectory_digest": sha256(run / "trajectory.json")})
        calls = []
        def role(config, name, payload, stage, campaign, input_files=None):
            calls.append(name)
            self.assertNotIn("private_reference", payload)
            self.assertNotIn("hidden_answer", str(payload))
            self.assertIn("submitted_files/briefing.md", input_files)
            if name == "judge":
                scores = [{"criterion_id": item["id"], "score": 0 if item["id"] == "c01" else max(item["weight"], 0),
                           "rationale": "Offline protocol test", "evidence": ["briefing.md, paragraph 1"]}
                          for item in document["rubric"]["items"]]
                return {"result": {"scores": scores}, "receipt": {"fixture": True}}
            self.assertEqual(payload["unsatisfied_criterion_ids"], ["c01"])
            return {"result": {"attribution": [{"criterion_id": "c01", "labels": ["Agent"],
                                                  "explanation": "The fair requirement was not met.", "evidence": ["briefing.md"]}]},
                    "receipt": {"fixture": True}}
        with patch("wgforge.workflow.json_role", side_effect=role):
            audit = audit_run(scenario, run, self.root / "audit", self.config)
        self.assertEqual(calls, ["judge", "attributor"])
        self.assertTrue(audit["scenario_accepted"])
        self.assertFalse(audit["executor_fully_satisfied"])
        self.assertEqual(audit["defects"], [])


if __name__ == "__main__":
    unittest.main()
