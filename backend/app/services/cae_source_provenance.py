"""Pure parsers and policy checks for caller-provided CAE source metadata.

This module does not run Git, walk filesystems, or read source bytes. A future
trusted runner may pass raw ``git ls-files --cached --stage -v --sparse
--full-name -z`` output and already-owned file bytes. Index evidence establishes
only selected paths' index membership and metadata; it does not prove a clean
worktree, Git status, filter-normalized content, or source safety. The built-in
profile is intentionally immutable in v1; policy changes require trusted owner
maintenance and a new reviewed profile version. Source approval is not cloud
permission and metadata/path policy cannot prove content is secret-free.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Literal, Sequence


class SourceProvenanceError(ValueError):
    """Fixed-code parsing/policy failure; messages contain no source values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ApprovedSourceFileV1:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SourceIndexEntryV1:
    path: str
    size_bytes: int
    source_sha256: str
    origin: Literal["tracked", "untracked"]
    mode: str | None
    stage: int | None
    index_oid: str | None
    provenance_status: Literal["index-entry", "approved-index-absent"]


@dataclass(frozen=True)
class SourceIndexObservationV1:
    schema_version: int
    source_policy_profile_sha256: str
    object_format: Literal["sha1", "sha256"]
    head_oid: str
    raw_index_output_sha256: str
    entries: tuple[SourceIndexEntryV1, ...]

    def canonical_json(self) -> bytes:
        value = {
            "schemaVersion": self.schema_version,
            "sourcePolicyProfileSha256": self.source_policy_profile_sha256,
            "objectFormat": self.object_format,
            "headOid": self.head_oid,
            "rawIndexOutputSha256": self.raw_index_output_sha256,
            "entries": [
                {
                    "path": entry.path,
                    "sizeBytes": entry.size_bytes,
                    "sourceSha256": entry.source_sha256,
                    "origin": entry.origin,
                    "mode": entry.mode,
                    "stage": entry.stage,
                    "indexOid": entry.index_oid,
                    "provenanceStatus": entry.provenance_status,
                }
                for entry in self.entries
            ],
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")


_PROFILE_VALUE = {
    "schemaVersion": 1,
    "profileId": "onepath-source-selection-v1",
    "limits": {
        "maxFiles": 32,
        "maxFileBytes": 1_048_576,
        "maxTotalBytes": 4_194_304,
        "maxPathUtf8Bytes": 1024,
        "maxIndexOutputBytes": 1_048_576,
        "maxIndexRecords": 4096,
    },
    "allowedExtensions": sorted((
        ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".json", ".toml", ".yaml", ".yml", ".md", ".txt", ".css",
        ".html", ".sql", ".c", ".h", ".cpp", ".hpp", ".rs", ".go",
        ".sh", ".ps1",
    )),
    "allowedExactNames": ["license", "notice"],
    "deniedPathComponents": sorted((
        ".codex", ".git", ".hg", ".local", ".svn", ".venv", "backups",
        "build", "deps", "dist",
        "credentials", "data", "databases", "logs", "memory", "memories",
        "models", "node_modules", "obsidian", "private", "secrets", "venv",
        "target", "third_party", "vendor", "weights", "__pycache__",
    )),
    "deniedExactNames": sorted((
        "authorized_keys", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa",
        "known_hosts",
    )),
    "deniedNamePrefixes": ["credentials.", "secrets."],
}
BUILTIN_SOURCE_PROFILE_V1_JSON = json.dumps(
    _PROFILE_VALUE, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False, allow_nan=False,
).encode("utf-8")
BUILTIN_SOURCE_PROFILE_V1_SHA256 = hashlib.sha256(BUILTIN_SOURCE_PROFILE_V1_JSON).hexdigest()

MAX_FILES = 32
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 4_194_304
MAX_PATH_UTF8_BYTES = 1024
MAX_INDEX_OUTPUT_BYTES = 1_048_576
MAX_INDEX_RECORDS = 4096
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OID = re.compile(r"^[0-9a-f]+$")
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)
_ALLOWED_EXTENSIONS = frozenset(_PROFILE_VALUE["allowedExtensions"])
_ALLOWED_EXACT_NAMES = frozenset(_PROFILE_VALUE["allowedExactNames"])
_DENIED_COMPONENTS = frozenset(_PROFILE_VALUE["deniedPathComponents"])
_DENIED_EXACT_NAMES = frozenset(_PROFILE_VALUE["deniedExactNames"])
_DENIED_PREFIXES = tuple(_PROFILE_VALUE["deniedNamePrefixes"])


