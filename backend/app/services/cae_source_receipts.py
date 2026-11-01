"""Deterministic evidence receipts for bounded CAE source selections.

Receipts preserve metadata supplied by the caller. They do not authenticate a
caller, grant permission, inspect the filesystem, or run Git.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import itertools
import json
import re
import sqlite3

from pydantic import ValidationError

from app.schemas.cae import ArtifactRef
from app.services.cae_source_git import GitExecutableIdentityV1, GitMetadataObservationV1
from app.services.cae_source_provenance import (
    ApprovedSourceFileV1,
    BUILTIN_SOURCE_PROFILE_V1_JSON,
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
    SourceIndexEntryV1,
    SourceIndexObservationV1,
    compute_git_blob_oid,
    validate_approved_files,
)
from app.storage import cae_source_artifacts as artifacts
from app.storage.cae_source_approvals import RootIdentity


MAX_RECEIPT_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAVEPOINTS = itertools.count()


class CaeSourceReceiptError(ValueError):
    """Fixed-code receipt validation/persistence error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, init=False)
class SourceProvenanceReceiptV1:
    """Immutable canonical receipt bytes; ``as_dict`` returns a detached view."""

    _canonical_bytes: bytes = field(repr=False)

    def __init__(self, canonical_bytes: bytes):
        if type(canonical_bytes) is not bytes or len(canonical_bytes) > MAX_RECEIPT_BYTES:
            raise CaeSourceReceiptError("RECEIPT_LIMIT_EXCEEDED")
        object.__setattr__(self, "_canonical_bytes", bytes(canonical_bytes))

    @property
    def canonical_bytes(self) -> bytes:
        return self._canonical_bytes

    def as_dict(self) -> dict:
        try:
            return json.loads(self._canonical_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CaeSourceReceiptError("INVALID_RECEIPT") from None


def _fail(code: str) -> None:
    raise CaeSourceReceiptError(code)


def _canonical_json(value: object) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("INVALID_RECEIPT")
    if len(raw) > MAX_RECEIPT_BYTES:
        _fail("RECEIPT_LIMIT_EXCEEDED")
    return raw


def _text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        _fail("INVALID_RECEIPT_TEXT")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("INVALID_RECEIPT_TEXT")
    if len(encoded) > maximum:
        _fail("INVALID_RECEIPT_TEXT")
    return value


def _artifact_ref(value: ArtifactRef | Mapping[str, object]) -> ArtifactRef:
    try:
        ref = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except (ValidationError, TypeError):
        _fail("INVALID_ARTIFACT_REF")
    if type(ref.version) is not int or ref.version != 1 or ref.id != "source-" + ref.sha256:
        _fail("INVALID_ARTIFACT_REF")
    return ArtifactRef(id=ref.id, sha256=ref.sha256, version=ref.version)


def _artifact_ref_value(ref: ArtifactRef) -> dict[str, object]:
    return {"id": ref.id, "sha256": ref.sha256, "version": ref.version}


def _fixed_policy_ref() -> ArtifactRef:
    if hashlib.sha256(BUILTIN_SOURCE_PROFILE_V1_JSON).hexdigest() != BUILTIN_SOURCE_PROFILE_V1_SHA256:
        _fail("SOURCE_POLICY_INTEGRITY_FAILED")
    return ArtifactRef(
        id="source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
        sha256=BUILTIN_SOURCE_PROFILE_V1_SHA256,
        version=1,
    )


def _root_value(observation: GitMetadataObservationV1) -> dict[str, object]:
    root = observation.root_identity
    if (not isinstance(root, RootIdentity) or not isinstance(root.platform, str)
            or not root.platform or type(root.device) is not int or root.device < 0
            or type(root.file_id) is not int or root.file_id < 0):
        _fail("INVALID_ROOT_IDENTITY")
    return {
        "path": _text(observation.root_path, maximum=4096),
        "platform": _text(root.platform, maximum=64),
        "device": root.device,
        "fileId": root.file_id,
    }


def _observation_value(observation: GitMetadataObservationV1) -> dict:
    if (not isinstance(observation, GitMetadataObservationV1)
            or type(observation.schema_version) is not int or observation.schema_version != 1):
        _fail("INVALID_GIT_OBSERVATION")
    root = _root_value(observation)
    executable = observation.executable
    if (not isinstance(executable, GitExecutableIdentityV1)
            or not isinstance(executable.canonical_path, str)
            or not isinstance(executable.sha256, str)
            or not _SHA256.fullmatch(executable.sha256)):
        _fail("INVALID_GIT_EXECUTABLE")
    executable_value = {
        "path": _text(executable.canonical_path, maximum=4096),
        "sha256": executable.sha256,
        "expectedVersionLabel": _text(executable.expected_version_label, maximum=256),
    }
    if observation.object_format not in {"sha1", "sha256"}:
        _fail("INVALID_GIT_OBSERVATION")
    oid_size = 40 if observation.object_format == "sha1" else 64
    if (not isinstance(observation.head_oid, str) or len(observation.head_oid) != oid_size
            or not re.fullmatch(r"[0-9a-f]+", observation.head_oid)
            or not isinstance(observation.root_output_sha256, str)
            or not _SHA256.fullmatch(observation.root_output_sha256)):
        _fail("INVALID_GIT_OBSERVATION")

    index = observation.source_index
    if (not isinstance(index, SourceIndexObservationV1)
            or type(index.schema_version) is not int or index.schema_version != 1
            or index.source_policy_profile_sha256 != BUILTIN_SOURCE_PROFILE_V1_SHA256
            or index.object_format != observation.object_format
            or index.head_oid != observation.head_oid
            or not isinstance(index.raw_index_output_sha256, str)
            or not _SHA256.fullmatch(index.raw_index_output_sha256)
            or not isinstance(index.entries, tuple)):
        _fail("INVALID_SOURCE_INDEX_OBSERVATION")
    try:
        index_value = json.loads(index.canonical_json().decode("utf-8"))
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
        _fail("INVALID_SOURCE_INDEX_OBSERVATION")
    return {
        "schemaVersion": observation.schema_version,
        "root": root,
        "gitExecutable": executable_value,
        "objectFormat": observation.object_format,
        "headOid": observation.head_oid,
        "rootOutputSha256": observation.root_output_sha256,
        "sourceIndex": index_value,
    }


def _validate_observation_entries(
    observation: GitMetadataObservationV1,
    files: tuple[ApprovedSourceFileV1, ...],
    copied: Mapping[str, bytes],
) -> list[dict[str, object]]:
    index = observation.source_index
    if len(index.entries) != len(files):
        _fail("SOURCE_INDEX_SELECTION_MISMATCH")
    records: list[dict[str, object]] = []
    for selected, entry in zip(files, index.entries, strict=True):
        if not isinstance(entry, SourceIndexEntryV1):
            _fail("INVALID_SOURCE_INDEX_ENTRY")
        if (entry.path != selected.path or entry.size_bytes != selected.size_bytes
                or entry.source_sha256 != selected.sha256):
            _fail("SOURCE_INDEX_SELECTION_MISMATCH")
        data = copied[selected.path]
        captured_blob_oid = compute_git_blob_oid(data, observation.object_format)
        if entry.origin == "tracked":
            if (entry.provenance_status != "index-entry" or entry.mode not in {"100644", "100755"}
                    or type(entry.stage) is not int or entry.stage != 0
                    or not isinstance(entry.index_oid, str)
                    or len(entry.index_oid) != (40 if observation.object_format == "sha1" else 64)
                    or not re.fullmatch(r"[0-9a-f]+", entry.index_oid)):
                _fail("INVALID_SOURCE_INDEX_ENTRY")
        elif entry.origin == "untracked":
            if (entry.provenance_status != "approved-index-absent" or entry.mode is not None
                    or entry.stage is not None or entry.index_oid is not None):
                _fail("INVALID_SOURCE_INDEX_ENTRY")
        else:
            _fail("INVALID_SOURCE_INDEX_ENTRY")
        records.append({
            "path": selected.path,
            "sizeBytes": selected.size_bytes,
            "sourceSha256": selected.sha256,
            "origin": entry.origin,
            "mode": entry.mode,
            "stage": entry.stage,
            "indexOid": entry.index_oid,
            "provenanceStatus": entry.provenance_status,
            "capturedBlobOid": captured_blob_oid,
        })
    return records


def build_source_provenance_receipt(
    *,
    repository_id: str,
    scope_ref: ArtifactRef | Mapping[str, object],
    policy_artifact_ref: ArtifactRef | Mapping[str, object],
    approved_files: Sequence[ApprovedSourceFileV1],
    captured_bytes: Mapping[str, bytes],
    before: GitMetadataObservationV1,
    after: GitMetadataObservationV1,
) -> SourceProvenanceReceiptV1:
    """Build bounded evidence from supplied bytes and metadata; perform no I/O."""
    repository_id = _text(repository_id, maximum=256)
    scope = _artifact_ref(scope_ref)
    policy = _artifact_ref(policy_artifact_ref)
    expected_policy = _fixed_policy_ref()
    if policy != expected_policy:
        _fail("SOURCE_POLICY_REFERENCE_MISMATCH")
    try:
        files = validate_approved_files(approved_files)
    except (TypeError, ValueError):
        _fail("INVALID_SOURCE_SELECTION")
    if not isinstance(captured_bytes, Mapping):
        _fail("INVALID_CAPTURED_BYTES")
    try:
        items = list(captured_bytes.items())
    except Exception:
        _fail("INVALID_CAPTURED_BYTES")
    if (len(items) != len(files) or any(not isinstance(path, str) for path, _ in items)
            or {path for path, _ in items} != {entry.path for entry in files}):
        _fail("SOURCE_FILE_SET_MISMATCH")
    copied: dict[str, bytes] = {}
    total = 0
    for entry in files:
        raw = captured_bytes[entry.path]
        if type(raw) is not bytes:
            _fail("INVALID_CAPTURED_BYTES")
        if len(raw) != entry.size_bytes:
            _fail("SOURCE_SIZE_MISMATCH")
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            _fail("SOURCE_HASH_MISMATCH")
        total += len(raw)
        copied[entry.path] = bytes(raw)
    if total > 4_194_304:
        _fail("SOURCE_LIMIT_EXCEEDED")

    before_value = _observation_value(before)
    after_value = _observation_value(after)
    before_json = _canonical_json(before_value)
    after_json = _canonical_json(after_value)
    if before_json != after_json:
        _fail("STALE_GIT_OBSERVATION")
    records = _validate_observation_entries(before, files, copied)
    value = {
        "schemaVersion": 1,
        "evidenceType": "onepath.source-provenance",
        "repositoryId": repository_id,
        "scopeRef": _artifact_ref_value(scope),
        "policy": {
            "profileSha256": BUILTIN_SOURCE_PROFILE_V1_SHA256,
            "artifactRef": _artifact_ref_value(policy),
        },
        "root": before_value["root"],
        "files": records,
        "beforeObservation": before_value,
        "afterObservation": after_value,
    }
    return SourceProvenanceReceiptV1(_canonical_json(value))


def persist_source_provenance_receipt(
    connection: sqlite3.Connection,
    *,
    repository_id: str,
    scope_ref: ArtifactRef | Mapping[str, object],
    approved_files: Sequence[ApprovedSourceFileV1],
    captured_bytes: Mapping[str, bytes],
    before: GitMetadataObservationV1,
    after: GitMetadataObservationV1,
) -> tuple[ArtifactRef, SourceProvenanceReceiptV1]:
    """Build, then store the fixed policy and validated receipt in caller's transaction."""
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    receipt = build_source_provenance_receipt(
        repository_id=repository_id,
        scope_ref=scope_ref,
        policy_artifact_ref=_fixed_policy_ref(),
        approved_files=approved_files,
        captured_bytes=captured_bytes,
        before=before,
        after=after,
    )
    raw = receipt.canonical_bytes

    savepoint = f"cae_source_receipt_{next(_SAVEPOINTS)}"
    try:
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            policy_ref = artifacts.put_object(connection, BUILTIN_SOURCE_PROFILE_V1_JSON)
            if policy_ref != _fixed_policy_ref():
                _fail("SOURCE_POLICY_REFERENCE_MISMATCH")
            receipt_ref = artifacts.put_object(connection, raw)
            if artifacts.read_object(connection, receipt_ref) != raw:
                _fail("RECEIPT_PERSISTENCE_FAILED")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            try:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            except sqlite3.Error:
                pass
            try:
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except sqlite3.Error:
                pass
            raise
    except CaeSourceReceiptError:
        raise
    except artifacts.CaeSourceArtifactError:
        raise
    except sqlite3.Error:
        raise CaeSourceReceiptError("DATABASE_FAILURE") from None
    return receipt_ref, receipt
