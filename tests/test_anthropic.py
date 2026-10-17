"""Native Messages API contract tests. All HTTP is mocked; no paid calls."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

from wgforge import anthropic as ant
from wgforge import runtime as rt
from wgforge.streaming import StreamError


def config():
    return {"provider": "anthropic", "approve_live": True, "endpoint": "https://api.anthropic.com/v1/messages",
            "model": "claude-opus-5-5", "api_key_env": "WGFORGE_ANTHROPIC_TEST_KEY", "allowed_hosts": ["api.anthropic.com"],
            "privacy": {"public_upload_approved": True, "private_upload_approved": True}, "max_retries": 0,
            "effort": "high", "timeout_seconds": 3, "use_json_response_format": False,
            "budget": {"max_cost_usd": 20, "input_usd_per_million": 4, "output_usd_per_million": 20,
                       "cache_read_usd_per_million": 0.2, "cache_write_5m_usd_per_million": 5,
                       "cache_write_1h_usd_per_million": 8, "max_requests": 12, "max_steps": 10,
                       "max_input_tokens": 100000, "max_output_tokens": 1000, "max_total_tokens": 500000}}


def reply(blocks=None, stop="end_turn", usage=None):
    return {"id": "msg_mock", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": blocks if blocks is not None else [{"type": "text", "text": '{"ok":true}'}],
            "stop_reason": stop, "stop_sequence": None,
            "usage": usage or {"input_tokens": 20, "output_tokens": 30}}


def tool_reply(name, arguments, call_id, thinking=False):
    blocks = [{"type": "tool_use", "name": name, "input": arguments, "id": call_id}]
    if thinking:
        blocks.insert(0, {"type": "thinking", "thinking": "", "signature": "opaque-unmodified-signature"})
    return reply(blocks, "tool_use")


def events(result):
    """Represent a native response with text/thinking and fragmented tool JSON."""
    initial = {**result, "content": [], "stop_reason": None, "stop_sequence": None,
               "usage": {**result["usage"], "output_tokens": 1}}
    chunks = [{"type": "message_start", "message": initial}]
    for index, block in enumerate(result["content"]):
        initial_block = copy.deepcopy(block)
        deltas = []
        if block["type"] == "text":
            initial_block["text"] = ""
            deltas.append({"type": "text_delta", "text": block["text"]})
        elif block["type"] == "thinking":
            initial_block.update(thinking="", signature="")
            deltas.extend([{"type": "thinking_delta", "thinking": block["thinking"]},
                           {"type": "signature_delta", "signature": block["signature"]}])
        elif block["type"] == "tool_use":
            initial_block["input"] = {}
            raw = json.dumps(block["input"])
            deltas.extend({"type": "input_json_delta", "partial_json": value}
                          for value in [raw[:len(raw)//2], raw[len(raw)//2:]])
        chunks.append({"type": "content_block_start", "index": index, "content_block": initial_block})
        chunks.extend({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas)
        chunks.append({"type": "content_block_stop", "index": index})
    chunks.extend([{"type": "message_delta", "delta": {"stop_reason": result["stop_reason"], "stop_sequence": None},
                    "usage": {"output_tokens": result["usage"]["output_tokens"]}}, {"type": "message_stop"}])
    return chunks


def sse(chunks):
    return "".join("event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n" for item in chunks).encode()


class Response(io.BytesIO):
    pass


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.requests, self.headers = list(responses), [], []
    def open(self, request, timeout=None):
        self.requests.append(json.loads(request.data))
        self.headers.append(dict(request.header_items()))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return Response(item if isinstance(item, bytes) else json.dumps(item).encode())


class AnthropicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.cfg = config()
        self.env = patch.dict(os.environ, {"WGFORGE_ANTHROPIC_TEST_KEY": "native-secret-key"})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def complete(self, responses, messages=None, tools=None):
        state, fake = {"requests": []}, FakeHTTP(responses)
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            provider = rt.Provider(self.cfg, state, lambda: None)
            result = provider.complete(messages or [{"role": "user", "content": "Return JSON"}], tools=tools)
        return result, state, fake
    def public(self):
        public = self.root / "public"
        (public / "anchors").mkdir(parents=True)
        (public / "task.json").write_text('{"task_id":"native","prompt":"Write a memo."}')
        return public
    def test_native_payload_headers_and_json_role(self):
        fake = FakeHTTP([reply()])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_json_role({"task": "example"}, self.root / "judge", self.cfg, "judge")
        self.assertEqual(result["result"], {"ok": True})
        payload = fake.requests[0]
        self.assertTrue(payload["system"])
        self.assertTrue(all(m["role"] != "system" for m in payload["messages"]))
        self.assertEqual(payload["model"], "claude-opus-5-5")
        self.assertEqual(payload["max_tokens"], 1000)
        self.assertEqual(payload["thinking"], {"type": "adaptive", "display": "omitted"})
        self.assertEqual(payload["output_config"], {"effort": "high"})
        self.assertNotIn("response_format", payload)
        headers = {k.lower(): v for k, v in fake.headers[0].items()}
        self.assertEqual(headers["x-api-key"], "native-secret-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", headers)
        self.assertEqual(result["receipt"]["provider_api"], "anthropic_messages")
    def test_tool_execution_thinking_roundtrip_and_completed_resume(self):
        public, run = self.public(), self.root / "run"
        first = tool_reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "toolu_write", thinking=True)
        fake = FakeHTTP([first, tool_reply("finish", {"summary": "Done"}, "toolu_finish")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(public, run, self.cfg)
            resumed = rt.run_agent(public, run, self.cfg, resume=True)
        self.assertTrue(result["completed"])
        self.assertEqual(result, resumed)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[1]["messages"][1]["content"], first["content"])
        self.assertEqual(fake.requests[1]["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(fake.requests[1]["tool_choice"], {"type": "auto", "disable_parallel_tool_use": True})
        self.assertIn("input_schema", fake.requests[0]["tools"][0])
        trajectory = json.loads((run / "trajectory.json").read_text())
        self.assertEqual(trajectory["messages"][2]["provider_content"], first["content"])
        self.assertEqual(result["usage"]["prompt_tokens"], 40)
    def test_unconsumed_native_response_resume_does_not_repay(self):
        public, run = self.public(), self.root / "run"
        fake = FakeHTTP([tool_reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w", True)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_agent(public, run, self.cfg)
        restored = FakeHTTP([tool_reply("finish", {"summary": "Done"}, "f")])
        with patch.object(rt.urllib.request, "build_opener", return_value=restored):
            self.assertTrue(rt.run_agent(public, run, self.cfg, resume=True)["completed"])
        self.assertEqual(len(restored.requests), 1)
        self.assertEqual(restored.requests[0]["messages"][1]["content"][0]["signature"], "opaque-unmodified-signature")
    def test_cache_usage_is_counted_once_and_priced_by_bucket(self):
        usage = {"input_tokens": 3, "output_tokens": 11, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 7,
                 "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 2}}
        _, state, _ = self.complete([reply(usage=usage)])
        receipt = state["requests"][0]
        self.assertEqual(receipt["prompt_tokens"], 15)
        self.assertEqual(receipt["completion_tokens"], 11)
        self.assertEqual(receipt["charged_tokens"], 26)
        self.assertAlmostEqual(receipt["charged_usd"], (3*4 + 11*20 + 3*5 + 2*8 + 7*0.2)/1e6)
        self.assertEqual(receipt["provider_usage"], usage)
    def test_unsplit_cache_write_cost_is_conservative(self):
        counts = ant.usage_counts({"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 10})
        self.assertEqual(ant.usage_cost(counts, self.cfg["budget"]), 80 / 1e6)
    def test_cache_reservation_covers_most_expensive_input_bucket(self):
        self.cfg["budget"]["max_cost_usd"] = 0.00001
        fake, state = FakeHTTP([]), {"requests": []}
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaises(rt.BudgetExceeded):
                rt.Provider(self.cfg, state, lambda: None).complete([{"role": "user", "content": "test"}])
        self.assertEqual(fake.requests, [])
    def test_usage_schema_rejects_negative_boolean_and_bad_cache_breakdown(self):
        for usage in [{"input_tokens": -1, "output_tokens": 1}, {"input_tokens": True, "output_tokens": 1},
                      {"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 3,
                       "cache_creation": {"ephemeral_5m_input_tokens": 2}}]:
            with self.subTest(usage=usage), self.assertRaises(ant.AnthropicProtocolError):
                ant.usage_counts(usage)
    def test_multimodal_tool_results_are_grouped_and_images_are_embedded(self):
        native = tool_reply("view_image", {"path": "image.png"}, "image")
        msg = ant.assistant_message(native["content"])
        msg["tool_calls"].append({"id": "other", "type": "function", "function": {"name": "read_file", "arguments": "{}"}})
        msg["provider_content"].append({"type": "tool_use", "id": "other", "name": "read_file", "input": {}})
        payload = ant.request_payload(self.cfg, [{"role": "user", "content": "Analyze"}, msg,
            {"role": "tool", "tool_call_id": "image", "content": [{"type": "text", "text": "A figure"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]},
            {"role": "tool", "tool_call_id": "other", "content": "data"}])
        results = payload["messages"][-1]["content"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"][1], {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}})
    def test_remote_images_are_not_silently_downloaded(self):
        with self.assertRaisesRegex(ant.AnthropicProtocolError, "embedded"):
            ant.request_payload(self.cfg, [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}]}])
    def test_image_base64_is_not_counted_as_text_tokens(self):
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8=" * 40000}}
        payload = {"model": "claude-opus-5-5", "max_tokens": 1000, "thinking": {"type": "adaptive"},
                   "messages": [{"role": "user", "content": [image]}]}
        bound = rt._input_bound(payload)
        self.assertGreater(bound, 4784)
        self.assertLess(bound, 7000)
    def test_outbound_wire_size_is_checked_before_network(self):
        self.cfg["max_payload_bytes"] = 100
        fake, state = FakeHTTP([]), {"requests": []}
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.BudgetExceeded, "max_payload_bytes"):
                rt.Provider(self.cfg, state, lambda: None).complete([{"role": "user", "content": "x" * 500}])
        self.assertEqual(fake.requests, [])
        self.assertEqual(state["requests"], [])
    def test_modified_native_blocks_are_rejected(self):
        msg = ant.assistant_message(tool_reply("read_file", {"path": "a"}, "r")["content"])
        msg["tool_calls"][0]["function"]["arguments"] = '{"path":"different"}'
        with self.assertRaisesRegex(ant.AnthropicProtocolError, "disagree"):
            ant.request_payload(self.cfg, [{"role": "user", "content": "test"}, msg])
    def test_thinking_and_redacted_thinking_do_not_contaminate_json_text(self):
        raw = reply([{"type": "thinking", "thinking": "", "signature": "keep"},
                     {"type": "redacted_thinking", "data": "encrypted"}, {"type": "text", "text": '{"ok":true}'}])
        (message, _), _, _ = self.complete([raw])
        self.assertEqual(message["content"], '{"ok":true}')
        self.assertEqual(message["provider_content"], raw["content"])
    def test_refusal_and_length_do_not_complete_a_json_role(self):
        for reason, expected in [("refusal", "refusal"), ("max_tokens", "truncated"), ("pause_turn", "pause_turn")]:
            fake = FakeHTTP([reply(stop=reason)])
            run = self.root / reason
            with patch.object(rt.urllib.request, "build_opener", return_value=fake):
                with self.assertRaisesRegex(rt.ProviderError, expected):
                    rt.run_json_role({"task": "test"}, run, self.cfg, "judge")
            saved = json.loads((run / "run.json").read_text())
            self.assertFalse(saved["completed"])
            self.assertEqual(saved["request_receipts"][0]["provider_stop_reason"], reason)
            self.assertEqual(saved["usage"]["completion_tokens"], 30)
    def test_different_model_response_is_not_accepted(self):
        raw = reply()
        raw["model"] = "claude-sonnet-other"
        with self.assertRaisesRegex(rt.ProviderError, "different model"):
            self.complete([raw])
    def test_native_secret_echo_is_redacted_everywhere(self):
        fake = FakeHTTP([reply([{"type": "text", "text": '{"result":"native-secret-key"}'}])])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            rt.run_json_role({"task": "test"}, self.root / "author", self.cfg, "author")
        for path in (self.root / "author").glob("*.json"):
            self.assertNotIn("native-secret-key", path.read_text())
    def test_unknown_network_outcome_is_never_replayed(self):
        public, run = self.public(), self.root / "run"
        fake = FakeHTTP([TimeoutError("native-secret-key")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "uncertain"):
                rt.run_agent(public, run, self.cfg)
            with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
                rt.run_agent(public, run, self.cfg, resume=True)
        self.assertEqual(len(fake.requests), 1)
        state = json.loads((run / "checkpoint.json").read_text())
        self.assertGreater(state["requests"][0]["charged_tokens"], 1000)
    def test_529_uncertain_and_error_type_redacted(self):
        body = b'{"error":{"type":"overloaded_error","message":"native-secret-key"}}'
        error = urllib.error.HTTPError(self.cfg["endpoint"], 529, "Overloaded", {}, io.BytesIO(body))
        fake, state = FakeHTTP([error]), {"requests": []}
        self.cfg["max_retries"] = 5
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            provider = rt.Provider(self.cfg, state, lambda: None)
            with self.assertRaises(rt.ProviderError):
                provider.complete([{"role": "user", "content": "test"}])
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(state["requests"][0]["status"], "uncertain")
        self.assertEqual(state["requests"][0]["provider_error_code"], "overloaded_error")
        self.assertNotIn("native-secret-key", json.dumps(state))
    def test_legacy_compaction_rejected_for_signed_thinking(self):
        self.cfg["context_compaction"] = {"strategy": "deterministic_tool_results"}
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "signed thinking"):
            rt.validate_config(self.cfg)
    def test_endpoint_and_native_settings_checked(self):
        for key, value in [("endpoint", "https://api.anthropic.com/v1/chat/completions"), ("provider", "unknown"),
                           ("effort", "invalid"), ("anthropic_version", "2023-06-01\nx-secret: yes"),
                           ("thinking", {"type": "disabled"}), ("prompt_cache", {"ttl": "forever"})]:
            with self.subTest(key=key), self.assertRaises(rt.RuntimeErrorBase):
                rt.validate_config({**self.cfg, key: value})
    def test_streamed_agent_native_tool_flow(self):
        self.cfg["stream"] = True
        fake = FakeHTTP([sse(events(tool_reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w", True))),
                         sse(events(tool_reply("finish", {"summary": "Done"}, "f")))])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(self.public(), self.root / "run", self.cfg)
        self.assertTrue(result["completed"])
        self.assertEqual(result["request_receipts"][0]["stream_diagnostics"]["termination"], "message_stop")
        self.assertNotIn("stream_options", fake.requests[0])


class AnthropicStreamTests(unittest.TestCase):
    def read(self, chunks, **kwargs):
        return ant.read_message(io.BytesIO(sse(chunks)), 100000, time.time() + 10, **kwargs)[0]
    def test_fragmented_tool_thinking_and_final_usage_roundtrip(self):
        result = tool_reply("read_file", {"path": "input/a.csv"}, "r", True)
        chunks = events(result)
        chunks.insert(1, {"type": "ping"})
        chunks.insert(2, {"type": "future_info", "something": "ignored"})
        self.assertEqual(self.read(chunks), result)
    def test_missing_message_stop_is_uncertain_even_after_final_usage(self):
        with self.assertRaisesRegex(StreamError, "message_stop"):
            self.read(events(reply())[:-1])
    def test_stream_error_never_returns_partial_tools(self):
        chunks = events(tool_reply("write_file", {"path": "output/a.txt", "content": "a"}, "w"))[:-2]
        chunks.append({"type": "error", "error": {"type": "overloaded_error", "message": "secret-provider-body"}})
        with self.assertRaisesRegex(StreamError, "streaming error") as failed:
            self.read(chunks)
        self.assertNotIn("secret-provider-body", str(failed.exception))
    def test_unclosed_block_and_missing_final_usage_rejected(self):
        for removed_type in ["content_block_stop", "message_delta"]:
            with self.subTest(removed_type=removed_type), self.assertRaises(StreamError):
                self.read([item for item in events(reply()) if item["type"] != removed_type])
    def test_invalid_partial_tool_json_rejected(self):
        chunks = events(tool_reply("read_file", {}, "r"))
        for item in chunks:
            if item["type"] == "content_block_delta":
                item["delta"]["partial_json"] = "invalid"
        with self.assertRaisesRegex(StreamError, "incomplete"):
            self.read(chunks)
    def test_final_usage_replaces_initial_cumulative_usage(self):
        result = self.read(events(reply(usage={"input_tokens": 9, "output_tokens": 17})))
        self.assertEqual(result["usage"]["output_tokens"], 17)
    def test_size_limit_and_deadline(self):
        with self.assertRaises(StreamError):
            ant.read_message(io.BytesIO(sse(events(reply()))), 10, time.time() + 1)
        with self.assertRaisesRegex(StreamError, "deadline"):
            ant.read_message(io.BytesIO(sse(events(reply()))), 10000, time.time() - 1)
    def test_partial_diagnostics_are_marked_nonterminal(self):
        saved = []
        with self.assertRaises(StreamError):
            self.read(events(reply())[:-1], progress=lambda info, partial: saved.append((info, partial)))
        self.assertEqual(saved[-1][0]["termination"], "rejected")
        self.assertFalse(saved[-1][0]["message_stop"])


if __name__ == "__main__":
    unittest.main()
