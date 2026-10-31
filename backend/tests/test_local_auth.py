"""Isolated local authentication store tests; never touches owner application DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.storage.local_auth import LocalAuthError, LocalAuthStore  # noqa: E402

SECRET = "dummy-bootstrap-secret-0123456789abcdef0123456789"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class LocalAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="onepath-local-auth-")
        self.db = Path(self.temp.name) / "isolated.sqlite3"
        self.store = LocalAuthStore(self.db)
        self.store.initialize("instance-a", SECRET, now=T0)

    def tearDown(self): self.temp.cleanup()

    def test_bootstrap_verification_pairing_and_only_hashes_persist(self):
        with self.assertRaises(LocalAuthError): self.store.issue_pairing_code("wrong", now=T0)
        code = self.store.issue_pairing_code(SECRET, now=T0)
        session = self.store.consume_pairing_code(code, now=T0)
        self.assertEqual(session.principal.kind, "owner")
        self.assertEqual(session.client_id, "owner-browser")
        self.assertEqual(self.store.get_session(session.token, now=T0), session.principal)
        metadata = self.store.get_session_metadata(session.token, now=T0)
        self.assertEqual((metadata.principal, metadata.client_id, metadata.expires_at), (session.principal, "owner-browser", session.expires_at))
        self.assertFalse(hasattr(metadata, "token"))
        self.store.logout(session.token, now=T0)
        self.assertIsNone(self.store.get_session(session.token, now=T0))
        with closing(sqlite3.connect(self.db)) as conn:
            contents = repr(conn.execute("SELECT * FROM local_auth_bootstrap_epochs").fetchall()) + repr(conn.execute("SELECT * FROM local_auth_pairing_codes").fetchall()) + repr(conn.execute("SELECT * FROM local_auth_sessions").fetchall())
        self.assertNotIn(SECRET, contents)
        self.assertNotIn(code, contents)
        self.assertNotIn(session.token, contents)

    def test_pairing_code_is_single_consume_under_concurrent_attempts(self):
        code = self.store.issue_pairing_code(SECRET, now=T0)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self._consume(code), range(8)))
        self.assertEqual(sum(x is not None for x in results), 1)

    def _consume(self, code):
        try: return self.store.consume_pairing_code(code, now=T0)
        except LocalAuthError: return None

    def test_expiry_replay_logout_and_absolute_session_expiry(self):
        code = self.store.issue_pairing_code(SECRET, now=T0)
        with self.assertRaises(LocalAuthError) as exact_pair_expiry:
            self.store.consume_pairing_code(code, now=T0 + timedelta(seconds=300))
        self.assertEqual(exact_pair_expiry.exception.code, "PAIRING_DENIED")
        with self.assertRaises(LocalAuthError): self.store.consume_pairing_code(code, now=T0)
        code = self.store.issue_pairing_code(SECRET, now=T0)
        with self.assertRaises(LocalAuthError): self.store.consume_pairing_code(code, now=T0 + timedelta(seconds=301))
        with self.assertRaises(LocalAuthError): self.store.consume_pairing_code(code, now=T0)
        code = self.store.issue_pairing_code(SECRET, now=T0)
        session = self.store.consume_pairing_code(code, now=T0)
        self.assertEqual(self.store.get_session(session.token, now=T0 + timedelta(hours=8) - timedelta(microseconds=1)), session.principal)
        self.assertIsNone(self.store.get_session(session.token, now=T0 + timedelta(hours=8)))

    def test_all_public_clock_inputs_require_aware_utc(self):
        bad_times = (datetime(2026, 1, 1), T0.astimezone(timezone(timedelta(hours=8))))
        for bad_time in bad_times:
            with self.subTest(now=bad_time):
                checks = (
                    lambda: LocalAuthStore(self.db).initialize("bad-clock", SECRET, now=bad_time),
                    lambda: self.store.issue_pairing_code(SECRET, now=bad_time),
                    lambda: self.store.consume_pairing_code("dummy", now=bad_time),
                    lambda: self.store.get_session("dummy", now=bad_time),
                    lambda: self.store.logout("dummy", now=bad_time),
                )
                for check in checks:
                    with self.assertRaises(LocalAuthError) as caught: check()
                    self.assertEqual(caught.exception.code, "INVALID_TIME")

    def test_new_instance_invalidates_old_sessions_and_old_bootstrap(self):
        code = self.store.issue_pairing_code(SECRET, now=T0)
        session = self.store.consume_pairing_code(code, now=T0)
        self.store.initialize("instance-b", "new-bootstrap-secret-0123456789abcdef0123456789", now=T0 + timedelta(seconds=1))
        self.assertIsNone(self.store.get_session(session.token, now=T0 + timedelta(seconds=2)))
        with self.assertRaises(LocalAuthError): self.store.issue_pairing_code(SECRET, now=T0)

    def test_additive_schema_preserves_unrelated_owner_tables_and_ids(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE owner_history(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO owner_history VALUES('preserve-id','original')")
            conn.commit()
        self.store.initialize("instance-a", SECRET, now=T0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id,value FROM owner_history").fetchall(), [("preserve-id", "original")])


if __name__ == "__main__": unittest.main()
