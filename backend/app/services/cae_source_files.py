"""Read bounded, caller-approved source paths through validated open handles.

This module is an internal byte reader only. It does not grant approval, inspect
Git, enumerate a repository, or persist source data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import os
import re
import stat
from typing import Any

from pydantic import ValidationError

from app.schemas.cae import SourceFileEntryV1


class SourceFileReadError(ValueError):
    """Fixed-code failure; never contains an absolute path or file contents."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourceRootIdentity:
    platform: str
    device: int
    file_id: int


@dataclass(frozen=True)
class SourceReadLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0
               for value in (self.max_files, self.max_file_bytes, self.max_total_bytes)):
            raise ValueError("source read limits must be positive finite integers")


_DRIVE_ROOT = re.compile(r"^[A-Za-z]:\\$")
_CHUNK_BYTES = 64 * 1024
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_DEVICE = 0x40
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _root_text(root: str | os.PathLike[str]) -> str:
    try:
        value = os.fspath(root)
    except TypeError:
        raise SourceFileReadError("INVALID_ROOT") from None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SourceFileReadError("INVALID_ROOT")
    return value


def _validated_entries(
    entries: Sequence[SourceFileEntryV1 | dict[str, Any]], limits: SourceReadLimits,
) -> list[SourceFileEntryV1]:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or not entries:
        raise SourceFileReadError("INVALID_SOURCE_ENTRIES")
    if len(entries) > limits.max_files:
        raise SourceFileReadError("SOURCE_LIMIT_EXCEEDED")
    snapshot = tuple(entries)
    if not snapshot or len(snapshot) > limits.max_files:
        raise SourceFileReadError("SOURCE_LIMIT_EXCEEDED")
    result: list[SourceFileEntryV1] = []
    seen: set[str] = set()
    total = 0
    for value in snapshot:
        try:
            if isinstance(value, SourceFileEntryV1):
                source = value.model_dump(mode="python")
            elif isinstance(value, dict):
                source = dict(value)
            else:
                raise TypeError
            # Revalidate a detached field snapshot; existing Pydantic objects are mutable.
            entry = SourceFileEntryV1.model_validate(source)
        except (ValidationError, TypeError):
            raise SourceFileReadError("INVALID_SOURCE_ENTRY") from None
        folded = entry.path.casefold()
        if folded in seen:
            raise SourceFileReadError("SOURCE_PATH_COLLISION")
        seen.add(folded)
        if entry.sizeBytes > limits.max_file_bytes or entry.sizeBytes > limits.max_total_bytes - total:
            raise SourceFileReadError("SOURCE_LIMIT_EXCEEDED")
        total += entry.sizeBytes
        result.append(entry)
    return result


def _snapshot_limits(limits: SourceReadLimits) -> SourceReadLimits:
    if not isinstance(limits, SourceReadLimits):
        raise SourceFileReadError("INVALID_LIMITS")
    try:
        return SourceReadLimits(limits.max_files, limits.max_file_bytes, limits.max_total_bytes)
    except (AttributeError, TypeError, ValueError):
        raise SourceFileReadError("INVALID_LIMITS") from None


def _snapshot_identity(identity: SourceRootIdentity) -> SourceRootIdentity:
    if not isinstance(identity, SourceRootIdentity):
        raise SourceFileReadError("INVALID_ROOT_IDENTITY")
    try:
        platform = identity.platform
        device = identity.device
        file_id = identity.file_id
    except AttributeError:
        raise SourceFileReadError("INVALID_ROOT_IDENTITY") from None
    if (not isinstance(platform, str) or type(device) is not int or type(file_id) is not int
            or device < 0 or file_id < 0):
        raise SourceFileReadError("INVALID_ROOT_IDENTITY")
    return SourceRootIdentity(platform, device, file_id)


def identify_source_root(root: str | os.PathLike[str]) -> SourceRootIdentity:
    """Return directory identity only; validate all root ancestors while opening."""
    value = _root_text(root)
    if os.name == "nt":
        handles: list[Any] = []
        api = _windows_api()
        try:
            _root_path, identity = _open_windows_root(value, handles, api)
            return identity
        finally:
            _close_windows(api, handles)
    if os.name == "posix":
        handles: list[int] = []
        try:
            _root_path, identity = _open_posix_root(value, handles)
            return identity
        finally:
            _close_posix(handles)
    raise SourceFileReadError("UNSUPPORTED_PLATFORM")


