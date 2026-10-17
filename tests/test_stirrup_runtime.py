"""Paper harness contract tests; no LLM, Docker, E2B, or search requests."""
import asyncio
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from wgforge.common import ForgeError, read_json
from wgforge import stirrup_runtime as sr


def config():
    return {"approve_live": True, "endpoint": "https://mock.example.com/v1/chat/completions",
            "model": "mock-model", "api_key_env": "WG_STIRRUP_TEST_KEY", "allowed_hosts": ["mock.example.com"],
            "privacy": {"public_upload_approved": True, "private_upload_approved": True},
            "max_retries": 0, "timeout_seconds": 2,
            "budget": {"max_cost_usd": 10, "input_usd_per_million": 1, "output_usd_per_million": 2,
                       "max_requests": 10, "max_steps": 6, "max_input_tokens": 1000000,
                       "max_output_tokens": 1000, "max_total_tokens": 3000000},
            "stirrup": {"sandbox": {"backend": "docker", "image": "unit-test-image"},
                        "context_tokens": 256000, "compaction_trigger": 0.7}}


def reply(name, arguments, identifier):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class ContractTests(unittest.TestCase):
    def test_no_host_fallback(self):
        cfg = config()
        cfg["stirrup"]["sandbox"]["backend"] = "local"
        with self.assertRaisesRegex(ForgeError, "isolated"):
            sr.validate_stirrup_config(cfg)

    def test_context_settings_match_paper(self):
        cfg = config()
        sr.validate_stirrup_config(cfg)
        cfg["stirrup"]["compaction_trigger"] = 0.8
        with self.assertRaisesRegex(ForgeError, "0.7"):
            sr.validate_stirrup_config(cfg)

    def test_explicit_image_required(self):
        cfg = config()
        cfg["stirrup"]["sandbox"].pop("image")
        with self.assertRaisesRegex(ForgeError, "explicit"):
            sr.validate_stirrup_config(cfg)

    def test_environment_image_can_be_prepared_without_running(self):
        cfg = config()
        cfg["stirrup"]["sandbox"] = {"backend": "docker", "image_env": "WG_STIRRUP_UNSET_IMAGE"}
        with patch.dict(os.environ, {}, clear=True):
            sr.validate_stirrup_config(cfg)
            with self.assertRaisesRegex(ForgeError, "explicit"):
                sr.validate_stirrup_config(cfg, check_dependencies=True)

    def test_finish_path_cannot_escape(self):
        self.assertEqual(str(sr._submitted_relative("/workspace/output/a.csv", "/workspace")), "a.csv")
        for path in ["../a", "/etc/passwd", "/workspace/output/../input/file", "/workspace/output"]:
            with self.subTest(path=path), self.assertRaises(ForgeError):
                sr._submitted_relative(path, "/workspace")


HAS_STIRRUP = importlib.util.find_spec("stirrup") is not None


