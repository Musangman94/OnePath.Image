"""Bounded CPU-child checks. Never touch an existing app/model process."""

import contextlib
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime.win32_jobs import NativeProcessError, ProcessHandle, WindowsJob  # noqa: E402


class ProcessHandlePinTests(unittest.TestCase):
    """Fake native calls verify close cannot race any retained-handle operation."""

    class BlockingApi:
        def __init__(self, blocked_call, *, fail=False, wait_timeout=False):
            self.blocked_call = blocked_call
            self.fail = fail
            self.wait_timeout = wait_timeout
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = []

        def _call(self, name, handle):
            self.calls.append((name, handle))
            if name == self.blocked_call:
                self.started.set()
                if not self.release.wait(5):
                    raise AssertionError("test did not release fake native call")
                if self.fail:
                    raise RuntimeError("fake native failure")

        def WaitForSingleObject(self, handle, _timeout):
            self._call("WaitForSingleObject", handle)
            return 258 if self.wait_timeout else 0

        def GetExitCodeProcess(self, handle, result):
            self._call("GetExitCodeProcess", handle)
            ctypes.cast(result, ctypes.POINTER(ctypes.c_uint32)).contents.value = 37
            return 1

        def GetProcessTimes(self, handle, creation, *_other_times):
            self._call("GetProcessTimes", handle)
            ctypes.cast(creation, ctypes.POINTER(ctypes.c_uint32)).contents.value = 123
            return 1

        def QueryFullProcessImageNameW(self, handle, _flags, buffer, _size):
            self._call("QueryFullProcessImageNameW", handle)
            buffer.value = r"C:\fake\child.exe"
            return 1

        def TerminateProcess(self, handle, _exit_code):
            self._call("TerminateProcess", handle)
            return 1

        def CloseHandle(self, handle):
            self.calls.append(("CloseHandle", handle))
            return 1

    def test_close_waits_until_each_native_operation_finishes(self):
        cases = (
            ("wait", "WaitForSingleObject", False),
            ("exit_code", "WaitForSingleObject", False),
            ("creation_time_100ns", "GetProcessTimes", False),
            ("image_path", "QueryFullProcessImageNameW", False),
            ("terminate_owned", "TerminateProcess", True),
        )
        operations = {
            "wait": lambda process: process.wait(0),
            "exit_code": lambda process: process.exit_code,
            "creation_time_100ns": lambda process: process.creation_time_100ns,
            "image_path": lambda process: process.image_path,
            "terminate_owned": lambda process: process.terminate_owned(),
        }
        for method, blocked_call, created in cases:
            with self.subTest(method=method):
                api = self.BlockingApi(blocked_call, wait_timeout=(method == "wait"))
                process = ProcessHandle(0x1234, 91, created=created)
                errors = []
                with patch("app.runtime.win32_jobs._api", return_value=api):
                    operation = threading.Thread(
                        target=lambda: self._capture(errors, lambda: operations[method](process)),
                        daemon=True,
                    )
                    operation.start()
                    self.assertTrue(api.started.wait(2), f"{method} did not reach fake API")
                    closed = threading.Event()
                    closer = threading.Thread(
                        target=lambda: (process.close(), closed.set()), daemon=True
                    )
                    closer.start()
                    self.assertFalse(closed.wait(0.05), f"close raced {method}")
                    api.release.set()
                    operation.join(2)
                    closer.join(2)
                self.assertFalse(operation.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(closed.is_set())
                self.assertEqual(api.calls[-1], ("CloseHandle", 0x1234))
                native_calls = [name for name, _handle in api.calls if name != "CloseHandle"]
                expected_last = "GetExitCodeProcess" if method == "exit_code" else (
                    "WaitForSingleObject" if method == "terminate_owned" else blocked_call
                )
                self.assertEqual(native_calls[-1], expected_last)
                self.assertLess(api.calls.index((native_calls[-1], 0x1234)), api.calls.index(("CloseHandle", 0x1234)))
                if method == "exit_code":
                    self.assertEqual(native_calls, ["WaitForSingleObject", "GetExitCodeProcess"])
                if method == "terminate_owned":
                    self.assertEqual(native_calls, ["TerminateProcess", "WaitForSingleObject"])

    def test_pin_is_released_when_a_native_call_raises(self):
        api = self.BlockingApi("GetProcessTimes", fail=True)
        process = ProcessHandle(0x1234, 91)
        errors = []
        with patch("app.runtime.win32_jobs._api", return_value=api):
            operation = threading.Thread(
                target=lambda: self._capture(errors, lambda: process.creation_time_100ns),
                daemon=True,
            )
            operation.start()
            self.assertTrue(api.started.wait(2))
            api.release.set()
            operation.join(2)
            self.assertFalse(operation.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RuntimeError)
            process.close()
        self.assertEqual(api.calls[-1], ("CloseHandle", 0x1234))

    @staticmethod
    def _capture(errors, operation):
        try:
            operation()
        except BaseException as error:
            errors.append(error)


@unittest.skipUnless(os.name == "nt", "Windows process ownership")
class WindowsJobTests(unittest.TestCase):
    def environment(self):
        names = {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "PROGRAMFILES",
            "LOCALAPPDATA", "APPDATA", "USERPROFILE", "COMSPEC", "PATHEXT",
        }
        return {key: value for key, value in os.environ.items() if key.upper() in names}

    @contextlib.contextmanager
    def resources(self):
        with tempfile.TemporaryDirectory(prefix="onepath-cpu-job-") as temporary:
            root = Path(temporary)
            with open(os.devnull, "rb") as incoming, (root / "out").open("wb") as outgoing:
                yield root, incoming, outgoing

    def spawn(self, job, incoming, outgoing, *arguments):
        return job.spawn(
            Path(sys.executable).resolve(), tuple(arguments), cwd=Path.cwd().resolve(),
            environment=self.environment(), stdin_fd=incoming.fileno(),
            stdout_fd=outgoing.fileno(), stderr_fd=outgoing.fileno(),
        )

    def test_exact_unicode_arguments_and_whitelisted_stdout(self):
        arguments = ("mint 🌿", 'a"b', "ends\\", "")
        code = "import json,sys;sys.stdout.buffer.write(json.dumps(sys.argv[1:],ensure_ascii=False).encode('utf-8'))"
        with self.resources() as (root, incoming, outgoing), WindowsJob() as job:
            with self.spawn(job, incoming, outgoing, "-c", code, *arguments) as process:
                self.assertTrue(process.wait(10))
                self.assertGreater(process.creation_time_100ns, 0)
            outgoing.flush()
            self.assertEqual(json.loads((root / "out").read_bytes()), list(arguments))

    def test_assignment_failure_never_executes_child_code_and_cleans_suspended_child(self):
        class RefusingJob(WindowsJob):
            def _assign(self, process):
                raise NativeProcessError("TEST_ASSIGNMENT_REFUSED", process.pid)

        with self.resources() as (root, incoming, outgoing), RefusingJob() as job:
            marker = root / "must-not-exist"
            with self.assertRaises(NativeProcessError) as caught:
                self.spawn(job, incoming, outgoing, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('bad')", str(marker))
            self.assertEqual(caught.exception.code, "TEST_ASSIGNMENT_REFUSED")
            self.assertFalse(marker.exists())
            try:
                observed = ProcessHandle.open_for_observation(caught.exception.process_id)
            except NativeProcessError:
                observed = None
            if observed is not None:
                with observed:
                    self.assertTrue(observed.wait(0))

    def test_last_job_close_stops_root_and_descendants(self):
        code = (
            "import json,os,subprocess,sys,time;from pathlib import Path;"
            "child=subprocess.Popen([sys.argv[2],'-c','import time;time.sleep(30)']);"
            "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));time.sleep(60)"
        )
        with self.resources() as (root, incoming, outgoing):
            marker = root / "children.json"
            job = WindowsJob()
            process = None
            retained = []
            try:
                with ProcessHandle.open_for_observation(os.getpid()) as this_process:
                    base_python = str(this_process.image_path)
                process = self.spawn(job, incoming, outgoing, "-c", code, str(marker), base_python)
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "test child did not publish its own descendants")
                for pid in job.member_pids():
                    handle = job.retain_member(pid)
                    retained.append(handle)
                    self.assertTrue(job.contains(handle))
                self.assertTrue(set(json.loads(marker.read_text())).issubset(handle.pid for handle in retained))
                job.close()
                self.assertTrue(process.wait(5))
                self.assertTrue(all(handle.wait(5) for handle in retained))
            finally:
                job.close()
                for handle in retained:
                    if not handle.wait(0):
                        handle.terminate_owned()
                    handle.close()
                if process is not None:
                    if not process.wait(0):
                        process.terminate_owned()
                    process.close()

    def test_observation_handle_cannot_terminate_process(self):
        # This read-only handle references this test process; no PID-based kill exists.
        with ProcessHandle.open_for_observation(os.getpid()) as process:
            self.assertTrue(process.image_path.is_absolute())
            with self.assertRaises(NativeProcessError) as caught:
                process.terminate_owned()
            self.assertEqual(caught.exception.code, "PROCESS_NOT_CREATED_HERE")
            self.assertFalse(process.wait(0))

    def test_retained_handle_reports_exit_status_after_wait_only(self):
        with self.resources() as (_, incoming, outgoing), WindowsJob() as job:
            with self.spawn(job, incoming, outgoing, "-c", "import sys,time;time.sleep(1);sys.exit(7)") as process:
                self.assertIsNone(process.exit_code)
                self.assertTrue(process.wait(5))
                self.assertEqual(process.exit_code, 7)


if __name__ == "__main__":
    unittest.main()
