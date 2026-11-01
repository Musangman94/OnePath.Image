from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.services.cae_source_approval_service import (
    SourceApprovalScopeV1,
    SourceApprovalService,
)
from app.storage.local_auth import LocalAuthStore
from app.storage.cae_source_approvals import (
    ApprovedFile,
    RootIdentity,
    SourceApprovalError,
    initialize as initialize_approvals,
)


T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-source-approval-service-bootstrap-0123456789"
ROTATED_BOOTSTRAP = "dummy-source-approval-service-rotated-0123456789"
ORIGIN = "http://127.0.0.1:8091"
CONTENT = b"owner-selected source bytes"


class SourceApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-approval-service-")
        self.path = Path(self.temp.name) / "auth-and-ledger.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-a", BOOTSTRAP, now=T0)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        initialize_approvals(self.connection)
        self.clock = [T0]
        self.auth = LocalAuthService(
            self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0],
        )
        self.service = SourceApprovalService(self.auth, clock=lambda: self.clock[0])
        self.session = self._new_session()
        self.files = [ApprovedFile(
            "src/main.py", hashlib.sha256(CONTENT).hexdigest(), len(CONTENT), "tracked",
        )]
        self.scope = self._scope()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=T0)
        return self.store.consume_pairing_code(code, now=T0)

    def _scope(self, **changes):
        values = {
            "root_path": r"C:\workspace\repo",
            "root_identity": RootIdentity("windows_ntfs", 12, 340),
            "files": tuple(self.files),
            "source_revision": "a" * 40,
            "provenance_ref": "source-" + "b" * 64,
            "exclusion_policy_version": "v1",
            "exclusion_policy_ref": "source-" + "c" * 64,
        }
        values.update(changes)
        return SourceApprovalScopeV1(**values)

    def _begin(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def _issue(self, *, token=None, scope=None, ttl=300):
        return self.service.issue(
            self.connection,
            session_token=self.session.token if token is None else token,
            scope=self.scope if scope is None else scope,
            ttl_seconds=ttl,
        )

    def _consume(self, grant, *, token=None, scope=None):
        return self.service.consume(
            self.connection,
            session_token=self.session.token if token is None else token,
            scope=self.scope if scope is None else scope,
            approval=grant.approval,
        )

    def _issue_and_commit(self, **kwargs):
        self._begin()
        grant = self._issue(**kwargs)
        self.connection.commit()
        return grant

    def test_identity_free_scope_snapshots_files_and_cannot_accept_auth_fields(self):
        fields_by_name = {item.name for item in fields(SourceApprovalScopeV1)}
        self.assertFalse({"owner_id", "principal_id", "client_id", "session_id"} & fields_by_name)
        with self.assertRaises(TypeError):
            SourceApprovalScopeV1(
                root_path=self.scope.root_path,
                root_identity=self.scope.root_identity,
                files=self.files,
                source_revision=self.scope.source_revision,
                provenance_ref=self.scope.provenance_ref,
                exclusion_policy_version=self.scope.exclusion_policy_version,
                exclusion_policy_ref=self.scope.exclusion_policy_ref,
                principal_id="caller-forged",
            )
        mutable = list(self.files)
        scope = self._scope(files=mutable)
        mutable.clear()
        self.assertEqual(scope.files, tuple(self.files))

    def test_issue_and_consume_bind_identity_only_from_authenticated_proof(self):
        self._begin()
        grant = self._issue()
        self.assertEqual(grant.selection.owner_id, self.session.principal.id)
        self.assertEqual(grant.selection.principal_id, self.session.principal.id)
        self.assertEqual(grant.selection.client_id, "owner-browser")
        self.assertEqual(grant.selection.session_id,
                         hashlib.sha256(self.session.token.encode("utf-8")).hexdigest())
        self.assertNotIn(self.session.token, repr(grant))
        self.assertTrue(self.connection.in_transaction)
        self.connection.commit()

        self._begin()
        selection = self._consume(grant)
        self.assertEqual(selection, grant.selection)
        self.connection.commit()

    def test_missing_transaction_and_wrong_database_fail_without_mutation(self):
        with self.assertRaises(AuthServiceError) as no_tx:
            self._issue()
        self.assertEqual(no_tx.exception.code, "AUTH_UNAVAILABLE")
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM cae_source_approvals"
        ).fetchone()[0], 0)

        other_path = Path(self.temp.name) / "other.sqlite3"
        other = sqlite3.connect(other_path)
        try:
            other.execute("BEGIN IMMEDIATE")
            with self.assertRaises(AuthServiceError):
                self.service.issue(other, session_token=self.session.token, scope=self.scope)
            self.assertTrue(other.in_transaction)
            self.assertEqual(self.connection.execute(
                "SELECT COUNT(*) FROM cae_source_approvals"
            ).fetchone()[0], 0)
            other.rollback()
        finally:
            other.close()

    def test_wrong_token_wrong_client_expiry_revocation_and_security_epoch_fail(self):
        self._begin()
        with self.assertRaises(AuthServiceError) as forged:
            self._issue(token="forged-cookie")
        self.assertEqual(forged.exception.code, "AUTHENTICATION_REQUIRED")
        self.connection.rollback()

        token_hash = hashlib.sha256(self.session.token.encode("utf-8")).digest()
        self.connection.execute(
            "UPDATE local_auth_sessions SET client_id='unrecognized-client' WHERE token_hash=?",
            (token_hash,),
        )
        self.connection.commit()
        self._begin()
        with self.assertRaises(AuthServiceError):
            self._issue()
        self.connection.rollback()

        fresh = self._new_session()
        self.clock[0] = T0 + timedelta(hours=8)
        self._begin()
        with self.assertRaises(AuthServiceError) as expired:
            self._issue(token=fresh.token)
        self.assertEqual(expired.exception.code, "AUTHENTICATION_REQUIRED")
        self.connection.rollback()

        self.clock[0] = T0 + timedelta(seconds=1)
        self.store.logout(fresh.token, now=self.clock[0])
        self._begin()
        with self.assertRaises(AuthServiceError):
            self._issue(token=fresh.token)
        self.connection.rollback()

        fresh = self._new_session()
        self.store.initialize("instance-b", ROTATED_BOOTSTRAP, now=T0 + timedelta(seconds=2))
        self._begin()
        with self.assertRaises(AuthServiceError):
            self._issue(token=fresh.token)
        self.connection.rollback()

    def test_distinct_valid_session_cannot_consume_another_sessions_approval(self):
        grant = self._issue_and_commit()
        other = self._new_session()
        self._begin()
        with self.assertRaisesRegex(SourceApprovalError, "APPROVAL_UNAVAILABLE"):
            self._consume(grant, token=other.token)
        self.connection.rollback()

        self._begin()
        self._consume(grant)
        self.connection.commit()

    def test_changed_metadata_and_references_leave_approval_unconsumed(self):
        grant = self._issue_and_commit()
        changed_scopes = (
            self._scope(root_path=r"C:\workspace\other"),
            self._scope(source_revision="d" * 40),
            self._scope(provenance_ref="source-" + "e" * 64),
            self._scope(exclusion_policy_ref="source-" + "f" * 64),
            self._scope(files=(ApprovedFile(
                "src/other.py", hashlib.sha256(CONTENT).hexdigest(), len(CONTENT), "tracked",
            ),)),
        )
        for scope in changed_scopes:
            self._begin()
            with self.assertRaisesRegex(SourceApprovalError, "APPROVAL_UNAVAILABLE"):
                self._consume(grant, scope=scope)
            self.assertTrue(self.connection.in_transaction)
            self.connection.rollback()
        self._begin()
        self._consume(grant)
        self.connection.commit()

    def test_expiry_boundary_and_replay_are_rejected(self):
        grant = self._issue_and_commit(ttl=1)
        self.clock[0] = T0 + timedelta(seconds=1)
        self._begin()
        with self.assertRaisesRegex(SourceApprovalError, "APPROVAL_UNAVAILABLE"):
            self._consume(grant)
        self.connection.rollback()

        self.clock[0] = T0
        grant = self._issue_and_commit()
        self._begin()
        self._consume(grant)
        self.connection.commit()
        self._begin()
        with self.assertRaisesRegex(SourceApprovalError, "APPROVAL_UNAVAILABLE"):
            self._consume(grant)
        self.connection.rollback()

    def test_issue_rollback_removes_nonce_and_caller_write(self):
        self.connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
        self.connection.commit()
        self._begin()
        self.connection.execute("INSERT INTO caller_state VALUES('rollback')")
        grant = self._issue()
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM cae_source_approvals"
        ).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 0)
        self._begin()
        with self.assertRaisesRegex(SourceApprovalError, "APPROVAL_UNAVAILABLE"):
            self._consume(grant)
        self.connection.rollback()

    def test_consume_rollback_restores_nonce_and_caller_write(self):
        self.connection.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
        self.connection.commit()
        grant = self._issue_and_commit()
        self._begin()
        self.connection.execute("INSERT INTO caller_state VALUES('rollback')")
        try:
            self._consume(grant)
            raise RuntimeError("simulated later persistence failure")
        except RuntimeError:
            self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 0)
        self._begin()
        self._consume(grant)
        self.connection.commit()


if __name__ == "__main__":
    unittest.main()
