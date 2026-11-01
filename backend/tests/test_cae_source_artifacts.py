import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.schemas.cae import ArtifactRef, SourceFileEntryV1, SourceStateManifestV1
from app.storage import cae_source_artifacts as artifacts


def _ref(content: bytes) -> ArtifactRef:
    digest = hashlib.sha256(content).hexdigest()
    return ArtifactRef(id="source-" + digest, sha256=digest, version=1)


def _entry(path: str, content: bytes, *, origin="tracked", size=None, digest=None):
    return SourceFileEntryV1(
        path=path,
        sizeBytes=len(content) if size is None else size,
        sha256=hashlib.sha256(content).hexdigest() if digest is None else digest,
        origin=origin,
    )


class CaeSourceArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-artifacts-")
        self.path = Path(self.temp.name) / "temporary.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        artifacts.initialize(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _create(self, files, file_bytes, **overrides):
        values = {
            "source_state_id": "state-1",
            "repository_id": "repo-1",
            "scope_ref": _ref(b"approved-scope"),
            "git_head": "a" * 40,
            "exclusion_policy_ref": _ref(b"exclusion-policy-v1"),
            "files": files,
            "file_bytes": file_bytes,
        }
        values.update(overrides)
        return artifacts.create_source_state(self.connection, **values)

    def _object_count(self):
        return self.connection.execute(
            "SELECT COUNT(*) FROM cae_source_objects"
        ).fetchone()[0]

    def test_put_object_requires_caller_transaction_without_writing(self):
        with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
            artifacts.put_object(self.connection, b"receipt")
        self.assertEqual(raised.exception.code, "TRANSACTION_REQUIRED")
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 0)

    def test_put_object_roundtrip_and_duplicate_are_idempotent(self):
        raw = b"opaque\x00canonical bytes"
        self.connection.execute("BEGIN IMMEDIATE")
        ref = artifacts.put_object(self.connection, raw)
        self.assertEqual(ref, _ref(raw))
        self.assertEqual(artifacts.read_object(self.connection, ref), raw)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 1)

        duplicate = artifacts.put_object(self.connection, raw)
        self.assertEqual(duplicate, ref)
        self.assertEqual(self._object_count(), 1)
        self.assertTrue(self.connection.in_transaction)
        self.connection.commit()

    def test_put_object_respects_caller_rollback(self):
        self.connection.execute("BEGIN IMMEDIATE")
        ref = artifacts.put_object(self.connection, b"rollback me")
        self.assertEqual(artifacts.read_object(self.connection, ref), b"rollback me")
        self.assertTrue(self.connection.in_transaction)

        self.connection.rollback()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 0)

    def test_put_object_rejects_bad_type_and_oversize_without_mutation(self):
        limits = artifacts.ArtifactLimits(max_files=1, max_file_bytes=4,
                                          max_source_bytes=3, max_index_bytes=4,
                                          max_descriptor_bytes=4)
        self.connection.execute("BEGIN IMMEDIATE")
        for raw, code in ((bytearray(b"no"), "INVALID_OBJECT_BYTES"),
                          (b"four", "OBJECT_LIMIT_EXCEEDED")):
            with self.subTest(code=code):
                with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
                    artifacts.put_object(self.connection, raw, limits=limits)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self._object_count(), 0)
                self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_put_object_rejects_corrupt_existing_address(self):
        raw = b"expected receipt"
        ref = _ref(raw)
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute(
            "INSERT INTO cae_source_objects(sha256,byte_length,content) VALUES(?,?,?)",
            (ref.sha256, len(raw), sqlite3.Binary(b"corrupted")),
        )
        self.connection.commit()

        self.connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
            artifacts.put_object(self.connection, raw)
        self.assertEqual(raised.exception.code, "OBJECT_INTEGRITY_FAILED")
        self.assertEqual(self._object_count(), 1)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_put_object_savepoint_preserves_unrelated_caller_writes_on_failure(self):
        self.connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
        digest = hashlib.sha256(b"trigger failure").hexdigest()
        self.connection.execute(
            "CREATE TRIGGER fail_object BEFORE INSERT ON cae_source_objects "
            f"WHEN NEW.sha256='{digest}' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        self.connection.commit()

        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("INSERT INTO caller_state VALUES('outer-change')")
        with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
            artifacts.put_object(self.connection, b"trigger failure")
        self.assertEqual(raised.exception.code, "DATABASE_FAILURE")
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 0)
        self.assertEqual(
            [row[0] for row in self.connection.execute("SELECT value FROM caller_state")],
            ["outer-change"],
        )
        self.connection.commit()

    def test_exact_binary_roundtrip_and_tracked_untracked_metadata(self):
        content = {"src/z.py": b"\x00\xff\x80\n", "notes/approved.md": b"owner note\n"}
        entries = [
            _entry("src/z.py", content["src/z.py"]),
            _entry("notes/approved.md", content["notes/approved.md"], origin="untracked"),
        ]
        ref, descriptor = self._create(entries, content)
        resolved, copied = artifacts.resolve_source_state(self.connection, ref)
        self.assertEqual(descriptor, resolved)
        self.assertEqual(copied, content)
        self.assertEqual(descriptor.includedUntracked, ["notes/approved.md"])
        self.assertEqual(ref.id, "source-" + ref.sha256)
        self.assertEqual(ref.version, 1)
        self.assertEqual(descriptor.manifestArtifactRef.id,
                         "source-" + descriptor.manifestArtifactRef.sha256)

    def test_canonical_hashes_ignore_input_order_and_identical_write_is_idempotent(self):
        content = {"b.py": b"B\x00", "a.py": b"A\xff"}
        entries = [_entry(path, data) for path, data in content.items()]
        ref1, descriptor1 = self._create(entries, content)
        count = self._object_count()
        ref2, descriptor2 = self._create(list(reversed(entries)), dict(reversed(list(content.items()))))
        self.assertEqual(ref1, ref2)
        self.assertEqual(descriptor1, descriptor2)
        self.assertEqual(self._object_count(), count)

    def test_mutating_input_buffer_does_not_change_stored_snapshot(self):
        input_buffer = bytearray(b"before")
        entry = _entry("source.bin", bytes(input_buffer))
        ref, _descriptor = self._create([entry], {"source.bin": input_buffer})
        input_buffer[:] = b"after!"
        _resolved, copied = artifacts.resolve_source_state(self.connection, ref)
        self.assertEqual(copied["source.bin"], b"before")

    def test_missing_extra_bad_hash_and_wrong_size_commit_no_objects(self):
        content = b"expected"
        valid = _entry("x.bin", content)
        cases = (
            ([valid], {}, "SOURCE_FILE_SET_MISMATCH"),
            ([valid], {"x.bin": content, "extra.bin": b"x"}, "SOURCE_FILE_SET_MISMATCH"),
            ([_entry("x.bin", content, digest="0" * 64)], {"x.bin": content}, "SOURCE_HASH_MISMATCH"),
            ([_entry("x.bin", content, size=len(content) + 1)], {"x.bin": content}, "SOURCE_SIZE_MISMATCH"),
        )
        for entries, file_bytes, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
                    self._create(entries, file_bytes)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self._object_count(), 0)

    def test_configured_limits_are_checked_before_any_object_insert(self):
        limits = artifacts.ArtifactLimits(max_files=1, max_file_bytes=2,
                                          max_source_bytes=2, max_index_bytes=1024,
                                          max_descriptor_bytes=2048)
        with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
            self._create([_entry("large.bin", b"abc")], {"large.bin": b"abc"}, limits=limits)
        self.assertEqual(raised.exception.code, "SOURCE_LIMIT_EXCEEDED")
        self.assertEqual(self._object_count(), 0)

    def test_outer_transaction_survives_mid_insert_failure_and_only_savepoint_rolls_back(self):
        self.connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
        self.connection.commit()
        self.connection.execute(
            "CREATE TRIGGER fail_second_object BEFORE INSERT ON cae_source_objects "
            "WHEN (SELECT COUNT(*) FROM cae_source_objects)=1 "
            "BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("INSERT INTO caller_state VALUES('outer-change')")
        with self.assertRaises(artifacts.CaeSourceArtifactError) as raised:
            self._create([_entry("a", b"a"), _entry("b", b"b")], {"a": b"a", "b": b"b"})
        self.assertEqual(raised.exception.code, "DATABASE_FAILURE")
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 0)
        rows = [row[0] for row in self.connection.execute("SELECT value FROM caller_state")]
        self.assertEqual(rows, ["outer-change"])
        self.connection.commit()
        self.connection.execute("DROP TRIGGER fail_second_object")
        self.connection.commit()

    def test_reference_id_version_and_object_digest_are_checked(self):
        content = b"bytes"
        ref, _descriptor = self._create([_entry("file.bin", content)], {"file.bin": content})
        with self.assertRaises(artifacts.CaeSourceArtifactError) as bad_id:
            artifacts.read_object(self.connection, {"id": "wrong", "sha256": ref.sha256, "version": 1})
        self.assertEqual(bad_id.exception.code, "INVALID_ARTIFACT_REF")
        with self.assertRaises(artifacts.CaeSourceArtifactError) as bad_version:
            artifacts.read_object(self.connection, {"id": ref.id, "sha256": ref.sha256, "version": 2})
        self.assertEqual(bad_version.exception.code, "INVALID_ARTIFACT_REF")
        self.connection.execute("UPDATE cae_source_objects SET content=? WHERE sha256=?",
                                (sqlite3.Binary(b"byTes"), hashlib.sha256(content).hexdigest()))
        with self.assertRaises(artifacts.CaeSourceArtifactError) as tampered:
            artifacts.resolve_source_state(self.connection, ref)
        self.assertEqual(tampered.exception.code, "OBJECT_INTEGRITY_FAILED")
        count = self._object_count()
        self.connection.execute(
            "UPDATE cae_source_objects SET byte_length=1048576,content=zeroblob(1048576) WHERE sha256=?",
            (hashlib.sha256(content).hexdigest(),),
        )
        with self.assertRaises(artifacts.CaeSourceArtifactError) as oversized:
            self._create([_entry("file.bin", content)], {"file.bin": content})
        self.assertEqual(oversized.exception.code, "OBJECT_INTEGRITY_FAILED")
        self.assertEqual(self._object_count(), count)

    def test_index_must_match_descriptor_and_missing_blob_fails_closed(self):
        content = b"payload"
        ref, descriptor = self._create([_entry("file.bin", content)], {"file.bin": content})
        mismatched_index = json.dumps(
            {"schemaVersion": 1, "files": []}, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        index_ref = _ref(mismatched_index)
        self.connection.execute(
            "INSERT INTO cae_source_objects(sha256,byte_length,content) VALUES(?,?,?)",
            (index_ref.sha256, len(mismatched_index), sqlite3.Binary(mismatched_index)),
        )
        descriptor_value = descriptor.model_dump(mode="json")
        descriptor_value["manifestArtifactRef"] = index_ref.model_dump(mode="json")
        mismatched_descriptor = SourceStateManifestV1.model_validate(descriptor_value)
        descriptor_bytes = json.dumps(
            mismatched_descriptor.model_dump(mode="json"), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        mismatched_ref = _ref(descriptor_bytes)
        self.connection.execute(
            "INSERT INTO cae_source_objects(sha256,byte_length,content) VALUES(?,?,?)",
            (mismatched_ref.sha256, len(descriptor_bytes), sqlite3.Binary(descriptor_bytes)),
        )
        with self.assertRaises(artifacts.CaeSourceArtifactError) as mismatch:
            artifacts.resolve_source_state(self.connection, mismatched_ref)
        self.assertEqual(mismatch.exception.code, "SOURCE_INDEX_MISMATCH")

        file_hash = hashlib.sha256(content).hexdigest()
        self.connection.execute("DELETE FROM cae_source_objects WHERE sha256=?", (file_hash,))
        with self.assertRaises(artifacts.CaeSourceArtifactError) as missing:
            artifacts.resolve_source_state(self.connection, ref)
        self.assertEqual(missing.exception.code, "OBJECT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