@unittest.skipUnless(HAS_STIRRUP, "Install optional pinned Stirrup requirements for official-loop tests")
class OfficialHarnessTests(unittest.TestCase):
    def setUp(self):
        from stirrup.tools.code_backends.base import CodeExecToolProvider, CommandResult
        from stirrup.core.models import ImageContentBlock

        class MemoryEnvironment(CodeExecToolProvider):
            """In-memory test double; never invokes a shell or container daemon."""
            def __init__(self):
                super().__init__(shell_timeout=600)
                self.files = {}
                self.commands = []
                self.uid = "1000"
            async def __aenter__(self):
                return self.get_code_exec_tool()
            async def __aexit__(self, *args):
                return None
            async def run_command(self, cmd, *, timeout=None):
                self.commands.append(cmd)
                if cmd == "id -u":
                    return CommandResult(0, self.uid, "")
                if cmd.startswith("python3 -c"):
                    return CommandResult(0, "3.12", "")
                if cmd == "write_task":
                    self.files["/workspace/deliverable/memo.txt"] = b"A source-grounded memo."
                    from PIL import Image
                    buffer = io.BytesIO()
                    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
                    self.files["/workspace/output/preview.png"] = buffer.getvalue()
                if cmd == "write_json":
                    self.files["/workspace/output/result.json"] = b'{"status":"success","file":"data.csv"}'
                    self.files["/workspace/output/data.csv"] = b"amount\n2\n"
                if cmd == "tamper":
                    self.files["/workspace/input/anchors/source.txt"] = b"rewritten"
                return CommandResult(0, "ok", "")
            async def read_file_bytes(self, path):
                if path not in self.files:
                    raise FileNotFoundError(path)
                return self.files[path]
            async def write_file_bytes(self, path, content):
                self.files[path] = content
            async def file_exists(self, path):
                return path in self.files
            async def is_directory(self, path):
                return False
            async def list_files(self, path):
                return [name for name in self.files if name.startswith(path)]
            async def resolve_output_source(self, path):
                if path not in self.files:
                    raise FileNotFoundError(path)
                return path
            async def view_image(self, path):
                return ImageContentBlock(data=self.files[path])

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.public = self.root / "public"
        (self.public / "anchors").mkdir(parents=True)
        (self.public / "task.json").write_text(json.dumps({"task_id": "test", "instructions": "Create memo.txt"}))
        (self.public / "anchors" / "source.txt").write_text("A real input fixture.")
        self.environment = MemoryEnvironment()
        self.cfg = config()
        self.key = patch.dict(os.environ, {"WG_STIRRUP_TEST_KEY": "unit-only-secret"})
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.tmp.cleanup()

    def execute(self, responses, *, generic=False, usage=None, run_name="run", finish_reasons=None):
        from wgforge import runtime
        queue, calls = list(responses), []
        def complete(provider, messages, tools=None, json_mode=False, context_event_id=None):
            calls.append(deepcopy({"messages": messages, "tools": tools}))
            response = queue.pop(0)
            if isinstance(response, BaseException):
                raise response
            index = len(provider.state["requests"])
            tokens = usage[index] if usage else 100
            provider.state["requests"].append({"index": index, "status": "succeeded", "prompt_tokens": tokens,
                "completion_tokens": 20, "charged_tokens": tokens + 20, "charged_usd": .001,
                "reported_cost_usd": .001, "finish_reason": finish_reasons[index] if finish_reasons else "tool_calls" if response.get("tool_calls") else "stop"})
            provider.save()
            return response, index
        with patch.object(sr, "_new_exec_env", return_value=(self.environment, "/workspace")), patch.object(runtime.Provider, "complete", complete):
            if generic:
                result = sr.run_json_task({"instructions": "Write real CSV and result.json"},
                    {"anchors/source.txt": self.public / "anchors" / "source.txt"}, self.root / run_name, self.cfg, "companion")
            else:
                result = sr.run_stirrup_agent(self.public, self.root / run_name, self.cfg)
        return result, calls

    def test_official_loop_submits_real_artifacts_and_exact_contexts(self):
        result, calls = self.execute([
            reply("code_exec", {"cmd": "write_task"}, "a"),
            reply("finish", {"summary": "Created memo", "paths": ["/workspace/deliverable/memo.txt"]}, "b")])
        self.assertTrue(result["completed"])
        self.assertEqual(result["harness"], "official_stirrup")
        self.assertEqual(result["usage"]["requests"], 2)
        self.assertEqual(set(result["artifact_hashes"]), {"memo.txt"})
        self.assertEqual({t["function"]["name"] for t in calls[0]["tools"]}, {"code_exec", "view_image", "finish", "abandon"})
        trajectory = read_json(self.root / "run" / "trajectory.json")
        self.assertEqual(len(trajectory["provider_contexts"]), 2)
        self.assertIn("independent execution agent", json.dumps(calls[0]["messages"]))
        self.assertNotIn("unit-only-secret", (self.root / "run" / "checkpoint.json").read_text())

    def test_same_version_modified_source_cannot_claim_pinned_revision(self):
        installed = Path(importlib.util.find_spec("stirrup").origin).parent
        copied = self.root / "altered_stirrup"
        shutil.copytree(installed, copied, ignore=shutil.ignore_patterns("__pycache__"))
        sr._verify_stirrup_source(copied)
        with (copied / "__init__.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# An unpinned local modification\n")
        with self.assertRaisesRegex(ForgeError, "differs from the pinned"):
            sr._verify_stirrup_source(copied)

    def test_generic_stage_returns_native_file_bytes_and_json(self):
        result, _ = self.execute([
            reply("code_exec", {"cmd": "write_json"}, "a"),
            reply("finish", {"summary": "Created CSV", "paths": ["/workspace/output/result.json", "/workspace/output/data.csv"]}, "b")], generic=True)
        self.assertEqual(result["result"]["status"], "success")
        self.assertEqual((result["artifact_root"] / "data.csv").read_bytes(), b"amount\n2\n")

    def test_compaction_calls_share_the_budget_and_keep_exact_context(self):
        result, calls = self.execute([
            reply("code_exec", {"cmd": "write_task"}, "a"),
            {"role": "assistant", "content": "The memo is complete at /workspace/deliverable/memo.txt."},
            reply("finish", {"summary": "Created memo", "paths": ["/workspace/deliverable/memo.txt"]}, "b")], usage=[180000, 100, 100])
        self.assertEqual(result["usage"]["requests"], 3)
        trace = read_json(self.root / "run" / "trajectory.json")
        self.assertEqual(len(trace["provider_contexts"]), 3)
        self.assertTrue(trace["context_events"])
        self.assertEqual(len(calls), 3)

    def test_native_assistant_replay_preserves_signed_blocks(self):
        response = reply("code_exec", {"cmd": "write_task"}, "a")
        response["provider_content"] = [{"type": "thinking", "thinking": "private test", "signature": "signed-test-block"},
            {"type": "tool_use", "id": "a", "name": "code_exec", "input": {"cmd": "write_task"}}]
        _, calls = self.execute([response,
            reply("finish", {"summary": "Created memo", "paths": ["/workspace/deliverable/memo.txt"]}, "b")])
        replayed = [m for m in calls[1]["messages"] if m["role"] == "assistant"]
        self.assertEqual(replayed[0], response)

    def test_uid_check_happens_before_provider_request(self):
        self.environment.uid = "0"
        with self.assertRaisesRegex(ForgeError, "UID 1000"):
            self.execute([])
        self.assertEqual(read_json(self.root / "run" / "checkpoint.json")["requests"], [])

    def test_zero_request_setup_failure_can_resume_after_environment_is_fixed(self):
        self.environment.uid = "0"
        with self.assertRaisesRegex(ForgeError, "UID 1000"):
            self.execute([])
        saved = read_json(self.root / "run" / "checkpoint.json")
        self.assertFalse(saved["agent_started"])
        self.assertEqual(saved["requests"], [])
        self.environment.uid = "1000"
        result, calls = self.execute([
            reply("code_exec", {"cmd": "write_task"}, "a"),
            reply("finish", {"summary": "Created memo", "paths": ["/workspace/deliverable/memo.txt"]}, "b")])
        self.assertTrue(result["completed"])
        self.assertEqual(len(calls), 2)
        saved = read_json(self.root / "run" / "checkpoint.json")
        self.assertEqual(len(saved["setup_failures"]), 1)
        self.assertEqual(len(saved["requests"]), 2)

    def test_fetch_records_real_download_once_and_exposes_sandbox_path(self):
        from wgforge import sources
        state, saves = {}, []
        downloaded = {"id": "source_one", "url": "https://example.com/data.csv", "path": "raw/verified.csv",
                      "sha256": "c" * 64, "bytes": 4, "mime_type": "text/csv"}
        def download(record, out, config):
            (out / "raw").mkdir(parents=True)
            (out / downloaded["path"]).write_bytes(b"x\n1\n")
            return downloaded
        async def exercise():
            tools = sr._web_tools(self.environment, "/workspace", self.root / "web", {"sources": {}}, state, lambda: saves.append(True))
            fetch = tools[1]
            params = fetch.parameters(url=downloaded["url"])
            first = await fetch.executor(params)
            second = await fetch.executor(params)
            return json.loads(first.content), json.loads(second.content)
        with patch.object(sources, "download_source", side_effect=download) as fetch_mock:
            first, second = asyncio.run(exercise())
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(state["web_downloads"], [downloaded])
        self.assertEqual(first["path"], "/workspace/input/downloads/source_one.csv")
        self.assertTrue(second["cached"])

    def test_search_failure_is_a_durable_uncertain_receipt(self):
        from wgforge import sources
        state = {}
        async def exercise():
            tool = sr._web_tools(self.environment, "/workspace", self.root / "web", {"sources": {}}, state, lambda: None)[0]
            await tool.executor(tool.parameters(query="occupational evidence"))
        with patch.object(sources, "discover_sources", side_effect=sources.SearchError("quota_or_rate_limit", 429)):
            with self.assertRaisesRegex(ForgeError, "uncertain billing"):
                asyncio.run(exercise())
        self.assertEqual(state["search_requests"][0]["status"], "uncertain")
        self.assertEqual(state["search_requests"][0]["failure"], {"code": "quota_or_rate_limit", "http_status": 429})

    def test_network_search_failure_keeps_downloads_and_finishes_without_replaying_search(self):
        from wgforge import sources
        body = b"x\n1\n"
        downloaded = {"id": "source_one", "url": "https://example.com/data.csv", "path": "raw/verified.csv",
                      "sha256": sr.hashlib.sha256(body).hexdigest(), "bytes": len(body), "mime_type": "text/csv"}
        def download(record, out, config):
            (out / "raw").mkdir(parents=True)
            (out / downloaded["path"]).write_bytes(body)
            return downloaded
        self.cfg["stirrup"].update(web_enabled=True, sources={})
        for code in ("network_connection_error", "network_timeout"):
            with self.subTest(code=code), patch.object(sources, "download_source", side_effect=download), \
                 patch.object(sources, "discover_sources", side_effect=sources.SearchError(code)) as search:
                result, calls = self.execute([
                    reply("web_fetch", {"url": downloaded["url"]}, "a"),
                    reply("web_search", {"query": "additional occupational evidence"}, "b"),
                    reply("web_search", {"query": "a different query must also be blocked"}, "c"),
                    reply("code_exec", {"cmd": "write_json"}, "d"),
                    reply("finish", {"summary": "Used existing evidence", "paths": [
                        "/workspace/output/result.json", "/workspace/output/data.csv"]}, "e")],
                    generic=True, run_name=code)
            self.assertEqual(result["result"]["status"], "success")
            self.assertEqual(search.call_count, 1)
            self.assertEqual(len(calls), 5)
            self.assertIn("Further searches are disabled", json.dumps(calls[2]["messages"]))
            state = read_json(self.root / code / "checkpoint.json")
            self.assertTrue(state["completed"])
            self.assertEqual(state["web_downloads"], [downloaded])
            self.assertEqual(len(state["search_requests"]), 1)
            receipt = state["search_requests"][0]
            self.assertEqual(receipt["status"], "uncertain")
            self.assertEqual(receipt["failure"]["code"], code)
            self.assertEqual(receipt["continuation"], "existing_sources_only")

    def test_search_still_stops_without_downloads_or_on_account_failure(self):
        from wgforge import sources
        for code, status, downloads in [("network_connection_error", None, []),
                                        ("network_timeout", None, []),
                                        ("quota_or_rate_limit", 429, [{"id": "existing"}]),
                                        ("authentication_or_access_denied", 401, [{"id": "existing"}])]:
            state = {"web_downloads": downloads}
            async def exercise():
                tool = sr._web_tools(self.environment, "/workspace", self.root / "web", {"sources": {}}, state, lambda: None)[0]
                await tool.executor(tool.parameters(query="occupational evidence"))
            with self.subTest(code=code), patch.object(sources, "discover_sources", side_effect=sources.SearchError(code, status)):
                with self.assertRaisesRegex(ForgeError, "uncertain billing"):
                    asyncio.run(exercise())
            self.assertNotIn("continuation", state["search_requests"][0])

    def test_unhandled_uncertain_search_still_blocks_model_calls(self):
        for status in ("in_flight", "uncertain"):
            client = sr.BudgetedStirrupClient(None, {"search_requests": [{"status": status}]}, lambda: None)
            with self.subTest(status=status), self.assertRaisesRegex(ForgeError, "uncertain completion"):
                asyncio.run(client.generate([], None))

    def test_native_anthropic_http_boundary_preserves_images_and_thinking(self):
        from wgforge import runtime
        cfg = deepcopy(self.cfg)
        cfg.update(provider="anthropic", endpoint="https://mock.example.com/v1/messages")
        responses = []
        for identifier, name, arguments in [("a", "code_exec", {"cmd": "write_task"}),
                ("b", "view_image", {"path": "/workspace/output/preview.png"}),
                ("c", "finish", {"summary": "Created memo", "paths": ["/workspace/deliverable/memo.txt"]})]:
            responses.append({"id": "response_" + identifier, "type": "message", "role": "assistant", "model": "mock-model",
                "content": [{"type": "thinking", "thinking": "test thought", "signature": "sig_" + identifier},
                            {"type": "tool_use", "id": identifier, "name": name, "input": arguments}],
                "stop_reason": "tool_use", "usage": {"input_tokens": 100, "output_tokens": 20}})
        calls = []
        class Response:
            def __init__(self, data):
                self.data = json.dumps(data).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.data[:limit]
        class HTTP:
            def open(self, request, timeout=None):
                calls.append(json.loads(request.data))
                return Response(responses.pop(0))
        with patch.object(sr, "_new_exec_env", return_value=(self.environment, "/workspace")), patch.object(runtime.urllib.request, "build_opener", return_value=HTTP()):
            report = sr.run_stirrup_agent(self.public, self.root / "native", cfg)
        self.assertTrue(report["completed"])
        self.assertEqual(report["usage"]["requests"], 3)
        replay = [m for m in calls[1]["messages"] if m["role"] == "assistant"]
        self.assertEqual(replay[0]["content"][0]["signature"], "sig_a")
        tool_results = [block for message in calls[2]["messages"] for block in message["content"] if block["type"] == "tool_result"]
        self.assertTrue(any(part["type"] == "image" for result in tool_results for part in result["content"]))

    def test_incomplete_session_is_not_replayed(self):
        with self.assertRaises(RuntimeError):
            self.execute([RuntimeError("simulated provider timeout")])
        with self.assertRaisesRegex(ForgeError, "automatic replay is disabled"):
            self.execute([])

    def test_refusal_and_pause_never_execute_attached_tool_calls(self):
        from wgforge.runtime import ProviderError
        for reason in ("refusal", "pause_turn"):
            with self.subTest(reason=reason), self.assertRaisesRegex(ProviderError, reason):
                self.execute([reply("code_exec", {"cmd": "write_task"}, "a")],
                             finish_reasons=[reason], run_name=reason)
            self.assertNotIn("write_task", self.environment.commands)
            state = read_json(self.root / reason / "checkpoint.json")
            self.assertEqual(state["requests"][0]["status"], "succeeded")

    def test_abandon_never_counts_as_completed(self):
        report, _ = self.execute([reply("abandon", {"reason": "Required input unavailable"}, "a")])
        self.assertFalse(report["completed"])
        self.assertTrue(report["abandoned"])
        self.assertEqual(report["terminal_status"], "abandoned")
        self.assertEqual(report["artifact_hashes"], {})
        resumed, calls = self.execute([])
        self.assertEqual(resumed, report)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
