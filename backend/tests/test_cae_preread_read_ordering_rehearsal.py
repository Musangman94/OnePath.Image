"""Synthetic ordering rehearsal; the reader spy never touches the filesystem."""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.services.cae_source_approval_service import SourceApprovalService
from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.services.cae_source_preread_request_service import SourcePrereadRequestService
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage import cae_source_read_attempts as attempts
from app.storage import cae_source_preread_requests as requests
from app.storage.cae_source_approvals import RootIdentity, SourceApprovalError, initialize as init_approvals
from app.storage.local_auth import LocalAuthStore


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-preread-ordering-rehearsal-bootstrap-0123456789"
ORIGIN = "http://127.0.0.1:8091"


class CommitFaultConnection(sqlite3.Connection):
    commit_fault = None

    def commit(self):
        fault = self.commit_fault
        self.commit_fault = None
        if fault == "before":
            raise sqlite3.OperationalError("synthetic commit failure")
        super().commit()
        if fault == "after":
            raise sqlite3.OperationalError("synthetic ambiguous commit response")


class PrereadReadOrderingRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-preread-ordering-")
        self.path = Path(self.temp.name) / "synthetic.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-ordering", BOOTSTRAP, now=NOW)
        self.connection = sqlite3.connect(self.path, timeout=5, factory=CommitFaultConnection)
        self.connection.row_factory = sqlite3.Row
        init_approvals(self.connection)
        requests.initialize(self.connection)
        attempts.initialize(self.connection)
        self.clock = [NOW]
        self.auth = LocalAuthService(self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0])
        self.issuer = SourcePrereadRequestService(self.auth, clock=lambda: self.clock[0])
        self.approvals = SourceApprovalService(self.auth, clock=lambda: self.clock[0])
        code = self.store.issue_pairing_code(BOOTSTRAP, now=self.clock[0])
        self.session = self.store.consume_pairing_code(code, now=self.clock[0])
        self.scope = SourceCaptureScopeV1(
            schema_version=1,
            root_path=r"C:\synthetic\fixture",
            root_identity=RootIdentity("windows_ntfs", 9, 17),
            relative_paths=("README.md", "src/main.py"),
            limits=SourceReadLimits(4, 4096, 8192),
        )
        self.reader_calls = []

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _observer(self):
        observer = sqlite3.connect(self.path, timeout=5)
        observer.row_factory = sqlite3.Row
        return observer

    def _pending(self, *, ttl=300):
        self.connection.execute("BEGIN IMMEDIATE")
        record = requests.create_request(
            self.connection, scope=self.scope,
            owner_id=self.session.principal.id, principal_id=self.session.principal.id,
            client_id="owner-browser",
            session_fingerprint=hashlib.sha256(self.session.token.encode()).hexdigest(),
            now=self.clock[0], ttl_seconds=ttl,
        )
        self.connection.commit()
        return record

    def _prepare_grant(self, *, ttl=300):
        request = self._pending()
        self.connection.execute("BEGIN IMMEDIATE")
        current = requests.get_request(self.connection, request.request_id)
        scope = requests.scope_from_request(current)
        grant = self.issuer.issue_for_request(
            self.connection, session_token=self.session.token,
            request_id=request.request_id, expected_revision=current.revision,
            expected_record_sha256=current.record_sha256, ttl_seconds=ttl,
        )
        return request, scope, grant

    def _prepare_attempt(self, *, ttl=300, token=None, consume_scope=None):
        request, scope, grant = self._prepare_grant(ttl=ttl)
        marker = self.approvals.consume_preread_capture_for_read(
            self.connection,
            session_token=self.session.token if token is None else token,
            scope=scope if consume_scope is None else consume_scope,
            approval=grant.approval,
        )
        return request, scope, grant, marker

    def _inert_reader_spy(self, scope, *, request_id, approval_id):
        """Observe durable metadata only; deliberately performs no source read."""
        observer = self._observer()
        try:
            row = observer.execute(
                "SELECT a.consumed_at,m.state,m.selection_sha256 "
                "FROM cae_source_approvals AS a JOIN cae_source_read_attempts AS m "
                "ON m.approval_id=a.approval_id WHERE a.approval_id=?",
                (approval_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row["consumed_at"])
            self.assertEqual(row["state"], "consumed_for_read")
            self.assertEqual(scope.canonical_bytes(), self.scope.canonical_bytes())
            self.reader_calls.append((request_id, approval_id, scope.canonical_bytes()))
        finally:
            observer.close()

    def _counts(self, observer):
        return (
            observer.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0],
            observer.execute("SELECT count(*) FROM cae_source_preread_issuances").fetchone()[0],
            observer.execute("SELECT count(*) FROM cae_source_read_attempts").fetchone()[0],
        )

    def test_spy_runs_once_only_after_commit_and_uses_stored_scope(self):
        request, scope, grant, marker = self._prepare_attempt()
        self.assertEqual(marker.state, "consumed_for_read")
        self.assertEqual(self.reader_calls, [])
        observer = self._observer()
        try:
            self.assertEqual(self._counts(observer), (0, 0, 0))
        finally:
            observer.close()
        self.connection.commit()
        self._inert_reader_spy(scope, request_id=request.request_id, approval_id=grant.approval.approval_id)
        self.assertEqual(len(self.reader_calls), 1)

    def test_known_rollback_invokes_no_spy_and_allows_explicit_fresh_attempt(self):
        request, _scope, _grant, _marker = self._prepare_attempt()
        self.connection.rollback()
        self.assertEqual(self.reader_calls, [])
        observer = self._observer()
        try:
            self.assertEqual(self._counts(observer), (0, 0, 0))
        finally:
            observer.close()
        self.assertEqual(requests.get_request(self.connection, request.request_id).state, "pending")

        self.connection.execute("BEGIN IMMEDIATE")
        current = requests.get_request(self.connection, request.request_id)
        scope = requests.scope_from_request(current)
        grant = self.issuer.issue_for_request(
            self.connection, session_token=self.session.token,
            request_id=request.request_id, expected_revision=current.revision,
            expected_record_sha256=current.record_sha256,
        )
        self.approvals.consume_preread_capture_for_read(
            self.connection, session_token=self.session.token, scope=scope, approval=grant.approval,
        )
        self.assertEqual(self.reader_calls, [])
        self.connection.commit()
        self._inert_reader_spy(scope, request_id=request.request_id, approval_id=grant.approval.approval_id)
        self.assertEqual(len(self.reader_calls), 1)

    def test_failed_and_ambiguous_commit_never_call_spy_or_auto_retry(self):
        request, _scope, _grant, _marker = self._prepare_attempt()
        self.connection.commit_fault = "before"
        with self.assertRaises(sqlite3.OperationalError):
            self.connection.commit()
        self.assertEqual(self.reader_calls, [])
        self.connection.rollback()
        observer = self._observer()
        try:
            self.assertEqual(self._counts(observer), (0, 0, 0))
        finally:
            observer.close()

        request, scope, grant, _marker = self._prepare_attempt()
        self.connection.commit_fault = "after"
        with self.assertRaises(sqlite3.OperationalError):
            self.connection.commit()
        self.assertEqual(self.reader_calls, [])
        observer = self._observer()
        try:
            self.assertEqual(self._counts(observer), (1, 1, 1))
        finally:
            observer.close()
        with self.assertRaises(SourceApprovalError):
            self.connection.execute("BEGIN IMMEDIATE")
            self.issuer.issue_for_request(
                self.connection, session_token=self.session.token,
                request_id=request.request_id, expected_revision=1,
                expected_record_sha256=request.record_sha256,
            )
        self.connection.rollback()
        self.assertEqual(self.reader_calls, [])

    def test_wrong_session_scope_and_expiry_never_reach_spy(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=self.clock[0])
        other = self.store.consume_pairing_code(code, now=self.clock[0])
        request, scope, grant = self._prepare_grant()
        with self.assertRaises((AuthServiceError, SourceApprovalError)):
            self.approvals.consume_preread_capture_for_read(
                self.connection, session_token=other.token, scope=scope, approval=grant.approval,
            )
        self.connection.rollback()
        self.assertEqual(self.reader_calls, [])

        request, scope, grant = self._prepare_grant()
        with self.assertRaises(SourceApprovalError):
            self.approvals.consume_preread_capture_for_read(
                self.connection, session_token=self.session.token,
                scope=SourceCaptureScopeV1(
                    schema_version=1, root_path=r"C:\synthetic\other",
                    root_identity=scope.root_identity, relative_paths=scope.relative_paths,
                    limits=scope.limits,
                ), approval=grant.approval,
            )
        self.connection.rollback()
        self.assertEqual(self.reader_calls, [])

        request, scope, grant = self._prepare_grant(ttl=1)
        self.clock[0] += timedelta(seconds=1)
        with self.assertRaises(SourceApprovalError):
            self.approvals.consume_preread_capture_for_read(
                self.connection, session_token=self.session.token, scope=scope, approval=grant.approval,
            )
        self.connection.rollback()
        self.assertEqual(self.reader_calls, [])
