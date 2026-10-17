"""Portable integration tests. Deterministic controls never become training data."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from wgforge.common import ForgeError, digest, read_json, sha256, write_json
from wgforge.demo import run_demo
from wgforge.schema import criterion_satisfied, validate_scores, validate_attribution, validate_repair, public_contract
from wgforge.materialize import verify_bundle, render_scenario
from wgforge.dataset import calibration_summary, export_sft, validate_trajectory
from wgforge.workflow import UnsuitableSource, validate_source_assessment, validate_audit_record
from wgforge.campaign import register_anchors
from wgforge.training import encode_example, pack_examples

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.report = run_demo(cls.root / "demo", ROOT)
        cls.scenario_path = cls.root / "demo" / "scenario"
        cls.document = verify_bundle(cls.scenario_path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_source_control_is_truthful(self):
        self.assertEqual(self.report["facts"]["event_count"], 200)
        self.assertEqual(self.report["facts"]["mean_magnitude"], "4.08")
        self.assertEqual(self.report["facts"]["threshold_event_count"], 88)
        self.assertFalse(self.report["model_called"])
        self.assertFalse(self.report["sft_export_eligible"])

    def test_repeatable_native_artifacts(self):
        other = run_demo(self.root / "repeat", ROOT)
        self.assertEqual(other["artifact_hashes"], self.report["artifact_hashes"])

    def test_demo_is_not_a_production_run(self):
        from wgforge.workflow import validate_run_binding
        with self.assertRaises((ForgeError, FileNotFoundError)):
            validate_run_binding(self.scenario_path, self.root / "demo" / "control")

    def test_public_contract_excludes_private_fields(self):
        public = public_contract(self.document)
        self.assertNotIn("rubric", public)
        self.assertNotIn("private_reference", public)
        bad = copy.deepcopy(self.document); bad["request"]["rubric"] = "private"
        with self.assertRaises(ForgeError):
            public_contract(bad)

    def test_negative_criterion_full_satisfaction_is_zero(self):
        penalty = self.document["rubric"]["items"][-1]
        self.assertTrue(criterion_satisfied(penalty, 0))
        self.assertFalse(criterion_satisfied(penalty, -5))

    def test_invalid_score_and_missing_evidence_rejected(self):
        scores = [{"criterion_id": i["id"], "score": max(0, i["weight"]), "rationale": "QA fixture only", "evidence": ["fixture evidence"]} for i in self.document["rubric"]["items"]]
        validate_scores(self.document, scores)
        scores[0]["evidence"] = []
        with self.assertRaises(ForgeError):
            validate_scores(self.document, scores)
        scores[0]["evidence"] = ["fixture evidence"]; scores[0]["score"] = 100
        with self.assertRaises(ForgeError):
            validate_scores(self.document, scores)

    def test_multilabel_attribution_is_allowed(self):
        scores = [{"criterion_id": i["id"], "score": max(0, i["weight"]), "rationale": "QA fixture", "evidence": ["fixture"]} for i in self.document["rubric"]["items"]]
        scores[0]["score"] = 0
        self.assertEqual(len(validate_attribution(self.document, scores, [{"criterion_id": "c01", "labels": ["Agent", "Task"], "explanation": "Both observed in QA fixture", "evidence": ["fixture"]}])), 1)
        with self.assertRaises(ForgeError):
            validate_attribution(self.document, scores, [])

    def test_repair_cannot_drop_or_lower_criterion(self):
        revised = copy.deepcopy(self.document); revised["revision"] += 1
        revised["rubric"]["items"][0]["weight"] -= 1
        with self.assertRaises(ForgeError):
            validate_repair(self.document, revised, {"c01"})
        revised = copy.deepcopy(self.document); revised["revision"] += 1; revised["rubric"]["items"].pop()
        with self.assertRaises(ForgeError):
            validate_repair(self.document, revised, {"c01"})

    def test_companion_csv_and_pptx_disclose_fiction_inside_files(self):
        from wgforge.artifacts import extract_artifact
        document = copy.deepcopy(self.document)
        disclosure = "Fictional scenario material for a software test."
        document["companions"] = [
            {"id": "csv_context", "path": "context.csv", "format": "csv", "fictitious": True, "disclosure": disclosure, "spec": {"tables": [{"headers": ["label", "value"], "rows": [["A", 1]]}]}},
            {"id": "slide_context", "path": "context.pptx", "format": "pptx", "fictitious": True, "disclosure": disclosure, "spec": {"slides": [{"title": "Scenario", "text": "Test-only content"}]}}]
        out = self.root / "companion_formats"
        render_scenario(document, ROOT / "examples" / "anchors", out)
        for name in ("context.csv", "context.pptx"):
            self.assertIn(disclosure, extract_artifact(out / "public" / "companions" / name)["text"])

    def test_source_assessment_requires_actual_excerpt(self):
        evidence = [{"record": {"id": "s1"}, "extracted_text": "A sufficiently long actual source excerpt with concrete occupational facts."}]
        assessment = [{"source_id": "s1", "usable": True, "occupation_fit": "This source supports concrete occupational data reconciliation and reporting.", "professional_operations": ["Reconcile independently reported counts"], "evidence_excerpt": evidence[0]["extracted_text"], "limitations": "Test only"}]
        validate_source_assessment(assessment, evidence)
        assessment[0]["evidence_excerpt"] = "Fabricated content absent from the original source document"
        with self.assertRaises(UnsuitableSource):
            validate_source_assessment(assessment, evidence)

    def test_source_operations_text_is_losslessly_normalized(self):
        evidence = [{"record": {"id": "s1"}, "extracted_text": "A sufficiently long actual source excerpt with concrete occupational facts."}]
        operations = "Reconcile independently reported counts; document the source coverage."
        assessment = [{"source_id": "s1", "usable": True,
                       "occupation_fit": "This source supports concrete occupational data reconciliation and reporting.",
                       "professional_operations": operations,
                       "evidence_excerpt": evidence[0]["extracted_text"], "limitations": "Test only"}]
        original = copy.deepcopy(assessment)
        normalized = validate_source_assessment(assessment, evidence)
        self.assertEqual(normalized[0]["professional_operations"], [operations])
        self.assertEqual(assessment, original)
        for bad in ("", "too short", [], [3]):
            with self.subTest(value=bad):
                assessment[0]["professional_operations"] = bad
                with self.assertRaises(UnsuitableSource):
                    validate_source_assessment(assessment, evidence)

    def test_lineage_dedup_includes_discarded_sources(self):
        registry = self.root / "registry.json"
        source = self.document["anchor_sources"]
        register_anchors(registry, "one-lineage", "first", source, "discarded")
        with self.assertRaises(ForgeError):
            register_anchors(registry, "one-lineage", "second", source)
        # Updating the same reservation is idempotent.
        self.assertEqual(register_anchors(registry, "one-lineage", "first", source, "discarded")["scenario_id"], "first")

    def test_calibration_is_labeled_adaptation(self):
        result = calibration_summary([.2, .4, .6, .8])
        self.assertEqual(result["selection"], "selected")
        self.assertIn("not reported WorkGenesis", result["method"])
        self.assertFalse(result["changes_scenario"])

    def test_empty_training_export_is_rejected(self):
        with self.assertRaises(ForgeError):
            export_sft([], self.root / "no_training")

    def test_compacted_trace_cannot_be_flat_exported(self):
        with self.assertRaisesRegex(ForgeError, "Compacted"):
            validate_trajectory({"context_events": [{"id": 1}]}, {}, {})

    def test_cli_doctor_and_help(self):
        result = subprocess.run([sys.executable, "-m", "wgforge", "doctor"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["live_calls_made"])
        result = subprocess.run([sys.executable, "-m", "wgforge", "campaign", "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def test_train_dryrun_rejects_unsealed_file(self):
        root = self.root / "unsealed_training"; root.mkdir()
        write_json(root / "manifest.json", {"files": {}})
        (root / "train.jsonl").write_text('{"metadata":{"mode":"live_qualified"}}\n')
        result = subprocess.run([sys.executable, str(ROOT / "recipes" / "train_sft.py"), "--dataset", str(root), "--model", "unused", "--output", str(root / "out")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("sealed SFT export", result.stderr)

    def test_train_dryrun_validates_numerical_options(self):
        result = subprocess.run([sys.executable, str(ROOT / "recipes" / "train_sft.py"), "--dataset", "unused", "--model", "unused", "--output", "unused", "--max-length", "0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("max-length", result.stderr)


class SimpleTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False, **kwargs):
        return "".join("<" + m["role"] + ">" + str(m.get("content") or "") + "</turn>" for m in messages) + ("<assistant>" if add_generation_prompt else "")
    def __call__(self, text, **kwargs):
        return {"input_ids": [ord(c) for c in text], "offset_mapping": [(n, n+1) for n in range(len(text))]}


class TrainingTests(unittest.TestCase):
    def test_assistant_only_mask(self):
        record = {"metadata": {"mode": "live_qualified"}, "messages": [{"role": "system", "content": "PRIVATE_TEST_PROMPT"}, {"role": "user", "content": "USER_TEST_INPUT"}, {"role": "assistant", "content": "VISIBLE_ASSISTANT"}]}
        result = encode_example(SimpleTokenizer(), record, 1000)
        labeled = "".join(chr(n) for n in result["labels"] if n != -100)
        self.assertIn("VISIBLE_ASSISTANT", labeled)
        self.assertNotIn("PRIVATE_TEST_PROMPT", labeled)
        self.assertNotIn("USER_TEST_INPUT", labeled)

    def test_no_silent_truncation(self):
        record = {"metadata": {"mode": "live_qualified"}, "messages": [{"role": "user", "content": "long input"}, {"role": "assistant", "content": "answer"}]}
        with self.assertRaises(ForgeError):
            encode_example(SimpleTokenizer(), record, 5)

    def test_whole_example_packing_preserves_labels(self):
        rows = [{"input_ids": [1, 2, 3], "attention_mask": [1, 1, 1], "labels": [-100, 2, 3]} for _ in range(3)]
        packed = pack_examples(rows, 6)
        self.assertEqual([len(r["input_ids"]) for r in packed], [6, 3])
        self.assertEqual(sum(len(r["labels"]) for r in packed), 9)

if __name__ == "__main__":
    unittest.main()
