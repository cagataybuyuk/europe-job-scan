"""Owner-only (dir 0700 / file 0600) atomic writes to a caller-supplied, repo-external dir.

Used by the Faz 1a summary and CSV export. Never writes under the repository tree or a
``control`` directory (runtime.json / STOP stay user-owned); refuses symlinks.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class OutputDirError(PermissionError):
    pass


def prepare_output_dir(out_dir: str | Path, *, forbidden_roots: tuple[Path, ...] = (REPO_ROOT,)) -> Path:
    raw = Path(out_dir)
    if not raw.is_absolute():
        raise OutputDirError("OUTPUT_DIR_NOT_ABSOLUTE")
    if ".." in raw.parts or "control" in raw.parts:
        raise OutputDirError("OUTPUT_DIR_FORBIDDEN_PATH")
    resolved = raw.resolve()
    for root in forbidden_roots:
        root = root.resolve()
        if resolved == root or root in resolved.parents:
            raise OutputDirError("OUTPUT_DIR_INSIDE_REPO")
    probe = Path(raw.anchor)
    for part in raw.parts[1:]:
        probe = probe / part
        if probe.is_symlink():
            raise OutputDirError("OUTPUT_DIR_SYMLINK")
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(raw)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OutputDirError("OUTPUT_DIR_UNSAFE")
    os.chmod(raw, 0o700)
    return resolved


def write_private_file(directory: Path, name: str, data: bytes) -> Path:
    if "/" in name or name in ("", ".", "..") or name.startswith("."):
        raise OutputDirError("OUTPUT_NAME_INVALID")
    target = directory / name
    if target.is_symlink():
        raise OutputDirError("OUTPUT_TARGET_SYMLINK")
    temp = directory / f".{name}.tmp-{os.getpid()}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    return target
