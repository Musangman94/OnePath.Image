"""Durable metadata markers for consumed pre-read source approvals.

Rows record that a grant was consumed for a future read attempt. They are not
permission, proof that bytes were read, or source-capture results.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from typing import Literal

from app.storage.cae_source_approvals import SourceApprovalError


_TABLE = "cae_source_read_attempts"
_APPROVAL_ID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_V2_KIND = "onepath.preread-source-capture"


@dataclass(frozen=True)
class ConsumedReadAttemptV1:
    """Provisional marker; the caller's outer transaction may still roll back."""

    attempt_id: str
    approval_id: str
    selection_sha256: str
    consumed_at: str
    state: Literal["consumed_for_read"]


def initialize(connection: sqlite3.Connection) -> None:
    """Create the additive marker table on an idle caller-owned connection."""
    if not isinstance(connection, sqlite3.Connection):
        raise SourceApprovalError("INVALID_CONNECTION")
    if connection.in_transaction:
        raise SourceApprovalError("INITIALIZATION_REQUIRES_IDLE_CONNECTION")
    try:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "attempt_id TEXT PRIMARY KEY CHECK(length(attempt_id)=32 AND attempt_id NOT GLOB '*[^0-9a-f]*'), "
            "approval_id TEXT NOT NULL UNIQUE, "
            "selection_sha256 TEXT NOT NULL CHECK(length(selection_sha256)=64 AND selection_sha256 NOT GLOB '*[^0-9a-f]*'), "
            "consumed_at TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK(state='consumed_for_read'))"
        )
    except sqlite3.Error:
        raise SourceApprovalError("DATABASE_FAILURE") from None


def record_consumed_marker(
    connection: sqlite3.Connection,
    *,
    approval_id: str,
    selection_json: bytes,
    selection_sha256: str,
) -> ConsumedReadAttemptV1:
    """Insert only for the exact canonical v2 binding already consumed in this UOW."""
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        raise SourceApprovalError("TRANSACTION_REQUIRED")
    if (not isinstance(approval_id, str) or not _APPROVAL_ID.fullmatch(approval_id)
            or type(selection_json) is not bytes
            or not isinstance(selection_sha256, str) or not _SHA256.fullmatch(selection_sha256)):
        raise SourceApprovalError("INVALID_APPROVAL")
    _validate_v2_binding(selection_json, selection_sha256)
    attempt_id = uuid.uuid4().hex
    try:
        cursor = connection.execute(
            f"INSERT INTO {_TABLE}(attempt_id,approval_id,selection_sha256,consumed_at,state) "
            "SELECT ?,a.approval_id,a.selection_sha256,a.consumed_at,'consumed_for_read' "
            "FROM cae_source_approvals AS a "
            "WHERE a.approval_id=? AND a.selection_sha256=? AND a.selection_json=? "
            "AND a.consumed_at IS NOT NULL",
            (attempt_id, approval_id, selection_sha256, sqlite3.Binary(selection_json)),
        )
        if cursor.rowcount != 1:
            raise SourceApprovalError("APPROVAL_UNAVAILABLE")
        row = connection.execute(
            f"SELECT consumed_at FROM {_TABLE} WHERE attempt_id=? AND approval_id=? "
            "AND selection_sha256=? AND state='consumed_for_read'",
            (attempt_id, approval_id, selection_sha256),
        ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise SourceApprovalError("ATTEMPT_UNAVAILABLE")
        return ConsumedReadAttemptV1(attempt_id, approval_id, selection_sha256, row[0], "consumed_for_read")
    except SourceApprovalError:
        raise
    except sqlite3.IntegrityError:
        raise SourceApprovalError("ATTEMPT_UNAVAILABLE") from None
    except sqlite3.Error:
        raise SourceApprovalError("DATABASE_FAILURE") from None


def _validate_v2_binding(selection_json: bytes, selection_sha256: str) -> None:
    """Rebuild and compare the exact v2 payload; never accept caller-labeled JSON."""
    if not hmac.compare_digest(hashlib.sha256(selection_json).hexdigest(), selection_sha256):
        raise SourceApprovalError("INVALID_APPROVAL")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            selection_json.decode("utf-8"), object_pairs_hook=unique_pairs,
            parse_constant=lambda _item: (_ for _ in ()).throw(ValueError("invalid constant")),
        )
        if (not isinstance(value, dict)
                or set(value) != {"schemaVersion", "selectionKind", "authScope", "captureScope"}
                or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 2
                or value["selectionKind"] != _V2_KIND
                or not isinstance(value["authScope"], dict)
                or set(value["authScope"]) != {"ownerId", "principalId", "clientId", "sessionId"}
                or not isinstance(value["captureScope"], dict)):
            raise ValueError("invalid v2 envelope")

        capture = value["captureScope"]
        if (set(capture) != {"schemaVersion", "root", "relativePaths", "policyRef", "limits"}
                or not isinstance(capture["root"], dict)
                or set(capture["root"]) != {"absolutePath", "identity"}
                or not isinstance(capture["root"]["identity"], dict)
                or set(capture["root"]["identity"]) != {"platform", "device", "fileId"}
                or not isinstance(capture["relativePaths"], list)
                or not isinstance(capture["limits"], dict)
                or set(capture["limits"]) != {"maxFiles", "maxFileBytes", "maxTotalBytes"}):
            raise ValueError("invalid capture scope")

        from app.services.cae_source_capture_scope import SourceCaptureScopeV1
        from app.services.cae_source_files import SourceReadLimits
        from app.storage.cae_source_approvals import RootIdentity, canonical_source_capture_selection

        identity = capture["root"]["identity"]
        limits = capture["limits"]
        scope = SourceCaptureScopeV1(
            schema_version=capture["schemaVersion"],
            root_path=capture["root"]["absolutePath"],
            root_identity=RootIdentity(identity["platform"], identity["device"], identity["fileId"]),
            relative_paths=tuple(capture["relativePaths"]),
            limits=SourceReadLimits(limits["maxFiles"], limits["maxFileBytes"], limits["maxTotalBytes"]),
        )
        auth = value["authScope"]
        expected = canonical_source_capture_selection(
            scope, owner_id=auth["ownerId"], principal_id=auth["principalId"],
            client_id=auth["clientId"], session_id=auth["sessionId"],
        )
        if expected != selection_json:
            raise ValueError("noncanonical v2 binding")
    except SourceApprovalError:
        raise
    except Exception:
        raise SourceApprovalError("INVALID_APPROVAL") from None


__all__ = ["ConsumedReadAttemptV1", "initialize", "record_consumed_marker"]
