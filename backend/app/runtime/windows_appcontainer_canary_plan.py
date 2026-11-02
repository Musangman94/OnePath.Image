"""Pure, inactive Windows AppContainer canary launch specification.

The plan is descriptive metadata only. It performs no Windows calls, profile
creation, ACL changes, file access, process launch, or sandbox enforcement.
All supplied paths, digest, and profile name are untrusted claims. A future
trusted OS adapter must independently pin the executable and verify its digest,
generate a fresh profile name with a CSPRNG, fail on profile collision, and
verify fixture ownership, fixed-volume containment, and reparse-point safety.
It must never launch from a plan alone. Drive letters and aliases are not proof
of a fixed volume or filesystem identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import ntpath
import re
import unicodedata


PLAN_SCHEMA_VERSION = 1
POLICY_REF = "onepath-cae-windows-appcontainer-canary-v1"
PROFILE_PREFIX = "OnePath.CAE.Canary."
MAX_FIXTURE_PATHS = 16
MAX_RELATIVE_PATH_UTF8_BYTES = 240
MAX_JOB_WALL_SECONDS = 60
MAX_JOB_CPU_SECONDS = 30
MAX_JOB_MEMORY_BYTES = 512 * 1024 * 1024
MAX_JOB_PROCESSES = 1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_SUFFIX = re.compile(r"^[0-9a-f]{32}$")
_CANARY_EXECUTABLE_NAME = "cae-canary.exe"
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:\\")
_INVALID_WIN32 = re.compile(r'[<>:"|?*]')
_RESERVED_WIN32 = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.I)
_STDIO_HANDLES = ("stdin", "stdout", "stderr")


class WindowsCanaryPlanError(ValueError):
    """Fixed-code validation failure; does not include supplied values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _absolute_windows_path(value: object, code: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise WindowsCanaryPlanError(code)
    path = value
    if (not _DRIVE_ABSOLUTE.match(path) or "/" in path
            or unicodedata.normalize("NFC", path) != path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise WindowsCanaryPlanError(code)
    tail = path[3:]
    if any(
        not component or component in {".", ".."}
        or component.endswith((".", " "))
        or _INVALID_WIN32.search(component)
        or _RESERVED_WIN32.fullmatch(component)
        for component in tail.split("\\") if tail
    ):
        raise WindowsCanaryPlanError(code)
    if ntpath.normpath(path) != path:
        raise WindowsCanaryPlanError(code)
    return path


def _relative_paths(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or not value or len(value) > MAX_FIXTURE_PATHS:
        raise WindowsCanaryPlanError(f"INVALID_{field}")
    normalized: list[str] = []
    seen: set[str] = set()
    for path in value:
        if (type(path) is not str or not path or "\\" in path or ":" in path
                or path.startswith("/") or unicodedata.normalize("NFC", path) != path
                or len(path.encode("utf-8")) > MAX_RELATIVE_PATH_UTF8_BYTES):
            raise WindowsCanaryPlanError(f"INVALID_{field}")
        components = path.split("/")
        if any(
            not component or component in {".", ".."}
            or component.endswith((".", " "))
            or _INVALID_WIN32.search(component)
            or _RESERVED_WIN32.fullmatch(component)
            for component in components
        ):
            raise WindowsCanaryPlanError(f"INVALID_{field}")
        key = path.casefold()
        if key in seen:
            raise WindowsCanaryPlanError(f"INVALID_{field}")
        seen.add(key)
        normalized.append(path)
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class CanaryJobLimitsV1:
    """Finite requested bounds; not evidence that a Job enforces them."""

    cpu_seconds: int = 10
    wall_seconds: int = 20
    memory_bytes: int = 256 * 1024 * 1024
    active_processes: int = 1
    kill_on_close: bool = True
    breakaway_allowed: bool = False

    def __post_init__(self) -> None:
        if (type(self.cpu_seconds) is not int or not 1 <= self.cpu_seconds <= MAX_JOB_CPU_SECONDS
                or type(self.wall_seconds) is not int or not 1 <= self.wall_seconds <= MAX_JOB_WALL_SECONDS
                or type(self.memory_bytes) is not int or not 1 <= self.memory_bytes <= MAX_JOB_MEMORY_BYTES
                or type(self.active_processes) is not int or self.active_processes != MAX_JOB_PROCESSES
                or self.kill_on_close is not True or self.breakaway_allowed is not False):
            raise WindowsCanaryPlanError("INVALID_JOB_LIMITS")


@dataclass(frozen=True, slots=True)
class WindowsAppContainerCanaryPlanV1:
    """Validated canary-only launch description; construction has no OS effects."""

    schema_version: int
    canary_executable_path: str
    canary_executable_sha256: str
    temporary_profile_name: str
    fixture_root: str
    fixture_read_paths: tuple[str, ...]
    fixture_write_paths: tuple[str, ...]
    inherited_handle_names: tuple[str, ...]
    job_limits: CanaryJobLimitsV1
    policy_ref: str = POLICY_REF
    capabilities: tuple[str, ...] = ()
    network_access: bool = False
    os_effects_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PLAN_SCHEMA_VERSION:
            raise WindowsCanaryPlanError("UNSUPPORTED_PLAN_VERSION")
        executable = _absolute_windows_path(self.canary_executable_path, "INVALID_CANARY_EXECUTABLE")
        fixture_root = _absolute_windows_path(self.fixture_root, "INVALID_FIXTURE_ROOT")
        if ntpath.basename(executable).casefold() != _CANARY_EXECUTABLE_NAME:
            raise WindowsCanaryPlanError("INVALID_CANARY_EXECUTABLE")
        if not _SHA256.fullmatch(self.canary_executable_sha256 if type(self.canary_executable_sha256) is str else ""):
            raise WindowsCanaryPlanError("INVALID_CANARY_DIGEST")
        if (type(self.temporary_profile_name) is not str
                or not self.temporary_profile_name.startswith(PROFILE_PREFIX)
                or not _PROFILE_SUFFIX.fullmatch(self.temporary_profile_name[len(PROFILE_PREFIX):])):
            raise WindowsCanaryPlanError("INVALID_PROFILE_NAME")
        if (type(self.policy_ref) is not str or self.policy_ref != POLICY_REF
                or type(self.capabilities) is not tuple or self.capabilities != ()
                or self.network_access is not False or self.os_effects_enabled is not False):
            raise WindowsCanaryPlanError("UNSUPPORTED_CANARY_POLICY")
        if type(self.job_limits) is not CanaryJobLimitsV1:
            raise WindowsCanaryPlanError("INVALID_JOB_LIMITS")
        if type(self.inherited_handle_names) is not tuple or self.inherited_handle_names != _STDIO_HANDLES:
            raise WindowsCanaryPlanError("INVALID_HANDLE_WHITELIST")
        read_paths = _relative_paths(self.fixture_read_paths, "READ_PATHS")
        write_paths = _relative_paths(self.fixture_write_paths, "WRITE_PATHS")
        all_paths = sorted(path.casefold() for path in (*read_paths, *write_paths))
        for index, path in enumerate(all_paths):
            for other in all_paths[index + 1:]:
                if other == path or other.startswith(path + "/"):
                    raise WindowsCanaryPlanError("FIXTURE_PATH_COLLISION")
        object.__setattr__(self, "canary_executable_path", executable)
        object.__setattr__(self, "fixture_root", fixture_root)
        object.__setattr__(self, "fixture_read_paths", read_paths)
        object.__setattr__(self, "fixture_write_paths", write_paths)

    @property
    def expected_identity_policy(self) -> tuple[str, ...]:
        """Checks a future trusted adapter must perform; this plan verifies none."""
        return (
            "independently-pin-canary-executable-path-and-verify-its-digest",
            "generate-profile-name-with-csprng-and-fail-on-collision",
            "never-launch-from-plan-alone",
            "derive-package-sid-from-exact-temporary-profile-name",
            "verify-token-is-appcontainer",
            "verify-token-package-sid-equals-derived-package-sid",
            "verify-capability-sids-empty",
            "verify-no-network-capability",
        )

    @property
    def fixture_access_policy(self) -> tuple[str, ...]:
        """Requested fixture-only access; path trust and ACL enforcement are unverified."""
        return (
            "verify-owned-fixture-root-and-fixed-volume",
            "verify-reparse-safe-containment-for-every-fixture-path",
            "read-only:fixture_read_paths",
            "write-only:fixture_write_paths",
            "deny:all-other-paths",
        )

    def canonical_bytes(self) -> bytes:
        value = {
            "schemaVersion": self.schema_version,
            "policyRef": self.policy_ref,
            "purpose": "inert-canary-only",
            "canaryExecutable": {
                "absolutePath": self.canary_executable_path,
                "sha256": self.canary_executable_sha256,
            },
            "temporaryProfileName": self.temporary_profile_name,
            "fixture": {
                "absoluteRoot": self.fixture_root,
                "readPaths": list(self.fixture_read_paths),
                "writePaths": list(self.fixture_write_paths),
            },
            "inheritedHandleNames": list(self.inherited_handle_names),
            "capabilities": [],
            "networkAccess": False,
            "jobLimits": {
                "cpuSeconds": self.job_limits.cpu_seconds,
                "wallSeconds": self.job_limits.wall_seconds,
                "memoryBytes": self.job_limits.memory_bytes,
                "activeProcesses": self.job_limits.active_processes,
                "killOnClose": True,
                "breakawayAllowed": False,
            },
            "expectedIdentityPolicy": list(self.expected_identity_policy),
            "osEffectsEnabled": False,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_windows_appcontainer_canary_plan(
    *,
    canary_executable_path: str,
    canary_executable_sha256: str,
    temporary_profile_name: str,
    fixture_root: str,
    fixture_read_paths: tuple[str, ...],
    fixture_write_paths: tuple[str, ...],
    job_limits: CanaryJobLimitsV1,
) -> WindowsAppContainerCanaryPlanV1:
    """Build untrusted descriptive metadata; this cannot authorize or trigger a launch."""
    return WindowsAppContainerCanaryPlanV1(
        schema_version=PLAN_SCHEMA_VERSION,
        canary_executable_path=canary_executable_path,
        canary_executable_sha256=canary_executable_sha256,
        temporary_profile_name=temporary_profile_name,
        fixture_root=fixture_root,
        fixture_read_paths=fixture_read_paths,
        fixture_write_paths=fixture_write_paths,
        inherited_handle_names=_STDIO_HANDLES,
        job_limits=job_limits,
    )


__all__ = [
    "CanaryJobLimitsV1",
    "PLAN_SCHEMA_VERSION",
    "POLICY_REF",
    "WindowsAppContainerCanaryPlanV1",
    "WindowsCanaryPlanError",
    "build_windows_appcontainer_canary_plan",
]
