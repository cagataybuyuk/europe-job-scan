from __future__ import annotations

import argparse
import json

from ejs.contracts.mw3 import Mw3Authority
from ejs.domain.append_only import AppendOnlyDomain
from ejs.persistence.append_only import InMemoryAppendOnlyWriteRepository
from ejs.services.mw3_canary import Mw3CanaryService


def main() -> int:
    parser = argparse.ArgumentParser(description="Europe Job Scan MW-3 append-only canary")
    parser.add_argument("--run-id", default="MW3-CANARY-20260817-01")
    args = parser.parse_args()

    authority = Mw3Authority(
        enabled=True,
        allowed_domains=frozenset({AppendOnlyDomain.SERVICE_CANARY_AUDIT}),
    )
    repo = InMemoryAppendOnlyWriteRepository(authority)
    report = Mw3CanaryService(repo).run(args.run_id)
    duplicate = Mw3CanaryService(repo).run(args.run_id)
    output = {
        "run_id": report.run_id,
        "first_append": report.appended,
        "retry_duplicate_suppressed": duplicate.duplicate_suppressed,
        "reconciliation_gap": report.reconciliation_gap,
        "business_state_mutations": report.business_state_mutations,
        "external_form_mutations": report.external_form_mutations,
        "passed": report.passed and duplicate.passed,
    }
    print(json.dumps(output, indent=2))
    return 0 if output["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
