"""Subscription bridge contract tests: fake CLI only, no model/credential calls."""
import copy
import json
import os
import signal
import sys
import time
import unittest
from unittest.mock import Mock, patch

from wgforge import claude_subscription as cs
from wgforge.runtime import BudgetExceeded, Provider, validate_config
from wgforge.sandbox import RuntimeErrorBase, canonical


def config():
    return {"provider": "claude_subscription", "harness": "stirrup", "model": cs.MODEL, "approve_live": True,
            "subscription": {"executable": "claude", "max_turns_per_call": 3},
            "privacy": {"public_upload_approved": True, "private_upload_approved": True},
            "effort": "high", "max_retries": 0, "timeout_seconds": 20, "max_run_seconds": 300,
            "max_payload_bytes": 500000, "max_response_bytes": 500000,
            "budget": {"max_cost_usd": 20, "input_usd_per_million": 4, "output_usd_per_million": 20,
                       "cache_read_usd_per_million": 0.2, "cache_write_5m_usd_per_million": 5,
                       "cache_write_1h_usd_per_million": 8, "max_requests": 12, "max_steps": 10,
                       "max_input_tokens": 100000, "max_output_tokens": 1000, "max_total_tokens": 500000}}


TOOLS = [{"type": "function", "function": {"name": "code_exec", "description": "Run in the isolated sandbox",
          "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
         {"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}]
MESSAGES = [{"role": "system", "content": "You are an independent solver."},
            {"role": "user", "content": "Inspect the actual input file."}]


def events(content="Done", calls=None, usage=None):
    usage = usage or {"input_tokens": 100, "cache_read_input_tokens": 30,
                      "cache_creation_input_tokens": 20, "output_tokens": 40}
    return [{"type": "system", "subtype": "init", "model": cs.MODEL, "tools": ["StructuredOutput"], "mcp_servers": []},
            {"type": "assistant", "message": {"id": "msg-1", "role": "assistant", "model": cs.MODEL,
                "content": [{"type": "tool_use", "id": "format-1", "name": "StructuredOutput", "input": {}}],
                "usage": {**usage, "output_tokens": 1}}},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
             "total_cost_usd": 0.002, "usage": usage,
             "structured_output": {"content": content, "tool_calls": calls or []}}]


class FakeCLI:
    def __init__(self, responses=None):
        self.responses = list(responses or [events()])
        self.calls, self.model_calls = [], []
        self.status = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "pro"}
        self.version = b"2.1.280 (Claude Code)\n"
        self.returncode = 0

    def __call__(self, argv, input_data=None, **kwargs):
        self.calls.append((argv, input_data, kwargs))
        if "--version" in argv:
            return {"returncode": 0, "stdout": self.version, "stderr": b""}
        if "auth" in argv:
            return {"returncode": 0, "stdout": canonical(self.status).encode(), "stderr": b""}
        self.model_calls.append((argv, input_data, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        data = b""
        for event in response:
            line = canonical(event).encode()
            kwargs["on_stdout_line"](line)
            data += line + b"\n"
        return {"returncode": self.returncode, "stdout": data, "stderr": b"never persist raw diagnostics"}


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCLI()
        self.state = {"role": "solver", "started_at_epoch": time.time()}
        self.saves = []
        self.patch_cli = patch("wgforge.claude_subscription._run_cli", self.fake)
        self.patch_exe = patch("wgforge.claude_subscription.shutil.which", return_value="/opt/homebrew/bin/claude")
        self.patch_cli.start(); self.patch_exe.start()
        self.addCleanup(self.patch_cli.stop); self.addCleanup(self.patch_exe.stop)

    def provider(self, cfg=None, private=False):
        return Provider(cfg or config(), self.state, lambda: self.saves.append(copy.deepcopy(self.state)), private=private)

    def test_runtime_dispatch_and_tool_actions_keep_cli_tools_disabled(self):
        self.fake.responses = [events(None, [{"name": "code_exec", "arguments": {"command": "ls /workspace/input"}}])]
        provider = self.provider()
        self.assertIsInstance(provider, cs.SubscriptionProvider)
        message, index = provider.complete(MESSAGES, TOOLS, context_event_id=7)
        self.assertEqual(index, 0)
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
        self.assertNotIn("provider_content", message)
        argv, data, kwargs = self.fake.model_calls[0]
        for flag in ("--safe-mode", "--no-session-persistence", "--no-chrome", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, argv)
        self.assertNotIn("--bare", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], cs.MODEL)
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}})
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]),
                         {"forceLoginMethod": "claudeai", "switchModelsOnFlag": False, "availableModels": [cs.MODEL]})
        self.assertEqual(self.state["requests"][0]["context_compaction_id"], 7)
        self.assertTrue(any(s["requests"][0]["status"] == "in_flight" for s in self.saves if s["requests"]))

    def test_tool_results_history_and_real_images_are_preserved(self):
        image_url = "data:image/png;base64,aGVsbG8="
        history = MESSAGES + [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old-call", "type": "function",
                "function": {"name": "code_exec", "arguments": '{"command":"render"}'}}]},
            {"role": "tool", "tool_call_id": "old-call", "content": [
                {"type": "text", "text": "Actual rendered image"}, {"type": "image_url", "image_url": {"url": image_url}}]}]
        self.provider().complete(history, TOOLS)
        event = json.loads(self.fake.model_calls[0][1])
        blocks = event["message"]["content"]
        self.assertEqual(blocks[-1], {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}})
        transcript = json.loads(blocks[0]["text"])
        self.assertEqual(transcript["conversation"][-1]["tool_call_id"], "old-call")
        self.assertEqual(transcript["conversation"][-1]["content"][-1]["type"], "image_reference")
        self.assertNotIn("aGVsbG8=", blocks[0]["text"])

    def test_environment_keeps_http_proxies_but_removes_api_oauth_and_custom_settings(self):
        proxies = {"HTTP_PROXY": "http://proxy.invalid:8080", "HTTPS_PROXY": "http://proxy.invalid:8081",
                   "NO_PROXY": "localhost,127.0.0.1", "http_proxy": "http://lower.invalid:8080",
                   "https_proxy": "http://lower.invalid:8081", "no_proxy": "localhost"}
        values = {"HOME": "/Users/example", "PATH": "/bin", "ANTHROPIC_API_KEY": "secret-api",
                  "CLAUDE_CODE_OAUTH_TOKEN": "secret-token", "ANTHROPIC_BASE_URL": "https://bad.invalid",
                  "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CONFIG_DIR": "/private/custom",
                  "ALL_PROXY": "socks5://unsupported.invalid:1080", "all_proxy": "socks5://unsupported.invalid:1081",
                  "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "999999", **proxies}
        with patch.dict(os.environ, values, clear=True):
            env = cs._child_environment(config())
        self.assertEqual(env["HOME"], values["HOME"])
        self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "1000")
        for key, value in proxies.items():
            self.assertEqual(env[key], value)
        for key in set(values) - {"HOME", "PATH", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"} - set(proxies):
            self.assertNotIn(key, env)

    def test_auth_and_model_calls_share_explicit_parent_http_proxy_route(self):
        for keys in (("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"), ("http_proxy", "https_proxy", "no_proxy")):
            with self.subTest(keys=keys):
                proxies = dict(zip(keys, ("http://network-proxy.invalid:8080", "http://network-proxy.invalid:8081", "localhost")))
                self.fake.responses = [events()]
                self.fake.calls.clear()
                with patch.dict(os.environ, {"HOME": "/Users/example", "PATH": "/bin", **proxies,
                        "ANTHROPIC_API_KEY": "secret-api", "ANTHROPIC_BASE_URL": "https://provider.invalid"}, clear=True):
                    self.provider().complete(MESSAGES)
                self.assertTrue(any("auth" in argv for argv, _, _ in self.fake.calls))
                self.assertTrue(any("-p" in argv for argv, _, _ in self.fake.calls))
                for _, _, kwargs in self.fake.calls:
                    for key, value in proxies.items():
                        self.assertEqual(kwargs["env"][key], value)
                    self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
                    self.assertNotIn("ANTHROPIC_BASE_URL", kwargs["env"])
                self.assertNotIn("network-proxy.invalid", canonical(self.state))

    def test_auth_status_whitelists_without_persisting_credentials_or_email(self):
        self.fake.status.update(email="private@example.com", accessToken="secret-value", organizationId="secret-org")
        status = cs.check_status(config())
        self.assertEqual(set(status), set(cs.AUTH_FIELDS))
        self.provider().complete(MESSAGES)
        dump = canonical(self.state)
        for sensitive in ("private@example.com", "secret-value", "secret-org", "never persist raw diagnostics"):
            self.assertNotIn(sensitive, dump)

    def test_api_auth_is_rejected_before_any_reservation_or_model_call(self):
        self.fake.status["authMethod"] = "api_key"
        with self.assertRaisesRegex(cs.SubscriptionError, "subscription"):
            self.provider().complete(MESSAGES)
        self.assertEqual(self.state["requests"], [])
        self.assertEqual(self.fake.model_calls, [])

    def test_offline_validation_does_not_probe_auth_or_cli(self):
        cfg = config(); cfg.pop("approve_live")
        self.assertIsNone(validate_config(cfg, private=True, offline=True))
        self.assertEqual(self.fake.calls, [])

    def test_shared_privacy_and_budget_checks_remain_enforced(self):
        for update in ({"privacy": {"public_upload_approved": True, "private_upload_approved": False}}, {"budget": {}}):
            with self.subTest(update=update), self.assertRaises(RuntimeErrorBase):
                self.provider({**config(), **update}, private=True)

    def test_no_api_config_retry_fallback_or_wrong_model(self):
        for update in ({"api_key_env": "KEY"}, {"endpoint": "https://api.anthropic.com/v1/messages"},
                       {"model": "opus"}, {"max_retries": 1}, {"fallback_model": "sonnet"},
                       {"harness": "legacy"}, {"harness": None},
                       {"subscription": {"executable": "claude", "args": ["--dangerously-skip-permissions"]}}):
            with self.subTest(update=update), self.assertRaises(RuntimeErrorBase):
                self.provider({**config(), **update})

    def test_partial_event_checkpoint_writes_are_batched_without_losing_events(self):
        sequence = events()
        deltas = [{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"text": "a"}}}
                  for _ in range(1000)]
        self.fake.responses = [sequence[:1] + deltas + sequence[1:]]
        with patch("wgforge.claude_subscription.time.monotonic", return_value=100):
            self.provider().complete(MESSAGES)
        self.assertEqual(len(self.state["requests"][0]["cli_events"]), 1003)
        self.assertLess(len(self.saves), 10)
        self.assertEqual(self.saves[0]["requests"][0]["cli_events"], [])
        self.assertEqual(self.saves[1]["requests"][0]["cli_events"][0]["subtype"], "init")
        self.assertEqual(self.saves[-1]["requests"][0]["status"], "succeeded")

    def test_actual_cli_auth_failure_is_actionable_and_never_echoes_raw_error(self):
        self.fake.responses = [[events()[0],
            {"type": "assistant", "error": "authentication_failed", "message": {
                "id": "synthetic-error", "model": "<synthetic>", "content": [
                    {"type": "text", "text": "secret-provider-error"}]}},
            {"type": "result", "subtype": "error_during_execution", "is_error": True,
             "api_error_status": 403, "total_cost_usd": 0,
             "usage": {"input_tokens": 0, "output_tokens": 0}, "num_turns": 1}]]
        provider = self.provider()
        with self.assertRaisesRegex(cs.SubscriptionError, "claude auth login") as caught:
            provider.complete(MESSAGES)
        self.assertNotIn("secret-provider-error", str(caught.exception))
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["failure_class"], "subscription_account_access")
        self.assertEqual(receipt["cli_error_codes"], ["authentication_failed"])
        self.assertEqual(receipt["cli_api_error_statuses"], [403])
        self.assertEqual(receipt["status"], "uncertain")
        self.assertEqual(receipt["reported_cost_usd"], 0)
        self.assertGreater(receipt["charged_usd"], 0)
        with self.assertRaisesRegex(cs.SubscriptionError, "prior subscription"):
            provider.complete(MESSAGES)
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_failure_metadata_is_whitelisted(self):
        metadata, message = cs._known_failure([{"type": "assistant", "error": "secret-provider-text"},
            {"type": "result", "api_error_status": "secret-token"}])
        self.assertEqual(metadata, {})
        self.assertIsNone(message)
        metadata, message = cs._known_failure([{"type": "result", "api_error_status": 429}])
        self.assertEqual(metadata["failure_class"], "subscription_rate_limit")
        self.assertIn("usage/reset", message)

    def test_preflight_checks_only_version_and_auth(self):
        self.assertEqual(cs.subscription_preflight(config()), [])
        self.assertEqual(self.fake.model_calls, [])
        self.fake.status["loggedIn"] = False
        pending = cs.subscription_preflight(config())
        self.assertEqual(len(pending), 1)

    def test_unsupported_cli_versions_are_rejected_before_auth_or_model_calls(self):
        for version in ("2.1.267", "2.1.279"):
            with self.subTest(version=version):
                self.fake.version = (version + " (Claude Code)\n").encode()
                self.fake.calls.clear()
                with self.assertRaisesRegex(cs.SubscriptionError, "2.1.280 or newer") as caught:
                    self.provider()
                self.assertIn("brew upgrade --cask claude-code", str(caught.exception))
                self.assertIn("restart", str(caught.exception))
                pending = cs.subscription_preflight(config())
                self.assertEqual(len(pending), 1)
                self.assertIn(version, pending[0])
                self.assertTrue(all("--version" in argv for argv, _, _ in self.fake.calls))
                self.assertEqual(self.fake.model_calls, [])
                self.assertEqual(self.state["requests"], [])

    def test_minimum_supported_cli_version_is_accepted(self):
        self.fake.version = b"2.1.280 (Claude Code)\n"
        self.assertEqual(cs.subscription_preflight(config()), [])
        provider = self.provider()
        self.assertEqual(provider.cli_version, "2.1.280")
        provider.complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["cli_version"], "2.1.280")
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_every_invocation_is_fresh_and_only_stirrup_sees_actions(self):
        self.fake.responses = [events(None, [{"name": "code_exec", "arguments": {"command": "one"}}]), events("Done")]
        provider = self.provider()
        message, _ = provider.complete(MESSAGES, TOOLS)
        history = MESSAGES + [message, {"role": "tool", "tool_call_id": message["tool_calls"][0]["id"], "content": "real result"}]
        provider.complete(history, TOOLS)
        for argv, _, _ in self.fake.model_calls:
            self.assertNotIn("--resume", argv)
            self.assertNotIn("--continue", argv)
        self.assertEqual(len(self.state["requests"]), 2)

    def test_aggregate_usage_does_not_become_last_context_usage(self):
        stream = events(usage={"input_tokens": 300, "cache_creation_input_tokens": 60, "cache_read_input_tokens": 90, "output_tokens": 70})
        stream[1]["message"]["usage"] = {"input_tokens": 70, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, "output_tokens": 1}
        stream.insert(2, {"type": "assistant", "message": {**copy.deepcopy(stream[1]["message"]), "id": "msg-2"}})
        stream.insert(3, {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 25}}})
        stream[-1]["num_turns"] = 2
        self.fake.responses = [stream]
        provider = self.provider(); provider.complete(MESSAGES)
        receipt = self.state["requests"][0]
        self.assertEqual((receipt["prompt_tokens"], receipt["completion_tokens"], receipt["charged_tokens"]), (450, 70, 520))
        self.assertEqual((receipt["context_input_tokens"], receipt["context_output_tokens"]), (120, 25))
        self.assertEqual(receipt["observed_model_requests"], 2)
        self.assertEqual(provider.totals()["request_count_basis"], "cli_invocations")
        self.assertEqual(receipt["cost_basis"], "cli_estimate_not_subscription_bill")

    def test_duplicate_assistant_blocks_count_one_observed_request(self):
        stream = events(); stream.insert(2, copy.deepcopy(stream[1]))
        self.fake.responses = [stream]
        self.provider().complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["observed_model_requests"], 1)
        self.assertEqual(self.state["requests"][0]["charged_tokens"], 190)

    def test_real_model_and_helper_model_are_checked(self):
        for change in ("init", "assistant", "helper"):
            stream = events()
            if change == "init": stream[0]["model"] = "claude-sonnet-5-5"
            elif change == "assistant": stream[1]["message"]["model"] = "claude-sonnet-5-5"
            else: stream[-1]["modelUsage"] = {"claude-haiku-5-5": {"inputTokens": 100, "outputTokens": 10, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}}
            self.fake.responses = [stream]; self.state = {"requests": []}
            with self.subTest(change=change), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES)
            self.assertNotIn("message", self.state["requests"][0])

    def test_max_fallback_uses_official_cli_and_records_both_models_once(self):
        cfg = config(); cfg["effort"] = "max"
        cfg["subscription"].update(fallback_models=list(cs.FALLBACK_MODELS), switch_models_on_flag=True)
        stream = events()
        refusal = copy.deepcopy(stream[1])
        refusal["message"].update(id="declined", content=[], stop_reason="refusal")
        stream[1]["message"].update(model="claude-opus-4-8", stop_reason="end_turn")
        stream.insert(1, refusal)
        stream.insert(2, {"type": "stream_event", "event": {"type": "message_start", "message": {"model": cs.MODEL}}})
        stream.insert(3, {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "refusal"}}})
        stream.insert(4, {"type": "stream_event", "event": {"type": "message_start", "message": {"model": "claude-opus-4-8"}}})
        stream[-1]["modelUsage"] = {
            cs.MODEL: {"inputTokens": 100, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "outputTokens": 10},
            "claude-opus-4-8": {"inputTokens": 100, "cacheReadInputTokens": 30, "cacheCreationInputTokens": 20, "outputTokens": 40}}
        self.fake.responses = [stream]
        self.provider(cfg).complete(MESSAGES)
        argv = self.fake.model_calls[0][0]
        self.assertEqual(argv[argv.index("--effort") + 1], "max")
        self.assertEqual(argv[argv.index("--fallback-model") + 1], "claude-opus-5,claude-opus-4-8")
        self.assertTrue(json.loads(argv[argv.index("--settings") + 1])["switchModelsOnFlag"])
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["provider_model"], "claude-opus-4-8")
        self.assertTrue(receipt["fallback_used"])
        self.assertEqual((receipt["prompt_tokens"], receipt["completion_tokens"]), (250, 50))
        self.assertEqual(receipt["observed_model_requests"], 2)

    def test_availability_fallback_can_recover_provider_error(self):
        cfg = config(); cfg["subscription"]["fallback_models"] = ["claude-opus-5"]
        stream = events(); stream[1]["message"]["model"] = "claude-opus-5"
        stream.insert(1, {"type": "system", "error": "overloaded_error"})
        self.fake.responses = [stream]
        self.provider(cfg).complete(MESSAGES)
        self.assertTrue(self.state["requests"][0]["recovered_provider_error"])

    def test_fallback_does_not_accept_final_refusal_foreign_model_or_native_tools(self):
        cfg = config()
        cfg["subscription"].update(fallback_models=list(cs.FALLBACK_MODELS), switch_models_on_flag=True)
        for change in ("refusal", "foreign_model", "native_tool", "rate_limit"):
            stream = events(); stream[1]["message"]["model"] = "claude-opus-4-8"
            if change == "refusal": stream[1]["message"]["stop_reason"] = "refusal"
            elif change == "foreign_model": stream[1]["message"]["model"] = "claude-sonnet-5-5"
            elif change == "native_tool": stream[1]["message"]["content"][0]["name"] = "Bash"
            else: stream.insert(1, {"type": "system", "error": "rate_limit"})
            self.fake.responses = [stream]; self.state = {"requests": []}
            with self.subTest(change=change), self.assertRaises(cs.SubscriptionError):
                self.provider(cfg).complete(MESSAGES)
            self.assertNotIn("message", self.state["requests"][0])

    def test_fallback_settings_validate_exact_models(self):
        for settings in ({"fallback_models": "opus"}, {"fallback_models": ["sonnet"]},
                         {"fallback_models": [cs.MODEL]}, {"switch_models_on_flag": True},
                         {"switch_models_on_flag": "true"}):
            cfg = config(); cfg["subscription"].update(settings)
            with self.subTest(settings=settings), self.assertRaises(cs.SubscriptionError):
                self.provider(cfg)

    def test_cli_tools_and_undeclared_external_actions_are_rejected(self):
        streams = []
        stream = events(); stream[0]["tools"] = ["Bash"]; streams.append(stream)
        stream = events(); stream[1]["message"]["content"][0]["name"] = "Bash"; streams.append(stream)
        streams.append(events(None, [{"name": "Bash", "arguments": {"command": "bad"}}]))
        for stream in streams:
            self.fake.responses = [stream]; self.state = {"requests": []}
            with self.assertRaises(cs.SubscriptionError): self.provider().complete(MESSAGES, TOOLS)
            self.assertNotIn("message", self.state["requests"][0])

    def test_refusal_pause_and_length_never_expose_actions(self):
        for reason in ("refusal", "pause_turn", "max_tokens", "model_context_window_exceeded"):
            stream = events(None, [{"name": "code_exec", "arguments": {"command": "unsafe partial"}}])
            stream.insert(2, {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": reason}}})
            self.fake.responses = [stream]; self.state = {"requests": []}
            with self.subTest(reason=reason), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES, TOOLS)
            self.assertNotIn("message", self.state["requests"][0])

    def test_failed_final_retains_cost_and_cannot_replay(self):
        stream = events(); stream[-1].update(subtype="error_max_turns", is_error=True, total_cost_usd=8)
        self.fake.responses = [stream]
        provider = self.provider()
        with self.assertRaises(cs.SubscriptionError): provider.complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["charged_usd"], 8)
        with self.assertRaisesRegex(cs.SubscriptionError, "prior"): provider.complete(MESSAGES)
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_timeout_and_incomplete_stream_keep_reservation_and_partial_events(self):
        for response in (cs.SubscriptionError("timeout"), events()[:-1]):
            self.fake.responses = [response]; self.state = {"requests": []}
            provider = self.provider()
            with self.assertRaises(cs.SubscriptionError): provider.complete(MESSAGES)
            receipt = self.state["requests"][0]
            self.assertEqual(receipt["status"], "uncertain")
            self.assertGreater(receipt["charged_tokens"], 0)
            with self.assertRaises(cs.SubscriptionError): provider.complete(MESSAGES)

    def test_exit_error_does_not_leak_stderr(self):
        self.fake.returncode = 1
        with self.assertRaises(cs.SubscriptionError) as caught: self.provider().complete(MESSAGES)
        self.assertNotIn("diagnostics", str(caught.exception))
        self.assertEqual(len(self.state["requests"][0]["stderr_sha256"]), 64)

    def test_input_output_and_total_budgets_block_before_acceptance(self):
        for key, value in (("max_input_tokens", 1), ("max_total_tokens", 1), ("max_cost_usd", 0.00001)):
            cfg = config(); cfg["budget"][key] = value; self.state = {"requests": []}
            with self.subTest(key=key), self.assertRaises(BudgetExceeded): self.provider(cfg).complete(MESSAGES)
            self.assertEqual(self.state["requests"], [])
        cfg = config(); cfg["max_payload_bytes"] = 1
        with self.assertRaises(BudgetExceeded): self.provider(cfg).complete(MESSAGES)

    def test_stream_bound_and_usage_overrun_leave_no_actions(self):
        cfg = config(); cfg["max_response_bytes"] = 15
        with self.assertRaises(cs.SubscriptionError): self.provider(cfg).complete(MESSAGES)
        self.state = {"requests": []}; stream = events(); stream[-1]["total_cost_usd"] = 5
        self.fake.responses = [stream]
        with self.assertRaisesRegex(cs.SubscriptionError, "reserved"): self.provider().complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["charged_usd"], 5)

    def test_invalid_or_missing_usage_is_never_zero_cost_success(self):
        for value in (None, {"input_tokens": 10}, {"input_tokens": True, "output_tokens": 2}):
            stream = events(); stream[-1]["usage"] = value
            self.fake.responses = [stream]; self.state = {"requests": []}
            with self.assertRaises(cs.SubscriptionError): self.provider().complete(MESSAGES)
            self.assertGreater(self.state["requests"][0]["charged_usd"], 0)

    def test_wall_clock_deadline_checked_before_auth_and_model(self):
        self.state["started_at_epoch"] = time.time() - 1000
        provider = self.provider()
        before = len(self.fake.calls)
        with self.assertRaises(BudgetExceeded): provider.complete(MESSAGES)
        self.assertEqual(len(self.fake.calls), before)

    def test_remaining_wall_clock_controls_subprocess_timeout(self):
        cfg = config(); cfg["max_run_seconds"] = 5; cfg["timeout_seconds"] = 100
        self.provider(cfg).complete(MESSAGES)
        self.assertLessEqual(self.fake.model_calls[0][2]["timeout_seconds"], 5)

    def test_saved_full_input_and_events_are_separate_from_returned_actions(self):
        self.provider().complete(MESSAGES)
        receipt = self.state["requests"][0]
        self.assertEqual(len(receipt["cli_events"]), 3)
        self.assertEqual(receipt["cli_input"]["stdin"]["type"], "user")
        self.assertFalse(receipt["native_message_replay"])
        self.assertEqual(receipt["provider_api"], "claude_code_subscription")

    def test_json_role_text_output_is_retained_without_fake_tool_events(self):
        self.fake.responses = [events('{"score":5}')]
        message, _ = self.provider().complete(MESSAGES, json_mode=True)
        self.assertEqual(message, {"role": "assistant", "content": '{"score":5}'})

    def test_model_usage_counts_cache_once_and_uses_whole_invocation(self):
        stream = events()
        stream[-1]["modelUsage"] = {cs.MODEL: {"inputTokens": 100, "outputTokens": 40,
            "cacheReadInputTokens": 30, "cacheCreationInputTokens": 20, "costUSD": 0.002}}
        self.fake.responses = [stream]
        self.provider().complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["charged_tokens"], 190)


