"""Durable metadata-only pending requests for a proposed CAE source read.

This store records caller-supplied identity metadata and a validated path-only
scope. It does not authenticate, establish owner intent, issue a grant, or read
source files. Only decline and expiry are supported terminal decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
import uuid

from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.storage.cae_source_approvals import RootIdentity


_TABLE = "cae_source_preread_requests"
_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
MAX_REQUEST_BYTES = 128 * 1024
_STATES = {"pending", "declined", "expired"}


class SourcePrereadRequestError(ValueError):
    """Fixed-code error that never includes scope, identity, or source data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourcePrereadRequestRecordV1:
    request_id: str
    revision: int
    state: str
    record_sha256: str
    record_bytes: bytes = field(repr=False)
    created_at: str
    expires_at: str
    decided_at: str | None

    def as_dict(self) -> dict:
        try:
            return json.loads(self.record_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail("REQUEST_CORRUPT")


def _fail(code: str) -> None:
    raise SourcePrereadRequestError(code)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        _fail("INVALID_CONNECTION")


def _transaction(connection: sqlite3.Connection) -> None:
    _connection(connection)
    if not connection.in_transaction:
        _fail("TRANSACTION_REQUIRED")


def _initialized(connection: sqlite3.Connection) -> None:
    _connection(connection)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,),
        ).fetchone()
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    if row is None:
        _fail("STORE_NOT_INITIALIZED")


