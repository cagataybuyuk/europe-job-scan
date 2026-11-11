"""Private local outcome after durable confirmation; never a Sheet write.

The caller is the trusted ATS acknowledgement observer. The ledger authenticates
the recorded identity/status/evidence binding, not the employer source or posting
URL: its public snapshot contains neither a target URL nor an evidence kind.
The caller must supply the independently reviewed posting URL and ATS-ack proof.
This module cannot reserve, submit, confirm, retry or clear a ledger slot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

from ejs.persistence.submission_ledger import SqliteSubmissionLedger


_MAX_BYTES = 8192
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,1023}\Z")
_EVIDENCE = re.compile(r"evidence:sha256:[a-f0-9]{64}\Z")
_POSTING = re.compile(
    r"https://jobs\.smartrecruiters\.com/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/"
    r"[0-9]{8,20}(?:-[A-Za-z0-9_-]{1,200})?\Z"
)


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError("APPLICATION_OUTCOME_REJECTED")


def _digest(value: str) -> str:
    _require(type(value) is str and _OPAQUE.fullmatch(value) is not None)
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _private_file(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
        and 0 < info.st_size <= _MAX_BYTES
    )


def _check_parent(path: Path, parent_fd: int | None = None) -> None:
    for component in (path.parent, *path.parent.parents):
        info = component.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
    info = path.parent.lstat()
    _require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    if parent_fd is not None:
        opened = os.fstat(parent_fd)
        _require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino))


def _existing_matches(parent_fd: int, filename: str, payload: bytes) -> bool:
    try:
        info = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    _require(_private_file(info))
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        _require(_private_file(opened) and (info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino))
        chunks = bytearray()
        while len(chunks) <= _MAX_BYTES:
            chunk = os.read(fd, min(4096, _MAX_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        _require(_private_file(current) and (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino))
        _require(hmac.compare_digest(bytes(chunks), payload))
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _publish(path: Path, payload: bytes) -> bool:
    """Publish complete bytes atomically, without replacing any existing file."""
    _check_parent(path)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp_name = None
    temp_fd = None
    try:
        _check_parent(path, parent_fd)
        if _existing_matches(parent_fd, path.name, payload):
            os.fsync(parent_fd)
            return False
        temp_name = ".ejs-outcome-" + secrets.token_hex(16) + ".tmp"
        temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=parent_fd)
        os.fchmod(temp_fd, 0o600)
        remaining = memoryview(payload)
        while remaining:
            count = os.write(temp_fd, remaining)
            _require(count > 0)
            remaining = remaining[count:]
        os.fsync(temp_fd)
        _check_parent(path, parent_fd)
        try:
            # link() is atomic and exclusive on Mac/Linux; unlike replace(), it
            # cannot overwrite a concurrently published or hostile destination.
            os.link(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
        except FileExistsError:
            _require(_existing_matches(parent_fd, path.name, payload))
            return False
        os.fsync(parent_fd)
        os.unlink(temp_name, dir_fd=parent_fd)
        temp_name = None
        os.fsync(parent_fd)
        _check_parent(path, parent_fd)
        _require(_existing_matches(parent_fd, path.name, payload))
        return True
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def write_confirmed_outcome(path: Path, *, ledger: SqliteSubmissionLedger,
                            submit_key: str, execution_id: str, requisition_id: str,
                            posting_url: str, evidence_ref: str) -> dict:
    """Record Applied locally only after an exact durable Confirmed match.

    The bootstrap must pin this path and the existing canonical ledger; accepting
    either path from an ATS/AI manifest would undermine the single-host boundary.
    Existing canonical bytes are idempotent. Unsafe/corrupt/conflicting files are
    never chmodded, truncated, repaired or replaced, including on storage failure.
    """
    try:
        _require(type(ledger) is SqliteSubmissionLedger)
        submit_digest = _digest(submit_key)
        execution_digest = _digest(execution_id)
        requisition_digest = _digest(requisition_id)
        _require(type(evidence_ref) is str and _EVIDENCE.fullmatch(evidence_ref) is not None)
        _require(type(posting_url) is str and _POSTING.fullmatch(posting_url) is not None)
        _require(isinstance(path, Path) and path.is_absolute() and ".." not in path.parts)
        _require(path != ledger.path and path.name not in ("", ".", ".."))
        snapshot = ledger.snapshot()
        _require(snapshot is not None and snapshot["status"] == "Confirmed")
        _require(hmac.compare_digest(snapshot["submit_key_digest"], submit_digest)
                 and hmac.compare_digest(snapshot["execution_digest"], execution_digest)
                 and hmac.compare_digest(snapshot["requisition_digest"], requisition_digest)
                 and hmac.compare_digest(snapshot["evidence_digest"], evidence_ref.removeprefix("evidence:sha256:")))
        record = {
            "schema_version": "EJS-CONFIRMED-APPLICATION-1",
            "status": "Applied", "source": "ATS_ACKNOWLEDGEMENT",
            "posting_url": posting_url, "requisition_id": requisition_id,
            "execution_id": execution_id, "evidence_ref": evidence_ref,
        }
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        _require(len(payload) <= _MAX_BYTES)
        wrote = _publish(path, payload)
        return {"status": "APPLIED_RECORDED", "applications_write_performed": False,
                "local_outcome_write_performed": wrote, "idempotent": not wrote}
    except Exception:
        raise PermissionError("APPLICATION_OUTCOME_REJECTED") from None
