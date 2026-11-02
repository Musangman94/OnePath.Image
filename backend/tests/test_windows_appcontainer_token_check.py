from __future__ import annotations

import ctypes
import struct
import sys
import threading
import weakref
from unittest import TestCase
from unittest.mock import patch

from app.runtime import win32_jobs
from app.runtime.win32_jobs import NativeProcessError, ProcessHandle
from app.runtime.windows_appcontainer_profiles import _create_owned_profile
from app.runtime import windows_appcontainer_token_check as checks


EXPECTED_SID = 0x7100
ACTUAL_SID = 0x7100
TOKEN_HANDLE = 0x2200
FAKE_SID_BYTES = b"\x01\x01" + (b"\x00" * 6) + struct.pack("<I", 42)


def pointer_bytes(value: int) -> bytes:
    return value.to_bytes(ctypes.sizeof(ctypes.c_void_p), sys.byteorder)


def appcontainer_sid_buffer(sid_bytes=FAKE_SID_BYTES, *, pointer_offset=None, pointer_adjust=0):
    sid_offset = ctypes.sizeof(ctypes.c_void_p) if pointer_offset is None else pointer_offset
    allocation = max(ctypes.sizeof(ctypes.c_void_p), sid_offset + len(sid_bytes))
    owner = ctypes.create_string_buffer(allocation)
    base = ctypes.addressof(owner)
    pointer = base + sid_offset + pointer_adjust
    ctypes.c_void_p.from_buffer(owner).value = pointer
    if 0 <= sid_offset < allocation:
        copy_count = min(len(sid_bytes), allocation - sid_offset)
        ctypes.memmove(base + sid_offset, sid_bytes, copy_count)
    return checks._OwnedTokenInformationBuffer(owner, allocation)


class FakeProfileApi:
    def create_profile(self, _name):
        return 0, EXPECTED_SID

    def free_sid(self, _sid):
        return True

    def delete_profile(self, _name):
        return 0


class FakeTokenApi:
    def __init__(self, information=None, *, token_handle=TOKEN_HANDLE,
                 equal_result=None, close_error=False, open_error=False, invalid_sids=()):
        self.information = information or {
            checks.TOKEN_IS_APPCONTAINER: struct.pack("<I", 1),
            checks.TOKEN_APPCONTAINER_SID: appcontainer_sid_buffer(),
            checks.TOKEN_CAPABILITIES: struct.pack("<I", 0),
        }
        self.token_handle = token_handle
        self.equal_result = equal_result
        self.close_error = close_error
        self.open_error = open_error
        self.invalid_sids = set(invalid_sids)
        self.events = []
        self.sid_buffer_ref = None
        self.sid_buffer_alive_during_compare = False

    def open_process_token(self, process_handle):
        self.events.append(("open", process_handle))
        if self.open_error:
            raise RuntimeError("fake open failure")
        return self.token_handle

    def query_token_information(self, token_handle, information_class):
        self.events.append(("query", token_handle, information_class))
        value = self.information[information_class]
        if isinstance(value, Exception):
            raise value
        if type(value) is checks._OwnedTokenInformationBuffer:
            if information_class == checks.TOKEN_APPCONTAINER_SID:
                self.sid_buffer_ref = weakref.ref(value)
                self.actual_sid_pointer = int.from_bytes(
                    value.copy_bytes()[:ctypes.sizeof(ctypes.c_void_p)], sys.byteorder,
                )
            return value
        if type(value) is not bytes:
            return value
        result = checks._OwnedTokenInformationBuffer.from_bytes(value)
        if information_class == checks.TOKEN_APPCONTAINER_SID:
            self.sid_buffer_ref = weakref.ref(result)
        return result

    def equal_sid(self, expected_sid, actual_sid):
        self.events.append(("equal", expected_sid, actual_sid))
        self.sid_buffer_alive_during_compare = (
            self.sid_buffer_ref is not None and self.sid_buffer_ref() is not None
        )
        if self.equal_result is not None:
            return self.equal_result
        return expected_sid == EXPECTED_SID and actual_sid == getattr(self, "actual_sid_pointer", None)

    def is_valid_sid(self, sid):
        self.events.append(("valid_sid", sid))
        return sid not in self.invalid_sids

    def close_token(self, token_handle):
        self.events.append(("close", token_handle))
        if self.close_error:
            raise RuntimeError("fake close failure")


