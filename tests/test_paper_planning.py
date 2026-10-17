"""Complete paper coverage and grounded adjustable planning; no LLM/network."""
import copy
import csv
import json
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wgforge.planning import PlanningError, build_paper_playbooks, compact_plans, hydrate_plan, load_paper_playbooks, plan_tasks


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_PATH = ROOT / "wgforge/data/occupation_playbooks.json"
MANIFEST_PATH = ROOT / "wgforge/data/anchors/onet_manifest.json"


class PaperPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verified = load_paper_playbooks()

    def plans(self, **settings):
        # Validate real source data once per class, then exercise scheduling at
        # 20K scale without re-reading 50 MB of immutable reference data each time.
        with patch("wgforge.planning.load_paper_playbooks", return_value=self.verified):
            return plan_tasks({"adapter": "paper_occupations", "seed": 42, **settings})

    def test_default_100_covers_all_65_with_counts_one_or_two(self):
        plans = self.plans()
        counts = Counter(plan["profession"] for plan in plans)
        self.assertEqual(len(plans), 100)
        self.assertEqual(len(counts), 65)
        self.assertEqual(Counter(counts.values()), {1: 30, 2: 35})
        self.assertEqual(len({plan["plan_id"] for plan in plans}), 100)

    def test_adjustable_20000_unique_balanced_and_prefix_stable(self):
        small = self.plans(count=100)
        large = self.plans(count=20000)
        self.assertEqual(large[:100], small)
        self.assertEqual(len({plan["plan_id"] for plan in large}), 20000)
        counts = Counter(plan["profession"] for plan in large)
        self.assertEqual(set(counts.values()), {307, 308})
        self.assertEqual(sum(counts.values()), 20000)
        self.assertEqual(len(counts), 65)

    def test_different_seed_changes_order_and_identity(self):
        a, b = self.plans(count=65), self.plans(count=65, seed=43)
        self.assertNotEqual([row["profession"] for row in a], [row["profession"] for row in b])
        self.assertFalse({row["plan_id"] for row in a} & {row["plan_id"] for row in b})

    def test_no_forced_activity_or_output_family(self):
        for plan in self.plans(count=65):
            self.assertEqual(plan["deliverable_family"], "any")
            self.assertEqual(plan["required_file_formats"], [])
            self.assertEqual(plan["activity"], "")
            self.assertNotIn("activity_id", plan)
            self.assertNotIn("activity_provenance", plan)
            self.assertNotIn("onet_importance", plan)
            self.assertTrue(plan["occupation_playbook"]["duties"])
            self.assertFalse(plan["occupation_playbook"]["original_paper_playbook"])

    def test_financial_and_project_management_use_real_task_statements(self):
        by_name = {p["occupation"]: p for p in self.verified["playbooks"]}
        for title, code in [("Financial and Investment Analysts", "13-2051.00"),
                            ("Project Management Specialists", "13-1082.00")]:
            playbook = by_name[title]
            self.assertEqual(playbook["onet_soc_codes"], [code])
            self.assertGreater(len(playbook["duties"]), 10)
            self.assertEqual(playbook["work_activities"], [])
            self.assertIn("onet30_work_activities", playbook["missing_source_tables"][code])
            self.assertTrue(all(row["occupation_code"] == code for row in playbook["duties"]))

    def test_broad_buyers_label_preserved_with_three_explicit_codes(self):
        buyer = next(p for p in self.plans(count=65) if p["profession"] == "Buyers and Purchasing Agents")
        self.assertEqual(buyer["occupation_code"], "13-1020")
        self.assertEqual(buyer["occupation_codes"], ["13-1021.00", "13-1022.00", "13-1023.00"])
        self.assertEqual(buyer["occupation_mapping_kind"], "soc_broad_group")
        self.assertEqual(len(buyer["occupation_playbook"]["descriptions"]), 3)

    def test_all_duties_match_exact_raw_official_rows(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        source = next(row for row in manifest["sources"] if row["id"] == "onet30_task_statements")
        with (MANIFEST_PATH.parent / source["path"]).open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        for playbook in self.verified["playbooks"]:
            self.assertEqual(playbook["source_provenance"][source["id"]]["sha256"], source["sha256"])
            for duty in playbook["duties"]:
                row = rows[duty["source_row"] - 2]
                self.assertEqual((duty["occupation_code"], duty["task_id"], duty["task"]),
                                 (row["O*NET-SOC Code"], row["Task ID"], row["Task"]))

    def test_missing_occupation_fails_instead_of_silent_64_coverage(self):
        data = copy.deepcopy(self.verified)
        data["playbooks"].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playbooks.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(PlanningError, "all 65"):
                load_paper_playbooks({"playbooks_path": path, "onet_manifest_path": MANIFEST_PATH})

    def test_mutated_duty_does_not_acquire_official_provenance(self):
        data = copy.deepcopy(self.verified)
        data["playbooks"][0]["duties"][0]["task"] = "Invented work with a copied official source hash"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playbooks.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(PlanningError, "differ from"):
                load_paper_playbooks({"playbooks_path": path, "onet_manifest_path": MANIFEST_PATH})

    def test_required_source_hash_is_checked_before_building(self):
        catalog = self.verified["paper_occupation_catalog"]
        manifest = json.loads(MANIFEST_PATH.read_text())
        manifest["sources"][0]["sha256"] = "0" * 64
        # Point to existing source paths via an explicit manifest in the same
        # directory without ever changing the real reference manifest.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=MANIFEST_PATH.parent) as handle:
            json.dump(manifest, handle)
            handle.flush()
            with self.assertRaisesRegex(PlanningError, "provenance verification"):
                build_paper_playbooks(catalog, Path(handle.name))

    def test_paper_catalog_does_not_accept_different_occupation_labels(self):
        catalog = copy.deepcopy(self.verified["paper_occupation_catalog"])
        catalog["occupations"][0]["occupation"] = "A different profession"
        with self.assertRaisesRegex(PlanningError, "Table 2"):
            build_paper_playbooks(catalog, MANIFEST_PATH)

    def test_unsupported_coverage_filters_and_forced_family_fail(self):
        for settings in [{"professions": ["Financial Managers"]}, {"occupation_codes": ["11-3031.00"]},
                         {"exclusions": {"professions": ["Financial Managers"]}}, {"heldout_fraction": 0.1},
                         {"sampling": "uniform"}, {"deliverable_families": ["spreadsheet"]}, {"split": "heldout"}]:
            with self.subTest(settings=settings), self.assertRaises(PlanningError):
                self.plans(count=100, **settings)

    def test_original_playbook_claim_cannot_be_forged(self):
        data = copy.deepcopy(self.verified)
        data["playbooks"][0]["original_paper_playbook"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playbooks.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(PlanningError, "differ from"):
                load_paper_playbooks({"playbooks_path": path, "onet_manifest_path": MANIFEST_PATH})

    def test_compact_20000_stores_catalog_once_and_bounds_schedule_size(self):
        plans = self.plans(count=20000)
        packed = compact_plans(plans)
        self.assertEqual(len(packed["plans"]), 20000)
        self.assertEqual(len(packed["playbooks"]), 65)
        self.assertTrue(all("occupation_playbook" not in plan for plan in packed["plans"]))
        schedule_bytes = len(json.dumps(packed["plans"], separators=(",", ":")).encode())
        self.assertLess(schedule_bytes, 10_000_000)
        self.assertLess(len(json.dumps(packed, separators=(",", ":")).encode()),
                        10_000_000 + len(json.dumps(packed["playbooks"], separators=(",", ":")).encode()) + 100)
        for index in (0, 64, 65, 99, 19999):
            self.assertEqual(hydrate_plan(packed["plans"][index], packed["playbooks"]), plans[index])

    def test_compaction_preserves_custom_fields_and_does_not_mutate_inputs(self):
        plans = self.plans(count=2)
        plans[0]["additional_setting"] = {"hint": "custom"}
        packed = compact_plans(plans)
        restored = hydrate_plan(packed["plans"][0], packed["playbooks"])
        self.assertEqual(restored, plans[0])
        restored["occupation_playbook"]["duties"][0]["task"] = "edited output"
        packed["plans"][0]["additional_setting"]["hint"] = "edited pack"
        self.assertNotEqual(restored, plans[0])
        self.assertEqual(plans[0]["additional_setting"], {"hint": "custom"})
        self.assertNotEqual(packed["playbooks"][plans[0]["playbook_id"]]["duties"][0]["task"], "edited output")

    def test_hydration_rejects_missing_tampered_or_mismatched_playbook(self):
        packed = compact_plans(self.plans(count=1))
        plan = packed["plans"][0]
        with self.assertRaisesRegex(PlanningError, "missing"):
            hydrate_plan(plan, {})
        broken = copy.deepcopy(packed["playbooks"])
        broken[plan["playbook_id"]]["duties"][0]["task"] = "tampered"
        with self.assertRaisesRegex(PlanningError, "SHA-256"):
            hydrate_plan(plan, broken)
        with self.assertRaisesRegex(PlanningError, "metadata disagrees"):
            hydrate_plan({**plan, "profession": "Wrong occupation"}, packed["playbooks"])
        with self.assertRaisesRegex(PlanningError, "SHA-256"):
            hydrate_plan({**plan, "playbook_sha256": "0" * 64}, packed["playbooks"])

    def test_compaction_rejects_duplicate_plans_and_unverified_digest(self):
        plans = self.plans(count=1)
        with self.assertRaisesRegex(PlanningError, "unique"):
            compact_plans(plans * 2)
        plans[0]["playbook_sha256"] = "0" * 64
        with self.assertRaisesRegex(PlanningError, "digest"):
            compact_plans(plans)

    def test_nonpaper_plans_roundtrip_without_a_catalog(self):
        plan = {"plan_id": "old-plan", "profession": "Test", "activity": "Test activity"}
        packed = compact_plans([plan])
        self.assertEqual(packed, {"plans": [plan], "playbooks": {}})
        self.assertEqual(hydrate_plan(packed["plans"][0], {}), plan)


if __name__ == "__main__":
    unittest.main()
