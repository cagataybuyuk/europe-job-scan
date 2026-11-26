#!/usr/bin/env python3
"""Offline, read-only acceptance check for the bounded FUNCTION-02 KPMGNederland/NL live bundle.

  PYTHONPATH=src python scripts/function02_acceptance_check.py --bundle-dir <private bundle dir> --expect-invocations 2

Makes no network request and writes nothing. Prints counts and status codes only, never listing content.
Exit 0: every check passed. Exit 1: at least one failed (names are printed). Exit 2: unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ejs.services.application_bridge import prepare_application_report
from ejs.services.expansion_replay import LIVE_BUNDLE_MAX_REQUESTS, validate_evidence
from ejs.services.expansion_shadow import SCHEMA, project_for_application_bridge

PAIR = ("KPMGNederland", "NL")
_URL = re.compile(r"https://api\.smartrecruiters\.com/v1/companies/KPMGNederland/postings"
                  r"\?country=nl&offset=\d+&limit=100")


def check(report: dict, bundle: dict, expect_invocations: int | None) -> dict[str, bool]:
    evidence = bundle.get("evidence")
    validate_evidence(evidence)
    log = report["request_log"]
    pairs = {(p["tenant"], p["country"]): p for p in report["coverage"]["pairs"]}
    scoped = [pair for pair, p in pairs.items() if p["status"] != "not_approved"]
    pair = pairs[PAIR]
    ids = [v["posting_id"] for v in report["vacancies"]]
    projected = project_for_application_bridge(report)
    prepared = prepare_application_report(projected)
    calls = evidence["invocations"]
    return {
        "live_report_not_executable": (report["schema"] == SCHEMA and report["transport_mode"] == "live_country_feed"
                                       and report["execution_authorized"] is False
                                       and report["snapshot_consistency"]["atomic_snapshot"] is False),
        "scope_is_exactly_the_pilot_pair": scoped == [PAIR],
        "pair_completed_and_acceptance_ready": pair["status"] == "completed" and report["acceptance"]["ready"] is True,
        "requests_within_total_budget": 0 < len(log) == report["network_requests"] <= LIVE_BUNDLE_MAX_REQUESTS,
        "every_request_is_the_approved_get": all(_URL.fullmatch(r["url"]) for r in log),
        "every_request_returned_200": all(r["status"] == 200 for r in log),
        "no_interrupted_or_inflight_attempt": (evidence["in_flight"] is None
                                               and all(c["status"] == "completed" for c in calls)),
        "invocation_count_as_expected": expect_invocations is None or len(calls) == expect_invocations,
        "evidence_matches_report": sum(len(c["requests"]) for c in calls) == len(log),
        "stable_first_page_recheck": any(c["tenant"] == PAIR[0] and c["country"] == PAIR[1] and c["status"] == "stable"
                                         for c in report["snapshot_consistency"]["first_page_rechecks"]),
        "listings_unique_and_match_declared_total": (len(ids) == len(set(ids)) == report["listings_kept"]
                                                     == pair["accepted_listings"] == pair["declared_total"] > 0),
        "every_listing_observed_in_nl": all(v["discovery_evidence"]["countries"] == ["NL"] for v in report["vacancies"]),
        "projection_keeps_every_row": (projected["excluded_rows"]["count"] == 0
                                       and projected["listings_kept"] == report["listings_kept"]),
        "w2_bridge_non_executable": (prepared["candidate_count"] == projected["listings_kept"]
                                     and prepared["executable"] is False and prepared["submit_authorized"] is False),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle-dir", required=True)
    ap.add_argument("--expect-invocations", type=int)
    a = ap.parse_args(argv)
    try:
        directory = Path(a.bundle_dir)
        report = json.loads((directory / "expansion-report.json").read_bytes())
        bundle = json.loads((directory / "expansion-replay.json").read_bytes())
        results = check(report, bundle, a.expect_invocations)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("ACCEPTANCE_INPUT_UNREADABLE", file=sys.stderr)
        return 2
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    summary = {"requests": report["network_requests"], "listings": report["listings_kept"],
               "invocations": len(bundle["evidence"]["invocations"]), "excluded_rows": 0}
    print(json.dumps(summary, sort_keys=True))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
