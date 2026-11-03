"""Explicit, trusted Windows smoke only; never a candidate execution API.

The sole native entry launches fixed System32 cmd.exe with a literal echo only.
The /d flag disables AutoRun, and no caller-supplied command is accepted.
It changes no ACLs and reads no owner source. Existing production effect gates
stay disabled. Native invocation requires the reviewed engineering harness;
the expected digest is a preflight pin, not owner consent or an attestation.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from app.runtime import win32_jobs as jobs
from app.runtime.windows_appcontainer_canary_plan import CanaryJobLimitsV1
from app.runtime.windows_appcontainer_profiles import _WindowsProfileApi, _create_owned_profile, _new_profile_name
from app.runtime.windows_appcontainer_token_check import _WindowsTokenApi, _check_with_api


class WindowsSmokeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("capabilities", ctypes.c_void_p),
                ("count", w.DWORD), ("reserved", w.DWORD)]


class _AppContainerJob(jobs.WindowsJob):
    """Internal trusted adapter; caller owns/pins the profile for its lifetime.

    Job assignment is atomic with creation, including before the parent can
    observe the returned handles. The inherited base spawn checks membership,
    verifies the token, then resumes. This class is NOT exposed to candidates.
    """

    def __init__(self, profile, token_api, limits: CanaryJobLimitsV1):
        if type(limits) is not CanaryJobLimitsV1:
            raise WindowsSmokeError("INVALID_LIMITS")
        self._lifecycle_lock = threading.RLock()
        self._profile = profile
        self._token_api = token_api
        self.token_observation = None
        self.limits = limits
        super().__init__()
        try:
            requested = jobs._ExtendedLimits()
            requested.basic.flags = 0x2000 | 0x2 | 0x4 | 0x8 | 0x100 | 0x200
            requested.basic.per_process_time = limits.cpu_seconds * 10_000_000
            requested.basic.per_job_time = limits.cpu_seconds * 10_000_000
            requested.basic.active_process_limit = 1
            requested.process_memory = limits.memory_bytes
            requested.job_memory = limits.memory_bytes
            if not self._api.SetInformationJobObject(
                self.value, 9, ctypes.byref(requested), ctypes.sizeof(requested)
            ):
                raise WindowsSmokeError("JOB_QUOTA_SET_FAILED")
            actual = jobs._ExtendedLimits()
            if not self._api.QueryInformationJobObject(
                self.value, 9, ctypes.byref(actual), ctypes.sizeof(actual), None
            ):
                raise WindowsSmokeError("JOB_QUOTA_QUERY_FAILED")
            if (actual.basic.flags != requested.basic.flags
                    or actual.basic.per_process_time != requested.basic.per_process_time
                    or actual.basic.per_job_time != requested.basic.per_job_time
                    or actual.basic.active_process_limit != 1
                    or actual.process_memory != limits.memory_bytes
                    or actual.job_memory != limits.memory_bytes):
                raise WindowsSmokeError("JOB_QUOTA_MISMATCH")
            restrictions = w.DWORD(0xFF)
            if not self._api.SetInformationJobObject(self.value, 4, ctypes.byref(restrictions), 4):
                raise WindowsSmokeError("JOB_UI_RESTRICTIONS_FAILED")
        except BaseException:
            self.close()
            raise

    def _creation_attributes(self):
        return (
            (0x20009, _SecurityCapabilities(self._profile.sid, None, 0, 0)),
            (0x2000D, (w.HANDLE * 1)(self.value)),
            (0x2000E, w.DWORD(1)),  # PROCESS_CREATION_CHILD_PROCESS_RESTRICTED
        )

    def _assign(self, process):
        # The Job list attribute assigns atomically inside CreateProcessW.
        if not self.contains(process):
            raise WindowsSmokeError("ATOMIC_JOB_MEMBERSHIP_MISSING")

    def _verify_before_resume(self, process):
        self.token_observation = _check_with_api(self._token_api, process, self._profile)

    def spawn(self, *args, **kwargs):
        with self._lifecycle_lock:
            return super().spawn(*args, **kwargs)

    def close(self):
        with self._lifecycle_lock:
            super().close()


def _file_api():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise WindowsSmokeError("PLATFORM_UNSUPPORTED")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "GetSystemDirectoryW": ([w.LPWSTR, w.UINT], w.UINT),
        "GetDriveTypeW": ([w.LPCWSTR], w.UINT),
        "GetFileAttributesW": ([w.LPCWSTR], w.DWORD),
        "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
        "GetFinalPathNameByHandleW": ([w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        getattr(api, name).argtypes = arguments
        getattr(api, name).restype = result
    return api


def _local_app_data() -> str:
    # Read the current user's OS-known folder, never inherit the whole parent
    # environment. Without LOCALAPPDATA AppContainer creation returns error 203.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    query = shell.SHGetFolderPathW
    query.argtypes = [w.HWND, ctypes.c_int, w.HANDLE, w.DWORD, w.LPWSTR]
    query.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(260)
    if query(None, 0x001C, None, 0, buffer) != 0 or not Path(buffer.value).is_absolute():
        raise WindowsSmokeError("LOCAL_APP_DATA_UNAVAILABLE")
    return buffer.value


def _smoke_environment(executable: Path) -> dict[str, str]:
    return {"SystemRoot": str(executable.parent.parent), "LOCALAPPDATA": _local_app_data()}


@contextmanager
def _pinned_system_binary(expected_sha256: str):
    """Hold a no-write/no-delete-sharing handle on a fixed, non-reparse image."""
    if type(expected_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise WindowsSmokeError("BINARY_PIN_INVALID")
    api = _file_api()
    buffer = ctypes.create_unicode_buffer(32768)
    size = api.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise WindowsSmokeError("SYSTEM_DIRECTORY_UNAVAILABLE")
    executable = Path(buffer.value) / "cmd.exe"
    if api.GetDriveTypeW(executable.anchor) != 3:
        raise WindowsSmokeError("SYSTEM_VOLUME_UNSUPPORTED")
    for component in reversed((executable, *executable.parents)):
        attributes = api.GetFileAttributesW(str(component))
        if attributes == 0xFFFFFFFF or attributes & 0x400:
            raise WindowsSmokeError("SYSTEM_PATH_UNSAFE")
    handle = api.CreateFileW(str(executable), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise WindowsSmokeError("BINARY_PIN_OPEN_FAILED")
    try:
        length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if (not 0 < length < len(buffer)
                or buffer.value.casefold() != ("\\\\?\\" + str(executable)).casefold()):
            raise WindowsSmokeError("BINARY_PIN_PATH_MISMATCH")
        # Windows sharing denies content modification/deletion while the handle
        # is retained. System32 ancestors are OS-owned, not caller-selected.
        with executable.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected_sha256:
            raise WindowsSmokeError("BINARY_PIN_MISMATCH")
        yield executable
    finally:
        if not api.CloseHandle(handle):
            raise WindowsSmokeError("BINARY_PIN_CLOSE_FAILED")


def run_windows_appcontainer_smoke(*, expected_binary_sha256: str, journal_path: Path) -> dict:
    """One explicit trusted smoke. No native work happens on import.

    Positive completion proves only this fixed child/profile/token/Job path.
    It does not prove candidate filesystem/network/escape containment. No
    production launch gate, consent mechanism or SandboxAttestation is issued.
    """
    # Exclusive creation prevents overwriting earlier failed evidence. An
    # interrupted intent is UNKNOWN, never authority to adopt/delete a profile.
    with journal_path.open("x", encoding="utf-8") as journal:
        def record(event, **fields):
            journal.write(json.dumps({"event": event, **fields}, sort_keys=True) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
        record("begin", binary_sha256=expected_binary_sha256, sandbox_attested=False)
        try:
            result = _run_smoke(expected_binary_sha256, record)
        except BaseException as error:
            record("failed", code=getattr(error, "code", "UNEXPECTED_FAILURE"),
                   native_error=getattr(error, "native_error", None),
                   automatic_retry_allowed=False)
            raise
        record("completed", result=result)
        return result


def _run_smoke(expected_binary_sha256, record):
    with _pinned_system_binary(expected_binary_sha256) as executable:
        def name_factory():
            name = _new_profile_name()
            record("profile_create_intent", name=name)
            return name
        profile = _create_owned_profile(_WindowsProfileApi(), name_factory=name_factory)
        profile_name = profile.name
        child = None
        job = None
        resources = ExitStack()
        cleanup_confirmed = True
        try:
            record("profile_created", name=profile_name)
            # Same ownership condition as profile.close(): retain the SID across
            # attribute creation, token verification and the child lifetime.
            state = profile._WindowsAppContainerProfileLease__state
            with state.condition:
                directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="onepath-r1-smoke-"))
                stdin = resources.enter_context(open(Path(directory) / "stdin", "w+b"))
                stdout = resources.enter_context(open(Path(directory) / "stdout", "w+b"))
                stderr = resources.enter_context(open(Path(directory) / "stderr", "w+b"))
                job = _AppContainerJob(profile, _WindowsTokenApi(), CanaryJobLimitsV1())
                child = job.spawn(
                    executable, ("/d", "/q", "/c", "echo", "onepath-r1-canary"), cwd=executable.parent,
                    environment=_smoke_environment(executable),
                    stdin_fd=stdin.fileno(), stdout_fd=stdout.fileno(), stderr_fd=stderr.fileno(),
                )
                observation = job.token_observation
                record("child_verified_and_resumed", is_appcontainer=observation.is_appcontainer,
                       profile_sid_matches=observation.profile_sid_matches,
                       capability_count=observation.capability_count,
                       atomic_job_membership=True, quota_readback_matched=True)
                if not child.wait(job.limits.wall_seconds):
                    raise WindowsSmokeError("SMOKE_TIMEOUT")
                exit_code = child.exit_code
                stdout.seek(0)
                output = stdout.read(65537)
                stderr.seek(0)
                errors = stderr.read(65537)
                record("child_exited", exit_code=exit_code, stdout_bytes=len(output),
                       stderr_bytes=len(errors), stderr_sha256=hashlib.sha256(errors).hexdigest())
                if exit_code != 0:
                    raise WindowsSmokeError("SMOKE_CHILD_FAILED")
                if output != b"onepath-r1-canary\r\n" or errors:
                    raise WindowsSmokeError("SMOKE_OUTPUT_INVALID")
                observation = job.token_observation
                if observation is None:
                    raise WindowsSmokeError("SMOKE_TOKEN_MISSING")
                result = {
                    "schema_version": 1, "scope": "fixed-system-cmd-literal-echo-smoke-only",
                    "binary_sha256": expected_binary_sha256, "profile_name": profile_name,
                    "is_appcontainer": observation.is_appcontainer,
                    "profile_sid_matches": observation.profile_sid_matches,
                    "capability_count": observation.capability_count,
                    "atomic_job_membership": True, "quota_readback_matched": True,
                    "child_exit_code": 0, "stdout_bytes": len(output),
                    "sandbox_attested": False, "candidate_execution_enabled": False,
                }
        except jobs.NativeProcessError as error:
            if child is None and error.retained_process is not None:
                child = error.retained_process
            raise
        finally:
            try:
                if job is not None:
                    job.stop_and_wait(5)
                if child is not None:
                    child.close()
            except BaseException:
                cleanup_confirmed = False
                # Preserve exact live handles/profile for explicit reconciliation.
                # Never delete the profile while child cleanup is uncertain.
                error = WindowsSmokeError("SMOKE_CLEANUP_UNCERTAIN")
                error.retained_job, error.retained_child, error.retained_profile = job, child, profile
                error.retained_resources = resources
                raise error from None
            finally:
                if cleanup_confirmed:
                    try:
                        profile.close()
                        record("profile_deleted", name=profile_name)
                    finally:
                        resources.close()
        result["job_empty_and_profile_deleted"] = True
        return result
