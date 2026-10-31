"""Additive local-owner pairing and session persistence; no HTTP integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
import uuid


class LocalAuthError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LocalPrincipal:
    id: str
    kind: str


@dataclass(frozen=True)
class LocalSession:
    token: str
    principal: LocalPrincipal
    client_id: str
    expires_at: str


@dataclass(frozen=True)
class LocalSessionMetadata:
    principal: LocalPrincipal
    client_id: str
    expires_at: str
    session_fingerprint: str


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise LocalAuthError("INVALID_TIME")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class LocalAuthStore:
    PAIR_TTL = 300
    SESSION_TTL = 8 * 60 * 60
    OWNER_BROWSER_CLIENT = "owner-browser"

    def __init__(self, path: Path | str | None = None, *, connection: sqlite3.Connection | None = None):
        if (path is None) == (connection is None):
            raise ValueError("Provide exactly one database path or connection.")
        self.path = Path(path) if path is not None else None
        self._provided = connection

    def _connection(self):
        if self._provided is not None:
            return self._provided, False
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection, True

    def _check_shared_connection(self, connection: sqlite3.Connection) -> None:
        """Require the caller's transaction to use this store's configured database."""
        if self._provided is not None:
            if connection is not self._provided:
                raise LocalAuthError("AUTH_DATABASE_MISMATCH")
            return
        if self.path is None or str(self.path) == ":memory:":
            raise LocalAuthError("AUTH_DATABASE_MISMATCH")
        try:
            rows = connection.execute("PRAGMA database_list").fetchall()
            main_path = next((row[2] for row in rows if row[1] == "main"), "")
            if not main_path:
                raise LocalAuthError("AUTH_DATABASE_MISMATCH")
            if Path(main_path).resolve(strict=True) != self.path.resolve(strict=True):
                raise LocalAuthError("AUTH_DATABASE_MISMATCH")
        except LocalAuthError:
            raise
        except (OSError, sqlite3.Error, StopIteration, RuntimeError):
            raise LocalAuthError("AUTH_DATABASE_MISMATCH") from None

    def _transaction(self):
        connection, owned = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        return connection, owned

    def is_initialized(self) -> bool:
        """Return only whether a current bootstrap epoch exists."""
        if self.path is not None and not self.path.exists():
            return False
        connection, owned = self._connection()
        try:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_auth_bootstrap_epochs'").fetchone()
            if not exists:
                return False
            return connection.execute("SELECT 1 FROM local_auth_bootstrap_epochs WHERE singleton=1").fetchone() is not None
        except sqlite3.Error:
            return False
        finally:
            if owned: connection.close()

    @staticmethod
    def _finish(connection, owned, error=None):
        if error is None: connection.commit()
        else: connection.rollback()
        if owned: connection.close()

    def initialize(self, instance_id: str, bootstrap_secret: str, *, now: datetime | None = None) -> None:
        if not instance_id or len(bootstrap_secret) < 40:
            raise LocalAuthError("INVALID_BOOTSTRAP")
        stamp = _iso(now or datetime.now(timezone.utc))
        secret_hash = _digest(bootstrap_secret)
        connection, owned = self._transaction()
        try:
            for statement in ("""CREATE TABLE IF NOT EXISTS local_auth_principals(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind='owner'), created_at TEXT NOT NULL);
                """ , """CREATE TABLE IF NOT EXISTS local_auth_bootstrap_epochs(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id TEXT NOT NULL,
                security_epoch INTEGER NOT NULL, bootstrap_hash BLOB NOT NULL, created_at TEXT NOT NULL);
                """ , """CREATE TABLE IF NOT EXISTS local_auth_pairing_codes(
                code_hash BLOB PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES local_auth_principals(id),
                client_id TEXT NOT NULL, expires_at TEXT NOT NULL, security_epoch INTEGER NOT NULL);
                """ , """CREATE TABLE IF NOT EXISTS local_auth_sessions(
                token_hash BLOB PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES local_auth_principals(id),
                client_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                security_epoch INTEGER NOT NULL, revoked_at TEXT);"""):
                connection.execute(statement)
            principal = connection.execute("SELECT id FROM local_auth_principals WHERE kind='owner' ORDER BY created_at LIMIT 1").fetchone()
            if principal is None:
                principal_id = str(uuid.uuid4())
                connection.execute("INSERT INTO local_auth_principals(id,kind,created_at) VALUES(?,?,?)", (principal_id, "owner", stamp))
            else:
                principal_id = principal[0]
            current = connection.execute("SELECT instance_id,security_epoch FROM local_auth_bootstrap_epochs WHERE singleton=1").fetchone()
            if current is None:
                epoch = 1
            elif current[0] == instance_id:
                old_hash = connection.execute("SELECT bootstrap_hash FROM local_auth_bootstrap_epochs WHERE singleton=1").fetchone()[0]
                if not hmac.compare_digest(bytes(old_hash), secret_hash):
                    raise LocalAuthError("BOOTSTRAP_MISMATCH")
                epoch = current[1]
            else:
                epoch = current[1] + 1
                connection.execute("DELETE FROM local_auth_pairing_codes")
                connection.execute("UPDATE local_auth_sessions SET revoked_at=COALESCE(revoked_at,?)", (stamp,))
            connection.execute("INSERT INTO local_auth_bootstrap_epochs(singleton,instance_id,security_epoch,bootstrap_hash,created_at) VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET instance_id=excluded.instance_id,security_epoch=excluded.security_epoch,bootstrap_hash=excluded.bootstrap_hash,created_at=excluded.created_at", (instance_id, epoch, secret_hash, stamp))
            self._finish(connection, owned)
        except Exception as exc:
            self._finish(connection, owned, exc)
            if isinstance(exc, LocalAuthError): raise
            raise LocalAuthError("AUTH_STORAGE") from None

    def issue_pairing_code(self, bootstrap_secret: str, *, now: datetime | None = None) -> str:
        moment = now or datetime.now(timezone.utc)
        _iso(moment)
        expires = _iso(moment + timedelta(seconds=self.PAIR_TTL))
        code = secrets.token_urlsafe(32)
        connection, owned = self._transaction()
        try:
            row = connection.execute("SELECT instance_id,security_epoch,bootstrap_hash FROM local_auth_bootstrap_epochs WHERE singleton=1").fetchone()
            if row is None or not hmac.compare_digest(bytes(row[2]), _digest(bootstrap_secret)):
                raise LocalAuthError("BOOTSTRAP_DENIED")
            principal = connection.execute("SELECT id FROM local_auth_principals WHERE kind='owner' ORDER BY created_at LIMIT 1").fetchone()
            connection.execute("INSERT INTO local_auth_pairing_codes(code_hash,principal_id,client_id,expires_at,security_epoch) VALUES(?,?,?,?,?)", (_digest(code), principal[0], self.OWNER_BROWSER_CLIENT, expires, row[1]))
            self._finish(connection, owned)
            return code
        except Exception as exc:
            self._finish(connection, owned, exc)
            if isinstance(exc, LocalAuthError): raise
            raise LocalAuthError("AUTH_STORAGE") from None

    def consume_pairing_code(self, code: str, *, now: datetime | None = None) -> LocalSession:
        moment = now or datetime.now(timezone.utc); stamp = _iso(moment)
        token = secrets.token_urlsafe(32); expires = _iso(moment + timedelta(seconds=self.SESSION_TTL))
        connection, owned = self._transaction()
        denied = False
        result = None
        try:
            row = connection.execute("SELECT principal_id,client_id,expires_at,security_epoch FROM local_auth_pairing_codes WHERE code_hash=?", (_digest(code),)).fetchone()
            connection.execute("DELETE FROM local_auth_pairing_codes WHERE code_hash=?", (_digest(code),))
            if row is None:
                denied = True
            elif row[2] <= stamp:
                denied = True
            else:
                epoch = connection.execute("SELECT security_epoch FROM local_auth_bootstrap_epochs WHERE singleton=1").fetchone()
                if epoch is None or row[3] != epoch[0]:
                    denied = True
                else:
                    connection.execute("INSERT INTO local_auth_sessions(token_hash,principal_id,client_id,created_at,expires_at,security_epoch) VALUES(?,?,?,?,?,?)", (_digest(token), row[0], row[1], stamp, expires, row[3]))
                    result = LocalSession(token, LocalPrincipal(row[0], "owner"), row[1], expires)
            self._finish(connection, owned)
        except Exception as exc:
            self._finish(connection, owned, exc)
            if isinstance(exc, LocalAuthError): raise
            raise LocalAuthError("AUTH_STORAGE") from None
        if denied:
            raise LocalAuthError("PAIRING_DENIED")
        return result

    def get_session(self, token: str, *, now: datetime | None = None) -> LocalPrincipal | None:
        metadata = self.get_session_metadata(token, now=now)
        return metadata.principal if metadata else None

    def get_session_metadata(
        self, token: str, *, now: datetime | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> LocalSessionMetadata | None:
        """Read a live session; a supplied connection must be the active same-database unit of work.

        Supplied connections are validated but never committed, rolled back, or
        closed. The caller owns transaction mode and completion.
        """
        stamp = _iso(now or datetime.now(timezone.utc))
        if connection is None:
            connection, owned = self._connection()
        else:
            if not isinstance(connection, sqlite3.Connection):
                raise LocalAuthError("INVALID_CONNECTION")
            if not connection.in_transaction:
                raise LocalAuthError("TRANSACTION_REQUIRED")
            self._check_shared_connection(connection)
            owned = False
        try:
            row = connection.execute("SELECT s.token_hash,s.principal_id,p.kind,s.client_id,s.expires_at FROM local_auth_sessions s JOIN local_auth_principals p ON p.id=s.principal_id JOIN local_auth_bootstrap_epochs e ON e.singleton=1 AND e.security_epoch=s.security_epoch WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?", (_digest(token), stamp)).fetchone()
            if row is None:
                return None
            token_hash = bytes(row[0])
            if not hmac.compare_digest(token_hash, _digest(token)):
                raise LocalAuthError("AUTH_STORAGE")
            return LocalSessionMetadata(
                LocalPrincipal(row[1], row[2]), row[3], row[4], token_hash.hex(),
            )
        finally:
            if owned: connection.close()

    def logout(self, token: str, *, now: datetime | None = None) -> None:
        stamp = _iso(now or datetime.now(timezone.utc))
        connection, owned = self._transaction()
        try:
            connection.execute("UPDATE local_auth_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE token_hash=?", (stamp, _digest(token)))
            self._finish(connection, owned)
        except Exception:
            self._finish(connection, owned, True)
            raise LocalAuthError("AUTH_STORAGE") from None
