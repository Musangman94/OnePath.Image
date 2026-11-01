"""Prepare or execute a frozen, narrowly scoped native Git metadata probe.

`--plan` is process-free. `--execute` accepts only a byte-matching plan and a
separate explicit approval flag; it reconstructs all commands locally and
never treats the plan as executable input. This is not a generic Git runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import shlex
import stat
import subprocess
import sys
import time

from app.services.cae_source_git import (
    CLEANUP_TIMEOUT_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    GitExecutionUncertain,
    GitObservationError,
    GitMetadataRunner,
    TrustedGitExecutable,
    _parse_root_observation,
    _run_bounded,
    _validate_executable_pin,
    _minimal_environment,
)
from app.services.cae_source_provenance import ApprovedSourceFileV1, validate_approved_files
from app.services.cae_source_files import identify_source_root
from app.storage.cae_source_approvals import RootIdentity


WORKSPACE = Path(__file__).resolve().parents[2]
GIT_PATH = Path(r"C:\Program Files\Git\cmd\git.exe")
EXPECTED_RUNNER_SHA256 = "e9888e6c312e692cf8457e8c9344587f6d6a5d00f394ad2368708d786cf24c62"
EXPECTED_TEST_SHA256 = "61c0e64d845a1896659ba6d9c7b48b43ab09e7931421626e076c9400b482083b"
FROZEN_DEPENDENCIES = {
    "reader": "backend/app/services/cae_source_files.py",
    "provenance": "backend/app/services/cae_source_provenance.py",
    "ledger": "backend/app/storage/cae_source_approvals.py",
    "planTest": "backend/tests/test_cae_source_git_native_plan.py",
}
EXPECTED_GIT_VERSION_LABEL = "pending-native-version-check"
PLAN_SCHEMA = "cae-source-git-native-probe-plan-v2"
MAX_PLAN_BYTES = 128 * 1024
MAX_CHILDREN = 16
TOTAL_SECONDS = 600
DIAGNOSTIC_STDERR_CAP_BYTES = 8 * 1024


def _digest_file(path: Path, cap: int) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > cap:
        raise ValueError("INPUT_FILE_REJECTED")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ValueError("INPUT_FILE_CHANGED")
        while chunk := source.read(64 * 1024):
            total += len(chunk)
            if total > cap:
                raise ValueError("INPUT_FILE_LIMIT")
            digest.update(chunk)
        after = os.fstat(source.fileno())
    current = path.lstat()
    if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            or (current.st_dev, current.st_ino, current.st_size)
            != (info.st_dev, info.st_ino, info.st_size)):
        raise ValueError("INPUT_FILE_CHANGED")
    return digest.hexdigest()


def _reject_reparse_ancestors(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        attrs = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attrs & 0x400:
            raise ValueError("LINKED_PATH_REJECTED")


def _prepare_owned_run_paths(anchor: Path, run_id: str) -> tuple[Path, Path, RootIdentity]:
    """Create only fixtures/templates directly below an existing trusted anchor."""
    if (not anchor.is_absolute() or not isinstance(run_id, str) or len(run_id) != 32
            or any(char not in "0123456789abcdef" for char in run_id)):
        raise ValueError("OWNED_PATH_INPUT_INVALID")
    _reject_reparse_ancestors(anchor)
    try:
        anchor_info = anchor.lstat()
    except OSError:
        raise ValueError("VERIFICATION_ANCHOR_UNAVAILABLE") from None
    if not stat.S_ISDIR(anchor_info.st_mode) or getattr(anchor_info, "st_file_attributes", 0) & 0x400:
        raise ValueError("VERIFICATION_ANCHOR_INVALID")
    canonical_anchor = anchor.resolve(strict=True)
    if canonical_anchor != anchor:
        raise ValueError("VERIFICATION_ANCHOR_NONCANONICAL")
    observed_anchor = identify_source_root(anchor)
    anchor_identity = RootIdentity(observed_anchor.platform, observed_anchor.device, observed_anchor.file_id)
    containers: dict[str, tuple[Path, tuple[str, int, int]]] = {}
    for name in ("fixtures", "templates"):
        path = anchor / name
        try:
            path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir()
            except OSError:
                raise ValueError("FIXTURE_CONTAINER_CREATE_FAILED") from None
        except OSError:
            raise ValueError("FIXTURE_CONTAINER_UNAVAILABLE") from None
        _reject_reparse_ancestors(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("FIXTURE_CONTAINER_INVALID")
        if path.resolve(strict=True) != path:
            raise ValueError("FIXTURE_CONTAINER_NONCANONICAL")
        observed = identify_source_root(path)
        container_identity = (observed.platform, observed.device, observed.file_id)
        current_anchor = identify_source_root(anchor)
        if (current_anchor.platform, current_anchor.device, current_anchor.file_id) != (
                anchor_identity.platform, anchor_identity.device, anchor_identity.file_id):
            raise ValueError("VERIFICATION_ANCHOR_CHANGED")
        containers[name] = (path, container_identity)
    fixture_parent = containers["fixtures"][0]
    template_parent = containers["templates"][0]
    fixture = fixture_parent / ("cae-git-probe-" + run_id)
    template = template_parent / run_id
    for path in (fixture, template):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError("RUNPATH_UNAVAILABLE") from None
        raise ValueError("RUNPATH_ALREADY_EXISTS")
    # Recheck container identities immediately before returning their exclusive child paths.
    for name, (path, expected) in containers.items():
        observed = identify_source_root(path)
        if (observed.platform, observed.device, observed.file_id) != expected:
            raise ValueError("FIXTURE_CONTAINER_CHANGED")
    return fixture, template, anchor_identity


def _exe_hash() -> str:
    _reject_reparse_ancestors(GIT_PATH)
    canonical = GIT_PATH.resolve(strict=True)
    before = canonical.stat(follow_symlinks=False)
    if (canonical != GIT_PATH or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0 or before.st_size > 128 * 1024 * 1024):
        raise ValueError("GIT_EXECUTABLE_REJECTED")
    return _digest_file(canonical, 128 * 1024 * 1024)


def _interpreter_image(path_value: str) -> dict[str, str]:
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("PYTHON_EXECUTABLE_PATH_INVALID")
    _reject_reparse_ancestors(path)
    try:
        image = path.resolve(strict=True)
    except OSError:
        raise ValueError("PYTHON_EXECUTABLE_MISSING") from None
    if not stat.S_ISREG(image.stat(follow_symlinks=False).st_mode):
        raise ValueError("PYTHON_EXECUTABLE_REJECTED")
    return {"path": str(path), "imagePath": str(image),
            "sha256": _digest_file(image, 256 * 1024 * 1024)}


def _python_identity(executable: str | None = None,
                     base_executable: str | None = None) -> dict[str, object]:
    launch_path = sys.executable if executable is None else executable
    base_path = getattr(sys, "_base_executable", None) if base_executable is None else base_executable
    launch = _interpreter_image(launch_path)
    base = None
    if isinstance(base_path, str) and base_path:
        candidate = Path(base_path)
        if not candidate.is_absolute() or os.path.normcase(str(candidate)) != os.path.normcase(launch_path):
            base = _interpreter_image(base_path)
    return {"launch": launch, "base": base}


def _launch_checked_python(expected: dict[str, object], dispatch, *args, **kwargs):
    if _python_identity() != expected:
        raise ValueError("PYTHON_PIN_CHANGED")
    return dispatch(*args, **kwargs)


def _commands(root: str, template: str, commits: tuple[str, str], blob_oid: str,
              marker: str) -> list[dict[str, object]]:
    """Return the finite argv list. Only validated stdout-derived OIDs remain placeholders."""
    exe = str(GIT_PATH)
    prefix = [exe, "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
              "-c", "core.untrackedCache=false", "--git-dir=" + root + os.sep + ".git",
              "--work-tree=" + root, "--literal-pathspecs"]
    identity = ["-c", "user.name=CAE native probe", "-c", "user.email=cae-probe@localhost"]

    def item(label: str, argv: list[str]) -> dict[str, object]:
        return {"label": label, "argv": argv, "cwd": str(GIT_PATH.parent),
                "stdin": "DEVNULL", "shell": False, "timeoutSeconds": COMMAND_TIMEOUT_SECONDS,
                "stdoutLimitBytes": MAX_STDOUT_BYTES, "stderrLimitBytes": MAX_STDERR_BYTES,
                "cleanupSeconds": CLEANUP_TIMEOUT_SECONDS}

    canary_script = str(Path(marker).parent / ".cae-probe-canary.py")
    positive_argv = _positive_canary_argv(prefix, sys.executable, canary_script)
    root_observe = prefix + ["rev-parse", "--show-toplevel", "--show-object-format", "HEAD"]
    index_observe = prefix + ["ls-files", "--cached", "--stage", "-v", "--sparse", "--full-name", "-z"]
    return [
        item("git-version", [exe, "--version"]),
        item("init-empty-template", prefix + ["init", "--quiet", "--template=" + template, "--initial-branch=main"]),
        item("add-tracked-source", prefix + identity + ["add", "--", "tracked.py"]),
        item("initial-commit", prefix + identity + ["commit", "--quiet", "-m", "probe initial"]),
        item("initial-head", prefix + ["rev-parse", "HEAD"]),
        item("second-empty-commit", prefix + identity + ["commit", "--quiet", "--allow-empty", "-m", "probe second"]),
        item("second-head", prefix + ["rev-parse", "HEAD"]),
        item("canary-positive-control", positive_argv),
        item("observe-before-root", root_observe),
        item("observe-before-index", index_observe),
        item("change-index-to-known-blob", _index_change_argv(prefix, blob_oid)),
        item("move-head-back-with-cas", prefix + ["update-ref", "refs/heads/main", commits[0], commits[1]]),
        item("observe-after-root", root_observe),
        item("observe-after-index", index_observe),
    ]


def build_plan(*, run_id: str | None = None, created_utc: str | None = None) -> dict[str, object]:
    if not WORKSPACE.is_absolute():
        raise ValueError("WORKSPACE_INVALID")
    runner_path = WORKSPACE / "backend/app/services/cae_source_git.py"
    test_path = WORKSPACE / "backend/tests/test_cae_source_git.py"
    harness_hash = _digest_file(Path(__file__), 1024 * 1024)
    runner_hash = _digest_file(runner_path, 1024 * 1024)
    test_hash = _digest_file(test_path, 1024 * 1024)
    dependency_hashes = {name: _digest_file(WORKSPACE / rel, 1024 * 1024)
                         for name, rel in FROZEN_DEPENDENCIES.items()}
    if runner_hash != EXPECTED_RUNNER_SHA256 or test_hash != EXPECTED_TEST_SHA256:
        raise ValueError("FROZEN_INPUT_HASH_MISMATCH")
    git_hash = _exe_hash()
    python_identity = _python_identity()
    run_id = secrets.token_hex(16) if run_id is None else run_id
    if (not isinstance(run_id, str) or len(run_id) != 32
            or any(char not in "0123456789abcdef" for char in run_id)):
        raise ValueError("RUN_ID_INVALID")
    fixture = WORKSPACE / ".local/verification/fixtures" / ("cae-git-probe-" + run_id)
    template = WORKSPACE / ".local/verification/templates" / run_id
    # Plan generation validates the parent chain but never creates the fixture.
    for path in (WORKSPACE, WORKSPACE / ".local", WORKSPACE / ".local/verification"):
        _reject_reparse_ancestors(path)
    approved = {
        "tracked.py": b"VALUE = 'original'\n",
        "untracked.py": b"UNTRACKED = True\n",
    }
    tracked_blob = b"blob " + str(len(approved["tracked.py"])).encode("ascii") + b"\0" + approved["tracked.py"]
    files = [ApprovedSourceFileV1(path, len(data), hashlib.sha256(data).hexdigest())
             for path, data in approved.items()]
    pin = TrustedGitExecutable(GIT_PATH, git_hash, EXPECTED_GIT_VERSION_LABEL)
    env = _minimal_environment(GIT_PATH)
    env_keys = sorted(env)
    commands = _commands(str(fixture), str(template), ("<commit-1-oid>", "<commit-2-oid>"),
                         "<local-computed-blob-oid>", str(fixture / ".cae-probe-negative"))
    if len(commands) > MAX_CHILDREN:
        raise ValueError("CHILD_COUNT_LIMIT")
    # Runner/API details are captured for review; constructing it causes no process launch.
    runner = GitMetadataRunner(pin, _process_factory=lambda *args, **kwargs: None)
    del runner
    return {
        "schema": PLAN_SCHEMA,
        "runId": run_id,
        "status": "PREPARED_NATIVE_PROBE_PLAN",
        "createdUtc": created_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workspace": str(WORKSPACE),
        "fixture": str(fixture),
        "template": str(template),
        "sourceHashes": {"harness": harness_hash, "runner": runner_hash,
                          "fakeTest": test_hash, **dependency_hashes},
        "gitExecutable": {"path": str(GIT_PATH), "sha256": git_hash,
                          "versionLabel": EXPECTED_GIT_VERSION_LABEL,
                          "versionObservation": "PENDING_EXECUTE_GATE"},
        "pythonExecutable": python_identity,
        "pythonPinRule": "The external launcher must hash and compare the launch and optional base interpreter images before starting this harness; the running harness cannot authenticate its already-running interpreter. Recheck both identities before fixture staging and immediately before every native child launch. Protected host runtime remains a precondition.",
        "rootIdentityAuthority": "identify_source_root on the exclusively created fixture before every observation and cleanup",
        "approvedFixtureFiles": [{"path": f.path, "bytes": f.size_bytes, "sha256": f.sha256}
                                  for f in files],
        "environment": {"source": "cae_source_git._minimal_environment", "keys": env_keys,
                        "values": env},
        "commands": commands,
        "fixtureActions": ["reject existing/reparse fixture path and exclusively create fresh fixture/template directories",
                           "bind root identity and .git directory identity; verify .git identity before each observation and after each mutation",
                           "write ownership nonce in the scoped fixture; ownership is exclusive creation plus root identity, not an ACL",
                           "derive root identity only via identify_source_root on owned fixture and recheck before every action/observation",
                           "write only two known source fixture files",
                           "install canary filter/process/fsmonitor/pager entries only in fixture .git/config",
                           "positive and negative canary markers are created exclusively and retained; never delete marker or fixture",
                           "verify negative marker absence after every Git setup/mutation/observation; retain fixture evidence"],
        "canary": {"writerSource": _canary_writer_source(),
                   "configuredKeys": ["filter.probe.clean", "filter.probe.process", "core.fsmonitor", "core.pager"],
                   "commandTemplate": [sys.executable, str(fixture / ".cae-probe-canary.py"), "--positive"],
                   "scriptPath": str(fixture / ".cae-probe-canary.py"),
                   "positiveMarkerPath": str(fixture / ".cae-probe-positive"),
                   "negativeMarkerPath": str(fixture / ".cae-probe-negative"),
                   "positiveAliasArgv": commands[7]["argv"],
                   "positiveAliasName": "onepath-probe-canary",
                   "positiveWriterMode": "--positive",
                   "positiveControlExit": 0,
                   "positiveControlClaim": "Git shell-alias-to-writer invocation only; does not test fsmonitor protocol activation",
                   "blobOids": {"sha1": hashlib.sha1(tracked_blob).hexdigest(),
                                "sha256": hashlib.sha256(tracked_blob).hexdigest()}},
        "limits": {"maxChildren": MAX_CHILDREN, "plannedChildren": len(commands),
                   "wallClockSeconds": TOTAL_SECONDS, "stdoutBytes": MAX_STDOUT_BYTES,
                   "stderrBytes": MAX_STDERR_BYTES, "perCommandSeconds": COMMAND_TIMEOUT_SECONDS,
                   "cleanupSeconds": CLEANUP_TIMEOUT_SECONDS, "recursiveCleanup": False,
                   "descendantContainmentClaim": False},
        "binaryPinRule": "rehash and recheck regular-file identity immediately before every Git invocation; path/hash pin is not an immutable execution anchor",
        "probeAssertions": ["exact root and root identity", "sha1/sha256 object format and HEAD",
                            "selected tracked entry and approved index-absent file",
                            "index metadata hash changes after known-blob cacheinfo update",
                            "HEAD changes after compare-and-swap ref update", "positive marker retained and negative marker absent throughout Git observations"],
        "limitations": ["plan mode launched zero child processes", "native Git behavior and actual version are unverified",
                        "does not establish filesystem-wide atomicity or descendant cleanup",
                        "metadata and path policy cannot establish source content is secret-free"],
        "executeGate": "requires exact plan SHA-256 plus explicit --gandalf-authorized flag; separate review/authorization remains required",
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _quote_git_config_value(value: str) -> str:
    """Encode one Git config string using Git's quoted C-style escapes."""
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("GIT_CONFIG_VALUE_INVALID")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _git_shell_command(argv: tuple[str, ...]) -> str:
    if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError("GIT_SHELL_COMMAND_INVALID")
    forward = tuple(arg.replace("\\", "/") if re.match(r"^[A-Za-z]:\\", arg) else arg for arg in argv)
    command = shlex.join(forward)
    if tuple(shlex.split(command, posix=True)) != forward:
        raise ValueError("GIT_SHELL_COMMAND_INVALID")
    return command


