"""FUNCTION-04 offline suitability run. No network, no browser, no form access, no submission.

Reads a Function 02 report (verified projection, or a live expansion report that is projected with the
unchanged ``project_for_application_bridge``), owner criteria and optional already-collected posting details,
then writes an owner-only prioritized report outside the repository. Prints counts only.

Exit codes: 0 written, 2 usage or input problem (nothing written).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
import sys
from hashlib import sha256
from pathlib import Path

from ejs.services.criteria_loader import CriteriaError, load_criteria
from ejs.services.expansion_shadow import SCHEMA as EXPANSION_SCHEMA, project_for_application_bridge
from ejs.services.owner_only_files import OutputDirError, prepare_output_dir, write_private_file
from ejs.services.suitability import SuitabilityError, evaluate_report, project_for_bridge
from ejs.services.suitability_evidence import detail_from_smartrecruiters

REPORT_NAME = "suitability-report.json"
BRIDGE_NAME = "suitability-bridge.json"


def _load_details(directory: Path, rows: list[dict]) -> dict:
    """Files named ``<tenant>__<posting_id>.json`` hold raw posting bytes collected earlier."""
    details: dict = {}
    for row in rows:
        tenant, posting = row.get("tenant"), row.get("posting_id")
        if not tenant or not posting:
            continue
        path = directory / f"{tenant}__{posting}.json"
        if path.is_file() and not path.is_symlink():
            try:
                details[(tenant, posting)] = detail_from_smartrecruiters(path.read_bytes(), tenant, posting)
            except (ValueError, OSError):
                continue          # counted as unmatched by the evaluator; never guessed
    return details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True, help="absolute owner-only directory outside the repo")
    parser.add_argument("--details-dir", type=Path)
    parser.add_argument("--reviewer-ratings", type=Path, help="JSON: {'tenant/posting': {'scope': {'rating': n, 'ref': s}}}")
    parser.add_argument("--expected-criteria-sha256", help="independently recorded SHA-256 of the approved criteria file")
    parser.add_argument("--expected-rule-version", help="independently recorded rule version of the approved criteria")
    args = parser.parse_args(argv)
    try:
        out_dir = prepare_output_dir(args.out_dir)
        if any((out_dir / n).exists() for n in (REPORT_NAME, BRIDGE_NAME)):
            raise OutputDirError("OUTPUT_EXISTS")
        criteria_bytes = args.criteria.read_bytes()
        criteria = load_criteria(args.criteria)
        actual_sha = sha256(criteria_bytes).hexdigest()
        if args.expected_criteria_sha256 and actual_sha != args.expected_criteria_sha256:
            raise ValueError("CRITERIA_DIGEST_MISMATCH")      # a different file than the approved one: do not evaluate
        report = json.loads(args.report.read_bytes())
        if isinstance(report, dict) and report.get("schema") == EXPANSION_SCHEMA:
            report = project_for_application_bridge(report)
        rows = report.get("vacancies", []) if isinstance(report, dict) else []
        details = _load_details(args.details_dir, rows) if args.details_dir else {}
        reviewer = {}
        if args.reviewer_ratings:
            reviewer = {tuple(k.split("/", 1)): v for k, v in json.loads(args.reviewer_ratings.read_text()).items()}
        result = evaluate_report(report, criteria, details=details, reviewer_ratings=reviewer,
                                 criteria_sha256=actual_sha,
                                 expected_criteria_sha256=args.expected_criteria_sha256,
                                 expected_rule_version=args.expected_rule_version)
        payload = (json.dumps(result, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()
        projected = project_for_bridge(result)
        bridge = (json.dumps(projected, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()
    except (CriteriaError, SuitabilityError, OutputDirError, ValueError, OSError) as exc:
        print(f"function04: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    write_private_file(out_dir, REPORT_NAME, payload)
    write_private_file(out_dir, BRIDGE_NAME, bridge)
    print(json.dumps({"listings_kept": result["listings_kept"], "duplicate_rows": result["duplicate_rows"],
                      "evidence_levels": result["evidence_level_distribution"],
                      "classifications": result["classification_distribution"],
                      "criteria_approval": result["criteria"]["approval"],
                      "profile_gaps": result["criteria"]["profile_gaps"],
                      "missing_evidence": result["missing_evidence_distribution"],
                      "review_flags": dict(sorted(Counter(f for v in result["vacancies"]
                                                          for f in v["suitability"]["review_flags"]).items())),
                      "preliminary_signals": dict(sorted(Counter(f for v in result["vacancies"]
                                                                 for f in v["suitability"]["preliminary_signals"]).items())),
                      "exclusions": projected["excluded_rows"],
                      "downstream_candidates": projected["listings_kept"],
                      "handoff_ready": result["handoff_ready"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