def capture_source_bytes(
    root: str | os.PathLike[str],
    expected_identity: SourceRootIdentity,
    entries: Sequence[SourceFileEntryV1 | dict[str, Any]],
    limits: SourceReadLimits,
) -> dict[str, bytes]:
    """Read exactly the listed bytes and verify each declared size and digest."""
    value = _root_text(root)
    limits = _snapshot_limits(limits)
    checked = _validated_entries(entries, limits)  # before any leaf-file open
    expected_identity = _snapshot_identity(expected_identity)
    if os.name == "nt":
        return _capture_windows(value, expected_identity, checked, limits)
    if os.name == "posix":
        return _capture_posix(value, expected_identity, checked, limits)
    raise SourceFileReadError("UNSUPPORTED_PLATFORM")


def _open_posix_root(root: str, handles: list[int]) -> tuple[str, SourceRootIdentity]:
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")
    if (not root.startswith("/") or root.startswith("//")
            or any(not hasattr(os, name) for name in required)
            or os.open not in getattr(os, "supports_dir_fd", set())):
        raise SourceFileReadError("UNSUPPORTED_PLATFORM")
    parts = root[1:].split("/")
    if parts and parts[-1] == "":
        parts.pop()
    if any(part in ("", ".", "..") for part in parts):
        raise SourceFileReadError("INVALID_ROOT")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        current = os.open("/", flags)
        handles.append(current)
        for component in parts:
            current = os.open(component, flags, dir_fd=current)
            handles.append(current)
        info = os.fstat(current)
    except OSError:
        raise SourceFileReadError("SOURCE_ROOT_OPEN_FAILED") from None
    if not stat.S_ISDIR(info.st_mode):
        raise SourceFileReadError("INVALID_ROOT")
    return root.rstrip("/") or "/", SourceRootIdentity("posix", info.st_dev, info.st_ino)


