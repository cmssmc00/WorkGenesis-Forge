"""Offline partition, process isolation and durable resume checks; no providers."""
from copy import deepcopy
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from wgforge import parallel
from wgforge.common import ForgeError, digest, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "batch"
        self.config = read_json(ROOT / "configs/gpt6_astra_subscription_one.json")

    def prepare(self, count=3):
        # Same offline validation as production, with a syntactically valid
        # image ID; Docker is not contacted by prepare_batch.
        with patch.dict(os.environ, {"WGFORGE_PAPER_IMAGE": "sha256:" + "a" * 64}):
            return parallel.prepare_batch(self.config, self.out, count)

    def test_one_global_balanced_schedule_is_partitioned_without_duplicates(self):
        packet = self.prepare(200)
        lanes = [row["schedule"]["plans"] for row in packet["workers"]]
        self.assertEqual([len(lane) for lane in lanes], [67, 67, 66])
        ids = [plan["plan_id"] for lane in lanes for plan in lane]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({lane[0]["profession"] for lane in lanes}), 3)
        self.assertEqual([p["slot_index"] for p in lanes[0]], list(range(0, 200, 3)))
        self.assertEqual(packet["config"]["campaign_mode"], "author_only")
        self.assertFalse(packet["config"]["trajectory_collection"]["enabled"])
        self.assertEqual(packet["config"]["roles"], self.config["roles"])
        self.assertEqual(parallel.load_batch(self.out), packet)

    def test_existing_batch_cannot_be_overwritten_and_config_change_rejected(self):
        self.prepare()
        saved = (self.out / "batch_plan.json").read_bytes()
        with self.assertRaisesRegex(ForgeError, "not empty"):
            self.prepare()
        self.assertEqual((self.out / "batch_plan.json").read_bytes(), saved)
        config = read_json(self.out / "config.json")
        config["planning"]["count"] = 99
        write_json(self.out / "config.json", config)
        with self.assertRaisesRegex(ForgeError, "differs"):
            parallel.load_batch(self.out)

    def test_worker_uses_same_config_and_own_schedule_and_shared_registry(self):
        packet = self.prepare(6)
        calls = []
        def campaign(config, out, **kwargs):
            calls.append((deepcopy(config), out, kwargs))
            return {"status": "completed"}
        with patch("wgforge.paper_campaign.run_paper_campaign", side_effect=campaign), patch.dict(os.environ):
            for lane in packet["workers"]:
                self.assertEqual(parallel.run_worker(self.out, lane["name"]), 0)
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertEqual(calls[1][0], calls[2][0])
        self.assertEqual(len({str(row[1]) for row in calls}), 3)
        for call, lane in zip(calls, packet["workers"]):
            self.assertEqual(call[2]["schedule"], lane["schedule"])
            self.assertEqual(call[2]["registry_path"], self.out.resolve() / "lineage_registry.json")

    def test_author_only_resume_refuses_an_old_full_pipeline_batch(self):
        packet = self.prepare()
        packet["config"].pop("campaign_mode")
        packet["digest"] = digest({k: v for k, v in packet.items() if k != "digest"})
        write_json(self.out / "batch_plan.json", packet)
        write_json(self.out / "config.json", packet["config"])
        with patch.object(parallel, "run_batch", side_effect=AssertionError("Full pipeline forbidden")) as run:
            self.assertEqual(parallel.main(["resume", "--out", str(self.out), "--author-only", "--approve-live"]), 2)
            run.assert_not_called()

    def test_real_processes_overlap_and_one_failure_leaves_other_results(self):
        self.prepare()
        real_popen = subprocess.Popen
        child = '''
from pathlib import Path
import sys, time
from wgforge.common import write_json
root, name = Path(sys.argv[1]), sys.argv[2]
(root / (name + '.started')).touch()
deadline = time.monotonic() + 8
while len(list(root.glob('*.started'))) < 3:
    if time.monotonic() > deadline: sys.exit(8)
    time.sleep(0.02)
if name == 'worker-2': sys.exit(2)
write_json(root / name / 'campaign.json', {'status': 'completed', 'counts': {
    'authored_tasks': 1, 'accepted_tasks': 0, 'completed_trajectories': 0}, 'finished_plan_ids': [name]})
'''
        def fake_worker(args, **kwargs):
            name = args[args.index("--name") + 1]
            return real_popen([sys.executable, "-c", child, str(self.out), name], **kwargs)
        with patch.object(parallel.subprocess, "Popen", side_effect=fake_worker):
            result = parallel.run_batch(self.out)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["finished_plans"], 2)
        self.assertEqual(result["authored_tasks"], 2)
        self.assertEqual(result["accepted_tasks"], 0)
        self.assertEqual([w["exit_code"] for w in result["workers"]], [0, 2, 0])
        self.assertEqual(parallel.refresh_status(self.out)["workers"][1]["exit_code"], 2)
        self.assertTrue((self.out / "worker-1/campaign.json").exists())
        self.assertTrue((self.out / "worker-3/campaign.json").exists())

    def test_concurrent_registry_writes_preserve_entries_and_reject_duplicates(self):
        self.out.mkdir()
        code = '''
from pathlib import Path
import sys
from wgforge.campaign import register_anchors
root, lane = Path(sys.argv[1]), sys.argv[2]
for i in range(12):
    sid = lane + '-' + str(i)
    register_anchors(root / 'registry.json', 'shared', sid, [{'id': sid, 'url': 'https://example.com/' + sid,
        'sha256': sid, 'rights_status': 'pending'}])
'''
        children = [subprocess.Popen([sys.executable, "-c", code, str(self.out), str(i)]) for i in range(3)]
        self.assertEqual([p.wait(timeout=20) for p in children], [0, 0, 0])
        entries = read_json(self.out / "registry.json")["entries"]
        self.assertEqual(len(entries), 36)
        from wgforge.campaign import AnchorConflict, register_anchors
        with self.assertRaises(AnchorConflict):
            register_anchors(self.out / "registry.json", "shared", "duplicate",
                             [{"id": "new", "url": "https://example.com/0-0", "sha256": "new", "rights_status": "pending"}])
        self.assertEqual(len(read_json(self.out / "registry.json")["entries"]), 36)


if __name__ == "__main__":
    unittest.main()