class WindowsAppContainerTokenCheckTests(TestCase):
    def setUp(self):
        self.profile = _create_owned_profile(
            FakeProfileApi(), name_factory=lambda: "OnePath.CAE.Canary." + "a" * 32,
        )
        self.process = ProcessHandle(0x1100, 321, created=True)

    def check(self, api):
        return checks._check_with_api(api, self.process, self.profile)

    def assert_fixed_error(self, api, code):
        with self.assertRaises(checks.AppContainerTokenCheckError) as caught:
            self.check(api)
        self.assertEqual(caught.exception.code, code)

    def test_valid_token_matches_owned_profile_and_closes_only_token(self):
        api = FakeTokenApi()
        result = self.check(api)
        self.assertEqual(result.schema_version, 1)
        self.assertTrue(result.is_appcontainer)
        self.assertTrue(result.profile_sid_matches)
        self.assertEqual(result.capability_count, 0)
        self.assertFalse(result.sandbox_enforced)
        self.assertTrue(api.sid_buffer_alive_during_compare)
        self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))
        self.assertEqual(self.process.value, 0x1100)  # Caller-owned child handle remains open.
        self.assertEqual(
            [event[2] for event in api.events if event[0] == "query"],
            [checks.TOKEN_IS_APPCONTAINER, checks.TOKEN_APPCONTAINER_SID, checks.TOKEN_CAPABILITIES],
        )

    def test_rejects_process_or_profile_not_from_expected_owned_primitives(self):
        api = FakeTokenApi()
        with self.assertRaises(checks.AppContainerTokenCheckError) as caught:
            checks._check_with_api(api, object(), self.profile)
        self.assertEqual(caught.exception.code, "PROCESS_HANDLE_INVALID")
        self.assertEqual(api.events, [])
        with self.assertRaises(checks.AppContainerTokenCheckError) as caught:
            checks._check_with_api(api, self.process, object())
        self.assertEqual(caught.exception.code, "PROFILE_LEASE_INVALID")
        self.assertEqual(api.events, [])

    def test_rejects_non_appcontainer_and_malformed_boolean_buffer(self):
        cases = (
            ({checks.TOKEN_IS_APPCONTAINER: struct.pack("<I", 0)}, "TOKEN_NOT_APPCONTAINER"),
            ({checks.TOKEN_IS_APPCONTAINER: b"\x01"}, "TOKEN_INFORMATION_INVALID"),
        )
        for changes, code in cases:
            with self.subTest(code=code):
                info = FakeTokenApi().information | changes
                api = FakeTokenApi(info)
                self.assert_fixed_error(api, code)
                self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

    def test_rejects_null_malformed_or_mismatched_profile_sid(self):
        cases = (
            (appcontainer_sid_buffer(pointer_adjust=1000), "TOKEN_SID_OUT_OF_BOUNDS"),
            (appcontainer_sid_buffer(pointer_adjust=-ctypes.sizeof(ctypes.c_void_p)),
             "TOKEN_SID_OUT_OF_BOUNDS"),
            (appcontainer_sid_buffer(FAKE_SID_BYTES[:-1]), "TOKEN_SID_TRUNCATED"),
            (appcontainer_sid_buffer(b"\x01\x10" + (b"\x00" * 6) + (b"\x00" * 64)),
             "TOKEN_SID_INVALID"),
        )
        for sid_info, code in cases:
            with self.subTest(code=code):
                info = FakeTokenApi().information | {checks.TOKEN_APPCONTAINER_SID: sid_info}
                api = FakeTokenApi(info)
                self.assert_fixed_error(api, code)
                self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

        info = FakeTokenApi().information | {checks.TOKEN_APPCONTAINER_SID: appcontainer_sid_buffer()}
        api = FakeTokenApi(info, equal_result=False)
        self.assert_fixed_error(api, "TOKEN_SID_MISMATCH")

        info = FakeTokenApi().information
        api = FakeTokenApi(info, invalid_sids=(ACTUAL_SID,))
        self.assert_fixed_error(api, "TOKEN_SID_INVALID")
        self.assertFalse(any(event[0] == "equal" for event in api.events))

    def test_rejects_present_or_truncated_capabilities(self):
        cases = (
            (struct.pack("<I", 1), "TOKEN_CAPABILITIES_PRESENT"),
            (b"\x00\x00", "TOKEN_INFORMATION_INVALID"),
        )
        for value, code in cases:
            with self.subTest(code=code):
                info = FakeTokenApi().information | {checks.TOKEN_CAPABILITIES: value}
                api = FakeTokenApi(info)
                self.assert_fixed_error(api, code)
                self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

    def test_query_and_sid_compare_failures_close_the_token(self):
        info = FakeTokenApi().information | {
            checks.TOKEN_CAPABILITIES: RuntimeError("fake query failure"),
        }
        api = FakeTokenApi(info)
        self.assert_fixed_error(api, "TOKEN_QUERY_FAILED")
        self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

        api = FakeTokenApi(equal_result=None)
        api.equal_sid = lambda *_: (_ for _ in ()).throw(RuntimeError("compare"))
        self.assert_fixed_error(api, "TOKEN_SID_COMPARE_FAILED")
        self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

    def test_open_or_close_failures_are_fail_closed(self):
        api = FakeTokenApi(open_error=True)
        self.assert_fixed_error(api, "TOKEN_OPEN_FAILED")
        self.assertFalse(any(event[0] == "close" for event in api.events))
        api = FakeTokenApi(close_error=True)
        self.assert_fixed_error(api, "TOKEN_HANDLE_CLOSE_FAILED")
        self.assertEqual(api.events[-1], ("close", TOKEN_HANDLE))

    def test_invalid_or_aliased_token_handle_is_never_closed_as_caller_handle(self):
        for token_handle in (None, 0, True, self.process.value):
            with self.subTest(token_handle=token_handle):
                api = FakeTokenApi(token_handle=token_handle)
                self.assert_fixed_error(api, "TOKEN_HANDLE_INVALID")
                self.assertFalse(any(event[0] == "close" for event in api.events))

    def test_native_entry_is_disabled_before_any_dll_loader(self):
        with patch.object(checks, "_WindowsTokenApi", side_effect=AssertionError("DLL load")):
            with self.assertRaises(checks.AppContainerTokenCheckError) as caught:
                checks.check_appcontainer_token_pre_resume(self.process, self.profile)
        self.assertEqual(caught.exception.code, "OS_EFFECTS_DISABLED")

    def test_profile_sid_remains_live_until_comparison_finishes(self):
        sid_free_entered = threading.Event()
        allow_sid_free = threading.Event()
        token_open_entered = threading.Event()
        allow_token_open = threading.Event()
        close_started = threading.Event()

        class BlockingProfileApi(FakeProfileApi):
            def free_sid(self, sid):
                sid_free_entered.set()
                if not allow_sid_free.wait(2):
                    raise RuntimeError("fake profile cleanup timed out")
                return super().free_sid(sid)

        class BlockingTokenApi(FakeTokenApi):
            def open_process_token(self, process_handle):
                token_open_entered.set()
                if not allow_token_open.wait(2):
                    raise RuntimeError("fake token query timed out")
                return super().open_process_token(process_handle)

        profile_api = BlockingProfileApi()
        profile = _create_owned_profile(
            profile_api, name_factory=lambda: "OnePath.CAE.Canary." + "b" * 32,
        )
        api = BlockingTokenApi()
        check_results = []
        check_errors = []

        def check_token():
            try:
                check_results.append(checks._check_with_api(api, self.process, profile))
            except Exception as exc:
                check_errors.append(exc)

        def close_profile():
            close_started.set()
            profile.close()

        checker = threading.Thread(target=check_token)
        closer = threading.Thread(target=close_profile)
        checker.start()
        self.assertTrue(token_open_entered.wait(1))
        closer.start()
        self.assertTrue(close_started.wait(1))
        self.assertFalse(sid_free_entered.wait(0.05))
        allow_token_open.set()
        checker.join(2)
        self.assertFalse(checker.is_alive())
        self.assertEqual(check_errors, [])
        self.assertEqual(len(check_results), 1)
        self.assertTrue(sid_free_entered.wait(1))
        allow_sid_free.set()
        closer.join(2)
        self.assertFalse(closer.is_alive())

    def test_process_handle_close_waits_until_token_query_releases_its_pin(self):
        token_open_entered = threading.Event()
        allow_token_open = threading.Event()
        close_started = threading.Event()
        close_called = threading.Event()

        class FakeKernelApi:
            def CloseHandle(self, _handle):
                close_called.set()
                return True

        class BlockingTokenApi(FakeTokenApi):
            def open_process_token(self, process_handle):
                token_open_entered.set()
                if not allow_token_open.wait(2):
                    raise RuntimeError("fake token open timed out")
                return super().open_process_token(process_handle)

        api = BlockingTokenApi()
        result = []
        errors = []

        def query():
            try:
                result.append(checks._check_with_api(api, self.process, self.profile))
            except Exception as exc:
                errors.append(exc)

        def close_process():
            close_started.set()
            self.process.close()

        with patch.object(win32_jobs, "_api", return_value=FakeKernelApi()):
            checker = threading.Thread(target=query)
            closer = threading.Thread(target=close_process)
            checker.start()
            self.assertTrue(token_open_entered.wait(1))
            closer.start()
            self.assertTrue(close_started.wait(1))
            self.assertFalse(close_called.wait(0.05))
            allow_token_open.set()
            checker.join(2)
            closer.join(2)
            self.assertFalse(checker.is_alive())
            self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertTrue(close_called.is_set())

    def test_same_thread_close_while_pinned_fails_without_closing_handle(self):
        close_calls = []

        class FakeKernelApi:
            def CloseHandle(self, handle):
                close_calls.append(handle)
                return True

        with patch.object(win32_jobs, "_api", return_value=FakeKernelApi()):
            with self.process.pin() as handle:
                with self.assertRaises(NativeProcessError) as caught:
                    self.process.close()
                self.assertEqual(caught.exception.code, "PROCESS_HANDLE_CLOSE_WHILE_PINNED")
                self.assertEqual(self.process.value, handle)
                self.assertEqual(close_calls, [])
            self.process.close()

        self.assertEqual(close_calls, [0x1100])


if __name__ == "__main__":
    import unittest
    unittest.main()
