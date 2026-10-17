"""Unit tests use only mock HTTP transport, never a billable API."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from wgforge import runtime as rt


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.data[:limit]


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
    def open(self, request, timeout=None):
        self.requests.append((json.loads(request.data), timeout))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return Response(item)


def config():
    return {"approve_live": True, "endpoint": "https://mock.example.com/v1/chat/completions",
            "model": "mock-model-not-real", "api_key_env": "WGFORGE_TEST_KEY", "allowed_hosts": ["mock.example.com"],
            "privacy": {"public_upload_approved": True}, "max_retries": 0, "timeout_seconds": 3,
            "budget": {"max_cost_usd": 2, "input_usd_per_million": 1, "output_usd_per_million": 2,
                       "max_requests": 12, "max_steps": 10, "max_input_tokens": 100000,
                       "max_output_tokens": 1000, "max_total_tokens": 500000}}


def reply(name, args, call_id):
    return {"id": "mock-response-" + call_id, "choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 120, "completion_tokens": 80}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = self.root / "task"
        (self.task / "public" / "anchors").mkdir(parents=True)
        (self.task / "private").mkdir()
        (self.task / "public" / "task.json").write_text(json.dumps({"task_id": "unit", "prompt": "Create a memo from sources."}))
        (self.task / "public" / "anchors" / "source.txt").write_text("PUBLIC SOURCE")
        (self.task / "private" / "rubric.json").write_text('{"answer":"PRIVATE CANARY"}')
        self.run = self.root / "run"
        self.cfg = config()
        self.environment = patch.dict(os.environ, {"WGFORGE_TEST_KEY": "unit-secret-credential"})
        self.environment.start()
    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()
    def live(self, responses, resume=False):
        fake = FakeHTTP(responses)
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_agent(self.task / "public", self.run, self.cfg, resume=resume)
        return result, fake
    def completed(self):
        return self.live([reply("read_file", {"path": "input/anchors/source.txt"}, "r"),
                          reply("write_file", {"path": "output/memo.txt", "content": "A grounded memo"}, "w"),
                          reply("finish", {"summary": "Saved memo."}, "f")])
    def test_rollout_schema_privacy_and_usage(self):
        result, fake = self.completed()
        self.assertTrue(result["completed"])
        self.assertEqual(result["usage"]["requests"], 3)
        self.assertIn("PUBLIC SOURCE", json.dumps(fake.requests[1]))
        self.assertNotIn("PRIVATE CANARY", json.dumps(fake.requests))
        self.assertEqual(fake.requests[0][1], 3)
        for path in self.run.glob("*.json"):
            self.assertNotIn("unit-secret-credential", path.read_text())
        messages = json.loads((self.run / "trajectory.json").read_text())["messages"]
        self.assertEqual(messages[3]["tool_call_id"], "r")
        self.assertEqual(messages[2]["tool_calls"][0]["type"], "function")
    def test_resume_done_uses_no_http(self):
        result, _ = self.completed()
        again, fake = self.live([], resume=True)
        self.assertEqual(fake.requests, [])
        self.assertEqual(result, again)
    def test_resume_budget_changes_are_rejected(self):
        self.completed()
        self.cfg["budget"]["max_cost_usd"] = 200
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "identical"):
            self.live([], resume=True)
    def test_recovered_response_not_paid_twice(self):
        fake = FakeHTTP([reply("write_file", {"path": "output/memo.txt", "content": "Memo"}, "w")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        result, recovered = self.live([reply("finish", {"summary": "Done"}, "f")], resume=True)
        self.assertTrue(result["completed"])
        self.assertEqual(len(recovered.requests), 1)
    def test_inflight_not_replayed(self):
        with self.assertRaises(KeyboardInterrupt):
            self.live([KeyboardInterrupt()])
        with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
            self.live([], resume=True)
        self.assertEqual(len(json.loads((self.run / "checkpoint.json").read_text())["requests"]), 1)
    def test_retries_spend_full_failed_reservation(self):
        self.cfg["max_retries"] = 1
        error = urllib.error.HTTPError(self.cfg["endpoint"], 429, "limit", {}, io.BytesIO(b'{"error":{"code":"rate_limit","message":"unit-secret-credential"}}'))
        fake = FakeHTTP([error, reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"), reply("finish", {"summary": "done"}, "f")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt.time, "sleep"):
            result = rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertEqual(result["usage"]["requests"], 3)
        self.assertGreater(result["cost"]["charged_upper_bound_usd"], result["cost"]["reported_usd"])
        self.assertNotIn("unit-secret-credential", (self.run / "checkpoint.json").read_text())
        self.assertEqual(result["request_receipts"][0]["provider_error_code"], "rate_limit")
    def test_budget_blocks_network_before_any_upload(self):
        self.cfg["budget"]["max_cost_usd"] = 0.000001
        fake = FakeHTTP([])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaises(rt.BudgetExceeded):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertEqual(fake.requests, [])
    def test_token_budget_reports_next_reservation_before_network(self):
        messages = [{"role": "user", "content": "A small request"}]
        input_bound = rt._input_bound(rt._request_payload(self.cfg, messages))
        output_bound = self.cfg["budget"]["max_output_tokens"]
        used = 5000
        remaining = input_bound + output_bound - 1
        self.cfg["budget"]["max_total_tokens"] = used + remaining
        state = {"requests": [{"status": "succeeded", "charged_tokens": used, "charged_usd": 0.01}]}
        fake = FakeHTTP([])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            provider = rt.Provider(self.cfg, state, lambda: None)
            with self.assertRaises(rt.BudgetExceeded) as failed:
                provider.complete(messages)
        message = str(failed.exception)
        for detail in (f"used={used}", f"remaining={remaining}", f"input_upper_bound={input_bound}",
                       f"output_limit={output_bound}", "No API request was sent"):
            self.assertIn(detail, message)
        self.assertNotIn("uncertain requests", message)
        self.assertEqual(fake.requests, [])
        self.assertEqual(len(state["requests"]), 1)
    def test_endpoint_consent_and_key_controls(self):
        for key, value in [("endpoint", "https://evil.example.com/v1/chat/completions"), ("endpoint", "http://mock.example.com/v1/chat/completions"),
                           ("endpoint", "https://user:pass@mock.example.com/v1/chat/completions"), ("approve_live", False), ("api_key", "SECRET")]:
            candidate = {**self.cfg, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(rt.RuntimeErrorBase):
                rt.validate_config(candidate)
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "private rubrics"):
            rt.validate_config(self.cfg, private=True)
    def test_malformed_provider_cases(self):
        for payload in [[], {}, {"usage": []}, {"usage": {"prompt_tokens": 1, "completion_tokens": 2}, "choices": [None]}]:
            state = {"requests": []}
            fake = FakeHTTP([payload])
            with patch.object(rt.urllib.request, "build_opener", return_value=fake):
                provider = rt.Provider(self.cfg, state, lambda: None)
                with self.assertRaises(rt.ProviderError):
                    provider.complete([{"role": "user", "content": "test"}])
            self.assertEqual(state["requests"][0]["status"], "invalid_response")
    def test_secret_echo_redacted(self):
        result, _ = self.live([reply("write_file", {"path": "output/memo.txt", "content": "unit-secret-credential"}, "w"), reply("finish", {"summary": "done"}, "f")])
        self.assertTrue(result["completed"])
        self.assertNotIn("unit-secret-credential", (self.run / "checkpoint.json").read_text())
        self.assertEqual((self.run / "workspace" / "output" / "memo.txt").read_text(), "[REDACTED]")
    def test_paths_tools_and_output_limits(self):
        sandbox = rt.Sandbox(self.root / "sandbox", {"max_file_bytes": 10})
        for path in ["/etc/passwd", "input/../private/key", "input\\secret", "output//x", "output/./x"]:
            with self.subTest(path=path), self.assertRaises(rt.RuntimeErrorBase):
                sandbox.execute("read_file", {"path": path})
        for name, args in [("shell", {"command": "pwd"}), ("write_file", {"path": "input/x.txt", "content": "x"}),
                           ("write_file", {"path": "output/x.py", "content": "pass"}),
                           ("write_file", {"path": "output/x.txt", "content": "x" * 11})]:
            with self.assertRaises(rt.RuntimeErrorBase):
                sandbox.execute(name, args)
        (sandbox.root / "input" / "link").symlink_to(self.task / "private" / "rubric.json")
        with self.assertRaises(rt.RuntimeErrorBase):
            sandbox.execute("read_file", {"path": "input/link"})
    def test_table_pagination(self):
        sandbox = rt.Sandbox(self.root / "sandbox")
        (sandbox.root / "input" / "rows.csv").write_text("name,value\na,1\nb,2\n")
        result = sandbox.execute("read_table", {"path": "input/rows.csv", "offset": 1, "limit": 1})
        self.assertEqual(result["rows"], [["b", "2"]])
        self.assertEqual(result["total_rows"], 2)
    def test_wall_deadline_survives_resume(self):
        fake = FakeHTTP([reply("read_file", {"path": "input/anchors/source.txt"}, "r")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        checkpoint = json.loads((self.run / "checkpoint.json").read_text())
        checkpoint["started_at_epoch"] = 0
        rt.atomic_json(self.run / "checkpoint.json", checkpoint)
        with self.assertRaisesRegex(rt.BudgetExceeded, "deadline"):
            self.live([], resume=True)


class IsolatedRoleTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown
    live = RuntimeTests.live
    completed = RuntimeTests.completed
    def json_reply(self, result):
        return {"id": "json-response", "choices": [{"message": {"role": "assistant", "content": json.dumps(result)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20}}

    def test_network_uncertainty_never_replays(self):
        self.cfg["max_retries"] = 5
        fake = FakeHTTP([TimeoutError("unit-secret-credential")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "uncertain"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
            with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
                rt.run_agent(self.task / "public", self.run, self.cfg, resume=True)
        self.assertEqual(len(fake.requests), 1)
        receipt = json.loads((self.run / "run.json").read_text())
        self.assertEqual(receipt["request_receipts"][0]["status"], "uncertain")
        self.assertGreater(receipt["cost"]["charged_upper_bound_usd"], 0)

    def test_http_500_never_replays(self):
        self.cfg["max_retries"] = 5
        failure = urllib.error.HTTPError(self.cfg["endpoint"], 500, "unknown", {}, io.BytesIO(b"secret body"))
        fake = FakeHTTP([failure])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaises(rt.ProviderError):
                rt.run_agent(self.task / "public", self.run, self.cfg)
            with self.assertRaisesRegex(rt.ProviderError, "unknown completion"):
                rt.run_agent(self.task / "public", self.run, self.cfg, resume=True)
        self.assertEqual(len(fake.requests), 1)
        self.assertNotIn("secret body", (self.run / "checkpoint.json").read_text())

    def test_public_root_and_private_contract_rejected_before_http(self):
        fake = FakeHTTP([])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.RuntimeErrorBase, "Public directory"):
                rt.run_agent(self.task, self.run, self.cfg)
            (self.task / "public" / "task.json").write_text('{"rubric": "PRIVATE CANARY"}')
            with self.assertRaisesRegex(rt.RuntimeErrorBase, "Private grading"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertEqual(fake.requests, [])

    def test_separate_roles_do_not_inherit_any_transcript(self):
        author = FakeHTTP([self.json_reply({"created": "AUTHOR ONLY"})])
        with patch.object(rt.urllib.request, "build_opener", return_value=author):
            result = rt.run_json_role({"instructions": "Create JSON", "source": "PUBLIC"}, self.root / "author", self.cfg, "author")
        self.assertEqual(result["result"], {"created": "AUTHOR ONLY"})
        solver, fake = self.completed()
        self.assertNotIn("AUTHOR ONLY", json.dumps(fake.requests))
        self.assertNotIn("PRIVATE CANARY", json.dumps(fake.requests))
        self.assertEqual(result["receipt"]["role"], "author")
        self.assertNotEqual(result["receipt"]["prompt_digest"], solver["prompt_digest"])

    def test_every_privileged_role_requires_separate_approval(self):
        with patch.object(rt.urllib.request, "build_opener") as opened:
            for role in rt.PRIVATE_ROLES:
                with self.subTest(role=role), self.assertRaisesRegex(rt.RuntimeErrorBase, "private_upload_approved"):
                    rt.run_json_role({"rubric": "PRIVATE CANARY"}, self.root / role, self.cfg, role)
            opened.assert_not_called()

    def test_json_role_completed_resume_uses_no_network(self):
        self.cfg["privacy"]["private_upload_approved"] = True
        fake = FakeHTTP([self.json_reply({"scores": []})])
        payload = {"instructions": "Return scores", "rubric": "PRIVATE CANARY"}
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_json_role(payload, self.run, self.cfg, "judge")
            resumed = rt.run_json_role(payload, self.run, self.cfg, "judge", resume=True)
        self.assertEqual(result, resumed)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(result["receipt"]["payload_digest"], rt.digest(payload))
        self.assertTrue(result["receipt"]["private_upload"])
        self.assertTrue(fake.requests[0][0]["response_format"] == {"type": "json_object"})

    def test_json_role_recovery_from_committed_response(self):
        payload = {"instructions": "Return an object"}
        fake = FakeHTTP([self.json_reply({"answer": 42})])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_json_role(payload, self.run, self.cfg, "author")
        empty = FakeHTTP([])
        with patch.object(rt.urllib.request, "build_opener", return_value=empty):
            result = rt.run_json_role(payload, self.run, self.cfg, "author", resume=True)
        self.assertEqual(result["result"], {"answer": 42})
        self.assertEqual(empty.requests, [])

    def test_json_role_changed_payload_or_role_rejected(self):
        payload = {"instructions": "Return an object"}
        fake = FakeHTTP([self.json_reply({"answer": 42})])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            rt.run_json_role(payload, self.run, self.cfg, "author")
            with self.assertRaisesRegex(rt.RuntimeErrorBase, "identical"):
                rt.run_json_role({"different": True}, self.run, self.cfg, "author", resume=True)
        self.assertEqual(len(fake.requests), 1)

    def test_output_hash_and_trajectory_provenance(self):
        report, _ = self.completed()
        self.assertEqual(report["artifact_digest"], rt.digest(report["artifact_hashes"]))
        self.assertEqual(report["trajectory_digest"], rt.file_hash(self.run / "trajectory.json"))
        self.assertEqual(report["config_digest"], rt.digest(self.cfg))
        self.assertTrue(all(Path(p).is_absolute() and Path(p).is_file() for p in report["artifact_paths"]))
        (self.run / "workspace" / "output" / "memo.txt").write_text("tampered")
        with self.assertRaisesRegex(rt.RuntimeErrorBase, "changed"):
            self.live([], resume=True)

    def test_teacher_gets_only_public_input(self):
        fake = FakeHTTP([reply("write_file", {"path": "output/memo.txt", "content": "Memo"}, "w"), reply("finish", {"summary": "done"}, "f")])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            report = rt.run_agent(self.task / "public", self.run, self.cfg, role="teacher")
        self.assertEqual(report["role"], "teacher")
        self.assertNotIn("PRIVATE CANARY", json.dumps(fake.requests))

    def test_native_code_tool_not_advertised_by_default(self):
        _, fake = self.completed()
        self.assertNotIn("execute_python", [t["function"]["name"] for t in fake.requests[0][0]["tools"]])


class MiniMaxCompatibilityTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown
    live = RuntimeTests.live
    json_reply = IsolatedRoleTests.json_reply

    def test_split_thinking_survives_tool_followup_trace_and_resume(self):
        self.cfg.update(model="MiniMax-M3", reasoning_split=True, use_json_response_format=False)
        first = reply("write_file", {"path": "output/memo.txt", "content": "A grounded memo"}, "w")
        raw = first["choices"][0]["message"]
        raw.update(reasoning_content="Preserve this thinking exactly.",
                   reasoning_details=[{"type": "reasoning.text", "text": "A separate reasoning block.",
                                       "signature": "test-signature", "index": 0}])
        result, fake = self.live([first, reply("finish", {"summary": "Saved memo."}, "f")])
        self.assertTrue(result["completed"])
        self.assertTrue(fake.requests[0][0]["reasoning_split"])
        self.assertEqual(fake.requests[1][0]["messages"][2], raw)
        trace = json.loads((self.run / "trajectory.json").read_text())
        self.assertEqual(trace["messages"][2], raw)
        resumed, empty = self.live([], resume=True)
        self.assertEqual(resumed, result)
        self.assertEqual(empty.requests, [])

    def test_json_role_parses_only_content_and_preserves_split_thinking(self):
        self.cfg.update(model="MiniMax-M3", reasoning_split=True, use_json_response_format=False)
        response = self.json_reply({"answer": 42})
        raw = response["choices"][0]["message"]
        raw["reasoning_content"] = "This thinking text is deliberately not JSON."
        fake = FakeHTTP([response])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_json_role({"instructions": "Return JSON"}, self.run, self.cfg, "author")
        self.assertEqual(result["result"], {"answer": 42})
        self.assertTrue(fake.requests[0][0]["reasoning_split"])
        self.assertNotIn("response_format", fake.requests[0][0])
        trace = json.loads((self.run / "trajectory.json").read_text())
        self.assertEqual(trace["messages"][-1], raw)

    def test_prompt_only_json_still_rejects_prose_and_nonobjects(self):
        self.cfg.update(reasoning_split=True, use_json_response_format=False)
        invalid = ('Here is the answer: {"answer": 42}', '[]', '```json\n{}\n```\nExtra prose',
                   '```json\n{}\n```\n```json\n{}\n```', '```json\n[]\n```', '```json\n{"a": }\n```')
        for index, content in enumerate(invalid):
            with self.subTest(content=content):
                response = self.json_reply({})
                response["choices"][0]["message"].update(content=content, reasoning_content="Thinking")
                fake = FakeHTTP([response])
                with patch.object(rt.urllib.request, "build_opener", return_value=fake):
                    with self.assertRaisesRegex(rt.ProviderError, "one valid JSON object"):
                        rt.run_json_role({"instructions": "Return JSON"}, self.run / str(index), self.cfg, "author")
                self.assertEqual(len(fake.requests), 1)
                self.assertFalse(json.loads((self.run / str(index) / "run.json").read_text())["completed"])

    def test_prompt_only_json_accepts_single_fence_without_changing_trace(self):
        self.cfg.update(reasoning_split=True, use_json_response_format=False)
        response = self.json_reply({"answer": 42})
        raw = response["choices"][0]["message"]
        raw["content"] = '  \n```json\n{"answer": 42}\n```\n'
        raw["reasoning_content"] = "Thinking stays outside the parsed object."
        fake = FakeHTTP([response])
        payload = {"instructions": "Return JSON"}
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            result = rt.run_json_role(payload, self.run, self.cfg, "author")
            resumed = rt.run_json_role(payload, self.run, self.cfg, "author", resume=True)
        self.assertEqual(result["result"], {"answer": 42})
        self.assertEqual(resumed, result)
        self.assertEqual(len(fake.requests), 1)
        trace = json.loads((self.run / "trajectory.json").read_text())
        self.assertEqual(trace["messages"][-1], raw)

    def test_structured_json_mode_still_rejects_fenced_response(self):
        response = self.json_reply({})
        response["choices"][0]["message"]["content"] = '```json\n{}\n```'
        fake = FakeHTTP([response])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "one valid JSON object"):
                rt.run_json_role({"instructions": "Return JSON"}, self.run, self.cfg, "author")
        self.assertEqual(fake.requests[0][0]["response_format"], {"type": "json_object"})

    def test_invalid_compatibility_settings_reject_before_http(self):
        for option in ("reasoning_split", "use_json_response_format"):
            with self.subTest(option=option):
                cfg = {**self.cfg, option: "true"}
                with patch.object(rt.urllib.request, "build_opener") as opened:
                    with self.assertRaisesRegex(rt.RuntimeErrorBase, "must be a boolean"):
                        rt.run_json_role({"instructions": "Return JSON"}, self.run, cfg, "author")
                    opened.assert_not_called()


class ArtifactContractRuntimeTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown
    live = RuntimeTests.live

    def test_writer_error_explains_header_array_and_followup_can_complete(self):
        path = "output/analysis.xlsx"
        wrong = {"tables": [{"name": "Evidence", "headers": "Field,Value", "rows": [["A", 12]]}]}
        correct = {"tables": [{"name": "Evidence", "headers": ["Field", "Value"], "rows": [["A", 12]]}]}
        report, fake = self.live([
            reply("write_workbook", {"path": path, "spec": wrong}, "bad"),
            reply("write_workbook", {"path": path, "spec": correct}, "fixed"),
            reply("finish", {"summary": "Workbook written"}, "f"),
        ])
        error = json.loads(fake.requests[1][0]["messages"][-1]["content"])["error"]
        self.assertIn("spec.tables[0].headers", error)
        self.assertIn('JSON array of strings, e.g. ["Field", "Value"]', error)
        definition = next(t for t in fake.requests[0][0]["tools"] if t["function"]["name"] == "write_workbook")
        headers = definition["function"]["parameters"]["properties"]["spec"]["properties"]["tables"]["items"]["properties"]["headers"]
        self.assertEqual(headers["type"], "array")
        self.assertEqual(headers["items"]["type"], "string")
        self.assertTrue(report["completed"])
        from wgforge.artifacts import extract_artifact
        table = extract_artifact(self.run / "workspace" / path)["tables"][0]
        self.assertEqual(table["headers"], ["Field", "Value"])
        self.assertEqual(table["rows"], [["A", 12]])


class TruncatedResponseTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown

    def test_reasoning_only_length_retains_receipt_and_does_not_retry_on_resume(self):
        self.cfg["max_retries"] = 5
        raw = {"role": "assistant", "reasoning_content": "Incomplete mock reasoning"}
        response = {"id": "truncated", "choices": [{"message": raw, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 1000}}
        fake = FakeHTTP([response])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            for resume in (False, True):
                with self.assertRaisesRegex(rt.ProviderError, "finish_reason=length; max_output_tokens=1000"):
                    rt.run_agent(self.task / "public", self.run, self.cfg, resume=resume)
        self.assertEqual(len(fake.requests), 1)
        checkpoint = json.loads((self.run / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["requests"][0]["message"], raw)
        self.assertEqual(checkpoint["requests"][0]["status"], "succeeded")
        self.assertEqual(checkpoint["consumed_requests"], [])
        report = json.loads((self.run / "run.json").read_text())
        self.assertFalse(report["completed"])
        self.assertEqual(report["usage"]["reserved_or_reported_tokens"], 1100)
        self.assertGreater(report["cost"]["reported_usd"], 0)

    def test_length_tool_calls_are_never_executed_even_if_arguments_parse(self):
        for suffix, arguments in (("valid", '{"path":"output/memo.txt","content":"partial"}'),
                                  ("partial", '{"path":"output/memo.txt","content":')):
            with self.subTest(suffix=suffix):
                response = reply("write_file", {}, "w")
                response["choices"][0]["finish_reason"] = "length"
                response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
                fake = FakeHTTP([response])
                with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt.Sandbox, "execute") as execute:
                    with self.assertRaisesRegex(rt.ProviderError, "truncated"):
                        rt.run_agent(self.task / "public", self.run / suffix, self.cfg)
                    execute.assert_not_called()
                self.assertFalse((self.run / suffix / "workspace" / "output" / "memo.txt").exists())

    def test_length_finish_does_not_accept_existing_artifacts(self):
        truncated = reply("finish", {"summary": "unfinished"}, "f")
        truncated["choices"][0]["finish_reason"] = "length"
        fake = FakeHTTP([reply("write_file", {"path": "output/memo.txt", "content": "Saved draft"}, "w"), truncated])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.ProviderError, "truncated"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        report = json.loads((self.run / "run.json").read_text())
        self.assertFalse(report["completed"])
        self.assertIn("memo.txt", report["artifact_hashes"])
        self.assertEqual(len(fake.requests), 2)

    def test_json_length_is_reported_before_json_parsing_and_blocks_resume_retry(self):
        for suffix, content in (("partial", '{"answer":'), ("valid", '{"answer":42}')):
            with self.subTest(suffix=suffix):
                response = {"id": "truncated-json", "choices": [{"message": {"role": "assistant", "content": content},
                             "finish_reason": "length"}], "usage": {"prompt_tokens": 50, "completion_tokens": 1000}}
                fake = FakeHTTP([response])
                with patch.object(rt.urllib.request, "build_opener", return_value=fake):
                    for resume in (False, True):
                        with self.assertRaisesRegex(rt.ProviderError, "finish_reason=length"):
                            rt.run_json_role({"instructions": "Return JSON"}, self.run / suffix, self.cfg, "author", resume=resume)
                self.assertEqual(len(fake.requests), 1)
                self.assertFalse((self.run / suffix / "result.json").exists())


class ContextCompactionTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown
    live = RuntimeTests.live

    def test_full_trace_retained_and_exact_provider_contexts_saved(self):
        self.cfg["context_compaction"] = {"strategy": "deterministic_tool_results", "window_tokens": 256000,
            "trigger_fraction": 0.7, "keep_recent_messages": 0, "minimum_result_chars": 256}
        self.cfg["budget"]["max_input_tokens"] = 12000
        source = "UNIQUE LARGE PUBLIC SOURCE " * 1000
        (self.task / "public" / "anchors" / "source.txt").write_text(source)
        result, fake = self.live([reply("read_file", {"path": "input/anchors/source.txt"}, "r"),
                                 reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"),
                                 reply("finish", {"summary": "done"}, "f")])
        self.assertTrue(result["completed"])
        self.assertGreater(result["context_compaction_count"], 0)
        trajectory = json.loads((self.run / "trajectory.json").read_text())
        self.assertIn(source, trajectory["messages"][3]["content"])
        self.assertTrue(trajectory["context_events"])
        self.assertTrue(trajectory["provider_contexts"])
        compacted_receipt = next(r for r in result["request_receipts"] if "context_compaction_id" in r)
        context = next(x for x in trajectory["provider_contexts"] if x["context_event_id"] == compacted_receipt["context_compaction_id"])
        actual_request = fake.requests[compacted_receipt["index"]][0]
        self.assertEqual(actual_request["messages"], context["messages"])
        self.assertEqual(rt.digest(context["messages"]), compacted_receipt["context_digest"])
        self.assertEqual(rt.digest(actual_request), compacted_receipt["request_digest"])
        self.assertNotIn(source, json.dumps(context["messages"]))
        self.assertIn("Historical tool content was explicitly omitted", json.dumps(context["messages"]))
        self.assertEqual(result["usage"]["requests"], 3)

    def test_compaction_disabled_means_no_trace_events(self):
        report, _ = self.live([reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w"),
                               reply("finish", {"summary": "done"}, "f")])
        trajectory = json.loads((self.run / "trajectory.json").read_text())
        self.assertEqual(trajectory["context_events"], [])
        self.assertEqual(trajectory["provider_contexts"], [])
        self.assertEqual(report["context_compaction_strategy"], "none")

    def test_compaction_has_no_silent_context_overrun(self):
        self.cfg["context_compaction"] = {"strategy": "deterministic_tool_results", "window_tokens": 1500}
        fake = FakeHTTP([])
        with patch.object(rt.urllib.request, "build_opener", return_value=fake):
            with self.assertRaisesRegex(rt.BudgetExceeded, "context capacity"):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        self.assertEqual(fake.requests, [])

    def test_invalid_compaction_configuration_rejected(self):
        for settings in ({"strategy": "silently_summarize"}, {"strategy": "deterministic_tool_results", "trigger_fraction": float("nan")},
                         {"strategy": "deterministic_tool_results", "keep_recent_messages": -1}):
            self.cfg["context_compaction"] = settings
            with self.subTest(settings=settings), self.assertRaises(rt.RuntimeErrorBase):
                rt.validate_config(self.cfg)

    def test_compacted_successful_response_resumes_without_repaying(self):
        self.cfg["context_compaction"] = {"strategy": "deterministic_tool_results", "window_tokens": 256000,
            "trigger_fraction": 0.7, "keep_recent_messages": 0, "minimum_result_chars": 256}
        self.cfg["budget"]["max_input_tokens"] = 12000
        (self.task / "public" / "anchors" / "source.txt").write_text("LARGE SOURCE " * 1800)
        fake = FakeHTTP([reply("read_file", {"path": "input/anchors/source.txt"}, "r"),
                         reply("write_file", {"path": "output/memo.txt", "content": "memo"}, "w")])
        original = rt._assistant_message
        calls = []
        def interrupted(message):
            calls.append(message)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return original(message)
        with patch.object(rt.urllib.request, "build_opener", return_value=fake), patch.object(rt, "_assistant_message", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                rt.run_agent(self.task / "public", self.run, self.cfg)
        result, resumed = self.live([reply("finish", {"summary": "done"}, "f")], resume=True)
        self.assertTrue(result["completed"])
        self.assertEqual(len(resumed.requests), 1)
        self.assertEqual(result["usage"]["requests"], 3)
        trace = json.loads((self.run / "trajectory.json").read_text())
        recovery_context = next(x for x in trace["provider_contexts"] if 1 in x["request_indices"])
        self.assertEqual(recovery_context["messages"], fake.requests[1][0]["messages"])