def _positive_canary_argv(prefix: list[str] | tuple[str, ...],
                          python_executable: str, canary_script: str) -> list[str]:
    command = _git_shell_command((python_executable, canary_script, "--positive"))
    return [*prefix, "-c", "alias.onepath-probe-canary=!" + command,
            "onepath-probe-canary"]


def _index_change_argv(prefix: list[str] | tuple[str, ...], blob_oid: str) -> list[str]:
    return [*prefix, "update-index", "--add", "--cacheinfo", "100644", blob_oid,
            "probe-shadow.py"]


def _tracked_blob_oid(object_format: str) -> str:
    content = b"VALUE = 'original'\n"
    framed = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    if object_format == "sha1":
        return hashlib.sha1(framed).hexdigest()
    if object_format == "sha256":
        return hashlib.sha256(framed).hexdigest()
    raise ValueError("OBJECT_FORMAT_INVALID")


def _canary_writer_source() -> str:
    return (
        "import os,pathlib,sys\n"
        "mode=sys.argv[1]\n"
        "if mode not in ('--positive','--negative'):\n raise SystemExit(74)\n"
        "name='.cae-probe-positive' if mode=='--positive' else '.cae-probe-negative'\n"
        "p=pathlib.Path(__file__).parent/name\n"
        "fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)\n"
        "raise SystemExit(73 if mode=='--negative' else 0)\n"
    )


