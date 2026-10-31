"""Internal one-use ledger for explicit CAE source-selection approvals.

This ledger is not an authentication or permission boundary. Callers MUST obtain
explicit owner confirmation and revalidate the live owner session on the same
SQLite connection before consuming an approval. Trusted caller code supplies
the selection/provenance metadata; this module validates and binds it, but does
not attest Git state, inspect files, apply exclusions, or read source bytes.

All writes require an already-active outer SQLite transaction. This module
never commits or rolls back the caller's connection, so approval consumption
can share one unit of work with source artifact insertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from typing import Literal


class SourceApprovalError(ValueError):
    """Fixed-code error that never includes secrets, paths, or source data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RootIdentity:
    platform: str
    device: int
    file_id: int


@dataclass(frozen=True)
class ApprovedFile:
    path: str
    sha256: str
    size_bytes: int
    origin: Literal["tracked", "untracked", "generated"]


@dataclass(frozen=True)
class SourceSelection:
    """Exact caller-attested selection binding; values are not independently attested here."""

    owner_id: str
    principal_id: str
    client_id: str
    session_id: str
    root_path: str
    root_identity: RootIdentity
    files: tuple[ApprovedFile, ...]
    source_revision: str | None
    provenance_ref: str
    exclusion_policy_version: str
    exclusion_policy_ref: str


@dataclass(frozen=True)
class IssuedApproval:
    approval_id: str
    secret: bytes = field(repr=False)
    expires_at: str


DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900
MAX_FILES = 4096
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_ID_BYTES = 128
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:\\")
_RESERVED_WINDOWS = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)


def _fail() -> None:
    raise SourceApprovalError("INVALID_APPROVAL")


