from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.services import cae_source_git as git_runner
from app.services.cae_source_files import identify_source_root
from app.services.cae_source_provenance import ApprovedSourceFileV1
from app.storage.cae_source_approvals import RootIdentity


def index_record(path, *, mode="100644", oid="a" * 40):
    return f"H {mode} {oid} 0\t{path}".encode("utf-8") + b"\0"


@dataclass
class ScriptedProcess:
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    return_code: int | None = 0
    terminate_exits: bool = True
    wait_uncertain: bool = False

    def __post_init__(self):
        self.stdout = io.BytesIO(self.stdout_bytes)
        self.stderr = io.BytesIO(self.stderr_bytes)
        self.terminated = False
        self.wait_calls = 0

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        if self.terminate_exits:
            self.return_code = -15

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_uncertain or self.return_code is None:
            raise subprocess.TimeoutExpired("fake-git", timeout)
        return self.return_code


class ScriptedFactory:
    def __init__(self, processes):
        self.processes = list(processes)
        self.calls = []
        self.started = []

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        if not self.processes:
            raise AssertionError("unexpected process launch")
        process = self.processes.pop(0)
        self.started.append(process)
        return process


class CaeSourceGitRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-git-fake-")
        self.root = Path(self.temp.name) / "approved-root"
        self.root.mkdir()
        self.exe = Path(self.temp.name) / ("fake-git.exe" if os.name == "nt" else "fake-git")
        self.exe.write_bytes(b"owned fake executable bytes; never launched")
        if os.name == "posix":
            self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        self.pin = git_runner.TrustedGitExecutable(
            self.exe, hashlib.sha256(self.exe.read_bytes()).hexdigest(), "Git test pin v1",
        )
        self.root_identity = self._identity(self.root)
        self.approved = (ApprovedSourceFileV1("src/main.py", 11, "b" * 64),)
        self.root_output = (f"{str(self.root).replace(chr(92), '/') }\nsha1\n"
                            + "a" * 40 + "\n").encode("utf-8")
        self.index_output = index_record("src/main.py")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _identity(path):
        identity = identify_source_root(path)
        return RootIdentity(identity.platform, identity.device, identity.file_id)

    def runner(self, factory):
        return git_runner.GitMetadataRunner(self.pin, _process_factory=factory)

    def valid_factory(self):
        return ScriptedFactory((
            ScriptedProcess(self.root_output),
            ScriptedProcess(self.index_output),
        ))

    def test_exact_two_fixed_commands_env_pin_root_and_index_identity(self):
        factory = self.valid_factory()
        with patch.dict(os.environ, {
            "PATH": "poison-path", "GIT_DIR": "poison-dir", "GIT_CONFIG_COUNT": "8",
            "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "true",
            "PYTHONPATH": "poison-python", "LD_PRELOAD": "poison-loader",
            "PAGER": "poison-pager", "SSH_AUTH_SOCK": "poison-ssh",
        }, clear=False):
            observation = self.runner(factory).observe(self.root, self.root_identity, self.approved)
        self.assertEqual(len(factory.calls), 2)
        first, second = factory.calls
        self.assertEqual(first[0][0], str(self.exe.resolve()))
        self.assertIn("--no-pager", first[0])
        self.assertIn("--no-optional-locks", first[0])
        self.assertIn("--literal-pathspecs", first[0])
        self.assertEqual(first[0][-4:], ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD"))
        self.assertEqual(second[0][-7:], ("ls-files", "--cached", "--stage", "-v", "--sparse", "--full-name", "-z"))
        for argv, kwargs in (first, second):
            self.assertEqual(kwargs["cwd"], str(self.exe.parent))
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(kwargs["env"]["PATH"], str(self.exe.parent))
            for forbidden in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                              "PYTHONPATH", "LD_PRELOAD", "PAGER", "SSH_AUTH_SOCK"):
                self.assertNotIn(forbidden, kwargs["env"])
        self.assertEqual(observation.root_identity, self.root_identity)
        self.assertEqual(observation.object_format, "sha1")
        self.assertEqual(observation.head_oid, "a" * 40)
        self.assertEqual(observation.executable.sha256, self.pin.expected_sha256)
        self.assertEqual(observation.executable.expected_version_label, "Git test pin v1")
        self.assertEqual(observation.source_index.entries[0].path, "src/main.py")
        self.assertTrue(all(process.stdout.closed and process.stderr.closed for process in factory.started))
        self.assertTrue(all(process.wait_calls == 1 for process in factory.started))

    def test_canonical_metadata_is_stable_for_same_fixed_outputs(self):
        first = self.runner(self.valid_factory()).observe(self.root, self.root_identity, self.approved)
        second = self.runner(self.valid_factory()).observe(self.root, self.root_identity, self.approved)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.root_output_sha256, hashlib.sha256(self.root_output).hexdigest())

    def test_bad_executable_hash_and_root_identity_fail_before_launch(self):
        factory = self.valid_factory()
        bad_pin = git_runner.TrustedGitExecutable(self.exe, "0" * 64, "wrong hash")
        with self.assertRaises(git_runner.GitObservationError) as bad_hash:
            git_runner.GitMetadataRunner(bad_pin, _process_factory=factory).observe(
                self.root, self.root_identity, self.approved,
            )
        self.assertEqual(bad_hash.exception.code, "GIT_EXECUTABLE_HASH_MISMATCH")
        self.assertEqual(factory.calls, [])

        with self.assertRaises(git_runner.GitObservationError) as bad_root:
            self.runner(factory).observe(
                self.root, RootIdentity(self.root_identity.platform, self.root_identity.device, self.root_identity.file_id + 1),
                self.approved,
            )
        self.assertEqual(bad_root.exception.code, "ROOT_IDENTITY_MISMATCH")
        self.assertEqual(factory.calls, [])

        noncanonical = self.root / ".." / self.root.name
        with self.assertRaises(git_runner.GitObservationError):
            self.runner(factory).observe(noncanonical, self.root_identity, self.approved)
        self.assertEqual(factory.calls, [])

        different_root = Path(self.temp.name) / "different-owned-root"
        different_root.mkdir()
        identity = self._identity(different_root)
        modified_identity = RootIdentity(identity.platform, identity.device, identity.file_id + 1)
        with self.assertRaises(git_runner.GitObservationError):
            self.runner(factory).observe(different_root, modified_identity, self.approved)
        self.assertEqual(factory.calls, [])

    def test_git_root_mismatch_stops_before_index_command(self):
        other_root = Path(self.temp.name) / "other-root"
        other_root.mkdir()
        output = f"{str(other_root).replace(chr(92), '/') }\nsha1\n" + "a" * 40 + "\n"
        factory = ScriptedFactory((ScriptedProcess(output.encode("utf-8")), ScriptedProcess(self.index_output)))
        with self.assertRaises(git_runner.GitObservationError) as raised:
            self.runner(factory).observe(self.root, self.root_identity, self.approved)
        self.assertEqual(raised.exception.code, "GIT_ROOT_MISMATCH")
        self.assertEqual(len(factory.calls), 1)

    def test_invalid_object_format_or_head_stops_before_index_command(self):
        outputs = (
            f"{str(self.root).replace(chr(92), '/') }\nunknown\n" + "a" * 40 + "\n",
            f"{str(self.root).replace(chr(92), '/') }\nsha256\n" + "a" * 40 + "\n",
        )
        for output in outputs:
            with self.subTest(output=output[-70:]):
                factory = ScriptedFactory((ScriptedProcess(output.encode("utf-8")), ScriptedProcess(self.index_output)))
                with self.assertRaises(git_runner.GitObservationError):
                    self.runner(factory).observe(self.root, self.root_identity, self.approved)
                self.assertEqual(len(factory.calls), 1)

    def test_nonzero_exit_is_redacted_and_stops_before_second_command(self):
        secret = "do-not-leak-credential"
        factory = ScriptedFactory((ScriptedProcess(b"", secret.encode(), return_code=1), ScriptedProcess()))
        with self.assertRaises(git_runner.GitObservationError) as raised:
            self.runner(factory).observe(self.root, self.root_identity, self.approved)
        self.assertEqual(raised.exception.code, "GIT_COMMAND_FAILED")
        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(len(factory.calls), 1)

    def test_output_caps_abort_without_launching_followup_command(self):
        for over_stdout, over_stderr in ((True, False), (False, True)):
            output = b"x" * (git_runner.MAX_STDOUT_BYTES + 1) if over_stdout else b""
            error = b"x" * (git_runner.MAX_STDERR_BYTES + 1) if over_stderr else b""
            factory = ScriptedFactory((ScriptedProcess(output, error), ScriptedProcess(self.index_output)))
            with self.subTest(over_stdout=over_stdout, over_stderr=over_stderr):
                with self.assertRaises(git_runner.GitObservationError) as raised:
                    self.runner(factory).observe(self.root, self.root_identity, self.approved)
                self.assertEqual(raised.exception.code, "GIT_OUTPUT_LIMIT")
                self.assertEqual(len(factory.calls), 1)

    def test_timeout_and_uncertain_cleanup_never_launch_second_command(self):
        with patch.object(git_runner, "COMMAND_TIMEOUT_SECONDS", 0.01):
            timeout_proc = ScriptedProcess(b"", return_code=None)
            factory = ScriptedFactory((timeout_proc, ScriptedProcess(self.index_output)))
            with self.assertRaises(git_runner.GitObservationError) as raised:
                self.runner(factory).observe(self.root, self.root_identity, self.approved)
            self.assertEqual(raised.exception.code, "GIT_COMMAND_TIMEOUT")
            self.assertTrue(timeout_proc.terminated)
            self.assertEqual(len(factory.calls), 1)

        with patch.object(git_runner, "COMMAND_TIMEOUT_SECONDS", 0.01):
            uncertain = ScriptedProcess(b"", return_code=None, terminate_exits=False, wait_uncertain=True)
            factory = ScriptedFactory((uncertain, ScriptedProcess(self.index_output)))
            with self.assertRaises(git_runner.GitExecutionUncertain) as raised:
                self.runner(factory).observe(self.root, self.root_identity, self.approved)
            self.assertEqual(raised.exception.code, "GIT_EXECUTION_UNCERTAIN")
            self.assertEqual(len(factory.calls), 1)


if __name__ == "__main__":
    unittest.main()
