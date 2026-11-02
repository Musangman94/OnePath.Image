from __future__ import annotations

import json
import unittest

from app.services.cae_source_capture_scope import (
    SCOPE_SCHEMA_VERSION,
    SourceCaptureScopeError,
    SourceCaptureScopeV1,
)
from app.services.cae_source_files import SourceReadLimits
from app.services.cae_source_provenance import BUILTIN_SOURCE_PROFILE_V1_SHA256
from app.storage.cae_source_approvals import RootIdentity


class SourceCaptureScopeTests(unittest.TestCase):
    def scope(self, **overrides):
        value = {
            "schema_version": SCOPE_SCHEMA_VERSION,
            "root_path": "Z:\\synthetic\\missing-root",
            "root_identity": RootIdentity("windows_ntfs", 11, 29),
            "relative_paths": ("src/app.py", "README.md"),
            "limits": SourceReadLimits(8, 4096, 8192),
        }
        value.update(overrides)
        return SourceCaptureScopeV1(**value)

    def test_canonical_binding_is_sorted_deterministic_and_metadata_only(self):
        first = self.scope(relative_paths=("src/app.py", "README.md"))
        second = self.scope(relative_paths=("README.md", "src/app.py"))
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.sha256, second.sha256)
        value = json.loads(first.canonical_bytes())
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(value["relativePaths"], ["README.md", "src/app.py"])
        self.assertEqual(value["policyRef"]["sha256"], BUILTIN_SOURCE_PROFILE_V1_SHA256)
        self.assertEqual(value["root"]["absolutePath"], "Z:\\synthetic\\missing-root")
        encoded = first.canonical_bytes().decode("utf-8")
        for forbidden in ("sourceSha256", "origin", "capturedBytes", "ownerId", "sessionId"):
            self.assertNotIn(forbidden, encoded)
        exposed = first.policy_ref
        exposed["id"] = "changed"
        self.assertEqual(first.policy_ref["sha256"], BUILTIN_SOURCE_PROFILE_V1_SHA256)

    def test_accepts_posix_scope_without_touching_filesystem(self):
        value = self.scope(
            root_path="/synthetic/does-not-exist",
            root_identity=RootIdentity("posix", 0, 1),
            relative_paths=("src/main.py",),
        )
        self.assertEqual(value.root_path, "/synthetic/does-not-exist")

    def test_rejects_unsupported_or_non_integer_versions(self):
        for version in (2, True, 1.0):
            with self.subTest(version=version), self.assertRaises(SourceCaptureScopeError):
                self.scope(schema_version=version)

    def test_rejects_invalid_root_path_or_platform_identity(self):
        cases = (
            {"root_path": "relative/root"},
            {"root_path": "C:/wrong/slashes"},
            {"root_path": "//server/share"},
            {"root_identity": RootIdentity("windows_ntfs", True, 29)},
            {"root_identity": RootIdentity("windows_ntfs", 11, -1)},
            {"root_identity": RootIdentity("unknown", 11, 29)},
            {"root_identity": RootIdentity("posix", 11, 29)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(SourceCaptureScopeError):
                self.scope(**overrides)

    def test_rejects_noncanonical_windows_root_components(self):
        roots = (
            "Z:\\src\\file.py:stream",
            "Z:\\folder.",
            "Z:\\folder ",
            "Z:\\CON",
            "Z:\\src\\con.txt",
            "Z:\\bad?name",
            "Z:\\bad<name",
            "Z:\\bad\"name",
            "Z:\\bad|name",
            "Z:\\bad*name",
            "Z:\\bad\x01name",
            "Z:\\e\u0301",
            "Z:\\src\\..\\other",
            "Z:\\src\\\\other",
        )
        for root_path in roots:
            with self.subTest(root_path=root_path), self.assertRaises(SourceCaptureScopeError):
                self.scope(root_path=root_path)

    def test_accepts_canonical_windows_drive_roots(self):
        for root_path in ("C:\\", "Z:\\source", "D:\\Work\\OnePath"):
            with self.subTest(root_path=root_path):
                scope = self.scope(root_path=root_path)
                self.assertEqual(scope.root_path, root_path)

    def test_rejects_list_subclass_with_misreported_length(self):
        class MisreportedList(list):
            def __len__(self):
                return 1

        paths = MisreportedList(["src/one.py", "src/two.py"])
        with self.assertRaises(SourceCaptureScopeError) as caught:
            self.scope(relative_paths=paths, limits=SourceReadLimits(1, 4096, 8192))
        self.assertEqual(caught.exception.code, "INVALID_PATHS")

    def test_unhashable_platform_uses_fixed_identity_validation_error(self):
        with self.assertRaises(SourceCaptureScopeError) as caught:
            self.scope(root_identity=RootIdentity([], 11, 29))
        self.assertEqual(caught.exception.code, "INVALID_ROOT_IDENTITY")

    def test_rejects_escape_malformed_excluded_and_unapproved_paths(self):
        paths = (
            ("../src/app.py",), ("/src/app.py",), ("src\\app.py",),
            ("C:/src/app.py",), ("src//app.py",), ("src/./app.py",),
            ("secrets/key.py",), ("vendor/lib.py",), (".hidden/app.py",),
            ("src/image.png",), ("src/e\u0301.py",),
        )
        for relative_paths in paths:
            with self.subTest(relative_paths=relative_paths), self.assertRaises(SourceCaptureScopeError):
                self.scope(relative_paths=relative_paths)

    def test_rejects_duplicate_casefold_and_ancestor_collisions(self):
        cases = (
            ("src/app.py", "src/APP.py"),
            ("src/app.py", "src/app.py"),
            ("src/app.py", "src/app.py/child.py"),
            ("src/app.py/child.py", "src/app.py"),
        )
        for relative_paths in cases:
            with self.subTest(relative_paths=relative_paths), self.assertRaises(SourceCaptureScopeError):
                self.scope(relative_paths=relative_paths)

    def test_rejects_empty_or_over_limit_path_sets(self):
        for relative_paths, limits in (
            ((), SourceReadLimits(8, 4096, 8192)),
            (("a.py",) * 9, SourceReadLimits(8, 4096, 8192)),
        ):
            with self.subTest(relative_paths=relative_paths), self.assertRaises(SourceCaptureScopeError):
                self.scope(relative_paths=relative_paths, limits=limits)

    def test_rejects_invalid_or_profile_exceeding_limits(self):
        def forged_limits(**changes):
            value = SourceReadLimits(1, 1, 1)
            for name, replacement in changes.items():
                object.__setattr__(value, name, replacement)
            return value

        cases = (
            forged_limits(max_files=0),
            forged_limits(max_file_bytes=0),
            forged_limits(max_file_bytes=2, max_total_bytes=1),
            forged_limits(max_files=33),
            forged_limits(max_file_bytes=1_048_577, max_total_bytes=2_000_000),
            forged_limits(max_total_bytes=4_194_305),
            forged_limits(max_files=True),
        )
        for limits in cases:
            with self.subTest(limits=limits), self.assertRaises(SourceCaptureScopeError):
                self.scope(limits=limits)


if __name__ == "__main__":
    unittest.main()
