from __future__ import annotations

import argparse
import json
from pathlib import Path

from ejs.apps.github_runner.canary_policy import CanaryEvidence, validate_r2_release_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the manual R2 single-submit release gate")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--approval-phrase", required=True)
    parser.add_argument("--second-approver", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    manifest = validate_r2_release_gate(CanaryEvidence.from_dict(evidence), expected_sha=args.expected_sha, approval_phrase=args.approval_phrase, second_approver=args.second_approver)
    payload = json.dumps(manifest, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
