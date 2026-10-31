"""Private local owner bootstrap credential file (explicitly invoked only)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit
import uuid


class CredentialError(Exception):
    """Fixed-code error; never includes secret or OS exception text."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OwnerBootstrapRecord:
    version: int
    instance_id: str
    server_origin: str
    bootstrap_secret: str
    created_at: str


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError
        host = parsed.hostname
        address = ipaddress.ip_address(host or "")
        if not address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        rendered_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        return f"{parsed.scheme}://{rendered_host}" + (f":{port}" if port is not None else "")
    except Exception:
        raise CredentialError("INVALID_ORIGIN") from None


def _record_bytes(record: OwnerBootstrapRecord) -> bytes:
    return (json.dumps(record.__dict__, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class OwnerCredentialStore:
    """Store rooted at a caller-selected trusted OS-owner directory."""
    DIRECTORY = "owner-auth"
    FILENAME = "bootstrap-v1.json"

    def __init__(self, trusted_root: Path):
        self.root = Path(trusted_root)
        self.directory = self.root / self.DIRECTORY
        self.path = self.directory / self.FILENAME

    def create(self, server_origin: str, *, now: datetime | None = None) -> OwnerBootstrapRecord:
        origin = _origin(server_origin)
        if self.path.exists() or self.path.is_symlink() or self.directory.exists() or self.directory.is_symlink():
            raise CredentialError("CREDENTIAL_EXISTS")
        try:
            self._create_private_directory()
            record = OwnerBootstrapRecord(1, str(uuid.uuid4()), origin, secrets.token_urlsafe(32), _timestamp(now))
            self._exclusive_write(_record_bytes(record))
            return record
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("CREDENTIAL_IO") from None

    def read(self) -> OwnerBootstrapRecord:
        try:
            data = self._read_private()
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {"version", "instance_id", "server_origin", "bootstrap_secret", "created_at"} or value["version"] != 1:
                raise ValueError
            record = OwnerBootstrapRecord(**value)
            created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
            if type(record.version) is not int or str(uuid.UUID(record.instance_id)) != record.instance_id or len(record.bootstrap_secret) < 40 or not record.created_at.endswith("Z") or created.utcoffset().total_seconds() != 0 or _origin(record.server_origin) != record.server_origin:
                raise ValueError
            return record
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("CREDENTIAL_INVALID") from None

    def rotate(self, server_origin: str | None = None, *, now: datetime | None = None) -> OwnerBootstrapRecord:
        old = self.read()
        origin = _origin(server_origin) if server_origin is not None else old.server_origin
        new = OwnerBootstrapRecord(1, str(uuid.uuid4()), origin, secrets.token_urlsafe(32), _timestamp(now))
        try:
            self._replace_private(_record_bytes(new))
            return new
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("CREDENTIAL_IO") from None

    def _create_private_directory(self):
        if os.name == "nt":
            _windows_create_directory(self.directory)
        else:
            if not hasattr(os, "O_NOFOLLOW"):
                raise CredentialError("SECURE_FILE_UNAVAILABLE")
            self.directory.mkdir(mode=0o700)
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CredentialError("UNSAFE_DIRECTORY")

    def _exclusive_write(self, data: bytes):
        if os.name == "nt":
            _windows_write_new(self.path, data)
            return
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if os.fstat(fd).st_uid != os.getuid():
                raise CredentialError("UNSAFE_FILE")
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally:
            os.close(fd)

    def _read_private(self) -> bytes:
        if os.name == "nt":
            return _windows_read_private(self.directory, self.path)
        flags = os.O_RDONLY | os.O_NOFOLLOW
        dfd = os.open(self.directory, flags | getattr(os, "O_DIRECTORY", 0))
        try:
            ds = os.fstat(dfd)
            if ds.st_uid != os.getuid() or stat.S_IMODE(ds.st_mode) != 0o700:
                raise CredentialError("UNSAFE_DIRECTORY")
            fd = os.open(self.FILENAME, flags, dir_fd=dfd)
            try:
                fs = os.fstat(fd)
                if not stat.S_ISREG(fs.st_mode) or fs.st_uid != os.getuid() or stat.S_IMODE(fs.st_mode) != 0o600:
                    raise CredentialError("UNSAFE_FILE")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(65537)
                if len(data) > 65536:
                    raise CredentialError("CREDENTIAL_INVALID")
                return data
            finally:
                os.close(fd)
        finally:
            os.close(dfd)

    def _replace_private(self, data: bytes):
        # Validate through an opened handle before the atomic replacement.
        self._read_private()
        if os.name == "nt":
            _windows_replace_private(self.directory, self.path, data)
            return
        fd, temp_name = tempfile.mkstemp(prefix=".bootstrap-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
            os.replace(temp_name, self.path)
        finally:
            os.close(fd)
            try: os.unlink(temp_name)
            except FileNotFoundError: pass


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise CredentialError("INVALID_TIME")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _windows_create_directory(path: Path):
    # Fail closed until the native ACL backend is available on Windows.
    try:
        _win_security().create_directory(path)
    except CredentialError:
        raise
    except Exception:
        raise CredentialError("SECURE_FILE_UNAVAILABLE") from None


def _windows_write_new(path: Path, data: bytes):
    try: _win_security().write_new(path, data)
    except CredentialError: raise
    except Exception: raise CredentialError("SECURE_FILE_UNAVAILABLE") from None


def _windows_read_private(directory: Path, path: Path) -> bytes:
    try: return _win_security().read_private(directory, path)
    except CredentialError: raise
    except Exception: raise CredentialError("SECURE_FILE_UNAVAILABLE") from None


def _windows_replace_private(directory: Path, path: Path, data: bytes):
    try: _win_security().replace_private(directory, path, data)
    except CredentialError: raise
    except Exception: raise CredentialError("SECURE_FILE_UNAVAILABLE") from None


def _win_security():
    if os.name != "nt":
        raise CredentialError("SECURE_FILE_UNAVAILABLE")
    import ctypes as c
    from ctypes import wintypes as w

    class SA(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]

    class Native:
        INVALID = c.c_void_p(-1).value
        OPEN_REPARSE = 0x00200000
        BACKUP = 0x02000000
        WRITE_THROUGH = 0x80000000
        FILE_ATTRIBUTE_TAG_INFO = 9
        SE_FILE_OBJECT = 1
        OWNER_SECURITY_INFORMATION = 1
        DACL_SECURITY_INFORMATION = 4
        PROTECTED_DACL = 0x1000
        def __init__(self):
            self.adv = c.WinDLL("advapi32", use_last_error=True)
            self.kernel = c.WinDLL("kernel32", use_last_error=True)
            self.adv.EqualSid.argtypes = [c.c_void_p, c.c_void_p]; self.adv.EqualSid.restype = w.BOOL
            self.adv.GetSecurityDescriptorControl.argtypes = [c.c_void_p, c.POINTER(w.WORD), c.POINTER(w.DWORD)]; self.adv.GetSecurityDescriptorControl.restype = w.BOOL
            self.adv.GetSecurityDescriptorDacl.argtypes = [c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)]; self.adv.GetSecurityDescriptorDacl.restype = w.BOOL
            self.adv.GetAce.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]; self.adv.GetAce.restype = w.BOOL
            self.kernel.CloseHandle.argtypes = [w.HANDLE]; self.kernel.CloseHandle.restype = w.BOOL
            self.kernel.FlushFileBuffers.argtypes = [w.HANDLE]; self.kernel.FlushFileBuffers.restype = w.BOOL
            self.kernel.WriteFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]; self.kernel.WriteFile.restype = w.BOOL
            self.kernel.ReadFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]; self.kernel.ReadFile.restype = w.BOOL
            self.user = self._current_sid()
            self.system = self._sid("S-1-5-18")
            self.sddl = f"D:P(A;OICI;FA;;;{self._sid_text(self.user)})(A;OICI;FA;;;S-1-5-18)"
        def __del__(self):
            try:
                self.kernel.LocalFree.argtypes = [c.c_void_p]
                self.kernel.LocalFree(self.system)
            except Exception: pass
        def fail(self): raise CredentialError("SECURE_FILE_UNAVAILABLE")
        def _sid(self, value):
            result = c.c_void_p()
            fn = self.adv.ConvertStringSidToSidW
            fn.argtypes = [w.LPCWSTR, c.POINTER(c.c_void_p)]; fn.restype = w.BOOL
            if not fn(value, c.byref(result)): self.fail()
            return result
        def _sid_text(self, sid):
            text = w.LPWSTR()
            fn = self.adv.ConvertSidToStringSidW
            fn.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]; fn.restype = w.BOOL
            if not fn(sid, c.byref(text)): self.fail()
            result = text.value
            self.kernel.LocalFree.argtypes = [c.c_void_p]; self.kernel.LocalFree(text)
            return result
        def _current_sid(self):
            token = w.HANDLE(); self.kernel.GetCurrentProcess.restype = w.HANDLE
            self.adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]; self.adv.OpenProcessToken.restype = w.BOOL
            self.adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
            self.adv.GetTokenInformation.restype = w.BOOL
            if not self.adv.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, c.byref(token)): self.fail()
            try:
                size = w.DWORD()
                self.adv.GetTokenInformation(token, 1, None, 0, c.byref(size))
                buf = c.create_string_buffer(size.value)
                if not self.adv.GetTokenInformation(token, 1, buf, size, c.byref(size)): self.fail()
                self._token_buffer = buf
                return c.c_void_p.from_buffer(buf).value
            finally: self.kernel.CloseHandle(token)
        def _sa(self):
            descriptor = c.c_void_p()
            self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.sddl, 1, c.byref(descriptor), None)
            return SA(c.sizeof(SA), descriptor, False), descriptor
        def _open(self, path, access, flags, creation):
            fn = self.kernel.CreateFileW
            fn.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(SA), w.DWORD, w.DWORD, w.HANDLE]
            fn.restype = w.HANDLE
            handle = fn(str(path), access, 0, None, creation, flags, None)
            if c.cast(handle, c.c_void_p).value == self.INVALID: self.fail()
            return handle
        def _attributes(self, handle):
            class Info(c.Structure): _fields_ = [("attributes", w.DWORD), ("tag", w.DWORD)]
            info = Info()
            fn = self.kernel.GetFileInformationByHandleEx
            fn.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]; fn.restype = w.BOOL
            if not fn(handle, self.FILE_ATTRIBUTE_TAG_INFO, c.byref(info), c.sizeof(info)): self.fail()
            if info.attributes & 0x400: self.fail()
            return info.attributes
        def _validate(self, handle, directory=False):
            sd = c.c_void_p(); owner = c.c_void_p(); dacl = c.c_void_p(); present = w.BOOL(); defaulted = w.BOOL(); control = w.WORD()
            get = self.adv.GetSecurityInfo
            get.argtypes = [w.HANDLE, w.DWORD, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)]
            get.restype = w.DWORD
            if get(handle, self.SE_FILE_OBJECT, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(sd)) != 0: self.fail()
            try:
                if not self.adv.EqualSid(owner, self.user): self.fail()
                if not self.adv.GetSecurityDescriptorControl(sd, c.byref(control), c.byref(w.DWORD())) or not (control.value & self.PROTECTED_DACL): self.fail()
                if not self.adv.GetSecurityDescriptorDacl(sd, c.byref(present), c.byref(dacl), c.byref(defaulted)) or not present.value or not dacl.value: self.fail()
                count = c.c_ushort.from_address(dacl.value + 4).value
                if count != 2: self.fail()
                found = set()
                for i in range(count):
                    ace = c.c_void_p()
                    if not self.adv.GetAce(dacl, i, c.byref(ace)): self.fail()
                    raw = c.cast(ace, c.POINTER(c.c_ubyte))
                    if raw[0] != 0: self.fail()
                    flags = raw[1]
                    if flags & ~0x03: self.fail()
                    mask = c.c_uint32.from_address(ace.value + 4).value
                    sid = c.c_void_p(ace.value + 8)
                    if mask != 0x1F01FF: self.fail()
                    if self.adv.EqualSid(sid, self.user): found.add("user")
                    elif self.adv.EqualSid(sid, self.system): found.add("system")
                    else: self.fail()
                if found != {"user", "system"}: self.fail()
            finally: self.kernel.LocalFree(sd)
        def create_directory(self, path):
            sa, sd = self._sa()
            try:
                fn = self.kernel.CreateDirectoryW; fn.argtypes = [w.LPCWSTR, c.POINTER(SA)]; fn.restype = w.BOOL
                if not fn(str(path), c.byref(sa)): self.fail()
            finally: self.kernel.LocalFree(sd)
        def write_new(self, path, data):
            directory = Path(path).parent
            dh = self._open(directory, 0x00020000 | 0x0001, self.BACKUP | self.OPEN_REPARSE, 3)
            try:
                if not self._attributes(dh) & 0x10: self.fail()
                self._validate(dh, True)
            finally: self.kernel.CloseHandle(dh)
            sa, sd = self._sa()
            try:
                fn = self.kernel.CreateFileW; fn.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(SA),w.DWORD,w.DWORD,w.HANDLE]; fn.restype = w.HANDLE
                h = fn(str(path), 0x40000000, 0, c.byref(sa), 1, self.WRITE_THROUGH | self.OPEN_REPARSE, None)
                if c.cast(h,c.c_void_p).value == self.INVALID: self.fail()
                try:
                    self._validate(h)
                    written = w.DWORD(); buf = c.create_string_buffer(data)
                    if not self.kernel.WriteFile(h, buf, len(data), c.byref(written), None) or written.value != len(data) or not self.kernel.FlushFileBuffers(h): self.fail()
                finally: self.kernel.CloseHandle(h)
            finally: self.kernel.LocalFree(sd)
        def read_private(self, directory, path):
            dh = self._open(directory, 0x00020000 | 0x0001, self.BACKUP | self.OPEN_REPARSE, 3)
            try:
                if not self._attributes(dh) & 0x10: self.fail()
                self._validate(dh, True)
                fh = self._open(path, 0x80000000, self.OPEN_REPARSE, 3)
                try:
                    if self._attributes(fh) & 0x10: self.fail()
                    self._validate(fh)
                    size = w.DWORD(); self.kernel.GetFileSizeEx(fh, c.byref(c.c_longlong())); self.kernel.SetFilePointer(fh, 0, None, 0)
                    chunks=[]; total=0
                    while total <= 65536:
                        buf=c.create_string_buffer(min(8192,65537-total)); got=w.DWORD()
                        if not self.kernel.ReadFile(fh,buf,len(buf),c.byref(got),None): self.fail()
                        if not got.value: break
                        chunks.append(buf.raw[:got.value]); total += got.value
                    if total > 65536: self.fail()
                    return b"".join(chunks)
                finally: self.kernel.CloseHandle(fh)
            finally: self.kernel.CloseHandle(dh)
        def replace_private(self, directory, path, data):
            self.read_private(directory, path)
            temp = path.with_name(".bootstrap-" + secrets.token_hex(12) + ".tmp")
            self.write_new(temp, data)
            try:
                fn = self.kernel.MoveFileExW; fn.argtypes=[w.LPCWSTR,w.LPCWSTR,w.DWORD]; fn.restype=w.BOOL
                if not fn(str(temp),str(path),0x1|0x8): self.fail()
                self.read_private(directory,path)
            finally:
                try: os.unlink(temp)
                except OSError: pass
    try: return Native()
    except CredentialError: raise
    except Exception: raise CredentialError("SECURE_FILE_UNAVAILABLE") from None