def _write_exclusive(path: Path, data: bytes) -> None:
    _reject_reparse_ancestors(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _owned_identity(root: Path, run_id: str, expected: RootIdentity | None = None) -> RootIdentity:
    expected_root = WORKSPACE / ".local/verification/fixtures" / ("cae-git-probe-" + run_id)
    if root != expected_root:
        raise ValueError("FIXTURE_PATH_MISMATCH")
    _reject_reparse_ancestors(root)
    canonical = root.resolve(strict=True)
    if canonical != root or not root.is_dir():
        raise ValueError("FIXTURE_PATH_REJECTED")
    nonce = root / ".cae-probe-owner"
    nonce_info = nonce.lstat()
    if (not stat.S_ISREG(nonce_info.st_mode) or getattr(nonce_info, "st_file_attributes", 0) & 0x400):
        raise ValueError("FIXTURE_OWNERSHIP_MISMATCH")
    observed = identify_source_root(root)
    actual = RootIdentity(observed.platform, observed.device, observed.file_id)
    if expected is not None and actual != expected:
        raise ValueError("FIXTURE_IDENTITY_CHANGED")
    return actual


def _git_directory_identity(root: Path, run_id: str, root_identity: RootIdentity) -> tuple[str, int, int]:
    _owned_identity(root, run_id, root_identity)
    git_dir = root / ".git"
    _reject_reparse_ancestors(git_dir)
    info = git_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("GIT_DIRECTORY_REJECTED")
    if callable(getattr(git_dir, "is_junction", None)) and git_dir.is_junction():
        raise ValueError("GIT_DIRECTORY_REJECTED")
    observed = identify_source_root(git_dir)
    return observed.platform, observed.device, observed.file_id


def _verify_git_directory(root: Path, run_id: str, root_identity: RootIdentity,
                          expected: tuple[str, int, int]) -> None:
    if _git_directory_identity(root, run_id, root_identity) != expected:
        raise ValueError("GIT_DIRECTORY_IDENTITY_CHANGED")


def _verify_marker(root: Path, run_id: str, root_identity: RootIdentity,
                   marker: Path, *, expected_present: bool) -> None:
    _owned_identity(root, run_id, root_identity)
    if marker.parent != root:
        raise ValueError("MARKER_PATH_REJECTED")
    try:
        info = marker.lstat()
    except FileNotFoundError:
        if expected_present:
            raise ValueError("POSITIVE_MARKER_MISSING") from None
        return
    if not expected_present:
        raise ValueError("NEGATIVE_MARKER_PRESENT")
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("POSITIVE_MARKER_INVALID")


def _validate_plan_payload(raw: bytes, expected_sha256: str) -> tuple[dict[str, object], dict[str, object]]:
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        raise ValueError("PLAN_HASH_INVALID")
    if len(raw) > MAX_PLAN_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("PLAN_HASH_MISMATCH")
    plan = json.loads(raw.decode("utf-8", "strict"))
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("PLAN_SCHEMA_REJECTED")
    # Reconstruct all content from local constants and current source identities.
    reconstructed = build_plan(run_id=plan.get("runId"), created_utc=plan.get("createdUtc"))
    if _canonical_json(plan) != _canonical_json(reconstructed) or raw != _canonical_json(reconstructed):
        raise ValueError("PLAN_RECONSTRUCTION_MISMATCH")
    return plan, reconstructed


def _load_approved_plan(path: Path, expected_sha256: str) -> tuple[dict[str, object], dict[str, object]]:
    if (not path.is_absolute() or path.parent != WORKSPACE / ".local/verification"
            or not path.name.startswith("cae-source-git-plan-") or path.suffix != ".json"):
        raise ValueError("PLAN_PATH_REJECTED")
    _reject_reparse_ancestors(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PLAN_BYTES:
        raise ValueError("PLAN_FILE_REJECTED")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ValueError("PLAN_FILE_CHANGED")
        raw = stream.read(MAX_PLAN_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
        raise ValueError("PLAN_FILE_CHANGED")
    plan, reconstructed = _validate_plan_payload(raw, expected_sha256)
    if path.name != "cae-source-git-plan-" + str(plan.get("runId")) + ".json":
        raise ValueError("PLAN_PATH_MISMATCH")
    return plan, reconstructed


def _safe_exception_location(exc: BaseException | None) -> tuple[str | None, str | None, int | None]:
    if exc is None:
        return None, None, None
    traceback = exc.__traceback__
    if traceback is None:
        return type(exc).__name__, None, None
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    return type(exc).__name__, traceback.tb_frame.f_code.co_name, traceback.tb_lineno


def _redact_diagnostic_text(value: str, root: Path, template: Path) -> str:
    paths = ((str(root / ".git"), "<fixture-gitdir>"), (str(root), "<fixture>"),
             (str(template), "<template>"), (str(GIT_PATH), "<git-executable>"),
             (str(Path(sys.executable)), "<python-executable>"), (str(WORKSPACE), "<workspace>"))
    variants: list[tuple[str, str]] = []
    for path, label in paths:
        for variant in (path, path.replace("\\", "/"), path.replace("/", "\\")):
            if variant and all(existing != variant for existing, _ in variants):
                variants.append((variant, label))
    for path, label in sorted(variants, key=lambda item: len(item[0]), reverse=True):
        value = re.sub(re.escape(path), label, value, flags=re.IGNORECASE)
    safe = "".join(char if char in "\r\n\t" or ord(char) >= 32 else "<CTRL>" for char in value)
    return safe[:DIAGNOSTIC_STDERR_CAP_BYTES]


class _DiagnosticStderrTee:
    """Retain a bounded stderr prefix while forwarding every read to the runner."""

    def __init__(self, inner):
        self.inner = inner
        self.data = bytearray()
        self.observed_bytes = 0
        self.overflow = False
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        chunk = self.inner.read(size)
        if not chunk:
            return chunk
        self.observed_bytes += len(chunk)
        room = max(0, DIAGNOSTIC_STDERR_CAP_BYTES - len(self.data))
        if len(chunk) > room:
            self.overflow = True
        self.data.extend(chunk[:room])
        return chunk

    def close(self) -> None:
        self.closed = True
        self.inner.close()


class _DiagnosticProcess:
    """Expose the Popen interface while recording the exit code for one child."""

    def __init__(self, inner, record: dict[str, object], tee: _DiagnosticStderrTee):
        self.inner = inner
        self.record = record
        self.stdout = inner.stdout
        self.stderr = tee

    def poll(self):
        result = self.inner.poll()
        if result is not None:
            self.record["exitCode"] = result
        return result

    def wait(self, timeout=None):
        result = self.inner.wait(timeout=timeout)
        self.record["exitCode"] = result
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _diagnostic_stderr_metadata(tee: _DiagnosticStderrTee | None, root: Path,
                                template: Path) -> dict[str, object]:
    data = bytes(tee.data) if tee is not None else b""
    return {
        "stderr": _redact_diagnostic_text(data.decode("utf-8", "replace"), root, template),
        "stderrSha256": hashlib.sha256(data).hexdigest(),
        "stderrBytesCaptured": len(data),
        "stderrBytesObserved": tee.observed_bytes if tee is not None else 0,
        "stderrOverflow": tee.overflow if tee is not None else False,
        "stderrPipeClosed": tee.closed if tee is not None else False,
    }


def _execute_plan(plan: dict[str, object], plan_sha256: str) -> dict[str, object]:
    run_id = plan["runId"]
    root = WORKSPACE / ".local/verification/fixtures" / ("cae-git-probe-" + run_id)
    template = WORKSPACE / ".local/verification/templates" / run_id
    report_path = WORKSPACE / ".local/verification" / ("cae-source-git-result-" + run_id + ".json")
    start = time.monotonic()
    pids: list[int] = []
    confirmed_cleanup_pids: list[int] = []
    completed_labels: list[str] = []
    child_diagnostics: list[dict[str, object]] = []
    root_identity: RootIdentity | None = None
    child_count = 0
    launch_attempt_count = 0
    phase = "preflight"
    anchor_identity: RootIdentity | None = None
    failure_exception_class: str | None = None
    failure_function: str | None = None
    failure_line: int | None = None
    status = "PARTIAL"
    failure_code: str | None = None
    observations: dict[str, object] = {}
    git_hash = plan["gitExecutable"]["sha256"]
    python_identity = plan["pythonExecutable"]
    pin = TrustedGitExecutable(GIT_PATH, git_hash, EXPECTED_GIT_VERSION_LABEL)
    env = _minimal_environment(GIT_PATH)
    fixture_created = False
    uncertain = False
    canaries_installed = False
    git_dir_identity: tuple[str, int, int] | None = None
    positive_marker = root / ".cae-probe-positive"
    negative_marker = root / ".cae-probe-negative"

    def remaining() -> None:
        if time.monotonic() - start > TOTAL_SECONDS:
            raise ValueError("TOTAL_TIME_LIMIT")
        if child_count >= MAX_CHILDREN:
            raise ValueError("CHILD_COUNT_LIMIT")

    def process_factory(*args, **kwargs):
        nonlocal child_count, launch_attempt_count
        remaining()
        launch_attempt_count += 1
        argv = args[0] if args else kwargs.get("args", ())
        record: dict[str, object] = {
            "phase": phase,
            "argv": [_redact_diagnostic_text(str(arg), root, template) for arg in argv],
            "pid": None,
            "exitCode": None,
            "cleanupConfirmed": None,
        }
        child_diagnostics.append(record)
        try:
            process = _launch_checked_python(python_identity, subprocess.Popen, *args, **kwargs)
        except Exception as exc:
            record["launchErrorClass"] = type(exc).__name__
            raise
        child_count += 1
        pids.append(process.pid)
        record["pid"] = process.pid
        tee = _DiagnosticStderrTee(process.stderr)
        record["_stderrTee"] = tee
        return _DiagnosticProcess(process, record, tee)

    def call_git(label: str, argv: tuple[str, ...], *, require_root: bool = True) -> bytes:
        nonlocal phase
        phase = "git:" + label
        remaining()
        if require_root:
            if root_identity is None:
                raise ValueError("FIXTURE_IDENTITY_UNAVAILABLE")
            _owned_identity(root, run_id, root_identity)
            if git_dir_identity is not None:
                _verify_git_directory(root, run_id, root_identity, git_dir_identity)
            if canaries_installed:
                _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
                _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        executable, identity = _validate_executable_pin(pin)
        if executable != GIT_PATH or identity.sha256 != git_hash:
            raise ValueError("GIT_PIN_CHANGED")
        try:
            output = _run_bounded(process_factory, argv, cwd=GIT_PATH.parent, env=env)
        except GitExecutionUncertain:
            raise
        except GitObservationError as exc:
            # The bounded helper waited and drained pipes before reporting a nonzero exit.
            if exc.code == "GIT_COMMAND_FAILED":
                confirmed_cleanup_pids.extend(pid for pid in pids if pid not in confirmed_cleanup_pids)
                completed_labels.append(label + ":nonzero-reaped")
            raise
        confirmed_cleanup_pids.extend(pid for pid in pids if pid not in confirmed_cleanup_pids)
        completed_labels.append(label)
        if require_root:
            _owned_identity(root, run_id, root_identity)
            if git_dir_identity is not None:
                _verify_git_directory(root, run_id, root_identity, git_dir_identity)
            if canaries_installed:
                _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
                _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        return output

    try:
        if _python_identity() != python_identity:
            raise ValueError("PYTHON_PIN_CHANGED")
        # Only create the two direct containers after validating the existing anchor.
        phase = "prepare-owned-containers"
        verification = WORKSPACE / ".local/verification"
        root, template, anchor_identity = _prepare_owned_run_paths(verification, run_id)
        phase = "create-exclusive-fixture"
        root.mkdir()
        fixture_created = True
        template.mkdir()
        _write_exclusive(root / ".cae-probe-owner", (run_id + "\n").encode("ascii"))
        root_identity = _owned_identity(root, run_id)
        if template.resolve(strict=True) != template:
            raise ValueError("TEMPLATE_PATH_REJECTED")
        _reject_reparse_ancestors(template)
        _write_exclusive(root / "tracked.py", b"VALUE = 'original'\n")
        _owned_identity(root, run_id, root_identity)
        _write_exclusive(root / "untracked.py", b"UNTRACKED = True\n")
        _owned_identity(root, run_id, root_identity)

        version_output = call_git("git-version", (str(GIT_PATH), "--version"), require_root=False)
        git_version = version_output.decode("ascii", "strict").strip()
        if not git_version.startswith("git version ") or len(git_version) > 128:
            raise ValueError("GIT_VERSION_OUTPUT_INVALID")
        observations.update({"gitVersion": git_version, "gitExecutablePath": str(GIT_PATH),
                             "gitExecutableSha256": git_hash,
                             "gitVersionCheckpoint": "completed-before-repository-setup"})
        prefix = (str(GIT_PATH), "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
                  "-c", "core.untrackedCache=false", "--git-dir=" + str(root / ".git"),
                  "--work-tree=" + str(root), "--literal-pathspecs")
        identity_args = ("-c", "user.name=CAE native probe", "-c", "user.email=cae-probe@localhost")
        call_git("init", prefix + ("init", "--quiet", "--template=" + str(template), "--initial-branch=main"))
        git_dir_identity = _git_directory_identity(root, run_id, root_identity)
        call_git("add", prefix + identity_args + ("add", "--", "tracked.py"))
        call_git("commit-1", prefix + identity_args + ("commit", "--quiet", "-m", "probe initial"))
        commit1_raw = call_git("head-1", prefix + ("rev-parse", "HEAD"))
        commit1 = commit1_raw.decode("ascii", "strict").strip()
        if len(commit1) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit1):
            raise ValueError("HEAD_OID_INVALID")
        call_git("commit-2", prefix + identity_args + ("commit", "--quiet", "--allow-empty", "-m", "probe second"))
        commit2_raw = call_git("head-2", prefix + ("rev-parse", "HEAD"))
        commit2 = commit2_raw.decode("ascii", "strict").strip()
        if (len(commit2) != len(commit1) or commit2 == commit1
                or any(c not in "0123456789abcdef" for c in commit2)):
            raise ValueError("HEAD_OID_INVALID")

        script = root / ".cae-probe-canary.py"
        _write_exclusive(script, _canary_writer_source().encode("utf-8"))
        _owned_identity(root, run_id, root_identity)
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=False)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        call_git("canary-positive-control",
                 tuple(_positive_canary_argv(prefix, sys.executable, str(script))),
                 require_root=True)
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        python_command = _git_shell_command((sys.executable, str(script), "--negative"))
        encoded_python_command = _quote_git_config_value(python_command)
        config_path = root / ".git" / "config"
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        _reject_reparse_ancestors(config_path)
        config_info = config_path.lstat()
        if (not stat.S_ISREG(config_info.st_mode)
                or getattr(config_info, "st_file_attributes", 0) & 0x400):
            raise ValueError("FIXTURE_CONFIG_REJECTED")
        with config_path.open("ab") as config:
            config.write((f"\n[filter \"probe\"]\n clean = {encoded_python_command}\n process = {encoded_python_command}\n"
                          f"[core]\n fsmonitor = {encoded_python_command}\n pager = {encoded_python_command}\n").encode("utf-8"))
            config.flush()
            os.fsync(config.fileno())
        _owned_identity(root, run_id, root_identity)
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        canaries_installed = True
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)

        observed = identify_source_root(root)
        approved = validate_approved_files((
            ApprovedSourceFileV1("tracked.py", len(b"VALUE = 'original'\n"),
                                 hashlib.sha256(b"VALUE = 'original'\n").hexdigest()),
            ApprovedSourceFileV1("untracked.py", len(b"UNTRACKED = True\n"),
                                 hashlib.sha256(b"UNTRACKED = True\n").hexdigest()),
        ))
        runner = GitMetadataRunner(pin, _process_factory=process_factory)
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        phase = "observe-before"
        runner_before_pids = len(pids)
        try:
            observation1 = runner.observe(str(root), RootIdentity(observed.platform, observed.device, observed.file_id), approved)
        except GitExecutionUncertain:
            raise
        except Exception:
            confirmed_cleanup_pids.extend(pid for pid in pids[runner_before_pids:] if pid not in confirmed_cleanup_pids)
            raise
        confirmed_cleanup_pids.extend(pid for pid in pids[runner_before_pids:] if pid not in confirmed_cleanup_pids)
        observations.update({"observeBefore": {"objectFormat": observation1.object_format,
                                                "headOid": observation1.head_oid,
                                                "rawIndexSha256": observation1.source_index.raw_index_output_sha256,
                                                "rootIdentity": {"platform": root_identity.platform,
                                                                 "device": root_identity.device,
                                                                 "fileId": root_identity.file_id},
                                                "gitDirectoryIdentity": {"platform": git_dir_identity[0],
                                                                          "device": git_dir_identity[1],
                                                                          "fileId": git_dir_identity[2]}}})
        completed_labels.extend(["observe-before-root", "observe-before-index"])
        _owned_identity(root, run_id, root_identity)
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        if (observation1.object_format != ("sha1" if len(commit1) == 40 else "sha256")
                or observation1.head_oid != commit2 or observation1.root_identity != root_identity):
            raise ValueError("OBSERVATION_BEFORE_MISMATCH")
        entries1 = {entry.path: entry for entry in observation1.source_index.entries}
        if (set(entries1) != {"tracked.py", "untracked.py"}
                or entries1["tracked.py"].origin != "tracked"
                or entries1["untracked.py"].origin != "untracked"):
            raise ValueError("INDEX_BEFORE_MISMATCH")

        blob_oid = _tracked_blob_oid(observation1.object_format)
        call_git("index-change", tuple(_index_change_argv(prefix, blob_oid)))
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        call_git("head-cas", prefix + ("update-ref", "refs/heads/main", commit1, commit2))
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        phase = "observe-after"
        runner_before_pids = len(pids)
        try:
            observation2 = runner.observe(str(root), RootIdentity(observed.platform, observed.device, observed.file_id), approved)
        except GitExecutionUncertain:
            raise
        except Exception:
            confirmed_cleanup_pids.extend(pid for pid in pids[runner_before_pids:] if pid not in confirmed_cleanup_pids)
            raise
        confirmed_cleanup_pids.extend(pid for pid in pids[runner_before_pids:] if pid not in confirmed_cleanup_pids)
        observations.update({"observeAfter": {"objectFormat": observation2.object_format,
                                               "headOid": observation2.head_oid,
                                               "rawIndexSha256": observation2.source_index.raw_index_output_sha256,
                                               "rootIdentity": {"platform": root_identity.platform,
                                                                "device": root_identity.device,
                                                                "fileId": root_identity.file_id},
                                               "gitDirectoryIdentity": {"platform": git_dir_identity[0],
                                                                         "device": git_dir_identity[1],
                                                                         "fileId": git_dir_identity[2]}}})
        completed_labels.extend(["observe-after-root", "observe-after-index"])
        _owned_identity(root, run_id, root_identity)
        _verify_git_directory(root, run_id, root_identity, git_dir_identity)
        _verify_marker(root, run_id, root_identity, positive_marker, expected_present=True)
        _verify_marker(root, run_id, root_identity, negative_marker, expected_present=False)
        entries2 = {entry.path: entry for entry in observation2.source_index.entries}
        if (observation2.head_oid != commit1 or observation2.head_oid == observation1.head_oid
                or entries2.keys() != entries1.keys()
                or observation2.source_index.raw_index_output_sha256 == observation1.source_index.raw_index_output_sha256):
            raise ValueError("OBSERVATION_CHANGE_MISMATCH")
        status = "PASS"
        observations.update({
            "gitVersion": git_version,
            "gitExecutablePath": str(GIT_PATH),
            "gitExecutableSha256": git_hash,
            "rootIdentity": {"platform": root_identity.platform, "device": root_identity.device,
                             "fileId": root_identity.file_id},
            "gitDirectoryIdentity": {"platform": git_dir_identity[0], "device": git_dir_identity[1],
                                     "fileId": git_dir_identity[2]},
            "objectFormat": observation1.object_format,
            "headBefore": observation1.head_oid,
            "headAfter": observation2.head_oid,
            "rawIndexSha256Before": observation1.source_index.raw_index_output_sha256,
            "rawIndexSha256After": observation2.source_index.raw_index_output_sha256,
            "selectedOrigins": {path: entry.origin for path, entry in entries1.items()},
        })
    except GitExecutionUncertain:
        failure_code = "GIT_EXECUTION_UNCERTAIN"
        uncertain = True
        failure_exception_class, failure_function, failure_line = _safe_exception_location(sys.exception())
    except GitObservationError as exc:
        failure_code = exc.code
        uncertain = isinstance(exc, GitExecutionUncertain) or exc.code == "GIT_EXECUTION_FAILED"
        failure_exception_class, failure_function, failure_line = _safe_exception_location(exc)
    except Exception as exc:
        code = getattr(exc, "code", None)
        failure_code = code if isinstance(code, str) else (str(exc) if str(exc).isupper() and len(str(exc)) < 80 else "PROBE_FAILED")
        failure_exception_class, failure_function, failure_line = _safe_exception_location(exc)
    for record in child_diagnostics:
        tee = record.pop("_stderrTee", None)
        record.update(_diagnostic_stderr_metadata(tee, root, template))
        pid = record.get("pid")
        record["cleanupConfirmed"] = pid in confirmed_cleanup_pids if isinstance(pid, int) else None
    observations["childDiagnostics"] = child_diagnostics
    report = {
        "schema": "cae-source-git-native-probe-result-v1", "runId": run_id,
        "planSha256": plan_sha256, "status": status, "failureCode": failure_code,
        "fixture": str(root) if fixture_created else None,
        "fixtureRetained": fixture_created, "childCount": child_count, "childPids": pids,
        "confirmedCleanupPids": confirmed_cleanup_pids,
        "completedSteps": completed_labels, "uncertainCleanup": uncertain,
        "confirmedCleanupCount": len(confirmed_cleanup_pids),
        "launchAttemptCount": launch_attempt_count,
        "childCountMeaning": "returned Popen handles; launchAttemptCount increments immediately before Popen",
        "failurePhase": phase if failure_code else None,
        "failureExceptionClass": failure_exception_class,
        "failureFunction": failure_function,
        "failureLine": failure_line,
        "verificationAnchorIdentity": ({"platform": anchor_identity.platform, "device": anchor_identity.device,
                                         "fileId": anchor_identity.file_id} if anchor_identity else None),
        "rootIdentity": ({"platform": root_identity.platform, "device": root_identity.device,
                          "fileId": root_identity.file_id} if root_identity is not None else None),
        "gitDirectoryIdentity": ({"platform": git_dir_identity[0], "device": git_dir_identity[1],
                                  "fileId": git_dir_identity[2]}
                                 if git_dir_identity is not None else None),
        "observations": observations,
        "limits": {"maxChildren": MAX_CHILDREN, "wallClockSeconds": round(time.monotonic() - start, 3),
                   "recursiveCleanup": False, "descendantContainmentClaim": False},
        "limitations": ["host executable path/hash pin does not prevent replacement race after verification",
                        "no filesystem-wide atomic snapshot or descendant containment is claimed",
                        "fixture retained even after partial failure; cleanup is manual and must be separately reviewed"],
    }
    try:
        if report_path.exists() or report_path.is_symlink():
            raise ValueError("REPORT_ALREADY_EXISTS")
        _write_exclusive(report_path, _canonical_json(report))
    except Exception:
        return {**report, "status": "PARTIAL_REPORT_WRITE_FAILED", "failureCode": "REPORT_WRITE_FAILED"}
    return {**report, "reportPath": str(report_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="write a process-free bounded plan")
    parser.add_argument("--validate-plan", action="store_true", help="reconstruct and verify a plan without fixture creation or child launch")
    parser.add_argument("--execute", action="store_true", help="execute only a byte-matching frozen plan")
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--gandalf-authorized", action="store_true",
                        help="explicit approval marker; does not replace independent review")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    modes = sum(bool(value) for value in (args.plan, args.validate_plan, args.execute))
    if modes > 1:
        print("MODE_CONFLICT", file=sys.stderr)
        return 2
    if args.validate_plan:
        if args.plan_file is None or args.expected_plan_sha256 is None:
            print("PLAN_VALIDATION_INPUT_REQUIRED", file=sys.stderr)
            return 2
        try:
            plan, _ = _load_approved_plan(args.plan_file, args.expected_plan_sha256)
            print(json.dumps({"status": "PLAN_VALIDATED_NO_CHILDREN", "runId": plan["runId"],
                              "plannedChildren": plan["limits"]["plannedChildren"]},
                             sort_keys=True, separators=(",", ":")))
            return 0
        except Exception as exc:
            code = getattr(exc, "code", None)
            print(code if isinstance(code, str) else "PLAN_REJECTED", file=sys.stderr)
            return 1
    if args.execute:
        if (not args.gandalf_authorized or args.plan_file is None
                or args.expected_plan_sha256 is None):
            print("NATIVE_EXECUTION_APPROVAL_REQUIRED", file=sys.stderr)
            return 2
        if os.environ.get("CAE_NATIVE_PROBE_TEST_GUARD") == "1":
            print("NATIVE_EXECUTION_DISABLED_BY_TEST_GUARD", file=sys.stderr)
            return 2
        try:
            plan, _ = _load_approved_plan(args.plan_file, args.expected_plan_sha256)
            report = _execute_plan(plan, args.expected_plan_sha256)
            print(json.dumps({"status": report["status"], "failureCode": report["failureCode"],
                              "reportPath": report.get("reportPath"), "childCount": report["childCount"],
                              "fixture": report["fixture"]}, sort_keys=True, separators=(",", ":")))
            return 0 if report["status"] == "PASS" else 1
        except Exception as exc:
            code = getattr(exc, "code", None)
            print(code if isinstance(code, str) else "PLAN_REJECTED", file=sys.stderr)
            return 1
    if not args.plan:
        print("PLAN_MODE_REQUIRED", file=sys.stderr)
        return 2
    try:
        plan = build_plan()
        encoded = _canonical_json(plan)
        if len(encoded) > MAX_PLAN_BYTES:
            raise ValueError("PLAN_SIZE_LIMIT")
        destination_dir = WORKSPACE / ".local/verification"
        _reject_reparse_ancestors(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output or (destination_dir / ("cae-source-git-plan-" + plan["runId"] + ".json"))
        if (not destination.is_absolute() or destination.parent != destination_dir
                or destination.name != "cae-source-git-plan-" + plan["runId"] + ".json"):
            raise ValueError("PLAN_PATH_REJECTED")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(destination, flags, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({"status": plan["status"], "path": str(destination),
                          "sha256": hashlib.sha256(encoded).hexdigest(),
                          "plannedChildren": plan["limits"]["plannedChildren"]},
                         sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        # Emit only fixed exception codes, never path/env/file contents from exceptions.
        code = getattr(exc, "code", None)
        print(code if isinstance(code, str) else "PLAN_PREPARATION_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
