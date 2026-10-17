"""Offline campaign invariants, with stage boundaries replaced by deterministic fixtures.

The real campaign, accepted-target loop, lineage registry, file provenance,
demonstration collection and dataset index run here. No providers or sandboxes run.
"""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wgforge import paper_campaign as pc
from wgforge.common import ForgeError, digest, read_json, sha256, write_json
from wgforge.paper_acquisition import CHECKS, validate_anchor_selection
from wgforge.workflow import UnsuitableSource


def source_record(root, number=0):
    path = Path(root) / "anchors" / "source.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("observation,value\n%d,%d\n" % (number, number + 1), encoding="utf-8")
    return {"id": "source_%d" % number, "path": "anchors/source.csv",
            "url": "https://example.org/observations/%d.csv" % number,
            "source_page_url": "https://example.org/catalog/%d" % number,
            "sha256": sha256(path), "bytes": path.stat().st_size, "title": "Offline fixture",
            "license": "unknown", "rights_status": "pending", "synthetic": False,
            "retrieved_at": "2026-10-15T00:00:00+00:00"}


class PaperCampaignTests(unittest.TestCase):
    def test_temporary_search_failure_retries_new_attempt_without_rebuilding_saved_task(self):
        self.config["campaign_mode"] = "author_only"
        failed = False
        def acquire(*args, **kwargs):
            nonlocal failed
            if args[0]["plan_id"] == "plan-1" and not failed:
                failed = True
                write_json(Path(args[1]) / "failed_receipt.json", {"http_status": 503, "status": "uncertain"})
                raise pc.SearchUnavailable("HTTP 503")
            return self.acquire(*args, **kwargs)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire), patch.object(pc.time, "sleep"):
            state = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(state["counts"]["authored_tasks"], 2)
        self.assertEqual([r["status"] for r in state["attempts"]], ["authored_unverified", "source_unavailable", "authored_unverified"])
        self.assertEqual(len([e for e in self.events if e[0] == "author"]), 2)
        receipt = self.out / "tasks" / state["attempts"][1]["scenario_id"] / "acquisition/failed_receipt.json"
        self.assertEqual(read_json(receipt)["http_status"], 503)
        self.assertNotEqual(state["attempts"][1]["scenario_id"], state["attempts"][2]["scenario_id"])

    def test_search_outage_stops_after_three_attempts_and_keeps_completed_tasks(self):
        self.config["campaign_mode"] = "author_only"
        def acquire(*args, **kwargs):
            if args[0]["plan_id"] == "plan-1":
                raise pc.SearchUnavailable("HTTP 503")
            return self.acquire(*args, **kwargs)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire), patch.object(pc.time, "sleep") as sleep:
            state = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(state["counts"]["authored_tasks"], 1)
        self.assertEqual(state["stop_reason"], "search_service_unavailable_after_three_attempts")
        self.assertEqual(len(state["attempts"]), 4)
        self.assertEqual(sleep.call_count, 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "campaign"
        self.plans = [{"plan_id": "plan-%d" % index, "profession": "Test occupation %d" % index,
                       "instruction": "Construct a grounded task."} for index in range(2)]
        self.config = {"approve_live": True, "trajectory_collection": {"enabled": True},
                       "construction": {"max_attempts_per_plan": 4}}
        self.events, self.acquisitions = [], []
        self.outcomes = ["accepted", "accepted"]
        self.teacher_interrupt = False
        self.stack = ExitStack()
        self.stack.enter_context(patch("wgforge.paper_profile.validate_paper_profile"))
        self.stack.enter_context(patch.object(pc, "plan_tasks", side_effect=lambda cfg: deepcopy(self.plans)))
        self.stack.enter_context(patch.object(pc, "collect_paper_anchors", side_effect=self.acquire))
        self.stack.enter_context(patch.object(pc, "author_scenario", side_effect=self.author))
        self.stack.enter_context(patch.object(pc, "evolve_scenario", side_effect=self.evolve))
        self.stack.enter_context(patch.object(pc, "solver_role", side_effect=self.teacher))
        # Full stage acceptance/bundle validation is covered in test_paper_workflow;
        # these small fixtures isolate campaign bookkeeping and resume behavior.
        self.stack.enter_context(patch.object(pc, "verify_bundle", side_effect=lambda path: read_json(Path(path) / "scenario.json")))
        self.stack.enter_context(patch.object(pc, "verify_terminal_workflow", side_effect=lambda path: read_json(Path(path) / "workflow.json")))
        self.stack.enter_context(patch.object(pc, "validate_run_binding", side_effect=self.bind_run))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def acquire(self, plan, out, config, registry, lineage, sid, **kwargs):
        self.events.append(("acquire", sid))
        self.acquisitions.append({"plan": deepcopy(plan), "sid": sid, "out": out})
        source = source_record(out, len(self.acquisitions))
        manifest = Path(out) / "manifest.json"
        write_json(manifest, {"sources": [source]})
        return manifest

    def author(self, plan, manifest, source_root, out, config, scenario_id, lineage_id, campaign_root):
        self.events.append(("author", scenario_id))
        initial = Path(out) / "initial"
        document = {"scenario_id": scenario_id, "request": {"title": "Fixture task",
                    "context": "Fixture context", "deliverables": [{"path": "memo.txt"}]}}
        write_json(initial / "scenario.json", document)
        write_json(initial / "manifest.json", {"bundle_digest": digest(document)})
        return initial

    def evolve(self, initial, out, config, campaign_root):
        sid = read_json(Path(initial) / "scenario.json")["scenario_id"]
        outcome = self.outcomes.pop(0)
        self.events.append((outcome, sid))
        result = {"status": outcome, "accepted_scenario": str(initial)}
        write_json(Path(out) / "workflow.json", result)
        return result

    def teacher(self, config, role, scenario, run_dir, campaign_root):
        run_dir = Path(run_dir)
        if (run_dir / "run.json").exists():
            return read_json(run_dir / "run.json")
        sid = read_json(Path(scenario) / "scenario.json")["scenario_id"]
        self.assertEqual(role, "teacher")
        self.assertIn(("accepted", sid), self.events)
        self.assertNotIn(("discarded", sid), self.events)
        # No human approval/HMAC, evaluator-score or student filter exists here.
        self.assertNotIn("human_review", config)
        self.events.append(("teacher", sid))
        write_json(run_dir / "trajectory.json", {"protocol": "offline-fixture", "provider_contexts": []})
        report = {"completed": True, "trajectory_sha256": sha256(run_dir / "trajectory.json")}
        write_json(run_dir / "run.json", report)
        if self.teacher_interrupt:
            self.teacher_interrupt = False
            raise RuntimeError("Interrupted after a durable teacher trace")
        return report

    def bind_run(self, scenario, run_dir):
        run = read_json(Path(run_dir) / "run.json")
        if not run.get("completed") or sha256(Path(run_dir) / "trajectory.json") != run["trajectory_sha256"]:
            raise ForgeError("Invalid demonstration fixture")
        return run

    def test_target_counts_accepted_tasks_rebuilds_discard_with_new_anchors(self):
        self.outcomes = ["discarded", "accepted", "accepted"]
        state = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["counts"]["accepted_tasks"], 2)
        self.assertEqual(state["counts"]["discarded_scenarios"], 1)
        self.assertEqual(state["counts"]["completed_trajectories"], 2)
        self.assertEqual([a["plan"]["plan_id"] for a in self.acquisitions], ["plan-0", "plan-0", "plan-1"])
        self.assertNotEqual(self.acquisitions[0]["sid"], self.acquisitions[1]["sid"])
        lineage = self.acquisitions[1]["plan"]["lineage_memory"]
        self.assertEqual(lineage[0]["status"], "discarded")
        self.assertEqual(lineage[0]["scenario_id"], self.acquisitions[0]["sid"])
        registry = read_json(self.out / "lineage_registry.json")["entries"]
        self.assertEqual(len({row["sha256"][0] for row in registry}), 3)
        self.assertTrue(all(row.get("source_entries") for row in registry))
        dataset = read_json(self.out / "dataset" / "manifest.json")
        self.assertEqual(dataset["count"], 2)
        self.assertFalse(dataset["training_executed"])
        self.assertEqual(len([event for event in self.events if event[0] == "teacher"]), 2)

    def test_completed_resume_verifies_saved_results_without_new_stage_calls(self):
        state = pc.run_paper_campaign(self.config, self.out)
        events = deepcopy(self.events)
        resumed = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(resumed, state)
        self.assertEqual(self.events, events)
        self.assertEqual(len(self.acquisitions), 2)

    def test_author_only_never_evaluates_or_collects_teacher_and_reuses_results(self):
        self.config["campaign_mode"] = "author_only"
        # Even an inherited enabled teacher setting cannot override author-only.
        with patch.object(pc, "evolve_scenario", side_effect=AssertionError("Evaluation forbidden")) as evolve, \
             patch.object(pc, "solver_role", side_effect=AssertionError("Teacher forbidden")) as teacher:
            state = pc.run_paper_campaign(self.config, self.out)
            self.assertEqual(state["counts"]["authored_tasks"], 2)
            self.assertEqual(state["counts"]["accepted_tasks"], 0)
            self.assertEqual(state["counts"]["completed_trajectories"], 0)
            self.assertEqual(state["status"], "completed")
            self.assertFalse(state["evaluated"])
            events = deepcopy(self.events)
            self.assertEqual(pc.run_paper_campaign(self.config, self.out), state)
            self.assertEqual(self.events, events)
            evolve.assert_not_called()
            teacher.assert_not_called()
        index = read_json(self.out / "dataset/tasks.json")
        self.assertEqual(index["count"], 2)
        self.assertTrue(all(t["status"] == "authored_unverified" for t in index["tasks"]))
        self.assertFalse(list(self.out.rglob("evolution")))
        self.assertFalse((self.out / "dataset/trajectories.jsonl").exists())
        bundle = self.out / state["attempts"][0]["scenario"] / "manifest.json"
        write_json(bundle, {"bundle_digest": "changed"})
        with self.assertRaisesRegex(ForgeError, "authored task changed"):
            pc.run_paper_campaign(self.config, self.out)

    def test_author_only_interruption_keeps_finished_task(self):
        self.config["campaign_mode"] = "author_only"
        def acquire(*args, **kwargs):
            if args[0]["plan_id"] == "plan-1":
                raise ForgeError("Network interrupted")
            return self.acquire(*args, **kwargs)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire):
            with self.assertRaisesRegex(ForgeError, "Network interrupted"):
                pc.run_paper_campaign(self.config, self.out)
        saved = read_json(self.out / "campaign.json")
        self.assertEqual(saved["counts"]["authored_tasks"], 1)
        first = self.out / saved["attempts"][0]["scenario"] / "scenario.json"
        before = sha256(first)
        resumed = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(resumed["counts"]["authored_tasks"], 2)
        self.assertEqual(sha256(first), before)
        self.assertEqual([e[0] for e in self.events], ["acquire", "author", "acquire", "author"])

    def test_worker_schedule_is_used_and_completed_task_survives_next_failure(self):
        schedule = {"plans": deepcopy(self.plans), "playbooks": {}}
        registry = self.root / "shared.json"
        real_acquire = self.acquire
        def acquire(*args, **kwargs):
            self.assertEqual(kwargs["campaign_root"], self.out)
            if args[0]["plan_id"] == "plan-1":
                raise ForgeError("Network interrupted")
            return real_acquire(*args, **kwargs)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire), patch.object(pc, "plan_tasks") as planner:
            with self.assertRaisesRegex(ForgeError, "Network interrupted"):
                pc.run_paper_campaign(self.config, self.out, schedule=schedule, registry_path=registry)
            planner.assert_not_called()
        state = read_json(self.out / "campaign.json")
        self.assertEqual(state["finished_plan_ids"], ["plan-0"])
        first_trace = self.out / state["attempts"][0]["trajectory"]
        original_hash = sha256(first_trace)
        resumed = pc.run_paper_campaign(self.config, self.out, schedule=schedule, registry_path=registry)
        self.assertEqual(resumed["counts"]["finished_plans"], 2)
        self.assertEqual(sha256(first_trace), original_hash)
        self.assertEqual(len([e for e in self.events if e[0] == "teacher"]), 2)

    def test_parallel_anchor_race_advances_to_new_attempt_without_duplicate(self):
        from wgforge.campaign import AnchorConflict
        self.plans = self.plans[:1]
        calls = []
        def acquire(*args):
            calls.append(args[-1])
            if len(calls) == 1:
                raise AnchorConflict("Other worker reserved this source")
            return self.acquire(*args)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire):
            result = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(result["counts"]["finished_plans"], 1)
        self.assertEqual(result["counts"]["acquisition_failures"], 1)
        self.assertNotEqual(calls[0], calls[1])

    def test_teacher_interruption_keeps_acceptance_and_resumes_existing_trace(self):
        self.plans = self.plans[:1]
        self.teacher_interrupt = True
        with self.assertRaisesRegex(RuntimeError, "durable teacher trace"):
            pc.run_paper_campaign(self.config, self.out)
        saved = read_json(self.out / "campaign.json")
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["counts"]["accepted_tasks"], 1)
        self.assertEqual(saved["finished_plan_ids"], [])
        self.assertEqual(len(saved["attempts"]), 1)
        resumed = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(len(self.acquisitions), 1)
        self.assertEqual(len([event for event in self.events if event[0] == "teacher"]), 1)
        self.assertEqual(resumed["counts"]["completed_trajectories"], 1)

    def test_operational_cap_reports_incomplete_and_never_collects_discarded_task(self):
        self.config["construction"]["max_attempts_per_plan"] = 1
        self.outcomes = ["discarded"]
        state = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(state["stop_reason"], "construction_attempt_budget_exhausted")
        self.assertEqual(state["counts"]["accepted_tasks"], 0)
        self.assertEqual(state["counts"]["completed_trajectories"], 0)
        self.assertEqual(state["counts"]["target_tasks"], 2)
        self.assertNotIn("teacher", [event[0] for event in self.events])

    def test_uncertain_error_stops_without_automatic_new_attempt(self):
        paid_calls = []
        directories = []
        def uncertain(plan, out, config, registry, lineage, sid):
            directories.append(out)
            checkpoint = Path(out) / "researcher" / "checkpoint.json"
            if checkpoint.exists():
                raise ForgeError("Saved uncertain request; automatic replay disabled")
            paid_calls.append(sid)
            write_json(checkpoint, {"requests": [{"status": "uncertain", "charged_tokens": 100,
                                                  "charged_usd": 0.01}]})
            raise ForgeError("Provider completion uncertain")
        with patch.object(pc, "collect_paper_anchors", side_effect=uncertain):
            with self.assertRaisesRegex(ForgeError, "completion uncertain"):
                pc.run_paper_campaign(self.config, self.out)
            saved = read_json(self.out / "campaign.json")
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["attempts"], [])
            self.assertIsNotNone(saved["active_attempt"])
            self.assertEqual(saved["usage"]["uncertain_requests"], 1)
            with self.assertRaisesRegex(ForgeError, "replay disabled"):
                pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(len(paid_calls), 1)
        self.assertEqual(directories[0], directories[1])
        self.assertFalse((self.out / ".campaign.lock").exists())

    def test_unusable_source_rebuilds_without_teacher_or_fabricated_anchor(self):
        self.plans = self.plans[:1]
        count = 0
        def acquire(*args):
            nonlocal count
            count += 1
            if count == 1:
                raise UnsuitableSource("No real usable anchors found")
            return self.acquire(*args)
        with patch.object(pc, "collect_paper_anchors", side_effect=acquire):
            state = pc.run_paper_campaign(self.config, self.out)
        self.assertEqual(state["counts"]["accepted_tasks"], 1)
        self.assertEqual(state["counts"]["acquisition_failures"], 1)
        self.assertEqual([row["status"] for row in state["attempts"]], ["source_unavailable", "accepted"])
        self.assertEqual(len(read_json(self.out / "lineage_registry.json")["entries"]), 1)


