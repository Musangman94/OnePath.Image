from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.local_auth_service import AuthServiceError, LocalAuthService  # noqa: E402
from app.storage.local_auth import LocalAuthStore  # noqa: E402


T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
BOOTSTRAP = "dummy-source-session-bootstrap-secret-0123456789"
ROTATED_BOOTSTRAP = "dummy-source-session-rotated-bootstrap-secret-012345"
ORIGIN = "http://127.0.0.1:8091"


class SourceSessionTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="source-session-transaction-")
        self.connections = []
        self.path = Path(self.temp.name) / "auth.sqlite3"
        self.store = LocalAuthStore(self.path)
        self.store.initialize("instance-a", BOOTSTRAP, now=T0)
        self.clock = [T0]
        self.service = LocalAuthService(self.store, trusted_origin=ORIGIN, clock=lambda: self.clock[0])
        self.session = self._new_session()

    def tearDown(self):
        for connection in reversed(self.connections):
            connection.close()
        self.temp.cleanup()

    def _new_session(self):
        code = self.store.issue_pairing_code(BOOTSTRAP, now=T0)
        return self.store.consume_pairing_code(code, now=T0)

    def _connection(self, path=None, *, timeout=5):
        connection = sqlite3.connect(path or self.path, timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=100")
        self.connections.append(connection)
        return connection

    def test_live_cookie_proves_owner_stored_client_and_nonsecret_session_fingerprint(self):
        proof_conn = self._connection()
        proof_conn.execute("CREATE TABLE caller_probe(value TEXT)")
        proof_conn.commit()
        proof_conn.execute("BEGIN IMMEDIATE")
        proof_conn.execute("INSERT INTO caller_probe VALUES('uncommitted')")

        proof = self.service.authenticate_session(self.session.token, connection=proof_conn)

        self.assertEqual(proof.owner.principal, self.session.principal)
        self.assertEqual(proof.owner.client_id, "onepath-browser")
        self.assertEqual(proof.stored_client_id, "owner-browser")
        self.assertEqual(proof.session_fingerprint, hashlib.sha256(self.session.token.encode("utf-8")).hexdigest())
        self.assertNotIn(self.session.token, repr(proof))
        self.assertTrue(proof_conn.in_transaction)
        self.assertEqual(proof_conn.execute("SELECT value FROM caller_probe").fetchone()[0], "uncommitted")

        other = self._connection()
        try:
            self.assertEqual(other.execute("SELECT count(*) FROM caller_probe").fetchone()[0], 0)
        finally:
            other.close()
        proof_conn.rollback()
        self.assertEqual(proof_conn.execute("SELECT count(*) FROM caller_probe").fetchone()[0], 0)

    def test_forged_cookie_is_rejected_without_ending_callers_transaction(self):
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError) as raised:
            self.service.authenticate_session("forged-cookie", connection=connection)
        self.assertEqual((raised.exception.status, raised.exception.code), (401, "AUTHENTICATION_REQUIRED"))
        self.assertTrue(connection.in_transaction)
        connection.rollback()

    def test_unrecognized_stored_client_cannot_become_an_authenticated_owner(self):
        update = self._connection()
        token_hash = hashlib.sha256(self.session.token.encode("utf-8")).digest()
        update.execute(
            "UPDATE local_auth_sessions SET client_id='unrecognized-client' WHERE token_hash=?",
            (token_hash,),
        )
        update.commit()
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError) as raised:
            self.service.authenticate_session(self.session.token, connection=connection)
        self.assertEqual((raised.exception.status, raised.exception.code), (401, "AUTHENTICATION_REQUIRED"))
        connection.rollback()

    def test_requires_active_transaction_and_configured_database(self):
        no_tx = self._connection()
        with self.assertRaises(AuthServiceError) as raised:
            self.service.authenticate_session(self.session.token, connection=no_tx)
        self.assertEqual((raised.exception.status, raised.exception.code), (503, "AUTH_UNAVAILABLE"))
        self.assertFalse(no_tx.in_transaction)

        other_path = Path(self.temp.name) / "other.sqlite3"
        wrong = self._connection(other_path)
        wrong.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError) as raised:
            self.service.authenticate_session(self.session.token, connection=wrong)
        self.assertEqual((raised.exception.status, raised.exception.code), (503, "AUTH_UNAVAILABLE"))
        self.assertTrue(wrong.in_transaction)
        wrong.rollback()

        memory = self._connection(":memory:")
        memory.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            self.service.authenticate_session(self.session.token, connection=memory)
        memory.rollback()

    def test_connection_bound_store_accepts_only_its_exact_connection(self):
        shared = self._connection(":memory:")
        store = LocalAuthStore(connection=shared)
        store.initialize("memory-instance", BOOTSTRAP, now=T0)
        code = store.issue_pairing_code(BOOTSTRAP, now=T0)
        session = store.consume_pairing_code(code, now=T0)
        service = LocalAuthService(store, trusted_origin=ORIGIN, clock=lambda: T0)
        shared.execute("BEGIN IMMEDIATE")
        proof = service.authenticate_session(session.token, connection=shared)
        self.assertEqual(proof.owner.principal, session.principal)
        self.assertTrue(shared.in_transaction)
        shared.rollback()

        other = self._connection(":memory:")
        other.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            service.authenticate_session(session.token, connection=other)
        self.assertTrue(other.in_transaction)
        other.rollback()

    def test_expiry_revocation_and_security_epoch_are_rechecked(self):
        connection = self._connection()
        self.clock[0] = T0 + timedelta(hours=8)
        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError) as expired:
            self.service.authenticate_session(self.session.token, connection=connection)
        self.assertEqual(expired.exception.code, "AUTHENTICATION_REQUIRED")
        connection.rollback()

        self.clock[0] = T0 + timedelta(seconds=1)
        self.store.logout(self.session.token, now=self.clock[0])
        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            self.service.authenticate_session(self.session.token, connection=connection)
        connection.rollback()

        fresh = self._new_session()
        self.store.initialize("instance-b", ROTATED_BOOTSTRAP, now=T0 + timedelta(seconds=2))
        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            self.service.authenticate_session(fresh.token, connection=connection)
        connection.rollback()

    def test_writer_order_serializes_capture_and_logout(self):
        capture = self._connection(timeout=0.1)
        logout = self._connection(timeout=0.1)

        # Logout commits first; the later capture transaction observes revocation.
        self.store.logout(self.session.token, now=T0 + timedelta(seconds=1))
        capture.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            self.service.authenticate_session(self.session.token, connection=capture)
        capture.rollback()

        # Capture gets the writer lock and proves the cookie. Logout cannot
        # interleave before the owner commits that same unit of work.
        fresh = self._new_session()
        capture.execute("BEGIN IMMEDIATE")
        proof = self.service.authenticate_session(fresh.token, connection=capture)
        self.assertEqual(proof.session_fingerprint, hashlib.sha256(fresh.token.encode()).hexdigest())
        with self.assertRaises(sqlite3.OperationalError):
            logout.execute("BEGIN IMMEDIATE")
        self.assertFalse(logout.in_transaction)
        capture.commit()

        logout.execute("BEGIN IMMEDIATE")
        logout.execute(
            "UPDATE local_auth_sessions SET revoked_at=? WHERE token_hash=?",
            ((T0 + timedelta(seconds=3)).isoformat(timespec="microseconds").replace("+00:00", "Z"),
             hashlib.sha256(fresh.token.encode("utf-8")).digest()),
        )
        logout.commit()
        capture.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AuthServiceError):
            self.service.authenticate_session(fresh.token, connection=capture)
        capture.rollback()


if __name__ == "__main__":
    unittest.main()
