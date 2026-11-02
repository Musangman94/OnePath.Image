"""Dormant pre-resume AppContainer token-policy check for an owned child.

This component queries one process token and never resumes, creates, terminates,
or otherwise mutates a process. A successful check is not proof of sandboxing.
Native bindings are lazy and source-gated off; tests use a fake API only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import os
import sys
from typing import Any

from app.runtime.win32_jobs import ProcessHandle
from app.runtime.windows_appcontainer_profiles import WindowsAppContainerProfileLease


TOKEN_QUERY = 0x0008
TOKEN_IS_APPCONTAINER = 29
TOKEN_CAPABILITIES = 30
TOKEN_APPCONTAINER_SID = 31
MAX_TOKEN_INFORMATION_BYTES = 65_536
_NATIVE_TOKEN_CHECK_ENABLED = False
_NATIVE_TOKEN_CHECK_GATE = object()


class AppContainerTokenCheckError(ValueError):
    """Fixed-code fail-closed error; contains no process or token values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _OwnedTokenInformationBuffer:
    """Retains native buffer storage while any embedded SID pointer is compared."""

    __slots__ = ("_owner", "length", "__weakref__")

    def __init__(self, owner: object, length: int):
        if type(length) is not int or not 0 < length <= MAX_TOKEN_INFORMATION_BYTES:
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        try:
            capacity = ctypes.sizeof(owner)
        except (TypeError, ValueError):
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID") from None
        if length > capacity:
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        self._owner = owner
        self.length = length

    @classmethod
    def from_bytes(cls, value: bytes) -> _OwnedTokenInformationBuffer:
        if type(value) is not bytes or not value:
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        owner = ctypes.create_string_buffer(value, len(value))
        return cls(owner, len(value))

    def copy_bytes(self) -> bytes:
        return ctypes.string_at(ctypes.addressof(self._owner), self.length)

    def validate_sid_pointer(self, pointer: int) -> None:
        """Require a complete bounded SID structure inside this retained buffer."""
        if type(pointer) is not int or pointer <= 0:
            raise AppContainerTokenCheckError("TOKEN_SID_INVALID")
        base = ctypes.addressof(self._owner)
        offset = pointer - base
        header_size = ctypes.sizeof(ctypes.c_void_p)
        if (offset < header_size or offset > self.length
                or self.length - offset < 8):
            raise AppContainerTokenCheckError("TOKEN_SID_OUT_OF_BOUNDS")
        data = self.copy_bytes()
        revision, subauthority_count = data[offset], data[offset + 1]
        if revision != 1 or subauthority_count > 15:
            raise AppContainerTokenCheckError("TOKEN_SID_INVALID")
        sid_size = 8 + (subauthority_count * 4)
        if sid_size > self.length - offset:
            raise AppContainerTokenCheckError("TOKEN_SID_TRUNCATED")


@dataclass(frozen=True, slots=True)
class PreResumeAppContainerTokenCheckV1:
    """Narrow token result only; does not attest OS sandbox enforcement."""

    schema_version: int
    is_appcontainer: bool
    profile_sid_matches: bool
    capability_count: int
    sandbox_enforced: bool = False