def _fail(code: str = "INVALID_SOURCE_SELECTION") -> None:
    raise SourceProvenanceError(code)


def _normalized_path(value: object, *, path_limit: int = MAX_PATH_UTF8_BYTES) -> str:
    if not isinstance(value, str) or not value:
        _fail()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        _fail()
    if (len(encoded) > path_limit or value.startswith("/") or "\\" in value
            or ":" in value or value.endswith("/")
            or unicodedata.normalize("NFC", value) != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail()
    parts = value.split("/")
    for component in parts:
        folded = component.casefold()
        if (not component or component in {".", ".."} or component.endswith((".", " "))
                or _WINDOWS_RESERVED.fullmatch(component) or component.startswith(".")
                or folded in _DENIED_COMPONENTS):
            _fail("SOURCE_PATH_DENIED")
    basename = parts[-1].casefold()
    if (basename in _DENIED_EXACT_NAMES
            or any(basename.startswith(prefix) for prefix in _DENIED_PREFIXES)):
        _fail("SOURCE_PATH_DENIED")
    if basename not in _ALLOWED_EXACT_NAMES:
        dot = basename.rfind(".")
        if dot < 0 or basename[dot:] not in _ALLOWED_EXTENSIONS:
            _fail("SOURCE_EXTENSION_DENIED")
    return value


def validate_approved_files(
    entries: Sequence[ApprovedSourceFileV1],
) -> tuple[ApprovedSourceFileV1, ...]:
    """Validate and canonically sort the exact user-selected source-file list."""
    if (not isinstance(entries, Sequence) or isinstance(entries, (str, bytes))
            or not entries or len(entries) > MAX_FILES):
        _fail("SOURCE_FILE_LIMIT")
    normalized: list[ApprovedSourceFileV1] = []
    total = 0
    for entry in tuple(entries):
        if not isinstance(entry, ApprovedSourceFileV1):
            _fail()
        path = _normalized_path(entry.path)
        if (type(entry.size_bytes) is not int or entry.size_bytes < 0
                or entry.size_bytes > MAX_FILE_BYTES
                or entry.size_bytes > MAX_TOTAL_BYTES - total
                or not isinstance(entry.sha256, str) or not _SHA256.fullmatch(entry.sha256)):
            _fail("SOURCE_SIZE_OR_HASH_INVALID")
        total += entry.size_bytes
        normalized.append(ApprovedSourceFileV1(path, entry.size_bytes, entry.sha256))

    trie: dict = {}
    marker = object()
    exact: set[str] = set()
    for entry in normalized:
        folded = entry.path.casefold()
        if folded in exact:
            _fail("SOURCE_PATH_COLLISION")
        exact.add(folded)
        node = trie
        for component in folded.split("/"):
            if marker in node:
                _fail("SOURCE_PATH_COLLISION")
            node = node.setdefault(component, {})
        if marker in node or node:
            _fail("SOURCE_PATH_COLLISION")
        node[marker] = True
    return tuple(sorted(normalized, key=lambda entry: entry.path))


def _validate_oid(value: object, object_format: str) -> str:
    if object_format not in {"sha1", "sha256"}:
        _fail("INVALID_OBJECT_FORMAT")
    size = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if not isinstance(value, str) or len(value) != size or not _OID.fullmatch(value):
        _fail("INVALID_GIT_OBJECT_ID")
    return value


def _index_path(value: str, *, sparse_directory: bool = False) -> str:
    """Validate raw index path framing without applying selected-file policy globally."""
    path = value[:-1] if sparse_directory and value.endswith("/") else value
    if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path
            or ":" in path or path.endswith("/")
            or unicodedata.normalize("NFC", path) != path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        _fail("INVALID_INDEX_PATH")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or part.endswith((".", " ")) for part in parts):
        _fail("INVALID_INDEX_PATH")
    return path


def _parse_index_record(record: bytes, object_format: str) -> tuple[str, str, str, int, str]:
    header, separator, raw_path = record.partition(b"\t")
    if not separator or b"\t" in raw_path:
        _fail("INVALID_INDEX_RECORD")
    fields = header.split(b" ")
    if len(fields) != 4 or len(fields[0]) != 1 or fields[0] not in {b"H", b"S", b"h", b"s"}:
        _fail("INVALID_INDEX_RECORD")
    if len(fields[1]) != 6 or any(byte not in b"01234567" for byte in fields[1]):
        _fail("INVALID_INDEX_RECORD")
    try:
        mode = fields[1].decode("ascii")
        oid = fields[2].decode("ascii")
        stage_text = fields[3].decode("ascii")
        tag = fields[0].decode("ascii")
        raw_path_text = raw_path.decode("utf-8", "strict")
    except UnicodeError:
        _fail("INVALID_INDEX_RECORD")
    expected_oid_length = 40 if object_format == "sha1" else 64
    if len(oid) != expected_oid_length or not _OID.fullmatch(oid) or stage_text not in {"0", "1", "2", "3"}:
        _fail("INVALID_INDEX_RECORD")
    if mode == "040000" and not raw_path_text.endswith("/"):
        _fail("INVALID_INDEX_RECORD")
    path = _index_path(raw_path_text, sparse_directory=(mode == "040000"))
    return tag, mode, oid, int(stage_text), path


