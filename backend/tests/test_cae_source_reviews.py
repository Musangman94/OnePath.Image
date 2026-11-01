from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.cae import ArtifactRef
from app.services.cae_source_provenance import (
    BUILTIN_SOURCE_PROFILE_V1_JSON,
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
)
from app.storage.cae_source_approvals import (
    ApprovedFile,
    RootIdentity,
    SourceApprovalError,
    SourceSelection,
    canonical_source_selection,
    initialize as initialize_approvals,
    issue_approval,
)
from app.storage import cae_source_reviews as reviews


NOW = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
CONTENT = b"selected source preview"
POLICY_REF = ArtifactRef(
    id="source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
    sha256=BUILTIN_SOURCE_PROFILE_V1_SHA256,
    version=1,
)


def _ref(raw: bytes) -> ArtifactRef:
    digest = hashlib.sha256(raw).hexdigest()
    return ArtifactRef(id="source-" + digest, sha256=digest, version=1)


def _selection(**changes) -> SourceSelection:
    values = {
        "owner_id": "principal-1",
        "principal_id": "principal-1",
        "client_id": "owner-browser",
        "session_id": "a" * 64,
        "root_path": r"C:\workspace\repo",
        "root_identity": RootIdentity("windows_ntfs", 12, 340),
        "files": (ApprovedFile(
            "src/main.py", hashlib.sha256(CONTENT).hexdigest(), len(CONTENT), "tracked",
        ),),
        "source_revision": "b" * 40,
        "provenance_ref": _ref(b"source receipt").id,
        "exclusion_policy_version": "v1",
        "exclusion_policy_ref": POLICY_REF.id,
    }
    values.update(changes)
    return SourceSelection(**values)


class CaeSourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        reviews.initialize(self.connection)
        initialize_approvals(self.connection)
        self.selection = _selection()
        self.scope_ref = _ref(b"approved scope")
        self.provenance_ref = _ref(b"source receipt")

    def tearDown(self):
        self.connection.close()

    def _create(self, **overrides):
        values = {
            "selection": self.selection,
            "repository_id": "onepath-image",
            "scope_ref": self.scope_ref,
            "provenance_ref": self.provenance_ref,
            "policy_ref": POLICY_REF,
            "now": NOW,
        }
        values.update(overrides)
        return reviews.create_review(self.connection, **values)

    def _begin(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def test_public_selection_bytes_match_actual_ledger_producer(self):
        self._begin()
        approval = issue_approval(self.connection, self.selection, now=NOW)
        row = self.connection.execute(
            "SELECT selection_json,selection_sha256 FROM cae_source_approvals WHERE approval_id=?",
            (approval.approval_id,),
        ).fetchone()
        raw = canonical_source_selection(self.selection)
        self.assertEqual(bytes(row[0]), raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), row[1])
        self.connection.rollback()

    def test_pending_payload_is_canonical_bounded_and_detached(self):
        self._begin()
        record = self._create()
        self.assertEqual((record.revision, record.state), (1, "pending"))
        self.assertEqual(hashlib.sha256(record.record_bytes).hexdigest(), record.record_sha256)
        value = record.as_dict()
        self.assertEqual(value["recordKind"], "onepath.source-review")
        self.assertEqual(value["policyRef"], POLICY_REF.model_dump(mode="json"))
        self.assertEqual(value["policyProfileSha256"], BUILTIN_SOURCE_PROFILE_V1_SHA256)
        value["selection"]["files"].clear()
        self.assertEqual(len(record.as_dict()["selection"]["files"]), 1)
        self.connection.commit()
        loaded = reviews.get_review(self.connection, record.review_id)
        self.assertEqual(loaded.record_bytes, record.record_bytes)

    def test_selection_order_canonicalization_and_auth_scope_binding(self):
        second_file = ApprovedFile("src/other.py", "c" * 64, 3, "untracked")
        first = _selection(files=(second_file, self.selection.files[0]))
        second = _selection(files=(self.selection.files[0], second_file))
        self.assertEqual(canonical_source_selection(first), canonical_source_selection(second))
        self._begin()
        a = self._create(selection=first)
        b = self._create(selection=_selection(session_id="d" * 64))
        self.assertEqual(a.as_dict()["selection"], json_load(canonical_source_selection(first)))
        self.assertNotEqual(a.as_dict()["selection"]["authScope"],
                            b.as_dict()["selection"]["authScope"])
        self.connection.rollback()

    def test_refs_policy_selection_bindings_and_payload_bounds_fail_before_write(self):
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "SOURCE_SELECTION_REF_MISMATCH"):
            self._create(selection=_selection(provenance_ref="source-" + "d" * 64))
        with self.assertRaisesRegex(reviews.SourceReviewError, "INVALID_POLICY_REF"):
            self._create(policy_ref=_ref(b"wrong policy"))
        with self.assertRaisesRegex(reviews.SourceReviewError, "INVALID_REVIEW_TEXT"):
            self._create(repository_id="r" * 257)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM cae_source_reviews").fetchone()[0], 0)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_ttl_and_utc_validation_boundaries(self):
        for ttl in (0, 901):
            self._begin()
            with self.assertRaisesRegex(reviews.SourceReviewError, "INVALID_TTL"):
                self._create(ttl_seconds=ttl)
            self.connection.rollback()
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "INVALID_TIME"):
            self._create(now=datetime(2026, 11, 2))
        self.connection.rollback()

        self._begin()
        record = self._create(ttl_seconds=900)
        self.connection.commit()
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "REVIEW_NOT_EXPIRED"):
            reviews.decide_review(
                self.connection, review_id=record.review_id, expected_revision=1,
                expected_record_sha256=record.record_sha256, decision="expired", now=NOW + timedelta(seconds=899),
            )
        self.connection.rollback()
        self._begin()
        expired = reviews.decide_review(
            self.connection, review_id=record.review_id, expected_revision=1,
            expected_record_sha256=record.record_sha256, decision="expired", now=NOW + timedelta(seconds=900),
        )
        self.assertEqual((expired.revision, expired.state), (2, "expired"))
        self.connection.commit()

    def test_revision_cas_terminal_decision_replay_and_duplicate_approval_id(self):
        self._begin()
        first = self._create()
        self.connection.commit()

        self._begin()
        approved = reviews.decide_review(
            self.connection, review_id=first.review_id, expected_revision=1,
            expected_record_sha256=first.record_sha256, decision="approved",
            approval_id="ledger-grant-1", now=NOW + timedelta(seconds=1),
        )
        self.assertEqual((approved.revision, approved.state, approved.approval_id),
                         (2, "approved", "ledger-grant-1"))
        self.connection.commit()

        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "STALE_REVIEW"):
            reviews.decide_review(
                self.connection, review_id=first.review_id, expected_revision=1,
                expected_record_sha256=first.record_sha256, decision="rejected",
                now=NOW + timedelta(seconds=2),
            )
        self.connection.rollback()

        self._begin()
        second = self._create()
        self.connection.commit()
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "DUPLICATE_APPROVAL_ID"):
            reviews.decide_review(
                self.connection, review_id=second.review_id, expected_revision=1,
                expected_record_sha256=second.record_sha256, decision="approved",
                approval_id="ledger-grant-1", now=NOW + timedelta(seconds=1),
            )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(reviews.get_review(self.connection, second.review_id).state, "pending")
        self.connection.rollback()

    def test_reject_cancel_and_expire_are_single_terminal_transitions(self):
        for decision in ("rejected", "cancelled"):
            self._begin()
            pending = self._create()
            decided = reviews.decide_review(
                self.connection, review_id=pending.review_id, expected_revision=1,
                expected_record_sha256=pending.record_sha256, decision=decision,
                now=NOW + timedelta(seconds=1),
            )
            self.assertEqual((decided.revision, decided.state), (2, decision))
            with self.assertRaisesRegex(reviews.SourceReviewError, "STALE_REVIEW"):
                reviews.decide_review(
                    self.connection, review_id=pending.review_id, expected_revision=1,
                    expected_record_sha256=pending.record_sha256, decision="cancelled",
                    now=NOW + timedelta(seconds=2),
                )
            self.connection.rollback()

    def test_hash_json_and_column_corruption_fail_closed(self):
        self._begin()
        record = self._create()
        self.connection.commit()
        self.connection.execute(
            "UPDATE cae_source_reviews SET record_json=? WHERE review_id=?",
            (sqlite3.Binary(record.record_bytes + b" "), record.review_id),
        )
        with self.assertRaisesRegex(reviews.SourceReviewError, "REVIEW_CORRUPT"):
            reviews.get_review(self.connection, record.review_id)

    def test_malformed_or_oversized_sqlite_record_storage_fails_before_copy(self):
        self._begin()
        record = self._create()
        self.connection.commit()
        invalid_values = (
            "not-bytes",
            -1,
            3.5,
            sqlite3.Binary(b"x" * (reviews.MAX_REVIEW_BYTES + 1)),
        )
        for value in invalid_values:
            with self.subTest(storage_type=type(value).__name__):
                self.connection.execute("BEGIN")
                self.connection.execute(
                    "UPDATE cae_source_reviews SET record_json=? WHERE review_id=?",
                    (value, record.review_id),
                )
                with self.assertRaisesRegex(reviews.SourceReviewError, "REVIEW_CORRUPT"):
                    reviews.get_review(self.connection, record.review_id)
                self.assertTrue(self.connection.in_transaction)
                self.connection.rollback()
                restored = reviews.get_review(self.connection, record.review_id)
                self.assertEqual(restored.record_bytes, record.record_bytes)

    def test_initialization_and_writes_preserve_caller_transaction_ownership(self):
        other = sqlite3.connect(":memory:")
        try:
            other.execute("BEGIN")
            with self.assertRaisesRegex(reviews.SourceReviewError,
                                       "INITIALIZATION_REQUIRES_IDLE_CONNECTION"):
                reviews.initialize(other)
            other.rollback()
        finally:
            other.close()

        with self.assertRaisesRegex(reviews.SourceReviewError, "TRANSACTION_REQUIRED"):
            self._create()
        self.assertFalse(self.connection.in_transaction)

    def test_review_and_real_ledger_issue_rollback_together(self):
        self._begin()
        pending = self._create()
        self.connection.commit()

        self._begin()
        grant = issue_approval(self.connection, self.selection, now=NOW + timedelta(seconds=1))
        reviews.decide_review(
            self.connection, review_id=pending.review_id, expected_revision=1,
            expected_record_sha256=pending.record_sha256, decision="approved",
            approval_id=grant.approval_id, now=NOW + timedelta(seconds=1),
        )
        self.connection.rollback()
        self.assertEqual(reviews.get_review(self.connection, pending.review_id).state, "pending")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM cae_source_approvals WHERE approval_id=?",
            (grant.approval_id,),
        ).fetchone()[0], 0)


def json_load(raw: bytes):
    import json
    return json.loads(raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
