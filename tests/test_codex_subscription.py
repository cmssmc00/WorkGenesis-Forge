"""Codex subscription transport tests; subprocess fixtures never call a model."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from wgforge import codex_subscription as cs
from wgforge.runtime import BudgetExceeded, Provider, validate_config
from wgforge.sandbox import canonical


def config():
    return {"provider": "codex_subscription", "harness": "stirrup", "model": cs.MODEL,
            "effort": "xhigh", "approve_live": True, "max_retries": 0,
            "subscription": {"executable": "codex"},
            "privacy": {"public_upload_approved": True, "private_upload_approved": True},
            "timeout_seconds": 20, "max_run_seconds": 300,
            "max_payload_bytes": 500000, "max_response_bytes": 500000,
            "budget": {"max_cost_usd": 20, "input_usd_per_million": 20,
                       "output_usd_per_million": 75, "cache_read_usd_per_million": 2,
                       "max_requests": 12, "max_steps": 10, "max_input_tokens": 100000,
                       "max_output_tokens": 1000, "max_total_tokens": 500000}}


MESSAGES = [{"role": "system", "content": "You are an independent solver."},
            {"role": "user", "content": "Inspect the real input."}]
TOOLS = [{"type": "function", "function": {"name": "code_exec", "description": "Run in the sandbox",
          "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
         {"type": "function", "function": {"name": "finish", "parameters": {"type": "object", "properties": {
             "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["paths"]}}}]
CATALOG_ENTRY = {"slug": cs.MODEL, "supported_reasoning_levels": [{"effort": "xhigh"}],
                 "input_modalities": ["text", "image"], "tool_mode": "code_mode_only",
                 "multi_agent_version": "v2", "apply_patch_tool_type": "freeform",
                 "experimental_supported_tools": ["clock"], "supports_search_tool": True,
                 "node_repl_disabled": False, "context_window": 272000}


def events(content="Done", calls=None, usage=None):
    action = {"content": content, "tool_calls": [
        {"name": call["name"], "arguments": copy.deepcopy(call["arguments"])} for call in calls or []]}
    return [{"type": "thread.started", "thread_id": "thread-1"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item-1", "type": "agent_message", "text": canonical(action)}},
            {"type": "turn.completed", "usage": usage or {
                "input_tokens": 130, "cached_input_tokens": 30, "output_tokens": 40, "reasoning_output_tokens": 10}}]


def transport_diagnostics(detail="request timed out"):
    return [{"type": "error", "message": f"Reconnecting... {attempt}/5 ({detail})"}
            for attempt in (2, 3, 4, 5)] + [
        {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message":
            "Falling back from WebSockets to HTTPS transport. " + detail}}]


class FakeCLI:
    def __init__(self, responses=None):
        self.responses = list(responses or [events()])
        self.calls, self.model_calls, self.attachments, self.schemas = [], [], [], []
        self.version = b"codex-cli 0.154.0\n"
        self.auth = b"Logged in using ChatGPT\n"
        self.auth_stderr = False
        self.catalog = {"models": [copy.deepcopy(CATALOG_ENTRY)]}
        self.help = b"--ignore-user-config --ignore-rules --ephemeral --strict-config --output-schema --image"
        self.returncode = 0

    def __call__(self, argv, input_data=None, **kwargs):
        self.calls.append((argv, input_data, kwargs))
        if "--version" in argv:
            return {"returncode": 0, "stdout": self.version, "stderr": b""}
        if "--help" in argv:
            return {"returncode": 0, "stdout": self.help, "stderr": b""}
        if "--bundled" in argv:
            return {"returncode": 0, "stdout": canonical(self.catalog).encode(), "stderr": b""}
        if "login" in argv:
            return {"returncode": 0, "stdout": b"" if self.auth_stderr else self.auth,
                    "stderr": self.auth if self.auth_stderr else b""}
        self.model_calls.append((argv, input_data, kwargs))
        self.schemas.append(json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text()))
        self.attachments.append([Path(argv[i+1]).read_bytes() for i, v in enumerate(argv) if v == "--image"])
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        data = b""
        for event in response:
            line = canonical(event).encode()
            kwargs["on_stdout_line"](line)
            data += line + b"\n"
        return {"returncode": self.returncode, "stdout": data, "stderr": b"private diagnostic: never echo"}


class CodexSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCLI()
        self.state = {"role": "solver", "started_at_epoch": time.time()}
        self.saves = []
        self.patch_cli = patch("wgforge.codex_subscription._run_cli", self.fake)
        self.patch_exe = patch("wgforge.codex_subscription.shutil.which", return_value="/opt/homebrew/bin/codex")
        self.patch_cli.start(); self.patch_exe.start()
        self.addCleanup(self.patch_cli.stop); self.addCleanup(self.patch_exe.stop)

    def provider(self, cfg=None, private=False):
        return Provider(cfg or config(), self.state, lambda: self.saves.append(copy.deepcopy(self.state)), private=private)

    def test_runtime_dispatch_exact_model_effort_and_no_builtin_tool_profile(self):
        self.fake.responses = [events(None, [{"name": "code_exec", "arguments": {"cmd": "ls /workspace/input"}}])]
        provider = self.provider()
        self.assertIsInstance(provider, cs.SubscriptionProvider)
        message, index = provider.complete(MESSAGES, TOOLS, context_event_id=8)
        self.assertEqual(index, 0)
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
        argv, data, kwargs = self.fake.model_calls[0]
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config", "--skip-git-repo-check"):
            self.assertIn(flag, argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(argv[argv.index("--model")+1], cs.MODEL)
        self.assertEqual(argv[argv.index("--sandbox")+1], "read-only")
        self.assertEqual(argv[argv.index("--cd")+1], kwargs["cwd"])
        self.assertFalse(Path(kwargs["cwd"]).exists())
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        self.assertIn('forced_login_method="chatgpt"', argv)
        self.assertIn("agents.enabled=false", argv)
        for feature in ("shell_tool", "apps", "plugins", "hooks", "code_mode", "computer_use", "browser_use", "multi_agent", "memories"):
            self.assertIn(f"features.{feature}=false", argv)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["context_compaction_id"], 8)
        self.assertIsNone(receipt["provider_model"])
        self.assertFalse(receipt["provider_model_verified"])
        entry = receipt["tool_isolation_catalog"]["models"][0]
        self.assertEqual(entry["slug"], cs.MODEL)
        self.assertIsNone(entry["apply_patch_tool_type"])
        self.assertIsNone(entry["tool_mode"])
        self.assertTrue(entry["node_repl_disabled"])
        self.assertTrue(any(s["requests"][0]["status"] == "in_flight" for s in self.saves if s["requests"]))
        self.assertNotIn("private diagnostic", canonical(receipt))

    def test_real_image_files_and_history_references(self):
        history = MESSAGES + [{"role": "assistant", "content": None, "tool_calls": [{"id": "old-1", "type": "function",
            "function": {"name": "code_exec", "arguments": '{"cmd":"render"}'}}]},
            {"role": "tool", "tool_call_id": "old-1", "content": [{"type": "text", "text": "Rendered file"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]}]
        self.provider().complete(history, TOOLS)
        packet = json.loads(self.fake.model_calls[0][1])
        self.assertEqual(packet["conversation"][-1]["tool_call_id"], "old-1")
        self.assertEqual(packet["image_order"], ["image_1_message_3_part_1"])
        self.assertNotIn("aGVsbG8=", canonical(packet))
        self.assertEqual(self.fake.attachments, [[b"hello"]])
        self.assertIn("aGVsbG8=", canonical(self.state["requests"][0]["cli_input"]))

    def test_output_schema_pairs_each_tool_name_with_its_typed_arguments(self):
        self.provider().complete(MESSAGES, TOOLS)
        schema = self.fake.schemas[0]
        variants = schema["properties"]["tool_calls"]["items"]["anyOf"]
        self.assertEqual(len(variants), len(TOOLS))
        args = {v["properties"]["name"]["enum"][0]: v["properties"]["arguments"] for v in variants}
        self.assertEqual(args["code_exec"], {"type": "object", "properties": {"cmd": {"type": "string"}},
                                          "required": ["cmd"], "additionalProperties": False})
        self.assertEqual(args["finish"]["properties"]["paths"]["minItems"], 1)
        self.assertTrue(all(v["required"] == ["name", "arguments"] and not v["additionalProperties"] for v in variants))
        self.assertNotIn("arguments_json", canonical(schema))
        self.assertFalse(schema["additionalProperties"])

    def test_typed_command_preserves_regex_quotes_and_newlines_without_nested_json(self):
        command = "python - <<'PY'\nimport re\nprint(re.findall(r'\\$(-?[\\d,]+)', text))\nPY"
        self.fake.responses = [events(None, [{"name": "code_exec", "arguments": {"cmd": command}}])]
        message, _ = self.provider().complete(MESSAGES, TOOLS)
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"cmd": command})
        self.assertEqual(self.state["requests"][0]["cli_input"]["stdin"]["action_protocol"], "typed_tool_arguments_v2")
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_observed_invalid_inner_escape_is_never_repaired_or_executed(self):
        # Actual failure shape: outer JSON is valid, but the second decoding of
        # arguments_json fails on the command's raw regex backslash-dollar.
        inner = '{"cmd":"python - <<\'PY\'\\nprint(re.findall(r\'\\$(-?[\\d,]+)\', text))\\nPY"}'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(inner)
        old = {"content": None, "tool_calls": [{"name": "code_exec", "arguments_json": inner}]}
        sequence = events(); sequence[2]["item"]["text"] = canonical(old)
        self.assertEqual(cs._decode_json(sequence[2]["item"]["text"]), old)
        self.fake.responses = [sequence]
        provider = self.provider()
        with self.assertRaisesRegex(cs.SubscriptionError, "typed arguments"):
            provider.complete(MESSAGES, TOOLS)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["status"], "uncertain")
        self.assertEqual(receipt["cli_events"], sequence)
        self.assertEqual(receipt["prompt_tokens"], 130)
        self.assertNotIn("message", receipt)
        with self.assertRaisesRegex(cs.SubscriptionError, "prior Codex"):
            provider.complete(MESSAGES, TOOLS)
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_optional_omission_preserves_original_nullable_and_default_semantics(self):
        parameters = {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1},
            "source_page_url": {"type": "string", "default": ""},
            "nullable": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": "default-value"},
            "options": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1}}, "required": []}},
            "required": ["query"]}
        tools = [{"type": "function", "function": {"name": "search", "parameters": parameters}}]
        arguments = {"query": "observations", "source_page_url": None, "nullable": None, "options": {"limit": None}}
        self.fake.responses = [events(None, [{"name": "search", "arguments": arguments}])]
        message, _ = self.provider().complete(MESSAGES, tools)
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]),
                         {"query": "observations", "nullable": None, "options": {}})
        self.assertEqual(parameters["properties"]["nullable"]["default"], "default-value")
        strict = self.fake.schemas[0]["properties"]["tool_calls"]["items"]["anyOf"][0]["properties"]["arguments"]
        self.assertEqual(strict["required"], list(parameters["properties"]))
        self.assertEqual(strict["properties"]["source_page_url"]["anyOf"], [{"type": "string"}, {"type": "null"}])
        self.assertEqual(cs._argument_value({"query": "q", "source_page_url": "", "nullable": "literal"}, parameters),
                         {"query": "q", "source_page_url": "", "nullable": "literal"})

    def test_typed_arguments_validate_declared_types_fields_and_constraints_without_coercion(self):
        parameters = {"type": "object", "properties": {
            "cmd": {"type": "string", "minLength": 2, "maxLength": 8, "pattern": "^ok"},
            "count": {"type": "integer", "minimum": 1, "maximum": 5},
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
            "mode": {"type": "string", "enum": ["safe"]}}, "required": ["cmd", "count", "paths", "mode"]}
        valid = {"cmd": "okay", "count": 2, "paths": ["out.txt"], "mode": "safe"}
        bad = [{}, {**valid, "extra": 1}, {**valid, "cmd": None}, {**valid, "cmd": 42},
               {**valid, "cmd": "o"}, {**valid, "cmd": "not-ok"}, {**valid, "cmd": "ok-too-long"},
               {**valid, "count": "2"}, {**valid, "count": True}, {**valid, "count": 0}, {**valid, "count": 6},
               {**valid, "paths": []}, {**valid, "paths": [1]}, {**valid, "paths": ["a", "b", "c"]},
               {**valid, "mode": "unsafe"}]
        allowed = {"code_exec": {"parameters": parameters}}
        self.assertEqual(cs._argument_value(valid, parameters), valid)
        for arguments in bad:
            with self.subTest(arguments=arguments), self.assertRaises(cs.SubscriptionError):
                cs._action_message({"content": None, "tool_calls": [{"name": "code_exec", "arguments": arguments}]}, allowed, 0)

    def test_unsupported_tool_schema_fails_before_model_or_reservation(self):
        for parameters in ({"type": "object", "additionalProperties": True},
                           {"type": "object", "allOf": []},
                           {"type": "object", "properties": {"path": {"type": "string", "format": "uri"}}}):
            tools = [{"type": "function", "function": {"name": "unsupported", "parameters": parameters}}]
            with self.subTest(parameters=parameters), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES, tools)
        self.assertFalse(self.fake.model_calls)
        self.assertFalse(self.state["requests"])

    def test_usage_cache_and_reasoning_are_subsets_and_cost_is_estimate(self):
        provider = self.provider()
        provider.complete(MESSAGES)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["prompt_tokens"], 130)
        self.assertEqual(receipt["completion_tokens"], 40)
        self.assertEqual(receipt["charged_tokens"], 170)
        self.assertEqual(receipt["cached_prompt_tokens"], 30)
        self.assertEqual(receipt["reasoning_tokens"], 10)
        self.assertAlmostEqual(receipt["charged_usd"], (100*20 + 30*2 + 40*75)/1e6)
        self.assertEqual(receipt["context_input_tokens"], 130)
        self.assertEqual(receipt["cost_basis"], cs.COST_BASIS)
        self.assertIsNone(receipt["provider_reported_cost_usd"])
        self.assertEqual(provider.totals()["requests"], 1)

    def test_environment_preserves_only_auth_home_and_network_routes(self):
        safe = {"HOME": "/Users/test", "PATH": "/bin", "CODEX_HOME": "/Users/test/.codex",
                "HTTP_PROXY": "http://localhost:7890", "https_proxy": "http://localhost:7890", "NO_PROXY": "localhost"}
        unsafe = {"OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "CODEX_ACCESS_TOKEN": "secret",
                  "OPENAI_BASE_URL": "https://gateway.invalid", "CODEX_CLI_PATH": "/bad",
                  "CODEX_THREAD_ID": "parent", "RUST_LOG": "trace", "ALL_PROXY": "socks5://localhost:99"}
        with patch.dict(os.environ, {**safe, **unsafe}, clear=True):
            self.assertEqual(cs._child_environment(), safe)
            self.provider().complete(MESSAGES)
        for argv, _, kwargs in self.fake.calls:
            self.assertEqual(kwargs["env"], safe)

    def test_auth_status_accepts_stderr_but_never_api_key_identity(self):
        self.fake.auth_stderr = True
        self.assertTrue(cs.check_status(config())["loggedIn"])
        self.fake.auth = b"Logged in using an API key - sk-secret\n"
        with self.assertRaisesRegex(cs.SubscriptionError, "codex login"):
            self.provider().complete(MESSAGES)
        self.assertFalse(self.fake.model_calls)
        self.assertEqual(self.state["requests"], [])
        self.assertNotIn("sk-secret", canonical(cs.check_status(config())))

    def test_old_version_rejected_before_auth_or_model(self):
        self.fake.version = b"codex-cli 0.153.0\n"
        with self.assertRaisesRegex(cs.SubscriptionError, "0.154.0"):
            self.provider()
        self.assertFalse(any("login" in argv for argv, _, _ in self.fake.calls))
        self.assertFalse(self.fake.model_calls)

    def test_missing_flags_or_model_effort_and_image_support_fail_closed(self):
        for mutation in (lambda: setattr(self.fake, "help", b"--output-schema"),
                         lambda: self.fake.catalog.update(models=[]),
                         lambda: self.fake.catalog["models"][0].update(supported_reasoning_levels=[{"effort": "high"}]),
                         lambda: self.fake.catalog["models"][0].update(input_modalities=["text"])):
            self.fake = FakeCLI()
            with patch("wgforge.codex_subscription._run_cli", self.fake):
                mutation()
                with self.assertRaises(cs.SubscriptionError):
                    self.provider()
                self.assertFalse(self.fake.model_calls)

    def test_preflight_has_no_model_request(self):
        self.assertEqual(cs.subscription_preflight(config()), [])
        self.assertFalse(self.fake.model_calls)

    def test_offline_validation_needs_no_login_and_rejects_wrong_protocol(self):
        cfg = config(); cfg.pop("approve_live")
        validate_config(cfg, offline=True)
        self.assertFalse(self.fake.calls)
        for key, value in [("model", "gpt-6-sol"), ("effort", "high"), ("harness", "legacy"),
                           ("endpoint", "https://bad.invalid"), ("api_key_env", "OPENAI_API_KEY"),
                           ("max_retries", 1), ("reasoning_effort", "xhigh")]:
            changed = copy.deepcopy(cfg); changed[key] = value
            with self.subTest(key=key), self.assertRaises(Exception):
                validate_config(changed, offline=True)

    def test_request_token_cost_and_deadline_budgets_precede_model(self):
        for key, value, pattern in [("max_requests", 1, "invocation"), ("max_total_tokens", 100, "token budget"),
                                    ("max_cost_usd", 0.000001, "Estimated"), ("max_input_tokens", 100, "input bound")]:
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            cfg = config(); cfg["budget"][key] = value
            if key == "max_requests":
                self.state["requests"] = [{"status": "succeeded", "charged_usd": 0, "charged_tokens": 0}]
            with self.subTest(key=key), self.assertRaisesRegex(BudgetExceeded, pattern):
                self.provider(cfg).complete(MESSAGES)
        self.state = {"role": "solver", "started_at_epoch": 0}
        with self.assertRaisesRegex(BudgetExceeded, "deadline"):
            self.provider().complete(MESSAGES)
        self.assertFalse(self.fake.model_calls)

    def test_payload_guard_and_no_automatic_resume(self):
        cfg = config(); cfg["max_payload_bytes"] = 100
        with self.assertRaisesRegex(BudgetExceeded, "payload"):
            self.provider(cfg).complete(MESSAGES)
        self.state["requests"] = [{"status": "uncertain", "charged_usd": 1, "charged_tokens": 20}]
        with self.assertRaisesRegex(cs.SubscriptionError, "prior Codex"):
            self.provider().complete(MESSAGES)
        self.assertFalse(self.fake.model_calls)

    def test_model_switch_or_native_tool_is_rejected_during_stream(self):
        for event in [{"type": "turn.started", "model": "gpt-6-sol"},
                      {"type": "turn.started", "reasoning_effort": "low"},
                      {"type": "item.started", "item": {"id": "shell", "type": "command_execution", "command": "ls"}},
                      {"type": "item.completed", "item": {"id": "web", "type": "web_search"}}]:
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            self.fake.responses = [[events()[0], event]]
            with self.subTest(event=event), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_truncated_or_failed_turn_retains_allowance_and_safe_error(self):
        for sequence in [events()[:-1], [events()[0], {"type": "error", "message": "429 usage_limit_reached secret-key"},
                                       {"type": "turn.failed", "error": {"message": "secret-key"}}]]:
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            self.fake.responses = [sequence]
            with self.assertRaises(cs.SubscriptionError) as caught:
                self.provider().complete(MESSAGES)
            self.assertNotIn("secret-key", str(caught.exception))
            r = self.state["requests"][0]
            self.assertEqual(r["status"], "uncertain")
            self.assertGreater(r["charged_tokens"], 1000)
            self.assertEqual(r["cli_events"], sequence)

    def test_cli_503_reconnects_then_completed_turn_is_accepted_without_bridge_retry(self):
        sequence = events(None, [{"name": "code_exec", "arguments": {"cmd": "inspect /workspace/input"}}])
        sequence[2:2] = [{"type": "error", "message":
            f"Reconnecting... {attempt}/5 (unexpected status 503 Service Unavailable: An error occurred while processing your request.)"}
            for attempt in (2, 3, 4)]
        self.fake.responses = [sequence]
        message, index = self.provider().complete(MESSAGES, TOOLS)
        self.assertEqual(index, 0)
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
        self.assertEqual(len(self.fake.model_calls), 1)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["cli_events"], sequence)
        self.assertEqual(receipt["cli_recovered_error_events"], 3)
        self.assertEqual([r["attempt"] for r in receipt["cli_reconnect_progress"]], [2, 3, 4])
        self.assertEqual([r["http_status"] for r in receipt["cli_reconnect_progress"]], [503]*3)
        self.assertNotIn("failure_class", receipt)
        self.assertEqual(receipt["charged_tokens"], 170)

    def test_cli_stream_reconnect_then_success_retains_progress(self):
        sequence = events()
        sequence.insert(2, {"type": "error", "message":
            "Reconnecting... 1/5 (stream disconnected before completion: connection closed)"})
        self.fake.responses = [sequence]
        self.provider().complete(MESSAGES)
        self.assertEqual(self.state["requests"][0]["cli_recovered_error_events"], 1)
        self.assertIsNone(self.state["requests"][0]["cli_reconnect_progress"][0]["http_status"])

    def test_websocket_then_https_reconnects_use_completion_not_nested_error_wording(self):
        sequence = events(None, [{"name": "code_exec", "arguments": {"cmd": "inspect"}}])
        diagnostics = transport_diagnostics("stream disconnected before completion: Connection reset by peer (os error 54)")
        diagnostics += [{"type": "error", "message":
            f"Reconnecting... {attempt}/5 (Connection failed: error sending request)"}
            for attempt in (1, 2, 3)]
        sequence[2:2] = diagnostics
        self.fake.responses = [sequence]
        message, _ = self.provider().complete(MESSAGES, TOOLS)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["cli_recovered_error_events"], 8)
        self.assertEqual(receipt["cli_events"], sequence)
        self.assertEqual([p["attempt"] for p in receipt["cli_reconnect_progress"]], [2, 3, 4, 5, 1, 2, 3])
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_cli_retry_envelopes_allow_new_network_wording_only_after_valid_completion(self):
        for detail in ("Connection failed: error sending request", "temporary DNS failure", "proxy connection closed"):
            for success in (True, False):
                with self.subTest(detail=detail, success=success):
                    self.state = {"role": "solver", "started_at_epoch": time.time()}
                    sequence = events()
                    sequence[2:2] = transport_diagnostics(detail)
                    if not success:
                        sequence[-1] = {"type": "turn.failed", "error": {"message": detail}}
                    self.fake.responses = [sequence]
                    if success:
                        self.provider().complete(MESSAGES)
                        self.assertEqual(self.state["requests"][0]["cli_recovered_error_events"], 5)
                    else:
                        with self.assertRaises(cs.SubscriptionError):
                            self.provider().complete(MESSAGES)
                        self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_timeout_and_https_fallback_then_complete_is_accepted_without_bridge_retry(self):
        sequence = events(None, [{"name": "code_exec", "arguments": {"cmd": "inspect /workspace/input"}}])
        sequence[2:2] = transport_diagnostics()
        self.fake.responses = [sequence]
        message, index = self.provider().complete(MESSAGES, TOOLS)
        self.assertEqual(index, 0)
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
        self.assertEqual(len(self.fake.model_calls), 1)
        receipt = self.state["requests"][0]
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["cli_exit_code"], 0)
        self.assertEqual(receipt["cli_events"], sequence)
        self.assertEqual(receipt["cli_recovered_error_events"], 5)
        self.assertEqual([r["attempt"] for r in receipt["cli_reconnect_progress"]], [2, 3, 4, 5])
        self.assertEqual(receipt["cli_transport_fallbacks"], [{"event_index": 6,
            "from_transport": "websocket", "to_transport": "https", "reason": "request_timeout"}])
        self.assertNotIn("failure_class", receipt)
        self.assertEqual(receipt["charged_tokens"], 170)

    def test_https_fallback_without_final_success_retains_uncertainty_and_blocks_replay(self):
        prefix = events()[:2] + transport_diagnostics()
        for suffix in ([], [{"type": "turn.failed", "error": {"message": "request timed out"}}],
                       [{"type": "turn.failed", "error": {"message": "request timed out"}}] + events()[2:]):
            with self.subTest(suffix=suffix):
                self.state = {"role": "solver", "started_at_epoch": time.time()}
                sequence = prefix + suffix
                self.fake.responses = [sequence]
                provider = self.provider()
                calls_before = len(self.fake.model_calls)
                with self.assertRaises(cs.SubscriptionError):
                    provider.complete(MESSAGES, TOOLS)
                receipt = self.state["requests"][0]
                self.assertEqual(receipt["status"], "uncertain")
                # The diagnostic no longer aborts collection before the CLI exits.
                self.assertEqual(receipt["cli_exit_code"], 0)
                self.assertEqual(receipt["cli_events"], sequence)
                self.assertEqual(receipt["cli_recovered_error_events"], 0)
                self.assertEqual(receipt["failure_class"], "cli_provider_error")
                self.assertGreater(receipt["charged_tokens"], 1000)
                with self.assertRaisesRegex(cs.SubscriptionError, "prior Codex"):
                    provider.complete(MESSAGES, TOOLS)
                self.assertEqual(len(self.fake.model_calls), calls_before + 1)

    def test_transport_recovery_classifies_tls_and_other_transient_details_consistently(self):
        for detail, reason in (
                ("stream disconnected before completion: tls handshake eof", "stream_disconnected"),
                ("stream disconnected before completion: connection reset by peer", "stream_disconnected"),
                ("unexpected status 503 Service Unavailable", "transient_http_error")):
            with self.subTest(detail=detail):
                self.state = {"role": "solver", "started_at_epoch": time.time()}
                sequence = events(None, [{"name": "code_exec", "arguments": {"cmd": "inspect"}}])
                sequence[2:2] = transport_diagnostics(detail)
                self.fake.responses = [sequence]
                calls_before = len(self.fake.model_calls)
                message, _ = self.provider().complete(MESSAGES, TOOLS)
                receipt = self.state["requests"][0]
                self.assertEqual(receipt["status"], "succeeded")
                self.assertEqual(receipt["cli_events"], sequence)
                self.assertEqual(receipt["cli_recovered_error_events"], 5)
                self.assertEqual(receipt["cli_transport_fallbacks"][0]["reason"], reason)
                self.assertEqual(message["tool_calls"][0]["function"]["name"], "code_exec")
                self.assertEqual(len(self.fake.model_calls), calls_before + 1)

    def test_tls_diagnostic_still_requires_a_successful_complete_valid_turn(self):
        for defect in ("missing_terminal", "failed_turn", "exit", "usage", "json", "native_action"):
            with self.subTest(defect=defect):
                self.state = {"role": "solver", "started_at_epoch": time.time()}
                sequence = events()
                sequence[2:2] = transport_diagnostics("stream disconnected before completion: tls handshake eof")
                self.fake.returncode = 1 if defect == "exit" else 0
                if defect == "missing_terminal":
                    sequence.pop()
                elif defect == "failed_turn":
                    sequence[-1] = {"type": "turn.failed", "error": {"message": "connection failed"}}
                elif defect == "usage":
                    sequence[-1]["usage"] = None
                elif defect == "json":
                    sequence[-2]["item"]["text"] = "not JSON"
                elif defect == "native_action":
                    sequence.insert(7, {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}})
                self.fake.responses = [sequence]
                with self.assertRaises(cs.SubscriptionError):
                    self.provider().complete(MESSAGES, TOOLS)
                self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_https_fallback_does_not_hide_invalid_completion_or_native_actions(self):
        for defect in ("exit", "usage", "json", "model", "effort", "diagnostic_model", "tool", "model_switch"):
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            sequence = events(); sequence[2:2] = transport_diagnostics()
            self.fake.returncode = 1 if defect == "exit" else 0
            if defect == "usage":
                sequence[-1]["usage"] = None
            elif defect == "json":
                sequence[-2]["item"]["text"] = "not JSON"
            elif defect == "model":
                sequence[-1]["model"] = "gpt-6-sol"
            elif defect == "effort":
                sequence[-1]["reasoning_effort"] = "low"
            elif defect == "diagnostic_model":
                sequence[6]["item"]["model"] = "gpt-6-sol"
            elif defect == "tool":
                sequence.insert(7, {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}})
            elif defect == "model_switch":
                sequence.insert(7, {"type": "model.changed", "model": cs.MODEL})
            self.fake.responses = [sequence]
            with self.subTest(defect=defect), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES, TOOLS)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_unknown_error_items_and_auth_rate_or_model_fallback_are_never_transport_progress(self):
        for text in ("request timed out", "Falling back to model gpt-6-sol. request timed out",
                     "Falling back from WebSockets to HTTPS transport. unexpected status 403 Forbidden",
                     "Falling back from WebSockets to HTTPS transport. 429 usage_limit_reached",
                     "Falling back from WebSockets to HTTPS transport. request timed out; authentication failed",
                     "unknown error", "Falling back from WebSockets to HTTPS transport. "):
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            diagnostic = {"type": "item.completed", "item": {"id": "error-1", "type": "error", "message": text}}
            sequence = events(); sequence.insert(2, diagnostic)
            self.fake.responses = [sequence]
            with self.subTest(text=text), self.assertRaises(cs.SubscriptionError) as caught:
                self.provider().complete(MESSAGES, TOOLS)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")
            self.assertNotIn("built-in action", str(caught.exception))
            self.assertEqual(self.state["requests"][0]["cli_events"], sequence)
            self.assertIsNone(cs._transport_fallback(diagnostic))
            self.assertEqual(cs._error_events(sequence)[0], [diagnostic])

    def test_diagnostic_classification_does_not_allow_native_actions_or_malformed_items(self):
        for kind in ("command_execution", "web_search", "mcp_tool_call", "file_change", "unknown_item"):
            with self.subTest(kind=kind), self.assertRaisesRegex(cs.SubscriptionError, "built-in action"):
                cs._check_event({"type": "item.started", "item": {"type": kind}})
        with self.assertRaisesRegex(cs.SubscriptionError, "malformed diagnostic"):
            cs._check_event({"type": "item.completed", "item": {"type": "error", "message": {"unexpected": "object"}}})

    def test_transport_diagnostic_outside_the_completed_turn_is_not_recovered(self):
        diagnostic = transport_diagnostics()[-1]
        for sequence in ([events()[0], diagnostic] + events()[1:], events() + [diagnostic],
                         events()[:2] + [diagnostic] + events()[2:] + events()):
            with self.subTest(sequence=sequence), self.assertRaises(cs.SubscriptionError):
                cs._inspect_events(sequence, config()["budget"])

    def test_transport_diagnostic_cannot_hide_auth_or_rate_error_metadata(self):
        for code in (401, 403, 429):
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            diagnostic = transport_diagnostics()[-1]
            diagnostic["item"]["error"] = {"status": code}
            self.assertIsNone(cs._transport_fallback(diagnostic))
            sequence = events(); sequence.insert(2, diagnostic)
            self.fake.responses = [sequence]
            with self.subTest(code=code), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES, TOOLS)
            receipt = self.state["requests"][0]
            self.assertEqual(receipt["status"], "uncertain")
            self.assertEqual(receipt["failure_class"], "subscription_rate_limit" if code == 429 else "subscription_account_access")
            self.assertEqual(receipt["cli_events"], sequence)

    def test_reconnect_does_not_mask_terminal_failure_or_missing_success(self):
        progress = {"type": "error", "message": "Reconnecting... 2/5 (unexpected status 503 Service Unavailable)"}
        sequences = [events()[:2]+[progress],
                     events()[:2]+[progress,{"type":"turn.failed","error":{"message":"503 unavailable"}}],
                     events()[:2]+[progress,{"type":"turn.failed","error":{"message":"503 unavailable"}}]+events()[2:],
                     events()+[progress]]
        for sequence in sequences:
            with self.subTest(types=[e["type"] for e in sequence]):
                self.state = {"role": "solver", "started_at_epoch": time.time()}
                self.fake.responses = [sequence]
                with self.assertRaises(cs.SubscriptionError):
                    self.provider().complete(MESSAGES)
                self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_successful_terminal_does_not_hide_auth_rate_or_unrecognized_error(self):
        for text, classification in [
            ("Reconnecting... 2/5 (unexpected status 429 usage_limit_reached)", "subscription_rate_limit"),
            ("Reconnecting... 2/5 (unexpected status 401 Unauthorized)", "subscription_account_access"),
            ("Reconnecting... 2/5 (unexpected status 403 Forbidden)", "subscription_account_access"),
            ("Reconnecting... 2/5 (stream disconnected before completion: authentication failed)", "subscription_account_access"),
            ("unexpected status 503 Service Unavailable", "cli_provider_error"),
            ("Reconnecting... 6/5 (unexpected status 503 Service Unavailable)", "cli_provider_error"),
            ("unknown terminal error", "cli_provider_error"),
            ("Reconnecting... 2/5 ()", "cli_provider_error")]:
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            sequence = events(); sequence.insert(2, {"type": "error", "message": text})
            self.fake.responses = [sequence]
            with self.subTest(text=text), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES)
            self.assertEqual(self.state["requests"][0]["failure_class"], classification)
            with self.assertRaises(cs.SubscriptionError):
                cs._inspect_events(sequence, config()["budget"])

    def test_recovered_reconnect_still_requires_zero_exit_and_valid_result(self):
        progress = {"type": "error", "message": "Reconnecting... 2/5 (unexpected status 503 Service Unavailable)"}
        for defect in ("exit", "usage", "json", "model", "tool"):
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            sequence = events(); sequence.insert(2, progress)
            self.fake.returncode = 1 if defect == "exit" else 0
            if defect == "usage":
                sequence[-1]["usage"] = None
            elif defect == "json":
                sequence[-2]["item"]["text"] = "not JSON"
            elif defect == "model":
                sequence[-1]["model"] = "gpt-6-sol"
            elif defect == "tool":
                sequence.insert(3,{"type":"item.started","item":{"type":"command_execution","command":"ls"}})
            self.fake.responses = [sequence]
            with self.subTest(defect=defect), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_reconnect_before_turn_start_is_not_recovered(self):
        sequence = events()
        sequence.insert(1,{"type":"error","message":"Reconnecting... 2/5 (unexpected status 503 Service Unavailable)"})
        with self.assertRaises(cs.SubscriptionError):
            cs._inspect_events(sequence, config()["budget"])

    def test_timeout_and_interrupt_preserve_reservation(self):
        for failure in (cs.SubscriptionError("timed out"), KeyboardInterrupt()):
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            self.fake.responses = [failure]
            with self.assertRaises(type(failure)):
                self.provider().complete(MESSAGES)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_malformed_structured_action_is_not_executed(self):
        invalid = ["```json\n{}\n```", '{"content":"x","tool_calls":[],"extra":1}',
                   '{"content":"x","content":"y","tool_calls":[]}',
                   '{"content":null,"tool_calls":[{"name":"undeclared","arguments":{}}]}',
                   '{"content":null,"tool_calls":[{"name":"code_exec","arguments":[]}]}']
        for text in invalid:
            self.state = {"role": "solver", "started_at_epoch": time.time()}
            response = events(); response[2]["item"]["text"] = text
            self.fake.responses = [response]
            with self.subTest(text=text), self.assertRaises(cs.SubscriptionError):
                self.provider().complete(MESSAGES, TOOLS)
            self.assertEqual(self.state["requests"][0]["status"], "uncertain")

    def test_missing_invalid_usage_and_multiple_turns_fail_closed(self):
        for usage in [None, {"input_tokens": 10, "output_tokens": -1},
                      {"input_tokens": 10, "output_tokens": 1, "cached_input_tokens": 20},
                      {"input_tokens": 10, "output_tokens": 1, "reasoning_output_tokens": 2}]:
            response = events(); response[-1]["usage"] = usage
            with self.subTest(usage=usage), self.assertRaises(cs.SubscriptionError):
                cs._inspect_events(response, config()["budget"])
        with self.assertRaises(cs.SubscriptionError):
            cs._inspect_events(events()+events(), config()["budget"])

    def test_output_allowance_overrun_preserves_actual_usage_and_rejects_action(self):
        self.fake.responses = [events(usage={"input_tokens": 130, "cached_input_tokens": 30, "output_tokens": 2000})]
        with self.assertRaisesRegex(cs.SubscriptionError, "allowance"):
            self.provider().complete(MESSAGES)
        r = self.state["requests"][0]
        self.assertEqual(r["completion_tokens"], 2000)
        self.assertTrue(r["cli_allowance_exceeded"])
        self.assertEqual(r["charged_tokens"], 2130)

    def test_partial_events_are_batched_but_all_retained(self):
        sequence = events()
        sequence[2:2] = [{"type": "item.updated", "item": {"id": "reason", "type": "reasoning", "text": "x"}} for _ in range(100)]
        self.fake.responses = [sequence]
        self.provider().complete(MESSAGES)
        self.assertEqual(len(self.state["requests"][0]["cli_events"]), 104)
        self.assertLess(len(self.saves), 20)

    def test_json_role_content_and_followup_tool_result(self):
        self.fake.responses = [events('{"status":"ok"}'), events(None, [{"name": "finish", "arguments": {"paths": ["/workspace/output/result.json"]}}])]
        provider = self.provider()
        message, _ = provider.complete(MESSAGES, json_mode=True)
        self.assertEqual(json.loads(message["content"]), {"status": "ok"})
        provider.complete(MESSAGES+[message,{"role":"user","content":"Submit files now"}], TOOLS)
        self.assertTrue(json.loads(self.fake.model_calls[0][1])["json_mode"])
        self.assertEqual(provider.totals()["requests"], 2)


class CodexProcessBoundaryTests(unittest.TestCase):
    def test_bounded_subprocess_stream_and_isolated_workdir(self):
        lines = []
        result = cs._run_cli([sys.executable, "-c", "import os,json;print(json.dumps({'cwd':os.getcwd()}))"],
                             timeout_seconds=5, max_output_bytes=4096, env={"PATH": os.environ.get("PATH", "")},
                             on_stdout_line=lines.append)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(lines), 1)
        self.assertFalse(Path(json.loads(lines[0])["cwd"]).exists())

    def test_output_limit_and_timeout_kill_child(self):
        for program, kwargs in [("print('x'*5000)", {"timeout_seconds":5,"max_output_bytes":100}),
                                ("import time;time.sleep(10)", {"timeout_seconds":0.1,"max_output_bytes":4096})]:
            with self.subTest(program=program), self.assertRaises(cs.SubscriptionError):
                cs._run_cli([sys.executable,"-c",program],env={"PATH":os.environ.get("PATH","")},**kwargs)


if __name__ == "__main__":
    unittest.main()
