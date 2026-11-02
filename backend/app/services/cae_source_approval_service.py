"""Authenticated adapter for the internal one-use source approval ledger.

This service revalidates owner sessions and binds their identity to ledger
operations. It is not itself a human-confirmation workflow or source collector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
from typing import Callable

from app.services.local_auth_service import (
    AuthServiceError,
    LocalAuthService,
    _AuthenticatedSessionProof,
)
from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.storage.cae_source_approvals import (
    ApprovedFile,
    IssuedApproval,
    RootIdentity,
    SourceApprovalError,
    SourceSelection,
    canonical_source_capture_selection,
    consume_capture_approval,
    consume_approval,
    issue_capture_approval,
    issue_approval,
)
from app.storage import cae_source_read_attempts as read_attempts
import sqlite3


@dataclass(frozen=True)
class SourceApprovalScopeV1:
    """Identity-free, copied selection metadata supplied by the caller."""

    root_path: str
    root_identity: RootIdentity
    files: tuple[ApprovedFile, ...]
    source_revision: str | None
    provenance_ref: str
    exclusion_policy_version: str
    exclusion_policy_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.root_identity, RootIdentity):
            raise TypeError("root_identity must be RootIdentity")
        if (not isinstance(self.files, Sequence)
                or isinstance(self.files, (str, bytes, bytearray))):
            raise TypeError("files must be a sequence of ApprovedFile values")
        snapshot: list[ApprovedFile] = []
        for entry in tuple(self.files):
            if not isinstance(entry, ApprovedFile):
                raise TypeError("files must contain ApprovedFile values")
            snapshot.append(ApprovedFile(
                entry.path, entry.sha256, entry.size_bytes, entry.origin,
            ))
        object.__setattr__(self, "root_identity", RootIdentity(
            self.root_identity.platform, self.root_identity.device, self.root_identity.file_id,
        ))
        object.__setattr__(self, "files", tuple(snapshot))


@dataclass(frozen=True)
class SourceApprovalGrantV1:
    """Bound selection and one-use secret for a trusted internal caller."""

    selection: SourceSelection
    approval: IssuedApproval


@dataclass(frozen=True)
class SourceCaptureApprovalGrantV2:
    """One-use path-only grant; this record is not proof of human intent."""

    scope: SourceCaptureScopeV1
    approval: IssuedApproval


_READ_ATTEMPT_SAVEPOINTS = itertools.count()


class SourceApprovalService:
    """Compose live owner-session proof with the existing approval ledger."""

    def __init__(self, auth_service: LocalAuthService, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(auth_service, LocalAuthService):
            raise ValueError("Local authentication service is required.")
        if clock is not None and not callable(clock):
            raise ValueError("Clock must be callable.")
        self.auth_service = auth_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() != timezone.utc.utcoffset(value)):
            raise ValueError("Clock must return an aware UTC datetime.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _selection(proof: _AuthenticatedSessionProof, scope: SourceApprovalScopeV1) -> SourceSelection:
        if not isinstance(scope, SourceApprovalScopeV1):
            raise TypeError("scope must be SourceApprovalScopeV1")
        principal = proof.owner.principal
        # This product currently has one owner principal identifier; no separate owner ID exists.
        return SourceSelection(
            owner_id=principal.id,
            principal_id=principal.id,
            client_id=proof.stored_client_id,
            session_id=proof.session_fingerprint,
            root_path=scope.root_path,
            root_identity=scope.root_identity,
            files=scope.files,
            source_revision=scope.source_revision,
            provenance_ref=scope.provenance_ref,
            exclusion_policy_version=scope.exclusion_policy_version,
            exclusion_policy_ref=scope.exclusion_policy_ref,
        )

    def bind_scope(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceApprovalScopeV1,
    ) -> SourceSelection:
        """Bind identity-free scope data to a freshly authenticated session."""
        proof = self.auth_service.authenticate_session(session_token, connection=connection)
        return self._selection(proof, scope)

    def issue(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceApprovalScopeV1,
        ttl_seconds: int = 300,
    ) -> SourceApprovalGrantV1:
        """Issue a one-use ledger token after live session revalidation.

        The caller must invoke this only after a separate owner-confirmation
        action; the session cookie alone is authentication, not that action.
        The caller owns the transaction and its eventual commit or rollback.
        """
        proof = self.auth_service.authenticate_session(session_token, connection=connection)
        selection = self._selection(proof, scope)
        approval = issue_approval(
            connection, selection, now=self._now(), ttl_seconds=ttl_seconds,
        )
        return SourceApprovalGrantV1(selection, approval)

    def consume(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceApprovalScopeV1,
        approval: IssuedApproval,
    ) -> SourceSelection:
        """Consume once for the currently authenticated session and exact scope."""
        proof = self.auth_service.authenticate_session(session_token, connection=connection)
        selection = self._selection(proof, scope)
        consume_approval(connection, approval, selection, now=self._now())
        return selection

    def issue_preread_capture(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceCaptureScopeV1,
        ttl_seconds: int = 300,
    ) -> SourceCaptureApprovalGrantV2:
        """Issue v2 path-only binding after a separate trusted owner action.

        Session authentication is not human confirmation. This adapter has no
        trusted confirmation caller and must remain dormant until one exists.
        """
        if type(scope) is not SourceCaptureScopeV1:
            raise SourceApprovalError("INVALID_APPROVAL")
        proof = self.auth_service.authenticate_session(session_token, connection=connection)
        principal_id = proof.owner.principal.id
        approval = issue_capture_approval(
            connection, scope, owner_id=principal_id, principal_id=principal_id,
            client_id=proof.stored_client_id, session_id=proof.session_fingerprint,
            now=self._now(), ttl_seconds=ttl_seconds,
        )
        return SourceCaptureApprovalGrantV2(scope, approval)

    def consume_preread_capture(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceCaptureScopeV1,
        approval: IssuedApproval,
    ) -> None:
        """Consume only for the exact scope and freshly authenticated session."""
        if type(scope) is not SourceCaptureScopeV1:
            raise SourceApprovalError("INVALID_APPROVAL")
        proof = self.auth_service.authenticate_session(session_token, connection=connection)
        principal_id = proof.owner.principal.id
        consume_capture_approval(
            connection, approval, scope, owner_id=principal_id, principal_id=principal_id,
            client_id=proof.stored_client_id, session_id=proof.session_fingerprint,
            now=self._now(),
        )

    def consume_preread_capture_for_read(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        scope: SourceCaptureScopeV1,
        approval: IssuedApproval,
    ) -> read_attempts.ConsumedReadAttemptV1:
        """Atomically consume v2 approval and record its non-authoritative marker.

        The caller owns the outer BEGIN IMMEDIATE transaction and its commit.
        The result is provisional until that commit succeeds and says nothing
        about whether a reader was invoked or bytes were captured.
        """
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise SourceApprovalError("TRANSACTION_REQUIRED")
        savepoint = f"cae_preread_attempt_{next(_READ_ATTEMPT_SAVEPOINTS)}"
        try:
            connection.execute(f"SAVEPOINT {savepoint}")
        except sqlite3.Error:
            raise SourceApprovalError("DATABASE_FAILURE") from None
        try:
            self.consume_preread_capture(
                connection, session_token=session_token, scope=scope, approval=approval,
            )
            proof = self.auth_service.authenticate_session(session_token, connection=connection)
            principal_id = proof.owner.principal.id
            payload = canonical_source_capture_selection(
                scope, owner_id=principal_id, principal_id=principal_id,
                client_id=proof.stored_client_id, session_id=proof.session_fingerprint,
            )
            marker = read_attempts.record_consumed_marker(
                connection, approval_id=approval.approval_id,
                selection_json=payload,
                selection_sha256=hashlib.sha256(payload).hexdigest(),
            )
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return marker
        except BaseException as exc:
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                raise SourceApprovalError("TRANSACTION_STATE_UNCERTAIN") from None
            if isinstance(exc, (SourceApprovalError, AuthServiceError)):
                raise
            raise SourceApprovalError("DATABASE_FAILURE") from None


__all__ = [
    "SourceApprovalGrantV1",
    "SourceCaptureApprovalGrantV2",
    "SourceApprovalScopeV1",
    "SourceApprovalService",
    "AuthServiceError",
    "SourceApprovalError",
]
