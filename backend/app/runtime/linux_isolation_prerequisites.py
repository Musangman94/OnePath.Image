"""Read-only Linux isolation prerequisite observations for R1.

This module does not configure the host, launch a canary, or attest a sandbox.
Every positive value describes readable metadata only; delegation and
enforcement remain unproven.
"""

from __future__ import annotations

from dataclasses import dataclass
import platform
from pathlib import Path
import re
from typing import Callable


_OS_RELEASE = "/etc/os-release"
_LSM_LIST = "/sys/kernel/security/lsm"
_APPARMOR_USERNS_RESTRICTION = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
_SECCOMP_ACTIONS = "/proc/sys/kernel/seccomp/actions_avail"
_CGROUP_MEMBERSHIP = "/proc/self/cgroup"
_METADATA_LIMIT = 65536
_KERNEL_RELEASE = re.compile(r"^[A-Za-z0-9._+~-]{1,128}$")
_TOKEN = re.compile(r"^[a-z0-9_]{1,64}$")
_CGROUP_COMPONENT = re.compile(r"^[A-Za-z0-9_.:@-]{1,255}$")


@dataclass(frozen=True, slots=True)
class LinuxIsolationPrerequisiteProbeV1:
    """Immutable observation record; never a sandbox attestation."""

    schema_version: int
    host_status: str
    native_host_identity: str
    trust_status: str
    os_id: str | None
    os_version_id: str | None
    kernel_release: str | None
    apparmor_lsm_observed: bool | None
    apparmor_userns_restriction: str
    user_namespace_creation: str
    landlock_lsm_observed: bool | None
    landlock_syscall_availability: str
    landlock_enforcement: str
    seccomp_actions_available: tuple[str, ...] | None
    seccomp_enforcement: str
    cgroup_v2_observed: bool | None
    cgroup_controllers: tuple[str, ...] | None
    cgroup_child_controllers: tuple[str, ...] | None
    cgroup_delegation: str
    canary_readiness: str


def _read_public_metadata(path: str) -> str:
    return Path(path).read_text(encoding="ascii")


def _read(reader: Callable[[str], str], path: str) -> str | None:
    try:
        value = reader(path)
    except (OSError, UnicodeError, ValueError):
        return None
    if type(value) is not str or len(value) > _METADATA_LIMIT:
        return None
    return value


def _os_release_fields(raw: str | None) -> tuple[str | None, str | None, bool]:
    if raw is None:
        return None, None, False
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key not in {"ID", "VERSION_ID"}:
            continue
        if key in fields:
            return None, None, False
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
            return None, None, False
        fields[key] = value
    if set(fields) != {"ID", "VERSION_ID"}:
        return fields.get("ID"), fields.get("VERSION_ID"), False
    return fields["ID"].lower(), fields["VERSION_ID"], True


def _words(raw: str | None, *, allow_empty: bool = False) -> tuple[str, ...] | None:
    if raw is None:
        return None
    values = raw.split()
    if not values and allow_empty:
        return ()
    if not values or any(not _TOKEN.fullmatch(value) for value in values):
        return None
    if len(values) != len(set(values)):
        return None
    return tuple(values)


