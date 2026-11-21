"""Read-only CSV export (ROADMAP Faz 1.8, ARCHITECTURE §5). Ledger rows -> local CSV files.

One-way: this module writes owner-only files into a caller-supplied repo-external
directory. There is no Sheet/Apps Script/network code path. Columns are a fixed
whitelist (unknown columns are an error, so personal data cannot slip in); text cells
that could be read as formulas are neutralized.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable, Mapping

from ejs.services.owner_only_files import prepare_output_dir, write_private_file

EXPORT_VERSION = "EJS-EXPORT-1"
APPLICATION_COLUMNS = ("application_id", "company", "role_title", "location", "source_kind", "ats_family",
                       "state", "score", "decision", "reason_code", "cv_id", "submitted_at",
                       "last_event_class", "last_event_at", "needs_user_reason", "rule_version")
OUTCOME_COLUMNS = ("source_kind", "source_id", "application_id", "event_class", "occurred_at",
                   "match_confidence", "conflict")
SUMMARY_COLUMNS = ("date", "discovered", "filtered", "apply_label", "needs_user", "queue_status",
                   "queue_empty_reason", "feeder_dead")


class ExportError(ValueError):
    pass


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t") else text


def render_csv(columns: tuple[str, ...], rows: Iterable[Mapping[str, object]]) -> bytes:
    buf = io.StringIO()
    buf.write(f"# export_version={EXPORT_VERSION}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        extra = set(row) - set(columns)
        if extra:
            raise ExportError(f"EXPORT_UNKNOWN_COLUMN:{','.join(sorted(extra))}")
        w.writerow([_cell(row.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8")


def _export(out_dir: str | Path, name: str, columns: tuple[str, ...], rows: Iterable[Mapping[str, object]]) -> Path:
    data = render_csv(columns, rows)              # validate before touching the filesystem
    return write_private_file(prepare_output_dir(out_dir), name, data)


def export_applications(out_dir: str | Path, rows: Iterable[Mapping[str, object]]) -> Path:
    return _export(out_dir, "applications.csv", APPLICATION_COLUMNS, rows)


def export_outcome_events(out_dir: str | Path, rows: Iterable[Mapping[str, object]]) -> Path:
    return _export(out_dir, "outcome_events.csv", OUTCOME_COLUMNS, rows)


def export_daily_summary(out_dir: str | Path, rows: Iterable[Mapping[str, object]]) -> Path:
    return _export(out_dir, "daily_summary.csv", SUMMARY_COLUMNS, rows)
