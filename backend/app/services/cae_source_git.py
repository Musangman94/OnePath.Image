"""Bounded metadata-only Git observations for a trusted source-selection runner.

This module is not a generic command executor. It launches only two fixed Git
metadata commands using an internally pinned absolute executable. It never
reads source files, runs status/diff, traverses a checkout, or claims a clean
worktree, atomic repository snapshot, descendant cleanup, or OS sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from typing import Callable, Literal, Sequence

from app.services.cae_source_provenance import (
    ApprovedSourceFileV1,
    SourceIndexObservationV1,
    parse_cached_index,
    validate_approved_files,
)
from app.services.cae_source_files import SourceFileReadError, identify_source_root
from app.storage.cae_source_approvals import RootIdentity


class GitObservationError(ValueError):
    """Redacted fixed-code failure for the bounded Git metadata runner."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class GitExecutionUncertain(GitObservationError):
    """The direct child or its output pipes could not be conclusively reaped."""


@dataclass(frozen=True)
class TrustedGitExecutable:
    path: Path
    expected_sha256: str
    expected_version_label: str


@dataclass(frozen=True)
class GitExecutableIdentityV1:
    canonical_path: str
    sha256: str
    expected_version_label: str


@dataclass(frozen=True)
class GitMetadataObservationV1:
    schema_version: int
    root_path: str
    root_identity: RootIdentity
    executable: GitExecutableIdentityV1
    object_format: Literal["sha1", "sha256"]
    head_oid: str
    root_output_sha256: str
    source_index: SourceIndexObservationV1

    def canonical_json(self) -> bytes:
        value = {
            "schemaVersion": self.schema_version,
            "root": {
                "path": self.root_path,
                "platform": self.root_identity.platform,
                "device": self.root_identity.device,
                "fileId": self.root_identity.file_id,
            },
            "gitExecutable": {
                "path": self.executable.canonical_path,
                "sha256": self.executable.sha256,
                "expectedVersionLabel": self.executable.expected_version_label,
            },
            "objectFormat": self.object_format,
            "headOid": self.head_oid,
            "rootOutputSha256": self.root_output_sha256,
            "sourceIndex": json.loads(self.source_index.canonical_json().decode("utf-8")),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")


MAX_STDOUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 8_192
MAX_GIT_EXE_BYTES = 128 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 5.0
CLEANUP_TIMEOUT_SECONDS = 2.0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]+$")
_REPARSE_POINT = 0x400


def _fail(code: str = "GIT_METADATA_INVALID") -> None:
    raise GitObservationError(code)


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT)


def _reject_linked_components(path: Path) -> None:
    try:
        for component in (path, *path.parents):
            info = component.lstat()
            if (stat.S_ISLNK(info.st_mode) or _is_reparse(info)
                    or (callable(getattr(component, "is_junction", None)) and component.is_junction())):
                _fail("LINKED_PATH_REJECTED")
    except GitObservationError:
        raise
    except (OSError, RuntimeError, ValueError):
        _fail("PATH_UNAVAILABLE")


