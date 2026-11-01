import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.schemas.cae import SourceFileEntryV1  # noqa: E402
from app.services import cae_source_files as source_files  # noqa: E402


def _entry(relative_path, content, *, size=None, digest=None):
    return SourceFileEntryV1(
        path=relative_path,
        sizeBytes=len(content) if size is None else size,
        sha256=hashlib.sha256(content).hexdigest() if digest is None else digest,
        origin="tracked",
    )


class CaeSourceFileReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-reader-")
        self.temp_root = Path(self.temp.name).resolve(strict=True)
        self.root = self.temp_root / "approved-root"
        self.root.mkdir()
        self.identity = source_files.identify_source_root(self.root)
        self.limits = source_files.SourceReadLimits(
            max_files=32, max_file_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024,
        )

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, entries, *, root=None, identity=None, limits=None):
        return source_files.capture_source_bytes(
            self.root if root is None else root,
            self.identity if identity is None else identity,
            entries,
            self.limits if limits is None else limits,
        )

    def inspect(self, paths, *, root=None, identity=None, limits=None):
        return source_files.inspect_selected_source_files(
            self.root if root is None else root,
            self.identity if identity is None else identity,
            paths,
            self.limits if limits is None else limits,
        )

    def test_binary_empty_and_multiple_file_roundtrip(self):
        binary = b"\x00\xff\x80\r\n"
        (self.root / "nested").mkdir()
        (self.root / "nested" / "binary.dat").write_bytes(binary)
        (self.root / "empty.txt").write_bytes(b"")
        entries = [_entry("nested/binary.dat", binary), _entry("empty.txt", b"")]
        captured = self.capture(entries)
        self.assertEqual(captured, {"nested/binary.dat": binary, "empty.txt": b""})
        self.assertIs(type(captured["nested/binary.dat"]), bytes)

    def test_inspection_measures_empty_binary_and_multiple_files_deterministically(self):
        binary = b"\x00\xff\x80\r\n"
        (self.root / "nested").mkdir()
        (self.root / "nested" / "binary.dat").write_bytes(binary)
        (self.root / "empty.txt").write_bytes(b"")
        selected = ["nested/binary.dat", "empty.txt"]
        measured = self.inspect(selected)
        reversed_result = self.inspect(list(reversed(selected)))
        self.assertEqual(measured.entries, reversed_result.entries)
        self.assertEqual(measured.file_bytes["nested/binary.dat"], binary)
        self.assertEqual(measured.file_bytes["empty.txt"], b"")
        self.assertEqual(
            [(entry.path, entry.size_bytes, entry.sha256) for entry in measured.entries],
            [("empty.txt", 0, hashlib.sha256(b"").hexdigest()),
             ("nested/binary.dat", len(binary), hashlib.sha256(binary).hexdigest())],
        )
        self.assertFalse(hasattr(measured.entries[0], "origin"))

    def test_inspection_snapshots_paths_entries_and_readonly_bytes(self):
        content = b"stable bytes"
        (self.root / "source.bin").write_bytes(content)
        selected = ["source.bin"]
        result = self.inspect(selected)
        selected.clear()
        self.assertEqual(result.entries[0].path, "source.bin")
        self.assertEqual(result.file_bytes["source.bin"], content)
        with self.assertRaises(TypeError):
            result.file_bytes["source.bin"] = b"changed"
        with self.assertRaises((AttributeError, TypeError)):
            result.entries[0].path = "changed"

    def test_inspection_rejects_invalid_paths_collisions_and_path_byte_overflow_before_leaf_read(self):
        for paths, code in (
            ("missing.txt", "INVALID_SOURCE_ENTRIES"),
            (["../outside"], "INVALID_SOURCE_ENTRY"),
            (["/absolute"], "INVALID_SOURCE_ENTRY"),
            (["C:/drive"], "INVALID_SOURCE_ENTRY"),
            (["a\\b"], "INVALID_SOURCE_ENTRY"),
            (["missing", "MISSING"], "SOURCE_PATH_COLLISION"),
            (["é" * 600], "SOURCE_LIMIT_EXCEEDED"),
            (["missing", 42], "INVALID_SOURCE_ENTRY"),
        ):
            with self.subTest(paths=paths):
                with self.assertRaises(source_files.SourceFileReadError) as raised:
                    self.inspect(paths)
                self.assertEqual(raised.exception.code, code)

        with self.assertRaises(source_files.SourceFileReadError) as count_capped:
            self.inspect(["missing-a", "missing-b"],
                         limits=source_files.SourceReadLimits(1, 10, 10))
        self.assertEqual(count_capped.exception.code, "SOURCE_LIMIT_EXCEEDED")

    def test_inspection_enforces_observed_file_and_aggregate_caps(self):
        (self.root / "large.bin").write_bytes(b"12345")
        read_name = "_read_windows" if os.name == "nt" else "_read_posix"
        read_method = getattr(source_files, read_name)
        with patch.object(source_files, read_name, wraps=read_method) as content_read:
            with self.assertRaises(source_files.SourceFileReadError) as per_file:
                self.inspect(["large.bin"], limits=source_files.SourceReadLimits(1, 4, 8))
            content_read.assert_not_called()
        self.assertEqual(per_file.exception.code, "SOURCE_LIMIT_EXCEEDED")

        (self.root / "first.bin").write_bytes(b"123")
        (self.root / "second.bin").write_bytes(b"456")
        with patch.object(source_files, read_name, wraps=read_method) as content_read:
            with self.assertRaises(source_files.SourceFileReadError) as aggregate:
                self.inspect(["first.bin", "second.bin"],
                             limits=source_files.SourceReadLimits(2, 3, 5))
            content_read.assert_not_called()
        self.assertEqual(aggregate.exception.code, "SOURCE_LIMIT_EXCEEDED")

    def test_inspected_measurements_feed_hash_verified_capture(self):
        content = b"approved bytes"
        path = self.root / "source.bin"
        path.write_bytes(content)
        measured = self.inspect(["source.bin"])
        entries = [SourceFileEntryV1(
            path=item.path, sizeBytes=item.size_bytes, sha256=item.sha256, origin="tracked",
        ) for item in measured.entries]
        self.assertEqual(self.capture(entries), {"source.bin": content})

        path.write_bytes(b"altered bytes!")
        with self.assertRaises(source_files.SourceFileReadError) as changed:
            self.capture(entries)
        self.assertEqual(changed.exception.code, "SOURCE_HASH_MISMATCH")

    def test_invalid_paths_case_collisions_and_limits_are_rejected_before_leaf_open(self):
        for path in ("../outside", "/absolute", "C:/drive", "a\\b"):
            with self.subTest(path=path):
                bad = {"path": path, "sizeBytes": 0, "sha256": hashlib.sha256(b"").hexdigest(), "origin": "tracked"}
                with self.assertRaises(source_files.SourceFileReadError) as raised:
                    self.capture([bad])
                self.assertEqual(raised.exception.code, "INVALID_SOURCE_ENTRY")

        mutable_entry = _entry("safe.txt", b"")
        object.__setattr__(mutable_entry, "path", "../outside")
        with self.assertRaises(source_files.SourceFileReadError) as mutated:
            self.capture([mutable_entry])
        self.assertEqual(mutated.exception.code, "INVALID_SOURCE_ENTRY")

        same = _entry("missing", b"")
        with self.assertRaises(source_files.SourceFileReadError) as collision:
            self.capture([same, _entry("MISSING", b"")])
        self.assertEqual(collision.exception.code, "SOURCE_PATH_COLLISION")

        too_large = _entry("does-not-exist", b"xx")
        small_limits = source_files.SourceReadLimits(1, 1, 1)
        with self.assertRaises(source_files.SourceFileReadError) as capped:
            self.capture([too_large], limits=small_limits)
        self.assertEqual(capped.exception.code, "SOURCE_LIMIT_EXCEEDED")
        with self.assertRaises(source_files.SourceFileReadError) as count_capped:
            self.capture([same, _entry("also-missing", b"")], limits=small_limits)
        self.assertEqual(count_capped.exception.code, "SOURCE_LIMIT_EXCEEDED")

    def test_root_identity_mismatch_is_denied(self):
        other = Path(self.temp.name) / "other-root"
        other.mkdir()
        other_identity = source_files.identify_source_root(other)
        entry = _entry("not-opened", b"")
        with self.assertRaises(source_files.SourceFileReadError) as mismatch:
            self.capture([entry], identity=other_identity)
        self.assertEqual(mismatch.exception.code, "ROOT_IDENTITY_MISMATCH")
        with self.assertRaises(source_files.SourceFileReadError) as inspected:
            self.inspect(["not-opened"], identity=other_identity)
        self.assertEqual(inspected.exception.code, "ROOT_IDENTITY_MISMATCH")

    def test_size_and_digest_mismatches_fail_closed(self):
        path = self.root / "observed.bin"
        path.write_bytes(b"actual")
        with self.assertRaises(source_files.SourceFileReadError) as wrong_size:
            self.capture([_entry("observed.bin", b"actual", size=5)])
        self.assertEqual(wrong_size.exception.code, "SOURCE_SIZE_MISMATCH")
        with self.assertRaises(source_files.SourceFileReadError) as wrong_hash:
            self.capture([_entry("observed.bin", b"actual", digest="0" * 64)])
        self.assertEqual(wrong_hash.exception.code, "SOURCE_HASH_MISMATCH")

    def test_result_is_independent_bytes_after_source_mutation(self):
        original = b"captured-before-change"
        path = self.root / "source.txt"
        path.write_bytes(original)
        result = self.capture([_entry("source.txt", original)])
        path.write_bytes(b"new-content-after-capture")
        self.assertEqual(result["source.txt"], original)
        self.assertIs(type(result["source.txt"]), bytes)

    def test_hardlink_is_read_as_independent_bytes_not_relinked(self):
        original = self.root / "original.bin"
        linked = self.root / "approved-link.bin"
        content = b"hard-linked bytes"
        original.write_bytes(content)
        os.link(original, linked)
        captured = self.capture([_entry("approved-link.bin", content)])
        original.write_bytes(b"changed bytes here")
        self.assertEqual(captured["approved-link.bin"], content)
        self.assertIs(type(captured["approved-link.bin"]), bytes)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink proof requires a native POSIX filesystem")
    def test_posix_leaf_and_intermediate_symlinks_are_rejected(self):
        target_dir = self.temp_root / "target-dir"
        target_dir.mkdir()
        target_file = target_dir / "secret.bin"
        target_file.write_bytes(b"sentinel")
        leaf_link = self.root / "leaf-link"
        dir_link = self.root / "dir-link"
        leaf_link.symlink_to(target_file)
        dir_link.symlink_to(target_dir, target_is_directory=True)
        for relative in ("leaf-link", "dir-link/secret.bin"):
            with self.subTest(relative=relative):
                expected = _entry(relative, b"sentinel")
                with self.assertRaises(source_files.SourceFileReadError) as raised:
                    self.capture([expected])
                self.assertEqual(raised.exception.code, "SOURCE_FILE_OPEN_FAILED")
                with self.assertRaises(source_files.SourceFileReadError) as inspected:
                    self.inspect([relative])
                self.assertEqual(inspected.exception.code, "SOURCE_FILE_OPEN_FAILED")

    @unittest.skipUnless(os.name == "nt", "Native Windows NTFS junction proof requires Windows")
    def test_windows_junction_component_is_rejected_and_removed_safely(self):
        link = self.root / "junction"
        target = self.temp_root / "junction-target"
        target.mkdir()
        self.assertTrue(target.resolve(strict=True).is_relative_to(self.temp_root))
        self.assertTrue(link.parent.resolve(strict=True).is_relative_to(self.temp_root))
        payload = b"do-not-follow-junction"
        (target / "file.bin").write_bytes(payload)
        ps = (
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '"
            + str(link).replace("'", "''") + "' -Target '"
            + str(target).replace("'", "''") + "' | Out-Null"
        )
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                check=True, capture_output=True, timeout=15,
            )
            self.assertTrue(link.is_junction(), "PowerShell did not create the owned-temp junction")
            entry = _entry("junction/file.bin", payload)
            with self.assertRaises(source_files.SourceFileReadError) as raised:
                self.capture([entry])
            self.assertEqual(raised.exception.code, "SOURCE_REPARSE_REJECTED")
            with self.assertRaises(source_files.SourceFileReadError) as inspected:
                self.inspect(["junction/file.bin"])
            self.assertEqual(inspected.exception.code, "SOURCE_REPARSE_REJECTED")
        finally:
            if link.is_junction():
                self.assertTrue(link.parent.resolve(strict=True).is_relative_to(self.temp_root))
                self.assertTrue(target.resolve(strict=True).is_relative_to(self.temp_root))
                os.rmdir(link)

    @unittest.skipUnless(os.name == "nt", "Native Windows fixed-NTFS proof requires Windows")
    def test_windows_fixed_ntfs_positive(self):
        content = b"fixed-NTFS-file"
        file_path = self.root / "regular.bin"
        file_path.write_bytes(content)
        identity = source_files.identify_source_root(self.root)
        self.assertEqual(identity.platform, "windows_ntfs")
        self.assertEqual(self.capture([_entry("regular.bin", content)]), {"regular.bin": content})

    @unittest.skipUnless(os.name == "nt", "Native Windows file-link proof requires Windows")
    def test_windows_file_reparse_rejection_when_symlink_creation_is_available(self):
        content = b"file-link-target"
        target = self.temp_root / "file-target.bin"
        target.write_bytes(content)
        link = self.root / "file-link.bin"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Windows file symlink creation is unavailable; junction component proof remains required")
        with self.assertRaises(source_files.SourceFileReadError) as raised:
            self.capture([_entry("file-link.bin", content)])
        self.assertEqual(raised.exception.code, "SOURCE_REPARSE_REJECTED")
        with self.assertRaises(source_files.SourceFileReadError) as inspected:
            self.inspect(["file-link.bin"])
        self.assertEqual(inspected.exception.code, "SOURCE_REPARSE_REJECTED")

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO proof requires a native POSIX filesystem")
    def test_posix_fifo_leaf_is_rejected_as_nonregular(self):
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        entry = _entry("pipe", b"x")
        with self.assertRaises(source_files.SourceFileReadError) as raised:
            self.capture([entry])
        self.assertEqual(raised.exception.code, "SOURCE_FILE_NOT_REGULAR")
        with self.assertRaises(source_files.SourceFileReadError) as inspected:
            self.inspect(["pipe"])
        self.assertEqual(inspected.exception.code, "SOURCE_FILE_NOT_REGULAR")


if __name__ == "__main__":
    unittest.main()
