"""Content-addressed CAE source snapshots stored in a caller-owned SQLite DB.

This module accepts already-approved metadata and already-captured bytes. It does
not walk a repository or decide which files an owner approved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import itertools
import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from app.schemas.cae import ArtifactRef, SourceFileEntryV1, SourceStateManifestV1


_TABLE = "cae_source_objects"
_SAVEPOINTS = itertools.count()


class CaeSourceArtifactError(ValueError):
    """Fixed-code storage/validation error; messages never include source data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArtifactLimits:
    max_files: int = 4096
    max_file_bytes: int = 32 * 1024 * 1024
    max_source_bytes: int = 64 * 1024 * 1024
    max_index_bytes: int = 16 * 1024 * 1024
    max_descriptor_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        for value in (self.max_files, self.max_file_bytes, self.max_source_bytes,
                      self.max_index_bytes, self.max_descriptor_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("artifact limits must be positive finite integers")


DEFAULT_LIMITS = ArtifactLimits()


def initialize(connection: sqlite3.Connection) -> None:
    """Create only this store's table on an existing caller-owned connection."""
    _check_connection(connection)
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        "sha256 TEXT PRIMARY KEY, byte_length INTEGER NOT NULL, content BLOB NOT NULL)"
    )


def _check_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise CaeSourceArtifactError("INVALID_CONNECTION")


def _require_initialized(connection: sqlite3.Connection) -> None:
    _check_connection(connection)
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
    ).fetchone()
    if row is None:
        raise CaeSourceArtifactError("STORE_NOT_INITIALIZED")


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CaeSourceArtifactError("INVALID_CANONICAL_DATA") from None


def _artifact_ref(raw: bytes) -> ArtifactRef:
    digest = hashlib.sha256(raw).hexdigest()
    return ArtifactRef(id="source-" + digest, sha256=digest, version=1)


def _validated_ref(value: ArtifactRef | Mapping[str, Any]) -> ArtifactRef:
    try:
        ref = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except (ValidationError, TypeError):
        raise CaeSourceArtifactError("INVALID_ARTIFACT_REF") from None
    if ref.version != 1 or ref.id != "source-" + ref.sha256:
        raise CaeSourceArtifactError("INVALID_ARTIFACT_REF")
    return ref


def _entry(value: SourceFileEntryV1 | Mapping[str, Any]) -> SourceFileEntryV1:
    try:
        return value if isinstance(value, SourceFileEntryV1) else SourceFileEntryV1.model_validate(value)
    except (ValidationError, TypeError):
        raise CaeSourceArtifactError("INVALID_SOURCE_ENTRY") from None


def _decode_json(raw: bytes) -> Any:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise CaeSourceArtifactError("INVALID_STORED_JSON") from None


def _insert_object(connection: sqlite3.Connection, raw: bytes) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    existing = connection.execute(
        f"SELECT byte_length,length(content),"
        f"CASE WHEN byte_length<=? AND length(content)<=? THEN content ELSE NULL END "
        f"FROM {_TABLE} WHERE sha256=?",
        (len(raw), len(raw), digest),
    ).fetchone()
    if existing is not None:
        stored_length, actual_length, value = existing
        if (type(stored_length) is not int or type(actual_length) is not int
                or stored_length != len(raw) or actual_length != len(raw) or value is None):
            raise CaeSourceArtifactError("OBJECT_INTEGRITY_FAILED")
        stored = bytes(value)
        if hashlib.sha256(stored).hexdigest() != digest:
            raise CaeSourceArtifactError("OBJECT_INTEGRITY_FAILED")
        if stored != raw:
            raise CaeSourceArtifactError("OBJECT_HASH_COLLISION")
        return
    connection.execute(
        f"INSERT INTO {_TABLE}(sha256,byte_length,content) VALUES(?,?,?)",
        (digest, len(raw), sqlite3.Binary(raw)),
    )


def _read_digest(connection: sqlite3.Connection, digest: str, max_bytes: int) -> bytes:
    row = connection.execute(
        f"SELECT byte_length,length(content),"
        f"CASE WHEN byte_length<=? AND length(content)<=? THEN content ELSE NULL END "
        f"FROM {_TABLE} WHERE sha256=?",
        (max_bytes, max_bytes, digest),
    ).fetchone()
    if row is None:
        raise CaeSourceArtifactError("OBJECT_NOT_FOUND")
    stored_length, actual_length, value = row
    if (type(stored_length) is not int or type(actual_length) is not int
            or stored_length < 0 or stored_length > max_bytes or actual_length > max_bytes
            or value is None):
        raise CaeSourceArtifactError("OBJECT_LIMIT_EXCEEDED")
    raw = bytes(value)
    if len(raw) != stored_length or actual_length != stored_length or hashlib.sha256(raw).hexdigest() != digest:
        raise CaeSourceArtifactError("OBJECT_INTEGRITY_FAILED")
    return raw


