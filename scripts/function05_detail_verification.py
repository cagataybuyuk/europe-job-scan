"""FUNCTION-05 detail verification CLI.

``prepare``  Offline. Reads the verified Function 04 report and the approved criteria, selects up to five
             ``preliminary_high`` candidates in Function 04's priority order, writes a reviewable detail-request
             allowlist and prints its digest. No network, no browser, no form access.
``verify``   Re-checks the allowlist against the Function 04 report, then reads each allowlisted posting-detail
             endpoint once (``--live``, only with the owner-quoted allowlist digest) or replays saved bytes
             (``--replay-dir``, fully offline). Re-evaluates with the approved criteria and writes an owner-only
             report, a non-executable Function 06 hand-off and the raw evidence with SHA-256 hashes.

Output stays outside the repository (0700 directory, 0600 files, no overwrite). Exit codes: 0 written, 2 refused.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ejs.services.criteria_loader import CriteriaError, parse_criteria_text
from ejs.services.detail_verification import (DETAIL_HOST, DetailVerificationError, allowlist_digest, build_allowlist,
                                               build_handoff, build_report, check_f04_reproducible, request_details,
                                               verify_allowlist, verify_details)
from ejs.services.http_carrier import HttpCarrier, ReplayCarrier, urllib_transport
from ejs.services.owner_only_files import OutputDirError, prepare_output_dir, write_private_file

ALLOWLIST_NAME = "detail-allowlist.json"
REPORT_NAME = "detail-verification-report.json"
HANDOFF_NAME = "f06-handoff.json"
EVIDENCE_NAME = "evidence-manifest.json"


def _dump(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()


def _load_inputs(args):
    """One read per file: the bytes that are hashed are the bytes that are parsed."""
    if not args.expected_criteria_sha256 or not args.expected_rule_version:
        raise DetailVerificationError("EXPECTED_DIGEST_AND_RULE_VERSION_REQUIRED")
    criteria_bytes = args.criteria.read_bytes()
    criteria_sha = sha256(criteria_bytes).hexdigest()
    if criteria_sha != args.expected_criteria_sha256:
        raise DetailVerificationError("CRITERIA_DIGEST_MISMATCH")
    try:
        criteria = parse_criteria_text(criteria_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CriteriaError("CRITERIA_NOT_UTF8") from exc
    if criteria.rule_version != args.expected_rule_version:
        raise DetailVerificationError("CRITERIA_RULE_VERSION_MISMATCH")
    f04_bytes = args.f04_report.read_bytes()
    f04 = json.loads(f04_bytes)
    f04_sha = sha256(f04_bytes).hexdigest()
    if isinstance(f04, dict) and isinstance(f04.get("criteria"), dict) and f04["criteria"].get("sha256") != criteria_sha:
        raise DetailVerificationError("F04_REPORT_USED_DIFFERENT_CRITERIA")
    return criteria, criteria_sha, f04, f04_sha


def _replay_transport(directory: Path):
    def transport(url: str, headers: dict, timeout: float):
        parts = urlsplit(url).path.split("/")          # /v1/companies/<tenant>/postings/<id>
        path = directory / f"{parts[3]}__{parts[5]}.json"
        if path.is_file() and not path.is_symlink():
            return 200, path.read_bytes()
        return 404, b""
    return transport


def _store_evidence(out_dir: Path, observations: list[dict]) -> bytes:
    raw_dir = prepare_output_dir(out_dir / "raw")
    entries = []
    for o in observations:
        t = o["target"]
        name = f"{t['tenant']}__{t['posting_id']}.json"          # same layout Function 04's --details-dir reads
        if o["raw"] is not None:
            write_private_file(raw_dir, name, o["raw"])
        entries.append({"tenant": t["tenant"], "posting_id": t["posting_id"], "detail_url": t["detail_url"],
                        "observed_at": o["observed_at"], "http_status": o["http_status"], "outcome": o["outcome"],
                        "bytes": len(o["raw"]) if o["raw"] is not None else None,
                        "sha256": sha256(o["raw"]).hexdigest() if o["raw"] is not None else None,
                        "stored_as": f"raw/{name}" if o["raw"] is not None else None})
    return _dump({"schema": "EJS-DETAIL-EVIDENCE-1", "entries": entries})


def _prepare(args) -> int:
    criteria, criteria_sha, f04, f04_sha = _load_inputs(args)
    check_f04_reproducible(f04, criteria, criteria_sha256=criteria_sha,
                           expected_criteria_sha256=args.expected_criteria_sha256,
                           expected_rule_version=args.expected_rule_version)
    allowlist = build_allowlist(f04, f04_report_sha256=f04_sha, limit=args.limit)
    out_dir = prepare_output_dir(args.out_dir)
    if (out_dir / ALLOWLIST_NAME).exists():
        raise OutputDirError("OUTPUT_EXISTS")
    write_private_file(out_dir, ALLOWLIST_NAME, _dump(allowlist))
    print(json.dumps({
        "mode": "prepare_offline_no_requests", "network": False,
        "expected_request_count": len(allowlist["targets"]),
        "allowlist_sha256": allowlist["approval"]["allowlist_sha256"],
        "high_rows_in_report": allowlist["selection"]["high_rows_in_report"],
        "unbound": allowlist["selection"]["unbound"],
        "targets": [{k: t[k] for k in ("sequence", "f04_priority_rank", "tenant", "posting_id", "canonical_url",
                                      "detail_url")} for t in allowlist["targets"]]}, sort_keys=True, indent=1))
    return 0


def _verify(args, *, live_transport, clock, sleep, now) -> int:
    criteria, criteria_sha, f04, f04_sha = _load_inputs(args)
    check_f04_reproducible(f04, criteria, criteria_sha256=criteria_sha,
                           expected_criteria_sha256=args.expected_criteria_sha256,
                           expected_rule_version=args.expected_rule_version)
    doc = json.loads(args.allowlist.read_bytes())
    verify_allowlist(doc, f04, f04_report_sha256=f04_sha)
    digest = allowlist_digest(doc)
    targets = len(doc["targets"])
    if not targets:
        raise DetailVerificationError("NO_TARGETS")
    if args.live:
        if args.approve_allowlist_sha256 != digest:
            raise DetailVerificationError("LIVE_REQUIRES_OWNER_QUOTED_ALLOWLIST_DIGEST")
        carrier = HttpCarrier(enabled=True, transport=live_transport, clock=clock, sleep=sleep, max_requests=targets,
                              max_pages=targets, min_interval_s=1.0, allowed_hosts=frozenset({DETAIL_HOST}))
        mode = "live_allowlisted_get"
    else:
        carrier = ReplayCarrier(transport=_replay_transport(args.replay_dir), clock=clock, max_requests=targets,
                                max_pages=targets, allowed_hosts=frozenset({DETAIL_HOST}))
        mode = "offline_replay"
    out_dir = prepare_output_dir(args.out_dir)
    names = (REPORT_NAME, HANDOFF_NAME, EVIDENCE_NAME)
    if any((out_dir / n).exists() for n in names) or (out_dir / "raw").exists():
        raise OutputDirError("OUTPUT_EXISTS")
    started = now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    observations = request_details(doc, carrier, now=now)
    verification = verify_details(f04, doc, observations, criteria, criteria_sha256=criteria_sha,
                                  expected_criteria_sha256=args.expected_criteria_sha256,
                                  expected_rule_version=args.expected_rule_version)
    report = build_report(doc, verification, carrier, criteria_sha256=criteria_sha, allowlist_sha256=digest,
                          mode=mode, started_at=started)
    handoff = build_handoff(report)
    evidence = _store_evidence(out_dir, observations)
    write_private_file(out_dir, EVIDENCE_NAME, evidence)
    write_private_file(out_dir, REPORT_NAME, _dump(report))
    write_private_file(out_dir, HANDOFF_NAME, _dump(handoff))
    first = handoff["first_suitable_candidate"]
    print(json.dumps({"mode": mode, "requests_attempted": carrier.total, "requests_approved": targets,
                      "verdicts": report["verdict_counts"], "handoff_candidates": len(handoff["candidates"]),
                      "first_suitable": ({k: first[k] for k in ("tenant", "posting_id", "canonical_url", "requisition_id")}
                                         if first else None), "next_step": handoff["next_step"]}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None, *, live_transport: Callable = urllib_transport,
         clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--f04-report", type=Path, required=True, help="suitability-report.json from Function 04")
        p.add_argument("--criteria", type=Path, required=True)
        p.add_argument("--expected-criteria-sha256", required=True)
        p.add_argument("--expected-rule-version", required=True)
        p.add_argument("--out-dir", type=Path, required=True, help="absolute owner-only directory outside the repo")
    sub.choices["prepare"].add_argument("--limit", type=int, default=5)
    v = sub.choices["verify"]
    v.add_argument("--allowlist", type=Path, required=True)
    mode = v.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay-dir", type=Path, help="offline: <tenant>__<posting_id>.json files")
    mode.add_argument("--live", action="store_true", help="read each allowlisted endpoint once (needs the digest)")
    v.add_argument("--approve-allowlist-sha256", help="allowlist_sha256 the owner approved; required with --live")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return _prepare(args)
        return _verify(args, live_transport=live_transport, clock=clock, sleep=sleep, now=now)
    except (CriteriaError, DetailVerificationError, OutputDirError, ValueError, OSError) as exc:
        print(f"function05: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
