import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from app.storage.cae_source_approvals import (
    ApprovedFile,
    IssuedApproval,
    RootIdentity,
    SourceApprovalError,
    SourceSelection,
    consume_approval,
    initialize,
    issue_approval,
)


NOW = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
DATA = b"owner-selected source bytes"


def selection(**changes):
    values = {
        "owner_id": "owner-1",
        "principal_id": "principal-1",
        "client_id": "onepath-browser",
        "session_id": "session-1",
        "root_path": r"C:\workspace\repo",
        "root_identity": RootIdentity("windows_ntfs", 12, 340),
        "files": (ApprovedFile("src/main.py", hashlib.sha256(DATA).hexdigest(), len(DATA), "tracked"),),
        "source_revision": "a" * 40,
        "provenance_ref": "status-sha256:" + "b" * 64,
        "exclusion_policy_version": "v1",
        "exclusion_policy_ref": "policy-sha256:" + "c" * 64,
    }
    values.update(changes)
    return SourceSelection(**values)


class SourceApprovalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-source-approvals-")
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.connection = sqlite3.connect(self.path, timeout=5)
        initialize(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def issue(self, selected=None, *, now=NOW, ttl=300):
        self.connection.execute("BEGIN IMMEDIATE")
        approval = issue_approval(self.connection, selected or selection(), now=now, ttl_seconds=ttl)
        self.connection.commit()
        return approval

    def test_canonical_selection_is_bound_and_secret_is_never_persisted_or_reprd(self):
        chosen = selection()
        approval = self.issue(chosen)
        self.assertEqual(len(approval.secret), 32)
        self.assertNotIn(approval.secret.hex(), repr(approval))
        self.assertEqual(approval.expires_at, "2026-11-01T04:05:00.000000Z")
        row = self.connection.execute(
            "SELECT selection_sha256,selection_json,secret_sha256 FROM cae_source_approvals"
        ).fetchone()
        self.assertEqual(row[0], hashlib.sha256(bytes(row[1])).hexdigest())
        self.assertEqual(row[2], hashlib.sha256(approval.secret).digest())
        self.assertNotIn(approval.secret, bytes(row[1]))
        self.assertNotIn(approval.secret, bytes(row[2]))
        self.connection.execute("BEGIN IMMEDIATE")
        consume_approval(self.connection, approval, chosen, now=NOW + timedelta(seconds=1))
        self.connection.commit()

    def test_file_order_has_one_canonical_selection_digest(self):
        first = ApprovedFile("a.txt", hashlib.sha256(b"a").hexdigest(), 1, "tracked")
        second = ApprovedFile("b.txt", hashlib.sha256(b"b").hexdigest(), 1, "untracked")
        self.issue(selection(files=(first, second)))
        self.issue(selection(files=(second, first)))
        rows = self.connection.execute(
            "SELECT selection_sha256 FROM cae_source_approvals ORDER BY approval_id"
        ).fetchall()
        self.assertEqual(rows[0][0], rows[1][0])

    def test_mismatch_scope_root_policy_file_and_provenance_fail_closed(self):
        approval = self.issue()
        for changed in (
            {"owner_id": "owner-2"}, {"principal_id": "principal-2"},
            {"client_id": "other-client"}, {"session_id": "session-2"},
            {"root_path": r"C:\other\repo"},
            {"root_identity": RootIdentity("windows_ntfs", 12, 341)},
            {"exclusion_policy_version": "v2"},
            {"exclusion_policy_ref": "policy-sha256:" + "d" * 64},
            {"source_revision": "d" * 40},
            {"provenance_ref": "status-sha256:" + "e" * 64},
            {"files": (ApprovedFile("src/main.py", "0" * 64, len(DATA), "tracked"),)},
        ):
            with self.subTest(changed=changed):
                self.connection.execute("BEGIN IMMEDIATE")
                with self.assertRaises(SourceApprovalError) as raised:
                    consume_approval(self.connection, approval, selection(**changed), now=NOW + timedelta(seconds=1))
                self.assertEqual(raised.exception.code, "APPROVAL_UNAVAILABLE")
                self.connection.rollback()
        self.connection.execute("BEGIN IMMEDIATE")
        consume_approval(self.connection, approval, selection(), now=NOW + timedelta(seconds=1))
        self.connection.commit()

    def test_expiry_boundary_replay_and_wrong_secret(self):
        approval = self.issue(ttl=1)
        self.connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(SourceApprovalError):
            consume_approval(self.connection, approval, selection(), now=NOW + timedelta(seconds=1))
        self.connection.rollback()
        wrong = IssuedApproval(approval.approval_id, b"x" * 32, approval.expires_at)
        self.connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(SourceApprovalError):
            consume_approval(self.connection, wrong, selection(), now=NOW + timedelta(milliseconds=1))
        self.connection.rollback()
        self.connection.execute("BEGIN IMMEDIATE")
        consume_approval(self.connection, approval, selection(), now=NOW + timedelta(milliseconds=1))
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(SourceApprovalError):
            consume_approval(self.connection, approval, selection(), now=NOW + timedelta(milliseconds=2))
        self.connection.rollback()

    def test_invalid_selection_and_ttl_are_rejected(self):
        invalid = (
            ("../secret",), ("/absolute",), (r"a\b",), ("a//b",), ("a/./b",), ("\ud800",),
            ("a/../b",), ("dir/A.py", "dir/a.PY"),
        )
        for paths in invalid:
            files = tuple(ApprovedFile(path, "0" * 64, 1, "tracked") for path in paths)
            with self.subTest(paths=paths):
                self.connection.execute("BEGIN IMMEDIATE")
                with self.assertRaises(SourceApprovalError):
                    issue_approval(self.connection, selection(files=files), now=NOW)
                self.connection.rollback()
        bad_sizes = (True, -1, 32 * 1024 * 1024 + 1)
        for size in bad_sizes:
            self.connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(SourceApprovalError):
                issue_approval(self.connection, selection(files=(ApprovedFile("x", "0" * 64, size, "tracked"),)), now=NOW)
            self.connection.rollback()
        for ttl in (0, -1, 901, True, 1.5):
            self.connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(SourceApprovalError):
                issue_approval(self.connection, selection(), now=NOW, ttl_seconds=ttl)
            self.connection.rollback()

    def test_file_ancestor_collisions_fail_in_both_orders_and_siblings_pass(self):
        cases = (
            ("a", "a/b"), ("a/b", "a"),
            ("A", "a/b"), ("a/b", "A"),
            ("a", "a/b/c"), ("a/b/c", "a"),
        )
        for first_path, second_path in cases:
            with self.subTest(paths=(first_path, second_path)):
                files = (
                    ApprovedFile(first_path, "0" * 64, 1, "tracked"),
                    ApprovedFile(second_path, "1" * 64, 1, "tracked"),
                )
                before = self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0]
                self.connection.execute("BEGIN IMMEDIATE")
                with self.assertRaises(SourceApprovalError):
                    issue_approval(self.connection, selection(files=files), now=NOW)
                self.assertTrue(self.connection.in_transaction)
                self.assertEqual(self.connection.execute(
                    "SELECT count(*) FROM cae_source_approvals"
                ).fetchone()[0], before)
                self.connection.rollback()

        siblings = (
            ApprovedFile("a", "0" * 64, 1, "tracked"),
            ApprovedFile("ab/b", "1" * 64, 1, "tracked"),
        )
        self.connection.execute("BEGIN IMMEDIATE")
        issue_approval(self.connection, selection(files=siblings), now=NOW)
        self.connection.rollback()

    def test_mutations_require_outer_transaction_and_explicit_rollback_removes_issue(self):
        with self.assertRaises(SourceApprovalError) as raised:
            issue_approval(self.connection, selection(), now=NOW)
        self.assertEqual(raised.exception.code, "TRANSACTION_REQUIRED")
        self.connection.execute("BEGIN IMMEDIATE")
        issue_approval(self.connection, selection(), now=NOW)
        self.connection.rollback()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)

    def test_consumption_and_artifact_insert_rollback_together(self):
        approval = self.issue()
        self.connection.execute("CREATE TABLE test_source_objects(value TEXT)")
        self.connection.execute("BEGIN IMMEDIATE")
        consume_approval(self.connection, approval, selection(), now=NOW + timedelta(seconds=1))
        self.connection.execute("INSERT INTO test_source_objects VALUES('artifact')")
        self.connection.rollback()
        self.assertIsNone(self.connection.execute(
            "SELECT consumed_at FROM cae_source_approvals WHERE approval_id=?", (approval.approval_id,)
        ).fetchone()[0])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM test_source_objects").fetchone()[0], 0)

    def test_two_connections_have_one_durable_consumer(self):
        approval = self.issue()
        outcomes = []
        barrier = threading.Barrier(2)

        def worker():
            connection = sqlite3.connect(self.path, timeout=5)
            try:
                barrier.wait()
                connection.execute("BEGIN IMMEDIATE")
                try:
                    consume_approval(connection, approval, selection(), now=NOW + timedelta(seconds=1))
                    connection.commit()
                    outcomes.append("consumed")
                except SourceApprovalError:
                    connection.rollback()
                    outcomes.append("rejected")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["consumed", "rejected"])
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cae_source_approvals WHERE consumed_at IS NOT NULL"
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