def _timestamp(value: datetime) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        _fail("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("REQUEST_CORRUPT")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("REQUEST_CORRUPT")
    if _iso(_timestamp(parsed)) != value:
        _fail("REQUEST_CORRUPT")
    return parsed.astimezone(timezone.utc)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        _fail("INVALID_IDENTITY_METADATA")
    try:
        if len(value.encode("utf-8", "strict")) > 128:
            _fail("INVALID_IDENTITY_METADATA")
    except UnicodeError:
        _fail("INVALID_IDENTITY_METADATA")
    return value


def _session_fingerprint(value: object) -> str:
    fingerprint = _text(value)
    if not _SHA256.fullmatch(fingerprint):
        _fail("INVALID_IDENTITY_METADATA")
    return fingerprint


def _canonical(value: object) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("INVALID_REQUEST")
    if len(raw) > MAX_REQUEST_BYTES:
        _fail("REQUEST_LIMIT_EXCEEDED")
    return raw


def _decode_scope(value: object) -> SourceCaptureScopeV1:
    if (not isinstance(value, dict)
            or set(value) != {"schemaVersion", "root", "relativePaths", "policyRef", "limits"}):
        _fail("REQUEST_CORRUPT")
    root = value["root"]
    if (not isinstance(root, dict) or set(root) != {"absolutePath", "identity"}
            or not isinstance(root["identity"], dict)
            or set(root["identity"]) != {"platform", "device", "fileId"}
            or not isinstance(value["relativePaths"], list)
            or not isinstance(value["limits"], dict)
            or set(value["limits"]) != {"maxFiles", "maxFileBytes", "maxTotalBytes"}):
        _fail("REQUEST_CORRUPT")
    identity = root["identity"]
    limits = value["limits"]
    try:
        scope = SourceCaptureScopeV1(
            schema_version=value["schemaVersion"],
            root_path=root["absolutePath"],
            root_identity=RootIdentity(identity["platform"], identity["device"], identity["fileId"]),
            relative_paths=tuple(value["relativePaths"]),
            limits=SourceReadLimits(limits["maxFiles"], limits["maxFileBytes"], limits["maxTotalBytes"]),
        )
    except Exception:
        _fail("REQUEST_CORRUPT")
    if scope.canonical_bytes() != _canonical(value):
        _fail("REQUEST_CORRUPT")
    return scope


def _payload_value(
    *, request_id: str, created_at: str, expires_at: str,
    owner_id: str, principal_id: str, client_id: str, session_fingerprint: str,
    scope: SourceCaptureScopeV1,
) -> dict:
    if type(scope) is not SourceCaptureScopeV1:
        _fail("INVALID_SCOPE")
    return {
        "schemaVersion": 1,
        "recordKind": "onepath.preread-source-request",
        "requestId": request_id,
        "createdAt": created_at,
        "expiresAt": expires_at,
        "authScope": {
            "ownerId": _text(owner_id),
            "principalId": _text(principal_id),
            "clientId": _text(client_id),
            "sessionFingerprint": _session_fingerprint(session_fingerprint),
        },
        "captureScope": json.loads(scope.canonical_bytes().decode("utf-8"), object_pairs_hook=_unique_pairs),
    }


def _validate_payload(raw: bytes, digest: str, request_id: str,
                      created_at: str, expires_at: str) -> dict:
    if (type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or hashlib.sha256(raw).hexdigest() != digest):
        _fail("REQUEST_CORRUPT")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _item: (_ for _ in ()).throw(ValueError("invalid constant")))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        _fail("REQUEST_CORRUPT")
    if (not isinstance(value, dict)
            or set(value) != {"schemaVersion", "recordKind", "requestId", "createdAt", "expiresAt",
                              "authScope", "captureScope"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["recordKind"] != "onepath.preread-source-request"
            or value["requestId"] != request_id
            or value["createdAt"] != created_at or value["expiresAt"] != expires_at
            or not isinstance(value["authScope"], dict)
            or set(value["authScope"]) != {"ownerId", "principalId", "clientId", "sessionFingerprint"}):
        _fail("REQUEST_CORRUPT")
    created = _parse_iso(created_at)
    expires = _parse_iso(expires_at)
    if expires <= created or expires - created > timedelta(seconds=MAX_TTL_SECONDS):
        _fail("REQUEST_CORRUPT")
    for item in value["authScope"].values():
        try:
            _text(item)
        except SourcePrereadRequestError:
            _fail("REQUEST_CORRUPT")
    try:
        _session_fingerprint(value["authScope"]["sessionFingerprint"])
    except SourcePrereadRequestError:
        _fail("REQUEST_CORRUPT")
    scope = _decode_scope(value["captureScope"])
    try:
        expected = _canonical(_payload_value(
            request_id=request_id, created_at=created_at, expires_at=expires_at,
            owner_id=value["authScope"]["ownerId"],
            principal_id=value["authScope"]["principalId"],
            client_id=value["authScope"]["clientId"],
            session_fingerprint=value["authScope"]["sessionFingerprint"],
            scope=scope,
        ))
    except SourcePrereadRequestError:
        _fail("REQUEST_CORRUPT")
    if expected != raw:
        _fail("REQUEST_CORRUPT")
    return value


def _row_record(row) -> SourcePrereadRequestRecordV1:
    if row is None:
        _fail("REQUEST_NOT_FOUND")
    request_id, revision, state, digest, raw, created, expires, decided = row
    if (not isinstance(request_id, str) or not _ID.fullmatch(request_id)
            or type(revision) is not int or state not in _STATES
            or not isinstance(created, str) or not isinstance(expires, str)):
        _fail("REQUEST_CORRUPT")
    if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
        _fail("REQUEST_CORRUPT")
    _validate_payload(raw, digest, request_id, created, expires)
    created_time, expires_time = _parse_iso(created), _parse_iso(expires)
    if revision == 1 and state == "pending":
        if decided is not None:
            _fail("REQUEST_CORRUPT")
    elif revision == 2 and state in {"declined", "expired"}:
        if not isinstance(decided, str):
            _fail("REQUEST_CORRUPT")
        decided_time = _parse_iso(decided)
        if decided_time < created_time or (state == "declined" and decided_time >= expires_time):
            _fail("REQUEST_CORRUPT")
        if state == "expired" and decided_time < expires_time:
            _fail("REQUEST_CORRUPT")
    else:
        _fail("REQUEST_CORRUPT")
    return SourcePrereadRequestRecordV1(request_id, revision, state, digest, raw, created, expires, decided)


def initialize(connection: sqlite3.Connection) -> None:
    """Create the request record table on an idle caller-owned connection."""
    _connection(connection)
    if connection.in_transaction:
        _fail("INITIALIZATION_REQUIRES_IDLE_CONNECTION")
    try:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "request_id TEXT PRIMARY KEY CHECK(length(request_id)=32 AND request_id NOT GLOB '*[^0-9a-f]*'), "
            "revision INTEGER NOT NULL, state TEXT NOT NULL, record_sha256 TEXT NOT NULL, record_json BLOB NOT NULL, "
            "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT, "
            "CHECK((revision=1 AND state='pending' AND decided_at IS NULL) OR "
            "(revision=2 AND state IN ('declined','expired') AND decided_at IS NOT NULL)))"
        )
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")


