"""FUNCTION-05 detail verification CLI.

``prepare``  Offline. Reads the verified Function 04 report and the approved criteria, selects up to five
             ``preliminary_high`` candidates in Function 04's priority order, writes a reviewable detail-request
             allowlist and prints its digest. No network, no browser, no form access.
``verify``   Re-checks the allowlist against the Function 04 report, then reads each allowlisted posting-detail
             endpoint once (``--live``, only with the owner-quoted allowlist digest) or replays saved bytes
             (``--replay-dir``, fully offline). Re-evaluates with the approved criteria and writes an owner-only
             report, a non-executable Function 06 hand-off and the raw evidence with SHA-256 hashes.

Both commands bind the run to the verified ORIGINAL Function 02 data: the owner pins the SHA-256 of the Function 02
file Function 04 was run on, and Function 04 is regenerated from it with the approved criteria and must match the given
report exactly. ``--live`` additionally needs ``--budget-dir``: a durable single-use reservation (one file per allowlist
digest and per detail URL, created before any request, never released automatically, so an interrupted or uncertain
attempt stays consumed and is never retried). Every attempt is journaled (fsync) before and after the request.
Replay output is labelled synthetic and is never Function 06 eligible.

Output stays outside the repository (0700 directory, 0600 files, no overwrite). Exit codes: 0 written, 2 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ejs.services.criteria_loader import CriteriaError, parse_criteria_text
from ejs.services.detail_verification import (DETAIL_HOST, DetailVerificationError, allowlist_digest, build_allowlist,
                                               build_handoff, build_report, check_lineage, request_details,
                                               reserve_budget, verify_allowlist, verify_details)
from ejs.services.expansion_shadow import SCHEMA as EXPANSION_SCHEMA, project_for_application_bridge
from ejs.services.http_carrier import HttpCarrier, ReplayCarrier, urllib_transport
from ejs.services.owner_only_files import OutputDirError, prepare_output_dir, write_private_file

ALLOWLIST_NAME = "detail-allowlist.json"
REPORT_NAME = "detail-verification-report.json"
HANDOFF_NAME = "f06-handoff.json"
EVIDENCE_NAME = "evidence-manifest.json"
JOURNAL_NAME = "attempt-journal.jsonl"


def _dump(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()


def _load_inputs(args):
    """One read per file: the bytes that are hashed are the bytes that are parsed."""
    if not args.expected_criteria_sha256 or not args.expected_rule_version or not args.expected_f02_sha256:
        raise DetailVerificationError("EXPECTED_DIGESTS_AND_RULE_VERSION_REQUIRED")
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
    f02_bytes = args.f02_report.read_bytes()                 # the original Function 02 data, pinned by the owner
    f02_sha = sha256(f02_bytes).hexdigest()
    if f02_sha != args.expected_f02_sha256:
        raise DetailVerificationError("F02_DIGEST_MISMATCH")
    projection = json.loads(f02_bytes)
    if isinstance(projection, dict) and projection.get("schema") == EXPANSION_SCHEMA:
        projection = project_for_application_bridge(projection)          # exactly what function04_suitability does
    lineage = check_lineage(f04, projection, criteria, criteria_sha256=criteria_sha,
                            expected_criteria_sha256=args.expected_criteria_sha256,
                            expected_rule_version=args.expected_rule_version, f02_source_sha256=f02_sha)
    return criteria, criteria_sha, f04, f04_sha, projection, lineage


def _replay_transport(directory: Path):
    def transport(url: str, headers: dict, timeout: float):
        parts = urlsplit(url).path.split("/")          # /v1/companies/<tenant>/postings/<id>
        path = directory / f"{parts[3]}__{parts[5]}.json"
        if path.is_file() and not path.is_symlink():
            return 200, path.read_bytes()
        return 404, b""
    return transport


class _Journal:
    """Append-only, fsynced attempt journal plus immediate raw-body storage. Never rewritten or deleted."""

    def __init__(self, out_dir: Path, header: dict):
        self.raw_dir = prepare_output_dir(out_dir / "raw")
        self._fd = os.open(out_dir / JOURNAL_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.event(header)

    def event(self, record: dict) -> None:
        os.write(self._fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(self._fd)

    def start(self, record: dict) -> None:
        self.event(record)

    def finish(self, record: dict, raw: bytes | None) -> None:
        stored = None
        if raw is not None:
            parts = urlsplit(record["detail_url"]).path.split("/")        # /v1/companies/<tenant>/postings/<id>
            name = f"{parts[3]}__{parts[5]}.json"          # same layout Function 04's --details-dir reads
            write_private_file(self.raw_dir, name, raw)
            stored = f"raw/{name}"
        self.event({**record, "stored_as": stored})

    def close(self) -> None:
        os.close(self._fd)


def _evidence_manifest(observations: list[dict], origin: str) -> bytes:
    entries = [{"tenant": o["target"]["tenant"], "posting_id": o["target"]["posting_id"],
                "detail_url": o["target"]["detail_url"], "outcome": o["outcome"], "attempted": o["attempted"],
                "http_status": o["http_status"], "requested_at": o["requested_at"], "observed_at": o["observed_at"],
                "duration_ms": o["duration_ms"], "bytes": o["response_bytes"], "sha256": o["response_sha256"],
                "stored_as": (f"raw/{o['target']['tenant']}__{o['target']['posting_id']}.json"
                              if o["raw"] is not None else None)} for o in observations]
    return _dump({"schema": "EJS-DETAIL-EVIDENCE-1", "evidence_origin": origin, "entries": entries})


def _prepare(args) -> int:
    criteria, criteria_sha, f04, f04_sha, projection, lineage = _load_inputs(args)
    allowlist = build_allowlist(f04, f04_report_sha256=f04_sha, lineage=lineage, limit=args.limit)
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
    criteria, criteria_sha, f04, f04_sha, projection, lineage = _load_inputs(args)
    doc = json.loads(args.allowlist.read_bytes())
    verify_allowlist(doc, f04, f04_report_sha256=f04_sha, lineage=lineage)
    digest = allowlist_digest(doc)
    targets = len(doc["targets"])
    if not targets:
        raise DetailVerificationError("NO_TARGETS")
    budget_dir = None
    if args.live:
        if args.approve_allowlist_sha256 != digest:
            raise DetailVerificationError("LIVE_REQUIRES_OWNER_QUOTED_ALLOWLIST_DIGEST")
        if args.budget_dir is None:
            raise DetailVerificationError("LIVE_REQUIRES_BUDGET_DIR")
        carrier = HttpCarrier(enabled=True, transport=live_transport, clock=clock, sleep=sleep, max_requests=targets,
                              max_pages=targets, min_interval_s=1.0, allowed_hosts=frozenset({DETAIL_HOST}))
        mode = "live_allowlisted_get"
    else:
        carrier = ReplayCarrier(transport=_replay_transport(args.replay_dir), clock=clock, max_requests=targets,
                                max_pages=targets, allowed_hosts=frozenset({DETAIL_HOST}))
        mode = "offline_replay"
    out_dir = prepare_output_dir(args.out_dir)
    names = (REPORT_NAME, HANDOFF_NAME, EVIDENCE_NAME, JOURNAL_NAME)
    if any((out_dir / n).exists() for n in names) or (out_dir / "raw").exists():
        raise OutputDirError("OUTPUT_EXISTS")
    if args.live:
        budget_dir = prepare_output_dir(args.budget_dir)
        if budget_dir == out_dir or budget_dir in out_dir.parents or out_dir in budget_dir.parents:
            raise DetailVerificationError("BUDGET_DIR_MUST_BE_SEPARATE_FROM_OUT_DIR")
        reserve_budget(budget_dir, doc, now=now, out_dir=out_dir)       # durable, before any request, never released
    origin = "live_employer_response" if args.live else "offline_replay_synthetic"
    started = now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    journal = _Journal(out_dir, {"event": "run_started", "mode": mode, "evidence_origin": origin,
                                 "allowlist_sha256": digest, "requests_approved": targets, "started_at": started,
                                 "budget_reserved": budget_dir is not None})
    try:
        observations = request_details(doc, carrier, now=now, on_start=journal.start, on_finish=journal.finish)
        verification = verify_details(projection, observations, criteria, criteria_sha256=criteria_sha,
                                      expected_criteria_sha256=args.expected_criteria_sha256,
                                      expected_rule_version=args.expected_rule_version)
        report = build_report(doc, verification, carrier, criteria_sha256=criteria_sha, allowlist_sha256=digest,
                              mode=mode, started_at=started)
        handoff = build_handoff(report)
        journal.event({"event": "run_finished", "requests_attempted": carrier.total})
    finally:
        journal.close()                       # on interruption the journal keeps started-without-finished records
    write_private_file(out_dir, EVIDENCE_NAME, _evidence_manifest(observations, origin))
    write_private_file(out_dir, REPORT_NAME, _dump(report))
    write_private_file(out_dir, HANDOFF_NAME, _dump(handoff))
    first = handoff["first_candidate_for_review"]
    print(json.dumps({"mode": mode, "evidence_origin": origin, "requests_attempted": carrier.total,
                      "requests_approved": targets, "verdicts": report["verdict_counts"],
                      "handoff_candidates": len(handoff["candidates"]),
                      "first_candidate_for_review": ({k: first[k] for k in ("tenant", "posting_id", "canonical_url",
                                                                           "requisition_id")} if first else None),
                      "next_step": handoff["next_step"]}, sort_keys=True))
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
        p.add_argument("--f02-report", type=Path, required=True,
                       help="the exact Function 02 file Function 04 was run on (original verified data)")
        p.add_argument("--expected-f02-sha256", required=True, help="independently recorded SHA-256 of that file")
        p.add_argument("--out-dir", type=Path, required=True, help="absolute owner-only directory outside the repo")
    sub.choices["prepare"].add_argument("--limit", type=int, default=5)
    v = sub.choices["verify"]
    v.add_argument("--allowlist", type=Path, required=True)
    mode = v.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay-dir", type=Path, help="offline: <tenant>__<posting_id>.json files")
    mode.add_argument("--live", action="store_true", help="read each allowlisted endpoint once (needs the digest)")
    v.add_argument("--approve-allowlist-sha256", help="allowlist_sha256 the owner approved; required with --live")
    v.add_argument("--budget-dir", type=Path, help="persistent owner-only single-use budget directory; required with --live")
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
