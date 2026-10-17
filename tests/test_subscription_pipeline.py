"""Subscription transport integration, with no actual CLI/model/network execution.

The full paper pipeline and official Stirrup loop remain real. A simulated CLI
process emits documented stream-json messages to the real subscription provider.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_paper_integration as fixtures
from wgforge import paper_campaign, paper_profile, runtime, sources, stirrup_runtime
from wgforge.common import ForgeError, canonical, digest, read_json
from wgforge.workflow import validate_run_binding, verify_terminal_workflow

ROOT = Path(__file__).resolve().parents[1]
SUB_CONFIG = ROOT / "configs/claude_opus55_subscription.json"
API_CONFIG = ROOT / "configs/claude_opus55_paper.json"
HAS_STIRRUP = importlib.util.find_spec("stirrup") is not None


def probe_response(argv):
    """No subprocess is launched, including for login and version checks."""
    if "--version" in argv:
        return {"returncode": 0, "stdout": b"2.1.280 (Claude Code)\n", "stderr": b""}
    if "auth" in argv and "status" in argv:
        return {"returncode": 0, "stdout": canonical({"loggedIn": True, "authMethod": "claude.ai",
                "apiProvider": "firstParty", "subscriptionType": "pro"}).encode(), "stderr": b""}
    raise AssertionError("A model CLI call is forbidden during offline preflight")


class SubscriptionProfileTests(unittest.TestCase):
    def test_subscription_and_native_api_profiles_remain_distinct_and_valid(self):
        subscription, api = read_json(SUB_CONFIG), read_json(API_CONFIG)
        self.assertEqual(subscription["planning"]["count"], 100)
        self.assertEqual(read_json(ROOT / "configs/claude_opus55_subscription_one.json")["planning"]["count"], 1)
        for cfg, provider in ((subscription, "claude_subscription"), (api, "anthropic")):
            paper_profile.validate_paper_profile(cfg)
            self.assertEqual({role["provider"] for role in cfg["roles"].values()}, {provider})
            self.assertEqual({role["model"] for role in cfg["roles"].values()}, {"claude-opus-5-5"})
        for role in subscription["roles"].values():
            self.assertNotIn("api_key_env", role)
            self.assertNotIn("endpoint", role)
        self.assertTrue(all("api_key_env" in role for role in api["roles"].values()))

    def test_keyless_preflight_checks_local_login_without_model_or_http_call(self):
        from wgforge import claude_subscription
        cfg = read_json(SUB_CONFIG)
        probes = []
        def run_cli(argv, *args, **kwargs):
            probes.append(list(argv))
            return probe_response(argv)
        with patch.dict(os.environ, {"PATH": os.environ.get("PATH", ""),
                    "WGFORGE_PAPER_IMAGE": "unit-test-image", "SERPAPI_API_KEY": "offline-source-key"}, clear=True), \
             patch("wgforge.planning.plan_tasks", return_value=[{"profession": "Geoscientist"} for _ in range(100)]), \
             patch.object(claude_subscription, "_executable", return_value="/offline-test/claude"), \
             patch.object(claude_subscription, "_run_cli", side_effect=run_cli), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("No native HTTP provider in preflight")):
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
            report = paper_profile.preflight(cfg)
        self.assertEqual(report["requested_accepted_tasks"], 100)
        self.assertEqual(report["model_transport"], "claude_code_subscription_bridge")
        self.assertFalse(report["native_message_replay"])
        self.assertEqual(report["tasks_generated"], 0)
        self.assertEqual(report["paid_calls_made"], 0)
        self.assertTrue(probes)
        self.assertFalse(any("ANTHROPIC_API_KEY" in text for text in report["runtime_setup_pending"]))
        self.assertTrue(all("auth" in argv or "--version" in argv for argv in probes))

    def test_mixed_subscription_and_api_roles_are_rejected(self):
        cfg = read_json(SUB_CONFIG)
        cfg["roles"]["teacher"] = read_json(API_CONFIG)["roles"]["teacher"]
        with self.assertRaisesRegex(ForgeError, "one model transport"):
            paper_profile.validate_paper_profile(cfg)

    def test_legacy_entrypoints_reject_subscription_before_any_cli_or_artifact_execution(self):
        from wgforge import claude_subscription
        cfg = read_json(SUB_CONFIG)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public = root / "public"
            public.mkdir()
            (public / "task.json").write_text(canonical({"task_id": "legacy-entrypoint-guard",
                "prompt": "Create a memo from public sources."}))
            with patch.object(claude_subscription, "_run_cli",
                        side_effect=AssertionError("Even CLI auth/version probes must not run")) as cli, \
                 patch.object(runtime, "Sandbox",
                        side_effect=AssertionError("Legacy artifacts must not be executed")) as sandbox, \
                 patch.object(runtime.urllib.request, "build_opener",
                        side_effect=AssertionError("Native API fallback must not run")) as http:
                for role in ("solver", "judge"):
                    role_cfg = deepcopy(cfg["roles"][role])
                    role_cfg["approve_live"] = True
                    self.assertEqual(role_cfg["harness"], "stirrup")
                    for resume in (False, True):
                        out = root / (role + "-" + str(resume))
                        with self.subTest(role=role, resume=resume), self.assertRaisesRegex(
                                runtime.RuntimeErrorBase,
                                "Subscription runs require the Stirrup workflow entrypoint"):
                            if role == "solver":
                                runtime.run_agent(public, out, role_cfg, role=role, resume=resume)
                            else:
                                runtime.run_json_role({"instructions": "Score the supplied artifacts."},
                                                      out, role_cfg, role=role, resume=resume)
                        self.assertFalse(out.exists())
                cli.assert_not_called()
                sandbox.assert_not_called()
                http.assert_not_called()

    def test_native_training_export_rejects_bridge_without_breaking_native_contract(self):
        from wgforge.dataset import validate_trajectory
        task = {"task_id": "unit-native-contract"}
        messages = [{"role": "system", "content": "Execute the task independently."},
                    {"role": "user", "content": canonical(task)},
                    fixtures.tool_reply("finish", {"paths": ["output.txt"]}, 0),
                    {"role": "tool", "tool_call_id": "call_0",
                     "content": canonical({"finished": True, "artifact_hashes": {}})}]
        trajectory = {"mode": "live", "role": "teacher", "isolated_context": True, "messages": messages}
        report = {"role": "teacher", "prompt_digest": digest(messages[:2]), "artifact_hashes": {}}
        self.assertTrue(validate_trajectory(trajectory, report, task))
        for target, flag in (("trajectory", {"model_transport": "claude_subscription"}),
                             ("report", {"model_transport": "claude_subscription"}),
                             ("trajectory", {"native_message_replay": False})):
            changed_trace, changed_report = deepcopy(trajectory), deepcopy(report)
            (changed_trace if target == "trajectory" else changed_report).update(flag)
            with self.subTest(target=target, flag=flag), self.assertRaisesRegex(ForgeError, "dedicated training adapter"):
                validate_trajectory(changed_trace, changed_report, task)


@unittest.skipUnless(HAS_STIRRUP, "Requires optional pinned Stirrup dependencies; external services remain mocked")
class SubscriptionPipelineTests(unittest.TestCase):
    sandbox = fixtures.PaperIntegrationTests.sandbox
    download = fixtures.PaperIntegrationTests.download
    result_for = fixtures.PaperIntegrationTests.result_for

    def setUp(self):
        fixtures.PaperIntegrationTests.setUp(self)
        self.config = read_json(ROOT / "configs/claude_opus55_subscription_one.json")
        self.config["approve_live"] = True
        self.config["synthesis"]["rubric_size_target"] = 20
        self.config["evidence_char_limit"] = 500000
        self.config["audit_char_limit"] = 1000000
        for role in self.config["roles"].values():
            role["stirrup"]["sandbox"] = {"backend": "docker", "image": "unit-test-only-image"}
        self.shadow_requests = {}
        self.cli_calls = []
        self.active_call = None

    def tearDown(self):
        fixtures.PaperIntegrationTests.tearDown(self)

    def mock_cli(self, argv, input_data=None, *, timeout_seconds, max_output_bytes, env, on_stdout_line=None):
        if "--version" in argv or "auth" in argv:
            return probe_response(argv)
        self.cli_calls.append({"argv": list(argv), "input": input_data})
        self.assertIn("--safe-mode", argv)
        self.assertNotIn("--bare", argv)
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--fallback-model", argv)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        provider, messages, tools = self.active_call
        stage_key = (provider.state["role"], provider.state.get("started_at_epoch"))
        shadow = SimpleNamespace(state={"role": provider.state["role"],
            "requests": self.shadow_requests.setdefault(stage_key, [])}, save=lambda: None)
        response, _ = fixtures.PaperIntegrationTests.complete(self, shadow, messages, tools)
        proposal = {"content": response.get("content"), "tool_calls": [
            {"name": row["function"]["name"], "arguments": json.loads(row["function"]["arguments"])}
            for row in response.get("tool_calls", [])]}
        ordinal = len(self.cli_calls)
        # Last-context usage differs from aggregate usage on purpose. This
        # verifies the distinction used by the outer Stirrup summary controller.
        events = [
            {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": [], "mcp_servers": []},
            {"type": "assistant", "message": {"id": "msg_%d" % ordinal, "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": canonical(proposal)}],
                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 10,
                          "cache_read_input_tokens": 20, "output_tokens": 1}}},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                "total_cost_usd": 0.001, "usage": {"input_tokens": 900, "output_tokens": 60,
                    "cache_creation_input_tokens": 10, "cache_read_input_tokens": 20},
                "structured_output": proposal},
        ]
        if on_stdout_line is not None:
            for event in events:
                on_stdout_line(canonical(event).encode())
        return {"returncode": 0, "stdout": ("\n".join(canonical(event) for event in events) + "\n").encode(), "stderr": b""}

    def test_subscription_full_paper_chain_preserves_isolation_and_labels_bridge_traces(self):
        from wgforge import claude_subscription
        original_complete = claude_subscription.SubscriptionProvider.complete
        def capture(provider, messages, tools=None, json_mode=False, context_event_id=None):
            self.active_call = (provider, messages, tools)
            return original_complete(provider, messages, tools, json_mode=json_mode, context_event_id=context_event_id)
        candidate = sources._candidate(fixtures.SOURCE_URL, "Observation catalog")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "must-not-be-used", "ANTHROPIC_AUTH_TOKEN": "must-not-be-used"}), \
             patch.object(paper_campaign, "plan_tasks", return_value=[self.plan]), \
             patch.object(stirrup_runtime, "_new_exec_env", side_effect=self.sandbox), \
             patch.object(claude_subscription.SubscriptionProvider, "complete", new=capture), \
             patch.object(claude_subscription, "_executable", return_value="/offline-test/claude"), \
             patch.object(claude_subscription, "_run_cli", side_effect=self.mock_cli), \
             patch.object(sources, "discover_sources", return_value=[candidate]), \
             patch.object(sources, "download_source", side_effect=self.download), \
             patch.object(runtime.Provider, "complete", side_effect=AssertionError("Native HTTP completion must not run")), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("Native HTTP transport must not be initialized")):
            state = paper_campaign.run_paper_campaign(self.config, self.out)
            before = len(self.cli_calls)
            resumed = paper_campaign.run_paper_campaign(self.config, self.out)
            self.assertEqual(len(self.cli_calls), before)
            self.assertEqual(resumed, state)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["counts"]["accepted_tasks"], 1)
        self.assertEqual(state["counts"]["completed_trajectories"], 1)
        self.assertEqual(self.stages, ["researcher", "companion_author", "task_author", "solver", "judge", "attributor",
                        "repairer", "solver", "judge", "attributor", "teacher"])
        self.assertEqual(len(self.cli_calls), 24)
        record = state["attempts"][0]
        evolution = (self.out / record["workflow"]).parent
        workflow = verify_terminal_workflow(evolution)
        accepted = evolution / workflow["accepted_scenario"]
        demo = self.out / "tasks" / record["scenario_id"] / "demonstration"
        report = validate_run_binding(accepted, demo)
        self.assertEqual(report["harness"], "official_stirrup_subscription_bridge")
        self.assertEqual(report["outer_harness"], "official_stirrup")
        self.assertEqual(report["provider"], "claude_code_subscription")
        self.assertFalse(report["native_message_replay"])
        self.assertFalse(report["full_transcript_retained"])
        self.assertTrue(report["external_transcript_retained"])
        self.assertIsNone(report["cost"]["actual_billed_usd"])
        self.assertFalse(report["cost"]["guaranteed_upper_bound"])
        self.assertIn("not the subscription bill", report["cost"]["basis"])
        self.assertIn("CLI invocations", report["usage"]["request_scope"])
        trace = read_json(demo / "trajectory.json")
        self.assertFalse(trace["native_message_replay"])
        self.assertTrue(trace["cli_invocations"])
        self.assertEqual(trace["cli_invocations"][0]["context_input_tokens"], 130)
        self.assertGreater(trace["cli_invocations"][0]["prompt_tokens"], 130)
        self.assertTrue(trace["cli_invocations"][0]["cli_events"])
        self.assertTrue(trace["cli_invocations"][0]["cli_input"])
        dataset = [json.loads(line) for line in (self.out / "dataset" / "trajectories.jsonl").read_text().splitlines()]
        self.assertEqual(dataset[0]["mode"], "subscription_bridge_trace")
        self.assertFalse(dataset[0]["native_message_replay"])
        for path in self.out.rglob("*.json"):
            self.assertNotIn("must-not-be-used", path.read_text())


if __name__ == "__main__":
    unittest.main()