class _WindowsTokenApi:
    """Lazy native bindings. Instantiation occurs only behind the disabled gate."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise AppContainerTokenCheckError("PLATFORM_UNSUPPORTED")
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._open_process_token = advapi32.OpenProcessToken
        self._open_process_token.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self._open_process_token.restype = w.BOOL
        self._get_token_information = advapi32.GetTokenInformation
        self._get_token_information.argtypes = [
            w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
        ]
        self._get_token_information.restype = w.BOOL
        self._equal_sid = advapi32.EqualSid
        self._equal_sid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._equal_sid.restype = w.BOOL
        self._is_valid_sid = advapi32.IsValidSid
        self._is_valid_sid.argtypes = [ctypes.c_void_p]
        self._is_valid_sid.restype = w.BOOL
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [w.HANDLE]
        self._close_handle.restype = w.BOOL

    def open_process_token(self, process_handle: int) -> int:
        token = w.HANDLE()
        if not self._open_process_token(process_handle, TOKEN_QUERY, ctypes.byref(token)):
            raise AppContainerTokenCheckError("TOKEN_OPEN_FAILED")
        if not token.value:
            raise AppContainerTokenCheckError("TOKEN_HANDLE_INVALID")
        return int(token.value)

    def query_token_information(self, token_handle: int, information_class: int) -> _OwnedTokenInformationBuffer:
        if information_class not in (TOKEN_IS_APPCONTAINER, TOKEN_APPCONTAINER_SID, TOKEN_CAPABILITIES):
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_CLASS_INVALID")
        required = w.DWORD()
        ctypes.set_last_error(0)
        first_ok = self._get_token_information(
            token_handle, information_class, None, 0, ctypes.byref(required),
        )
        first_error = ctypes.get_last_error()
        if (first_ok or first_error != 122 or not 0 < required.value <= MAX_TOKEN_INFORMATION_BYTES):
            raise AppContainerTokenCheckError("TOKEN_QUERY_FAILED")
        owner = ctypes.create_string_buffer(required.value)
        returned = w.DWORD()
        if not self._get_token_information(
            token_handle, information_class, ctypes.cast(owner, ctypes.c_void_p),
            required.value, ctypes.byref(returned),
        ):
            raise AppContainerTokenCheckError("TOKEN_QUERY_FAILED")
        if not 0 < returned.value <= required.value:
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        return _OwnedTokenInformationBuffer(owner, int(returned.value))

    def equal_sid(self, expected_sid: int, actual_sid: int) -> bool:
        return bool(self._equal_sid(expected_sid, actual_sid))

    def is_valid_sid(self, sid: int) -> bool:
        return bool(self._is_valid_sid(sid))

    def close_token(self, token_handle: int) -> None:
        if not self._close_handle(token_handle):
            raise AppContainerTokenCheckError("TOKEN_HANDLE_CLOSE_FAILED")


def _buffer(api: Any, token: int, information_class: int) -> _OwnedTokenInformationBuffer:
    try:
        result = api.query_token_information(token, information_class)
    except AppContainerTokenCheckError:
        raise
    except Exception:
        raise AppContainerTokenCheckError("TOKEN_QUERY_FAILED") from None
    if type(result) is not _OwnedTokenInformationBuffer:
        raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
    return result


def _check_token_with_api(api: Any, process_handle: int, expected_sid: int) -> PreResumeAppContainerTokenCheckV1:
    try:
        token = api.open_process_token(process_handle)
    except AppContainerTokenCheckError:
        raise
    except Exception:
        raise AppContainerTokenCheckError("TOKEN_OPEN_FAILED") from None
    if type(token) is not int or token <= 0 or token == process_handle:
        raise AppContainerTokenCheckError("TOKEN_HANDLE_INVALID")

    failure: AppContainerTokenCheckError | None = None
    result: PreResumeAppContainerTokenCheckV1 | None = None
    try:
        is_appcontainer_info = _buffer(api, token, TOKEN_IS_APPCONTAINER)
        is_appcontainer_bytes = is_appcontainer_info.copy_bytes()
        if len(is_appcontainer_bytes) != ctypes.sizeof(w.DWORD):
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        is_appcontainer = int.from_bytes(is_appcontainer_bytes, "little")
        if is_appcontainer != 1:
            raise AppContainerTokenCheckError("TOKEN_NOT_APPCONTAINER")

        # Keep the owner buffer alive through EqualSid: its PSID field points to
        # SID storage associated with this GetTokenInformation result.
        appcontainer_sid_info = _buffer(api, token, TOKEN_APPCONTAINER_SID)
        sid_bytes = appcontainer_sid_info.copy_bytes()
        if len(sid_bytes) < ctypes.sizeof(ctypes.c_void_p):
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        actual_sid = int.from_bytes(
            sid_bytes[:ctypes.sizeof(ctypes.c_void_p)], sys.byteorder,
        )
        if actual_sid <= 0:
            raise AppContainerTokenCheckError("TOKEN_SID_INVALID")
        appcontainer_sid_info.validate_sid_pointer(actual_sid)
        try:
            valid_expected = api.is_valid_sid(expected_sid)
            valid_actual = api.is_valid_sid(actual_sid)
        except Exception:
            raise AppContainerTokenCheckError("TOKEN_SID_COMPARE_FAILED") from None
        if valid_expected is not True or valid_actual is not True:
            raise AppContainerTokenCheckError("TOKEN_SID_INVALID")
        try:
            same_sid = api.equal_sid(expected_sid, actual_sid)
        except Exception:
            raise AppContainerTokenCheckError("TOKEN_SID_COMPARE_FAILED") from None
        if same_sid is not True:
            raise AppContainerTokenCheckError("TOKEN_SID_MISMATCH")

        capabilities_info = _buffer(api, token, TOKEN_CAPABILITIES)
        capabilities_bytes = capabilities_info.copy_bytes()
        if len(capabilities_bytes) < ctypes.sizeof(w.DWORD):
            raise AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
        capability_count = int.from_bytes(capabilities_bytes[:4], "little")
        if capability_count != 0:
            raise AppContainerTokenCheckError("TOKEN_CAPABILITIES_PRESENT")
        result = PreResumeAppContainerTokenCheckV1(
            schema_version=1,
            is_appcontainer=True,
            profile_sid_matches=True,
            capability_count=capability_count,
        )
    except AppContainerTokenCheckError as exc:
        failure = exc
    except Exception:
        failure = AppContainerTokenCheckError("TOKEN_INFORMATION_INVALID")
    finally:
        try:
            api.close_token(token)
        except Exception:
            raise AppContainerTokenCheckError("TOKEN_HANDLE_CLOSE_FAILED") from None
    if failure is not None:
        raise failure
    if result is None:
        raise AppContainerTokenCheckError("TOKEN_CHECK_FAILED")
    return result


def _check_with_api(
    api: Any,
    process: ProcessHandle,
    profile: WindowsAppContainerProfileLease,
) -> PreResumeAppContainerTokenCheckV1:
    """Fakeable path; inputs are caller-owned suspended-child/profile leases."""
    if type(process) is not ProcessHandle:
        raise AppContainerTokenCheckError("PROCESS_HANDLE_INVALID")
    if type(profile) is not WindowsAppContainerProfileLease:
        raise AppContainerTokenCheckError("PROFILE_LEASE_INVALID")
    # Keep the profile lease's SID allocation alive through IsValidSid/EqualSid;
    # close() uses this same condition before releasing the SID pointer.
    try:
        state = profile._WindowsAppContainerProfileLease__state
        with state.condition:
            if state.status != "open":
                raise AppContainerTokenCheckError("PROFILE_SID_UNAVAILABLE")
            expected_sid = state.binding[2]
            if type(expected_sid) is not int or expected_sid <= 0:
                raise AppContainerTokenCheckError("PROFILE_SID_UNAVAILABLE")
            # Keep the exact process handle open while OpenProcessToken and all
            # queries use it; ProcessHandle.close() waits for this pin.
            with process.pin() as process_handle:
                return _check_token_with_api(api, process_handle, expected_sid)
    except AppContainerTokenCheckError:
        raise
    except Exception:
        raise AppContainerTokenCheckError("PROFILE_SID_UNAVAILABLE") from None


def check_appcontainer_token_pre_resume(
    process: ProcessHandle,
    profile: WindowsAppContainerProfileLease,
    *,
    internal_gate: object | None = None,
) -> PreResumeAppContainerTokenCheckV1:
    """Native token query, gated off; call only before any intended resume.

    The supplied ProcessHandle type does not prove suspension or Job membership.
    This function does not resume the child or establish sandbox isolation. The
    source-controlled gate is disabled.
    """
    if (not _NATIVE_TOKEN_CHECK_ENABLED or internal_gate is not _NATIVE_TOKEN_CHECK_GATE):
        raise AppContainerTokenCheckError("OS_EFFECTS_DISABLED")
    api = _WindowsTokenApi()
    return _check_with_api(api, process, profile)


__all__ = [
    "AppContainerTokenCheckError",
    "PreResumeAppContainerTokenCheckV1",
    "check_appcontainer_token_pre_resume",
]
