"""Windows-owned jobs: create suspended, assign, resume; never adopt a PID tree.

Uses documented Win32 APIs. Import is safe on other platforms; constructing a
job there fails explicitly. Closing the last, noninherited job handle kills its
members. No breakaway flags are used.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from contextlib import contextmanager
from functools import lru_cache
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Iterator

_SPAWN_LOCK = threading.Lock()
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_STILL_ACTIVE = 259
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_CREATE_SUSPENDED = 0x4
_CREATE_UNICODE_ENVIRONMENT = 0x400
_CREATE_NO_WINDOW = 0x08000000
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000


class NativeProcessError(OSError):
    def __init__(
        self, code: str, process_id: int | None = None,
        retained_process: ProcessHandle | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.process_id = process_id
        self.retained_process = retained_process


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
        ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_process_limit", w.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes"
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IOCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        ("x", w.DWORD), ("y", w.DWORD), ("x_size", w.DWORD), ("y_size", w.DWORD),
        ("x_chars", w.DWORD), ("y_chars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", ctypes.c_void_p),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


@lru_cache(maxsize=1)
def _api() -> Any:
    if os.name != "nt":
        raise NativeProcessError("PLATFORM_UNSUPPORTED")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "IsProcessInJob": ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "GetCurrentProcess": ([], w.HANDLE),
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "DuplicateHandle": ([w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL),
        "GetProcessTimes": ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "QueryFullProcessImageNameW": ([w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
        "InitializeProcThreadAttributeList": ([ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        "UpdateProcThreadAttribute": ([ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                            ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_ProcessInfo)], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, result
    return dll


class ProcessHandle:
    """A retained kernel process handle; waits never reopen a potentially reused PID."""

    def __init__(self, handle: int, pid: int, *, created: bool = False):
        if handle in (None, 0, -1, _INVALID_HANDLE):
            raise NativeProcessError("PROCESS_HANDLE_INVALID")
        self._handle: int | None = handle
        self._handle_lock = threading.RLock()
        self._pin_owners: dict[int, int] = {}
        self.pid = pid
        self._created = created

    @classmethod
    def open_for_observation(cls, pid: int) -> ProcessHandle:
        handle = _api().OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise NativeProcessError("PROCESS_UNAVAILABLE", pid)
        return cls(handle, pid)

    @property
    def value(self) -> int:
        with self._handle_lock:
            if self._handle is None:
                raise NativeProcessError("PROCESS_HANDLE_CLOSED", self.pid)
            return self._handle

    @contextmanager
    def pin(self) -> Iterator[int]:
        """Keep this exact kernel handle open while a bounded API operation uses it."""
        with self._handle_lock:
            if self._handle is None:
                raise NativeProcessError("PROCESS_HANDLE_CLOSED", self.pid)
            owner = threading.get_ident()
            self._pin_owners[owner] = self._pin_owners.get(owner, 0) + 1
            try:
                yield self._handle
            finally:
                remaining = self._pin_owners[owner] - 1
                if remaining:
                    self._pin_owners[owner] = remaining
                else:
                    del self._pin_owners[owner]

    @property
    def creation_time_100ns(self) -> int:
        times = [w.FILETIME() for _ in range(4)]
        with self.pin() as handle:
            if not _api().GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                raise NativeProcessError("PROCESS_IDENTITY_UNAVAILABLE", self.pid)
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    @property
    def image_path(self) -> Path:
        size = w.DWORD(32_768)
        buffer = ctypes.create_unicode_buffer(size.value)
        with self.pin() as handle:
            if not _api().QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                raise NativeProcessError("PROCESS_IMAGE_UNAVAILABLE", self.pid)
        return Path(buffer.value)

    def wait(self, timeout_seconds: float = 0) -> bool:
        if not 0 <= timeout_seconds <= 240:
            raise ValueError("Invalid process wait.")
        with self.pin() as handle:
            result = _api().WaitForSingleObject(handle, int(timeout_seconds * 1000))
        if result == _WAIT_OBJECT_0:
            return True
        if result == _WAIT_TIMEOUT:
            return False
        raise NativeProcessError("PROCESS_WAIT_FAILED", self.pid)

    @property
    def exit_code(self) -> int | None:
        with self.pin() as handle:
            result = _api().WaitForSingleObject(handle, 0)
            if result == _WAIT_TIMEOUT:
                return None
            if result != _WAIT_OBJECT_0:
                raise NativeProcessError("PROCESS_WAIT_FAILED", self.pid)
            value = w.DWORD()
            if not _api().GetExitCodeProcess(handle, ctypes.byref(value)):
                raise NativeProcessError("PROCESS_EXIT_CODE_UNAVAILABLE", self.pid)
            return int(value.value)

    def close(self) -> None:
        with self._handle_lock:
            if self._pin_owners.get(threading.get_ident(), 0):
                raise NativeProcessError("PROCESS_HANDLE_CLOSE_WHILE_PINNED", self.pid)
            handle, self._handle = self._handle, None
            if handle is not None:
                _api().CloseHandle(handle)

    def terminate_owned(self) -> None:
        if not self._created:
            raise NativeProcessError("PROCESS_NOT_CREATED_HERE", self.pid)
        # Termination addresses the original retained handle, never a reopened PID.
        with self.pin() as handle:
            _api().TerminateProcess(handle, 1)
            if not self.wait(5):
                raise NativeProcessError("PROCESS_STOP_FAILED", self.pid, self)

    def __enter__(self) -> ProcessHandle:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def tcp_listeners(port: int) -> tuple[tuple[str, int], ...]:
    """Read IPv4 listener ownership; never open a connection or alter a process."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise NativeProcessError("PORT_INVALID")
    _api()  # Explicit platform check before loading the Windows DLL.
    query = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG]
    query.restype = w.DWORD
    for _ in range(3):
        size = w.DWORD()
        status = query(None, ctypes.byref(size), False, 2, 3, 0)
        if status not in (0, 122) or not 4 <= size.value <= 1_048_576:
            raise NativeProcessError("LISTENER_QUERY_FAILED")
        buffer = ctypes.create_string_buffer(size.value)
        status = query(buffer, ctypes.byref(size), False, 2, 3, 0)
        if status == 122:
            continue
        if status:
            raise NativeProcessError("LISTENER_QUERY_FAILED")
        count = struct.unpack_from("<I", buffer)[0]
        if 4 + count * 24 > len(buffer):
            raise NativeProcessError("LISTENER_QUERY_INVALID")
        matches = []
        for offset in range(4, 4 + count * 24, 24):
            state, address, network_port, _, _, pid = struct.unpack_from("<6I", buffer, offset)
            if state == 2 and socket.ntohs(network_port & 0xFFFF) == port:
                matches.append((socket.inet_ntoa(struct.pack("<I", address)), pid))
        return tuple(matches)
    raise NativeProcessError("LISTENER_QUERY_CHANGED")


