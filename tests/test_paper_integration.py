"""Offline end-to-end paper pipeline with the real pinned Stirrup loop.

Only provider responses, source I/O and the isolated execution environment are
test doubles. No model API, paid search, HTTP download, Docker or E2B is used.
Actual stage dispatch, source verification, native-file transfer, repair,
acceptance, demonstration collection, hashes and resume behavior remain real.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wgforge import paper_campaign, runtime, sources, stirrup_runtime
from wgforge.common import canonical, read_json, sha256
from wgforge.materialize import verify_bundle
from wgforge.paper_acquisition import CHECKS
from wgforge.workflow import validate_run_binding, verify_terminal_workflow

ROOT = Path(__file__).resolve().parents[1]
HAS_STIRRUP = importlib.util.find_spec("stirrup") is not None
SOURCE_URL = "https://example.org/observations.csv"
SOURCE_BYTES = b"event_id,magnitude\none,2\ntwo,4\n"
SOURCE_HASH = hashlib.sha256(SOURCE_BYTES).hexdigest()
COMPANION_BYTES = b"Internal weekly review\nAudience: the regional team lead. Use the supplied catalog only.\n"
PRIVATE_SENTINEL = "PRIVATE_AUTHOR_FACTS_MUST_NOT_REACH_SOLVER"


def tool_reply(name, arguments, number):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_%d" % number, "type": "function", "function": {
            "name": name, "arguments": canonical(arguments)}}]}


@unittest.skipUnless(HAS_STIRRUP, "Requires optional pinned Stirrup dependencies; all external services are mocked")
class PaperIntegrationTests(unittest.TestCase):
    def setUp(self):
        from stirrup.tools.code_backends.base import CodeExecToolProvider, CommandResult
        from stirrup.core.models import ImageContentBlock

        class MemorySandbox(CodeExecToolProvider):
            def __init__(self):
                super().__init__(shell_timeout=600)
                self.files = {}
                self.commands = []

            async def __aenter__(self):
                return self.get_code_exec_tool()

            async def __aexit__(self, *args):
                return None

            async def run_command(self, cmd, *, timeout=None):
                self.commands.append(cmd)
                if cmd == "id -u":
                    return CommandResult(0, "1000", "")
                if cmd.startswith("python3 -c"):
                    return CommandResult(0, "3.12", "")
                if cmd.startswith("fixture_write:"):
                    for path, value in json.loads(cmd.split(":", 1)[1]).items():
                        self.files[path] = value.encode("utf-8")
                return CommandResult(0, "Offline fixture command completed.", "")

            async def read_file_bytes(self, path):
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

        self.sandbox_type = MemorySandbox
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "campaign"
        self.config = read_json(ROOT / "configs/claude_opus55_paper.json")
        self.config["approve_live"] = True
        self.config["planning"]["count"] = 1
        self.config["synthesis"]["rubric_size_target"] = 20
        self.config["evidence_char_limit"] = 500000
        self.config["audit_char_limit"] = 1000000
        for cfg in self.config["roles"].values():
            cfg["endpoint"] = "https://mock.example.com/v1/messages"
            cfg["allowed_hosts"] = ["mock.example.com"]
            cfg["api_key_env"] = "WG_INTEGRATION_KEY"
            cfg["stirrup"]["sandbox"] = {"backend": "docker", "image": "unit-test-only-image"}
        self.plan = {"plan_id": "integration-plan", "profession": "Geoscientists",
                     "instruction": "Prepare a grounded occupational briefing task."}
        self.calls, self.stages, self.sandboxes, self.source_records = [], [], [], []
        self.stage_payloads = []
        self.key = patch.dict(os.environ, {"WG_INTEGRATION_KEY": "offline-test-secret"})
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.temp.cleanup()

    def sandbox(self, settings):
        env = self.sandbox_type()
        self.sandboxes.append(env)
        return env, "/workspace"

    def download(self, candidate, out, config):
        out = Path(out)
        path = "raw/" + SOURCE_HASH + ".csv"
        local = out / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(SOURCE_BYTES)
        record = {**candidate, "path": path, "sha256": SOURCE_HASH, "bytes": len(SOURCE_BYTES),
                  "retrieved_at": "2026-10-15T00:00:00+00:00", "mime_type": "text/csv",
                  "final_url": SOURCE_URL, "retrieval_method": "offline_fixture"}
        self.source_records.append(record)
        return record

    def result_for(self, role, payload):
        """Deterministic model outputs; validators and file transfers are real."""
        if role == "researcher":
            return {"status": "success", "anchors": [{"download_url": SOURCE_URL, "sha256": SOURCE_HASH,
                    "content_summary": "Two observations with event identifiers and numeric magnitudes.",
                    "key_fields": ["event_id", "magnitude"],
                    "suitability": {name: {"pass": True, "reason": "Verified against the actual fixture CSV."}
                                    for name in CHECKS}}], "search_log": ["occupational observation catalog"]}
        if role == "companion_author":
            row = payload["sources"][0]
            return {"source_assessment": [{"source_id": row["record"]["id"], "usable": True,
                    "occupation_fit": "The real catalog supports geoscientists interpreting bounded earthquake observations.",
                    "professional_operations": ["Reconcile the event count and maximum observed magnitude"],
                    "evidence_excerpt": row["extracted_text"][:200], "limitations": "A two-event bounded snapshot."}],
                    "scenario_context": {"organization": "Regional Analysis Desk", "worker_role": "Geoscientist",
                    "period": "This week's review", "business_situation": "The lead needs a bounded catalog briefing.",
                    "intended_work_product": "An internal briefing memo"}, "gap_analysis": [], "known_limitations": [],
                    "companions": [{"id": "review_policy", "path": "policy.md", "format": "md",
                    "artifact_path": "companions/policy.md", "sha256": hashlib.sha256(COMPANION_BYTES).hexdigest(),
                    "purpose": "Identify the briefing audience and permitted scope.", "derived_from": [row["path"]],
                    "assumptions": ["An internal weekly review"], "external_facts_used": [],
                    "provenance_note": "The observed catalog is the sole quantitative evidence.",
                    "verified": {"opens_and_parses": True, "internally_consistent": True, "cross_file_consistent": True}}]}
        if role == "task_author":
            anchor = next(name for name in payload["input_paths"] if name.startswith("anchors/"))
            rubric = [{"id": "c%02d" % (number + 1), "weight": 2, "required": number < 5,
                       "tags": ["source-grounded"], "criterion": "Bounded briefing requirement %d" % (number + 1),
                       "verification": "structured", "evidence": [anchor]} for number in range(20)]
            rubric[0]["criterion"] = "State the maximum magnitude is 3."
            rubric[-1].update(weight=-2, criterion="Do not fabricate observations.", required=False)
            return {"request": {"title": "Prepare the internal observation briefing", "role": "Geoscientist",
                    "context": "The team lead needs a short briefing before the weekly review.",
                    "instructions": "Review the attached catalog and policy; write briefing.md with the count and maximum magnitude.",
                    "deliverables": [{"path": "briefing.md", "format": "md", "description": "A concise internal briefing"}]},
                    "rubric": {"items": rubric}, "task_manifest": {"research_required": False},
                    "fact_sheet": {"private_notes": PRIVATE_SENTINEL}}
        if role == "judge":
            self.assertNotIn("private_reference", payload)
            return {"scores": [{"criterion_id": item["id"],
                    "score": 0 if item["weight"] < 0 or item["id"] == "c02" or (payload["round_index"] == 0 and item["id"] == "c01") else item["weight"],
                    "rationale": "Offline independent assessment against the submitted memo and source.",
                    "evidence": ["submitted_files/briefing.md", "The observed maximum is 4."]}
                    for item in payload["rubric"]["items"]]}
        if role == "attributor":
            return {"attribution": [{"criterion_id": cid, "labels": ["Rubric" if cid == "c01" else "Agent"],
                    "explanation": "The rubric's maximum conflicts with the input." if cid == "c01" else "The agent omitted a required explanation.",
                    "evidence": ["The source and submitted memo both identify maximum 4."]}
                    for cid in payload["unsatisfied_criterion_ids"]]}
        if role == "repairer":
            revised = deepcopy(payload["scenario"])
            revised["revision"] += 1
            revised["rubric"]["items"][0]["criterion"] = "State the maximum observed magnitude is 4."
            return {"scenario": revised, "changes": [{"criterion_id": "c01",
                    "reason": "Correct the factual maximum using the immutable catalog.",
                    "changed_paths": ["rubric.items[0].criterion"]}], "criterion_mapping": {}}
        raise AssertionError("Unexpected role: " + role)

    def complete(self, provider, messages, tools=None, json_mode=False, context_event_id=None):
        role = provider.state["role"]
        index = len(provider.state["requests"])
        content = next(message["content"] for message in messages if message["role"] == "user")
        payload = json.loads(content if isinstance(content, str) else "".join(part.get("text", "") for part in content))
        if index == 0:
            self.stages.append(role)
            self.stage_payloads.append((role, deepcopy(payload)))
            self.sandboxes[-1].role = role
            # All inputs have been uploaded before the first model call.
            for name, remote in payload["input_paths"].items():
                self.assertIn(remote, self.sandboxes[-1].files)
            if role in {"solver", "teacher"}:
                self.assertNotIn(PRIVATE_SENTINEL, canonical(messages))
                self.assertNotIn("rubric", payload)
                self.assertNotIn("private", " ".join(payload["input_paths"]))
                self.assertIn("companions/policy.md", payload["input_paths"])
            if role == "task_author":
                self.assertEqual(self.sandboxes[-1].files[payload["input_paths"]["companions/policy.md"]], COMPANION_BYTES)
            if role in {"judge", "attributor", "repairer"}:
                self.assertIn("submitted_files/briefing.md", payload["input_paths"])
                self.assertTrue(any(name.startswith("reference_files/anchors/") for name in payload["input_paths"]))
        self.calls.append((role, index))
        if role == "researcher" and index < 2:
            response = tool_reply("web_search", {"query": "geoscientist earthquake observations CSV"}, index) if index == 0 else tool_reply(
                "web_fetch", {"url": SOURCE_URL, "source_page_url": "https://example.org/catalog", "title": "Observation catalog"}, index)
        else:
            writing = index == (2 if role == "researcher" else 0)
            if role in {"solver", "teacher"}:
                files = {"/workspace/deliverable/briefing.md": "# Internal briefing\nThere are two observations; the maximum magnitude is 4.\n"}
            else:
                files = {"/workspace/output/result.json": canonical(self.result_for(role, payload))}
                if role == "companion_author":
                    files["/workspace/output/companions/policy.md"] = COMPANION_BYTES.decode()
            response = tool_reply("code_exec", {"cmd": "fixture_write:" + canonical(files)}, index) if writing else tool_reply(
                "finish", {"summary": "Completed the offline fixture stage.", "paths": list(files)}, index)
        provider.state["requests"].append({"index": index, "status": "succeeded", "prompt_tokens": 100,
            "completion_tokens": 20, "charged_tokens": 120, "charged_usd": 0.001,
            "reported_cost_usd": 0.001, "finish_reason": "tool_calls"})
        provider.save()
        return response, index

    def test_real_stage_chain_repairs_only_rubric_then_collects_teacher_and_resumes(self):
        candidate = sources._candidate(SOURCE_URL, "Observation catalog")
        # The corpus scheduler itself is separately tested; use one explicit
        # occupational fixture so this test targets the complete stage junctions.
        with patch.object(paper_campaign, "plan_tasks", return_value=[self.plan]), \
             patch.object(stirrup_runtime, "_new_exec_env", side_effect=self.sandbox), \
             patch.object(runtime.Provider, "complete", autospec=True, side_effect=self.complete), \
             patch.object(sources, "discover_sources", return_value=[candidate]) as search, \
             patch.object(sources, "download_source", side_effect=self.download) as download, \
             patch("socket.create_connection", side_effect=AssertionError("Network access forbidden in integration test")):
            state = paper_campaign.run_paper_campaign(self.config, self.out)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["counts"]["accepted_tasks"], 1)
            self.assertEqual(state["counts"]["completed_trajectories"], 1)
            expected = ["researcher", "companion_author", "task_author", "solver", "judge", "attributor",
                        "repairer", "solver", "judge", "attributor", "teacher"]
            self.assertEqual(self.stages, expected)
            self.assertEqual(len(self.calls), 24)
            self.assertEqual(state["usage"]["requests"], 24)
            search.assert_called_once()
            download.assert_called_once()
            calls_before_resume = deepcopy(self.calls)
            resumed = paper_campaign.run_paper_campaign(self.config, self.out)
            self.assertEqual(resumed, state)
            self.assertEqual(self.calls, calls_before_resume)
            self.assertEqual(len(self.sandboxes), len(expected))
        record = state["attempts"][0]
        evolution = (self.out / record["workflow"]).parent
        verified = verify_terminal_workflow(evolution)
        accepted = evolution / verified["accepted_scenario"]
        document = verify_bundle(accepted)
        self.assertEqual(document["revision"], 1)
        self.assertEqual(document["rubric"]["items"][0]["criterion"], "State the maximum observed magnitude is 4.")
        self.assertEqual(document["rubric"]["items"][1]["criterion"], "Bounded briefing requirement 2")
        self.assertEqual(sha256(accepted / "public" / "companions" / "policy.md"), hashlib.sha256(COMPANION_BYTES).hexdigest())
        anchor_name = "anchors/" + self.source_records[0]["id"] + ".csv"
        self.assertEqual(sha256(accepted / "public" / anchor_name), SOURCE_HASH)
        audit = read_json(evolution / verified["rounds"][-1]["audit"])
        self.assertTrue(audit["scenario_accepted"])
        self.assertFalse(audit["executor_fully_satisfied"])
        self.assertEqual(audit["defects"], [])
        self.assertEqual(audit["attribution"][0]["labels"], ["Agent"])
        self.assertFalse(audit["human_reviewed"])
        demo = self.out / "tasks" / record["scenario_id"] / "demonstration"
        run = validate_run_binding(accepted, demo)
        self.assertTrue(run["completed"])
        self.assertEqual(run["role"], "teacher")
        self.assertEqual(read_json(self.out / "dataset" / "manifest.json")["count"], 1)
        # Every stage got a fresh isolated sandbox, and acceptance was independently
        # re-executed before the separate teacher trajectory was collected.
        solver_payloads = [payload for role, payload in self.stage_payloads if role == "solver"]
        self.assertEqual([payload["revision"] for payload in solver_payloads], [0, 1])


if __name__ == "__main__":
    unittest.main()
