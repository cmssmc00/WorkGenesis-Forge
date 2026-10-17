"""Untrusted code is never executed on the host. Docker tests are explicitly opt-in."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wgforge import sandbox as sb


IMAGE = "example.invalid/wgforge-python@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.broker = sb.Sandbox(self.root / "workspace")
    def tearDown(self):
        self.tmp.cleanup()
    def code_broker(self, limits=None):
        return sb.Sandbox(self.root / "code", limits, {"enable_code": True, "image": IMAGE})
    def fake_transport(self, files=None, cleanup=0, remaining=None):
        result = {"exit_code": 0, "log": "executed inside mocked Docker", "files": files or {}}
        records = []
        def fake(argv, data, timeout, limit, env):
            records.append((argv, data, timeout, limit, env))
            if argv[1:3] == ["image", "inspect"]:
                return 0, IMAGE_ID.encode(), b""
            if argv[1] == "run":
                return 0, json.dumps(result).encode(), b""
            if argv[1] == "rm":
                return cleanup, b"", b""
            if argv[1:3] == ["container", "ls"]:
                return remaining if remaining is not None else (0, b"", b"")
            raise AssertionError("Unexpected Docker call")
        return fake, records

    def test_default_broker_has_no_execution(self):
        with self.assertRaisesRegex(sb.RuntimeErrorBase, "Unknown tool"):
            self.broker.execute("execute_python", {"code": "print('never run')"})
        with self.assertRaisesRegex(sb.RuntimeErrorBase, "disabled"):
            sb.execute_python(self.broker, "print('never run')")

    def test_code_requires_digest_pinned_image(self):
        for image in ("python:latest", "python:3.12", "sha256:" + "a" * 64, "repo@sha256:short", "repo@sha256:" + "G" * 64):
            with self.subTest(image=image), self.assertRaisesRegex(sb.RuntimeErrorBase, "digest"):
                sb.validate_code_config({"enable_code": True, "image": image})
        for key, value in (("cpus", float("nan")), ("pids_limit", 10000), ("memory_mb", True), ("timeout_seconds", 301)):
            with self.subTest(key=key), self.assertRaises(sb.RuntimeErrorBase):
                sb.validate_code_config({"enable_code": True, "image": IMAGE, key: value})

    def test_docker_command_has_real_isolation(self):
        broker = self.code_broker()
        command = sb.docker_command("/usr/bin/docker", broker, broker.code_config, "wgforge-test")
        self.assertIn("--pull=never", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--security-opt") + 1], "no-new-privileges:true")
        self.assertEqual(command[command.index("--user") + 1], "65532:65532")
        for flag in ("--memory", "--memory-swap", "--cpus", "--pids-limit", "--ulimit"):
            self.assertIn(flag, command)
        mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0].endswith(",dst=/input,readonly"))
        self.assertNotIn("docker.sock", " ".join(command))
        writable = [command[i + 1] for i, value in enumerate(command) if value == "--tmpfs"]
        self.assertTrue(any(v.startswith("/output:rw,") and "size=" in v for v in writable))
        self.assertTrue(any(v.startswith("/tmp:rw,") and "size=" in v for v in writable))
        self.assertNotIn("--privileged", command)
        self.assertEqual(command[command.index("--entrypoint") + 1], "python3")

    def test_missing_docker_never_falls_back_to_host_python(self):
        with patch.object(sb.shutil, "which", return_value=None), patch.object(sb.subprocess, "Popen") as launched:
            with self.assertRaisesRegex(sb.RuntimeErrorBase, "no host fallback"):
                sb.execute_python(self.code_broker(), "print('never run')")
            launched.assert_not_called()

    def test_no_pull_or_install_when_image_missing(self):
        with patch.object(sb.shutil, "which", return_value="/usr/bin/docker"), patch.object(sb, "_run_bounded", return_value=(1, b"", b"private daemon diagnostics")) as call:
            with self.assertRaisesRegex(sb.RuntimeErrorBase, "never pulls"):
                sb.execute_python(self.code_broker(), "print('never run')")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0][1:3], ["image", "inspect"])

    def test_mocked_container_import_is_bounded_and_hashed(self):
        broker = self.code_broker()
        fake, calls = self.fake_transport({"report.txt": base64.b64encode(b"result").decode()})
        with patch.object(sb.shutil, "which", return_value="/usr/bin/docker"), patch.object(sb, "_run_bounded", side_effect=fake):
            result = sb.execute_python(broker, "print('only sent to container')")
        self.assertEqual((broker.root / "output" / "report.txt").read_text(), "result")
        self.assertEqual(result["artifact_hashes"], sb.tree_hashes(broker.root / "output"))
        self.assertEqual(result["sandbox"], "docker")
        self.assertEqual(result["image_id"], IMAGE_ID)
        self.assertEqual(calls[1][4], {"PATH": os.defpath, "HOME": "/nonexistent", "DOCKER_HOST": "unix:///var/run/docker.sock"})
        self.assertEqual(json.loads(calls[1][1])["code"], "print('only sent to container')")

    def test_container_output_traversal_and_quota_rejected_before_import(self):
        for filename, content in (("../outside.txt", b"x"), ("safe.txt", b"too large")):
            broker = self.code_broker({"max_file_bytes": 3})
            fake, _ = self.fake_transport({filename: base64.b64encode(content).decode()})
            with patch.object(sb.shutil, "which", return_value="/usr/bin/docker"), patch.object(sb, "_run_bounded", side_effect=fake):
                with self.assertRaises(sb.RuntimeErrorBase):
                    sb.execute_python(broker, "pass")
            self.assertEqual(sb.tree_hashes(broker.root / "output"), {})

    def test_cleanup_failure_is_fatal(self):
        for remaining in ((1, b"", b"daemon failure"), (0, b"wgforge-container-still-present\n", b"")):
            fake, _ = self.fake_transport(cleanup=1, remaining=remaining)
            with patch.object(sb.shutil, "which", return_value="/usr/bin/docker"), patch.object(sb, "_run_bounded", side_effect=fake):
                with self.assertRaisesRegex(sb.SandboxFatalError, "cleanup could not be verified"):
                    sb.execute_python(self.code_broker(), "pass")

    def test_cleanup_already_absent_verified(self):
        fake, _ = self.fake_transport(cleanup=1, remaining=(0, b"", b""))
        with patch.object(sb.shutil, "which", return_value="/usr/bin/docker"), patch.object(sb, "_run_bounded", side_effect=fake):
            self.assertEqual(sb.execute_python(self.code_broker(), "pass")["exit_code"], 0)

    def test_symlinks_hardlinks_and_special_files_blocked(self):
        target = self.root / "secret.txt"
        target.write_text("PRIVATE")
        path = self.broker.root / "output" / "link.txt"
        path.symlink_to(target)
        with self.assertRaises(sb.RuntimeErrorBase):
            self.broker.execute("read_file", {"path": "output/link.txt"})
        path.unlink()
        os.link(target, path)
        with self.assertRaisesRegex(sb.RuntimeErrorBase, "Hard-linked"):
            self.broker.execute("read_file", {"path": "output/link.txt"})
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(sb.RuntimeErrorBase, "Special"):
            self.broker.validate_outputs()

    def test_csv_formula_injection_rejected_but_numbers_allowed(self):
        for cell in ("=HYPERLINK(1)", "+SUM(A1)", "@A1", "-cmd", "\t=evil"):
            with self.subTest(cell=cell), self.assertRaisesRegex(sb.RuntimeErrorBase, "formula-like"):
                self.broker.execute("write_file", {"path": "output/data.csv", "content": "name,value\na," + cell})
        self.broker.execute("write_file", {"path": "output/data.csv", "content": "name,value\na,-42\nb,+1.2\nc,'=literal"})
        self.assertIn("-42", (self.broker.root / "output" / "data.csv").read_text())

    def test_structured_writers_create_real_artifacts(self):
        spec = {"title": "Evidence", "text": "A concise document", "tables": [{"name": "Data", "headers": ["a", "b"], "rows": [[1, 2]]}],
                "slides": [{"title": "Summary", "text": "Verified content"}]}
        for tool, suffix in (("write_document", "docx"), ("write_workbook", "xlsx"), ("write_slides", "pptx"), ("write_pdf", "pdf")):
            with self.subTest(tool=tool):
                result = self.broker.execute(tool, {"path": "output/evidence." + suffix, "spec": spec})
                self.assertGreater(result["bytes"], 100)
                self.assertTrue((self.broker.root / result["path"]).is_file())

    def test_invalid_nested_artifact_input_preserves_previous_workbook(self):
        from copy import deepcopy
        good = {"tables": [{"name": "Data", "headers": ["Field", "Value"], "rows": [["A", 12]]}]}
        path = "output/analysis.xlsx"
        self.broker.execute("write_workbook", {"path": path, "spec": good})
        original = (self.broker.root / path).read_bytes()
        invalid = [
            ({"headers": "Field,Value"}, "spec.tables[0].headers"),
            ({"headers": ["Field", 123]}, "spec.tables[0].headers[1]"),
            ({"rows": "A,12"}, "spec.tables[0].rows"),
            ({"rows": ["A,12"]}, "spec.tables[0].rows[0]"),
            ({"rows": [["A"]]}, "must have 2 cells"),
            ({"rows": [["A", {"unsupported": 12}]]}, "spec.tables[0].rows[0][1]"),
        ]
        for change, detail in invalid:
            with self.subTest(change=change):
                spec = deepcopy(good)
                spec["tables"][0].update(change)
                with self.assertRaises(sb.RuntimeErrorBase) as failed:
                    self.broker.execute("write_workbook", {"path": path, "spec": spec})
                self.assertIn(detail, str(failed.exception))
                self.assertEqual((self.broker.root / path).read_bytes(), original)

    def test_title_only_workbook_does_not_create_empty_success(self):
        with self.assertRaisesRegex(sb.RuntimeErrorBase, "requires tables"):
            self.broker.execute("write_workbook", {"path": "output/empty.xlsx", "spec": {"title": "Test"}})
        self.assertFalse((self.broker.root / "output/empty.xlsx").exists())
        self.broker.execute("write_workbook", {"path": "output/notes.xlsx", "spec": {"text": "A useful source note"}})
        self.assertIn("A useful source note", self.broker.execute("read_file", {"path": "output/notes.xlsx"})["text"])

    def test_structured_schema_errors_do_not_echo_unknown_keys_or_values(self):
        for spec in ({"title": "Test", "PRIVATE_CANARY": "SECRET_VALUE"},
                     {"slides": [{"title": {"PRIVATE_CANARY": "SECRET_VALUE"}, "text": "Body"}]}):
            with self.subTest(spec=spec), self.assertRaises(sb.RuntimeErrorBase) as failed:
                self.broker.execute("write_slides", {"path": "output/slides.pptx", "spec": spec})
            self.assertNotIn("PRIVATE_CANARY", str(failed.exception))
            self.assertNotIn("SECRET_VALUE", str(failed.exception))


@unittest.skipUnless(os.environ.get("WGFORGE_DOCKER_TEST_IMAGE"), "Opt-in integration requires a preloaded digest-pinned Docker image")
class RealDockerIntegrationTests(unittest.TestCase):
    def test_network_input_and_host_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            broker = sb.Sandbox(Path(temporary), code_config={"enable_code": True, "image": os.environ["WGFORGE_DOCKER_TEST_IMAGE"]})
            (broker.root / "input" / "source.txt").write_text("immutable public input")
            code = """
import json, os, pathlib, socket
checks = {'uid': os.getuid(), 'host_secret_absent': not pathlib.Path('/root/.aws/credentials').exists()}
try:
    pathlib.Path('/input/source.txt').write_text('tamper')
    checks['input_readonly'] = False
except OSError:
    checks['input_readonly'] = True
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
    checks['network_blocked'] = False
except OSError:
    checks['network_blocked'] = True
pathlib.Path('/output/checks.json').write_text(json.dumps(checks))
"""
            receipt = sb.execute_python(broker, code)
            self.assertEqual(receipt["exit_code"], 0)
            checks = json.loads((broker.root / "output" / "checks.json").read_text())
            self.assertEqual(checks, {"uid": 65532, "host_secret_absent": True, "input_readonly": True, "network_blocked": True})
            self.assertEqual((broker.root / "input" / "source.txt").read_text(), "immutable public input")
