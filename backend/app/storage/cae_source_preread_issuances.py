"""Metadata-only links between a pending pre-read request and its v2 grant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import sqlite3
import hashlib

from app.storage.cae_source_approvals import SourceApprovalError


_TABLE = "cae_source_preread_issuances"
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_APPROVAL_ID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IssuedPrereadLinkV1:
    request_id: str
    request_sha256: str
    approval_id: str
    selection_sha256: str
    approval_expires_at: str
    issued_at: str


def _fail(code: str) -> None:
    raise SourceApprovalError(code)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("REQUEST_UNAVAILABLE")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("REQUEST_UNAVAILABLE")
    if (parsed.tzinfo is None or parsed.utcoffset() != timedelta(0)
            or parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value):
        _fail("REQUEST_UNAVAILABLE")
    return parsed.astimezone(timezone.utc)


def initialize(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        _fail("INVALID_CONNECTION")
    if connection.in_transaction:
        _fail("INITIALIZATION_REQUIRES_IDLE_CONNECTION")
    try:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "request_id TEXT PRIMARY KEY CHECK(length(request_id)=32 AND request_id NOT GLOB '*[^0-9a-f]*'), "
            "request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'), "
            "approval_id TEXT NOT NULL UNIQUE, "
            "selection_sha256 TEXT NOT NULL CHECK(length(selection_sha256)=64 AND selection_sha256 NOT GLOB '*[^0-9a-f]*'), "
            "approval_expires_at TEXT NOT NULL, issued_at TEXT NOT NULL)"
        )
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")


def _initialized(connection: sqlite3.Connection) -> None:
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,),
        ).fetchone()
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    if row is None:
        _fail("STORE_NOT_INITIALIZED")


def find(
    connection: sqlite3.Connection, *, request_id: str, request_sha256: str,
    selection_json: bytes, request_expires_at: str,
) -> IssuedPrereadLinkV1 | None:
    _initialized(connection)
    if (not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)
            or not isinstance(request_sha256, str) or not _SHA256.fullmatch(request_sha256)
            or type(selection_json) is not bytes
            or not isinstance(request_expires_at, str)):
        _fail("REQUEST_CORRUPT")
    selection_sha256 = hashlib.sha256(selection_json).hexdigest()
    try:
        row = connection.execute(
            f"SELECT i.request_id,i.request_sha256,i.approval_id,i.selection_sha256,"
            f"i.approval_expires_at,i.issued_at,a.selection_sha256,a.selection_json,a.expires_at "
            f"FROM {_TABLE} AS i LEFT JOIN cae_source_approvals AS a ON a.approval_id=i.approval_id "
            "WHERE i.request_id=?", (request_id,),
        ).fetchone()
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    if row is None:
        return None
    link = IssuedPrereadLinkV1(*row[:6])
    if (not isinstance(link.request_id, str) or not _REQUEST_ID.fullmatch(link.request_id)
            or not isinstance(link.request_sha256, str) or not _SHA256.fullmatch(link.request_sha256)
            or not isinstance(link.approval_id, str) or not _APPROVAL_ID.fullmatch(link.approval_id)
            or not isinstance(link.selection_sha256, str) or not _SHA256.fullmatch(link.selection_sha256)
            or not isinstance(link.approval_expires_at, str) or not isinstance(link.issued_at, str)
            or type(row[6]) is not str or type(row[7]) is not bytes or type(row[8]) is not str
            or link.request_sha256 != request_sha256 or link.selection_sha256 != selection_sha256
            or _timestamp(link.approval_expires_at) > _timestamp(request_expires_at)
            or _timestamp(link.issued_at) >= _timestamp(link.approval_expires_at)
            or row[6] != selection_sha256 or row[7] != selection_json
            or row[8] != link.approval_expires_at):
        _fail("REQUEST_CORRUPT")
    return link


def record(
    connection: sqlite3.Connection, *, request_id: str, request_sha256: str,
    request_expires_at: str, approval_id: str, selection_json: bytes,
    approval_expires_at: str, issued_at: str,
) -> IssuedPrereadLinkV1:
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    _initialized(connection)
    if (not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)
            or not isinstance(request_sha256, str) or not _SHA256.fullmatch(request_sha256)
            or not isinstance(approval_id, str) or not _APPROVAL_ID.fullmatch(approval_id)
            or type(selection_json) is not bytes
            or not isinstance(approval_expires_at, str)
            or not isinstance(request_expires_at, str) or not isinstance(issued_at, str)):
        _fail("INVALID_APPROVAL")
    # Reconstruct the only permissible binding from the durable request. The
    # caller-provided payload is a comparison value, never the authority.
    from app.storage import cae_source_preread_requests as requests
    from app.storage.cae_source_approvals import canonical_source_capture_selection

    try:
        pending = requests.get_request(connection, request_id)
        if (pending.state != "pending" or pending.revision != 1
                or pending.record_sha256 != request_sha256
                or pending.expires_at != request_expires_at):
            _fail("REQUEST_UNAVAILABLE")
        stored = pending.as_dict()
        auth = stored["authScope"]
        expected_json = canonical_source_capture_selection(
            requests.scope_from_request(pending), owner_id=auth["ownerId"],
            principal_id=auth["principalId"], client_id=auth["clientId"],
            session_id=auth["sessionFingerprint"],
        )
    except SourceApprovalError:
        raise
    except Exception:
        _fail("REQUEST_UNAVAILABLE")
    if selection_json != expected_json:
        _fail("APPROVAL_UNAVAILABLE")
    selection_sha256 = hashlib.sha256(expected_json).hexdigest()
    created_at = _timestamp(pending.created_at)
    request_expiry = _timestamp(pending.expires_at)
    issued_time = _timestamp(issued_at)
    approval_expiry = _timestamp(approval_expires_at)
    if (issued_time < created_at or issued_time >= request_expiry
            or approval_expiry <= issued_time or approval_expiry > request_expiry):
        _fail("APPROVAL_UNAVAILABLE")
    try:
        approval_row = connection.execute(
            "SELECT selection_sha256,selection_json,created_at,expires_at,consumed_at "
            "FROM cae_source_approvals WHERE approval_id=?", (approval_id,),
        ).fetchone()
        if (approval_row is None or type(approval_row[0]) is not str
                or type(approval_row[1]) is not bytes or type(approval_row[2]) is not str
                or type(approval_row[3]) is not str or approval_row[0] != selection_sha256
                or approval_row[1] != expected_json
                or approval_row[2] != issued_at or approval_row[3] != approval_expires_at
                or approval_row[4] is not None):
            _fail("APPROVAL_UNAVAILABLE")
        cursor = connection.execute(
            f"INSERT INTO {_TABLE}(request_id,request_sha256,approval_id,selection_sha256,approval_expires_at,issued_at) "
            "SELECT r.request_id,r.record_sha256,a.approval_id,a.selection_sha256,a.expires_at,? "
            "FROM cae_source_preread_requests AS r JOIN cae_source_approvals AS a "
            "ON a.approval_id=? WHERE r.request_id=? AND r.revision=1 AND r.state='pending' "
            "AND r.record_sha256=? AND r.expires_at=? AND r.expires_at>? "
            "AND r.created_at<=? AND a.selection_sha256=? AND a.selection_json=? "
            "AND a.created_at=? AND a.expires_at=? AND a.consumed_at IS NULL",
            (issued_at, approval_id, request_id, request_sha256, request_expires_at,
             issued_at, issued_at, selection_sha256, sqlite3.Binary(expected_json), issued_at,
             approval_expires_at),
        )
        if cursor.rowcount != 1:
            _fail("REQUEST_UNAVAILABLE")
    except SourceApprovalError:
        raise
    except sqlite3.IntegrityError:
        _fail("REQUEST_UNAVAILABLE")
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return IssuedPrereadLinkV1(
        request_id, request_sha256, approval_id, selection_sha256, approval_expires_at, issued_at,
    )


__all__ = ["IssuedPrereadLinkV1", "initialize", "find", "record"]
