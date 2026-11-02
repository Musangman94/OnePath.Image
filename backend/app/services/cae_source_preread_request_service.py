"""Dormant request-bound issuance composition for CAE v2 capture grants.

This adapter is intentionally unrouted. Session authentication and issuance
are not human confirmation; no trusted owner-action caller exists yet.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import itertools
import sqlite3
from typing import Callable

from app.services.cae_source_approval_service import SourceCaptureApprovalGrantV2
from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage.cae_source_approvals import (
    SourceApprovalError,
    canonical_source_capture_selection,
    issue_capture_approval,
)
from app.storage import cae_source_preread_issuances as issuances
from app.storage import cae_source_preread_requests as requests


_SAVEPOINTS = itertools.count()


class SourcePrereadRequestService:
    """Issue a v2 grant only from a pending request bound to the live session."""

    def __init__(self, auth_service: LocalAuthService, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(auth_service, LocalAuthService):
            raise ValueError("Authentication service is invalid.")
        self._auth = auth_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceApprovalError("INVALID_TIME")
        return value.astimezone(timezone.utc)

    def issue_for_request(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        request_id: str,
        expected_revision: int,
        expected_record_sha256: str,
        ttl_seconds: int = 300,
    ) -> SourceCaptureApprovalGrantV2:
        """Prepare a provisional grant; the caller owns and commits the transaction.

        The caller supplies no scope or identity. A future trusted owner-action
        boundary must invoke this only after actual owner confirmation. The
        secret is never persisted: after an ambiguous commit or lost response,
        callers must not retry until rollback is known; a committed but lost
        grant requires a fresh owner action and request after its expiry.
        """
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise SourceApprovalError("TRANSACTION_REQUIRED")
        savepoint = f"cae_preread_issue_{next(_SAVEPOINTS)}"
        try:
            connection.execute(f"SAVEPOINT {savepoint}")
        except sqlite3.Error:
            raise SourceApprovalError("DATABASE_FAILURE") from None
        try:
            proof = self._auth.authenticate_session(session_token, connection=connection)
            record = requests.get_request(connection, request_id)
            if (type(expected_revision) is not int or expected_revision != 1
                    or not isinstance(expected_record_sha256, str)
                    or record.revision != expected_revision
                    or record.record_sha256 != expected_record_sha256
                    or record.state != "pending"):
                raise SourceApprovalError("APPROVAL_UNAVAILABLE")
            now = self._now()
            auth_scope = record.as_dict()["authScope"]
            principal_id = proof.owner.principal.id
            if (auth_scope["ownerId"] != principal_id
                    or auth_scope["principalId"] != principal_id
                    or auth_scope["clientId"] != proof.stored_client_id
                    or auth_scope["sessionFingerprint"] != proof.session_fingerprint):
                raise SourceApprovalError("APPROVAL_UNAVAILABLE")
            request_expires = datetime.fromisoformat(record.expires_at[:-1] + "+00:00")
            remaining_seconds = int((request_expires - now).total_seconds())
            if remaining_seconds < 1:
                raise SourceApprovalError("APPROVAL_UNAVAILABLE")
            if type(ttl_seconds) is not int or ttl_seconds < 1:
                raise SourceApprovalError("INVALID_TTL")
            ttl = min(ttl_seconds, remaining_seconds)
            scope = requests.scope_from_request(record)
            if type(scope) is not SourceCaptureScopeV1:
                raise SourceApprovalError("APPROVAL_UNAVAILABLE")
            approval = issue_capture_approval(
                connection, scope, owner_id=principal_id, principal_id=principal_id,
                client_id=proof.stored_client_id, session_id=proof.session_fingerprint,
                now=now, ttl_seconds=ttl,
            )
            selection_json = canonical_source_capture_selection(
                scope, owner_id=principal_id, principal_id=principal_id,
                client_id=proof.stored_client_id, session_id=proof.session_fingerprint,
            )
            issuances.record(
                connection, request_id=record.request_id,
                request_sha256=record.record_sha256, request_expires_at=record.expires_at,
                approval_id=approval.approval_id, selection_json=selection_json,
                approval_expires_at=approval.expires_at,
                issued_at=now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            )
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return SourceCaptureApprovalGrantV2(scope, approval)
        except BaseException as exc:
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                raise SourceApprovalError("TRANSACTION_STATE_UNCERTAIN") from None
            if isinstance(exc, (SourceApprovalError, AuthServiceError)):
                raise
            if isinstance(exc, requests.SourcePrereadRequestError):
                raise SourceApprovalError("APPROVAL_UNAVAILABLE") from None
            raise SourceApprovalError("DATABASE_FAILURE") from None


__all__ = ["SourcePrereadRequestService"]
