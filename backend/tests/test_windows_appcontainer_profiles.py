from __future__ import annotations

import importlib
import threading
from unittest import TestCase
from unittest.mock import patch

from app.runtime import windows_appcontainer_profiles as profiles


def profile_name(suffix: str) -> str:
    return "OnePath.CAE.Canary." + suffix * 32


class FakeProfileApi:
    def __init__(self, results=(), *, free_result=True, delete_results=()):
        self.results = list(results)
        self.free_result = free_result
        self.delete_results = list(delete_results)
        self.events = []

    def create_profile(self, name):
        self.events.append(("create", name))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def free_sid(self, sid):
        self.events.append(("free", sid))
        return self.free_result

    def delete_profile(self, name):
        self.events.append(("delete", name))
        return self.delete_results.pop(0) if self.delete_results else profiles._S_OK


class WindowsAppContainerProfileTests(TestCase):
    def test_import_and_native_entry_do_not_load_dlls_when_gate_is_disabled(self):
        with patch.object(profiles, "_WindowsProfileApi", side_effect=AssertionError("DLL load")):
            with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
                profiles.create_windows_appcontainer_profile()
        self.assertEqual(caught.exception.code, "OS_EFFECTS_DISABLED")
        self.assertIs(importlib.import_module("app.runtime.windows_appcontainer_profiles"), profiles)

    def test_csprng_profile_name_has_allowed_bounded_format(self):
        with patch.object(profiles.secrets, "token_hex", return_value="d" * 32) as token_hex:
            value = profiles._new_profile_name()
        token_hex.assert_called_once_with(16)
        self.assertEqual(value, profile_name("d"))
        self.assertLessEqual(len(value), 64)

    def test_create_success_returns_owned_lease_and_cleanup_is_ordered_once(self):
        api = FakeProfileApi([(profiles._S_OK, 1234)])
        lease = profiles._create_owned_profile(api, name_factory=lambda: profile_name("a"))
        self.assertEqual(lease.sid, 1234)
        self.assertEqual(api.events, [("create", profile_name("a"))])
        lease.close()
        lease.close()
        self.assertEqual(api.events, [
            ("create", profile_name("a")), ("free", 1234), ("delete", profile_name("a")),
        ])
        self.assertFalse(lease.cleanup_uncertain)
        with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
            _ = lease.sid
        self.assertEqual(caught.exception.code, "PROFILE_SID_RELEASED")

    def test_lease_binding_cannot_be_forged_or_retargeted_by_ordinary_use(self):
        api = FakeProfileApi([(profiles._S_OK, 222)])
        lease = profiles._create_owned_profile(api, name_factory=lambda: profile_name("a"))
        with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
            profiles.WindowsAppContainerProfileLease(api, profile_name("b"), 333)
        self.assertEqual(caught.exception.code, "PROFILE_LEASE_NOT_OWNED")
        with self.assertRaises(AttributeError):
            lease.name = profile_name("b")
        with self.assertRaises(AttributeError):
            lease.sid = 333
        state = lease._WindowsAppContainerProfileLease__state
        with self.assertRaises(AttributeError):
            state.binding = (api, profile_name("b"), 333)
        lease.close()
        self.assertIn(("delete", profile_name("a")), api.events)
        self.assertNotIn(("delete", profile_name("b")), api.events)

    def test_concurrent_close_waits_and_calls_free_delete_exactly_once(self):
        entered_free = threading.Event()
        allow_free = threading.Event()
        second_done = threading.Event()

        class BlockingApi(FakeProfileApi):
            def free_sid(self, sid):
                self.events.append(("free", sid))
                entered_free.set()
                if not allow_free.wait(2):
                    raise RuntimeError("test release timed out")
                return True

        api = BlockingApi([(profiles._S_OK, 444)])
        lease = profiles._create_owned_profile(api, name_factory=lambda: profile_name("4"))
        first_errors = []
        second_errors = []

        def close_first():
            try:
                lease.close()
            except Exception as exc:
                first_errors.append(exc)

        def close_second():
            try:
                lease.close()
            except Exception as exc:
                second_errors.append(exc)
            finally:
                second_done.set()

        first = threading.Thread(target=close_first)
        second = threading.Thread(target=close_second)
        first.start()
        self.assertTrue(entered_free.wait(1))
        second.start()
        self.assertFalse(second_done.wait(0.05))
        allow_free.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertEqual(api.events, [
            ("create", profile_name("4")), ("free", 444), ("delete", profile_name("4")),
        ])

    def test_collision_never_adopts_or_deletes_and_retries_a_new_random_name(self):
        api = FakeProfileApi([
            (profiles._HRESULT_ERROR_ALREADY_EXISTS, None),
            (profiles._S_OK, 5678),
        ])
        names = iter((profile_name("b"), profile_name("c")))
        lease = profiles._create_owned_profile(api, name_factory=lambda: next(names))
        self.assertEqual(api.events, [
            ("create", profile_name("b")), ("create", profile_name("c")),
        ])
        self.assertEqual(lease.name, profile_name("c"))
        lease.close()
        self.assertNotIn(("delete", profile_name("b")), api.events)

    def test_collision_exhaustion_never_deletes_any_name(self):
        api = FakeProfileApi([(profiles._HRESULT_ERROR_ALREADY_EXISTS, None)] * 3)
        names = iter(profile_name(ch) for ch in ("d", "e", "f"))
        with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
            profiles._create_owned_profile(api, name_factory=lambda: next(names))
        self.assertEqual(caught.exception.code, "PROFILE_NAME_COLLISION_LIMIT")
        self.assertEqual([event[0] for event in api.events], ["create"] * 3)

    def test_unknown_hresult_or_api_exception_never_deletes_or_adopts(self):
        for result in ((-1, None), RuntimeError("native outcome ambiguous")):
            with self.subTest(result=result):
                api = FakeProfileApi([result])
                with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
                    profiles._create_owned_profile(api, name_factory=lambda: profile_name("1"))
                self.assertEqual(caught.exception.code, "PROFILE_CREATE_UNCERTAIN")
                self.assertEqual([event[0] for event in api.events], ["create"])

    def test_s_ok_with_invalid_sid_cleans_only_proven_owned_name_once(self):
        api = FakeProfileApi([(profiles._S_OK, None)])
        with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
            profiles._create_owned_profile(api, name_factory=lambda: profile_name("2"))
        self.assertEqual(caught.exception.code, "PROFILE_CREATE_INVALID_RESULT")
        self.assertEqual(api.events, [
            ("create", profile_name("2")), ("delete", profile_name("2")),
        ])

    def test_free_or_delete_failure_is_uncertain_and_never_retried(self):
        cases = (
            {"free_result": False},
            {"delete_results": [-1]},
        )
        for options in cases:
            with self.subTest(options=options):
                api = FakeProfileApi([(profiles._S_OK, 999)], **options)
                lease = profiles._create_owned_profile(api, name_factory=lambda: profile_name("3"))
                with self.assertRaises(profiles.WindowsAppContainerProfileError) as caught:
                    lease.close()
                self.assertEqual(caught.exception.code, "PROFILE_CLEANUP_UNCERTAIN")
                first_events = list(api.events)
                self.assertTrue(lease.cleanup_uncertain)
                with self.assertRaises(profiles.WindowsAppContainerProfileError):
                    lease.close()
                self.assertEqual(api.events, first_events)


if __name__ == "__main__":
    import unittest
    unittest.main()
