"""Synthetic security-ordering/failure checks. No native effects in this module."""
import ctypes
from contextlib import nullcontext
from ctypes import wintypes as w
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import os
import threading
from unittest.mock import patch

from app.runtime import win32_jobs as jobs
from app.runtime import windows_appcontainer_smoke as smoke
from app.runtime.windows_appcontainer_canary_plan import CanaryJobLimitsV1


class FakeApi:
    def __init__(self, fail=None):
        self.fail = fail
        self.events = []
        self.attributes = {}
        self.quota = None
        self.counts = []

    def CreateJobObjectW(self, *_):
        return 100

    def SetInformationJobObject(self, handle, kind, value, length):
        self.events.append(("set", kind))
        if kind == 9:
            self.quota = ctypes.string_at(value, length)
        return self.fail != "quota_set"

    def QueryInformationJobObject(self, handle, kind, value, length, returned):
        if kind == 9:
            ctypes.memmove(value, self.quota, len(self.quota))
            if self.fail == "quota_mismatch":
                ctypes.cast(value, ctypes.POINTER(jobs._ExtendedLimits)).contents.basic.active_process_limit = 2
        return self.fail != "quota_query"

    def CloseHandle(self, handle):
        self.events.append(("close", handle))
        return True

    def GetCurrentProcess(self):
        return -1

    def DuplicateHandle(self, current, original, target, result, *_):
        ctypes.cast(result, ctypes.POINTER(w.HANDLE)).contents.value = original + 10000
        return True

    def InitializeProcThreadAttributeList(self, buffer, count, flags, size):
        self.counts.append(count)
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
        return bool(buffer)

    def UpdateProcThreadAttribute(self, buffer, flags, key, value, size, *_):
        self.events.append(("attribute", key))
        self.attributes[key] = ctypes.string_at(value, size)
        return self.fail != key

    def DeleteProcThreadAttributeList(self, *_):
        self.events.append(("delete_attributes",))

    def CreateProcessW(self, executable, command, pa, ta, inherit, flags, env, cwd, startup, info):
        self.events.append(("create",))
        assert inherit is True
        assert flags & jobs._CREATE_SUSPENDED
        assert not flags & 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        if self.fail == "create":
            return False
        value = ctypes.cast(info, ctypes.POINTER(jobs._ProcessInfo)).contents
        value.process, value.thread, value.pid, value.tid = 200, 201, 42, 43
        return True

    def IsProcessInJob(self, process, job, result):
        self.events.append(("membership",))
        ctypes.cast(result, ctypes.POINTER(w.BOOL)).contents.value = self.fail != "membership"
        return True

    def ResumeThread(self, handle):
        self.events.append(("resume",))
        return 0 if self.fail == "resume_count" else 1

    def TerminateProcess(self, handle, code):
        self.events.append(("terminate", handle))
        return True

    def WaitForSingleObject(self, handle, timeout):
        return 258 if self.fail == "cleanup" else 0


