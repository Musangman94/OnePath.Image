"""Bounded HTTP-facing adapter over the local owner authentication store."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from app.services.owner_credentials import OwnerCredentialStore
from app.storage.local_auth import LocalAuthError, LocalAuthStore, LocalPrincipal


class AuthServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


@dataclass(frozen=True)
class AuthenticatedOwner:
    principal: LocalPrincipal
    client_id: str
    expires_at: str


@dataclass(frozen=True)
class PairResult:
    token: str = field(repr=False)
    owner: AuthenticatedOwner


@dataclass(frozen=True)
class _AuthenticatedSessionProof:
    owner: AuthenticatedOwner
    stored_client_id: str
    session_fingerprint: str


def normalize_loopback_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError
        address = ipaddress.ip_address(parsed.hostname)
        if not address.is_loopback:
            raise ValueError
        port = parsed.port
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")
    except Exception:
        raise ValueError("Trusted origin must be an HTTP(S) literal loopback origin.") from None


class _AttemptLimiter:
    """One bounded per-instance window; all pair and CLI-code attempts count."""
    LIMIT = 10
    WINDOW = timedelta(seconds=60)

    def __init__(self):
        self._attempts: deque[datetime] = deque(maxlen=self.LIMIT)

    def allow(self, now: datetime) -> bool:
        cutoff = now - self.WINDOW
        while self._attempts and self._attempts[0] <= cutoff:
            self._attempts.popleft()
        if len(self._attempts) >= self.LIMIT:
            return False
        self._attempts.append(now)
        return True


class LocalAuthService:
    """HTTPS origins get Secure cookies; HTTP is restricted to literal loopback development."""
    PUBLIC_CLIENT_ID = "onepath-browser"

    def __init__(self, store: LocalAuthStore, *, trusted_origin: str, clock=None):
        if not isinstance(store, LocalAuthStore):
            raise ValueError("Local authentication configuration is invalid.")
        self.store = store
        self.trusted_origin = normalize_loopback_origin(trusted_origin)
        self.secure_cookie = self.trusted_origin.startswith("https://")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._limiter = _AttemptLimiter()

    def now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.")
        return value.astimezone(timezone.utc)

    def is_ready(self) -> bool:
        try:
            return self.store.is_initialized()
        except Exception:
            return False

    def _rate_check(self, now: datetime) -> None:
        if not self._limiter.allow(now):
            raise AuthServiceError(429, "RATE_LIMITED", "Too many authentication attempts. Try again later.")

    def count_pair_attempt(self) -> None:
        self._rate_check(self.now())

    def issue_pairing_code(self, authorization: str | None) -> tuple[str, str]:
        now = self.now()
        self._rate_check(now)
        if not self.is_ready():
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.")
        prefix = "OnePathBootstrap "
        supplied = authorization[len(prefix):] if isinstance(authorization, str) and authorization.startswith(prefix) else ""
        try:
            code = self.store.issue_pairing_code(supplied, now=now)
        except LocalAuthError as exc:
            if exc.code == "BOOTSTRAP_DENIED":
                raise AuthServiceError(401, "BOOTSTRAP_DENIED", "Bootstrap authorization was rejected.") from None
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.") from None
        return code, (now + timedelta(seconds=self.store.PAIR_TTL)).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def pair(self, code: str) -> PairResult:
        now = self.now()
        self._rate_check(now)
        if not self.is_ready():
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.")
        try:
            session = self.store.consume_pairing_code(code, now=now)
        except LocalAuthError as exc:
            if exc.code == "PAIRING_DENIED":
                raise AuthServiceError(401, "PAIRING_DENIED", "The pairing code is invalid or expired.") from None
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.") from None
        owner = self._map_owner(session.principal, session.client_id, session.expires_at)
        if owner is None:
            try:
                self.store.logout(session.token, now=now)
            except Exception:
                raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.") from None
            raise AuthServiceError(401, "PAIRING_DENIED", "The pairing code is invalid or expired.")
        return PairResult(session.token, owner)

    def authenticate(self, token: str | None) -> AuthenticatedOwner:
        if not self.is_ready():
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.")
        if not token:
            raise AuthServiceError(401, "AUTHENTICATION_REQUIRED", "Owner authentication is required.")
        try:
            metadata = self.store.get_session_metadata(token, now=self.now())
        except Exception:
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.") from None
        owner = self._map_owner(metadata.principal, metadata.client_id, metadata.expires_at) if metadata else None
        if owner is None:
            raise AuthServiceError(401, "AUTHENTICATION_REQUIRED", "Owner authentication is required.")
        return owner

    def authenticate_session(
        self, token: str | None, *, connection: sqlite3.Connection,
    ) -> _AuthenticatedSessionProof:
        """Revalidate a bearer cookie in the caller's active unit of work.

        The caller must begin an outer ``BEGIN IMMEDIATE`` transaction before
        this check and keep it through approval consumption and source writes.
        This orders the proof against logout and bootstrap-epoch rotation. This
        read never commits, rolls back, or closes the supplied connection; on a
        SQLite lock-upgrade failure the caller must roll back and start over.
        """
        if not token:
            raise AuthServiceError(401, "AUTHENTICATION_REQUIRED", "Owner authentication is required.")
        try:
            metadata = self.store.get_session_metadata(token, now=self.now(), connection=connection)
        except Exception:
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication cannot be revalidated.") from None
        owner = self._map_owner(metadata.principal, metadata.client_id, metadata.expires_at) if metadata else None
        if owner is None:
            raise AuthServiceError(401, "AUTHENTICATION_REQUIRED", "Owner authentication is required.")
        return _AuthenticatedSessionProof(owner, metadata.client_id, metadata.session_fingerprint)

    def status(self, token: str | None) -> AuthenticatedOwner | None:
        try:
            return self.authenticate(token)
        except AuthServiceError:
            return None

    def is_session_active(self, token: str) -> bool:
        """Revalidation hook for future SSE event batches; entry middleware alone cannot close streams."""
        try:
            self.authenticate(token)
            return True
        except AuthServiceError:
            return False

    def logout(self, token: str) -> None:
        if not self.is_ready():
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.")
        try:
            self.store.logout(token, now=self.now())
        except Exception:
            raise AuthServiceError(503, "AUTH_UNAVAILABLE", "Owner authentication is unavailable.") from None

    def _map_owner(self, principal: LocalPrincipal, stored_client_id: str, expires_at: str) -> AuthenticatedOwner | None:
        if principal.kind != "owner" or stored_client_id != self.store.OWNER_BROWSER_CLIENT:
            return None
        return AuthenticatedOwner(principal, self.PUBLIC_CLIENT_ID, expires_at)


def initialize_application_auth(database: Path, trusted_origin: str) -> LocalAuthService:
    """Create or securely rotate the current owner credential and SQLite epoch."""
    database = Path(database)
    credentials = OwnerCredentialStore(database.parent)
    if credentials.path.exists() or credentials.path.is_symlink():
        credentials.read()  # Existing material must validate before rotation.
        record = credentials.rotate(trusted_origin)
    else:
        record = credentials.create(trusted_origin)
    store = LocalAuthStore(database)
    store.initialize(record.instance_id, record.bootstrap_secret)
    return LocalAuthService(store, trusted_origin=trusted_origin)
