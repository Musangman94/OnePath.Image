"""Internal composition of authenticated sessions, source receipts, and reviews.

This service does not perform source I/O or establish human intent. A trusted
owner-confirmation controller must gate ``approve_review`` before calling it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import itertools
import json
import sqlite3
from typing import Callable

from app.schemas.cae import ArtifactRef
from app.services.cae_source_approval_service import (
    SourceApprovalGrantV1,
    SourceApprovalScopeV1,
    SourceApprovalService,
)
from app.services.cae_source_git import GitMetadataObservationV1
from app.services.cae_source_provenance import (
    ApprovedSourceFileV1,
    BUILTIN_SOURCE_PROFILE_V1_JSON,
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
)
from app.services.cae_source_receipts import (
    CaeSourceReceiptError,
    build_source_provenance_receipt,
    persist_source_provenance_receipt,
)
from app.services.local_auth_service import AuthServiceError, LocalAuthService
from app.storage import cae_source_artifacts as artifacts
from app.storage import cae_source_approvals as approvals
from app.storage import cae_source_reviews as reviews
from app.storage.cae_source_approvals import (
    ApprovedFile,
    IssuedApproval,
    SourceApprovalError,
    SourceSelection,
    canonical_source_selection,
    issue_approval,
)


_SAVEPOINTS = itertools.count()
_MAX_FILES = 32
_MAX_FILE_BYTES = 1_048_576
_MAX_TOTAL_BYTES = 4_194_304


class SourceReviewServiceError(ValueError):
    """Fixed-code orchestration error without source or credential material."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SourceReviewService:
    """Internal-only authenticated adapter for the pending review lifecycle."""

    def __init__(self, auth_service: LocalAuthService, *, clock: Callable[[], datetime] | None = None):
        self._approval = SourceApprovalService(auth_service, clock=clock)
        self._auth = auth_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() != timezone.utc.utcoffset(value)):
            raise SourceReviewServiceError("INVALID_CLOCK")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _transaction(connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise SourceReviewServiceError("TRANSACTION_REQUIRED")

    @staticmethod
    def _fixed_policy_ref() -> ArtifactRef:
        return ArtifactRef(
            id="source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
            sha256=BUILTIN_SOURCE_PROFILE_V1_SHA256,
            version=1,
        )

    @staticmethod
    def _snapshot_inputs(
        approved_files: Sequence[ApprovedSourceFileV1], captured_bytes: Mapping[str, bytes],
    ) -> tuple[tuple[ApprovedSourceFileV1, ...], dict[str, bytes]]:
        if (not isinstance(approved_files, Sequence)
                or isinstance(approved_files, (str, bytes, bytearray))
                or len(approved_files) > _MAX_FILES):
            raise CaeSourceReceiptError("SOURCE_LIMIT_EXCEEDED")
        files = tuple(ApprovedSourceFileV1(f.path, f.size_bytes, f.sha256)
                      for f in approved_files if isinstance(f, ApprovedSourceFileV1))
        if len(files) != len(approved_files):
            raise CaeSourceReceiptError("INVALID_SOURCE_SELECTION")
        if not isinstance(captured_bytes, Mapping) or len(captured_bytes) > _MAX_FILES:
            raise CaeSourceReceiptError("INVALID_CAPTURED_BYTES")
        entries = tuple(captured_bytes.items())
        if len(entries) > _MAX_FILES or any(not isinstance(path, str) or type(raw) is not bytes
                                             for path, raw in entries):
            raise CaeSourceReceiptError("INVALID_CAPTURED_BYTES")
        snapshot = dict(entries)
        if len(snapshot) != len(entries):
            raise CaeSourceReceiptError("INVALID_CAPTURED_BYTES")
        if any(len(raw) > _MAX_FILE_BYTES for raw in snapshot.values()) or sum(
                len(raw) for raw in snapshot.values()) > _MAX_TOTAL_BYTES:
            raise CaeSourceReceiptError("SOURCE_LIMIT_EXCEEDED")
        return files, snapshot

    @staticmethod
    def _scope_from_receipt(value: dict, receipt_ref: ArtifactRef) -> SourceApprovalScopeV1:
        root = value["root"]
        files = tuple(ApprovedFile(
            item["path"], item["sourceSha256"], item["sizeBytes"], item["origin"],
        ) for item in value["files"])
        return SourceApprovalScopeV1(
            root_path=root["path"],
            root_identity=approvals.RootIdentity(root["platform"], root["device"], root["fileId"]),
            files=files,
            source_revision=value["beforeObservation"]["headOid"],
            provenance_ref=receipt_ref.id,
            exclusion_policy_version=BUILTIN_SOURCE_PROFILE_V1_SHA256,
            exclusion_policy_ref=SourceReviewService._fixed_policy_ref().id,
        )

    def _bound_selection(self, connection, session_token, stored: SourceSelection) -> SourceSelection:
        scope = SourceApprovalScopeV1(
            root_path=stored.root_path,
            root_identity=stored.root_identity,
            files=stored.files,
            source_revision=stored.source_revision,
            provenance_ref=stored.provenance_ref,
            exclusion_policy_version=stored.exclusion_policy_version,
            exclusion_policy_ref=stored.exclusion_policy_ref,
        )
        bound = self._approval.bind_scope(
            connection, session_token=session_token, scope=scope,
        )
        if canonical_source_selection(bound) != canonical_source_selection(stored):
            raise SourceReviewServiceError("SESSION_SCOPE_MISMATCH")
        return bound

    def prepare_review(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        repository_id: str,
        scope_ref: ArtifactRef,
        approved_files: Sequence[ApprovedSourceFileV1],
        captured_bytes: Mapping[str, bytes],
        before: GitMetadataObservationV1,
        after: GitMetadataObservationV1,
        ttl_seconds: int = reviews.DEFAULT_REVIEW_TTL_SECONDS,
    ) -> reviews.SourceReviewRecordV1:
        self._transaction(connection)
        files, data = self._snapshot_inputs(approved_files, captured_bytes)
        now = self._now()
        policy = self._fixed_policy_ref()
        receipt = build_source_provenance_receipt(
            repository_id=repository_id, scope_ref=scope_ref, policy_artifact_ref=policy,
            approved_files=files, captured_bytes=data, before=before, after=after,
        )
        receipt_bytes = receipt.canonical_bytes
        receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
        predicted_ref = ArtifactRef(
            id="source-" + receipt_digest, sha256=receipt_digest, version=1,
        )
        scope = self._scope_from_receipt(receipt.as_dict(), predicted_ref)
        bound = self._approval.bind_scope(
            connection, session_token=session_token, scope=scope,
        )
        savepoint = f"cae_source_review_{next(_SAVEPOINTS)}"
        try:
            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                actual_ref, stored_receipt = persist_source_provenance_receipt(
                    connection, repository_id=repository_id, scope_ref=scope_ref,
                    approved_files=files, captured_bytes=data, before=before, after=after,
                )
                if actual_ref != predicted_ref or stored_receipt.canonical_bytes != receipt_bytes:
                    raise SourceReviewServiceError("RECEIPT_IDENTITY_MISMATCH")
                record = reviews.create_review(
                    connection, selection=bound, repository_id=repository_id,
                    scope_ref=scope_ref, provenance_ref=actual_ref, policy_ref=policy,
                    now=now, ttl_seconds=ttl_seconds,
                )
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                self._rollback_savepoint(connection, savepoint)
                raise
        except (sqlite3.Error, CaeSourceReceiptError, SourceApprovalError,
                reviews.SourceReviewError, AuthServiceError):
            raise
        return record

    def approve_review(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        review_id: str,
        expected_revision: int,
        expected_record_sha256: str,
        ttl_seconds: int = 300,
    ) -> SourceApprovalGrantV1:
        self._transaction(connection)
        self._auth.authenticate_session(session_token, connection=connection)
        record = reviews.get_review(connection, review_id)
        stored = reviews.selection_from_review(record)
        bound = self._bound_selection(connection, session_token, stored)
        savepoint = f"cae_source_review_{next(_SAVEPOINTS)}"
        try:
            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                approval = issue_approval(
                    connection, bound, now=self._now(), ttl_seconds=ttl_seconds,
                )
                reviews.decide_review(
                    connection, review_id=review_id, expected_revision=expected_revision,
                    expected_record_sha256=expected_record_sha256, decision="approved",
                    now=self._now(), approval_id=approval.approval_id,
                )
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                self._rollback_savepoint(connection, savepoint)
                raise
        except (sqlite3.Error, SourceApprovalError, reviews.SourceReviewError):
            raise
        return SourceApprovalGrantV1(bound, approval)

    def decline_review(
        self,
        connection: sqlite3.Connection,
        *,
        session_token: str | None,
        review_id: str,
        expected_revision: int,
        expected_record_sha256: str,
        decision: str,
    ) -> reviews.SourceReviewRecordV1:
        self._transaction(connection)
        if not isinstance(decision, str) or decision not in {"rejected", "cancelled"}:
            raise SourceReviewServiceError("INVALID_REVIEW_DECISION")
        self._auth.authenticate_session(session_token, connection=connection)
        record = reviews.get_review(connection, review_id)
        stored = reviews.selection_from_review(record)
        self._bound_selection(connection, session_token, stored)
        return reviews.decide_review(
            connection, review_id=review_id, expected_revision=expected_revision,
            expected_record_sha256=expected_record_sha256, decision=decision,
            now=self._now(),
        )

    @staticmethod
    def _rollback_savepoint(connection: sqlite3.Connection, savepoint: str) -> None:
        try:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        except sqlite3.Error:
            pass
        try:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except sqlite3.Error:
            pass


__all__ = ["SourceReviewService", "SourceReviewServiceError"]