def parse_cached_index(
    raw_index_output: bytes,
    approved_files: Sequence[ApprovedSourceFileV1],
    *,
    object_format: Literal["sha1", "sha256"],
    head_oid: str,
) -> SourceIndexObservationV1:
    """Parse bounded cached-index metadata and classify only explicit selections.

    The raw index output contains metadata only, never source bytes. Absence from
    this cache is recorded as approved-index-absent; this is not a whole-repo
    clean/dirty/status claim. A future runner must supply the exact documented
    ``ls-files --cached --stage -v --sparse --full-name -z`` bytes.
    """
    if not isinstance(raw_index_output, bytes) or len(raw_index_output) > MAX_INDEX_OUTPUT_BYTES:
        _fail("INDEX_OUTPUT_LIMIT")
    files = validate_approved_files(approved_files)
    if not isinstance(object_format, str) or object_format not in {"sha1", "sha256"}:
        _fail("INVALID_OBJECT_FORMAT")
    _validate_oid(head_oid, object_format)
    if raw_index_output:
        if not raw_index_output.endswith(b"\0"):
            _fail("INVALID_INDEX_FRAMING")
        records = raw_index_output[:-1].split(b"\0")
        if not records or any(not record for record in records):
            _fail("INVALID_INDEX_FRAMING")
    else:
        records = []
    if len(records) > MAX_INDEX_RECORDS:
        _fail("INDEX_RECORD_LIMIT")

    selected = {entry.path.casefold(): entry for entry in files}
    matches: dict[str, list[tuple[str, str, str, int, str]]] = {key: [] for key in selected}
    sparse_prefixes: list[str] = []
    for record in records:
        parsed = _parse_index_record(record, object_format)
        tag, mode, oid, stage, path = parsed
        folded = path.casefold()
        if mode == "040000":
            sparse_prefixes.append(folded)
        if folded in selected:
            if path != selected[folded].path:
                _fail("SOURCE_INDEX_CASE_COLLISION")
            matches[folded].append(parsed)

    for prefix in sparse_prefixes:
        if any(path.startswith(prefix + "/") for path in selected):
            _fail("SOURCE_INDEX_SPARSE_ANCESTOR")

    observations: list[SourceIndexEntryV1] = []
    for entry in files:
        folded = entry.path.casefold()
        found = matches[folded]
        if not found:
            observations.append(SourceIndexEntryV1(
                entry.path, entry.size_bytes, entry.sha256, "untracked", None, None,
                None, "approved-index-absent",
            ))
            continue
        if len(found) != 1:
            _fail("SOURCE_INDEX_DUPLICATE")
        tag, mode, oid, stage, path = found[0]
        if tag != "H" or stage != 0:
            _fail("SOURCE_INDEX_FLAGS_OR_STAGE")
        if mode not in {"100644", "100755"} or set(oid) == {"0"}:
            _fail("SOURCE_INDEX_MODE_UNSUPPORTED")
        observations.append(SourceIndexEntryV1(
            path, entry.size_bytes, entry.sha256, "tracked", mode, stage, oid,
            "index-entry",
        ))

    return SourceIndexObservationV1(
        1, BUILTIN_SOURCE_PROFILE_V1_SHA256, object_format, head_oid,
        hashlib.sha256(raw_index_output).hexdigest(),
        tuple(observations),
    )


def compute_git_blob_oid(content: bytes, object_format: Literal["sha1", "sha256"]) -> str:
    """Hash caller-owned raw bytes as a Git blob; filters and worktree status are omitted."""
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        _fail("SOURCE_SIZE_LIMIT")
    if not isinstance(object_format, str) or object_format not in {"sha1", "sha256"}:
        _fail("INVALID_OBJECT_FORMAT")
    digest = hashlib.new(object_format)
    digest.update(b"blob " + str(len(content)).encode("ascii") + b"\0")
    digest.update(content)
    return digest.hexdigest()
