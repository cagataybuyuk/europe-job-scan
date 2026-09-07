from __future__ import annotations

import argparse
import json
from pathlib import Path

from ejs.contracts.freeze import load_contract_freeze
from ejs.persistence.connector_capture import ConnectorCaptureWorkspaceReadRepository
from ejs.services.parity import build_mw1_parity_report
from ejs.services.shadow import ShadowRunService


def main() -> int:
    parser = argparse.ArgumentParser(description="Europe Job Scan MW-1 production-input read shadow")
    parser.add_argument("--freeze", default="config/contract_freeze_2026-08-17.json")
    parser.add_argument("--capture", default="config/live_connector_capture_2026-08-17.json")
    parser.add_argument("--run-id", default="MW1-SHADOW-20260817-01")
    args = parser.parse_args()

    freeze = load_contract_freeze(Path(args.freeze))
    repo = ConnectorCaptureWorkspaceReadRepository(Path(args.capture))
    snapshot = repo.read_snapshot()
    shadow = ShadowRunService(repo).run(args.run_id)
    parity = build_mw1_parity_report(snapshot, freeze)

    output = {
        "run_id": args.run_id,
        "capture_id": parity.capture_id,
        "application_count": shadow.application_count,
        "active_rule_count": shadow.active_rule_count,
        "write_attempts": shadow.write_attempts,
        "critical_parity_pass_rate": parity.pass_rate,
        "passed": parity.passed,
        "checks": [
            {"name": c.name, "expected": c.expected, "observed": c.observed, "passed": c.passed}
            for c in parity.checks
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if parity.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
