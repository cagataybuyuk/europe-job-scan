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
    COUNTRIES, LIVE_APPROVED_PAIRS, VERSION1_APPROVED_PAIRS, LIVE_MAX_REQUESTS, RESEARCH, Checkpoint, LiveObservation,
    effective_rechecks, live_replay_batch, recheck_first_page,
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
    scope_name = getattr(args, "expansion_scope", None)
    if scope_name not in (None, "version1-nl-ie") or (scope_name is not None and not live):
        parser.error("EXPANSION_LIVE_SCOPE_NOT_APPROVED")
    approved = (VERSION1_APPROVED_PAIRS if scope_name == "version1-nl-ie" else LIVE_APPROVED_PAIRS) if live else None
    if live and not approved:
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
        fixture_digest = sha256(json.dumps(sorted(approved)).encode()).hexdigest()
    else:
        try:
            fixtures, fixture_digest = _load_fixtures(Path(args.expansion_fixtures))
        except (OSError, ValueError, RecursionError):
            parser.error(_FIXTURE_ERROR)

    inputs = json.dumps([markets, criteria_bytes, fixture_digest, "live" if live else "fixtures"], sort_keys=True)
    request_budget = min(args.expansion_max_pages or MAX_REQUESTS_PER_RUN, LIVE_MAX_REQUESTS)
    final: dict = {}

    def render_reports(checkpoint: Checkpoint, listings: list[dict[str, str]],
                       evidence: dict | None = None) -> tuple[bytes, bytes]:
        report = build_expansion_report(checkpoint, listings, markets, criteria, evidence=evidence,
                                        approved=approved)
        final["report"] = report
        return ((json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode(),
                render_markdown(report).encode())

    def work_remaining(checkpoint: Checkpoint, evidence: dict) -> bool:
        """True when an approved pair can still advance or a completed pair still lacks a recheck."""
        failed = {c.tenant for c in checkpoint.cursors if c.status == "error"}
        known = effective_rechecks(evidence)
        return any((c.status == "pending" and c.tenant not in failed and (c.tenant, c.country) in approved)
                   or (c.status == "complete" and c.total and (c.tenant, c.country) in approved
                       and known.get((c.tenant, c.country), {"status": "not_checked"})["status"] == "not_checked")
                   for c in checkpoint.cursors)

    def read_live(checkpoint: Checkpoint, reserved: int, evidence: dict):
        """One bounded invocation. The carrier is capped at the reservation the store already made durable."""
        carrier = HttpCarrier(enabled=True, transport=transport, clock=clock, sleep=sleep,
                              max_requests=reserved, max_pages=reserved)
        # Keep one request for drift rechecks; more run only while the reservation lasts.
        reserve = 1 if reserved > 1 else 0
        result = live_replay_batch(checkpoint, carrier, carrier.begin_employer,
                                   approved=approved, max_pages=reserved - reserve, max_per_employer=reserved - reserve)
        known = effective_rechecks(evidence)
        needs = [c for c in result.checkpoint.cursors
                 if (c.tenant, c.country) in approved and c.status == "complete" and c.total and known.get((c.tenant, c.country),
                                                                      {"status": "not_checked"})["status"] == "not_checked"]
        rechecks = tuple(recheck_first_page(c, carrier, carrier.begin_employer) for c in needs)
        requests = tuple(e for e in carrier.log if e["status"] != "blocked")
        return result, LiveObservation(requests=requests, rechecks=rechecks)

    try:
        result = replay_private_batch(Path(args.out_dir), fixtures, initialize=args.expansion_init,
                                      max_pages=args.expansion_max_pages,
                                      live=read_live if live else None, live_reserve=request_budget,
                                      live_needed=work_remaining if live else None,
                                      input_digest=sha256(inputs.encode()).hexdigest(), render_reports=render_reports)
    except PermissionError:
        parser.error(STORE_ERROR)
    report = final["report"]
    coverage_status = report["coverage"]["status"]
    accounting = report.get("request_accounting")
    print(f"coverage={coverage_status} {'live' if live else 'fixture'}_pages={result.pages} "
          f"new_listings={len(result.listings)} requests={accounting['spent'] if live else 0}"
          + (f" remaining={accounting['remaining']} "
             f"acceptance={'ready' if report['acceptance']['ready'] else 'not_ready'}" if live else ""))
    # Drift is sticky in the durable evidence: a resume can never turn it into success.
    if live and any(c["status"] in ("drift", "unreadable")
                    for c in report["snapshot_consistency"]["first_page_rechecks"]):
        return 1
    code = {"completed": 0, "failed": 1, "incomplete": 3}[coverage_status]
    if live and code == 0 and not report["acceptance"]["ready"]:
        return 4          # collection complete, but a required stable recheck is missing: not accepted
    return code
