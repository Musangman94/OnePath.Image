"""Authenticated adapter for the internal one-use source approval ledger.

This service revalidates owner sessions and binds their identity to ledger
operations. It is not itself a human-confirmation workflow or source collector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.services.local_auth_service import (
    AuthServiceError,
    LocalAuthService,
    _AuthenticatedSessionProof,
)
from app.storage.cae_source_approvals import (
    ApprovedFile,
    IssuedApproval,
    RootIdentity,
    SourceApprovalError,
    SourceSelection,
    consume_approval,
    issue_approval,
)
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


__all__ = [
    "SourceApprovalGrantV1",
    "SourceApprovalScopeV1",
    "SourceApprovalService",
    "AuthServiceError",
    "SourceApprovalError",
]
