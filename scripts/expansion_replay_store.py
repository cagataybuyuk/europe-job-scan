"""Owner-only persistence for synthetic W3.5 replay batches."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, fields
import fcntl
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from ejs.services.expansion_replay import (
    MAX_PAGES_PER_EMPLOYER, MAX_REQUESTS_PER_RUN, RESEARCH,
    Checkpoint, Cursor, ReplayResult, initial_checkpoint, replay_batch,
    validate_checkpoint,
)
from scripts.shadow_discovery import private_output_directory, require_no_extended_acl


BUNDLE_NAME = "expansion-replay.json"
STORE_ERROR = "EXPANSION_STORE_REVIEW_REQUIRED"
_SCHEMA = "EJS-EXPANSION-REPLAY-BUNDLE-1"
_MAX_BYTES = 32_000_000
_LOCK_NAME = ".expansion-replay.lock"
_CURSOR_KEYS = {field.name for field in fields(Cursor)}
_LISTING_KEYS = {"company", "role", "location", "url", "ats_kind", "source",
                 "tenant", "region", "posting_id"}
_EMPLOYERS = {tenant: company for company, tenant, _, _ in RESEARCH}


def _require_private_file(fd: int) -> None:
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError(STORE_ERROR)
    require_no_extended_acl(fd)


@contextmanager
def _private_file(directory_fd: int, name: str, flags: int):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
    try:
        _require_private_file(fd)
        yield fd
    finally:
        os.close(fd)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(STORE_ERROR)
        obj[key] = value
    return obj


def _load_bundle(directory_fd: int) -> tuple[Checkpoint, list[dict[str, str]]]:
    with _private_file(directory_fd, BUNDLE_NAME, os.O_RDONLY) as fd:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError(STORE_ERROR)
    bundle = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if (type(bundle) is not dict or bundle.keys() != {"schema", "checkpoint", "listings"}
            or bundle["schema"] != _SCHEMA or type(bundle["listings"]) is not list):
        raise ValueError(STORE_ERROR)
    data = bundle["checkpoint"]
    if (type(data) is not dict or data.keys() != {"scope_version", "cursors"}
            or type(data["cursors"]) is not list):
        raise ValueError(STORE_ERROR)
    cursors = []
    for row in data["cursors"]:
        if type(row) is not dict or row.keys() != _CURSOR_KEYS or type(row["seen"]) is not list:
            raise ValueError(STORE_ERROR)
        cursors.append(Cursor(**{**row, "seen": tuple(row["seen"])}))
    checkpoint = Checkpoint(data["scope_version"], tuple(cursors))
    validate_checkpoint(checkpoint)

    # Check that cursor progress and accumulated output describe the same postings.
    remaining: dict[tuple[str, str], set[str]] = {}
    for cursor in checkpoint.cursors:
        for posting_id in cursor.seen:
            remaining.setdefault((cursor.tenant, posting_id), set()).add(cursor.country)
    for row in bundle["listings"]:
        if (type(row) is not dict or row.keys() != _LISTING_KEYS
                or not all(type(value) is str and value.isprintable() for value in row.values())):
            raise ValueError(STORE_ERROR)
        tenant, posting = row["tenant"], row["posting_id"]
        countries = remaining.pop((tenant, posting), None)
        if (not countries or row["company"] != _EMPLOYERS[tenant]
                or row["ats_kind"] != "smartrecruiters" or row["region"] != "global"
                or row["source"] != "company_watchlist:smartrecruiters"
                or row["url"] != f"https://jobs.smartrecruiters.com/{tenant}/{posting}"
                or not row["role"] or row["role"] != row["role"].strip()
                or not any(row["location"] == code or row["location"].endswith(", " + code)
                           for code in countries)):
            raise ValueError(STORE_ERROR)
    if remaining:
        raise ValueError(STORE_ERROR)
    return checkpoint, bundle["listings"]


def _publish_bundle(directory_fd: int, checkpoint: Checkpoint, listings: list[dict[str, str]]) -> None:
    raw = (json.dumps({"schema": _SCHEMA, "checkpoint": asdict(checkpoint), "listings": listings},
                      ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError(STORE_ERROR)

    pending = f".expansion-{uuid4().hex}.tmp"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            _require_private_file(fd)
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.replace(pending, BUNDLE_NAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    except BaseException:
        os.unlink(pending, dir_fd=directory_fd)
        raise
    os.fsync(directory_fd)


def replay_private_batch(directory: Path, fixtures: dict[tuple[str, str, int], dict], *,
                         initialize: bool = False, max_pages: int = MAX_REQUESTS_PER_RUN,
                         max_per_employer: int = MAX_PAGES_PER_EMPLOYER) -> ReplayResult:
    """Run one synthetic batch and atomically commit progress with cumulative output.

    Each call holds a process lock and reloads the owner-only bundle. Explicit
    initialization requires an absent bundle; resume requires an existing one.
    """
    try:
        if (not directory.is_absolute() or "control" in directory.parts
                or type(initialize) is not bool or (not initialize and not directory.is_dir())):
            raise ValueError(STORE_ERROR)
        with ExitStack() as stack:
            directory_fd = private_output_directory(directory, stack)
            lock_fd = stack.enter_context(_private_file(directory_fd, _LOCK_NAME, os.O_RDWR | os.O_CREAT))
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if initialize:
                try:
                    os.stat(BUNDLE_NAME, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    checkpoint, listings = initial_checkpoint(), []
                else:
                    raise ValueError(STORE_ERROR)
            else:
                checkpoint, listings = _load_bundle(directory_fd)
            result = replay_batch(checkpoint, fixtures, max_pages=max_pages,
                                  max_per_employer=max_per_employer)
            listings.extend(result.listings)
            _publish_bundle(directory_fd, result.checkpoint, listings)
        return result
    except (OSError, ValueError, RecursionError):
        # Paths and parser input are private.
        pass
    raise PermissionError(STORE_ERROR) from None