def read_object(
    connection: sqlite3.Connection,
    ref: ArtifactRef | Mapping[str, Any],
    *,
    limits: ArtifactLimits = DEFAULT_LIMITS,
) -> bytes:
    _require_initialized(connection)
    checked = _validated_ref(ref)
    return _read_digest(connection, checked.sha256, max(limits.max_source_bytes, limits.max_index_bytes,
                                                        limits.max_descriptor_bytes, limits.max_file_bytes))


def put_object(
    connection: sqlite3.Connection,
    raw: bytes,
    *,
    limits: ArtifactLimits = DEFAULT_LIMITS,
) -> ArtifactRef:
    """Persist opaque bytes on the caller's active transaction and return their reference."""
    _require_initialized(connection)
    if not connection.in_transaction:
        raise CaeSourceArtifactError("TRANSACTION_REQUIRED")
    if type(raw) is not bytes:
        raise CaeSourceArtifactError("INVALID_OBJECT_BYTES")
    if not isinstance(limits, ArtifactLimits):
        raise CaeSourceArtifactError("INVALID_LIMITS")
    if len(raw) > limits.max_source_bytes:
        raise CaeSourceArtifactError("OBJECT_LIMIT_EXCEEDED")

    ref = _artifact_ref(raw)
    savepoint = f"cae_object_write_{next(_SAVEPOINTS)}"
    try:
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            _insert_object(connection, raw)
            stored = _read_digest(connection, ref.sha256, limits.max_source_bytes)
            if stored != raw:
                raise CaeSourceArtifactError("OBJECT_INTEGRITY_FAILED")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            try:
                _savepoint_rollback(connection, savepoint)
            except sqlite3.Error:
                pass
            raise
    except CaeSourceArtifactError:
        raise
    except sqlite3.Error:
        raise CaeSourceArtifactError("DATABASE_FAILURE") from None
    return ref


def _savepoint_rollback(connection: sqlite3.Connection, name: str) -> None:
    try:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
    finally:
        connection.execute(f"RELEASE SAVEPOINT {name}")


def create_source_state(
    connection: sqlite3.Connection,
    *,
    source_state_id: str,
    repository_id: str,
    scope_ref: ArtifactRef | Mapping[str, Any],
    git_head: str | None,
    exclusion_policy_ref: ArtifactRef | Mapping[str, Any],
    files: Sequence[SourceFileEntryV1 | Mapping[str, Any]],
    file_bytes: Mapping[str, bytes | bytearray | memoryview],
    limits: ArtifactLimits = DEFAULT_LIMITS,
) -> tuple[ArtifactRef, SourceStateManifestV1]:
    """Persist a validated manifest and captured bytes atomically via savepoint."""
    _require_initialized(connection)
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)) or len(files) < 1:
        raise CaeSourceArtifactError("INVALID_SOURCE_ENTRIES")
    if len(files) > limits.max_files:
        raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")
    if not isinstance(file_bytes, Mapping):
        raise CaeSourceArtifactError("INVALID_FILE_BYTES")

    entries = sorted((_entry(item) for item in files), key=lambda item: item.path)
    expected_paths = [entry.path for entry in entries]
    if len(expected_paths) != len(file_bytes) or set(expected_paths) != set(file_bytes):
        raise CaeSourceArtifactError("SOURCE_FILE_SET_MISMATCH")

    copied: dict[str, bytes] = {}
    total = 0
    for entry in entries:
        value = file_bytes[entry.path]
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise CaeSourceArtifactError("INVALID_FILE_BYTES")
        view = memoryview(value)
        size = view.nbytes
        if size > limits.max_file_bytes or size > limits.max_source_bytes - total:
            raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")
        total += size
        if size != entry.sizeBytes:
            raise CaeSourceArtifactError("SOURCE_SIZE_MISMATCH")
        raw = bytes(view)
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise CaeSourceArtifactError("SOURCE_HASH_MISMATCH")
        copied[entry.path] = raw

    index_value = {
        "schemaVersion": 1,
        "files": [entry.model_dump(mode="json") for entry in entries],
    }
    index_bytes = _canonical_json(index_value)
    if len(index_bytes) > limits.max_index_bytes:
        raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")
    index_ref = _artifact_ref(index_bytes)
    untracked = [entry.path for entry in entries if entry.origin == "untracked"]
    try:
        descriptor = SourceStateManifestV1(
            sourceStateId=source_state_id,
            repositoryId=repository_id,
            scopeRef=_validated_ref(scope_ref),
            gitHead=git_head,
            files=entries,
            includedUntracked=untracked,
            exclusionPolicyRef=_validated_ref(exclusion_policy_ref),
            manifestArtifactRef=index_ref,
        )
    except (ValidationError, TypeError):
        raise CaeSourceArtifactError("INVALID_SOURCE_MANIFEST") from None
    descriptor_bytes = _canonical_json(descriptor.model_dump(mode="json"))
    if len(descriptor_bytes) > limits.max_descriptor_bytes:
        raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")
    source_ref = _artifact_ref(descriptor_bytes)

    objects: dict[str, bytes] = {}
    for raw in (*copied.values(), index_bytes, descriptor_bytes):
        digest = hashlib.sha256(raw).hexdigest()
        previous = objects.setdefault(digest, raw)
        if previous != raw:
            raise CaeSourceArtifactError("OBJECT_HASH_COLLISION")

    savepoint = f"cae_source_write_{next(_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        for raw in objects.values():
            _insert_object(connection, raw)
        resolved, read_files = resolve_source_state(connection, source_ref, limits=limits)
        if resolved != descriptor or read_files != copied:
            raise CaeSourceArtifactError("SOURCE_STATE_INTEGRITY_FAILED")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException as exc:
        try:
            _savepoint_rollback(connection, savepoint)
        except sqlite3.Error:
            pass
        if isinstance(exc, CaeSourceArtifactError):
            raise
        if isinstance(exc, sqlite3.Error):
            raise CaeSourceArtifactError("DATABASE_FAILURE") from None
        raise
    return source_ref, descriptor


