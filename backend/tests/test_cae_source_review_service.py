import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.cae import ArtifactRef
from app.services.cae_source_approval_service import SourceApprovalScopeV1, SourceApprovalService
from app.services.cae_source_git import GitExecutableIdentityV1, GitMetadataObservationV1
from app.services.cae_source_provenance import (
    ApprovedSourceFileV1,
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
    parse_cached_index,
)
from app.services.cae_source_review_service import SourceReviewService
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage import cae_source_artifacts as artifacts
from app.storage import cae_source_approvals as approvals
from app.storage import cae_source_reviews as reviews
from app.storage.cae_source_approvals import RootIdentity, consume_approval
from app.storage.local_auth import LocalAuthStore


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-source-review-service-bootstrap-0123456789"
ORIGIN = "http://127.0.0.1:8091"
CONTENT = b"selected in-memory source"
HEAD = "a" * 40


def ref(raw: bytes) -> ArtifactRef:
    digest = hashlib.sha256(raw).hexdigest()
    return ArtifactRef(id="source-" + digest, sha256=digest, version=1)


class SourceReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-review-service-")
        self.path = Path(self.temp.name) / "auth-and-source.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-a", BOOTSTRAP, now=NOW)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        artifacts.initialize(self.connection)
        approvals.initialize(self.connection)
        reviews.initialize(self.connection)
        self.clock = [NOW]
        self.auth = LocalAuthService(self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0])
        self.service = SourceReviewService(self.auth, clock=lambda: self.clock[0])
        self.session = self._new_session()
        self.content = {"src/a.py": CONTENT}
        self.files = tuple(ApprovedSourceFileV1(
            path, len(raw), hashlib.sha256(raw).hexdigest(),
        ) for path, raw in self.content.items())
        self.index = parse_cached_index(b"", self.files, object_format="sha1", head_oid=HEAD)
        self.observation = GitMetadataObservationV1(
            1, r"C:\workspace\repo", RootIdentity("windows_ntfs", 12, 340),
            GitExecutableIdentityV1("X:/Git/git.exe", "b" * 64, "Git 2.55 test"),
            "sha1", HEAD, "c" * 64, self.index,
        )
        self.scope_ref = ref(b"prepared source scope")

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=NOW)
        return self.store.consume_pairing_code(code, now=NOW)

    def _begin(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def _prepare(self, **overrides):
        values = {
            "session_token": self.session.token,
            "repository_id": "onepath-image",
            "scope_ref": self.scope_ref,
            "approved_files": self.files,
            "captured_bytes": self.content,
            "before": self.observation,
            "after": self.observation,
        }
        values.update(overrides)
        return self.service.prepare_review(self.connection, **values)

    def _scope(self, selection):
        return SourceApprovalScopeV1(
            root_path=selection.root_path, root_identity=selection.root_identity,
            files=selection.files, source_revision=selection.source_revision,
            provenance_ref=selection.provenance_ref,
            exclusion_policy_version=selection.exclusion_policy_version,
            exclusion_policy_ref=selection.exclusion_policy_ref,
        )

    def _object_count(self):
        return self.connection.execute("SELECT count(*) FROM cae_source_objects").fetchone()[0]

    def _approval_count(self):
        return self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0]

    def test_prepare_persists_receipt_policy_and_pending_review_without_grant(self):
        self._begin()
        record = self._prepare()
        self.assertEqual((record.revision, record.state), (1, "pending"))
        selected = reviews.selection_from_review(record)
        self.assertEqual(
            selected.session_id,
            hashlib.sha256(self.session.token.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(selected.source_revision, HEAD)
        self.assertEqual(selected.exclusion_policy_version, BUILTIN_SOURCE_PROFILE_V1_SHA256)
        payload = record.as_dict()
        receipt_ref = ArtifactRef.model_validate(payload["provenanceRef"])
        policy_ref = ArtifactRef.model_validate(payload["policyRef"])
        self.assertEqual(artifacts.read_object(self.connection, receipt_ref)[:1], b"{")
        self.assertEqual(artifacts.read_object(self.connection, policy_ref),
                         __import__("app.services.cae_source_provenance", fromlist=["BUILTIN_SOURCE_PROFILE_V1_JSON"]).BUILTIN_SOURCE_PROFILE_V1_JSON)
        self.assertEqual(self._approval_count(), 0)
        self.connection.commit()

    def test_authenticated_approval_issues_consumable_one_use_grant(self):
        self._begin()
        pending = self._prepare()
        self.connection.commit()
        self._begin()
        grant = self.service.approve_review(
            self.connection, session_token=self.session.token, review_id=pending.review_id,
            expected_revision=1, expected_record_sha256=pending.record_sha256,
        )
        current = reviews.get_review(self.connection, pending.review_id)
        self.assertEqual((current.state, current.revision), ("approved", 2))
        self.assertEqual(current.approval_id, grant.approval.approval_id)
        self.assertEqual(self._approval_count(), 1)
        adapter = SourceApprovalService(self.auth, clock=lambda: self.clock[0])
        bound = adapter.consume(
            self.connection, session_token=self.session.token,
            scope=self._scope(grant.selection), approval=grant.approval,
        )
        self.assertEqual(bound, grant.selection)
        self.connection.commit()
        self._begin()
        with self.assertRaises(Exception):
            consume_approval(self.connection, grant.approval, bound, now=NOW)
        self.connection.rollback()

    def test_wrong_token_and_distinct_session_cannot_approve(self):
        self._begin()
        pending = self._prepare()
        self.connection.commit()
        self._begin()
        with self.assertRaises(AuthServiceError):
            self.service.approve_review(
                self.connection, session_token="forged-token", review_id=pending.review_id,
                expected_revision=1, expected_record_sha256=pending.record_sha256,
            )
        self.connection.rollback()
        other = self._new_session()
        self._begin()
        with self.assertRaisesRegex(ValueError, "SESSION_SCOPE_MISMATCH"):
            self.service.approve_review(
                self.connection, session_token=other.token, review_id=pending.review_id,
                expected_revision=1, expected_record_sha256=pending.record_sha256,
            )
        self.assertEqual(self._approval_count(), 0)
        self.assertEqual(reviews.get_review(self.connection, pending.review_id).state, "pending")
        self.connection.rollback()

    def test_stale_replay_and_expired_review_leave_no_nonce(self):
        self._begin()
        stale = self._prepare()
        self.connection.commit()
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "STALE_REVIEW"):
            self.service.approve_review(
                self.connection, session_token=self.session.token, review_id=stale.review_id,
                expected_revision=1, expected_record_sha256="0" * 64,
            )
        self.assertEqual(self._approval_count(), 0)
        self.connection.rollback()
        self._begin()
        grant = self.service.approve_review(
            self.connection, session_token=self.session.token, review_id=stale.review_id,
            expected_revision=1, expected_record_sha256=stale.record_sha256,
        )
        self.connection.commit()
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "STALE_REVIEW"):
            self.service.approve_review(
                self.connection, session_token=self.session.token, review_id=stale.review_id,
                expected_revision=1, expected_record_sha256=stale.record_sha256,
            )
        self.assertEqual(self._approval_count(), 1)
        self.connection.rollback()

        self.clock[0] = NOW + timedelta(seconds=1)
        self._begin()
        expired = self._prepare(ttl_seconds=1)
        self.connection.commit()
        self.clock[0] += timedelta(seconds=1)
        self._begin()
        with self.assertRaisesRegex(reviews.SourceReviewError, "REVIEW_EXPIRED"):
            self.service.approve_review(
                self.connection, session_token=self.session.token, review_id=expired.review_id,
                expected_revision=1, expected_record_sha256=expired.record_sha256,
            )
        self.assertEqual(self._approval_count(), 1)
        self.connection.rollback()

    def test_decision_failure_rolls_back_issued_nonce_and_preserves_unrelated_write(self):
        self._begin()
        pending = self._prepare()
        self.connection.commit()
        self._begin()
        self.connection.execute("CREATE TEMP TABLE caller_data(value TEXT)")
        self.connection.execute("INSERT INTO caller_data VALUES('keep')")
        with patch.object(reviews, "decide_review", side_effect=reviews.SourceReviewError("INJECTED")):
            with self.assertRaisesRegex(reviews.SourceReviewError, "INJECTED"):
                self.service.approve_review(
                    self.connection, session_token=self.session.token, review_id=pending.review_id,
                    expected_revision=1, expected_record_sha256=pending.record_sha256,
                )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self._approval_count(), 0)
        self.assertEqual(self.connection.execute("SELECT value FROM caller_data").fetchone()[0], "keep")
        self.assertEqual(reviews.get_review(self.connection, pending.review_id).state, "pending")
        self.connection.rollback()

    def test_prepare_failure_rolls_back_receipt_objects_and_declines_do_not_issue_grant(self):
        self._begin()
        self.connection.execute("CREATE TEMP TABLE caller_data(value TEXT)")
        self.connection.execute("INSERT INTO caller_data VALUES('keep')")
        with patch.object(reviews, "create_review", side_effect=reviews.SourceReviewError("INJECTED")):
            with self.assertRaisesRegex(reviews.SourceReviewError, "INJECTED"):
                self._prepare()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self._object_count(), 0)
        self.assertEqual(self.connection.execute("SELECT value FROM caller_data").fetchone()[0], "keep")
        self.connection.rollback()

        for decision in ("rejected", "cancelled"):
            self._begin()
            pending = self._prepare()
            self.connection.commit()
            self._begin()
            decided = self.service.decline_review(
                self.connection, session_token=self.session.token, review_id=pending.review_id,
                expected_revision=1, expected_record_sha256=pending.record_sha256,
                decision=decision,
            )
            self.assertEqual(decided.state, decision)
            self.assertEqual(self._approval_count(), 0)
            self.connection.rollback()

    def test_service_requires_caller_transaction(self):
        with self.assertRaisesRegex(ValueError, "TRANSACTION_REQUIRED"):
            self._prepare()

    def test_malformed_decisions_fail_with_fixed_errors_without_mutation(self):
        self._begin()
        pending = self._prepare()
        self.connection.commit()
        invalid = ([], {}, None, 0, True, ())
        self._begin()
        self.connection.execute("CREATE TEMP TABLE caller_data(value TEXT)")
        self.connection.execute("INSERT INTO caller_data VALUES('keep')")
        for decision in (*invalid, "approved"):
            with self.subTest(decision_type=type(decision).__name__, decision=repr(decision)):
                with self.assertRaisesRegex(ValueError, "INVALID_REVIEW_DECISION"):
                    self.service.decline_review(
                        self.connection, session_token=self.session.token,
                        review_id=pending.review_id, expected_revision=1,
                        expected_record_sha256=pending.record_sha256, decision=decision,
                    )
                if decision != "approved":
                    with self.assertRaisesRegex(reviews.SourceReviewError,
                                                "INVALID_REVIEW_DECISION"):
                        reviews.decide_review(
                            self.connection, review_id=pending.review_id,
                            expected_revision=1, expected_record_sha256=pending.record_sha256,
                            decision=decision, now=NOW,
                        )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT value FROM caller_data").fetchone()[0], "keep")
        self.assertEqual(reviews.get_review(self.connection, pending.review_id).state, "pending")
        self.assertEqual(self._approval_count(), 0)
        self.connection.rollback()


if __name__ == "__main__":
    unittest.main()
