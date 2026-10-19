"""Serial Claude launcher: no real Docker, search, or model calls."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tests import test_codex_launcher as fixtures
from wgforge.paper_profile import validate_paper_profile


class ClaudeLauncherTests(unittest.TestCase):
    def setUp(self):
        fixtures.CodexLauncherTests.setUp(self)
        shutil.copyfile(fixtures.ROOT / 'scripts/run_claude_subscription.sh',
                        self.root / 'scripts/run_claude_subscription.sh')
        shutil.copyfile(fixtures.ROOT / 'configs/claude_opus55_subscription_serial.json',
                        self.root / 'configs/claude_opus55_subscription_serial.json')
        cli = self.root / 'bin/claude'
        cli.write_text('#!/bin/bash\nexit 0\n'); cli.chmod(0o755)

    def run_launcher(self, *args, **changes):
        return subprocess.run(['bash', str(self.root / 'scripts/run_claude_subscription.sh'), *args],
                              env={**self.env, **changes}, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)

    def test_default_is_200_serial_author_only_even_with_parallel_environment(self):
        result = self.run_launcher(WGFORGE_PARALLEL_MODE='1', WGFORGE_PARALLEL_RESUME='/ignored')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads((self.root / 'campaign.json').read_text())
        args = record['args']; cfg = json.loads(Path(args[args.index('--config') + 1]).read_text())
        self.assertEqual(args[args.index('--count') + 1], '200')
        self.assertTrue(Path(args[args.index('--out') + 1]).name.startswith('opus55-xhigh-author-only-serial-200-'))
        self.assertEqual(cfg['campaign_mode'], 'author_only')
        self.assertFalse(cfg['trajectory_collection']['enabled'])
        self.assertFalse((self.root / 'parallel.json').exists())
        validate_paper_profile(cfg)
        for role in cfg['roles'].values():
            self.assertEqual((role['provider'], role['model'], role['effort']),
                             ('claude_subscription', 'claude-opus-5-5', 'xhigh'))

    def test_resume_uses_saved_configuration_and_creates_no_second_run(self):
        first = self.run_launcher('3')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        args = json.loads((self.root / 'campaign.json').read_text())['args']
        run = Path(args[args.index('--out') + 1]); (run / 'campaign.json').write_text('{}')
        saved = (run / 'config.json').read_bytes()
        result = self.run_launcher('--resume', str(run))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        resumed = json.loads((self.root / 'campaign.json').read_text())['args']
        self.assertEqual(resumed[resumed.index('--out') + 1], str(run))
        self.assertEqual(resumed[resumed.index('--count') + 1], '3')
        self.assertEqual((run / 'config.json').read_bytes(), saved)
        self.assertEqual(len(list((self.root / 'runs').iterdir())), 1)

    def test_missing_key_does_not_start(self):
        result = self.run_launcher(SERPAPI_API_KEY='')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'campaign.json').exists())

    def test_missing_image_does_not_start(self):
        result = self.run_launcher(TEST_IMAGE_MISSING='1')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'campaign.json').exists())

    def test_preflight_failure_does_not_start(self):
        result = self.run_launcher(TEST_READY='0')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'campaign.json').exists())

    def test_resume_rejects_other_provider(self):
        run = self.root / 'old-run'; run.mkdir()
        (run / 'campaign.json').write_text('{}')
        (run / 'config.json').write_text(json.dumps({'campaign_mode': 'author_only',
            'roles': {'researcher': {'provider': 'codex_subscription'}}, 'planning': {'count': 200}}))
        result = self.run_launcher('--resume', str(run))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'campaign.json').exists())
