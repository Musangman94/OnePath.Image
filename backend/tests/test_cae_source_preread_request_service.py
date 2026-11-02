from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.services.cae_source_preread_request_service import SourcePrereadRequestService
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage import cae_source_preread_issuances as issuances
from app.storage import cae_source_preread_requests as requests
from app.storage.cae_source_approvals import (
    RootIdentity,
    SourceApprovalError,
    canonical_source_capture_selection,
    initialize as init_approvals,
    issue_capture_approval,
)
from app.storage.local_auth import LocalAuthStore


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-preread-request-service-bootstrap-0123456789"
ORIGIN = "http://127.0.0.1:8091"


class SourcePrereadRequestServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-preread-request-service-")
        self.path = Path(self.temp.name) / "auth-and-ledger.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-preread-request", BOOTSTRAP, now=NOW)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        init_approvals(self.connection)
        requests.initialize(self.connection)
        self.clock = [NOW]
        self.auth = LocalAuthService(self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0])
        self.service = SourcePrereadRequestService(self.auth, clock=lambda: self.clock[0])
        self.session = self._new_session()
        self.scope = SourceCaptureScopeV1(
            schema_version=1,
            root_path=r"C:\synthetic\repo",
            root_identity=RootIdentity("windows_ntfs", 14, 28),
            relative_paths=("src/main.py", "README.md"),
            limits=SourceReadLimits(8, 4096, 8192),
        )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=self.clock[0])
        return self.store.consume_pairing_code(code, now=self.clock[0])

    def _pending(self, *, token=None, ttl=300, owner_id=None, principal_id=None, client_id="owner-browser"):
        session = self.session if token is None else token
        token_value = session.token if hasattr(session, "token") else session
        self.connection.execute("BEGIN IMMEDIATE")
        record = requests.create_request(
            self.connection, scope=self.scope,
            owner_id=self.session.principal.id if owner_id is None else owner_id,
            principal_id=self.session.principal.id if principal_id is None else principal_id,
            client_id=client_id,
            session_fingerprint=hashlib.sha256(token_value.encode("utf-8")).hexdigest(),
            now=self.clock[0], ttl_seconds=ttl,
        )
        self.connection.commit()
        return record

    def _issue(self, record, *, token=None, ttl=300):
        self.connection.execute("BEGIN IMMEDIATE")
        return self.service.issue_for_request(
            self.connection,
            session_token=self.session.token if token is None else token,
            request_id=record.request_id,
            expected_revision=record.revision,
            expected_record_sha256=record.record_sha256,
            ttl_seconds=ttl,
        )

    def test_issue_uses_stored_scope_live_identity_and_derives_issued_state(self):
        record = self._pending()
        self.assertNotIn("scope", inspect.signature(self.service.issue_for_request).parameters)
        grant = self._issue(record)
        ledger = self.connection.execute(
            "SELECT selection_sha256,selection_json,secret_sha256,expires_at FROM cae_source_approvals WHERE approval_id=?",
            (grant.approval.approval_id,),
        ).fetchone()
        payload = bytes(ledger["selection_json"])
        value = json.loads(payload)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), ledger["selection_sha256"])
        self.assertEqual(value["captureScope"], json.loads(self.scope.canonical_bytes()))
        self.assertEqual(value["authScope"]["ownerId"], self.session.principal.id)
        self.assertEqual(value["authScope"]["sessionId"], hashlib.sha256(self.session.token.encode()).hexdigest())
        self.assertNotIn(self.session.token.encode(), payload)
        self.assertLessEqual(ledger["expires_at"], record.expires_at)
        self.assertEqual(requests.get_request(self.connection, record.request_id).state, "issued")
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cae_source_preread_issuances WHERE request_id=?", (record.request_id,),
        ).fetchone()[0], 1)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(cae_source_preread_issuances)")}
        self.assertNotIn("secret", columns)
        with self.assertRaises(requests.SourcePrereadRequestError):
            requests.decline_request(
                self.connection, request_id=record.request_id, expected_revision=1,
                expected_record_sha256=record.record_sha256, now=self.clock[0],
            )
        self.connection.commit()

    def test_mismatch_expiry_and_replay_fail_without_second_grant(self):
        record = self._pending()
        other = self._new_session()
        with self.assertRaises(SourceApprovalError):
            self._issue(record, token=other.token)
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)

        expired = self._pending(ttl=2)
        self.clock[0] += timedelta(seconds=2)
        with self.assertRaises(SourceApprovalError):
            self._issue(expired)
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)

        self.clock[0] = NOW
        grant = self._issue(record)
        self.connection.commit()
        with self.assertRaises(SourceApprovalError):
            self._issue(record)
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 1)
        self.assertIsNotNone(grant.approval.secret)

    def test_each_stored_identity_field_must_match_live_proof(self):
        bad_records = (
            self._pending(owner_id="other-owner"),
            self._pending(principal_id="other-principal"),
            self._pending(client_id="other-client"),
            self._pending(token=self._new_session()),
        )
        for record in bad_records:
            with self.subTest(request_id=record.request_id):
                with self.assertRaises(SourceApprovalError):
                    self._issue(record)
                self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)

    def test_outer_rollback_and_issue_failure_leave_no_grant_or_link(self):
        record = self._pending()
        self._issue(record)
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_preread_issuances").fetchone()[0], 0)
        self.assertEqual(requests.get_request(self.connection, record.request_id).state, "pending")

        self.connection.execute("BEGIN IMMEDIATE")
        with patch.object(issuances, "record", side_effect=SourceApprovalError("DATABASE_FAILURE")):
            with self.assertRaises(SourceApprovalError):
                self.service.issue_for_request(
                    self.connection, session_token=self.session.token,
                    request_id=record.request_id, expected_revision=1,
                    expected_record_sha256=record.record_sha256,
                )
        self.connection.commit()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_preread_issuances").fetchone()[0], 0)

    def test_second_connection_serializes_and_cannot_issue_same_request(self):
        record = self._pending()
        grant = self._issue(record)
        second = sqlite3.connect(self.path, timeout=0.01)
        second.row_factory = sqlite3.Row
        try:
            with self.assertRaises(sqlite3.OperationalError):
                second.execute("BEGIN IMMEDIATE")
            self.connection.commit()
            second.execute("BEGIN IMMEDIATE")
            with self.assertRaises(SourceApprovalError):
                self.service.issue_for_request(
                    second, session_token=self.session.token,
                    request_id=record.request_id, expected_revision=1,
                    expected_record_sha256=record.record_sha256,
                )
            second.rollback()
            self.assertEqual(second.execute(
                "SELECT count(*) FROM cae_source_approvals WHERE approval_id=?",
                (grant.approval.approval_id,),
            ).fetchone()[0], 1)
        finally:
            second.close()
        self.assertEqual(requests.get_request(self.connection, record.request_id).state, "issued")

    def test_decline_prevents_issue_and_link_digest_tampering_is_corrupt(self):
        declined = self._pending()
        self.connection.execute("BEGIN IMMEDIATE")
        requests.decline_request(
            self.connection, request_id=declined.request_id, expected_revision=1,
            expected_record_sha256=declined.record_sha256, now=self.clock[0],
        )
        self.connection.commit()
        with self.assertRaises(SourceApprovalError):
            self._issue(declined)
        self.connection.rollback()

        pending = self._pending()
        grant = self._issue(pending)
        self.connection.commit()
        self.connection.execute(
            "UPDATE cae_source_preread_issuances SET approval_id=? WHERE request_id=?",
            ("ffffffff-ffff-ffff-ffff-ffffffffffff", pending.request_id),
        )
        with self.assertRaises(SourceApprovalError):
            requests.get_request(self.connection, pending.request_id)

    def test_storage_boundary_rejects_unrelated_grant_and_expiry(self):
        cases = (
            (self._pending(), self.scope.__class__(
                schema_version=1, root_path=r"C:\synthetic\other",
                root_identity=self.scope.root_identity, relative_paths=self.scope.relative_paths,
                limits=self.scope.limits,
            ), self.session.principal.id, 300),
            (self._pending(), self.scope, "different-owner", 300),
            (self._pending(ttl=2), self.scope, self.session.principal.id, 10),
        )
        for record, grant_scope, owner_id, grant_ttl in cases:
            with self.subTest(request_id=record.request_id):
                self.connection.execute("BEGIN IMMEDIATE")
                selection = canonical_source_capture_selection(
                    grant_scope, owner_id=owner_id, principal_id=self.session.principal.id,
                    client_id="owner-browser",
                    session_id=hashlib.sha256(self.session.token.encode()).hexdigest(),
                )
                approval = issue_capture_approval(
                    self.connection, grant_scope, owner_id=owner_id,
                    principal_id=self.session.principal.id, client_id="owner-browser",
                    session_id=hashlib.sha256(self.session.token.encode()).hexdigest(),
                    now=NOW, ttl_seconds=grant_ttl,
                )
                with self.assertRaises(SourceApprovalError):
                    issuances.record(
                        self.connection, request_id=record.request_id,
                        request_sha256=record.record_sha256, request_expires_at=record.expires_at,
                        approval_id=approval.approval_id, selection_json=selection,
                        approval_expires_at=approval.expires_at,
                        issued_at=NOW.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    )
                self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_preread_issuances").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
