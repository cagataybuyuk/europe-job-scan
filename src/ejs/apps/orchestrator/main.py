from __future__ import annotations

import argparse
import json
from pathlib import Path

from ejs.contracts.freeze import load_contract_freeze


def main() -> int:
    parser = argparse.ArgumentParser(description="Europe Job Scan MW-1 read-shadow bootstrap")
    parser.add_argument("--freeze", default="config/contract_freeze_2026-08-17.json")
    args = parser.parse_args()

    freeze = load_contract_freeze(Path(args.freeze))
    print(json.dumps({
        "freeze_id": freeze.freeze_id,
        "rule_bundle": freeze.active_rule_bundle,
        "write_authority": freeze.write_authority,
        "browser_execution_enabled": freeze.browser_execution_enabled,
        "status": "MW-1 bootstrap ready"
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
