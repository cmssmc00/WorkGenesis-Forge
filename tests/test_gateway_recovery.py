"""Explicit recovery preserves immutable history and conservative billing."""
import copy
import io
import json
import urllib.error
import unittest
from unittest.mock import patch

from wgforge import runtime as rt, workflow
from wgforge.recovery import prepare_gateway_recovery
from tests import test_runtime as fixtures
from tests.test_streaming import StreamingHTTP, sse, deltas


class GatewayRecoveryTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def failed_source(self):
        error = urllib.error.HTTPError(self.cfg["endpoint"], 504, "timeout", {}, io.BytesIO(b"provider error"))
        fake = fixtures.FakeHTTP([fixtures.reply("read_file", {"path": "input/anchors/source.txt"}, "r"), error])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "504"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        rt.atomic_json(self.run / "role_config.json", {"effective_config": self.cfg})
        self.before = {str(p.relative_to(self.run)): rt.file_hash(p) for p in self.run.rglob("*") if p.is_file()}
        self.original_usage = workflow.aggregate_usage(self.root)
        self.original_state = json.loads((self.run / "checkpoint.json").read_text())
        self.fork = self.root / "recovered"
        self.streaming = {**copy.deepcopy(self.cfg), "stream": True}

    def prepare(self):
        with patch.object(rt.urllib.request, "build_opener", side_effect=AssertionError("Offline preparation must not access the network")):
            return prepare_gateway_recovery(self.task / "public", self.run, self.fork, self.streaming, self.root)

    def test_recovery_continues_history_without_double_billing_or_read_replay(self):
        self.failed_source()
        report = self.prepare()
        self.assertFalse(report["completed"])
        self.assertEqual(workflow.aggregate_usage(self.root), self.original_usage)
        self.assertEqual(report["usage"]["requests"], 2)
        self.assertEqual(report["request_receipts"][-1]["status"], "uncertain")
        responses = [fixtures.reply("write_file", {"path": "output/memo.txt", "content": "Grounded memo"}, "w"),
                     fixtures.reply("finish", {"summary": "done"}, "f")]
        fake = StreamingHTTP([sse(deltas(response)) for response in responses])
        original_execute = rt.Sandbox.execute
        invoked = []
        def execute(broker, name, arguments):
            invoked.append(name)
            return original_execute(broker, name, arguments)
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt.Sandbox, "execute", execute):
            result = rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
        self.assertTrue(result["completed"])
        self.assertEqual(fake.requests[0]["messages"], self.original_state["messages"])
        self.assertEqual(invoked, ["write_file", "finish"])
        self.assertEqual(result["usage"]["requests"], 4)
        usage = workflow.aggregate_usage(self.root)
        self.assertEqual(usage["requests"], 4)
        self.assertEqual(usage["uncertain_requests"], 1)
        self.assertEqual(usage["charged_tokens"], self.original_usage["charged_tokens"] + 400)
        self.assertAlmostEqual(usage["charged_upper_bound_usd"], self.original_usage["charged_upper_bound_usd"] + 0.00056)
        self.assertEqual(self.before, {str(p.relative_to(self.run)): rt.file_hash(p) for p in self.run.rglob("*") if p.is_file()})
        self.assertIn("gateway_recovery", json.loads((self.fork / "trajectory.json").read_text()))

    def test_new_uncertain_request_still_blocks_automatic_resume(self):
        self.failed_source()
        self.prepare()
        fake = StreamingHTTP([b"data: {\"choices\": []}\n\n"])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "uncertain"):
                rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
            with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
                rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
        self.assertEqual(len(fake.requests), 1)
        usage = workflow.aggregate_usage(self.root)
        self.assertEqual(usage["requests"], 3)
        self.assertEqual(usage["uncertain_requests"], 2)

    def test_fresh_fork_retains_both_prior_uncertain_attempts_in_campaign(self):
        self.failed_source()
        self.prepare()
        failed_fork = self.fork
        fake = StreamingHTTP([b'data: {"choices": []}\n\n'])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "uncertain"):
                rt.run_agent(self.task / "public", failed_fork, self.streaming, resume=True)
        failed_hashes = rt.tree_hashes(failed_fork)
        usage_before = workflow.aggregate_usage(self.root)
        self.fork = self.root / "eof-fixed"
        self.prepare()
        self.assertEqual(workflow.aggregate_usage(self.root), usage_before)
        fake = StreamingHTTP([
            sse(deltas(fixtures.reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w")), False),
            sse(deltas(fixtures.reply("finish", {"summary": "done"}, "f")), False)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
        self.assertTrue(result["completed"])
        self.assertEqual(fake.requests[0]["messages"], self.original_state["messages"])
        usage_after = workflow.aggregate_usage(self.root)
        self.assertEqual(usage_after["requests"], 5)
        self.assertEqual(usage_after["uncertain_requests"], 2)
        self.assertEqual(usage_after["charged_tokens"], usage_before["charged_tokens"] + 400)
        self.assertEqual(rt.tree_hashes(failed_fork), failed_hashes)

    def test_prepared_evolution_resumes_with_same_config_and_shared_budget(self):
        self.failed_source()
        rt.atomic_json(self.task / "manifest.json", {"bundle_digest": "test-bundle"})
        campaign = {"approve_live": True, "roles": {"solver": self.streaming},
                    "campaign_budget": {"max_requests": 50, "max_tokens": 5000000, "max_cost_usd": 20},
                    "repair": {"max_rounds": 1}}
        with patch.object(workflow, "verify_bundle", return_value={}), patch.object(rt.urllib.request, "build_opener") as network:
            workflow.prepare_evolution_recovery(self.task, self.run, self.fork, campaign, self.root)
            network.assert_not_called()
        fake = StreamingHTTP([
            sse(deltas(fixtures.reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"))),
            sse(deltas(fixtures.reply("finish", {"summary": "done"}, "f")))])
        with patch.object(workflow, "verify_bundle", return_value={}), patch.object(workflow, "audit_run", return_value={"defects": [], "attribution": []}), patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = workflow.evolve_scenario(self.task, self.fork, campaign, campaign_root=self.root)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["usage"]["requests"], 4)
        self.assertEqual(fake.requests[0]["messages"], self.original_state["messages"])

    def test_source_or_inherited_receipt_tampering_rejected(self):
        self.failed_source()
        self.prepare()
        state = json.loads((self.fork / "checkpoint.json").read_text())
        state["requests"][-1]["charged_tokens"] = 0
        rt.atomic_json(self.fork / "checkpoint.json", state)
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "preserve every receipt"):
            workflow.aggregate_usage(self.root)
        state["requests"] = self.original_state["requests"]
        rt.atomic_json(self.fork / "checkpoint.json", state)
        changed = copy.deepcopy(self.original_state)
        changed["error"] = "changed"
        rt.atomic_json(self.run / "checkpoint.json", changed)
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "changed or is missing"):
            workflow.aggregate_usage(self.root)
        with patch.object(rt.urllib.request, "build_opener") as network:
            with self.assertRaisesRegex(rt.RuntimeErrorBase, "changed or is missing"):
                rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
            network.assert_not_called()

    def test_changed_model_or_budget_rejected(self):
        self.failed_source()
        for mutate in (lambda cfg: cfg.update(model="other"), lambda cfg: cfg["budget"].update(max_total_tokens=999999)):
            self.streaming = {**copy.deepcopy(self.cfg), "stream": True}
            mutate(self.streaming)
            with self.assertRaisesRegex(rt.RuntimeErrorBase, "only stream"):
                self.prepare()

    def test_changed_public_or_generated_outputs_rejected(self):
        self.failed_source()
        (self.run / "workspace/output/memo.txt").write_text("unexpected")
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "unmodified source evidence"):
            self.prepare()

    def test_budget_root_cannot_be_dropped(self):
        self.failed_source()
        self.prepare()
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "same campaign budget root"):
            workflow.aggregate_usage(self.fork)

    def test_deadline_starts_at_first_execution_and_not_on_later_resume(self):
        self.failed_source()
        self.prepare()
        state = json.loads((self.fork / "checkpoint.json").read_text())
        state["started_at_epoch"] = 0
        rt.atomic_json(self.fork / "checkpoint.json", state)
        fake = StreamingHTTP([sse(deltas(fixtures.reply("finish", {"summary": "no output"}, "f")))])
        # Stop after receiving the message so ordinary resume has a saved response.
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)
        state = json.loads((self.fork / "checkpoint.json").read_text())
        self.assertGreater(state["started_at_epoch"], 0)
        self.assertEqual(state["started_at_epoch"], state["gateway_recovery"]["started_at_epoch"])
        state["started_at_epoch"] = 0
        rt.atomic_json(self.fork / "checkpoint.json", state)
        with patch.object(rt.urllib.request, "build_opener", return_value=StreamingHTTP([])):
            with self.assertRaisesRegex(rt.BudgetExceeded, "deadline"):
                rt.run_agent(self.task / "public", self.fork, self.streaming, resume=True)


if __name__ == "__main__":
    unittest.main()
