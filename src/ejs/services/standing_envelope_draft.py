"""W36-02 S4: offline draft of the owner's standing-envelope publication set.

Drafts only. From the owner's exact ``envelope.json`` and ``eligibility-index.json``
bytes this derives the matching ``current.json`` pointer and writes the three files
into a directory the owner names. It never writes under the root-owned approval
root, never runs as root, never overwrites a file and never publishes: root
ownership and the atomic rename into place are the owner's manual procedure in
docs/STANDING_ENVELOPE_RUNBOOK.md. The checks below are a draft lint of the fixed
limits; a draft authorizes nothing. The engine's admission re-checks every pin,
scope and limit against the published bytes at every guard point.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import stat

from ejs.services.current_fact_approval import MAX_APPROVAL_BYTES
from ejs.services.execution_manifest import (
    MAX_MANIFEST_BYTES, POLICY_VERSION, _opaque, _strict_int, _utc, _validate_target,
)
from ejs.services.inventory_eligibility import MATCHING_RULE
from ejs.services.private_source_files import _check_access_acl
from ejs.services.standing_envelope import (
    _APPROVAL_ROOT, _ATS, _BOUNDED_STAGES, _DAY_TIMEZONE, _ENVELOPE_KEYS, _INDEX_KEYS, _PINS,
    ENVELOPE_SCHEMA, INDEX_SCHEMA, MAX_STANDING_TARGETS, POINTER_SCHEMA, _digest, _record,
)
from ejs.services.suitability import _COUNTRY_NAMES

ERROR = "STANDING_ENVELOPE_DRAFT_REJECTED"
FILES = ("envelope.json", "eligibility-index.json", "current.json")


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _lint_envelope(envelope: dict) -> None:
    _require(envelope["status"] == "active" and envelope["policy_version"] == POLICY_VERSION
             and envelope["ats_family"] == _ATS and envelope["daily_timezone"] == _DAY_TIMEZONE
             and envelope["matching_rule_version"] == MATCHING_RULE
             and envelope["country"] in _COUNTRY_NAMES
             and _opaque(envelope["candidate_profile_version"]))
    _strict_int(envelope["daily_cap"], 1, 1)
    issued, expires = _utc(envelope["issued_at"]), _utc(envelope["expires_at"])
    _require(issued < expires and expires - issued <= timedelta(hours=24))
    stages = envelope["rollout_stages"]
    _require(type(stages) is list and stages and len(set(stages)) == len(stages)
             and all(type(s) is str and s in _BOUNDED_STAGES for s in stages))
    targets = envelope["targets"]
    _require(type(targets) is list and 0 < len(targets) <= MAX_STANDING_TARGETS)
    seen = set()
    for row in targets:
        target = _validate_target(row)
        identity = tuple(sorted(target.items()))
        _require(target["ats_family"] == _ATS and identity not in seen)
        seen.add(identity)
    templates = envelope["template_sha256s"]
    _require(type(templates) is list and templates and len(set(templates)) == len(templates)
             and all(map(_digest, templates)))
    _require(all(_digest(envelope[f"{asset}_sha256"]) for asset in _PINS))


def draft_records(envelope_raw: bytes, index_raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Return ``(envelope, index, pointer)``: the owner's exact bytes and the derived pointer."""
    try:
        _require(type(envelope_raw) is bytes and 0 < len(envelope_raw) <= MAX_MANIFEST_BYTES
                 and type(index_raw) is bytes and 0 < len(index_raw) <= MAX_MANIFEST_BYTES)
        envelope = _record(envelope_raw, ENVELOPE_SCHEMA, _ENVELOPE_KEYS)
        index = _record(index_raw, INDEX_SCHEMA, _INDEX_KEYS)
        _require(envelope["envelope_id"] == index["envelope_id"]
                 and envelope["generation"] == index["generation"])
        _lint_envelope(envelope)
        entries = index["entries"]
        _require(type(entries) is dict and entries
                 and all(_opaque(key) and _digest(pin) for key, pin in entries.items()))
        pointer = json.dumps(dict(
            schema_version=POINTER_SCHEMA, envelope_id=envelope["envelope_id"],
            generation=envelope["generation"],
            envelope_sha256=hashlib.sha256(envelope_raw).hexdigest(),
            eligibility_index_sha256=hashlib.sha256(index_raw).hexdigest(),
        ), sort_keys=True, separators=(",", ":")).encode("utf-8")
        _require(len(pointer) <= MAX_APPROVAL_BYTES)
        return envelope_raw, index_raw, pointer
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del envelope_raw, index_raw
    raise PermissionError(ERROR) from None


def _draft_directory(directory: Path, stack: ExitStack) -> int:
    """Open the owner's own 0700 directory, outside the approval root, without following links."""
    _require(isinstance(directory, Path) and directory.is_absolute() and ".." not in directory.parts)
    _require(os.geteuid() != 0)  # drafting never runs as root
    approval = _APPROVAL_ROOT
    _require(directory != approval and approval not in directory.parents
             and directory not in approval.parents)
    fd = None
    for component in directory.parts:
        fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        stack.callback(os.close, fd)
    info = os.fstat(fd)
    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
    _check_access_acl(fd)
    return fd


def write_draft(directory: Path, envelope_raw: bytes, index_raw: bytes) -> dict[str, str]:
    """Write the three drafts (0600, exclusive create) and return ``{file name: sha256}``.

    Refuses an existing file; on any failure removes only what this call created.
    """
    records = draft_records(envelope_raw, index_raw)
    with ExitStack() as stack:
        try:
            dfd = _draft_directory(directory, stack)
            return _write_draft_files(dfd, records)
        except (OSError, ValueError, TypeError):
            raise PermissionError(ERROR) from None


def _write_draft_files(dfd: int, records: tuple[bytes, bytes, bytes]) -> dict[str, str]:
    created: list[str] = []
    try:
        for name, payload in zip(FILES, records):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=dfd)
            created.append(name)
            try:
                os.fchmod(fd, 0o600)
                _check_access_acl(fd)
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(dfd)
        created = []
        return {name: hashlib.sha256(payload).hexdigest() for name, payload in zip(FILES, records)}
    finally:
        for name in created:
            try:
                os.unlink(name, dir_fd=dfd)
            except OSError:
                pass
