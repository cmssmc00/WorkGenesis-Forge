"""Codex bridge junctions and paper stages; never launch a CLI or sandbox.

The backend's event/CLI contract is tested separately. Here a simulated provider
replaces its external boundary while the runtime dispatch, pinned Stirrup loop,
paper stage controllers, private/public split and durable records remain real.
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
from wgforge.common import ForgeError, canonical, digest, read_json, write_json
from wgforge.dataset import validate_trajectory
from wgforge.workflow import aggregate_usage, validate_run_binding, verify_terminal_workflow

ROOT = Path(__file__).resolve().parents[1]
HAS_STIRRUP = importlib.util.find_spec("stirrup") is not None


def codex_config():
    return read_json(ROOT / "configs/gpt6_astra_subscription_one.json")


class CodexSubscriptionProfileTests(unittest.TestCase):
    def test_exact_selected_model_effort_transport_and_old_profiles(self):
        config = codex_config()
        paper_profile.validate_paper_profile(config)
        self.assertEqual(config["planning"]["count"], 1)
        full = read_json(ROOT / "configs/gpt6_astra_subscription.json")
        paper_profile.validate_paper_profile(full)
        self.assertEqual(full["planning"]["count"], 100)
        self.assertEqual({(r["provider"], r["model"], r["effort"])
                          for r in config["roles"].values()}, {("codex_subscription", "gpt-6-astra", "xhigh")})
        for filename, provider in (("claude_opus55_subscription_one.json", "claude_subscription"),
                                   ("claude_opus55_paper.json", "anthropic")):
            old = read_json(ROOT / "configs" / filename)
            paper_profile.validate_paper_profile(old)
            self.assertEqual({r["provider"] for r in old["roles"].values()}, {provider})
            self.assertEqual({r["model"] for r in old["roles"].values()}, {"claude-opus-5-5"})
        for change in ({"model": "gpt-6-sol"}, {"effort": "high"},
                       {"provider": "claude_subscription"}):
            invalid = deepcopy(config)
            invalid["roles"]["teacher"].update(change)
            with self.subTest(change=change), self.assertRaises(ForgeError):
                paper_profile.validate_paper_profile(invalid)

    def test_keyless_preflight_calls_codex_auth_adapter_once_and_reports_actual_model(self):
        from wgforge import codex_subscription, claude_subscription
        config = codex_config()
        invocations = []

        def local_probe(argv, *args, **kwargs):
            invocations.append(argv)
            if "--version" in argv:
                data = b"codex-cli 0.154.0\n"
            elif "--help" in argv:
                data = b"--ignore-user-config --ignore-rules --ephemeral --strict-config --output-schema --image"
            elif "--bundled" in argv:
                data = canonical({"models": [{"slug": "gpt-6-astra", "input_modalities": ["text", "image"],
                    "supported_reasoning_levels": [{"effort": "xhigh"}]}]}).encode()
            elif "login" in argv and "status" in argv:
                data = b"Logged in using ChatGPT\n"
            else:
                raise AssertionError("Inference is forbidden in preflight")
            return {"returncode": 0, "stdout": data, "stderr": b""}

        with patch.dict(os.environ, {"PATH": os.environ.get("PATH", ""),
                    "WGFORGE_PAPER_IMAGE": "offline-image", "SERPAPI_API_KEY": "offline-search"}, clear=True), \
             patch("wgforge.planning.plan_tasks", return_value=[{"profession": "Geoscientist"}]), \
             patch.object(codex_subscription, "subscription_preflight", wraps=codex_subscription.subscription_preflight) as probe, \
             patch.object(codex_subscription, "_executable", return_value="/offline/codex"), \
             patch.object(codex_subscription, "_run_cli", side_effect=local_probe), \
             patch.object(claude_subscription, "subscription_preflight", side_effect=AssertionError("Wrong auth backend")), \
             patch("subprocess.Popen", side_effect=AssertionError("No subprocess")), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("No native API")):
            report = paper_profile.preflight(config)
        probe.assert_called_once()
        self.assertEqual(len(invocations), 4)
        self.assertEqual(probe.call_args.args[0]["provider"], "codex_subscription")
        self.assertEqual(report["model"], "gpt-6-astra")
        self.assertEqual(report["effort"], "xhigh")
        self.assertEqual(report["model_transport"], "codex_cli_subscription_bridge")
        self.assertFalse(report["native_message_replay"])
        self.assertEqual(report["paid_calls_made"], 0)
        self.assertEqual(report["tasks_generated"], 0)
        self.assertIn("this plan requests 1 accepted tasks", canonical(report["user_requested_differences"]))
        self.assertNotIn("default 100", canonical(report["user_requested_differences"]))
        self.assertNotIn("Claude", canonical(report))
        self.assertFalse(any("API_KEY" in line for line in report["runtime_setup_pending"]))

    def test_private_approval_still_required_before_codex_transport(self):
        config = codex_config()["roles"]["judge"]
        config["privacy"]["private_upload_approved"] = False
        with patch("subprocess.Popen", side_effect=AssertionError("Privacy check must be local")):
            with self.assertRaisesRegex(runtime.RuntimeErrorBase, "private_upload_approved"):
                runtime.validate_config(config, private=True, offline=True)

    def test_legacy_entrypoints_reject_codex_before_any_cli_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("subprocess.Popen", side_effect=AssertionError("No CLI allowed")), \
                 patch.object(runtime, "Sandbox", side_effect=AssertionError("No legacy sandbox")):
                for role in ("solver", "judge"):
                    config = codex_config()["roles"][role]
                    config["approve_live"] = True
                    for resume in (False, True):
                        out = root / (role + str(resume))
                        with self.subTest(role=role, resume=resume), self.assertRaisesRegex(
                                runtime.RuntimeErrorBase, "Stirrup workflow entrypoint"):
                            if role == "solver":
                                runtime.run_agent(root / "unused", out, config, role=role, resume=resume)
                            else:
                                runtime.run_json_role({}, out, config, role=role, resume=resume)
                        self.assertFalse(out.exists())

    def test_native_export_rejects_codex_even_if_only_run_receipt_labels_bridge(self):
        task = {"task_id": "native-contract"}
        messages = [{"role": "system", "content": "Execute independently."},
                    {"role": "user", "content": canonical(task)},
                    fixtures.tool_reply("finish", {"paths": ["output.txt"]}, 0),
                    {"role": "tool", "tool_call_id": "call_0",
                     "content": canonical({"finished": True, "artifact_hashes": {}})}]
        trajectory = {"mode": "live", "role": "teacher", "isolated_context": True, "messages": messages}
        run = {"role": "teacher", "prompt_digest": digest(messages[:2]), "artifact_hashes": {}}
        self.assertTrue(validate_trajectory(trajectory, run, task))
        for target in ("trajectory", "run"):
            for flag in ({"model_transport": "codex_subscription"}, {"native_message_replay": False}):
                changed_trace, changed_run = deepcopy(trajectory), deepcopy(run)
                (changed_trace if target == "trajectory" else changed_run).update(flag)
                with self.subTest(target=target, flag=flag), self.assertRaisesRegex(ForgeError, "dedicated training adapter"):
                    validate_trajectory(changed_trace, changed_run, task)

    def test_mixed_archived_cli_usage_counts_each_request_and_preserves_uncertainty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory, provider, statuses in (("prior_attempts/claude", "claude_code_subscription", ["succeeded"] * 2),
                                                  ("active/codex", "codex_cli_subscription", ["succeeded", "uncertain"])):
                write_json(root / directory / "checkpoint.json", {"requests": [
                    {"provider_api": provider, "status": status, "charged_tokens": 100, "charged_usd": 0.5}
                    for status in statuses]})
            totals = aggregate_usage(root)
        self.assertEqual(totals["requests"], 4)
        self.assertEqual(totals["subscription_cli_invocations"], 4)
        self.assertEqual(totals["uncertain_requests"], 1)
        self.assertEqual(totals["charged_tokens"], 400)
        self.assertEqual(totals["charged_upper_bound_usd"], 2)
        self.assertFalse(totals["cost_guaranteed_upper_bound"])
        self.assertIsNone(totals["subscription_quota_remaining"])


@unittest.skipUnless(HAS_STIRRUP, "Requires pinned Stirrup; external execution is mocked")
class CodexSubscriptionPipelineTests(unittest.TestCase):
    sandbox = fixtures.PaperIntegrationTests.sandbox
    download = fixtures.PaperIntegrationTests.download
    result_for = fixtures.PaperIntegrationTests.result_for

    def setUp(self):
        fixtures.PaperIntegrationTests.setUp(self)
        self.config = codex_config()
        self.config.update(approve_live=True, evidence_char_limit=500000, audit_char_limit=1000000)
        self.config["synthesis"]["rubric_size_target"] = 20
        for role in self.config["roles"].values():
            role["stirrup"]["sandbox"] = {"backend": "docker", "image": "offline-only"}

    def tearDown(self):
        fixtures.PaperIntegrationTests.tearDown(self)

    def test_real_codex_provider_jsonl_reaches_all_paper_stages_without_native_api(self):
        from wgforge import codex_subscription
        original = codex_subscription.SubscriptionProvider.complete
        active, shadows, inference_calls = {}, {}, []

        def capture(provider, messages, tools=None, json_mode=False, context_event_id=None):
            active.update(provider=provider, messages=messages, tools=tools)
            return original(provider, messages, tools, json_mode, context_event_id)

        def cli(argv, input_data=None, *, timeout_seconds, max_output_bytes, env, on_stdout_line=None, cwd=None):
            if "--version" in argv:
                output = b"codex-cli 0.154.0\n"
            elif "--help" in argv:
                output = b"--ignore-user-config --ignore-rules --ephemeral --strict-config --output-schema --image"
            elif "--bundled" in argv:
                output = canonical({"models": [{"slug": "gpt-6-astra", "input_modalities": ["text", "image"],
                    "supported_reasoning_levels": [{"effort": "xhigh"}]}]}).encode()
            elif "login" in argv and "status" in argv:
                output = b"Logged in using ChatGPT\n"
            else:
                self.assertIn("--json", argv)
                self.assertIn("--ignore-user-config", argv)
                self.assertIn("--ephemeral", argv)
                self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")
                self.assertIn('model_reasoning_effort="xhigh"', argv)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertTrue(Path(cwd).is_dir())
                provider = active["provider"]
                key = (provider.state["role"], provider.state["started_at_epoch"])
                shadow = SimpleNamespace(state={"role": key[0], "requests": shadows.setdefault(key, [])}, save=lambda: None)
                response, _ = fixtures.PaperIntegrationTests.complete(self, shadow, active["messages"], active["tools"])
                proposal = {"content": response.get("content"), "tool_calls": [
                    {"name": call["function"]["name"], "arguments": json.loads(call["function"]["arguments"])}
                    for call in response.get("tool_calls", [])]}
                inference_calls.append(list(argv))
                number = len(inference_calls)
                events = [{"type": "thread.started", "thread_id": "offline-%d" % number}, {"type": "turn.started"},
                    {"type": "item.completed", "item": {"id": "message-%d" % number,
                        "type": "agent_message", "text": canonical(proposal)}},
                    {"type": "turn.completed", "usage": {"input_tokens": 130, "cached_input_tokens": 30,
                        "output_tokens": 40, "reasoning_output_tokens": 10}}]
                for event in events:
                    on_stdout_line(canonical(event).encode())
                output = ("\n".join(canonical(event) for event in events) + "\n").encode()
            return {"returncode": 0, "stdout": output, "stderr": b""}

        candidate = sources._candidate(fixtures.SOURCE_URL, "Observation catalog")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-be-used", "ANTHROPIC_API_KEY": "must-not-be-used"}), \
             patch.object(codex_subscription.SubscriptionProvider, "complete", new=capture), \
             patch.object(codex_subscription, "_executable", return_value="/offline/codex"), \
             patch.object(codex_subscription, "_run_cli", side_effect=cli), \
             patch.object(paper_campaign, "plan_tasks", return_value=[self.plan]), \
             patch.object(stirrup_runtime, "_new_exec_env", side_effect=self.sandbox), \
             patch.object(sources, "discover_sources", return_value=[candidate]), \
             patch.object(sources, "download_source", side_effect=self.download), \
             patch("subprocess.Popen", side_effect=AssertionError("No external process")), \
             patch("socket.create_connection", side_effect=AssertionError("No network")), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("No native API")):
            state = paper_campaign.run_paper_campaign(self.config, self.out)
            self.assertEqual(paper_campaign.run_paper_campaign(self.config, self.out), state)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["counts"]["accepted_tasks"], 1)
        self.assertEqual(state["counts"]["completed_trajectories"], 1)
        self.assertEqual(self.stages, ["researcher", "companion_author", "task_author", "solver", "judge", "attributor",
                                      "repairer", "solver", "judge", "attributor", "teacher"])
        self.assertEqual(len(inference_calls), 24)
        self.assertEqual(state["usage"]["subscription_cli_invocations"], 24)
        self.assertEqual(state["usage"]["charged_tokens"], 24 * 170)
        demo = self.out / "tasks" / state["attempts"][0]["scenario_id"] / "demonstration"
        report, trace = read_json(demo / "run.json"), read_json(demo / "trajectory.json")
        row = report["request_receipts"][0]
        self.assertIsNone(row["provider_model"])
        self.assertFalse(row["provider_model_verified"])
        self.assertEqual(row["prompt_tokens"], 130)
        self.assertEqual(row["completion_tokens"], 40)
        self.assertEqual(row["charged_tokens"], 170)
        self.assertEqual(row["provider_api"], "codex_cli_subscription")
        self.assertFalse(report["cost"]["guaranteed_upper_bound"])
        self.assertIsNone(report["cost"]["actual_billed_usd"])
        self.assertTrue(trace["cli_invocations"][0]["cli_input"])
        self.assertTrue(trace["cli_invocations"][0]["cli_events"])
        self.assertFalse(trace["native_message_replay"])

    def test_typed_multitool_commands_images_and_finish_cross_real_stirrup(self):
        from wgforge import codex_subscription
        from PIL import Image

        # Backslashes here are command data. They must survive the single JSON
        # encoding of a typed argument object without a second JSON-string parse.
        text = 'regex \\d+\\.\\d+ and C:\\reports\\new\nQuoted: "data"; Unicode: 地震'
        result = {"checked": True, "command_data": text}
        commands = ["fixture_write:" + canonical({"/workspace/output/result.json": canonical(result)}),
                    "fixture_write:" + canonical({"/workspace/output/notes.txt": text})]
        proposals = [
            {"content": None, "tool_calls": [
                {"name": "code_exec", "arguments": {"cmd": command}} for command in commands]},
            {"content": None, "tool_calls": [
                {"name": "view_image", "arguments": {"path": "/workspace/input/preview.png"}}]},
            {"content": None, "tool_calls": [
                {"name": "finish", "arguments": {"summary": "Inspected image and submitted files.",
                    "paths": ["/workspace/output/result.json", "/workspace/output/notes.txt"]}}]},
        ]
        image_path = self.root / "preview.png"
        Image.new("RGB", (2, 2), color=(20, 40, 60)).save(image_path)
        png = image_path.read_bytes()
        inference_calls, attachment_paths, schemas = [], [], []

        def cli(argv, input_data=None, *, timeout_seconds, max_output_bytes, env, on_stdout_line=None, cwd=None):
            if "--version" in argv:
                output = b"codex-cli 0.154.0\n"
            elif "--help" in argv:
                output = b"--ignore-user-config --ignore-rules --ephemeral --strict-config --output-schema --image"
            elif "--bundled" in argv:
                output = canonical({"models": [{"slug": "gpt-6-astra", "input_modalities": ["text", "image"],
                    "supported_reasoning_levels": [{"effort": "xhigh"}]}]}).encode()
            elif "login" in argv and "status" in argv:
                output = b"Logged in using ChatGPT\n"
            else:
                turn = len(inference_calls)
                self.assertLess(turn, len(proposals), "Unexpected retry or extra model call")
                schema = read_json(Path(argv[argv.index("--output-schema") + 1]))
                schemas.append(schema)
                branches = {branch["properties"]["name"]["enum"][0]: branch
                            for branch in schema["properties"]["tool_calls"]["items"]["anyOf"]}
                for call in proposals[turn]["tool_calls"]:
                    argument_schema = branches[call["name"]]["properties"]["arguments"]
                    self.assertEqual(argument_schema["type"], "object")
                    self.assertFalse(argument_schema["additionalProperties"])
                    self.assertEqual(set(argument_schema["required"]), set(call["arguments"]))
                self.assertNotIn("arguments_json", canonical(schema))
                self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")
                self.assertIn('model_reasoning_effort="xhigh"', argv)
                paths = [Path(argv[index + 1]) for index, arg in enumerate(argv) if arg == "--image"]
                if turn == 2:
                    self.assertEqual(len(paths), 1)
                    self.assertEqual(paths[0].read_bytes(), png)
                    attachment_paths.extend(paths)
                else:
                    self.assertFalse(paths)
                inference_calls.append(list(argv))
                events = [{"type": "thread.started", "thread_id": "typed-%d" % turn}, {"type": "turn.started"},
                    {"type": "item.completed", "item": {"id": "message-%d" % turn,
                        "type": "agent_message", "text": canonical(proposals[turn])}},
                    {"type": "turn.completed", "usage": {"input_tokens": 130, "cached_input_tokens": 30,
                        "output_tokens": 40, "reasoning_output_tokens": 10}}]
                for event in events:
                    on_stdout_line(canonical(event).encode())
                output = ("\n".join(canonical(event) for event in events) + "\n").encode()
            return {"returncode": 0, "stdout": output, "stderr": b""}

        role = deepcopy(self.config["roles"]["companion_author"])
        role["approve_live"] = True
        out = self.root / "typed-tools"
        with patch.object(codex_subscription, "_executable", return_value="/offline/codex"), \
             patch.object(codex_subscription, "_run_cli", side_effect=cli), \
             patch.object(stirrup_runtime, "_new_exec_env", side_effect=self.sandbox), \
             patch("subprocess.Popen", side_effect=AssertionError("No external process")), \
             patch("socket.create_connection", side_effect=AssertionError("No network")), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("No native API")):
            made = stirrup_runtime.run_json_task({"instruction": "Inspect the image and produce fixture files."},
                {"preview.png": image_path}, out, role, "companion_author")
        self.assertEqual(made["result"], result)
        self.assertEqual((made["artifact_root"] / "notes.txt").read_text(), text)
        self.assertEqual(image_path.read_bytes(), png)
        self.assertEqual([cmd for cmd in self.sandboxes[-1].commands if cmd.startswith("fixture_write:")], commands)
        self.assertEqual(len(inference_calls), 3)
        self.assertTrue(made["receipt"]["completed"])
        self.assertEqual(made["receipt"]["usage"]["requests"], 3)
        self.assertTrue(all(not path.exists() for path in attachment_paths))
        state = read_json(out / "checkpoint.json")
        self.assertEqual([row["status"] for row in state["requests"]], ["succeeded"] * 3)
        calls = [call for message in state["messages"] if message["role"] == "assistant"
                 for call in message.get("tool_calls", [])]
        self.assertEqual([call["function"]["name"] for call in calls], ["code_exec", "code_exec", "view_image", "finish"])
        self.assertEqual([json.loads(call["function"]["arguments"])["cmd"] for call in calls[:2]], commands)

    def test_codex_dispatch_through_real_stirrup_full_chain_then_idempotent_resume(self):
        from wgforge import codex_subscription
        owner = self

        class OfflineCodexProvider:
            def __init__(self, config, state, save, private=False):
                runtime.validate_config(config, private=private, offline=True)
                owner.assertEqual(config["provider"], "codex_subscription")
                owner.assertEqual(config["effort"], "xhigh")
                self.config, self.state, self.save = config, state, save
                self.state.setdefault("requests", [])

            def complete(self, messages, tools=None, json_mode=False, context_event_id=None):
                response, index = fixtures.PaperIntegrationTests.complete(owner, self, messages, tools,
                                                                          json_mode, context_event_id)
                self.state["requests"][index].update(provider_api="codex_cli_subscription",
                    requested_model="gpt-6-astra", effort="xhigh", native_message_replay=False,
                    context_input_tokens=80, context_output_tokens=20,
                    cost_basis="configured_api_equivalent_estimate_not_subscription_bill",
                    cli_input={"serialized_messages": deepcopy(messages)}, cli_events=[{"type": "turn.completed"}])
                self.save()
                return response, index

        candidate = sources._candidate(fixtures.SOURCE_URL, "Observation catalog")
        with patch.object(codex_subscription, "SubscriptionProvider", OfflineCodexProvider), \
             patch.object(paper_campaign, "plan_tasks", return_value=[self.plan]), \
             patch.object(stirrup_runtime, "_new_exec_env", side_effect=self.sandbox), \
             patch.object(sources, "discover_sources", return_value=[candidate]) as search, \
             patch.object(sources, "download_source", side_effect=self.download) as download, \
             patch("subprocess.Popen", side_effect=AssertionError("No actual CLI/Docker")), \
             patch("socket.create_connection", side_effect=AssertionError("No network")), \
             patch.object(runtime.Provider, "complete", side_effect=AssertionError("No native API fallback")), \
             patch.object(runtime.urllib.request, "build_opener", side_effect=AssertionError("No API transport")):
            state = paper_campaign.run_paper_campaign(self.config, self.out)
            before = deepcopy(self.calls)
            self.assertEqual(paper_campaign.run_paper_campaign(self.config, self.out), state)
            self.assertEqual(self.calls, before)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["model_transport"], "codex_subscription")
        self.assertEqual(state["counts"]["accepted_tasks"], 1)
        self.assertEqual(state["counts"]["completed_trajectories"], 1)
        self.assertEqual(self.stages, ["researcher", "companion_author", "task_author", "solver", "judge", "attributor",
                                      "repairer", "solver", "judge", "attributor", "teacher"])
        self.assertEqual(len(self.calls), 24)
        self.assertEqual(state["usage"]["subscription_cli_invocations"], 24)
        search.assert_called_once()
        download.assert_called_once()
        record = state["attempts"][0]
        evolution = (self.out / record["workflow"]).parent
        result = verify_terminal_workflow(evolution)
        accepted = evolution / result["accepted_scenario"]
        demo = self.out / "tasks" / record["scenario_id"] / "demonstration"
        report = validate_run_binding(accepted, demo)
        self.assertEqual(report["version"], "workgenesis-stirrup-codex-subscription/v1")
        self.assertEqual(report["harness"], "official_stirrup_subscription_bridge")
        self.assertEqual(report["outer_harness"], "official_stirrup")
        self.assertEqual(report["provider"], "codex_cli_subscription")
        self.assertEqual(report["model_transport"], "codex_subscription")
        self.assertEqual(report["model"], "gpt-6-astra")
        self.assertEqual(report["effort"], "xhigh")
        self.assertFalse(report["native_message_replay"])
        self.assertFalse(report["full_transcript_retained"])
        self.assertTrue(report["external_transcript_retained"])
        self.assertIsNone(report["cost"]["actual_billed_usd"])
        self.assertFalse(report["cost"]["guaranteed_upper_bound"])
        trace = read_json(demo / "trajectory.json")
        self.assertEqual(trace["model_transport"], "codex_subscription")
        self.assertEqual(trace["cli_invocations"][0]["context_input_tokens"], 80)
        self.assertEqual(trace["cli_invocations"][0]["prompt_tokens"], 100)
        self.assertIn("Codex CLI", trace["training_note"])
        self.assertNotIn("Claude", trace["training_note"])
        dataset = [json.loads(line) for line in (self.out / "dataset/trajectories.jsonl").read_text().splitlines()]
        self.assertEqual(dataset[0]["mode"], "subscription_bridge_trace")
        self.assertEqual(dataset[0]["model_transport"], "codex_subscription")
        self.assertFalse(dataset[0]["native_message_replay"])


if __name__ == "__main__":
    unittest.main()
