"""Purpose-specific, expiring source-review records.

Review state records a bounded UI workflow. It is not a grant, an approval
secret, or proof of human intent; the source-approval ledger remains the sole
one-use grant authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
import uuid

from pydantic import ValidationError

from app.schemas.cae import ArtifactRef
from app.services.cae_source_provenance import BUILTIN_SOURCE_PROFILE_V1_SHA256
from app.storage.cae_source_approvals import (
    MAX_TTL_SECONDS,
    ApprovedFile,
    RootIdentity,
    SourceApprovalError,
    SourceSelection,
    canonical_source_selection,
)


_TABLE = "cae_source_reviews"
_STATES = {"pending", "approved", "rejected", "cancelled", "expired"}
_DECISIONS = {"approved", "rejected", "cancelled", "expired"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_REVIEW_BYTES = 128 * 1024
DEFAULT_REVIEW_TTL_SECONDS = 300


class SourceReviewError(ValueError):
    """Fixed-code review-store error; messages omit record contents."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourceReviewRecordV1:
    review_id: str
    revision: int
    state: str
    record_sha256: str
    record_bytes: bytes = field(repr=False)
    created_at: str
    expires_at: str
    decided_at: str | None
    approval_id: str | None

    def as_dict(self) -> dict:
        try:
            return json.loads(self.record_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceReviewError("REVIEW_CORRUPT") from None


def _fail(code: str) -> None:
    raise SourceReviewError(code)


def _connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        _fail("INVALID_CONNECTION")


def _require_transaction(connection: sqlite3.Connection) -> None:
    _connection(connection)
    if not connection.in_transaction:
        _fail("TRANSACTION_REQUIRED")


def _require_initialized(connection: sqlite3.Connection) -> None:
    _connection(connection)
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,),
    ).fetchone()
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
        _fail("REVIEW_CORRUPT")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("REVIEW_CORRUPT")
    if _iso(_timestamp(parsed)) != value:
        _fail("REVIEW_CORRUPT")
    return parsed.astimezone(timezone.utc)


def _canonical(value: object) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("INVALID_REVIEW")
    if len(raw) > MAX_REVIEW_BYTES:
        _fail("REVIEW_LIMIT_EXCEEDED")
    return raw


def _text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        _fail("INVALID_REVIEW_TEXT")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("INVALID_REVIEW_TEXT")
    if length > maximum:
        _fail("INVALID_REVIEW_TEXT")
    return value


def _ref(value: ArtifactRef | Mapping[str, object]) -> ArtifactRef:
    try:
        ref = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except (ValidationError, TypeError):
        _fail("INVALID_ARTIFACT_REF")
    if type(ref.version) is not int or ref.version != 1 or ref.id != "source-" + ref.sha256:
        _fail("INVALID_ARTIFACT_REF")
    return ArtifactRef(id=ref.id, sha256=ref.sha256, version=ref.version)


def _ref_value(ref: ArtifactRef) -> dict[str, object]:
    return {"id": ref.id, "sha256": ref.sha256, "version": ref.version}


def _decode_selection(value: object) -> SourceSelection:
    if not isinstance(value, dict) or set(value) != {
            "schemaVersion", "authScope", "root", "files", "sourceRevision",
            "provenanceRef", "exclusionPolicy"}:
        _fail("REVIEW_CORRUPT")
    auth = value["authScope"]
    root = value["root"]
    if (value["schemaVersion"] != 1 or not isinstance(auth, dict)
            or set(auth) != {"ownerId", "principalId", "clientId", "sessionId"}
            or not isinstance(root, dict) or set(root) != {"path", "identity"}
            or not isinstance(root["identity"], dict)
            or set(root["identity"]) != {"platform", "device", "fileId"}
            or not isinstance(value["files"], list)
            or not isinstance(value["exclusionPolicy"], dict)
            or set(value["exclusionPolicy"]) != {"version", "ref"}):
        _fail("REVIEW_CORRUPT")
    files: list[ApprovedFile] = []
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "sizeBytes", "origin"}:
            _fail("REVIEW_CORRUPT")
        files.append(ApprovedFile(item["path"], item["sha256"], item["sizeBytes"], item["origin"]))
    identity = root["identity"]
    selection = SourceSelection(
        owner_id=auth["ownerId"],
        principal_id=auth["principalId"],
        client_id=auth["clientId"],
        session_id=auth["sessionId"],
        root_path=root["path"],
        root_identity=RootIdentity(identity["platform"], identity["device"], identity["fileId"]),
        files=tuple(files),
        source_revision=value["sourceRevision"],
        provenance_ref=value["provenanceRef"],
        exclusion_policy_version=value["exclusionPolicy"]["version"],
        exclusion_policy_ref=value["exclusionPolicy"]["ref"],
    )
    if canonical_source_selection(selection) != _canonical(value):
        _fail("REVIEW_CORRUPT")
    return selection


