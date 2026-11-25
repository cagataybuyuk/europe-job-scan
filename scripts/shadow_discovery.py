#!/usr/bin/env python3
"""EJS-W1 shadow discovery CLI. Offline unless --live is given (Faz B gate; never used in Faz A).

  PYTHONPATH=src python scripts/shadow_discovery.py --watchlist <csv outside repo> --criteria <yaml outside repo> \
      --out-dir <dir> [--sheet-labels <json>] [--fixture-dir <dir> | --live]

Without --live and without --fixture-dir the carrier is OFF: supported employers report ``no_carrier``.
--fixture-dir replays ``<dir>/<slug>.json`` for the first page and ``<dir>/<slug>__<offset>.json`` thereafter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ejs.services.criteria_loader import load_criteria
from ejs.services.discovery import classify_url
from ejs.services.http_carrier import HttpCarrier, ReplayCarrier, urllib_transport
from ejs.services.shadow_run import read_watchlist, render_markdown, run_shadow


_SLUG = re.compile(r"/(?:boards|postings|companies)/([^/?]+)")

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


def fixture_transport(fixture_dir: Path):
    def transport(url: str, headers: dict, timeout: float):
        parsed = urlparse(url)
        match = _SLUG.search(parsed.path)
        if not match:
            return 404, b""
        query = parse_qs(parsed.query)
        offset = int((query.get("offset") or query.get("skip") or ["0"])[0])
        slug = match.group(1)
        suffix = f"__{offset}" if offset else ""
        fixture = fixture_dir / f"{slug}{suffix}.json"
        return (200, fixture.read_bytes()) if fixture.is_file() else (404, b"")
    return transport


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
    rows = read_watchlist(a.watchlist)
    if a.live:
        if a.sheet_labels:
            ap.error("--sheet-labels is available only for offline comparison")
        if not pilot_targets_match(rows):
            ap.error("--live requires exactly the four pilot employer/ATS/region/tenant mappings")
        carrier = HttpCarrier(enabled=True, transport=urllib_transport, clock=time.monotonic, sleep=time.sleep,
                              max_requests=20)
    elif a.fixture_dir:
        carrier = ReplayCarrier(transport=fixture_transport(Path(a.fixture_dir)), clock=time.monotonic)
    else:
        carrier = HttpCarrier()
    labels = json.loads(Path(a.sheet_labels).read_text(encoding="utf-8")) if a.sheet_labels else None
    started = datetime.now(timezone.utc).isoformat() if a.live else None
    rep = run_shadow(rows, criteria, fetch=carrier, begin_employer=carrier.begin_employer,
                     request_counts=lambda: carrier.per_employer, sheet_labels=labels)
    rep["transport_mode"] = "live" if a.live else "fixture" if a.fixture_dir else "disabled"
    if started:
        rep["observed_between_utc"] = [started, datetime.now(timezone.utc).isoformat()]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(rep), encoding="utf-8")
    (out / "request_log.json").write_text(json.dumps(carrier.log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
