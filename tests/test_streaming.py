"""Streaming integration tests use in-memory SSE responses, never an API."""
import copy
import io
import http.client
import json
import time
import unittest
from unittest.mock import patch

from wgforge import runtime as rt
from wgforge.streaming import read_completion, StreamError
from tests import test_runtime as fixtures

reply = fixtures.reply


def sse(chunks, done=True):
    encoded = b": keepalive\r\n\r\n" + b"".join(
        b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\r\n\r\n" for chunk in chunks)
    return encoded + (b"data: [DONE]\r\n\r\n" if done else b"")


def deltas(response):
    message = copy.deepcopy(response["choices"][0]["message"])
    for index, call in enumerate(message.get("tool_calls", [])):
        call["index"] = index
    return [{"id": response["id"], "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
            {"id": response["id"], "choices": [{"index": 0, "delta": {}, "finish_reason": response["choices"][0]["finish_reason"]}]},
            {"id": response["id"], "choices": [], "usage": response["usage"]}]


class StreamingHTTP:
    def __init__(self, bodies):
        self.bodies, self.requests = list(bodies), []

    def open(self, request, timeout=None):
        self.requests.append(json.loads(request.data))
        return io.BytesIO(self.bodies.pop(0))


class StreamParserTests(unittest.TestCase):
    def parse(self, chunks, done=True, limit=100000, deadline=None):
        return read_completion(io.BytesIO(sse(chunks, done)), limit, deadline or time.time() + 60)[0]

    def test_split_unicode_arguments_and_reasoning_metadata(self):
        first = {"role": "assistant", "content": None, "reasoning_content": "sample ",
                 "reasoning_details": [{"index": 0, "id": "r1", "type": "reasoning.text", "format": "MiniMax-response-v1", "text": "part "}],
                 "tool_calls": [{"index": 0, "id": "w", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "output/备忘.txt",'}}]}
        second = {"reasoning_content": "continuation", "reasoning_details": [{"index": 0, "text": "two"}],
                  "tool_calls": [{"index": 0, "function": {"arguments": '"content": "资料"}'}}]}
        result = self.parse([
            {"id": "x", "choices": [{"delta": first}]},
            {"id": "x", "choices": [{"delta": second, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
            {"id": "x", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}])
        message = rt._assistant_message(result["choices"][0]["message"])
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"])["content"], "资料")
        self.assertEqual(message["reasoning_content"], "sample continuation")
        self.assertEqual(message["reasoning_details"][0]["text"], "part two")
        self.assertEqual(message["reasoning_details"][0]["format"], "MiniMax-response-v1")
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "completion_tokens": 4})

    def test_incomplete_stream_missing_usage_or_finish_rejected(self):
        chunks = deltas(reply("finish", {"summary": "done"}, "f"))
        for variant, done in ((chunks[:-1], False), (chunks[:-1], True),
                              (chunks[:1] + chunks[2:], True), (chunks[:1] + chunks[2:], False)):
            with self.subTest(variant=variant, done=done), self.assertRaises(StreamError):
                self.parse(variant, done)

    def test_minimax_complete_eof_without_done_is_accepted(self):
        # MiniMax's M3 test_02_03_sse_done_marker documents missing [DONE].
        response = reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w")
        chunks = deltas(response)
        expected = self.parse(chunks, True)
        self.assertEqual(self.parse(chunks, False), expected)
        # Terminal choice and usage can share a single final frame.
        chunks[1]["usage"] = chunks.pop()["usage"]
        self.assertEqual(self.parse(chunks, False), expected)
        # EOF may follow a complete data line without the final blank separator.
        result, _ = read_completion(io.BytesIO(sse(chunks, False).rstrip(b"\r\n")), 100000, time.time() + 60)
        self.assertEqual(result, expected)

    def test_eof_cannot_accept_invalid_or_nonfinal_usage(self):
        chunks = deltas(reply("finish", {"summary": "done"}, "f"))
        for usage in ({}, {"prompt_tokens": 1, "completion_tokens": -1},
                      {"prompt_tokens": True, "completion_tokens": 2},
                      {"prompt_tokens": 1, "completion_tokens": "2"}):
            changed = copy.deepcopy(chunks)
            changed[-1]["usage"] = usage
            with self.subTest(usage=usage), self.assertRaisesRegex(StreamError, "final usage"):
                self.parse(changed, False)
        changed = copy.deepcopy(chunks[:2])
        changed[0]["usage"] = chunks[-1]["usage"]
        with self.assertRaisesRegex(StreamError, "final usage"):
            self.parse(changed, False)

    def test_eof_incomplete_json_or_tool_arguments_stay_errors(self):
        chunks = deltas(reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"))
        chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = '{"path":'
        with self.assertRaisesRegex(StreamError, "incomplete tool-call"):
            self.parse(chunks, False)
        with self.assertRaisesRegex(StreamError, "Malformed streaming JSON"):
            read_completion(io.BytesIO(b'data: {"choices": ['), 1000, time.time() + 60)

    def test_terminal_usage_frame_may_repeat_empty_assistant_role(self):
        chunks = deltas(reply("finish", {"summary": "done"}, "f"))
        chunks[-1]["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": "tool_calls"}]
        self.assertEqual(self.parse(chunks, False)["usage"]["prompt_tokens"], 120)

    def test_transport_exception_keeps_bounded_partial_diagnostics(self):
        chunks = deltas(reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"))
        class Interrupted(io.BytesIO):
            def readline(self, size=-1):
                value = super().readline(size)
                if not value:
                    raise http.client.IncompleteRead(b"untrusted raw tail")
                return value
        for prefix in (chunks[:1], chunks):
            updates = []
            with self.assertRaisesRegex(StreamError, "transport failed"):
                read_completion(Interrupted(sse(prefix, False)), 100000, time.time() + 60,
                    progress=lambda info, partial: updates.append((info, partial)))
            self.assertEqual(updates[-1][0]["termination"], "transport_error")
            self.assertGreater(updates[-1][0]["received_bytes"], 0)
            self.assertEqual(updates[-1][1]["tool_calls"][0]["id"], "w")
            self.assertNotIn("untrusted raw tail", str(updates))
        updates = []
        with self.assertRaises(StreamError):
            read_completion(io.BytesIO(sse(chunks[:1], False)), 100000, time.time() + 60,
                progress=lambda info, partial: updates.append((info, partial)), max_message_bytes=10)
        self.assertIsNone(updates[-1][1])
        self.assertFalse(updates[-1][0]["partial_message_saved"])

    def test_stream_error_invalid_utf8_size_and_deadline_are_bounded(self):
        for chunk in ({"error": {"message": "sensitive provider body"}}, {"base_resp": {"status_code": 1000}}):
            with self.assertRaises(StreamError) as caught:
                self.parse([chunk])
            self.assertNotIn("sensitive provider body", str(caught.exception))
        chunks = deltas(reply("finish", {"summary": "done"}, "f"))
        with self.assertRaisesRegex(StreamError, "size limit"):
            self.parse(chunks, limit=30)
        with self.assertRaisesRegex(StreamError, "deadline"):
            self.parse(chunks, deadline=time.time() - 1)
        with self.assertRaisesRegex(StreamError, "UTF-8"):
            read_completion(io.BytesIO(b"data: \xff\n\n"), 1000, time.time() + 60)

    def test_conflicting_identity_and_extra_choices_rejected(self):
        for chunks in (
            [{"id": "x", "choices": []}, {"id": "y", "choices": []}],
            [{"choices": [{"index": 1, "delta": {}}]}],
            [{"choices": [{"delta": {}}, {"delta": {}}]}],
        ):
            with self.assertRaises(StreamError):
                self.parse(chunks)


class StreamingRuntimeTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def test_complete_stream_writes_artifact_then_finishes(self):
        self.cfg["stream"] = True
        fake = StreamingHTTP([sse(deltas(reply("write_file", {"path": "output/memo.txt", "content": "Memo"}, "w"))),
                              sse(deltas(reply("finish", {"summary": "done"}, "f")))])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertTrue(result["completed"])
        self.assertEqual((self.run / "workspace/output/memo.txt").read_text(), "Memo")
        self.assertTrue(fake.requests[0]["stream"])
        self.assertEqual(fake.requests[0]["stream_options"], {"include_usage": True})
        self.assertEqual(result["usage"]["completion_tokens"], 160)

    def test_json_role_uses_streaming_and_parses_completed_content(self):
        self.cfg["stream"] = True
        response = {"id": "json", "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'},
                    "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}}
        fake = StreamingHTTP([sse(deltas(response))])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_json_role({"instructions": "Return JSON"}, self.run, self.cfg, "author")
        self.assertEqual(result["result"], {"ok": True})
        self.assertEqual(result["receipt"]["usage"]["completion_tokens"], 10)

    def test_interrupted_stream_never_executes_partial_tool_or_replays(self):
        self.cfg["stream"] = True
        chunks = deltas(reply("write_file", {"path": "output/memo.txt", "content": "Memo"}, "w"))
        fake = StreamingHTTP([sse(chunks[:1], done=False)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "uncertain"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
            with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
                rt.run_agent(self.task / "public", self.run, self.cfg, resume=True)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(rt.tree_hashes(self.run / "workspace/output"), {})
        state = json.loads((self.run / "checkpoint.json").read_text())
        self.assertEqual(state["requests"][0]["status"], "uncertain")
        self.assertEqual(state["consumed_requests"], [])
        self.assertGreater(state["requests"][0]["charged_tokens"], 200)
        self.assertIsNone(state["requests"][0]["stream_diagnostics"]["finish_reason"])
        self.assertEqual(state["requests"][0]["partial_message"]["tool_calls"][0]["id"], "w")
        receipt = json.loads((self.run / "run.json").read_text())["request_receipts"][0]
        self.assertNotIn("partial_message", receipt)
        self.assertIn("stream_diagnostics", receipt)

    def test_complete_done_less_stream_executes_once_and_counts_actual_usage(self):
        self.cfg["stream"] = True
        fake = StreamingHTTP([sse(deltas(reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w")), False),
                              sse(deltas(reply("finish", {"summary": "done"}, "f")), False)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(self.task / "public", self.run, self.cfg)
            self.assertEqual(rt.run_agent(self.task / "public", self.run, self.cfg, resume=True), result)
        self.assertTrue(result["completed"])
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(result["usage"]["reserved_or_reported_tokens"], 400)
        self.assertTrue(all(r["stream_diagnostics"]["termination"] == "eof_after_finish" for r in result["request_receipts"]))
        self.assertEqual((self.run / "workspace/output/memo.txt").read_text(), "memo")

    def test_saved_partial_stream_redacts_credentials_and_is_never_consumed(self):
        self.cfg["stream"] = True
        chunks = deltas(reply("write_file", {"path": "output/memo.txt", "content": "unit-secret-credential"}, "w"))
        fake = StreamingHTTP([sse(chunks[:1], False)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaises(rt.ProviderError):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        checkpoint = (self.run / "checkpoint.json").read_text()
        self.assertNotIn("unit-secret-credential", checkpoint)
        self.assertIn("[REDACTED]", checkpoint)
        self.assertFalse((self.run / "workspace/output/memo.txt").exists())

    def test_streaming_length_is_saved_but_not_executed(self):
        self.cfg["stream"] = True
        response = reply("write_file", {"path": "output/memo.txt", "content": "partial"}, "w")
        response["choices"][0]["finish_reason"] = "length"
        fake = StreamingHTTP([sse(deltas(response), False)])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "truncated"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertEqual(rt.tree_hashes(self.run / "workspace/output"), {})
        state = json.loads((self.run / "checkpoint.json").read_text())
        self.assertEqual(state["requests"][0]["status"], "succeeded")
        self.assertEqual(state["requests"][0]["charged_tokens"], 200)


if __name__ == "__main__":
    unittest.main()