def resolve_source_state(
    connection: sqlite3.Connection,
    source_state_ref: ArtifactRef | Mapping[str, Any],
    *,
    limits: ArtifactLimits = DEFAULT_LIMITS,
) -> tuple[SourceStateManifestV1, dict[str, bytes]]:
    """Verify descriptor, index, and every file object before returning bytes."""
    _require_initialized(connection)
    source_ref = _validated_ref(source_state_ref)
    descriptor_raw = _read_digest(connection, source_ref.sha256, limits.max_descriptor_bytes)
    descriptor_value = _decode_json(descriptor_raw)
    try:
        descriptor = SourceStateManifestV1.model_validate(descriptor_value)
    except (ValidationError, TypeError):
        raise CaeSourceArtifactError("INVALID_STORED_DESCRIPTOR") from None
    if _canonical_json(descriptor.model_dump(mode="json")) != descriptor_raw:
        raise CaeSourceArtifactError("INVALID_STORED_DESCRIPTOR")
    if len(descriptor.files) > limits.max_files:
        raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")

    index_ref = _validated_ref(descriptor.manifestArtifactRef)
    index_raw = _read_digest(connection, index_ref.sha256, limits.max_index_bytes)
    index_value = _decode_json(index_raw)
    if (not isinstance(index_value, dict) or set(index_value) != {"schemaVersion", "files"}
            or type(index_value.get("schemaVersion")) is not int or index_value["schemaVersion"] != 1
            or not isinstance(index_value.get("files"), list)):
        raise CaeSourceArtifactError("INVALID_SOURCE_INDEX")
    if _canonical_json(index_value) != index_raw:
        raise CaeSourceArtifactError("INVALID_SOURCE_INDEX")
    try:
        indexed_entries = [_entry(value) for value in index_value["files"]]
    except CaeSourceArtifactError:
        raise CaeSourceArtifactError("INVALID_SOURCE_INDEX") from None
    descriptor_entries = [entry.model_dump(mode="json") for entry in descriptor.files]
    normalized_index = [entry.model_dump(mode="json") for entry in indexed_entries]
    if normalized_index != descriptor_entries:
        raise CaeSourceArtifactError("SOURCE_INDEX_MISMATCH")

    total = 0
    for entry in descriptor.files:
        if entry.sizeBytes > limits.max_file_bytes or entry.sizeBytes > limits.max_source_bytes - total:
            raise CaeSourceArtifactError("SOURCE_LIMIT_EXCEEDED")
        total += entry.sizeBytes
    blobs: dict[str, bytes] = {}
    for entry in descriptor.files:
        raw = _read_digest(connection, entry.sha256, limits.max_file_bytes)
        if len(raw) != entry.sizeBytes:
            raise CaeSourceArtifactError("SOURCE_SIZE_MISMATCH")
        blobs[entry.path] = raw
    return descriptor, blobs
