from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.services.cae_source_approval_service import SourceApprovalService
from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage.cae_source_approvals import (
    RootIdentity,
    SourceApprovalError,
    consume_capture_approval,
    initialize as initialize_approvals,
)
from app.storage.local_auth import LocalAuthStore


T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-cae-preread-approval-bootstrap-0123456789"
ORIGIN = "http://127.0.0.1:8091"


class PreReadApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-preread-approval-")
        self.path = Path(self.temp.name) / "auth-and-ledger.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-preread", BOOTSTRAP, now=T0)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        initialize_approvals(self.connection)
        self.clock = [T0]
        self.auth = LocalAuthService(
            self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0],
        )
        self.service = SourceApprovalService(self.auth, clock=lambda: self.clock[0])
        self.session = self._new_session()
        self.scope = self._scope()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=self.clock[0])
        return self.store.consume_pairing_code(code, now=self.clock[0])

    def _scope(self, **changes):
        values = {
            "schema_version": 1,
            "root_path": r"C:\synthetic\source",
            "root_identity": RootIdentity("windows_ntfs", 31, 47),
            "relative_paths": ("README.md", "src/app.py"),
            "limits": SourceReadLimits(8, 4096, 8192),
        }
        values.update(changes)
        return SourceCaptureScopeV1(**values)

    def _begin(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def _issue(self, *, token=None, scope=None, ttl=300):
        return self.service.issue_preread_capture(
            self.connection,
            session_token=self.session.token if token is None else token,
            scope=self.scope if scope is None else scope,
            ttl_seconds=ttl,
        )

    def _consume(self, grant, *, token=None, scope=None):
        return self.service.consume_preread_capture(
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

    def _row(self, grant):
        return self.connection.execute(
            "SELECT selection_sha256,selection_json,secret_sha256,consumed_at "
            "FROM cae_source_approvals WHERE approval_id=?", (grant.approval.approval_id,),
        ).fetchone()

    def test_v2_binds_exact_scope_and_live_authenticated_identity(self):
        grant = self._issue_and_commit()
        row = self._row(grant)
        payload = bytes(row["selection_json"])
        record = json.loads(payload)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), row["selection_sha256"])
        self.assertEqual(record["schemaVersion"], 2)
        self.assertEqual(record["selectionKind"], "onepath.preread-source-capture")
        self.assertEqual(record["captureScope"], json.loads(self.scope.canonical_bytes()))
        self.assertEqual(record["authScope"]["ownerId"], self.session.principal.id)
        self.assertEqual(record["authScope"]["principalId"], self.session.principal.id)
        self.assertEqual(record["authScope"]["clientId"], "owner-browser")
        self.assertEqual(
            record["authScope"]["sessionId"],
            hashlib.sha256(self.session.token.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(self.session.token, payload.decode("utf-8"))
        self.assertNotIn("sha256", json.dumps(record["captureScope"]["relativePaths"]))

        changed_scopes = (
            self._scope(relative_paths=("README.md", "src/other.py")),
            self._scope(root_identity=RootIdentity("windows_ntfs", 31, 48)),
            self._scope(root_path=r"C:\synthetic\other"),
            self._scope(limits=SourceReadLimits(7, 4096, 8192)),
        )
        for changed in changed_scopes:
            self._begin()
            with self.assertRaises(SourceApprovalError):
                self._consume(grant, scope=changed)
            self.connection.rollback()

        self._begin()
        with self.assertRaises(SourceApprovalError):
            consume_capture_approval(
                self.connection, grant.approval, self.scope,
                owner_id="different-owner", principal_id="different-principal",
                client_id="owner-browser",
                session_id=hashlib.sha256(self.session.token.encode("utf-8")).hexdigest(),
                now=self.clock[0],
            )
        self.connection.rollback()

        other_session = self._new_session()
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._consume(grant, token=other_session.token)
        self.connection.rollback()

        self._begin()
        self._consume(grant)
        self.connection.commit()
        self.assertIsNotNone(self._row(grant)["consumed_at"])
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._consume(grant)
        self.connection.rollback()

    def test_revoked_session_and_expired_grant_fail_closed(self):
        grant = self._issue_and_commit(ttl=2)
        self.clock[0] += timedelta(seconds=2)
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._consume(grant)
        self.connection.rollback()

        self.clock[0] = T0
        revoked = self._issue_and_commit()
        self.auth.logout(self.session.token)
        self._begin()
        with self.assertRaises(AuthServiceError):
            self._consume(revoked)
        self.connection.rollback()

    def test_issue_and_consume_preserve_caller_transaction_rollback(self):
        self._begin()
        rolled_back = self._issue()
        self.connection.rollback()
        self.assertIsNone(self._row(rolled_back))

        grant = self._issue_and_commit()
        self._begin()
        self._consume(grant)
        self.connection.rollback()
        self.assertIsNone(self._row(grant)["consumed_at"])

        self._begin()
        self._consume(grant)
        self.connection.commit()
        self.assertIsNotNone(self._row(grant)["consumed_at"])


if __name__ == "__main__":
    unittest.main()