class AnchorSelectionTests(unittest.TestCase):
    def test_unreadable_source_becomes_a_rejected_candidate(self):
        with patch("wgforge.paper_acquisition.extract_artifact", side_effect=ValueError("Encrypted or oversized PDF")):
            with self.assertRaisesRegex(UnsuitableSource, "Encrypted"):
                validate_anchor_selection(self.response, self.root / "accepted", [])
        self.assertFalse((self.root / "accepted").exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.downloads = self.root / "download"
        self.record = source_record(self.downloads)
        self.selection = {"download_url": self.record["url"], "sha256": self.record["sha256"],
                          "content_summary": "A tabular observation with a measured value.",
                          "key_fields": ["observation", "value"],
                          "suitability": {name: {"pass": True, "reason": "Verified against the actual CSV."}
                                          for name in CHECKS}}
        self.response = {"result": {"status": "success", "anchors": [self.selection]},
                         "downloads": [self.record], "download_root": self.downloads}

    def tearDown(self):
        self.temp.cleanup()

    def test_seven_checks_are_required_and_each_rejection_blocks_acceptance(self):
        for name in CHECKS:
            for change in ("failed", "missing", "no_reason"):
                with self.subTest(check=name, change=change):
                    response = deepcopy(self.response)
                    checks = response["result"]["anchors"][0]["suitability"]
                    if change == "missing":
                        del checks[name]
                    elif change == "failed":
                        checks[name]["pass"] = False
                    else:
                        checks[name]["reason"] = ""
                    with self.assertRaisesRegex(ForgeError, "seven"):
                        validate_anchor_selection(response, self.root / "accepted", [])
        self.assertFalse((self.root / "accepted").exists())

    def test_valid_selection_uses_controller_bytes_and_keeps_source_page(self):
        accepted = validate_anchor_selection(self.response, self.root / "accepted", [], ["csv"])
        actual = accepted["sources"][0]
        self.assertEqual(sha256(self.root / "accepted" / actual["path"]), self.record["sha256"])
        self.assertEqual(actual["source_page_url"], self.record["source_page_url"])
        self.assertTrue(actual["parsed_verified"])

    def test_model_cannot_invent_download_or_reuse_discarded_source(self):
        response = deepcopy(self.response)
        response["result"]["anchors"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ForgeError, "controller-recorded"):
            validate_anchor_selection(response, self.root / "accepted", [])
        lineage = [{"status": "discarded", "sha256": [self.record["sha256"]], "urls": []}]
        with self.assertRaisesRegex(ForgeError, "already been consumed"):
            validate_anchor_selection(self.response, self.root / "accepted", lineage)

    def test_changed_cached_bytes_are_rejected_before_acceptance(self):
        (self.downloads / self.record["path"]).write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "byte count|SHA-256"):
            validate_anchor_selection(self.response, self.root / "accepted", [])


if __name__ == "__main__":
    unittest.main()