def _canonical_root(value: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError:
        _fail("INVALID_ROOT")
    if (not isinstance(raw, str) or not raw or "\x00" in raw
            or any(ord(char) < 32 or ord(char) == 127 for char in raw)):
        _fail("INVALID_ROOT")
    path = Path(raw)
    if not path.is_absolute():
        _fail("INVALID_ROOT")
    _reject_linked_components(path)
    try:
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("INVALID_ROOT")
    if os.path.normcase(str(path)) != os.path.normcase(str(canonical)):
        _fail("NONCANONICAL_ROOT")
    try:
        info = canonical.stat(follow_symlinks=False)
    except OSError:
        _fail("INVALID_ROOT")
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        _fail("INVALID_ROOT")
    return canonical


def _verify_root_identity(root: Path, expected: RootIdentity) -> RootIdentity:
    if (not isinstance(expected, RootIdentity) or type(expected.device) is not int
            or type(expected.file_id) is not int or expected.device < 0 or expected.file_id < 0):
        _fail("INVALID_ROOT_IDENTITY")
    _reject_linked_components(root)
    try:
        observed = identify_source_root(root)
    except SourceFileReadError as exc:
        _fail(exc.code)
    except Exception:
        _fail("ROOT_IDENTITY_MISMATCH")
    if (observed.platform != expected.platform or observed.device != expected.device
            or observed.file_id != expected.file_id):
        _fail("ROOT_IDENTITY_MISMATCH")
    return expected


def _validate_executable_pin(pin: TrustedGitExecutable) -> tuple[Path, GitExecutableIdentityV1]:
    if not isinstance(pin, TrustedGitExecutable) or not isinstance(pin.path, Path):
        _fail("INVALID_GIT_PIN")
    if (not pin.path.is_absolute() or not isinstance(pin.expected_sha256, str)
            or not _SHA256.fullmatch(pin.expected_sha256)
            or not isinstance(pin.expected_version_label, str)
            or not pin.expected_version_label or len(pin.expected_version_label) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in pin.expected_version_label)):
        _fail("INVALID_GIT_PIN")
    _reject_linked_components(pin.path)
    try:
        canonical = pin.path.resolve(strict=True)
        before = canonical.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError):
        _fail("GIT_EXECUTABLE_UNAVAILABLE")
    if (os.path.normcase(str(pin.path)) != os.path.normcase(str(canonical))
            or not stat.S_ISREG(before.st_mode) or _is_reparse(before)
            or before.st_size <= 0 or before.st_size > MAX_GIT_EXE_BYTES):
        _fail("GIT_EXECUTABLE_REJECTED")
    if os.name == "nt" and canonical.suffix.casefold() != ".exe":
        _fail("GIT_EXECUTABLE_REJECTED")
    if os.name == "posix" and not os.access(canonical, os.X_OK):
        _fail("GIT_EXECUTABLE_REJECTED")
    try:
        digest = hashlib.sha256()
        with canonical.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
                _fail("GIT_EXECUTABLE_CHANGED")
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = canonical.stat(follow_symlinks=False)
        if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                or (current.st_dev, current.st_ino, current.st_size)
                != (before.st_dev, before.st_ino, before.st_size)):
            _fail("GIT_EXECUTABLE_CHANGED")
    except GitObservationError:
        raise
    except OSError:
        _fail("GIT_EXECUTABLE_UNAVAILABLE")
    actual = digest.hexdigest()
    if not hmac.compare_digest(actual, pin.expected_sha256):
        _fail("GIT_EXECUTABLE_HASH_MISMATCH")
    return canonical, GitExecutableIdentityV1(str(canonical), actual, pin.expected_version_label)


def _minimal_environment(executable: Path) -> dict[str, str]:
    env = {
        "PATH": str(executable.parent),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    return env


def _drain(stream, limit: int, output: bytearray, overflow: threading.Event, failures: list[bool]) -> None:
    try:
        while len(output) <= limit:
            chunk = stream.read(min(64 * 1024, limit + 1 - len(output)))
            if not chunk:
                return
            output.extend(chunk)
            if len(output) > limit:
                overflow.set()
                return
    except BaseException:
        failures.append(True)


def _run_bounded(
    process_factory: Callable,
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
) -> bytes:
    try:
        process = process_factory(
            argv, cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            close_fds=True, bufsize=0,
        )
    except Exception:
        _fail("GIT_EXECUTION_FAILED")
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    failures: list[bool] = []
    readers = (
        threading.Thread(target=_drain, args=(process.stdout, MAX_STDOUT_BYTES, stdout, overflow, failures), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, MAX_STDERR_BYTES, stderr, overflow, failures), daemon=True),
    )
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    stop_code: str | None = None
    return_code = None
    while True:
        if overflow.is_set():
            stop_code = "GIT_OUTPUT_LIMIT"
            break
        try:
            return_code = process.poll()
        except Exception:
            stop_code = "GIT_EXECUTION_UNCERTAIN"
            break
        if return_code is not None:
            break
        if time.monotonic() >= deadline:
            stop_code = "GIT_COMMAND_TIMEOUT"
            break
        time.sleep(0.01)

    cleanup_uncertain = False
    if stop_code is not None:
        try:
            process.terminate()
        except Exception:
            cleanup_uncertain = True
        try:
            process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
        except Exception:
            cleanup_uncertain = True
    else:
        try:
            return_code = process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
        except Exception:
            try:
                process.terminate()
                process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
            except Exception:
                cleanup_uncertain = True
            stop_code = "GIT_COMMAND_TIMEOUT"

    join_deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
    for reader in readers:
        reader.join(max(0.0, join_deadline - time.monotonic()))
    pipes_uncertain = any(reader.is_alive() for reader in readers) or bool(failures)
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except Exception:
            pipes_uncertain = True
    if cleanup_uncertain or pipes_uncertain:
        raise GitExecutionUncertain("GIT_EXECUTION_UNCERTAIN")
    if overflow.is_set() and stop_code is None:
        stop_code = "GIT_OUTPUT_LIMIT"
    if stop_code == "GIT_EXECUTION_UNCERTAIN":
        raise GitExecutionUncertain("GIT_EXECUTION_UNCERTAIN")
    if stop_code is not None:
        _fail(stop_code)
    if return_code != 0:
        _fail("GIT_COMMAND_FAILED")
    return bytes(stdout)


