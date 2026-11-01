import hashlib
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.schemas.cae import ArtifactRef
from app.services import cae_source_receipts as receipts
from app.services.cae_source_git import (
    GitExecutableIdentityV1,
    GitMetadataObservationV1,
)
from app.services.cae_source_provenance import (
    ApprovedSourceFileV1,
    BUILTIN_SOURCE_PROFILE_V1_JSON,
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
    parse_cached_index,
)
from app.storage import cae_source_artifacts as artifacts
from app.storage.cae_source_approvals import RootIdentity


def _ref(raw: bytes) -> ArtifactRef:
    digest = hashlib.sha256(raw).hexdigest()
    return ArtifactRef(id="source-" + digest, sha256=digest, version=1)


class CaeSourceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.content = {
            "src/clean.py": b"clean = True\n",
            "src/dirty.py": b"working tree version\n",
            "src/new.py": b"approved untracked\n",
        }
        self.files = tuple(
            ApprovedSourceFileV1(path, len(raw), hashlib.sha256(raw).hexdigest())
            for path, raw in self.content.items()
        )
        self.scope = _ref(b"approved scope")
        self.policy = _ref(BUILTIN_SOURCE_PROFILE_V1_JSON)
        self.before = self._observation()
        self.after = self.before

    @staticmethod
    def _record(path, oid):
        return f"H 100644 {oid} 0\t{path}".encode("utf-8") + b"\0"

    def _observation(self, *, head="a" * 40, root_path="X:/repo", root_id=None,
                     executable=None, raw_index=None, entries=None, profile=None):
        selected = tuple(sorted(self.files, key=lambda item: item.path))
        clean_oid = hashlib.sha1(
            b"blob " + str(len(self.content["src/clean.py"])).encode("ascii") + b"\0"
            + self.content["src/clean.py"]
        ).hexdigest()
        dirty_index_bytes = b"staged version\n"
        dirty_oid = hashlib.sha1(
            b"blob " + str(len(dirty_index_bytes)).encode("ascii") + b"\0" + dirty_index_bytes
        ).hexdigest()
        if raw_index is None:
            raw_index = (self._record("src/clean.py", clean_oid)
                         + self._record("src/dirty.py", dirty_oid))
        source_index = parse_cached_index(
            raw_index, selected, object_format="sha1", head_oid=head,
        )
        if entries is not None:
            source_index = replace(source_index, entries=entries)
        if profile is not None:
            source_index = replace(source_index, source_policy_profile_sha256=profile)
        return GitMetadataObservationV1(
            1,
            root_path,
            root_id or RootIdentity("windows", 17, 42),
            executable or GitExecutableIdentityV1("X:/Git/git.exe", "b" * 64, "Git 2.55 test"),
            "sha1",
            head,
            hashlib.sha256((root_path + "\nsha1\n" + head).encode()).hexdigest(),
            source_index,
        )

    def _build(self, **overrides):
        values = {
            "repository_id": "onepath-image",
            "scope_ref": self.scope,
            "policy_artifact_ref": self.policy,
            "approved_files": self.files,
            "captured_bytes": self.content,
            "before": self.before,
            "after": self.after,
        }
        values.update(overrides)
        return receipts.build_source_provenance_receipt(**values)

    def _persist(self, connection, **overrides):
        values = {
            "repository_id": "onepath-image",
            "scope_ref": self.scope,
            "approved_files": self.files,
            "captured_bytes": self.content,
            "before": self.before,
            "after": self.after,
        }
        values.update(overrides)
        return receipts.persist_source_provenance_receipt(connection, **values)

    def test_receipt_is_deterministic_and_detached_from_mutable_inputs(self):
        content = dict(self.content)
        selection = list(self.files)
        scope = self.scope.model_dump(mode="json")
        first = self._build(captured_bytes=content, approved_files=selection, scope_ref=scope)
        second = self._build(
            captured_bytes=dict(reversed(list(content.items()))),
            approved_files=list(reversed(selection)),
        )
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        content["src/clean.py"] = b"mutated"
        selection.clear()
        scope["id"] = "changed"
        view = first.as_dict()
        view["files"].clear()
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(len(first.as_dict()["files"]), 3)
        self.assertEqual(first.as_dict()["scopeRef"], self.scope.model_dump(mode="json"))

    def test_clean_divergent_tracked_and_untracked_provenance_is_preserved(self):
        value = self._build().as_dict()
        files = {entry["path"]: entry for entry in value["files"]}
        clean = files["src/clean.py"]
        dirty = files["src/dirty.py"]
        untracked = files["src/new.py"]
        self.assertEqual(clean["origin"], "tracked")
        self.assertEqual(clean["capturedBlobOid"], clean["indexOid"])
        self.assertEqual(dirty["origin"], "tracked")
        self.assertNotEqual(dirty["capturedBlobOid"], dirty["indexOid"])
        self.assertEqual(dirty["provenanceStatus"], "index-entry")
        self.assertEqual(untracked["origin"], "untracked")
        self.assertIsNone(untracked["indexOid"])
        self.assertEqual(untracked["provenanceStatus"], "approved-index-absent")
        self.assertNotIn("clean = True", str(value))

    def test_selected_bytes_must_match_exact_paths_sizes_hashes_and_types(self):
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "SOURCE_FILE_SET_MISMATCH"):
            self._build(captured_bytes={"extra.py": b"x"})
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "SOURCE_FILE_SET_MISMATCH"):
            self._build(captured_bytes={"src/clean.py": self.content["src/clean.py"]})
        wrong_hash = (ApprovedSourceFileV1("src/clean.py", len(self.content["src/clean.py"]), "0" * 64),
                      *self.files[1:])
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "SOURCE_HASH_MISMATCH"):
            self._build(approved_files=wrong_hash)
        wrong_size = (ApprovedSourceFileV1("src/clean.py", 1, self.files[0].sha256), *self.files[1:])
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "SOURCE_SIZE_MISMATCH"):
            self._build(approved_files=wrong_size)
        bad_type = dict(self.content)
        bad_type["src/clean.py"] = bytearray(bad_type["src/clean.py"])
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "INVALID_CAPTURED_BYTES"):
            self._build(captured_bytes=bad_type)

    def test_stale_head_raw_index_root_or_executable_is_rejected(self):
        variants = (
            self._observation(head="c" * 40),
            self._observation(raw_index=self._record("src/clean.py", "d" * 40)
                              + self._record("src/dirty.py", "e" * 40)),
            self._observation(root_path="X:/other"),
            self._observation(root_id=RootIdentity("windows", 17, 43)),
            self._observation(executable=GitExecutableIdentityV1(
                "X:/Git/other.exe", "c" * 64, "Git 2.55 test")),
        )
        for after in variants:
            with self.subTest(after=after.root_path, head=after.head_oid):
                with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "STALE_GIT_OBSERVATION"):
                    self._build(after=after)

    def test_profile_or_selected_index_entry_inconsistency_is_rejected(self):
        bad_profile = self._observation(profile="0" * 64)
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError,
                                   "INVALID_SOURCE_INDEX_OBSERVATION"):
            self._build(before=bad_profile, after=bad_profile)
        entries = list(self.before.source_index.entries)
        entries[0] = replace(entries[0], source_sha256="f" * 64)
        bad_entries = self._observation(entries=tuple(entries))
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError,
                                   "SOURCE_INDEX_SELECTION_MISMATCH"):
            self._build(before=bad_entries, after=bad_entries)

    def test_fixed_policy_reference_and_field_bounds_are_enforced(self):
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError,
                                   "SOURCE_POLICY_REFERENCE_MISMATCH"):
            self._build(policy_artifact_ref=_ref(b"not the built-in policy"))
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "INVALID_RECEIPT_TEXT"):
            self._build(repository_id="r" * 257)
        too_long_root = self._observation(root_path="X:/" + "r" * 4096)
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "INVALID_RECEIPT_TEXT"):
            self._build(before=too_long_root, after=too_long_root)
        with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "RECEIPT_LIMIT_EXCEEDED"):
            receipts.SourceProvenanceReceiptV1(b"x" * (receipts.MAX_RECEIPT_BYTES + 1))

    def test_persistence_requires_transaction_and_roundtrips_deduplicated_objects(self):
        temp = tempfile.TemporaryDirectory(prefix="cae-source-receipts-")
        connection = sqlite3.connect(Path(temp.name) / "receipt.sqlite3")
        try:
            artifacts.initialize(connection)
            with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "TRANSACTION_REQUIRED"):
                self._persist(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 0)
            connection.execute("BEGIN IMMEDIATE")
            receipt_ref, stored_receipt = self._persist(connection)
            receipt = self._build()
            value = stored_receipt.as_dict()
            policy_ref = ArtifactRef.model_validate(value["policy"]["artifactRef"])
            self.assertEqual(artifacts.read_object(connection, receipt_ref), receipt.canonical_bytes)
            self.assertEqual(artifacts.read_object(connection, policy_ref), BUILTIN_SOURCE_PROFILE_V1_JSON)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 2)
            duplicate_ref, _ = self._persist(connection)
            self.assertEqual(duplicate_ref, receipt_ref)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 2)
            self.assertTrue(connection.in_transaction)
            connection.commit()
        finally:
            connection.close()
            temp.cleanup()

    def test_caller_rollback_removes_both_persisted_objects(self):
        temp = tempfile.TemporaryDirectory(prefix="cae-source-receipts-")
        connection = sqlite3.connect(Path(temp.name) / "receipt.sqlite3")
        try:
            artifacts.initialize(connection)
            connection.execute("BEGIN IMMEDIATE")
            self._persist(connection)
            self.assertTrue(connection.in_transaction)
            connection.rollback()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 0)
        finally:
            connection.close()
            temp.cleanup()

    def test_second_write_failure_rolls_back_pair_but_preserves_caller_work(self):
        temp = tempfile.TemporaryDirectory(prefix="cae-source-receipts-")
        connection = sqlite3.connect(Path(temp.name) / "receipt.sqlite3")
        try:
            artifacts.initialize(connection)
            connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
            receipt = self._build()
            digest = hashlib.sha256(receipt.canonical_bytes).hexdigest()
            connection.execute(
                "CREATE TRIGGER fail_receipt BEFORE INSERT ON cae_source_objects "
                f"WHEN NEW.sha256='{digest}' BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO caller_state VALUES('keep this')")
            with self.assertRaises(artifacts.CaeSourceArtifactError):
                self._persist(connection)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT value FROM caller_state").fetchone()[0], "keep this")
            connection.commit()
        finally:
            connection.close()
            temp.cleanup()

    def test_direct_data_holder_cannot_bypass_builder_or_persist_incomplete_data(self):
        temp = tempfile.TemporaryDirectory(prefix="cae-source-receipts-")
        connection = sqlite3.connect(Path(temp.name) / "receipt.sqlite3")
        try:
            artifacts.initialize(connection)
            connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO caller_state VALUES('keep this')")
            policy_ref = self.policy.model_dump(mode="json")
            bypass_payload = json.dumps(
                {
                    "schemaVersion": 1,
                    "evidenceType": "onepath.source-provenance",
                    "policy": {
                        "profileSha256": BUILTIN_SOURCE_PROFILE_V1_SHA256,
                        "artifactRef": policy_ref,
                    },
                },
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            forged_holder = receipts.SourceProvenanceReceiptV1(bypass_payload)
            with self.assertRaises(TypeError):
                receipts.persist_source_provenance_receipt(connection, forged_holder)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 0)
            with self.assertRaisesRegex(receipts.CaeSourceReceiptError, "SOURCE_FILE_SET_MISMATCH"):
                self._persist(connection, captured_bytes={"only-one.py": b"x"})
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cae_source_objects").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT value FROM caller_state").fetchone()[0], "keep this")
            connection.rollback()
        finally:
            connection.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