def _text(value: object, *, maximum: int = MAX_ID_BYTES) -> str:
    if not isinstance(value, str) or not value:
        _fail()
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        _fail()
    if (size > maximum or "\x00" in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        _fail()
    return value


def _path(value: object) -> str:
    path = _text(value, maximum=MAX_PATH_BYTES)
    if (path.startswith("/") or "\\" in path or ":" in path or path.endswith("/")):
        _fail()
    components = path.split("/")
    if any(not part or part in {".", ".."} or part.endswith((".", " "))
           or _RESERVED_WINDOWS.fullmatch(part) for part in components):
        _fail()
    return path


def _root_path(value: object) -> str:
    root = _text(value, maximum=4096)
    if root.startswith("/") and not root.startswith("//"):
        if root != "/" and (root.endswith("/") or any(p in {"", ".", ".."} for p in root[1:].split("/"))):
            _fail()
        return root
    if _DRIVE_ROOT.match(root) and "/" not in root:
        tail = root[3:]
        if tail and (root.endswith("\\") or any(p in {"", ".", ".."} for p in tail.split("\\"))):
            _fail()
        return root
    _fail()


def _timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise SourceApprovalError("INVALID_TIME")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail()
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail()
    return _timestamp(result)


def _selection_value(selection: SourceSelection) -> dict:
    if not isinstance(selection, SourceSelection):
        _fail()
    owner = _text(selection.owner_id)
    principal = _text(selection.principal_id)
    client = _text(selection.client_id)
    session = _text(selection.session_id)
    root = _root_path(selection.root_path)
    identity = selection.root_identity
    if (not isinstance(identity, RootIdentity) or type(identity.device) is not int
            or type(identity.file_id) is not int or identity.device < 0 or identity.file_id < 0):
        _fail()
    platform = _text(identity.platform, maximum=32)
    if platform not in {"posix", "windows_ntfs"}:
        _fail()
    if not isinstance(selection.files, tuple) or not selection.files or len(selection.files) > MAX_FILES:
        _fail()
    files = []
    path_trie: dict = {}
    file_marker = object()
    total = 0
    for entry in selection.files:
        if not isinstance(entry, ApprovedFile):
            _fail()
        path = _path(entry.path)
        node = path_trie
        for component in path.casefold().split("/"):
            if file_marker in node:
                _fail()  # An already-selected file is an ancestor.
            node = node.setdefault(component, {})
        if file_marker in node or node:
            _fail()  # Duplicate path or an already-selected descendant.
        node[file_marker] = True
        if (not isinstance(entry.sha256, str) or not _SHA256.fullmatch(entry.sha256)
                or type(entry.size_bytes) is not int or entry.size_bytes < 0
                or entry.size_bytes > MAX_FILE_BYTES
                or entry.size_bytes > MAX_TOTAL_BYTES - total
                or not isinstance(entry.origin, str)
                or entry.origin not in {"tracked", "untracked", "generated"}):
            _fail()
        total += entry.size_bytes
        files.append({"path": path, "sha256": entry.sha256, "sizeBytes": entry.size_bytes,
                      "origin": entry.origin})
    files.sort(key=lambda item: item["path"])
    if selection.source_revision is not None:
        revision = _text(selection.source_revision, maximum=128)
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            _fail()
    else:
        revision = None
    provenance_ref = _text(selection.provenance_ref)
    policy_version = _text(selection.exclusion_policy_version, maximum=64)
    policy_ref = _text(selection.exclusion_policy_ref)
    return {
        "schemaVersion": 1,
        "authScope": {"ownerId": owner, "principalId": principal,
                      "clientId": client, "sessionId": session},
        "root": {"path": root, "identity": {"platform": platform,
                  "device": identity.device, "fileId": identity.file_id}},
        "files": files,
        "sourceRevision": revision,
        "provenanceRef": provenance_ref,
        "exclusionPolicy": {"version": policy_version, "ref": policy_ref},
    }


def _canonical(value: dict) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail()


def _require_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise SourceApprovalError("INVALID_CONNECTION")


def _require_transaction(connection: sqlite3.Connection) -> None:
    _require_connection(connection)
    if not connection.in_transaction:
        raise SourceApprovalError("TRANSACTION_REQUIRED")


def initialize(connection: sqlite3.Connection) -> None:
    """Create the ledger table outside a caller transaction; never commits one."""
    _require_connection(connection)
    if connection.in_transaction:
        raise SourceApprovalError("INITIALIZATION_REQUIRES_IDLE_CONNECTION")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cae_source_approvals ("
            "approval_id TEXT PRIMARY KEY, selection_sha256 TEXT NOT NULL, "
            "selection_json BLOB NOT NULL, secret_sha256 BLOB NOT NULL, "
            "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT)"
        )
    except sqlite3.Error:
        raise SourceApprovalError("DATABASE_FAILURE") from None


def issue_approval(
    connection: sqlite3.Connection,
    selection: SourceSelection,
    *,
    now: datetime,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> IssuedApproval:
    """Persist an approval binding; caller must already confirm owner and selection."""
    _require_transaction(connection)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise SourceApprovalError("INVALID_TTL")
    stamp = _timestamp(now)
    payload = _canonical(_selection_value(selection))
    digest = hashlib.sha256(payload).hexdigest()
    secret = secrets.token_bytes(32)
    secret_digest = hashlib.sha256(secret).digest()
    approval_id = str(uuid.uuid4())
    expires = stamp + timedelta(seconds=ttl_seconds)
    if expires <= stamp:
        raise SourceApprovalError("INVALID_TIME")
    try:
        connection.execute(
            "INSERT INTO cae_source_approvals(approval_id,selection_sha256,selection_json,"
            "secret_sha256,created_at,expires_at,consumed_at) VALUES(?,?,?,?,?,?,NULL)",
            (approval_id, digest, sqlite3.Binary(payload), sqlite3.Binary(secret_digest),
             _iso(stamp), _iso(expires)),
        )
    except sqlite3.Error:
        raise SourceApprovalError("DATABASE_FAILURE") from None
    return IssuedApproval(approval_id, secret, _iso(expires))


def consume_approval(
    connection: sqlite3.Connection,
    approval: IssuedApproval,
    selection: SourceSelection,
    *,
    now: datetime,
) -> None:
    """Consume once iff secret, full selection, auth scope, and expiry still match."""
    _require_transaction(connection)
    if not isinstance(approval, IssuedApproval):
        raise SourceApprovalError("INVALID_APPROVAL")
    if (not isinstance(approval.secret, bytes) or len(approval.secret) != 32
            or not isinstance(approval.approval_id, str) or not _ID.fullmatch(approval.approval_id)):
        raise SourceApprovalError("INVALID_APPROVAL")
    stamp = _timestamp(now)
    payload = _canonical(_selection_value(selection))
    digest = hashlib.sha256(payload).hexdigest()
    secret_digest = hashlib.sha256(approval.secret).digest()
    try:
        row = connection.execute(
            "SELECT selection_sha256,selection_json,secret_sha256,created_at,expires_at,consumed_at "
            "FROM cae_source_approvals WHERE approval_id=?", (approval.approval_id,),
        ).fetchone()
        if row is None:
            raise SourceApprovalError("APPROVAL_UNAVAILABLE")
        stored_digest, stored_payload, stored_secret, created_text, expires_text, consumed = row
        if consumed is not None:
            raise SourceApprovalError("APPROVAL_UNAVAILABLE")
        created = _parse_iso(created_text)
        expires = _parse_iso(expires_text)
        stored_payload = bytes(stored_payload)
        stored_secret = bytes(stored_secret)
        if (expires <= created or expires - created > timedelta(seconds=MAX_TTL_SECONDS)
                or created > stamp or expires <= stamp
                or hashlib.sha256(stored_payload).hexdigest() != stored_digest
                or not hmac.compare_digest(stored_payload, payload)
                or not hmac.compare_digest(str(stored_digest).encode("ascii"), digest.encode("ascii"))
                or len(stored_secret) != 32
                or not hmac.compare_digest(stored_secret, secret_digest)):
            raise SourceApprovalError("APPROVAL_UNAVAILABLE")
        cursor = connection.execute(
            "UPDATE cae_source_approvals SET consumed_at=? WHERE approval_id=? "
            "AND consumed_at IS NULL AND expires_at>? AND selection_sha256=? AND secret_sha256=?",
            (_iso(stamp), approval.approval_id, _iso(stamp), digest, sqlite3.Binary(secret_digest)),
        )
        if cursor.rowcount != 1:
            raise SourceApprovalError("APPROVAL_UNAVAILABLE")
    except SourceApprovalError:
        raise
    except (sqlite3.Error, TypeError, ValueError, UnicodeError):
        raise SourceApprovalError("DATABASE_FAILURE") from None