class WindowsJob:
    """An unnamed, noninherited, one-launch job containing only our new process tree."""

    def __init__(self) -> None:
        self._api = _api()
        self._handle: int | None = self._api.CreateJobObjectW(None, None)
        self._launched = False
        if not self._handle:
            raise NativeProcessError("JOB_CREATE_FAILED")
        limits = _ExtendedLimits()
        limits.basic.flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise NativeProcessError("JOB_LIMIT_FAILED")

    @property
    def value(self) -> int:
        if self._handle is None:
            raise NativeProcessError("JOB_CLOSED")
        return self._handle

    def contains(self, process: ProcessHandle) -> bool:
        answer = w.BOOL()
        if not self._api.IsProcessInJob(process.value, self.value, ctypes.byref(answer)):
            raise NativeProcessError("JOB_MEMBERSHIP_UNAVAILABLE", process.pid)
        return bool(answer.value)

    def retain_member(self, pid: int) -> ProcessHandle:
        """Retain cleanup rights only after proving membership in this private job."""
        handle = self._api.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | 0x1, False, pid
        )
        if not handle:
            raise NativeProcessError("JOB_MEMBER_UNAVAILABLE", pid)
        process = ProcessHandle(handle, pid, created=True)
        try:
            if not self.contains(process):
                raise NativeProcessError("PROCESS_NOT_IN_THIS_JOB", pid)
        except BaseException:
            process.close()
            raise
        return process

    def member_pids(self) -> tuple[int, ...]:
        capacity = 16
        for _ in range(4):
            buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * capacity)
            returned = w.DWORD()
            ok = self._api.QueryInformationJobObject(self.value, 3, buffer, len(buffer), ctypes.byref(returned))
            if not ok:
                if ctypes.get_last_error() != 234:
                    raise NativeProcessError("JOB_MEMBERSHIP_UNAVAILABLE")
                capacity *= 4
                continue
            count = w.DWORD.from_buffer(buffer, 4).value
            if count > capacity:
                raise NativeProcessError("JOB_MEMBERSHIP_INVALID")
            values = (ctypes.c_size_t * count).from_buffer(buffer, 8)
            return tuple(int(value) for value in values)
        raise NativeProcessError("JOB_MEMBERSHIP_LIMIT")

    def _assign(self, process: ProcessHandle) -> None:
        if not self._api.AssignProcessToJobObject(self.value, process.value):
            raise NativeProcessError("JOB_ASSIGNMENT_FAILED", process.pid)

    def _creation_attributes(self) -> tuple[tuple[int, Any], ...]:
        """Trusted adapter extension; values stay alive through process creation."""
        return ()

    def _verify_before_resume(self, process: ProcessHandle) -> None:
        """Trusted adapter checks run after Job membership, before any child code."""

    def spawn(
        self, executable: Path, args: tuple[str, ...], *, cwd: Path,
        environment: dict[str, str], stdin_fd: int, stdout_fd: int, stderr_fd: int,
    ) -> ProcessHandle:
        """Spawn suspended and fail closed before executing any user code if assignment fails."""
        import msvcrt

        if self._launched:
            raise NativeProcessError("JOB_ALREADY_LAUNCHED")
        if not executable.is_absolute() or not cwd.is_absolute():
            raise NativeProcessError("LAUNCH_PATH_INVALID")
        command = subprocess.list2cmdline([str(executable), *args])
        if "\0" in command or len(command.encode("utf-16-le")) // 2 >= 32_767:
            raise NativeProcessError("LAUNCH_ARGUMENT_INVALID")
        if not environment or any(
            not key or "=" in key or "\0" in key or "\0" in value
            for key, value in environment.items()
        ):
            raise NativeProcessError("LAUNCH_ENVIRONMENT_INVALID")
        environment_block = "\0".join(
            f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].casefold())
        ) + "\0\0"
        self._launched = True
        duplicated: list[int] = []
        attribute_initialized = False
        attribute_buffer = None
        info = _ProcessInfo()
        process: ProcessHandle | None = None
        resumed = False
        # Inheritable duplicates exist only during this locked, whitelist-only spawn.
        with _SPAWN_LOCK:
            try:
                extra_attributes = self._creation_attributes()
                attribute_count = 1 + len(extra_attributes)
                current = self._api.GetCurrentProcess()
                for descriptor in (stdin_fd, stdout_fd, stderr_fd):
                    duplicate = w.HANDLE()
                    if not self._api.DuplicateHandle(
                        current, msvcrt.get_osfhandle(descriptor), current,
                        ctypes.byref(duplicate), 0, True, 2,
                    ):
                        raise NativeProcessError("STDIO_DUPLICATE_FAILED")
                    duplicated.append(duplicate.value)
                size = ctypes.c_size_t()
                self._api.InitializeProcThreadAttributeList(None, attribute_count, 0, ctypes.byref(size))
                if not 0 < size.value <= 65_536:
                    raise NativeProcessError("PROCESS_ATTRIBUTES_INVALID")
                attribute_buffer = ctypes.create_string_buffer(size.value)
                if not self._api.InitializeProcThreadAttributeList(attribute_buffer, attribute_count, 0, ctypes.byref(size)):
                    raise NativeProcessError("PROCESS_ATTRIBUTES_FAILED")
                attribute_initialized = True
                handles = (w.HANDLE * len(duplicated))(*duplicated)
                if not self._api.UpdateProcThreadAttribute(
                    attribute_buffer, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    handles, ctypes.sizeof(handles), None, None,
                ):
                    raise NativeProcessError("HANDLE_WHITELIST_FAILED")
                for attribute, value in extra_attributes:
                    if not self._api.UpdateProcThreadAttribute(
                        attribute_buffer, 0, attribute, ctypes.byref(value),
                        ctypes.sizeof(value), None, None,
                    ):
                        raise NativeProcessError("PROCESS_SECURITY_ATTRIBUTE_FAILED")
                startup = _StartupInfoEx()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = duplicated
                startup.attributes = ctypes.cast(attribute_buffer, ctypes.c_void_p).value
                command_buffer = ctypes.create_unicode_buffer(command)
                env_buffer = ctypes.create_unicode_buffer(environment_block)
                if not self._api.CreateProcessW(
                    str(executable), command_buffer, None, None, True,
                    _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW | _EXTENDED_STARTUPINFO_PRESENT,
                    env_buffer, str(cwd), ctypes.byref(startup), ctypes.byref(info),
                ):
                    error = NativeProcessError("PROCESS_CREATE_FAILED")
                    error.native_error = ctypes.get_last_error()
                    raise error
                process = ProcessHandle(info.process, info.pid, created=True)
                self._assign(process)
                if not self.contains(process):
                    raise NativeProcessError("JOB_MEMBERSHIP_INVALID", process.pid)
                self._verify_before_resume(process)
                if self._api.ResumeThread(info.thread) != 1:
                    raise NativeProcessError("PROCESS_RESUME_FAILED", process.pid)
                resumed = True
                return process
            finally:
                # Failure must not leave a suspended process behind.
                cleanup_error: NativeProcessError | None = None
                try:
                    if process is not None and not resumed:
                        try:
                            process.terminate_owned()
                        except NativeProcessError:
                            # Retain the exact child handle for caller recovery on
                            # an exceptional OS termination failure; never lose it
                            # and later attempt PID-based adoption.
                            cleanup_error = NativeProcessError(
                                "SUSPENDED_PROCESS_CLEANUP_FAILED", info.pid, process
                            )
                        else:
                            process.close()
                finally:
                    if info.thread:
                        self._api.CloseHandle(info.thread)
                    if attribute_initialized and attribute_buffer is not None:
                        self._api.DeleteProcThreadAttributeList(attribute_buffer)
                    for handle in duplicated:
                        self._api.CloseHandle(handle)
                if cleanup_error is not None:
                    raise cleanup_error

    def terminate(self) -> None:
        if not self._api.TerminateJobObject(self.value, 1):
            raise NativeProcessError("JOB_STOP_FAILED")

    def stop_and_wait(self, timeout_seconds: float = 5) -> None:
        if not 0 <= timeout_seconds <= 10:
            raise ValueError("Invalid job wait.")
        self.terminate()
        deadline = time.monotonic() + timeout_seconds
        while self.member_pids():
            if time.monotonic() >= deadline:
                raise NativeProcessError("JOB_STOP_TIMEOUT")
            time.sleep(0.01)
        self.close()

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._api.CloseHandle(handle)

    def __enter__(self) -> WindowsJob:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