def _validate_payload(raw: bytes, digest: str, review_id: str,
                      created_at: str, expires_at: str) -> dict:
    if (not isinstance(raw, bytes) or len(raw) > MAX_REVIEW_BYTES
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or hashlib.sha256(raw).hexdigest() != digest):
        _fail("REVIEW_CORRUPT")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("REVIEW_CORRUPT")
    if (not isinstance(value, dict) or _canonical(value) != raw
            or set(value) != {"schemaVersion", "recordKind", "reviewId", "createdAt",
                              "expiresAt", "selection", "repositoryId", "scopeRef",
                              "provenanceRef", "policyRef", "policyProfileSha256"}
            or value["schemaVersion"] != 1
            or value["recordKind"] != "onepath.source-review"
            or value["reviewId"] != review_id
            or value["createdAt"] != created_at or value["expiresAt"] != expires_at):
        _fail("REVIEW_CORRUPT")
    _parse_iso(created_at)
    expires = _parse_iso(expires_at)
    created = _parse_iso(created_at)
    if expires <= created or expires - created > timedelta(seconds=MAX_TTL_SECONDS):
        _fail("REVIEW_CORRUPT")
    _text(value["repositoryId"], maximum=256)
    scope = _ref(value["scopeRef"])
    provenance = _ref(value["provenanceRef"])
    policy = _ref(value["policyRef"])
    fixed_policy = ArtifactRef(
        id="source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
        sha256=BUILTIN_SOURCE_PROFILE_V1_SHA256,
        version=1,
    )
    if (policy != fixed_policy or value["policyProfileSha256"] != BUILTIN_SOURCE_PROFILE_V1_SHA256):
        _fail("REVIEW_CORRUPT")
    selection = _decode_selection(value["selection"])
    if (selection.provenance_ref != provenance.id
            or selection.exclusion_policy_ref != policy.id):
        _fail("REVIEW_CORRUPT")
    # Rebuild the detached top-level view so no caller-owned decoded containers escape.
    value["scopeRef"] = _ref_value(scope)
    value["provenanceRef"] = _ref_value(provenance)
    value["policyRef"] = _ref_value(policy)
    return value


def initialize(connection: sqlite3.Connection) -> None:
    """Create the review table on an idle caller-owned connection only."""
    _connection(connection)
    if connection.in_transaction:
        _fail("INITIALIZATION_REQUIRES_IDLE_CONNECTION")
    try:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "review_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL, "
            "record_sha256 TEXT NOT NULL, record_json BLOB NOT NULL, "
            "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT, "
            "approval_id TEXT UNIQUE, "
            "CHECK((revision=1 AND state='pending' AND decided_at IS NULL AND approval_id IS NULL) "
            "OR (revision=2 AND state='approved' AND decided_at IS NOT NULL AND approval_id IS NOT NULL) "
            "OR (revision=2 AND state IN ('rejected','cancelled','expired') "
            "AND decided_at IS NOT NULL AND approval_id IS NULL)))"
        )
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")


def create_review(
    connection: sqlite3.Connection,
    *,
    selection: SourceSelection,
    repository_id: str,
    scope_ref: ArtifactRef | Mapping[str, object],
    provenance_ref: ArtifactRef | Mapping[str, object],
    policy_ref: ArtifactRef | Mapping[str, object],
    now: datetime,
    ttl_seconds: int = DEFAULT_REVIEW_TTL_SECONDS,
) -> SourceReviewRecordV1:
    """Create a pending review record; this does not issue a grant."""
    _require_transaction(connection)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        _fail("INVALID_TTL")
    stamp = _timestamp(now)
    created = _iso(stamp)
    expires = _iso(stamp + timedelta(seconds=ttl_seconds))
    selected = json.loads(canonical_source_selection(selection).decode("utf-8"))
    scope = _ref(scope_ref)
    provenance = _ref(provenance_ref)
    policy = _ref(policy_ref)
    expected_policy = ArtifactRef(
        id="source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
        sha256=BUILTIN_SOURCE_PROFILE_V1_SHA256,
        version=1,
    )
    if policy != expected_policy:
        _fail("INVALID_POLICY_REF")
    if selection.provenance_ref != provenance.id or selection.exclusion_policy_ref != policy.id:
        _fail("SOURCE_SELECTION_REF_MISMATCH")
    review_id = uuid.uuid4().hex
    value = {
        "schemaVersion": 1,
        "recordKind": "onepath.source-review",
        "reviewId": review_id,
        "createdAt": created,
        "expiresAt": expires,
        "selection": selected,
        "repositoryId": _text(repository_id, maximum=256),
        "scopeRef": _ref_value(scope),
        "provenanceRef": _ref_value(provenance),
        "policyRef": _ref_value(policy),
        "policyProfileSha256": BUILTIN_SOURCE_PROFILE_V1_SHA256,
    }
    raw = _canonical(value)
    digest = hashlib.sha256(raw).hexdigest()
    _validate_payload(raw, digest, review_id, created, expires)
    try:
        connection.execute(
            f"INSERT INTO {_TABLE}(review_id,revision,state,record_sha256,record_json,created_at,expires_at,decided_at,approval_id) "
            "VALUES(?,1,'pending',?,?,?, ?,NULL,NULL)",
            (review_id, digest, sqlite3.Binary(raw), created, expires),
        )
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return SourceReviewRecordV1(review_id, 1, "pending", digest, raw, created, expires, None, None)


