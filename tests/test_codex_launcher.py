"""Launcher regression: resolve a portable tag or explicit local image ID.

The executable boundary is stubbed. No Docker, model, or search is invoked.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PINNED = 'sha256:' + 'a' * 64


class CodexLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-launcher-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('scripts', '.venv/bin', 'bin', 'configs'):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/run_codex_subscription.sh', self.root / 'scripts/run_codex_subscription.sh')
        shutil.copyfile(ROOT / 'scripts/run_codex_parallel.sh', self.root / 'scripts/run_codex_parallel.sh')
        stub = '''import json, os, sys
from pathlib import Path
a = sys.argv[1:]
p = Path(os.environ['LAUNCHER_TEST_ROOT'])
if a[:2] == ['-m', 'wgforge.parallel']:
    if a[2] == 'count': print('3')
    else: (p / 'parallel.json').write_text(json.dumps({'args': a, 'image': os.environ['WGFORGE_PAPER_IMAGE']}))
elif a[:2] == ['-m', 'wgforge']:
    if a[2] == 'preflight':
        print(json.dumps({'ready_for_operator_live_check': os.environ.get('TEST_READY', '1') == '1'}))
    elif a[2] == 'campaign':
        (p / 'campaign.json').write_text(json.dumps({'args': a, 'image': os.environ['WGFORGE_PAPER_IMAGE']}))
    else: sys.exit(90)
elif a and a[0] == '-':
    # Simulate a locally built image and explicit immutable IDs.
    (p / 'image-reference.txt').write_text(a[1])
    if (a[1] == 'workgenesis-paper:local' or a[1].startswith('sha256:')) and os.environ.get('TEST_IMAGE_MISSING') != '1':
        print('sha256:' + 'a' * 64 if a[1] == 'workgenesis-paper:local' else a[1])
    else:
        print('No such image', file=sys.stderr)
        sys.exit(2)
elif a[:1] == ['-c'] and 'docker.from_env' in a[1]:
    sys.exit(1 if os.environ.get('TEST_DOCKER_DOWN') == '1' and not (p / 'opened').exists() else 0)
elif a and a[0].endswith('/scripts/check_serpapi.py'):
    (p / 'account-checked').touch()
    sys.exit(2 if os.environ.get('TEST_SERPAPI_BLOCKED') == '1' else 0)
else:
    os.execv(sys.executable, [sys.executable] + a)
'''
        (self.root / '.venv/bin/python').write_text('#!' + sys.executable + '\n' + stub)
        for command, body in {
            'codex': 'exit 0',
            'docker': 'echo "Docker CLI must not be used" >&2; exit 97',
            'open': 'touch "$LAUNCHER_TEST_ROOT/opened"',
            'sleep': 'exit 0',
            'uname': 'echo "${TEST_OS:-Darwin}"',
        }.items():
            (self.root / 'bin' / command).write_text('#!/bin/bash\n' + body + '\n')
        for path in [self.root / '.venv/bin/python', *(self.root / 'bin').iterdir()]:
            path.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                    'LAUNCHER_TEST_ROOT': str(self.root), 'SERPAPI_API_KEY': 'offline-placeholder',
                    'HTTPS_PROXY': 'http://127.0.0.1:9'}
        self.env.pop('WGFORGE_PAPER_IMAGE', None)
        self.env.pop('WGFORGE_PARALLEL_MODE', None)
        self.env.pop('WGFORGE_PARALLEL_RESUME', None)

    def run_launcher(self, *args, **changes):
        result = subprocess.run(['bash', str(self.root / 'scripts/run_codex_subscription.sh'), *args],
                                env={**self.env, **changes}, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
        self.assertNotIn('Docker CLI must not be used', result.stdout)
        return result

    def test_default_tag_resolves_to_immutable_id(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout)
        campaign = json.loads((self.root / 'campaign.json').read_text())
        self.assertEqual(campaign['image'], PINNED)
        self.assertEqual((self.root / 'image-reference.txt').read_text(), 'workgenesis-paper:local')
        self.assertEqual(campaign['args'][campaign['args'].index('--count') + 1], '1')

    def test_explicit_image_id_is_preserved(self):
        image_id = 'sha256:' + 'b' * 64
        result = self.run_launcher(WGFORGE_PAPER_IMAGE=image_id)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads((self.root / 'campaign.json').read_text())['image'], image_id)

    def test_explicit_missing_image_never_substituted(self):
        result = self.run_launcher(WGFORGE_PAPER_IMAGE='intentionally-missing:local')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'campaign.json').exists())
        self.assertEqual((self.root / 'image-reference.txt').read_text(), 'intentionally-missing:local')

    def test_missing_pinned_image_stops_before_key_or_campaign(self):
        result = self.run_launcher(TEST_IMAGE_MISSING='1', SERPAPI_API_KEY='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Local image lookup failed', result.stdout)
        self.assertNotIn('Export SERPAPI_API_KEY', result.stdout)
        self.assertFalse((self.root / 'runs').exists())

    def test_missing_key_stops_after_successful_image_check(self):
        result = self.run_launcher(SERPAPI_API_KEY='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Verified local Docker image', result.stdout)
        self.assertIn('Export SERPAPI_API_KEY', result.stdout)
        self.assertFalse((self.root / 'runs').exists())

    def test_preflight_not_ready_creates_no_run(self):
        result = self.run_launcher(TEST_READY='0')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'runs').exists())

    def test_serpapi_account_failure_blocks_campaign(self):
        result = self.run_launcher(TEST_SERPAPI_BLOCKED='1')
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.root / 'account-checked').exists())
        self.assertFalse((self.root / 'campaign.json').exists())
        self.assertFalse((self.root / 'runs').exists())

    def test_docker_restart_uses_sdk_readiness(self):
        result = self.run_launcher(TEST_DOCKER_DOWN='1')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.root / 'opened').exists())

    def test_linux_missing_daemon_stops_without_opening_desktop(self):
        result = self.run_launcher(TEST_DOCKER_DOWN='1', TEST_OS='Linux')
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn('Start your Docker daemon', result.stdout)
        self.assertFalse((self.root / 'opened').exists())
        self.assertFalse((self.root / 'campaign.json').exists())

    def test_custom_count_and_fresh_output(self):
        for _ in range(2):
            result = self.run_launcher('100')
            self.assertEqual(result.returncode, 0, result.stdout)
            campaign = json.loads((self.root / 'campaign.json').read_text())
            self.assertEqual(campaign['args'][campaign['args'].index('--count') + 1], '100')
        self.assertEqual(len(list((self.root / 'runs').iterdir())), 2)

    def test_invalid_count_checks_nothing_else(self):
        self.assertEqual(self.run_launcher('0').returncode, 2)
        self.assertFalse((self.root / 'image-reference.txt').exists())
        self.assertFalse((self.root / 'runs').exists())

    def test_parallel_wrapper_defaults_to_200_total_tasks(self):
        result = subprocess.run(['bash', str(self.root / 'scripts/run_codex_parallel.sh')],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads((self.root / 'parallel.json').read_text())
        self.assertEqual(record['args'][2], 'new')
        self.assertEqual(record['args'][record['args'].index('--count') + 1], '200')
        self.assertEqual(record['image'], PINNED)
        self.assertFalse((self.root / 'campaign.json').exists())

    def test_parallel_resume_uses_existing_directory(self):
        batch = self.root / 'saved-batch'
        batch.mkdir()
        result = subprocess.run(['bash', str(self.root / 'scripts/run_codex_parallel.sh'), '--resume', str(batch)],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = json.loads((self.root / 'parallel.json').read_text())
        self.assertEqual(record['args'][2], 'resume')
        self.assertEqual(Path(record['args'][record['args'].index('--out') + 1]).resolve(), batch.resolve())
        self.assertEqual(list((self.root / 'runs').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
