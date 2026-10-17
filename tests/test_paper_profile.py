"""Offline profile checks must catch live transport errors before a campaign."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from wgforge.common import ForgeError
from wgforge.paper_profile import PAPER_ROLES, preflight, validate_paper_profile, with_task_count
from wgforge.runtime import validate_config
from wgforge.sandbox import RuntimeErrorBase
from wgforge.stirrup_runtime import STIRRUP_VERSION


ROOT = Path(__file__).resolve().parents[1]


class PaperProfileTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "configs/claude_opus55_paper.json").read_text())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch("urllib.request.build_opener", side_effect=AssertionError("No transport during offline checks")))
        self.stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("No containers during offline checks")))

    def setup_ready_environment(self):
        self.stack.enter_context(patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "mock-key-not-a-credential",
            "SERPAPI_API_KEY": "mock-search-key-not-a-credential",
            "WGFORGE_PAPER_IMAGE": "mock-paper-image:offline-only"}))
        self.stack.enter_context(patch("wgforge.paper_profile.sys.version_info", (3, 12, 0)))
        self.stack.enter_context(patch("wgforge.paper_profile.importlib.metadata.version", return_value=STIRRUP_VERSION))
        self.stack.enter_context(patch("wgforge.paper_profile.importlib.util.find_spec", return_value=object()))
        self.stack.enter_context(patch("wgforge.planning.plan_tasks", return_value=[{"profession": "Example"}] * 100))

    def test_checked_in_profile_validates_without_credentials_or_live_approval(self):
        self.assertIs(validate_paper_profile(self.config), self.config)

    def test_offline_runtime_never_reads_credential_values(self):
        role = self.config["roles"]["judge"]
        with patch("wgforge.runtime.os.environ.get", side_effect=AssertionError("Credential value read")):
            self.assertIsNone(validate_config(role, private=True, offline=True))

    def test_live_runtime_still_requires_approval_and_key(self):
        role = self.config["roles"]["solver"]
        with self.assertRaisesRegex(RuntimeErrorBase, "approve-live"):
            validate_config(role)
        role["approve_live"] = True
        with self.assertRaisesRegex(RuntimeErrorBase, "key environment variable"):
            validate_config(role)

    def test_all_roles_validate_native_endpoint_including_teacher(self):
        for name in PAPER_ROLES:
            broken = copy.deepcopy(self.config)
            broken["roles"][name]["endpoint"] = "https://api.anthropic.com/v1/chat/completions"
            with self.subTest(role=name), self.assertRaisesRegex(ForgeError, name + ".*messages"):
                validate_paper_profile(broken)

    def test_offline_does_not_skip_privacy_allowed_host_or_env_name(self):
        for update in [{"privacy": {"public_upload_approved": False}},
                       {"privacy": {"public_upload_approved": True, "private_upload_approved": False}},
                       {"allowed_hosts": ["different.example.com"]},
                       {"api_key_env": "bad environment name"}]:
            broken = copy.deepcopy(self.config)
            broken["roles"]["judge"].update(update)
            with self.subTest(update=update), self.assertRaises(ForgeError):
                validate_paper_profile(broken)

    def test_each_required_native_budget_field_is_checked(self):
        for name in ("max_cost_usd", "input_usd_per_million", "output_usd_per_million", "max_requests",
                     "max_steps", "max_input_tokens", "max_output_tokens", "max_total_tokens"):
            broken = copy.deepcopy(self.config)
            broken["roles"]["teacher"]["budget"].pop(name)
            with self.subTest(field=name), self.assertRaisesRegex(ForgeError, "teacher.*" + name):
                validate_paper_profile(broken)

    def test_negative_cache_cost_and_fractional_token_limits_fail(self):
        for field, value in [("cache_read_usd_per_million", -1), ("max_output_tokens", 1.5), ("max_requests", True)]:
            broken = copy.deepcopy(self.config)
            broken["roles"]["solver"]["budget"][field] = value
            with self.subTest(field=field), self.assertRaises(ForgeError):
                validate_paper_profile(broken)

    def test_campaign_request_and_token_limits_are_integers(self):
        for field, value in [("max_requests", 2.5), ("max_tokens", 3.5), ("max_cost_usd", float("nan"))]:
            broken = copy.deepcopy(self.config)
            broken["campaign_budget"][field] = value
            with self.subTest(field=field), self.assertRaises(ForgeError):
                validate_paper_profile(broken)

    def test_optional_roles_cannot_silently_select_another_model(self):
        self.config["roles"]["extra_role"] = copy.deepcopy(self.config["roles"]["solver"])
        self.config["roles"]["extra_role"]["model"] = "a-different-model"
        with self.assertRaisesRegex(ForgeError, "Opus 5.5"):
            validate_paper_profile(self.config)

    def test_native_unsupported_thinking_compaction_and_bad_types_fail_cleanly(self):
        for update in [{"thinking": {"type": "disabled"}}, {"context_compaction": {"enabled": True}},
                       {"effort": []}, {"anthropic_version": 123}, {"prompt_cache": {"ttl": []}}]:
            broken = copy.deepcopy(self.config)
            broken["roles"]["solver"].update(update)
            with self.subTest(update=update), self.assertRaises(ForgeError):
                validate_paper_profile(broken)

    def test_preflight_reports_missing_setup_once_for_shared_roles(self):
        self.setup_ready_environment()
        with patch.dict(os.environ, {}, clear=True):
            result = preflight(self.config)
        pending = result["runtime_setup_pending"]
        self.assertEqual(len(pending), len(set(pending)))
        self.assertEqual(sum("ANTHROPIC_API_KEY" in item for item in pending), 1)
        self.assertEqual(sum("SERPAPI_API_KEY" in item for item in pending), 1)
        self.assertEqual(sum("WGFORGE_PAPER_IMAGE" in item for item in pending), 1)
        self.assertFalse(result["ready_for_operator_live_check"])
        self.assertEqual((result["paid_calls_made"], result["tasks_generated"], result["containers_started"]), (0, 0, 0))

    def test_preflight_checks_nonresearcher_search_and_sandbox_requirements(self):
        self.setup_ready_environment()
        self.config["roles"]["companion_author"]["stirrup"]["sources"]["serpapi_key_env"] = "COMPANION_SEARCH_KEY"
        self.config["roles"]["teacher"]["stirrup"]["sandbox"] = {"backend": "e2b", "template_env": "TEACHER_E2B_TEMPLATE"}
        with patch("wgforge.paper_profile.importlib.util.find_spec", side_effect=lambda name: None if name == "e2b_code_interpreter" else object()):
            result = preflight(self.config)
        pending = result["runtime_setup_pending"]
        for value in ("COMPANION_SEARCH_KEY", "TEACHER_E2B_TEMPLATE", "e2b_code_interpreter", "E2B_API_KEY"):
            self.assertTrue(any(value in item for item in pending), (value, pending))

    def test_preflight_does_not_report_placeholder_or_bad_credentials_as_ready(self):
        self.setup_ready_environment()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "bad\ncredential", "WGFORGE_PAPER_IMAGE": "REPLACE_ME"}):
            result = preflight(self.config)
        self.assertFalse(result["ready_for_operator_live_check"])
        self.assertNotIn("bad\ncredential", json.dumps(result))
        self.assertTrue(any("WGFORGE_PAPER_IMAGE" in item for item in result["runtime_setup_pending"]))

    def test_environment_field_names_must_be_valid(self):
        for field in ("image_env", "serpapi_key_env"):
            broken = copy.deepcopy(self.config)
            settings = broken["roles"]["companion_author"]["stirrup"]
            container = settings["sandbox"] if field == "image_env" else settings["sources"]
            container[field] = "bad env name"
            with self.subTest(field=field), self.assertRaisesRegex(ForgeError, "environment variable"):
                validate_paper_profile(broken)

    def test_preflight_ready_means_only_local_presence_not_live_validation(self):
        self.setup_ready_environment()
        result = preflight(self.config)
        self.assertTrue(result["ready_for_operator_live_check"])
        self.assertEqual(result["runtime_setup_pending"], [])
        self.assertIn("real runs are not tested", result["runtime_validation"])
        self.assertEqual(result["requested_accepted_tasks"], 100)

    def test_count_override_does_not_mutate_profile(self):
        changed = with_task_count(self.config, 20000)
        self.assertEqual(changed["planning"]["count"], 20000)
        self.assertEqual(self.config["planning"]["count"], 100)


if __name__ == "__main__":
    unittest.main()
