from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.services.cae_source_approval_service import SourceApprovalScopeV1, SourceApprovalService
from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.services.local_auth_service import LocalAuthService
from app.storage import cae_source_read_attempts as attempts
from app.storage.cae_source_approvals import (
    ApprovedFile,
    RootIdentity,
    SourceApprovalError,
    initialize as init_approvals,
)
from app.storage.local_auth import LocalAuthStore


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-cae-read-attempt-bootstrap-0123456789"
ORIGIN = "http://127.0.0.1:8091"


class SourceReadAttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-read-attempt-")
        self.path = Path(self.temp.name) / "auth-and-ledger.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-read-attempt", BOOTSTRAP, now=NOW)
        self.connection = self._connect()
        init_approvals(self.connection)
        attempts.initialize(self.connection)
        self.clock = [NOW]
        self.auth = LocalAuthService(self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0])
        self.service = SourceApprovalService(self.auth, clock=lambda: self.clock[0])
        self.session = self._new_session()
        self.scope = self._scope()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=self.clock[0])
        return self.store.consume_pairing_code(code, now=self.clock[0])

    def _scope(self, **changes):
        values = {
            "schema_version": 1,
            "root_path": r"C:\synthetic\repo",
            "root_identity": RootIdentity("windows_ntfs", 50, 60),
            "relative_paths": ("README.md", "src/main.py"),
            "limits": SourceReadLimits(8, 4096, 8192),
        }
        values.update(changes)
        return SourceCaptureScopeV1(**values)

    def _begin(self, connection=None):
        (connection or self.connection).execute("BEGIN IMMEDIATE")

    def _issue(self):
        self._begin()
        grant = self.service.issue_preread_capture(
            self.connection, session_token=self.session.token, scope=self.scope,
        )
        self.connection.commit()
        return grant

    def _attempt(self, grant, *, scope=None, token=None, connection=None):
        return self.service.consume_preread_capture_for_read(
            connection or self.connection,
            session_token=self.session.token if token is None else token,
            scope=self.scope if scope is None else scope,
            approval=grant.approval,
        )

    def _rows(self, connection=None):
        connection = connection or self.connection
        approval = connection.execute(
            "SELECT selection_sha256,consumed_at FROM cae_source_approvals"
        ).fetchone()
        markers = connection.execute(
            "SELECT attempt_id,approval_id,selection_sha256,consumed_at,state "
            "FROM cae_source_read_attempts"
        ).fetchall()
        return approval, markers

    def test_marker_and_consumption_are_invisible_until_outer_commit_and_rollback_together(self):
        grant = self._issue()
        observer = self._connect()
        try:
            self._begin()
            marker = self._attempt(grant)
            self.assertEqual(marker.state, "consumed_for_read")
            self.assertTrue(self.connection.in_transaction)
            approval, rows = self._rows(observer)
            self.assertIsNone(approval["consumed_at"])
            self.assertEqual(rows, [])
            self.connection.rollback()

            approval, rows = self._rows(observer)
            self.assertIsNotNone(approval)
            self.assertIsNone(approval["consumed_at"])
            self.assertEqual(rows, [])

            self._begin()
            marker = self._attempt(grant)
            self.connection.commit()
            approval, rows = self._rows(observer)
            self.assertIsNotNone(approval["consumed_at"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["attempt_id"], marker.attempt_id)
            self.assertEqual(rows[0]["approval_id"], grant.approval.approval_id)
            self.assertEqual(rows[0]["selection_sha256"], approval["selection_sha256"])
            self.assertEqual(rows[0]["consumed_at"], approval["consumed_at"])
            self.assertEqual(rows[0]["state"], "consumed_for_read")
        finally:
            observer.close()

    def test_insert_fault_rolls_back_ledger_consumption_to_savepoint(self):
        grant = self._issue()
        self._begin()
        with patch.object(attempts, "record_consumed_marker", side_effect=SourceApprovalError("DATABASE_FAILURE")):
            with self.assertRaises(SourceApprovalError):
                self._attempt(grant)
        row = self.connection.execute(
            "SELECT consumed_at FROM cae_source_approvals WHERE approval_id=?",
            (grant.approval.approval_id,),
        ).fetchone()
        self.assertIsNone(row["consumed_at"])
        self.connection.commit()
        self.assertEqual(self._rows()[1], [])

    def test_mismatch_replay_and_duplicate_marker_fail_without_extra_rows(self):
        grant = self._issue()
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._attempt(grant, scope=self._scope(root_identity=RootIdentity("windows_ntfs", 50, 61)))
        self.connection.rollback()

        other_session = self._new_session()
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._attempt(grant, token=other_session.token)
        self.connection.rollback()

        self._begin()
        marker = self._attempt(grant)
        self.connection.commit()
        selection_json = bytes(self.connection.execute(
            "SELECT selection_json FROM cae_source_approvals WHERE approval_id=?",
            (grant.approval.approval_id,),
        ).fetchone()[0])
        self._begin()
        with self.assertRaises(SourceApprovalError):
            attempts.record_consumed_marker(
                self.connection, approval_id=grant.approval.approval_id,
                selection_json=selection_json,
                selection_sha256=marker.selection_sha256,
            )
        with self.assertRaises(SourceApprovalError):
            attempts.record_consumed_marker(
                self.connection, approval_id=grant.approval.approval_id,
                selection_json=selection_json,
                selection_sha256="0" * 64,
            )
        self.connection.rollback()
        self.assertEqual(len(self._rows()[1]), 1)

        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._attempt(grant)
        self.connection.rollback()

    def test_marker_rejects_tampered_v2_payloads_and_digests(self):
        grant = self._issue()
        row = self.connection.execute(
            "SELECT selection_json,selection_sha256 FROM cae_source_approvals WHERE approval_id=?",
            (grant.approval.approval_id,),
        ).fetchone()
        payload = bytes(row["selection_json"])
        record = json.loads(payload)
        tampered_values = []
        wrong_purpose = dict(record)
        wrong_purpose["selectionKind"] = "onepath.source-selection"
        tampered_values.append(json.dumps(wrong_purpose, sort_keys=True, separators=(",", ":")).encode())
        wrong_policy = json.loads(payload)
        wrong_policy["captureScope"]["policyRef"]["sha256"] = "0" * 64
        tampered_values.append(json.dumps(wrong_policy, sort_keys=True, separators=(",", ":")).encode())
        for altered in tampered_values:
            self._begin()
            with self.assertRaises(SourceApprovalError):
                attempts.record_consumed_marker(
                    self.connection, approval_id=grant.approval.approval_id,
                    selection_json=altered, selection_sha256=hashlib.sha256(altered).hexdigest(),
                )
            self.connection.rollback()

        self._begin()
        with self.assertRaises(SourceApprovalError):
            attempts.record_consumed_marker(
                self.connection, approval_id=grant.approval.approval_id,
                selection_json=payload, selection_sha256="0" * 64,
            )
        self.connection.rollback()
        self.assertEqual(self._rows()[1], [])

    def test_marker_rejects_consumed_v1_approval_for_other_purpose(self):
        v1_scope = SourceApprovalScopeV1(
            root_path=self.scope.root_path,
            root_identity=self.scope.root_identity,
            files=(ApprovedFile("src/main.py", "a" * 64, 1, "tracked"),),
            source_revision=None,
            provenance_ref="source-" + "b" * 64,
            exclusion_policy_version="v1",
            exclusion_policy_ref="source-" + "c" * 64,
        )
        self._begin()
        legacy = self.service.issue(
            self.connection, session_token=self.session.token, scope=v1_scope,
        )
        self.connection.commit()
        self._begin()
        self.service.consume(
            self.connection, session_token=self.session.token, scope=v1_scope,
            approval=legacy.approval,
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT selection_json,selection_sha256,consumed_at FROM cae_source_approvals WHERE approval_id=?",
            (legacy.approval.approval_id,),
        ).fetchone()
        self.assertIsNotNone(row["consumed_at"])
        self._begin()
        with self.assertRaises(SourceApprovalError):
            attempts.record_consumed_marker(
                self.connection, approval_id=legacy.approval.approval_id,
                selection_json=bytes(row["selection_json"]),
                selection_sha256=row["selection_sha256"],
            )
        self.connection.rollback()
        self.assertEqual(self._rows()[1], [])

    def test_expiry_and_revocation_do_not_create_markers(self):
        self._begin()
        grant = self.service.issue_preread_capture(
            self.connection, session_token=self.session.token, scope=self.scope, ttl_seconds=2,
        )
        self.connection.commit()
        self.clock[0] = NOW.replace(second=3)
        self._begin()
        with self.assertRaises(SourceApprovalError):
            self._attempt(grant)
        self.connection.rollback()
        self.assertEqual(self._rows()[1], [])

        self.clock[0] = NOW
        revoked = self._issue()
        self.auth.logout(self.session.token)
        self._begin()
        with self.assertRaises(Exception):
            self._attempt(revoked)
        self.connection.rollback()
        self.assertEqual(self._rows()[1], [])

    def test_competing_consumers_create_at_most_one_marker(self):
        grant = self._issue()
        barrier = threading.Barrier(2)
        results: list[str] = []

        def consume():
            connection = self._connect()
            try:
                barrier.wait(timeout=3)
                connection.execute("BEGIN IMMEDIATE")
                self._attempt(grant, connection=connection)
                connection.commit()
                results.append("consumed")
            except SourceApprovalError:
                if connection.in_transaction:
                    connection.rollback()
                results.append("rejected")
            finally:
                connection.close()

        workers = [threading.Thread(target=consume) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=6)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertCountEqual(results, ["consumed", "rejected"])
        self.assertEqual(len(self._rows()[1]), 1)


if __name__ == "__main__":
    unittest.main()
