"""Admission/export regressions using explicitly synthetic protocol fixtures.

All files are temporary. These fixtures are never production training examples.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wgforge.common import ForgeError, canonical, digest, read_json, sha256, tree_hashes, write_json
from wgforge.demo import run_demo
from wgforge.materialize import verify_bundle, render_scenario
from wgforge.dataset import sign_review, qualify, export_sft

ROOT = Path(__file__).resolve().parents[1]
KEY = "SYNTHETIC_UNIT_TEST_REVIEW_KEY_ONLY_" * 2


class DatasetGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        run_demo(self.root / "demo", ROOT)
        document = verify_bundle(self.root / "demo" / "scenario")
        document["request"]["deliverables"] = [{"path": "answer.txt", "format": "txt", "description": "Test fixture answer"}]
        self.scenario = self.root / "scenario"
        render_scenario(document, ROOT / "examples" / "anchors", self.scenario)
        self.run = self.root / "teacher"
        output = self.run / "workspace" / "output"; output.mkdir(parents=True)
        (output / "answer.txt").write_text("TEST FIXTURE ONLY. Not a model-generated professional artifact.\n")
        hashes = tree_hashes(output)
        task = read_json(self.scenario / "public" / "task.json")
        messages = [{"role": "system", "content": "Synthetic unit-test transport only"}, {"role": "user", "content": canonical(task)},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "finish_1", "type": "function", "function": {"name": "finish", "arguments": '{"summary":"Test fixture only"}'}}]},
                    {"role": "tool", "tool_call_id": "finish_1", "content": canonical({"finished": True, "artifact_hashes": hashes})}]
        write_json(self.run / "trajectory.json", {"mode": "live", "role": "teacher", "isolated_context": True, "messages": messages, "tools": []})
        receipt = {"mode": "live", "role": "teacher", "completed": True, "public_digest": digest(tree_hashes(self.scenario / "public")),
                   "artifact_hashes": hashes, "trajectory_digest": sha256(self.run / "trajectory.json"), "prompt_digest": digest(messages[:2]),
                   "model": "SYNTHETIC_UNIT_TEST_TRANSPORT_ONLY", "request_receipts": [{"status": "succeeded"}]}
        write_json(self.run / "run.json", receipt)
        scores = [{"criterion_id": item["id"], "score": max(item["weight"], 0), "rationale": "Synthetic audit fixture only", "evidence": ["unit-test fixture"]} for item in document["rubric"]["items"]]
        raw = sum(s["score"] for s in scores)
        audit = {"mode": "live_model_audit", "binding": {"bundle_digest": read_json(self.scenario / "manifest.json")["bundle_digest"], "run_receipt_sha256": sha256(self.run / "run.json"), "artifact_hashes": hashes},
                 "scores": scores, "attribution": [], "defects": [], "scenario_accepted": True, "executor_fully_satisfied": True,
                 "raw_score": raw, "positive_weight_total": raw, "normalized_score": 1, "missing_deliverables": [], "unverified_visual_files": []}
        audit["audit_digest"] = digest(audit)
        self.audit = self.root / "audit.json"; write_json(self.audit, audit)
        self.review = self.root / "review.json"
        self.environment = patch.dict(os.environ, {"WG_REVIEW_KEY": KEY}); self.environment.start()
        sign_review(self.scenario, self.run, self.audit, self.review, "SYNTHETIC_TEST_REVIEWER", "WG_REVIEW_KEY",
                    {k: True for k in ("source_rights", "semantic_correctness", "privacy_safety", "no_benchmark_overlap")})

    def tearDown(self):
        self.environment.stop(); self.temp.cleanup()

    def admit(self, filename="qualification.json", split="train"):
        path = self.root / filename
        qualify(self.scenario, self.run, self.audit, self.review, path, split=split)
        return path

    def test_admission_export_plumbing_only(self):
        manifest = export_sft([self.admit()], self.root / "export")
        self.assertEqual(manifest["counts"]["train"], 1)
        self.assertEqual(manifest["contamination_status"], "not_checked_no_heldout_corpus")
        self.assertFalse(manifest["training_executed"])
        self.assertTrue(manifest["anchor_hashes"])

    def test_changed_review_rejected(self):
        review = read_json(self.review); review["payload"]["reviewer"] = "changed"; write_json(self.review, review)
        with self.assertRaises(ForgeError): self.admit()

    def test_changed_audit_rejected(self):
        audit = read_json(self.audit); audit["scores"] = []; write_json(self.audit, audit)
        with self.assertRaises(ForgeError): self.admit()

    def test_changed_artifacts_rejected(self):
        path = self.run / "workspace" / "output" / "answer.txt"; path.write_text("changed")
        with self.assertRaises(ForgeError): self.admit()

    def test_changed_qualification_rejected(self):
        path = self.admit(); record = read_json(path); record["split"] = "test"; write_json(path, record)
        with self.assertRaises(ForgeError): export_sft([path], self.root / "export")

    def test_borrowed_review_or_fixture_run_rejected(self):
        receipt = read_json(self.run / "run.json"); receipt["mode"] = "fixture"; write_json(self.run / "run.json", receipt)
        with self.assertRaises(ForgeError): self.admit()

    def test_solver_cannot_be_final_teacher(self):
        receipt = read_json(self.run / "run.json"); receipt["role"] = "solver"; write_json(self.run / "run.json", receipt)
        with self.assertRaises(ForgeError): self.admit()

    def test_same_source_cannot_cross_splits(self):
        train = self.admit("train_q.json", "train"); test = self.admit("test_q.json", "test")
        with self.assertRaisesRegex(ForgeError, "different dataset splits"):
            export_sft([train, test], self.root / "export")

    def test_heldout_anchor_blocks_export(self):
        record = self.admit(); exclusion = self.root / "heldout.json"
        write_json(exclusion, {"anchor_hashes": read_json(record)["anchor_hashes"]})
        with self.assertRaisesRegex(ForgeError, "Held-out"):
            export_sft([record], self.root / "export", heldout_manifest=exclusion)

    def test_duplicate_trace_rejected(self):
        record = self.admit()
        with self.assertRaisesRegex(ForgeError, "Duplicate trajectory"):
            export_sft([record, record], self.root / "export")

if __name__ == "__main__":
    unittest.main()
