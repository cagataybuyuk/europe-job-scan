#!/usr/bin/env python3
"""EJS-W1 shadow discovery CLI. Offline unless --live is given (Faz B gate; never used in Faz A).

  PYTHONPATH=src python scripts/shadow_discovery.py --watchlist <csv outside repo> --criteria <yaml outside repo> \
      --out-dir <dir> [--sheet-labels <json>] [--fixture-dir <dir> | --live]

Without --live and without --fixture-dir the carrier is OFF: supported employers report ``no_carrier``.
--fixture-dir replays ``<dir>/<api-host>/<tenant>/<offset>.json``. The first page has offset 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import stat
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ejs.services.criteria_loader import load_criteria
from ejs.services.discovery import DiscoveryError, classify_url
from ejs.services.http_carrier import HttpCarrier, ReplayCarrier, urllib_transport
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


def private_outputs(path: Path, stack: ExitStack):
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
    if not resolved.exists():
        resolved.mkdir(mode=0o700)
    directory_fd = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stack.callback(os.close, directory_fd)
    info = os.fstat(directory_fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("live output directory must be owner-owned with mode 0700")
    if any(os.path.lexists(resolved / name) for name in _REPORT_NAMES):
        raise ValueError("live report files already exist")
    outputs = []
    for name in _REPORT_NAMES:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        output = stack.enter_context(os.fdopen(fd, "w", encoding="utf-8"))
        os.fchmod(output.fileno(), 0o600)
        outputs.append(output)
    return outputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watchlist", required=True)
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sheet-labels")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fixture-dir")
    mode.add_argument("--live", action="store_true", help="Phase B: public GET for the four pilot employers")
    a = ap.parse_args(argv)

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
            except (OSError, ValueError, RuntimeError) as exc:
                ap.error(f"unsafe live output: {exc}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            outputs = [stack.enter_context((out / name).open("w", encoding="utf-8")) for name in _REPORT_NAMES]
        started = datetime.now(timezone.utc).isoformat() if a.live else None
        rep = run_shadow(rows, criteria, fetch=carrier, begin_employer=carrier.begin_employer,
                         request_counts=lambda: carrier.per_employer, sheet_labels=labels)
        rep["transport_mode"] = "live" if a.live else "fixture" if a.fixture_dir else "disabled"
        if started:
            rep["observed_between_utc"] = [started, datetime.now(timezone.utc).isoformat()]
        outputs[0].write(json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        outputs[1].write(render_markdown(rep))
        outputs[2].write(json.dumps(carrier.log, indent=2, sort_keys=True) + "\n")
    print(f"employers={rep['employers_total']} supported={rep['employers_supported']} "
          f"pending={rep['employers_pending_adapter']} listings={rep['listings_parsed']} requests={carrier.total}")
    if a.live and rep["listings_unkeyed"]:
        return 1
    if a.live or a.fixture_dir:
        if not rep["employers_supported"] or any(e["status"] in {"partial", "error"} for e in rep["employers"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