class SmokeAdapterTests(unittest.TestCase):
    def launch(self, api, *, bad_token=False):
        profile = type("Profile", (), {"sid": 123})()
        def check(*_):
            api.events.append(("token",))
            if bad_token or api.fail == "cleanup":
                raise smoke.WindowsSmokeError("TEST_BAD_TOKEN")
            return object()
        with patch.object(jobs, "_api", return_value=api), \
                patch.object(smoke, "_check_with_api", side_effect=check), \
                tempfile.TemporaryFile() as stream:
            job = smoke._AppContainerJob(profile, object(), CanaryJobLimitsV1())
            try:
                child = job.spawn(Path(r"C:\Windows\System32\whoami.exe"), (),
                    cwd=Path(r"C:\Windows\System32"), environment={"SystemRoot": r"C:\Windows"},
                    stdin_fd=stream.fileno(), stdout_fd=stream.fileno(), stderr_fd=stream.fileno())
                child.close()
                return job
            finally:
                job.close()

    def test_security_attributes_quotas_and_order(self):
        api = FakeApi()
        self.launch(api)
        self.assertEqual(api.counts, [4, 4])
        self.assertEqual(set(api.attributes), {0x20002, 0x20009, 0x2000D, 0x2000E})
        capabilities = smoke._SecurityCapabilities.from_buffer_copy(api.attributes[0x20009])
        self.assertEqual(capabilities.sid, 123)
        self.assertIsNone(capabilities.capabilities)
        self.assertEqual(capabilities.count, 0)
        self.assertEqual(w.DWORD.from_buffer_copy(api.attributes[0x2000E]).value, 1)
        quota = jobs._ExtendedLimits.from_buffer_copy(api.quota)
        self.assertEqual(quota.basic.flags, 0x230E)
        self.assertEqual(quota.basic.active_process_limit, 1)
        self.assertEqual(quota.basic.per_job_time, 100_000_000)
        self.assertEqual(quota.job_memory, 256 * 1024 * 1024)
        labels = [event[0] for event in api.events]
        self.assertLess(labels.index("create"), labels.index("membership"))
        self.assertLess(labels.index("membership"), labels.index("token"))
        self.assertLess(labels.index("token"), labels.index("resume"))

    def test_every_pre_resume_failure_prevents_execution_and_closes_handles(self):
        for failure in ("quota_set", "quota_query", "quota_mismatch", 0x20002,
                        0x20009, 0x2000D, 0x2000E, "create", "membership", "token"):
            with self.subTest(failure=failure):
                api = FakeApi(failure)
                with self.assertRaises((smoke.WindowsSmokeError, jobs.NativeProcessError)):
                    self.launch(api, bad_token=failure == "token")
                self.assertNotIn(("resume",), api.events)
                self.assertIn(("close", 100), api.events)
                if failure in ("membership", "token"):
                    self.assertIn(("terminate", 200), api.events)
                    self.assertIn(("close", 200), api.events)
                    self.assertIn(("close", 201), api.events)

    def test_unexpected_suspend_count_is_failure_and_cleanup(self):
        api = FakeApi("resume_count")
        with self.assertRaisesRegex(jobs.NativeProcessError, "PROCESS_RESUME_FAILED"):
            self.launch(api)
        self.assertIn(("terminate", 200), api.events)

    def test_cleanup_failure_retains_original_child(self):
        api = FakeApi("cleanup")
        with self.assertRaises(jobs.NativeProcessError) as caught:
            self.launch(api)
        self.assertEqual(caught.exception.code, "SUSPENDED_PROCESS_CLEANUP_FAILED")
        self.assertEqual(caught.exception.retained_process.value, 200)
        self.assertNotIn(("resume",), api.events)

    def test_invalid_digest_rejected_without_windows_api(self):
        with patch.object(smoke, "_file_api") as api:
            with self.assertRaisesRegex(smoke.WindowsSmokeError, "BINARY_PIN_INVALID"):
                smoke._run_smoke("bad", lambda *args, **kwargs: None)
        api.assert_not_called()

    def test_journal_is_exclusive_and_durable_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "run.jsonl"
            with self.assertRaisesRegex(smoke.WindowsSmokeError, "BINARY_PIN_INVALID"):
                smoke.run_windows_appcontainer_smoke(expected_binary_sha256="bad", journal_path=journal)
            text = journal.read_text()
            self.assertIn('"event": "begin"', text)
            self.assertIn('"event": "failed"', text)
            self.assertIn('"automatic_retry_allowed": false', text)
            with self.assertRaises(FileExistsError):
                smoke.run_windows_appcontainer_smoke(expected_binary_sha256="bad", journal_path=journal)
            self.assertEqual(journal.read_text(), text)

    def test_environment_uses_only_os_paths_not_parent_secrets(self):
        with patch.object(smoke, "_local_app_data", return_value=r"C:\Users\fixture\AppData\Local"), \
                patch.dict("os.environ", {"SECRET_KEY": "must-not-leak", "PATH": "untrusted"}):
            env = smoke._smoke_environment(Path(r"C:\Windows\System32\whoami.exe"))
        self.assertEqual(env, {"SystemRoot": r"C:\Windows", "LOCALAPPDATA": r"C:\Users\fixture\AppData\Local"})

    def test_full_orchestration_records_cleanup_and_never_accepts_failed_or_empty_child(self):
        for case in ("pass", "empty", "exit", "timeout", "stop_failure", "journal_failure"):
            with self.subTest(case=case):
                events = []
                state = SimpleNamespace(condition=threading.RLock())
                profile = SimpleNamespace(name="owned-test-profile", close=lambda: events.append("profile_close"))
                profile._WindowsAppContainerProfileLease__state = state
                child = SimpleNamespace(wait=lambda timeout: case != "timeout", exit_code=1 if case == "exit" else 0,
                                        close=lambda: events.append("child_close"))
                job = SimpleNamespace(limits=CanaryJobLimitsV1(), token_observation=SimpleNamespace(
                    is_appcontainer=True, profile_sid_matches=True, capability_count=0))
                def spawn(executable, args, **kwargs):
                    self.assertEqual(executable, Path(r"C:\Windows\System32\cmd.exe"))
                    self.assertEqual(args, ("/d", "/q", "/c", "echo", "onepath-r1-canary"))
                    if case != "empty":
                        os.write(kwargs["stdout_fd"], b"onepath-r1-canary\r\n")
                    return child
                def stop(_timeout):
                    events.append("job_stop")
                    if case == "stop_failure":
                        raise RuntimeError("synthetic stop failure")
                job.spawn, job.stop_and_wait = spawn, stop
                def record(event, **fields):
                    events.append(event)
                    if event == "child_verified_and_resumed" and case == "journal_failure":
                        raise OSError("synthetic journal failure")
                with patch.object(smoke, "_pinned_system_binary", return_value=nullcontext(Path(r"C:\Windows\System32\cmd.exe"))), \
                        patch.object(smoke, "_create_owned_profile", return_value=profile), \
                        patch.object(smoke, "_WindowsProfileApi"), patch.object(smoke, "_WindowsTokenApi"), \
                        patch.object(smoke, "_AppContainerJob", return_value=job), \
                        patch.object(smoke, "_smoke_environment", return_value={"SystemRoot": r"C:\Windows"}):
                    if case == "pass":
                        result = smoke._run_smoke("a" * 64, record)
                        self.assertTrue(result["job_empty_and_profile_deleted"])
                        self.assertFalse(result["sandbox_attested"])
                    else:
                        with self.assertRaises((smoke.WindowsSmokeError, OSError)) as caught:
                            smoke._run_smoke("a" * 64, record)
                        if case == "stop_failure":
                            self.assertNotIn("profile_close", events)
                            self.assertIs(caught.exception.retained_child, child)
                            caught.exception.retained_resources.close()  # Owned synthetic files only.
                    self.assertIn("job_stop", events)
                    if case != "stop_failure":
                        self.assertLess(events.index("job_stop"), events.index("profile_close"))


if __name__ == "__main__":
    unittest.main()
