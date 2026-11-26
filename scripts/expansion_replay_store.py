"""Owner-only persistence for synthetic W3.5 replay batches."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, fields
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable
from uuid import uuid4

from ejs.services.expansion_replay import (
    MAX_PAGES_PER_EMPLOYER, MAX_REQUESTS_PER_RUN, RESEARCH,
    Checkpoint, Cursor, ReplayResult, initial_checkpoint, replay_batch,
    validate_checkpoint,
)
if __package__:
    from .shadow_discovery import private_output_directory, require_no_extended_acl
else:
    from shadow_discovery import private_output_directory, require_no_extended_acl


BUNDLE_NAME = "expansion-replay.json"
REPORT_NAMES = ("expansion-report.json", "expansion-report.md")
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


def decode_json(raw: bytes) -> object:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)


def _load_bundle(directory_fd: int, input_digest: str | None) -> tuple[Checkpoint, list[dict[str, str]]]:
    with _private_file(directory_fd, BUNDLE_NAME, os.O_RDONLY) as fd:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError(STORE_ERROR)
    bundle = decode_json(raw)
    keys = {"schema", "checkpoint", "listings"}
    if input_digest is not None:
        keys.add("input_sha256")
    if (type(bundle) is not dict or bundle.keys() != keys or bundle.get("input_sha256") != input_digest
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


def _publish_bundle(directory_fd: int, checkpoint: Checkpoint, listings: list[dict[str, str]],
                    input_digest: str | None) -> None:
    bundle = {"schema": _SCHEMA, "checkpoint": asdict(checkpoint), "listings": listings}
    if input_digest is not None:
        bundle["input_sha256"] = input_digest
    raw = (json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError(STORE_ERROR)
    _publish_file(directory_fd, BUNDLE_NAME, raw)


def _publish_file(directory_fd: int, name: str, raw: bytes) -> None:
    pending = f".expansion-{uuid4().hex}.tmp"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            _require_private_file(fd)
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.replace(pending, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    except BaseException:
        os.unlink(pending, dir_fd=directory_fd)
        raise
    os.fsync(directory_fd)


def replay_private_batch(directory: Path, fixtures: dict[tuple[str, str, int], object], *,
                         initialize: bool = False, max_pages: int = MAX_REQUESTS_PER_RUN,
                         max_per_employer: int = MAX_PAGES_PER_EMPLOYER,
                         input_digest: str | None = None,
                         render_reports: Callable[[Checkpoint, list[dict[str, str]]],
                                                  tuple[bytes, bytes]] | None = None) -> ReplayResult:
    """Run one synthetic batch and atomically commit progress with cumulative output.

    Each call holds a process lock and reloads the owner-only bundle. Explicit
    initialization requires an absent bundle; resume requires an existing one.
    A bound workflow requires matching inputs on resume. Reports are rebuildable
    views, published under the same lock after the bundle is synchronized.
    """
    try:
        if (not directory.is_absolute() or any(part.casefold() == "control" for part in directory.parts)
                or type(initialize) is not bool or (not initialize and not directory.is_dir())
                or (input_digest is not None and
                    (type(input_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", input_digest)))
                or (render_reports is not None and input_digest is None)):
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
                checkpoint, listings = _load_bundle(directory_fd, input_digest)
            if render_reports is not None:
                for name in REPORT_NAMES:
                    try:
                        with _private_file(directory_fd, name, os.O_RDONLY):
                            if initialize:
                                raise ValueError(STORE_ERROR)
                    except FileNotFoundError:
                        continue
            result = replay_batch(checkpoint, fixtures, max_pages=max_pages,
                                  max_per_employer=max_per_employer)
            listings.extend(result.listings)
            if initialize or result.pages:
                _publish_bundle(directory_fd, result.checkpoint, listings, input_digest)
            if render_reports is not None:
                payloads = render_reports(result.checkpoint, listings)
                for name, payload in zip(REPORT_NAMES, payloads, strict=True):
                    _publish_file(directory_fd, name, payload)
        return result
    except (OSError, ValueError, RecursionError):
        # Paths and parser input are private.
        pass
    raise PermissionError(STORE_ERROR) from None