def _open_posix_child(parent_fd: int, component: str, *, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    try:
        return os.open(component, flags, dir_fd=parent_fd)
    except OSError:
        raise SourceFileReadError("SOURCE_FILE_OPEN_FAILED") from None


def _capture_posix(
    root: str,
    expected: SourceRootIdentity,
    entries: list[SourceFileEntryV1],
    limits: SourceReadLimits,
) -> dict[str, bytes]:
    handles: list[int] = []
    output: dict[str, bytes] = {}
    try:
        _root_path, observed = _open_posix_root(root, handles)
        if expected.platform != "posix" or observed != expected:
            raise SourceFileReadError("ROOT_IDENTITY_MISMATCH")
        root_fd = handles[-1]
        for entry in entries:
            components = entry.path.split("/")
            parent_fd = root_fd
            for component in components[:-1]:
                child_fd = _open_posix_child(parent_fd, component, directory=True)
                handles.append(child_fd)
                parent_fd = child_fd
            leaf_fd = _open_posix_child(parent_fd, components[-1], directory=False)
            handles.append(leaf_fd)
            before = os.fstat(leaf_fd)
            if not stat.S_ISREG(before.st_mode):
                raise SourceFileReadError("SOURCE_FILE_NOT_REGULAR")
            if before.st_size != entry.sizeBytes:
                raise SourceFileReadError("SOURCE_SIZE_MISMATCH")
            raw = _read_posix(leaf_fd, entry.sizeBytes)
            after = os.fstat(leaf_fd)
            if _posix_signature(before) != _posix_signature(after):
                raise SourceFileReadError("SOURCE_CHANGED_DURING_READ")
            _verify_bytes(entry, raw)
            output[entry.path] = raw
        return output
    finally:
        _close_posix(handles)


def _posix_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def _read_posix(fd: int, expected_size: int) -> bytes:
    data = bytearray()
    cap = expected_size + 1
    while len(data) < cap:
        try:
            chunk = os.read(fd, min(_CHUNK_BYTES, cap - len(data)))
        except OSError:
            raise SourceFileReadError("SOURCE_READ_FAILED") from None
        if not chunk:
            break
        data.extend(chunk)
    if len(data) != expected_size:
        raise SourceFileReadError("SOURCE_SIZE_MISMATCH")
    return bytes(data)


def _close_posix(handles: list[int]) -> None:
    for handle in reversed(handles):
        try:
            os.close(handle)
        except OSError:
            pass


def _windows_api():
    if os.name != "nt":
        raise SourceFileReadError("UNSUPPORTED_PLATFORM")
    import ctypes as c
    from ctypes import wintypes as w

    class FileTime(c.Structure):
        _fields_ = [("dwLowDateTime", w.DWORD), ("dwHighDateTime", w.DWORD)]

    class ByHandleFileInformation(c.Structure):
        _fields_ = [
            ("dwFileAttributes", w.DWORD),
            ("ftCreationTime", FileTime),
            ("ftLastAccessTime", FileTime),
            ("ftLastWriteTime", FileTime),
            ("dwVolumeSerialNumber", w.DWORD),
            ("nFileSizeHigh", w.DWORD),
            ("nFileSizeLow", w.DWORD),
            ("nNumberOfLinks", w.DWORD),
            ("nFileIndexHigh", w.DWORD),
            ("nFileIndexLow", w.DWORD),
        ]

    class SecurityAttributes(c.Structure):
        _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", c.c_void_p),
                    ("bInheritHandle", w.BOOL)]

    class Api:
        INVALID_HANDLE_VALUE = c.c_void_p(-1).value
        OPEN_EXISTING = 3
        FILE_SHARE_READ = 0x1
        GENERIC_READ = 0x80000000
        FILE_LIST_DIRECTORY = 0x1
        FILE_READ_ATTRIBUTES = 0x80
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        FILE_TYPE_DISK = 1
        DRIVE_FIXED = 3

        def __init__(self):
            self.c = c
            self.w = w
            self.kernel = c.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD,
                                                c.POINTER(SecurityAttributes), w.DWORD,
                                                w.DWORD, w.HANDLE]
            self.kernel.CreateFileW.restype = w.HANDLE
            self.kernel.GetFileInformationByHandle.argtypes = [
                w.HANDLE, c.POINTER(ByHandleFileInformation)]
            self.kernel.GetFileInformationByHandle.restype = w.BOOL
            self.kernel.GetFileType.argtypes = [w.HANDLE]
            self.kernel.GetFileType.restype = w.DWORD
            self.kernel.ReadFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD,
                                             c.POINTER(w.DWORD), c.c_void_p]
            self.kernel.ReadFile.restype = w.BOOL
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.kernel.CloseHandle.restype = w.BOOL
            self.kernel.GetDriveTypeW.argtypes = [w.LPCWSTR]
            self.kernel.GetDriveTypeW.restype = w.UINT
            self.kernel.GetVolumeInformationByHandleW.argtypes = [
                w.HANDLE, w.LPWSTR, w.DWORD, c.POINTER(w.DWORD),
                c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
            self.kernel.GetVolumeInformationByHandleW.restype = w.BOOL
            self.info_type = ByHandleFileInformation
        def open(self, path: str, *, directory: bool):
            flags = self.FILE_FLAG_OPEN_REPARSE_POINT
            access = self.GENERIC_READ
            if directory:
                flags |= self.FILE_FLAG_BACKUP_SEMANTICS
                access = self.FILE_LIST_DIRECTORY | self.FILE_READ_ATTRIBUTES
            handle = self.kernel.CreateFileW(
                path, access, self.FILE_SHARE_READ, None, self.OPEN_EXISTING, flags, None)
            if c.cast(handle, c.c_void_p).value == self.INVALID_HANDLE_VALUE:
                raise SourceFileReadError("SOURCE_FILE_OPEN_FAILED")
            return handle
        def info(self, handle):
            result = ByHandleFileInformation()
            if not self.kernel.GetFileInformationByHandle(handle, c.byref(result)):
                raise SourceFileReadError("SOURCE_HANDLE_QUERY_FAILED")
            if self.kernel.GetFileType(handle) != self.FILE_TYPE_DISK:
                raise SourceFileReadError("SOURCE_FILE_NOT_REGULAR")
            if result.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SourceFileReadError("SOURCE_REPARSE_REJECTED")
            return result
        def root_volume(self, drive_root: str, handle):
            if self.kernel.GetDriveTypeW(drive_root) != self.DRIVE_FIXED:
                raise SourceFileReadError("UNSUPPORTED_FILESYSTEM")
            name = self.c.create_unicode_buffer(64)
            fs = self.c.create_unicode_buffer(64)
            serial = self.w.DWORD()
            max_component = self.w.DWORD()
            flags = self.w.DWORD()
            if not self.kernel.GetVolumeInformationByHandleW(
                    handle, name, len(name), self.c.byref(serial), self.c.byref(max_component),
                    self.c.byref(flags), fs, len(fs)):
                raise SourceFileReadError("UNSUPPORTED_FILESYSTEM")
            if fs.value.upper() != "NTFS":
                raise SourceFileReadError("UNSUPPORTED_FILESYSTEM")

    try:
        return Api()
    except (AttributeError, OSError):
        raise SourceFileReadError("UNSUPPORTED_PLATFORM") from None


def _windows_root_parts(root: str) -> tuple[str, list[str]]:
    if (root.startswith(("\\\\", "//")) or "/" in root
            or not re.match(r"^[A-Za-z]:\\", root)
            or any(part in (".", "..") or ":" in part for part in root[3:].split("\\") if part)):
        raise SourceFileReadError("INVALID_ROOT")
    drive_root = root[:2] + "\\"
    components = [part for part in root[3:].split("\\") if part]
    return drive_root, components


def _windows_identity(info: Any) -> SourceRootIdentity:
    file_id = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    return SourceRootIdentity("windows_ntfs", int(info.dwVolumeSerialNumber), file_id)


def _windows_size(info: Any) -> int:
    return (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)


def _windows_signature(info: Any) -> tuple[int, int, int, int, int, int]:
    time = info.ftLastWriteTime
    return (_windows_size(info), int(time.dwHighDateTime), int(time.dwLowDateTime),
            int(info.dwVolumeSerialNumber), int(info.nFileIndexHigh), int(info.nFileIndexLow))


def _open_windows_root(root: str, handles: list[Any], api: Any | None = None) -> tuple[str, SourceRootIdentity]:
    drive_root, components = _windows_root_parts(root)
    api = _windows_api() if api is None else api
    try:
        if api.kernel.GetDriveTypeW(drive_root) != api.DRIVE_FIXED:
            raise SourceFileReadError("UNSUPPORTED_FILESYSTEM")
        current = drive_root
        handle = api.open(current, directory=True)
        handles.append(handle)
        info = api.info(handle)
        if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise SourceFileReadError("INVALID_ROOT")
        volume_serial = int(info.dwVolumeSerialNumber)
        api.root_volume(drive_root, handle)
        for component in components:
            current = os.path.join(current, component)
            handle = api.open(current, directory=True)
            handles.append(handle)
            info = api.info(handle)
            if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise SourceFileReadError("INVALID_ROOT")
            if int(info.dwVolumeSerialNumber) != volume_serial:
                raise SourceFileReadError("UNSUPPORTED_FILESYSTEM")
        return current, _windows_identity(info)
    except SourceFileReadError:
        raise
    except Exception:
        raise SourceFileReadError("SOURCE_ROOT_OPEN_FAILED") from None


def _capture_windows(
    root: str,
    expected: SourceRootIdentity,
    entries: list[SourceFileEntryV1],
    limits: SourceReadLimits,
) -> dict[str, bytes]:
    handles: list[Any] = []
    output: dict[str, bytes] = {}
    api = _windows_api()
    try:
        root_path, observed = _open_windows_root(root, handles, api)
        if expected.platform != "windows_ntfs" or observed != expected:
            raise SourceFileReadError("ROOT_IDENTITY_MISMATCH")
        root_volume = observed.device
        for entry in entries:
            components = entry.path.split("/")
            current = root_path
            for component in components[:-1]:
                current = os.path.join(current, component)
                directory = api.open(current, directory=True)
                handles.append(directory)
                info = api.info(directory)
                if not info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY or info.dwVolumeSerialNumber != root_volume:
                    raise SourceFileReadError("SOURCE_REPARSE_REJECTED")
            leaf_path = os.path.join(current, components[-1])
            leaf = api.open(leaf_path, directory=False)
            handles.append(leaf)
            before = api.info(leaf)
            if (before.dwFileAttributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_DEVICE)
                    or before.dwVolumeSerialNumber != root_volume):
                raise SourceFileReadError("SOURCE_FILE_NOT_REGULAR")
            if _windows_size(before) != entry.sizeBytes:
                raise SourceFileReadError("SOURCE_SIZE_MISMATCH")
            raw = _read_windows(api, leaf, entry.sizeBytes)
            after = api.info(leaf)
            if _windows_signature(before) != _windows_signature(after):
                raise SourceFileReadError("SOURCE_CHANGED_DURING_READ")
            _verify_bytes(entry, raw)
            output[entry.path] = raw
        return output
    finally:
        _close_windows(api, handles)


def _read_windows(api: Any, handle: Any, expected_size: int) -> bytes:
    data = bytearray()
    cap = expected_size + 1
    while len(data) < cap:
        amount = min(_CHUNK_BYTES, cap - len(data))
        buffer = api.c.create_string_buffer(amount)
        received = api.w.DWORD()
        if not api.kernel.ReadFile(handle, buffer, amount, api.c.byref(received), None):
            raise SourceFileReadError("SOURCE_READ_FAILED")
        if received.value == 0:
            break
        data.extend(buffer.raw[:received.value])
    if len(data) != expected_size:
        raise SourceFileReadError("SOURCE_SIZE_MISMATCH")
    return bytes(data)


def _close_windows(api: Any, handles: list[Any]) -> None:
    try:
        close = api.kernel.CloseHandle
    except Exception:
        return
    for handle in reversed(handles):
        try:
            close(handle)
        except Exception:
            pass


def _verify_bytes(entry: SourceFileEntryV1, content: bytes) -> None:
    if len(content) != entry.sizeBytes:
        raise SourceFileReadError("SOURCE_SIZE_MISMATCH")
    if hashlib.sha256(content).hexdigest() != entry.sha256:
        raise SourceFileReadError("SOURCE_HASH_MISMATCH")