def _comma_words(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    values = [value.strip() for value in raw.strip().split(",")]
    if not values or any(not _TOKEN.fullmatch(value) for value in values):
        return None
    if len(values) != len(set(values)):
        return None
    return tuple(values)


def _userns_restriction(raw: str | None) -> str:
    value = raw.strip() if raw is not None else ""
    if value == "1":
        return "ENABLED_POLICY_OBSERVED"
    if value == "0":
        return "DISABLED_POLICY_OBSERVED"
    return "UNKNOWN"


def _cgroup_location(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, False
    unified: list[str] = []
    for line in raw.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            unified.append(fields[2])
    if len(unified) != 1:
        return None, False
    value = unified[0]
    if not value.startswith("/") or len(value) > 1024:
        return None, False
    if value == "/":
        return "", True
    components = value.split("/")[1:]
    if any(not _CGROUP_COMPONENT.fullmatch(part) or part in {".", ".."} for part in components):
        return None, False
    return "/".join(components), True


def probe_ubuntu24_read_only_isolation_prerequisites(
    *,
    platform_system: str | None = None,
    kernel_release: str | None = None,
    text_reader: Callable[[str], str] | None = None,
) -> LinuxIsolationPrerequisiteProbeV1:
    """Read a fixed set of public host metadata without changing OS state.

    Inject ``platform_system``, ``kernel_release`` and ``text_reader`` for
    synthetic tests. The production default reads only fixed /etc, /proc and
    /sys metadata paths. This function is not wired into application startup
    or any launch path.
    """
    try:
        system = platform.system() if platform_system is None else platform_system
        release = platform.release() if kernel_release is None else kernel_release
    except Exception:
        system, release = None, None
    if type(system) is not str or system != "Linux":
        return LinuxIsolationPrerequisiteProbeV1(
            schema_version=1,
            host_status="BLOCKED_UNSUPPORTED_HOST",
            native_host_identity="UNKNOWN",
            trust_status="UNTRUSTED_OBSERVATIONS_ONLY",
            os_id=None,
            os_version_id=None,
            kernel_release=None,
            apparmor_lsm_observed=None,
            apparmor_userns_restriction="UNKNOWN",
            user_namespace_creation="UNPROVEN",
            landlock_lsm_observed=None,
            landlock_syscall_availability="UNKNOWN",
            landlock_enforcement="UNPROVEN",
            seccomp_actions_available=None,
            seccomp_enforcement="UNPROVEN",
            cgroup_v2_observed=None,
            cgroup_controllers=None,
            cgroup_child_controllers=None,
            cgroup_delegation="UNPROVEN",
            canary_readiness="UNPROVEN",
        )

    reader = _read_public_metadata if text_reader is None else text_reader
    os_id, os_version, os_valid = _os_release_fields(_read(reader, _OS_RELEASE))
    valid_kernel = release if type(release) is str and _KERNEL_RELEASE.fullmatch(release) else None
    if not os_valid or valid_kernel is None:
        host_status = "UNKNOWN_HOST_METADATA"
    elif os_id != "ubuntu" or os_version != "24.04":
        host_status = "BLOCKED_UNSUPPORTED_DISTRIBUTION"
    else:
        host_status = "VISIBLE_USERSPACE_METADATA_ONLY"

    lsm_values = _comma_words(_read(reader, _LSM_LIST))
    apparmor_observed = None if lsm_values is None else "apparmor" in lsm_values
    landlock_observed = None if lsm_values is None else "landlock" in lsm_values
    restriction = _userns_restriction(_read(reader, _APPARMOR_USERNS_RESTRICTION))
    seccomp_actions = _words(_read(reader, _SECCOMP_ACTIONS))

    membership = _read(reader, _CGROUP_MEMBERSHIP)
    _, membership_valid = _cgroup_location(membership)
    cgroup_observed = True if membership_valid else None

    return LinuxIsolationPrerequisiteProbeV1(
        schema_version=1,
        host_status=host_status,
        native_host_identity="UNKNOWN",
        trust_status="UNTRUSTED_OBSERVATIONS_ONLY",
        os_id=os_id,
        os_version_id=os_version,
        kernel_release=valid_kernel,
        apparmor_lsm_observed=apparmor_observed,
        apparmor_userns_restriction=restriction,
        user_namespace_creation="UNPROVEN",
        landlock_lsm_observed=landlock_observed,
        landlock_syscall_availability="UNKNOWN",
        landlock_enforcement="UNPROVEN",
        seccomp_actions_available=seccomp_actions,
        seccomp_enforcement="UNPROVEN",
        cgroup_v2_observed=cgroup_observed,
        cgroup_controllers=None,
        cgroup_child_controllers=None,
        cgroup_delegation="UNPROVEN",
        canary_readiness="UNPROVEN_READ_ONLY_METADATA_ONLY",
    )


__all__ = ["LinuxIsolationPrerequisiteProbeV1", "probe_ubuntu24_read_only_isolation_prerequisites"]