class SubprocessBoundaryTests(unittest.TestCase):
    def test_fake_local_process_streams_and_never_invokes_a_shell(self):
        received = []
        result = cs._run_cli([sys.executable, "-c", "import sys; print(sys.stdin.readline().strip()); print('note', file=sys.stderr)"],
            b'{"type":"fake"}\n', timeout_seconds=3, max_output_bytes=4096, env=cs._child_environment(),
            on_stdout_line=received.append)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(received, [b'{"type":"fake"}'])
        self.assertEqual(result["stderr"], b"note\n")

    def test_fake_process_output_bound_and_timeout_terminate_process_group(self):
        for source, timeout, maximum in [("print('x'*100000)", 3, 20), ("import time; time.sleep(5)", 0.05, 4096)]:
            with self.subTest(source=source), self.assertRaises(cs.SubscriptionError):
                cs._run_cli([sys.executable, "-c", source], timeout_seconds=timeout,
                            max_output_bytes=maximum, env=cs._child_environment())

    def test_group_is_killed_even_after_parent_exit(self):
        child = Mock(pid=12345)
        child.poll.return_value = 0
        with patch("wgforge.claude_subscription.os.killpg") as kill:
            cs._terminate_group(child)
        kill.assert_called_once_with(12345, signal.SIGKILL)
        child.wait.assert_called()


if __name__ == "__main__":
    unittest.main()
