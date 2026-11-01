"""Cross-process checks for the process-free native-probe plan gate."""

from __future__ import annotations

import contextlib
import copy
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cae_source_git_native_probe as probe


WORKSPACE = Path(__file__).resolve().parents[2]
BACKEND = WORKSPACE / "backend"
HARNESS = WORKSPACE / "backend/tests/cae_source_git_native_probe.py"
CHILD_CODE = (
    "import runpy,sys; backend,script=sys.argv[1:3]; sys.path.insert(0,backend); "
    "sys.argv=[script,*sys.argv[3:]]; runpy.run_path(script,run_name='__main__')"
)
MAX_CAPTURE_BYTES = 64 * 1024
CHILD_TIMEOUT_SECONDS = 15


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class NativeProbePlanTests(unittest.TestCase):
    def test_index_change_plan_and_execute_callsite_use_add_with_raw_blob_oid(self):
        placeholder = "<local-computed-blob-oid>"
        expected_tail = ["update-index", "--add", "--cacheinfo", "100644",
                         placeholder, "probe-shadow.py"]
        planned = next(command["argv"] for command in probe._commands(
            "fixture", "template", ("a" * 40, "b" * 40), placeholder, "marker")
                       if command["label"] == "change-index-to-known-blob")
        self.assertEqual(planned[-len(expected_tail):], expected_tail)
        self.assertEqual(planned, probe._index_change_argv(planned[:-len(expected_tail)], placeholder))

        prefix = ["git.exe", "--git-dir=fixture/.git"]
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        matches = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "call_git"
                   and node.args and isinstance(node.args[0], ast.Constant)
                   and node.args[0].value == "index-change"]
        self.assertEqual(len(matches), 1)
        seen = []
        fake_globals = {"call_git": lambda *args: seen.append(args),
                        "prefix": tuple(prefix), "blob_oid": "a" * 40,
                        "_index_change_argv": probe._index_change_argv}
        eval(compile(ast.Expression(matches[0]), str(HARNESS), "eval"), fake_globals)
        self.assertEqual(seen, [("index-change", tuple(prefix + [
            "update-index", "--add", "--cacheinfo", "100644", "a" * 40, "probe-shadow.py"]))])
        self.assertEqual(probe._tracked_blob_oid("sha1"), hashlib.sha1(
            b"blob 19\0VALUE = 'original'\n").hexdigest())
        self.assertEqual(probe._tracked_blob_oid("sha256"), hashlib.sha256(
            b"blob 19\0VALUE = 'original'\n").hexdigest())

    def test_diagnostic_stderr_tee_is_bounded_transparent_and_redacts_known_paths(self):
        payload = ("fixture=" + str(WORKSPACE / ".local/verification/fixtures/run") + "\n").encode()
        payload += b"x" * (probe.DIAGNOSTIC_STDERR_CAP_BYTES + 17)

        class FakePipe:
            def __init__(self, data):
                self.data = data
                self.offset = 0
                self.closed = False

            def read(self, size=-1):
                if self.offset >= len(self.data):
                    return b""
                end = len(self.data) if size < 0 else min(len(self.data), self.offset + size)
                chunk = self.data[self.offset:end]
                self.offset = end
                return chunk

            def close(self):
                self.closed = True

        inner = FakePipe(payload)
        tee = probe._DiagnosticStderrTee(inner)
        forwarded = bytearray()
        while chunk := tee.read(4096):
            forwarded.extend(chunk)
        tee.close()
        metadata = probe._diagnostic_stderr_metadata(tee, WORKSPACE / ".local/verification/fixtures/run",
                                                     WORKSPACE / ".local/verification/templates/run")
        self.assertEqual(bytes(forwarded), payload)
        self.assertTrue(inner.closed)
        self.assertTrue(tee.closed)
        self.assertTrue(tee.overflow)
        self.assertEqual(metadata["stderrBytesCaptured"], probe.DIAGNOSTIC_STDERR_CAP_BYTES)
        self.assertEqual(metadata["stderrBytesObserved"], len(payload))
        self.assertEqual(metadata["stderrSha256"], hashlib.sha256(payload[:probe.DIAGNOSTIC_STDERR_CAP_BYTES]).hexdigest())
        self.assertNotIn(str(WORKSPACE), metadata["stderr"])

    def test_positive_canary_argv_is_wired_to_execute_callsite(self):
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        matches = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "call_git"
                   and node.args and isinstance(node.args[0], ast.Constant)
                   and node.args[0].value == "canary-positive-control"]
        self.assertEqual(len(matches), 1)
        call = matches[0]
        self.assertTrue(any(keyword.arg == "require_root" and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True for keyword in call.keywords))
        config_appends = [node for node in ast.walk(tree)
                          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and node.func.attr == "write" and node.lineno > call.lineno]
        self.assertTrue(config_appends, "negative configuration must be appended after the positive canary")
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "call_python_canary"
                             for node in ast.walk(tree)))

        seen = []
        fake_globals = {
            "call_git": lambda *args, **kwargs: seen.append((args, kwargs)),
            "_positive_canary_argv": probe._positive_canary_argv,
            "prefix": ["git.exe", "--git-dir=fixture/.git"],
            "sys": type("FakeSys", (), {"executable": "python.exe"})(),
            "script": Path("fixture") / ".cae-probe-canary.py",
        }
        eval(compile(ast.Expression(call), str(HARNESS), "eval"), fake_globals)
        self.assertEqual(len(seen), 1)
        args, kwargs = seen[0]
        self.assertEqual(args[0], "canary-positive-control")
        self.assertEqual(args[1], tuple(probe._positive_canary_argv(
            fake_globals["prefix"], "python.exe", str(fake_globals["script"]))))
        self.assertTrue(kwargs["require_root"])

    def test_shared_canary_writer_has_exclusive_known_mode_behavior_without_files(self):
        events = []

        class FakePath:
            def __init__(self, value):
                self.value = value
                self.parent = self

            def __truediv__(self, name):
                return FakePath(self.value + "/" + name)

        class FakeOS:
            O_CREAT, O_EXCL, O_WRONLY = 1, 2, 4

            @staticmethod
            def open(path, flags, mode):
                events.append((str(path), flags, mode))
                return 7

            @staticmethod
            def close(fd):
                events.append(("close", fd))

        fake_pathlib = type("FakePathlib", (), {"Path": Path})
        fake_sys = type("FakeSys", (), {"argv": []})()
        source = probe._canary_writer_source()
        for mode, expected_name, expected_exit in (
            ("--positive", ".cae-probe-positive", 0),
            ("--negative", ".cae-probe-negative", 73),
        ):
            fake_sys.argv = ["canary.py", mode, "ignored-tail"]
            with patch.dict(sys.modules, {"os": FakeOS, "pathlib": fake_pathlib, "sys": fake_sys}):
                with self.assertRaises(SystemExit) as raised:
                    exec(compile(source, "canary.py", "exec"), {"__file__": "fixture/canary.py"})
            self.assertEqual(raised.exception.code, expected_exit)
            expected_path = str(Path("fixture") / expected_name)
            self.assertEqual(events[-2:], [(expected_path, 7, 0o600), ("close", 7)])
        before = len(events)
        fake_sys.argv = ["canary.py", "--unknown"]
        with patch.dict(sys.modules, {"os": FakeOS, "pathlib": fake_pathlib, "sys": fake_sys}):
            with self.assertRaises(SystemExit) as raised:
                exec(compile(source, "canary.py", "exec"), {"__file__": "fixture/canary.py"})
        self.assertEqual(raised.exception.code, 74)
        self.assertEqual(len(events), before)
        self.assertTrue(all(entry[1] == 7 and entry[2] == 0o600 for entry in events[::2]))

    def test_git_config_value_encoder_matches_git_quoted_escape_vector(self):
        desired = r'"X:\Program Files\tool\tool.exe" --flag "two words"'
        expected = r'"\"X:\\Program Files\\tool\\tool.exe\" --flag \"two words\""'
        self.assertEqual(probe._quote_git_config_value(desired), expected)
        unicode_value = 'café\\tool'
        encoded_unicode = probe._quote_git_config_value(unicode_value)
        self.assertIn("café", encoded_unicode)
        self.assertNotIn("\\u", encoded_unicode)
        for invalid in ("bad\nline", "bad\rline", "bad\x00value", "bad\x7fvalue"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaisesRegex(ValueError, "GIT_CONFIG_VALUE_INVALID"):
                    probe._quote_git_config_value(invalid)

    def test_git_shell_command_roundtrips_spaced_quoted_unicode_paths(self):
        source = (r"C:\Program Files\Prøbe\python.exe",
                  'C:\\source repo\\canary "quoted".py', "--negative")
        command = probe._git_shell_command(source)
        expected = tuple(arg.replace("\\", "/") for arg in source)
        self.assertEqual(tuple(shlex.split(command, posix=True)), expected)
        self.assertIn("Prøbe", command)

    def _anchor(self, base: Path) -> Path:
        anchor = base / "verification"
        anchor.mkdir()
        return anchor

    def test_owned_containers_create_on_first_run_and_accept_existing_directories(self):
        with tempfile.TemporaryDirectory(prefix="cae-plan-paths-") as temp:
            anchor = self._anchor(Path(temp))
            run_id = "1" * 32
            with patch.object(probe.subprocess, "Popen", side_effect=AssertionError("no child allowed")):
                fixture, template, identity = probe._prepare_owned_run_paths(anchor, run_id)
                self.assertTrue((anchor / "fixtures").is_dir())
                self.assertTrue((anchor / "templates").is_dir())
                self.assertFalse(fixture.exists())
                self.assertFalse(template.exists())
                fixture2, template2, identity2 = probe._prepare_owned_run_paths(anchor, run_id)
            self.assertEqual((fixture2, template2), (fixture, template))
            self.assertEqual(identity2, identity)

    def test_owned_container_rejects_nondirectory_reparse_and_preexisting_runpaths(self):
        with tempfile.TemporaryDirectory(prefix="cae-plan-paths-") as temp:
            anchor = self._anchor(Path(temp))
            (anchor / "fixtures").write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FIXTURE_CONTAINER_INVALID"):
                probe._prepare_owned_run_paths(anchor, "2" * 32)

        with tempfile.TemporaryDirectory(prefix="cae-plan-paths-") as temp:
            anchor = self._anchor(Path(temp))
            (anchor / "fixtures").mkdir()
            original = probe._reject_reparse_ancestors

            def refuse_fixture_link(path: Path) -> None:
                if path == anchor / "fixtures":
                    raise ValueError("LINKED_PATH_REJECTED")
                original(path)

            with patch.object(probe, "_reject_reparse_ancestors", side_effect=refuse_fixture_link):
                with self.assertRaisesRegex(ValueError, "LINKED_PATH_REJECTED"):
                    probe._prepare_owned_run_paths(anchor, "3" * 32)

        for runpath_kind in ("fixtures", "templates"):
            with self.subTest(runpath_kind=runpath_kind), tempfile.TemporaryDirectory(prefix="cae-plan-paths-") as temp:
                anchor = self._anchor(Path(temp))
                (anchor / "fixtures").mkdir()
                (anchor / "templates").mkdir()
                run_id = "4" * 32
                parent = anchor / runpath_kind
                (parent / ("cae-git-probe-" + run_id if runpath_kind == "fixtures" else run_id)).mkdir()
                with self.assertRaisesRegex(ValueError, "RUNPATH_ALREADY_EXISTS"):
                    probe._prepare_owned_run_paths(anchor, run_id)

    def _minimal_env(self, *, git_dir: bool, pythonpath: bool, guard: bool = True) -> dict[str, str]:
        env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if os.environ.get(name)}
        if git_dir:
            env["GIT_DIR"] = r"C:\must-not-be-inherited"
        if pythonpath:
            env["PYTHONPATH"] = r"C:\audit-name-only"
        if guard:
            env["CAE_NATIVE_PROBE_TEST_GUARD"] = "1"
        return env

    def _child(self, args: list[str], *, git_dir: bool, pythonpath: bool,
               guard: bool = True) -> subprocess.CompletedProcess[bytes]:
        argv = (sys.executable, "-B", "-c", CHILD_CODE, str(BACKEND), str(HARNESS), *args)
        result = subprocess.run(
            argv, cwd=WORKSPACE, env=self._minimal_env(git_dir=git_dir, pythonpath=pythonpath, guard=guard),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, timeout=CHILD_TIMEOUT_SECONDS, check=False,
        )
        self.assertLessEqual(len(result.stdout), MAX_CAPTURE_BYTES)
        self.assertLessEqual(len(result.stderr), MAX_CAPTURE_BYTES)
        return result

    def test_cross_process_plan_validation_ignores_audit_name_presence(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(probe.main(["--plan"]), 0)
        generated = json.loads(output.getvalue())
        plan_path = Path(generated["path"])
        plan_bytes = plan_path.read_bytes()
        plan = json.loads(plan_bytes.decode("utf-8"))
        plan_sha = generated["sha256"]
        self.assertNotIn("ambientSensitiveVariableNamesObserved", plan)
        self.assertEqual(hashlib.sha256(plan_bytes).hexdigest(), plan_sha)

        # The same frozen explicit child environment validates in independent app processes.
        for git_dir in (False, True):
            for pythonpath in (False, True):
                with self.subTest(GIT_DIR=git_dir, PYTHONPATH=pythonpath):
                    result = self._child([
                        "--validate-plan", "--plan-file", str(plan_path),
                        "--expected-plan-sha256", plan_sha,
                    ], git_dir=git_dir, pythonpath=pythonpath)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                    self.assertIn(b"PLAN_VALIDATED_NO_CHILDREN", result.stdout)

        # The test guard must block a correct-plan execute before fixture creation or Git launch.
        guard = self._child([
            "--execute", "--plan-file", str(plan_path), "--expected-plan-sha256", plan_sha,
            "--gandalf-authorized",
        ], git_dir=True, pythonpath=True)
        self.assertEqual(guard.returncode, 2)
        self.assertIn(b"NATIVE_EXECUTION_DISABLED_BY_TEST_GUARD", guard.stderr)
        fixture = WORKSPACE / ".local/verification/fixtures" / ("cae-git-probe-" + plan["runId"])
        self.assertFalse(fixture.exists())

    def test_recomputed_hash_does_not_authorize_tampered_plan_fields(self):
        base = probe.build_plan(run_id="a" * 32, created_utc="2026-11-01T00:00:00Z")
        self.assertEqual(base["schema"], "cae-source-git-native-probe-plan-v2")
        self.assertIn("launch", base["pythonExecutable"])
        self.assertIn("sha256", base["pythonExecutable"]["launch"])
        self.assertIn("imagePath", base["pythonExecutable"]["launch"])
        if base["pythonExecutable"]["base"] is not None:
            self.assertIn("sha256", base["pythonExecutable"]["base"])
        self.assertIn("external launcher must hash", base["pythonPinRule"])
        mutations = []

        candidate = copy.deepcopy(base)
        del candidate["pythonExecutable"]
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["pythonExecutable"]["launch"]["sha256"] = "0" * 64
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["pythonExecutable"]["launch"]["path"] = "C:/other/python.exe"
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["environment"]["values"]["GIT_ALLOW_PROTOCOL"] = "https"
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["commands"][1]["argv"][0] = "C:/other/git.exe"
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["canary"]["commandTemplate"][0] = "C:/other/python.exe"
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["gitExecutable"]["sha256"] = "0" * 64
        mutations.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["sourceHashes"]["runner"] = "0" * 64
        mutations.append(candidate)

        for value in mutations:
            payload = _canonical(value)
            digest = hashlib.sha256(payload).hexdigest()
            with self.subTest(field=value):
                with self.assertRaises(ValueError):
                    probe._validate_plan_payload(payload, digest)

        canonical = _canonical(base)
        noncanonical = canonical + b"\n"
        with self.assertRaises(ValueError):
            probe._validate_plan_payload(noncanonical, hashlib.sha256(noncanonical).hexdigest())

    def test_changed_synthetic_interpreter_image_fails_before_dispatch(self):
        with tempfile.TemporaryDirectory(prefix="cae-python-pin-") as temp:
            image = Path(temp) / "python-image.exe"
            image.write_bytes(b"interpreter-image-before")
            expected = probe._python_identity(str(image), None)
            image.write_bytes(b"interpreter-image-after")
            dispatched = []
            with patch.object(probe.sys, "executable", str(image)):
                with self.assertRaisesRegex(ValueError, "PYTHON_PIN_CHANGED"):
                    probe._launch_checked_python(expected, lambda *a, **k: dispatched.append((a, k)))
            self.assertEqual(dispatched, [])

    def test_process_factory_checks_interpreter_pin_before_native_popen(self):
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        factories = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                     and node.name == "process_factory"]
        self.assertEqual(len(factories), 1)
        calls = [node for node in ast.walk(factories[0]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "_launch_checked_python"]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertTrue(any(isinstance(arg, ast.Attribute) and arg.attr == "Popen"
                            for arg in ast.walk(call)))


if __name__ == "__main__":
    unittest.main()
