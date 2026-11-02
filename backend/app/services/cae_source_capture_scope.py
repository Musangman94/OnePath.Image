"""Pure, inactive path-only contract for a bounded source capture request.

This module binds proposed scope metadata. It does not authorize a read, attest
the root identity, open paths, collect bytes, or issue an approval grant.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Mapping
import unicodedata

from app.services.cae_source_files import SourceReadLimits
from app.services.cae_source_provenance import (
    BUILTIN_SOURCE_PROFILE_V1_SHA256,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_UTF8_BYTES,
    MAX_TOTAL_BYTES,
    SourceProvenanceError,
    _normalized_path,
)
from app.storage.cae_source_approvals import RootIdentity, SourceApprovalError, _root_path


SCOPE_SCHEMA_VERSION = 1
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:\\")
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE,
)
_WINDOWS_INVALID = re.compile(r'[<>:"|?*]')
_POLICY_REF = {
    "id": "source-" + BUILTIN_SOURCE_PROFILE_V1_SHA256,
    "sha256": BUILTIN_SOURCE_PROFILE_V1_SHA256,
    "version": 1,
}


class SourceCaptureScopeError(ValueError):
    """Fixed-code contract validation failure; contains no supplied values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identity(value: object) -> RootIdentity:
    if not isinstance(value, RootIdentity):
        raise SourceCaptureScopeError("INVALID_ROOT_IDENTITY")
    platform, device, file_id = value.platform, value.device, value.file_id
    if (not isinstance(platform, str) or platform not in {"posix", "windows_ntfs"}
            or type(device) is not int or device < 0
            or type(file_id) is not int or file_id < 0):
        raise SourceCaptureScopeError("INVALID_ROOT_IDENTITY")
    return RootIdentity(platform, device, file_id)


def _root(value: object, platform: str) -> str:
    try:
        path = _root_path(value)
    except SourceApprovalError:
        raise SourceCaptureScopeError("INVALID_ROOT_PATH") from None
    if platform == "posix" and not path.startswith("/"):
        raise SourceCaptureScopeError("ROOT_PLATFORM_MISMATCH")
    if platform == "windows_ntfs":
        if not _DRIVE_ROOT.match(path):
            raise SourceCaptureScopeError("ROOT_PLATFORM_MISMATCH")
        tail = path[3:]
        if (unicodedata.normalize("NFC", path) != path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or any(not component or component in {".", ".."}
                       or component.endswith((".", " "))
                       or _WINDOWS_INVALID.search(component)
                       or _WINDOWS_RESERVED.fullmatch(component)
                       for component in tail.split("\\") if tail)):
            raise SourceCaptureScopeError("INVALID_ROOT_PATH")
    return path


def _limits(value: object) -> SourceReadLimits:
    if not isinstance(value, SourceReadLimits):
        raise SourceCaptureScopeError("INVALID_LIMITS")
    try:
        result = SourceReadLimits(value.max_files, value.max_file_bytes, value.max_total_bytes)
    except (AttributeError, TypeError, ValueError):
        raise SourceCaptureScopeError("INVALID_LIMITS") from None
    if (result.max_files > MAX_FILES or result.max_file_bytes > MAX_FILE_BYTES
            or result.max_total_bytes > MAX_TOTAL_BYTES
            or result.max_file_bytes > result.max_total_bytes):
        raise SourceCaptureScopeError("INVALID_LIMITS")
    return result


def _paths(value: object, limits: SourceReadLimits) -> tuple[str, ...]:
    if (type(value) not in (tuple, list) or not value
            or len(value) > limits.max_files):
        raise SourceCaptureScopeError("INVALID_PATHS")
    normalized: list[str] = []
    trie: dict = {}
    marker = object()
    try:
        for supplied in tuple(value):
            path = _normalized_path(supplied, path_limit=MAX_PATH_UTF8_BYTES)
            node = trie
            for component in path.casefold().split("/"):
                if marker in node:
                    raise SourceCaptureScopeError("PATH_COLLISION")
                node = node.setdefault(component, {})
            if marker in node or node:
                raise SourceCaptureScopeError("PATH_COLLISION")
            node[marker] = True
            normalized.append(path)
    except SourceProvenanceError as exc:
        raise SourceCaptureScopeError(exc.code) from None
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class SourceCaptureScopeV1:
    """Immutable canonical metadata binding a proposed bounded read scope.

    Path membership and policy validation are descriptive only. The root
    identity is caller-supplied metadata until a trusted reader revalidates it.
    """

    schema_version: int
    root_path: str
    root_identity: RootIdentity
    relative_paths: tuple[str, ...]
    limits: SourceReadLimits

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCOPE_SCHEMA_VERSION:
            raise SourceCaptureScopeError("UNSUPPORTED_SCOPE_VERSION")
        identity = _identity(self.root_identity)
        root = _root(self.root_path, identity.platform)
        limits = _limits(self.limits)
        paths = _paths(self.relative_paths, limits)
        object.__setattr__(self, "root_identity", identity)
        object.__setattr__(self, "root_path", root)
        object.__setattr__(self, "limits", limits)
        object.__setattr__(self, "relative_paths", paths)

    @property
    def policy_ref(self) -> Mapping[str, str | int]:
        """Return a detached copy of the one fixed built-in policy reference."""
        return dict(_POLICY_REF)

    def canonical_bytes(self) -> bytes:
        value = {
            "schemaVersion": self.schema_version,
            "root": {
                "absolutePath": self.root_path,
                "identity": {
                    "platform": self.root_identity.platform,
                    "device": self.root_identity.device,
                    "fileId": self.root_identity.file_id,
                },
            },
            "relativePaths": list(self.relative_paths),
            "policyRef": dict(_POLICY_REF),
            "limits": {
                "maxFiles": self.limits.max_files,
                "maxFileBytes": self.limits.max_file_bytes,
                "maxTotalBytes": self.limits.max_total_bytes,
            },
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    @property
    def sha256(self) -> str:
        import hashlib
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


__all__ = ["SourceCaptureScopeError", "SourceCaptureScopeV1", "SCOPE_SCHEMA_VERSION"]
