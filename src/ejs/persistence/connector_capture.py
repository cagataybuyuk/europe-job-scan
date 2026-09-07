from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ejs.domain.models import WorkspaceSnapshot


@dataclass(frozen=True)
class ConnectorCaptureWorkspaceReadRepository:
    """Read-only adapter for a connector-captured production snapshot.

    MW-1 uses connector reads against the authoritative Google Sheet, persists only a
    sanitized local capture, and maps it into the canonical WorkspaceSnapshot. It has
    no Google write client and therefore no production mutation path.
    """

    capture_path: Path

    def read_snapshot(self) -> WorkspaceSnapshot:
        raw: dict[str, Any] = json.loads(self.capture_path.read_text(encoding="utf-8"))
        surfaces = raw["surfaces"]
        rule_registry = surfaces["rule_registry"]
        applications = surfaces["applications"]

        rules = tuple(
            {"id": rule_id, "status": "Active"}
            for rule_id in rule_registry["active_rule_ids"]
        ) + tuple(
            {"id": rule_id, "status": "In Development"}
            for rule_id in rule_registry["in_development_rule_ids"]
        )

        application_rows = tuple(
            {"shadow_ordinal": i + 1} for i in range(int(applications["total_tracked"]))
        )

        config = {
            "candidate_profile": surfaces["candidate_profile"],
            "rule_registry": rule_registry,
            "application_metrics": applications,
            "source_targeting": surfaces["source_targeting"],
            "cv_base_library": surfaces["cv_base_library"],
            "application_answer_library": surfaces["application_answer_library"],
            "capture_id": raw["capture_id"],
            "read_fence": raw.get("read_fence", {}),
        }

        return WorkspaceSnapshot(
            source_id=raw["spreadsheet_id"],
            captured_at=raw["captured_at"],
            config=config,
            applications=application_rows,
            rules=rules,
        )