def _parse_root_observation(raw: bytes, expected_root: Path) -> tuple[str, str]:
    if not isinstance(raw, bytes) or len(raw) > 8192 or not raw.endswith(b"\n"):
        _fail("GIT_ROOT_OUTPUT_INVALID")
    fields = raw.split(b"\n")
    if len(fields) != 4 or fields[-1] != b"":
        _fail("GIT_ROOT_OUTPUT_INVALID")
    try:
        reported = fields[0].decode("utf-8", "strict")
        object_format = fields[1].decode("ascii")
        head_oid = fields[2].decode("ascii")
    except UnicodeError:
        _fail("GIT_ROOT_OUTPUT_INVALID")
    if (not reported or any(ord(char) < 32 or ord(char) == 127 for char in reported)
            or object_format not in {"sha1", "sha256"}):
        _fail("GIT_ROOT_OUTPUT_INVALID")
    reported_root = _canonical_root(reported)
    if os.path.normcase(str(reported_root)) != os.path.normcase(str(expected_root)):
        _fail("GIT_ROOT_MISMATCH")
    oid_length = 40 if object_format == "sha1" else 64
    if len(head_oid) != oid_length or not _HEX.fullmatch(head_oid):
        _fail("GIT_HEAD_INVALID")
    return object_format, head_oid


def _git_directory_identity(root: Path) -> tuple[str, int, int]:
    git_dir = root / ".git"
    try:
        info = git_dir.lstat()
    except OSError:
        _fail("GIT_DIRECTORY_UNAVAILABLE")
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        _fail("GIT_DIRECTORY_INVALID")
    _reject_linked_components(git_dir)
    try:
        identity = identify_source_root(git_dir)
    except SourceFileReadError:
        _fail("GIT_DIRECTORY_INVALID")
    except Exception:
        _fail("GIT_DIRECTORY_INVALID")
    return identity.platform, identity.device, identity.file_id


def _verify_git_directory_identity(root: Path, expected: tuple[str, int, int]) -> None:
    if _git_directory_identity(root) != expected:
        _fail("GIT_DIRECTORY_CHANGED")


class GitMetadataRunner:
    """Run only pinned Git root/index metadata commands for a selected file list."""

    def __init__(self, pin: TrustedGitExecutable, *, _process_factory: Callable | None = None):
        self.pin = pin
        self._process_factory = subprocess.Popen if _process_factory is None else _process_factory

    def observe(
        self,
        root: str | os.PathLike[str],
        expected_root_identity: RootIdentity,
        approved_files: Sequence[ApprovedSourceFileV1],
    ) -> GitMetadataObservationV1:
        files = validate_approved_files(approved_files)
        canonical_root = _canonical_root(root)
        identity = _verify_root_identity(canonical_root, expected_root_identity)
        git_directory_identity = _git_directory_identity(canonical_root)
        executable, executable_identity = _validate_executable_pin(self.pin)
        env = _minimal_environment(executable)
        prefix = (
            str(executable), "--no-pager", "--no-optional-locks",
            "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
            "--git-dir=" + str(canonical_root / ".git"),
            "--work-tree=" + str(canonical_root), "--literal-pathspecs",
        )
        root_command = prefix + ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD")
        index_command = prefix + ("ls-files", "--cached", "--stage", "-v", "--sparse", "--full-name", "-z")

        _verify_root_identity(canonical_root, identity)
        _verify_git_directory_identity(canonical_root, git_directory_identity)
        current_executable, current_identity = _validate_executable_pin(self.pin)
        if current_executable != executable or current_identity != executable_identity:
            _fail("GIT_EXECUTABLE_CHANGED")
        root_output = _run_bounded(self._process_factory, root_command, cwd=executable.parent, env=env)
        _verify_git_directory_identity(canonical_root, git_directory_identity)
        _verify_root_identity(canonical_root, identity)
        object_format, head_oid = _parse_root_observation(root_output, canonical_root)

        _verify_root_identity(canonical_root, identity)
        _verify_git_directory_identity(canonical_root, git_directory_identity)
        current_executable, current_identity = _validate_executable_pin(self.pin)
        if current_executable != executable or current_identity != executable_identity:
            _fail("GIT_EXECUTABLE_CHANGED")
        index_output = _run_bounded(self._process_factory, index_command, cwd=executable.parent, env=env)
        _verify_git_directory_identity(canonical_root, git_directory_identity)
        _verify_root_identity(canonical_root, identity)
        source_index = parse_cached_index(
            index_output, files, object_format=object_format, head_oid=head_oid,
        )
        _verify_root_identity(canonical_root, identity)
        _verify_git_directory_identity(canonical_root, git_directory_identity)
        current_executable, current_identity = _validate_executable_pin(self.pin)
        if current_executable != executable or current_identity != executable_identity:
            _fail("GIT_EXECUTABLE_CHANGED")
        return GitMetadataObservationV1(
            1, str(canonical_root), identity, executable_identity, object_format,
            head_oid, hashlib.sha256(root_output).hexdigest(), source_index,
        )
