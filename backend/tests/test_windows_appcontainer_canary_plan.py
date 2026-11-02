from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from app.runtime.windows_appcontainer_canary_plan import (
    CanaryJobLimitsV1,
    POLICY_REF,
    WindowsCanaryPlanError,
    build_windows_appcontainer_canary_plan,
)


class WindowsAppContainerCanaryPlanTests(unittest.TestCase):
    def plan(self, **overrides):
        value = {
            "canary_executable_path": "C:\\synthetic\\bin\\cae-canary.exe",
            "canary_executable_sha256": "a" * 64,
            "temporary_profile_name": "OnePath.CAE.Canary." + "b" * 32,
            "fixture_root": "C:\\synthetic\\fixture",
            "fixture_read_paths": ("input/known.txt", "manifest.json"),
            "fixture_write_paths": ("scratch/result.txt",),
            "job_limits": CanaryJobLimitsV1(),
        }
        value.update(overrides)
        return build_windows_appcontainer_canary_plan(**value)

    def test_plan_is_deterministic_inert_and_explicitly_not_enforced(self):
        first = self.plan(fixture_read_paths=("input/known.txt", "manifest.json"))
        second = self.plan(fixture_read_paths=("manifest.json", "input/known.txt"))
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        value = json.loads(first.canonical_bytes())
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(value["policyRef"], POLICY_REF)
        self.assertEqual(value["purpose"], "inert-canary-only")
        self.assertEqual(value["capabilities"], [])
        self.assertFalse(value["networkAccess"])
        self.assertFalse(value["osEffectsEnabled"])
        self.assertEqual(value["inheritedHandleNames"], ["stdin", "stdout", "stderr"])
        self.assertEqual(value["fixture"]["absoluteRoot"], "C:\\synthetic\\fixture")
        self.assertIn("verify-token-is-appcontainer", value["expectedIdentityPolicy"])
        self.assertIn("never-launch-from-plan-alone", value["expectedIdentityPolicy"])
        self.assertIn("generate-profile-name-with-csprng-and-fail-on-collision",
                      value["expectedIdentityPolicy"])
        self.assertIn("verify-reparse-safe-containment-for-every-fixture-path",
                      first.fixture_access_policy)
        self.assertNotIn("owner", first.canonical_bytes().decode("utf-8").lower())
        with self.assertRaises(FrozenInstanceError):
            first.fixture_root = "C:\\other"

    def test_build_is_importable_and_constructible_without_windows_apis(self):
        # All values are synthetic; construction performs no path or OS access.
        self.assertEqual(self.plan().os_effects_enabled, False)

    def test_rejects_unsupported_version_and_non_fixed_policy_or_effect_gate(self):
        base = self.plan()
        invalid = (
            {"schema_version": 2}, {"policy_ref": "other"},
            {"capabilities": ("internetClient",)}, {"network_access": True},
            {"os_effects_enabled": True},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(WindowsCanaryPlanError):
                replace(base, **changes)

    def test_rejects_bad_absolute_executable_or_fixture_root(self):
        for field in ("canary_executable_path", "fixture_root"):
            for path in (
                "relative\\path", "C:/slash/path", "C:\\bad\\..\\path",
                "C:\\bad.\\path", "C:\\bad \\path", "C:\\CON\\path",
                "C:\\COM¹\\path", "C:\\LPT².txt\\path",
                "C:\\bad:name", "C:\\bad?name", "C:\\e\u0301",
            ):
                with self.subTest(field=field, path=path), self.assertRaises(WindowsCanaryPlanError):
                    self.plan(**{field: path})
        for path in ("C:\\", "C:\\synthetic\\python.exe"):
            with self.subTest(canary=path), self.assertRaises(WindowsCanaryPlanError):
                self.plan(canary_executable_path=path)

    def test_rejects_malformed_digest_and_temporary_profile_name(self):
        for digest in ("", "A" * 64, "0" * 63, "g" * 64):
            with self.subTest(digest=digest), self.assertRaises(WindowsCanaryPlanError):
                self.plan(canary_executable_sha256=digest)
        for name in (
            "OnePath.CAE.Canary.short", "onepath.CAE.Canary." + "c" * 32,
            "OnePath.CAE.Canary." + "C" * 32, "OnePath.CAE.Canary." + "c" * 31,
        ):
            with self.subTest(name=name), self.assertRaises(WindowsCanaryPlanError):
                self.plan(temporary_profile_name=name)

    def test_rejects_bad_fixture_paths_collisions_and_oversized_sets(self):
        for paths in (
            (), ("../escape.txt",), ("C:/absolute.txt",), ("a\\b.txt",),
            ("a//b.txt",), ("a/./b.txt",), ("a/../b.txt",),
            ("NUL.txt",), ("COM¹.txt",), ("folder/LPT³.log",),
            ("trailing./x",), ("trailing /x",),
            ("a.txt", "A.txt"), ("x" * 241,), tuple(f"f{i}.txt" for i in range(17)),
        ):
            with self.subTest(paths=paths), self.assertRaises(WindowsCanaryPlanError):
                self.plan(fixture_read_paths=paths)
        with self.assertRaises(WindowsCanaryPlanError) as caught:
            self.plan(fixture_read_paths=("scratch/result.txt",))
        self.assertEqual(caught.exception.code, "FIXTURE_PATH_COLLISION")
        with self.assertRaises(WindowsCanaryPlanError) as caught:
            self.plan(fixture_read_paths=("scratch",))
        self.assertEqual(caught.exception.code, "FIXTURE_PATH_COLLISION")

    def test_rejects_non_finite_or_mutable_job_policy_and_handles(self):
        invalid_limits = (
            {"cpu_seconds": 0}, {"wall_seconds": 61},
            {"memory_bytes": 512 * 1024 * 1024 + 1}, {"active_processes": 2},
            {"kill_on_close": False}, {"breakaway_allowed": True},
            {"cpu_seconds": True},
        )
        for changes in invalid_limits:
            with self.subTest(changes=changes), self.assertRaises(WindowsCanaryPlanError):
                self.plan(job_limits=CanaryJobLimitsV1(**changes))


if __name__ == "__main__":
    unittest.main()
