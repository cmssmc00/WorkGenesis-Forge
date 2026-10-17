"""Retry directories share prior request budgets; no provider calls are made."""
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from wgforge import cli, workflow as w
from wgforge.common import ForgeError, write_json


class RetryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.campaign = self.root / "campaign"
        self.out = self.campaign / "retry"
        self.scenario = self.root / "scenario"
        write_json(self.scenario / "manifest.json", {"bundle_digest": "test-bundle"})
        self.cfg = {"approve_live": True, "roles": {"solver": {"budget": {
            "max_cost_usd": 5, "max_requests": 16, "max_total_tokens": 400000}}},
            "campaign_budget": {"max_cost_usd": 5, "max_requests": 3, "max_tokens": 5000},
            "repair": {"max_rounds": 1, "repeated_defect_limit": 3}}
        self.previous = self.campaign / "previous" / "checkpoint.json"
        write_json(self.previous, {"requests": [
            {"status": "succeeded", "charged_usd": 2, "charged_tokens": 1500},
            {"status": "uncertain", "charged_usd": 2, "charged_tokens": 2000}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_passes_shared_budget_root(self):
        with patch.object(cli, "_config", return_value=self.cfg), patch.object(w, "evolve_scenario", return_value={}) as evolve, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["evolve", "--scenario", str(self.scenario), "--config", "unused.json",
                                       "--out", str(self.out), "--campaign-root", str(self.campaign), "--approve-live"]), 0)
        evolve.assert_called_once_with(str(self.scenario), str(self.out), self.cfg, campaign_root=str(self.campaign))

    def test_retry_budget_and_final_usage_include_old_success_and_uncertainty(self):
        before = self.previous.read_bytes()
        def solver(config, role, scenario, stage, campaign):
            with patch.object(w._runtime(), "validate_config"):
                effective = w.role_config(config, role, stage, campaign)
            self.assertEqual(effective["budget"], {"max_cost_usd": 1, "max_requests": 1, "max_total_tokens": 1500})
            write_json(stage / "checkpoint.json", {"requests": [
                {"status": "succeeded", "charged_usd": 0.25, "charged_tokens": 1000}]})
        with patch.object(w, "verify_bundle", return_value={}), patch.object(w, "solver_role", side_effect=solver), patch.object(w, "audit_run", return_value={"defects": [], "attribution": []}):
            result = w.evolve_scenario(self.scenario, self.out, self.cfg, campaign_root=self.campaign)
        self.assertEqual(result["usage"], {"requests": 3, "charged_upper_bound_usd": 4.25,
                                          "charged_tokens": 4500, "uncertain_requests": 1})
        self.assertEqual(result["campaign_budget_root"], str(self.campaign))
        self.assertEqual(self.previous.read_bytes(), before)
        with patch.object(w._runtime(), "validate_config"):
            with self.assertRaisesRegex(ForgeError, "Campaign budget exhausted"):
                w.role_config(self.cfg, "solver", self.campaign / "another-retry", self.campaign)

    def test_outside_budget_root_is_rejected_before_creating_output(self):
        outside = self.root / "outside"
        for candidate in (outside, self.campaign / ".." / "outside"):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ForgeError, "inside campaign_root"):
                w.evolve_scenario(self.scenario, candidate, self.cfg, campaign_root=self.campaign)
        self.assertFalse(outside.exists())

    def test_resume_cannot_drop_shared_budget_root(self):
        with patch.object(w, "verify_bundle", return_value={}), patch.object(w, "solver_role", side_effect=ForgeError("interrupted")):
            with self.assertRaisesRegex(ForgeError, "interrupted"):
                w.evolve_scenario(self.scenario, self.out, self.cfg, campaign_root=self.campaign)
            with self.assertRaisesRegex(ForgeError, "different campaign budget root"):
                w.evolve_scenario(self.scenario, self.out, self.cfg)


if __name__ == "__main__":
    unittest.main()
