from __future__ import annotations

import json
from pathlib import Path

from ejs.domain.models import ContractFreeze


def load_contract_freeze(path: str | Path) -> ContractFreeze:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    freeze = ContractFreeze(
        freeze_id=data["freeze_id"],
        active_rule_bundle=data["active_rule_bundle"],
        active_contract_count=int(data["active_contract_count"]),
        regression_fixture_count=int(data["regression_fixture_count"]),
        write_authority=data["write_authority"],
        browser_execution_enabled=bool(data["browser_execution_enabled"]),
    )
    if freeze.write_authority != "NONE":
        raise ValueError("MW-0/MW-1 freeze must have write_authority=NONE")
    if freeze.browser_execution_enabled:
        raise ValueError("Browser execution must remain disabled during MW-0/MW-1")
    return freeze
