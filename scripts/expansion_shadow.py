"""CLI orchestration for a local synthetic expansion snapshot."""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import time

from ejs.services.criteria_loader import load_criteria
from ejs.services.expansion_replay import (
    COUNTRIES, LIVE_APPROVED_PAIRS, RESEARCH, Checkpoint, live_replay_batch,
)
from ejs.services.http_carrier import HttpCarrier, MAX_REQUESTS_PER_RUN, urllib_transport
from ejs.services.expansion_shadow import build_expansion_report, validate_watchlist
from ejs.services.shadow_run import read_watchlist, render_markdown

if __package__:
    from .expansion_replay_store import STORE_ERROR, decode_json, replay_private_batch
else:
    from expansion_replay_store import STORE_ERROR, decode_json, replay_private_batch

_MAX_FIXTURE_BYTES = 32_000_000
_TENANTS = {tenant for _, tenant, _, _ in RESEARCH}
_FIXTURE_ERROR = "EXPANSION_FIXTURES_INVALID"


def _load_fixtures(path: Path) -> tuple[dict[tuple[str, str, int], object], str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(_FIXTURE_ERROR)
        raw = stream.read(_MAX_FIXTURE_BYTES + 1)
    if len(raw) > _MAX_FIXTURE_BYTES:
        raise ValueError(_FIXTURE_ERROR)
    pages = decode_json(raw)
    if type(pages) is not list:
        raise ValueError(_FIXTURE_ERROR)
    fixtures = {}
    for record in pages:
        if type(record) is not dict or record.keys() != {"tenant", "country", "offset", "page"}:
            raise ValueError(_FIXTURE_ERROR)
        tenant, country, offset = record["tenant"], record["country"], record["offset"]
        if (type(tenant) is not str or tenant not in _TENANTS or country not in COUNTRIES
                or type(offset) is not int or offset < 0):
            raise ValueError(_FIXTURE_ERROR)
        key = (tenant, country, offset)
        if key in fixtures:
            raise ValueError(_FIXTURE_ERROR)
        fixtures[key] = record["page"]
    return fixtures, sha256(raw).hexdigest()


def run_expansion(args: argparse.Namespace, parser: argparse.ArgumentParser, *,
                  transport=urllib_transport, clock=time.monotonic, sleep=time.sleep) -> int:
    live = bool(getattr(args, "expansion_live", False))
    if live and not LIVE_APPROVED_PAIRS:
        parser.error("EXPANSION_LIVE_SCOPE_NOT_APPROVED")
    try:
        markets = validate_watchlist(read_watchlist(args.watchlist))
    except (OSError, ValueError, csv.Error):
        parser.error("EXPANSION_WATCHLIST_SCOPE_MISMATCH")
    try:
        criteria = load_criteria(args.criteria)
        criteria_bytes = json.dumps(criteria.raw, sort_keys=True, allow_nan=False)
    except (ValueError, RecursionError):
        parser.error("EXPANSION_CRITERIA_INVALID")
    fixtures = None
    if live:
        fixture_digest = sha256(json.dumps(sorted(LIVE_APPROVED_PAIRS)).encode()).hexdigest()
    else:
        try:
            fixtures, fixture_digest = _load_fixtures(Path(args.expansion_fixtures))
        except (OSError, ValueError, RecursionError):
            parser.error(_FIXTURE_ERROR)

    inputs = json.dumps([markets, criteria_bytes, fixture_digest, "live" if live else "fixtures"], sort_keys=True)
    carrier = (HttpCarrier(enabled=True, transport=transport, clock=clock, sleep=sleep,
                           max_requests=args.expansion_max_pages or MAX_REQUESTS_PER_RUN) if live else None)
    coverage_status = ""

    def render_reports(checkpoint: Checkpoint, listings: list[dict[str, str]]) -> tuple[bytes, bytes]:
        nonlocal coverage_status
        report = build_expansion_report(checkpoint, listings, markets, criteria,
                                        live_requests=dict(carrier.per_employer) if live else None)
        coverage_status = report["coverage"]["status"]
        return ((json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode(),
                render_markdown(report).encode())

    replay = None
    if live:
        replay = lambda checkpoint: live_replay_batch(  # noqa: E731
            checkpoint, carrier, carrier.begin_employer, max_pages=args.expansion_max_pages)
    try:
        result = replay_private_batch(Path(args.out_dir), fixtures, initialize=args.expansion_init,
                                      max_pages=args.expansion_max_pages, replay=replay,
                                      input_digest=sha256(inputs.encode()).hexdigest(), render_reports=render_reports)
    except PermissionError:
        parser.error(STORE_ERROR)
    print(f"coverage={coverage_status} {'live' if live else 'fixture'}_pages={result.pages} "
          f"new_listings={len(result.listings)} requests={carrier.total if live else 0}")
    return {"completed": 0, "failed": 1, "incomplete": 3}[coverage_status]
