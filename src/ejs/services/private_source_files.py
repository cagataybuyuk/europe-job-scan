"""Descriptor-based reads for private offline compiler sources on macOS and Linux."""
from contextlib import ExitStack
import ctypes
from dataclasses import dataclass
import errno
from functools import cache
import os
from pathlib import Path
import stat
import sys


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError


# Darwin <sys/acl.h>. Use the descriptor APIs to inspect APFS extended ACLs.
_ACL_TYPE_EXTENDED = 0x100
_ACL_EXTENDED_DENY = 2
_ACL_FIRST_ENTRY = 0
_ACL_NEXT_ENTRY = -1


@cache
def _darwin_acl_api() -> ctypes.CDLL:
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_uint]
    lib.acl_get_fd_np.restype = ctypes.c_void_p
    lib.acl_valid.argtypes = [ctypes.c_void_p]
    lib.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    lib.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    lib.acl_free.argtypes = [ctypes.c_void_p]
    return lib


def _check_access_acl(fd: int) -> None:
    if sys.platform == "linux":
        # An extended POSIX access ACL is rejected even if its current mask
        # disables every named grant. A directory's default ACL is inherited
        # at creation and has no effect on access to its existing entries.
        try:
            os.getxattr(fd, "system.posix_acl_access")
        except OSError as error:
            if error.errno == errno.ENODATA:
                return
        raise PermissionError

    _require(sys.platform == "darwin")
    lib = _darwin_acl_api()
    acl = lib.acl_get_fd_np(fd, _ACL_TYPE_EXTENDED)
    if not acl:
        _require(ctypes.get_errno() == errno.ENOENT)
        return
    try:
        _require(lib.acl_valid(acl) == 0)
        entry = ctypes.c_void_p()
        entry_id = _ACL_FIRST_ENTRY
        while lib.acl_get_entry(acl, entry_id, ctypes.byref(entry)) == 0:
            tag = ctypes.c_uint()
            _require(lib.acl_get_tag_type(entry, ctypes.byref(tag)) == 0
                     and tag.value == _ACL_EXTENDED_DENY)
            entry_id = _ACL_NEXT_ENTRY
        # On a validated Darwin ACL, EINVAL signals the end of the entries.
        _require(ctypes.get_errno() == errno.EINVAL)
    finally:
        lib.acl_free(acl)


@dataclass(frozen=True, repr=False)
class _OpenedPath:
    fd: int
    parent_fd: int | None
    name: str
    before: os.stat_result
    shared_sticky: bool = False

    def _state(self, info: os.stat_result) -> tuple:
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        if self.shared_sticky:
            # Other users may add entries to system temp directories. Sticky
            # protects the root- or caller-owned child checked by the walk.
            return identity
        return (*identity, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def verify(self) -> None:
        expected = self._state(self.before)
        if self.parent_fd is not None:
            named = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
            _require(self._state(named) == expected)
        _require(self._state(os.fstat(self.fd)) == expected)
        _check_access_acl(self.fd)
        _require(self._state(os.fstat(self.fd)) == expected)


def _open_directory(stack: ExitStack, name: str, parent_fd: int | None) -> _OpenedPath:
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid()))
    shared_sticky = bool(info.st_mode & 0o022)
    if shared_sticky:
        _require(info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX))
    directory = _OpenedPath(fd, parent_fd, name, info, shared_sticky)
    directory.verify()
    return directory


def _open_file(stack: ExitStack, parent_fd: int, name: str, limit: int) -> _OpenedPath:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
             and 0 < before.st_size <= limit)
    # O_NONBLOCK also avoids waiting if a FIFO replaces the inspected entry.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
                 dir_fd=parent_fd)
    stack.callback(os.close, fd)
    source = _OpenedPath(fd, parent_fd, name, before)
    source.verify()
    return source


def _read_exact(source: _OpenedPath) -> bytes:
    size = source.before.st_size
    raw = bytearray()
    while len(raw) <= size:
        chunk = os.read(source.fd, min(65_536, size + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    _require(len(raw) == size)
    return bytes(raw)


def _read_private_sources(sources: tuple[tuple[str | Path, int], ...]) -> tuple[bytes, ...]:
    """Return exact private bytes to the compiler for independent pin checks."""
    with ExitStack() as stack:
        directories = {("/",): _open_directory(stack, "/", None)}
        opened = []
        for filename, limit in sources:
            path = Path(filename)
            parts = path.parts
            _require(path.anchor == "/" and len(parts) >= 2 and ".." not in parts)
            parent = directories[("/",)]
            for end in range(2, len(parts)):
                prefix = parts[:end]
                if prefix not in directories:
                    directories[prefix] = _open_directory(stack, prefix[-1], parent.fd)
                parent = directories[prefix]
            _require(parent.before.st_uid == os.geteuid()
                     and stat.S_IMODE(parent.before.st_mode) == 0o700)
            opened.append(_open_file(stack, parent.fd, path.name, limit))

        raw = tuple(_read_exact(source) for source in opened)
        # Recheck all open descriptors and name bindings after the whole read,
        # including earlier inputs and every ancestor leading back to root.
        for source in opened:
            source.verify()
        for directory in reversed(directories.values()):
            directory.verify()
        return raw
