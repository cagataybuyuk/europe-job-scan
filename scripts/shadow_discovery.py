#!/usr/bin/env python3
"""EJS-W1 shadow discovery CLI. Offline unless --live is given (Faz B gate; never used in Faz A).

  PYTHONPATH=src python scripts/shadow_discovery.py --watchlist <csv outside repo> --criteria <yaml outside repo> \
      --out-dir <dir> [--sheet-labels <json>] [--fixture-dir <dir> | --live | --expansion-fixtures <json>]

Without --live and without --fixture-dir the carrier is OFF: supported employers report ``no_carrier``.
--fixture-dir replays ``<dir>/<api-host>/<tenant>/<offset>.json``. The first page has offset 0.
--expansion-fixtures runs bounded W3.5 synthetic country replay with a durable private checkpoint.
--expansion-live runs the same bounded workflow against the public country feed, but only for the pairs in
``expansion_replay.LIVE_APPROVED_PAIRS`` (empty until the owner approves a scope); otherwise it exits before any request.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import json
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ejs.services.criteria_loader import load_criteria
from ejs.services.discovery import DiscoveryError, classify_url
from ejs.services.http_carrier import MAX_REQUESTS_PER_RUN, HttpCarrier, ReplayCarrier, urllib_transport
from ejs.services.shadow_run import read_watchlist, render_markdown, run_shadow


_SLUG = re.compile(r"/(?:boards|postings|companies)/([A-Za-z0-9_-]+)(?:/|$)")

_PILOT_TARGETS = {
    "HSO International": ("greenhouse", "eu", "hsogroupbv"),
    "CarTrawler": ("lever", "global", "cartrawler"),
    "Veepee": ("lever", "global", "veepee"),
    "Rituals": ("smartrecruiters", "global", "Rituals1"),
}


def pilot_targets_match(rows: list[dict[str, str]]) -> bool:
    targets = {}
    for row in rows:
        cls = classify_url(row["url"])
        targets[row["employer"]] = (cls.kind, cls.region, cls.slug)
    return len(rows) == len(_PILOT_TARGETS) and targets == _PILOT_TARGETS


class MissingFixtureError(DiscoveryError):
    kind = "missing_fixture"


def fixture_transport(fixture_dir: Path):
    fixture_dir = fixture_dir.resolve()

    def transport(url: str, headers: dict, timeout: float):
        parsed = urlparse(url)
        match = _SLUG.search(parsed.path)
        if match is None or parsed.hostname is None:
            raise MissingFixtureError()
        query = parse_qs(parsed.query)
        offset = int((query.get("offset") or query.get("skip") or ["0"])[0])
        fixture = fixture_dir / parsed.hostname / match.group(1) / f"{offset}.json"
        if fixture.resolve() != fixture or not fixture.is_file():
            raise MissingFixtureError()
        try:
            return 200, fixture.read_bytes()
        except FileNotFoundError:
            raise MissingFixtureError() from None

    return transport


_REPORT_NAMES = ("report.json", "report.md", "request_log.json")


def require_no_extended_acl(fd: int) -> None:
    if sys.platform == "darwin":
        # Darwin acl_get_fd_np returns ENOENT when the extended ACL is absent.
        libc = ctypes.CDLL(None, use_errno=True)
        get_acl = libc.acl_get_fd_np
        get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
        get_acl.restype = ctypes.c_void_p
        release_acl = libc.acl_free
        release_acl.argtypes = (ctypes.c_void_p,)
        release_acl.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = get_acl(fd, 0x100)  # ACL_TYPE_EXTENDED
        if acl:
            release_acl(acl)
            raise ValueError("live output must have no extended ACL")
        if ctypes.get_errno() == errno.ENOENT:
            return
    elif sys.platform.startswith("linux"):
        try:
            os.getxattr(fd, "system.posix_acl_access")
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                return
        else:
            raise ValueError("live output must have no extended ACL")
    raise ValueError("cannot verify effective owner-only live output access")


def private_output_directory(path: Path, stack: ExitStack) -> int:
    supplied = path.absolute()
    resolved = supplied.resolve()
    if supplied != resolved:
        raise ValueError("live output must use a canonical path without symlinks")
    for directory in (resolved, *resolved.parents):
        if (os.path.lexists(directory / ".git")
                or ((directory / "HEAD").is_file() and (directory / "objects").is_dir()
                    and (directory / "refs").is_dir())
                or ((directory / "HEAD").is_file() and (directory / "commondir").is_file())):
            raise ValueError("live output must be outside Git repositories and worktrees")

    parent = resolved.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
        raise ValueError("live output parent must be owner-controlled")
    parent_fd = os.open(resolved.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require_no_extended_acl(parent_fd)
    finally:
        os.close(parent_fd)
    if not resolved.exists():
        resolved.mkdir(mode=0o700, exist_ok=True)
    directory_fd = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stack.callback(os.close, directory_fd)
    info = os.fstat(directory_fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("live output directory must be owner-owned with mode 0700")
    require_no_extended_acl(directory_fd)
    return directory_fd


def private_outputs(path: Path, stack: ExitStack):
    directory_fd = private_output_directory(path, stack)
    if any(os.path.lexists(path / name) for name in _REPORT_NAMES):
        raise ValueError("live report files already exist")
    outputs = []
    created = {}
    try:
        for name in _REPORT_NAMES:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            stack.callback(os.close, fd)
            opened = os.fstat(fd)
            created[name] = (opened.st_dev, opened.st_ino)
            output = stack.enter_context(os.fdopen(fd, "w", encoding="utf-8", closefd=False))
            os.fchmod(fd, 0o600)
            require_no_extended_acl(fd)
            outputs.append(output)
    except BaseException:
        for name, identity in created.items():
            try:
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Could not clean up newly created live report {name}: {exc}", file=sys.stderr)
        raise
    return outputs


def publish_offline(out: Path, payloads: tuple[bytes, ...], *, success: bool) -> None:
    destinations = [out / name for name in _REPORT_NAMES]
    if not success and any(os.path.lexists(path) for path in destinations):
        print("Offline discovery failed. Existing reports were preserved.", file=sys.stderr)
        return
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".w1-report-", dir=out) as stage:
        pending = Path(stage)
        backups = {}
        for path, payload in zip(destinations, payloads):
            staged = pending / path.name
            staged.write_bytes(payload)
            staged.chmod(0o600)
            if os.path.lexists(path):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"unsafe offline report path: {path.name}")
                backup = pending / f"{path.name}.previous"
                os.link(path, backup)
                backups[path] = backup
        published = []
        try:
            for path in destinations:
                os.replace(pending / path.name, path)
                published.append(path)
        except OSError:
            for path in reversed(published):
                if path in backups:
                    os.replace(backups[path], path)
                else:
                    path.unlink()
            raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watchlist", required=True)
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sheet-labels")
    ap.add_argument("--expansion-init", action="store_true", help="initialize an unused W3.5 output directory")
    ap.add_argument("--expansion-max-pages", type=int, help="lower the W3.5 batch page ceiling")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fixture-dir")
    mode.add_argument("--live", action="store_true", help="Phase B: public GET for the four pilot employers")
    mode.add_argument("--expansion-fixtures", help="W3.5: synthetic country-page JSON snapshot")
    mode.add_argument("--expansion-live", action="store_true",
                      help="FUNCTION-02: read-only country feed, only for owner-approved employer/country pairs")
    a = ap.parse_args(argv)

    if a.expansion_fixtures is not None or a.expansion_live:
        if a.sheet_labels is not None:
            ap.error("--sheet-labels cannot be used with --expansion-fixtures or --expansion-live")
        if a.expansion_max_pages is None:
            a.expansion_max_pages = MAX_REQUESTS_PER_RUN
        if not 1 <= a.expansion_max_pages <= MAX_REQUESTS_PER_RUN:
            ap.error(f"--expansion-max-pages must be between 1 and {MAX_REQUESTS_PER_RUN}")
        if __package__:
            from .expansion_shadow import run_expansion
        else:
            from expansion_shadow import run_expansion
        return run_expansion(a, ap)
    if a.expansion_init or a.expansion_max_pages is not None:
        ap.error("W3.5 options require --expansion-fixtures or --expansion-live")

    criteria = load_criteria(a.criteria)
    try:
        rows = read_watchlist(a.watchlist)
    except (ValueError, OSError, csv.Error) as exc:
        ap.error(f"invalid watchlist: {exc}")
    if a.live:
        if a.sheet_labels:
            ap.error("--sheet-labels is available only for offline comparison")
        if not pilot_targets_match(rows):
            ap.error("--live requires exactly the four pilot employer/ATS/region/tenant mappings")
        carrier = HttpCarrier(enabled=True, transport=urllib_transport, clock=time.monotonic, sleep=time.sleep,
                              max_requests=20)
    elif a.fixture_dir:
        carrier = ReplayCarrier(transport=fixture_transport(Path(a.fixture_dir)), clock=lambda: 0.0)
    else:
        carrier = HttpCarrier()
    labels = json.loads(Path(a.sheet_labels).read_text(encoding="utf-8")) if a.sheet_labels else None
    out = Path(a.out_dir)
    with ExitStack() as stack:
        if a.live:
            try:
                outputs = private_outputs(out, stack)
            except (AttributeError, OSError, ValueError, RuntimeError) as exc:
                ap.error(f"unsafe live output: {exc}")
        started = datetime.now(timezone.utc).isoformat() if a.live else None
        rep = run_shadow(rows, criteria, fetch=carrier, begin_employer=carrier.begin_employer,
                         request_counts=lambda: carrier.per_employer, sheet_labels=labels)
        rep["transport_mode"] = "live" if a.live else "fixture" if a.fixture_dir else "disabled"
        if started:
            rep["observed_between_utc"] = [started, datetime.now(timezone.utc).isoformat()]
        failed = bool(a.live and rep["listings_unkeyed"]) or bool(
            (a.live or a.fixture_dir)
            and (not rep["employers_supported"]
                 or any(e["status"] in {"partial", "error"} for e in rep["employers"]))
        )
        payloads = tuple(text.encode("utf-8", errors="strict") for text in (
            json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
            render_markdown(rep),
            json.dumps(carrier.log, indent=2, sort_keys=True, allow_nan=False) + "\n",
        ))
        if a.live:
            for output, payload in zip(outputs, payloads):
                output.write(payload.decode("utf-8"))
        else:
            publish_offline(out, payloads, success=not failed)

    print(f"employers={rep['employers_total']} supported={rep['employers_supported']} "
          f"pending={rep['employers_pending_adapter']} listings={rep['listings_parsed']} requests={carrier.total}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