def _row_record(row) -> SourceReviewRecordV1:
    if row is None:
        _fail("REVIEW_NOT_FOUND")
    review_id, revision, state, digest, raw, created, expires, decided, approval_id = row
    if (not isinstance(review_id, str) or not re.fullmatch(r"[0-9a-f]{32}", review_id)
            or type(revision) is not int or state not in _STATES
            or not isinstance(created, str) or not isinstance(expires, str)):
        _fail("REVIEW_CORRUPT")
    # SQLite may return any storage class for a corrupted BLOB column. Validate
    # its exact representation and bound before decoding or copying it.
    if type(raw) is not bytes or len(raw) > MAX_REVIEW_BYTES:
        _fail("REVIEW_CORRUPT")
    payload = _validate_payload(raw, digest, review_id, created, expires)
    created_time = _parse_iso(created)
    expires_time = _parse_iso(expires)
    if revision == 1 and state == "pending":
        if decided is not None or approval_id is not None:
            _fail("REVIEW_CORRUPT")
    elif revision == 2 and state in _DECISIONS:
        if not isinstance(decided, str):
            _fail("REVIEW_CORRUPT")
        decided_time = _parse_iso(decided)
        if decided_time < created_time:
            _fail("REVIEW_CORRUPT")
        if state == "expired":
            if decided_time < expires_time or approval_id is not None:
                _fail("REVIEW_CORRUPT")
        elif decided_time >= expires_time:
            _fail("REVIEW_CORRUPT")
        if state == "approved":
            if (not isinstance(approval_id, str) or not _ID.fullmatch(approval_id)):
                _fail("REVIEW_CORRUPT")
        elif approval_id is not None:
            _fail("REVIEW_CORRUPT")
    else:
        _fail("REVIEW_CORRUPT")
    return SourceReviewRecordV1(
        review_id, revision, state, digest, raw, created, expires, decided, approval_id,
    )


def get_review(connection: sqlite3.Connection, review_id: str) -> SourceReviewRecordV1:
    """Load and integrity-check a review without changing its state."""
    _require_initialized(connection)
    if not isinstance(review_id, str) or not re.fullmatch(r"[0-9a-f]{32}", review_id):
        _fail("INVALID_REVIEW_ID")
    try:
        row = connection.execute(
            f"SELECT review_id,revision,state,record_sha256,record_json,created_at,expires_at,decided_at,approval_id "
            f"FROM {_TABLE} WHERE review_id=?", (review_id,),
        ).fetchone()
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return _row_record(row)


def decide_review(
    connection: sqlite3.Connection,
    *,
    review_id: str,
    expected_revision: int,
    expected_record_sha256: str,
    decision: str,
    now: datetime,
    approval_id: str | None = None,
) -> SourceReviewRecordV1:
    """CAS the sole pending decision; approval_id is a reference, never authority."""
    _require_transaction(connection)
    if decision not in _DECISIONS:
        _fail("INVALID_REVIEW_DECISION")
    if type(expected_revision) is not int or expected_revision != 1:
        _fail("STALE_REVIEW")
    if not isinstance(expected_record_sha256, str) or not _SHA256.fullmatch(expected_record_sha256):
        _fail("STALE_REVIEW")
    if decision == "approved":
        if not isinstance(approval_id, str) or not _ID.fullmatch(approval_id):
            _fail("INVALID_APPROVAL_ID")
    elif approval_id is not None:
        _fail("INVALID_APPROVAL_ID")
    stamp = _timestamp(now)
    record = get_review(connection, review_id)
    if (record.revision != expected_revision or record.state != "pending"
            or record.record_sha256 != expected_record_sha256):
        _fail("STALE_REVIEW")
    expires = _parse_iso(record.expires_at)
    if decision == "expired":
        if stamp < expires:
            _fail("REVIEW_NOT_EXPIRED")
    elif stamp >= expires:
        _fail("REVIEW_EXPIRED")
    decided = _iso(stamp)
    try:
        cursor = connection.execute(
            f"UPDATE {_TABLE} SET revision=2,state=?,decided_at=?,approval_id=? "
            "WHERE review_id=? AND revision=1 AND state='pending' AND record_sha256=?",
            (decision, decided, approval_id, review_id, expected_record_sha256),
        )
        if cursor.rowcount != 1:
            _fail("STALE_REVIEW")
    except sqlite3.IntegrityError:
        _fail("DUPLICATE_APPROVAL_ID")
    except sqlite3.Error:
        _fail("DATABASE_FAILURE")
    return get_review(connection, review_id)


__all__ = [
    "DEFAULT_REVIEW_TTL_SECONDS",
    "MAX_REVIEW_BYTES",
    "SourceReviewError",
    "SourceReviewRecordV1",
    "create_review",
    "decide_review",
    "get_review",
    "initialize",
]
