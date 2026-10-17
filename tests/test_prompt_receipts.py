"""Execution receipts stay auditable without becoming repeated author context."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_paper_workflow as fixtures
from wgforge.common import canonical, digest, read_json, sha256, tree_hashes, write_json
from wgforge.paper_acquisition import CHECKS
from wgforge.sources import load_source_manifest
from wgforge.stirrup_runtime import _publish
from wgforge.synthesis import author_paper_scenario


ROOT = Path(__file__).resolve().parents[1]
CATALOG_SENTINEL = "TRANSPORT_CATALOG_NOT_SOURCE_EVIDENCE"


class SynthesisReceiptBoundaryTests(unittest.TestCase):
    setUp = fixtures.PaperWorkflowTests.setUp
    tearDown = fixtures.PaperWorkflowTests.tearDown
    request = fixtures.PaperWorkflowTests.request
    rubric = fixtures.PaperWorkflowTests.rubric
    material_result = fixtures.PaperWorkflowTests.material_result
    task_result = fixtures.PaperWorkflowTests.task_result
    native_record = fixtures.PaperWorkflowTests.native_record

    def test_cached_inflated_receipt_never_enters_either_author_but_provenance_survives(self):
        source = self.manifest["sources"][0]
        source["suitability"] = {key: {"pass": True, "reason": "Inspected this source."} for key in CHECKS}
        source["content_summary"] = "The verified observation catalog supports an occupational briefing."
        source["key_fields"] = ["magnitude", "event_time"]
        source["license_or_terms"] = "Retain the original public source terms."
        catalog = {"models": [{"base_instructions": (CATALOG_SENTINEL + " ") * 2500}]}
        receipt = {"request_receipts": [{"tool_isolation_catalog": catalog,
                    "tool_isolation_catalog_digest": digest(catalog), "charged_tokens": 20000} for _ in range(9)],
                   "completed": True}
        self.manifest["discovery"] = {"status": "success", "search_log": ["Catalog search query"],
            "rejected_candidates": ["An empty report"], "notes_for_next_stage": "Do not infer a complete global census.",
            "lineage_memory_digest": digest([]), "method": "Opened real files and checked the seven suitability criteria.",
            "receipt": receipt}
        cached = self.base / "manifest.json"
        write_json(cached, self.manifest)
        manifest = load_source_manifest(cached)
        manifest_before, files_before = deepcopy(manifest), tree_hashes(self.base)
        companion = self.native_record()
        seen = []

        def stage(config, role, payload, stage_dir, campaign_root, input_files=None):
            seen.append(role)
            self.assertNotIn(CATALOG_SENTINEL, canonical(payload))
            if role == "companion_author":
                expected = deepcopy(manifest_before)
                expected["discovery"].pop("receipt")
                self.assertEqual(payload["anchor_manifest"], expected)
                self.assertEqual(payload["sources"][0]["record"], source)
                self.assertEqual(payload["anchor_manifest"]["sources"][0]["suitability"], source["suitability"])
                self.assertLess(len(canonical(payload["anchor_manifest"])), len(canonical(manifest_before)) / 10)
                return {"result": self.material_result(payload, [companion]),
                        "receipt": deepcopy(receipt), "artifact_root": self.native}
            self.assertEqual(role, "task_author")
            material = payload["reference_file_manifest"]
            self.assertEqual(material["notes_for_task_synthesis"], "Do not infer a complete global census.")
            self.assertEqual(material["source_hashes"], [source["sha256"]])
            self.assertEqual(material["companions"][0]["sha256"], companion["sha256"])
            parsed_source = next(value for key, value in payload["reference_files_parsed"].items()
                                 if key.startswith("anchors/"))
            self.assertEqual(parsed_source["record"], source)
            self.assertEqual(sha256(input_files["companions/policy.md"]), companion["sha256"])
            return {"result": self.task_result(), "receipt": {"stage": role}, "artifact_root": self.native}

        with patch("wgforge.workflow.json_role", side_effect=stage), \
             patch("subprocess.Popen", side_effect=AssertionError("No CLI or container")), \
             patch("socket.create_connection", side_effect=AssertionError("No model/search/network")):
            initial = author_paper_scenario(self.plan, manifest, self.base, self.root / "generation", self.config)
        self.assertEqual(seen, ["companion_author", "task_author"])
        self.assertEqual(manifest, manifest_before)
        self.assertEqual(read_json(cached), manifest_before)
        self.assertEqual(tree_hashes(self.base), files_before)
        self.assertEqual(sha256(initial / "public" / "companions/policy.md"), companion["sha256"])
        self.assertEqual(read_json(self.root / "generation/generation.json")["companion_receipt"], receipt)


class PublishedReceiptTests(unittest.TestCase):
    def test_full_catalog_stays_in_checkpoint_not_publication_and_digest_remains(self):
        config = read_json(ROOT / "configs/gpt6_astra_subscription_one.json")["roles"]["researcher"]
        catalog = {"models": [{"base_instructions": CATALOG_SENTINEL * 1000}]}
        row = {"index": 0, "status": "succeeded", "prompt_tokens": 100, "completion_tokens": 20,
               "charged_tokens": 120, "charged_usd": 0.01, "reported_cost_usd": 0.01,
               "tool_isolation_catalog": catalog, "tool_isolation_catalog_digest": digest(catalog),
               "provider_api": "codex_cli_subscription", "message": {"role": "assistant", "content": "Done"}}
        state = {"role": "researcher", "requests": [row], "input_hashes": {}, "completed": False,
                 "config_digest": digest(config), "provider_contexts": []}
        before = deepcopy(state)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / "checkpoint.json", state)
            checkpoint_hash = sha256(root / "checkpoint.json")
            report = _publish(root, config, state)
            trace = read_json(root / "trajectory.json")
            self.assertNotIn(CATALOG_SENTINEL, canonical(report))
            self.assertNotIn(CATALOG_SENTINEL, canonical(trace))
            self.assertEqual(report["request_receipts"][0]["tool_isolation_catalog_digest"], digest(catalog))
            self.assertEqual(trace["cli_invocations"][0]["tool_isolation_catalog_digest"], digest(catalog))
            self.assertEqual(read_json(root / "checkpoint.json")["requests"][0]["tool_isolation_catalog"], catalog)
            self.assertEqual(sha256(root / "checkpoint.json"), checkpoint_hash)
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