def create_request(
    connection: sqlite3.Connection,
    *,
    scope: SourceCaptureScopeV1,
    owner_id: str,
    principal_id: str,
    client_id: str,
    session_fingerprint: str,
    now: datetime,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> SourcePrereadRequestRecordV1:
    """Persist a pending scope request; supplied identity metadata is not authenticated here."""
    _transaction(connection)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        _fail("INVALID_TTL")
    stamp = _timestamp(now)
    created, expires = _iso(stamp), _iso(stamp + timedelta(seconds=ttl_seconds))
    request_id = uuid.uuid4().hex
    raw = _canonical(_payload_value(
        request_id=request_id, created_at=created, expires_at=expires,
        owner_id=owner_id, principal_id=principal_id, client_id=client_id,
        session_fingerprint=session_fingerprint, scope=scope,
    ))
    digest = hashlib.sha256(raw).hexdigest()
    _validate_payload(raw, digest, request_id, created, expires)
    try:
        connection.execute(
            f"INSERT INTO {_TABLE}(request_id,revision,state,record_sha256,record_json,created_at,expires_at,decided_at) "
            "VALUES(?,1,'pending',?,?,?,?,NULL)",
            (request_id, digest, sqlite3.Binary(raw), created, expires),
        )
    except sqlite3.IntegrityError:
        _fail("DUPLICATE_REQUEST_ID")
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return SourcePrereadRequestRecordV1(request_id, 1, "pending", digest, raw, created, expires, None)


def get_request(connection: sqlite3.Connection, request_id: str) -> SourcePrereadRequestRecordV1:
    """Load and validate a stored request; this does not authenticate its metadata."""
    _initialized(connection)
    if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
        _fail("INVALID_REQUEST_ID")
    try:
        row = connection.execute(
            f"SELECT request_id,revision,state,record_sha256,record_json,created_at,expires_at,decided_at "
            f"FROM {_TABLE} WHERE request_id=?", (request_id,),
        ).fetchone()
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return _row_record(row)


def _decide(
    connection: sqlite3.Connection,
    *, request_id: str, expected_revision: int, expected_record_sha256: str,
    decision: str, now: datetime,
) -> SourcePrereadRequestRecordV1:
    _transaction(connection)
    if decision not in {"declined", "expired"}:
        _fail("INVALID_DECISION")
    if type(expected_revision) is not int or expected_revision != 1:
        _fail("STALE_REQUEST")
    if not isinstance(expected_record_sha256, str) or not _SHA256.fullmatch(expected_record_sha256):
        _fail("STALE_REQUEST")
    stamp = _timestamp(now)
    record = get_request(connection, request_id)
    if (record.revision != 1 or record.state != "pending"
            or record.record_sha256 != expected_record_sha256):
        _fail("STALE_REQUEST")
    expires = _parse_iso(record.expires_at)
    if decision == "expired" and stamp < expires:
        _fail("REQUEST_NOT_EXPIRED")
    if decision == "declined" and stamp >= expires:
        _fail("REQUEST_EXPIRED")
    try:
        cursor = connection.execute(
            f"UPDATE {_TABLE} SET revision=2,state=?,decided_at=? "
            "WHERE request_id=? AND revision=1 AND state='pending' AND record_sha256=?",
            (decision, _iso(stamp), request_id, expected_record_sha256),
        )
        if cursor.rowcount != 1:
            _fail("STALE_REQUEST")
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return get_request(connection, request_id)


def decline_request(
    connection: sqlite3.Connection,
    *, request_id: str, expected_revision: int, expected_record_sha256: str,
    now: datetime,
) -> SourcePrereadRequestRecordV1:
    """CAS a pending request to declined; this never issues or consumes a grant."""
    return _decide(connection, request_id=request_id, expected_revision=expected_revision,
                   expected_record_sha256=expected_record_sha256, decision="declined", now=now)


def expire_request(
    connection: sqlite3.Connection,
    *, request_id: str, expected_revision: int, expected_record_sha256: str,
    now: datetime,
) -> SourcePrereadRequestRecordV1:
    """CAS an elapsed pending request to expired; this is not a permission decision."""
    return _decide(connection, request_id=request_id, expected_revision=expected_revision,
                   expected_record_sha256=expected_record_sha256, decision="expired", now=now)


__all__ = [
    "DEFAULT_TTL_SECONDS", "MAX_REQUEST_BYTES", "MAX_TTL_SECONDS",
    "SourcePrereadRequestError", "SourcePrereadRequestRecordV1", "create_request",
    "decline_request", "expire_request", "get_request", "initialize",
]
