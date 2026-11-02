"""Dormant per-run AppContainer profile lifecycle for a Windows canary.

The native entry point is disabled in source and requires an internal gate.
The fake-API orchestration is testable without loading Windows DLLs. This is
profile lifecycle only; it neither launches a process nor enforces a sandbox.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import os
import re
import secrets
import threading
from typing import Any


_PROFILE_PREFIX = "OnePath.CAE.Canary."
_PROFILE_NAME = re.compile(r"^OnePath\.CAE\.Canary\.[0-9a-f]{32}$")
_S_OK = 0
_HRESULT_ERROR_ALREADY_EXISTS = -2_147_024_713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
_CREATE_ATTEMPTS = 3

# Explicit source-controlled effect gate. It is intentionally off; a reviewed
# internal integration must change the gate and still pass its private token.
_NATIVE_PROFILE_EFFECTS_ENABLED = False
_NATIVE_PROFILE_EFFECT_GATE = object()
_LEASE_CONSTRUCTOR_TOKEN = object()


class WindowsAppContainerProfileError(RuntimeError):
    """Fixed-code lifecycle error with no profile name or native detail."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _WindowsProfileApi:
    """Lazy ctypes bindings to documented APIs; instantiate only after the gate."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsAppContainerProfileError("PLATFORM_UNSUPPORTED")
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._create = userenv.CreateAppContainerProfile
        self._create.argtypes = [
            w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._create.restype = ctypes.c_long  # HRESULT is a signed 32-bit LONG.
        self._delete = userenv.DeleteAppContainerProfile
        self._delete.argtypes = [w.LPCWSTR]
        self._delete.restype = ctypes.c_long
        self._free_sid = advapi32.FreeSid
        self._free_sid.argtypes = [ctypes.c_void_p]
        self._free_sid.restype = ctypes.c_void_p

    def create_profile(self, name: str) -> tuple[int, int | None]:
        sid = ctypes.c_void_p()
        result = int(self._create(
            name, "OnePath CAE Canary", "Temporary canary profile; no network capabilities.",
            None, 0, ctypes.byref(sid),
        ))
        return result, sid.value

    def delete_profile(self, name: str) -> int:
        return int(self._delete(name))

    def free_sid(self, sid: int) -> bool:
        # FreeSid returns NULL on success and the original pointer on failure.
        return not bool(self._free_sid(ctypes.c_void_p(sid)))


def _new_profile_name() -> str:
    """Generate a fresh allowed name. Uniqueness is decided by Create, never assumed."""
    name = _PROFILE_PREFIX + secrets.token_hex(16)
    if not _PROFILE_NAME.fullmatch(name) or len(name) > 64:
        raise WindowsAppContainerProfileError("PROFILE_NAME_INVALID")
    return name


class _ProfileOwnershipState:
    """Private mutable lifecycle state around an immutable ownership binding."""

    __slots__ = ("binding", "condition", "status", "cleanup_uncertain")

    def __init__(self, api: Any, name: str, sid: int):
        self.binding = (api, name, sid)
        self.condition = threading.Condition()
        self.status = "open"
        self.cleanup_uncertain = False

    def __setattr__(self, name: str, value: object) -> None:
        if name == "binding" and hasattr(self, "binding"):
            raise AttributeError("Profile ownership binding is immutable.")
        object.__setattr__(self, name, value)


class WindowsAppContainerProfileLease:
    """Opaque lease for exactly one profile created by this call.

    Ordinary construction and retargeting are blocked; this is an API misuse
    guard, not a Python security boundary. This module opens no profile-storage
    handles. A future controller must stop its worker and close such handles
    before close(). Cleanup is attempted once and never retried here.
    """

    __slots__ = ("__state",)

    def __init__(self, api: Any, name: str, sid: int, *, _ownership_token: object | None = None):
        if _ownership_token is not _LEASE_CONSTRUCTOR_TOKEN:
            raise WindowsAppContainerProfileError("PROFILE_LEASE_NOT_OWNED")
        object.__setattr__(self, "_WindowsAppContainerProfileLease__state",
                           _ProfileOwnershipState(api, name, sid))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Profile lease bindings are immutable.")

    @property
    def name(self) -> str:
        return self.__state.binding[1]

    @property
    def sid(self) -> int:
        with self.__state.condition:
            if self.__state.status != "open":
                raise WindowsAppContainerProfileError("PROFILE_SID_RELEASED")
            return self.__state.binding[2]

    @property
    def cleanup_uncertain(self) -> bool:
        with self.__state.condition:
            return self.__state.cleanup_uncertain

    def close(self) -> None:
        """Release SID then delete once; concurrent callers wait for the same result."""
        state = self.__state
        with state.condition:
            if state.status == "closing":
                state.condition.wait_for(lambda: state.status != "closing")
            if state.status == "closed":
                return
            if state.status == "uncertain":
                raise WindowsAppContainerProfileError("PROFILE_CLEANUP_UNCERTAIN")
            state.status = "closing"
            api, name, sid = state.binding

        release_ok = False
        delete_ok = False
        try:
            try:
                release_ok = api.free_sid(sid)
            except Exception:
                release_ok = False
            try:
                # Ownership is established only by S_OK in _create_owned_profile.
                delete_ok = api.delete_profile(name) == _S_OK
            except Exception:
                delete_ok = False
        finally:
            with state.condition:
                state.cleanup_uncertain = not (release_ok and delete_ok)
                state.status = "uncertain" if state.cleanup_uncertain else "closed"
                state.condition.notify_all()
        if state.cleanup_uncertain:
            raise WindowsAppContainerProfileError("PROFILE_CLEANUP_UNCERTAIN")

    def __enter__(self) -> WindowsAppContainerProfileLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _create_owned_profile(api: Any, *, name_factory=_new_profile_name) -> WindowsAppContainerProfileLease:
    """Private fakeable orchestration; never treats a collision as ownership."""
    for _ in range(_CREATE_ATTEMPTS):
        name = name_factory()
        if type(name) is not str or not _PROFILE_NAME.fullmatch(name) or len(name) > 64:
            raise WindowsAppContainerProfileError("PROFILE_NAME_INVALID")
        try:
            result = api.create_profile(name)
        except Exception:
            # The API outcome may be unknown; do not delete by guessed name.
            raise WindowsAppContainerProfileError("PROFILE_CREATE_UNCERTAIN") from None
        if type(result) is not tuple or len(result) != 2:
            raise WindowsAppContainerProfileError("PROFILE_CREATE_UNCERTAIN")
        hresult, sid = result
        if type(hresult) is not int:
            raise WindowsAppContainerProfileError("PROFILE_CREATE_UNCERTAIN")
        if hresult == _HRESULT_ERROR_ALREADY_EXISTS:
            continue
        if hresult != _S_OK:
            # On any other result, ownership is unproven; never delete.
            raise WindowsAppContainerProfileError("PROFILE_CREATE_UNCERTAIN")
        if type(sid) is not int or sid <= 0:
            # S_OK proves this call created the named profile, so one exact-name
            # cleanup is safe even though the malformed result cannot form a lease.
            try:
                deleted = api.delete_profile(name) == _S_OK
            except Exception:
                deleted = False
            if not deleted:
                raise WindowsAppContainerProfileError("PROFILE_CLEANUP_UNCERTAIN")
            raise WindowsAppContainerProfileError("PROFILE_CREATE_INVALID_RESULT")
        return WindowsAppContainerProfileLease(
            api, name, sid, _ownership_token=_LEASE_CONSTRUCTOR_TOKEN,
        )
    raise WindowsAppContainerProfileError("PROFILE_NAME_COLLISION_LIMIT")


def create_windows_appcontainer_profile(*, internal_gate: object | None = None) -> WindowsAppContainerProfileLease:
    """Native OS-effect entry. Disabled until reviewed source explicitly enables it."""
    if (not _NATIVE_PROFILE_EFFECTS_ENABLED
            or internal_gate is not _NATIVE_PROFILE_EFFECT_GATE):
        raise WindowsAppContainerProfileError("OS_EFFECTS_DISABLED")
    api = _WindowsProfileApi()
    return _create_owned_profile(api)


__all__ = [
    "WindowsAppContainerProfileError",
    "WindowsAppContainerProfileLease",
    "create_windows_appcontainer_profile",
]
