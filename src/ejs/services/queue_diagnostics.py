"""QUEUE_EMPTY diagnosis, feeder-dead alert and daily summary (ROADMAP Faz 1.6). Pure.

An empty queue is never "DONE": it is ``QUEUE_EMPTY`` plus a reason class. Clock is
injected (``now``, timezone-aware). Summaries contain counts, listing ids and reason
codes only - no candidate data. This module does NOT touch ``engine_queue.py``.
Reason classes: feeder_dead | all_filtered | awaiting_user_backlog | paused, plus
``no_new_listings`` (feeder alive, zero discovered, below the dead threshold) - a
deliberate addition so an unexplained empty queue is never mislabeled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

from ejs.services.criteria_loader import Criteria
from ejs.services.owner_only_files import prepare_output_dir, write_private_file

REASONS = ("paused", "feeder_dead", "awaiting_user_backlog", "all_filtered", "no_new_listings")


class DiagnosticsError(ValueError):
    pass


@dataclass(frozen=True)
class QueueSnapshot:
    queue_depth: int
    paused: bool
    last_discover_at: datetime | None
    last_successful_discover_at: datetime | None
    consecutive_zero_runs: int
    discovered: int
    filtered: int
    needs_user: int
    applied_label: int            # listings labeled apply this round
    awaiting_user_backlog: int


@dataclass(frozen=True)
class QueueReport:
    status: str                   # QUEUE_EMPTY | QUEUE_NONEMPTY (never "DONE")
    reason: str | None
    feeder_dead: bool
    detail: Mapping[str, object]


def _aware(dt: datetime | None, name: str) -> None:
    if dt is not None and (dt.tzinfo is None or dt.utcoffset() is None):
        raise DiagnosticsError(f"NAIVE_DATETIME:{name}")


def _require_health(criteria: Criteria) -> tuple[float, int]:
    if criteria.feeder_dead_after_hours is None or criteria.feeder_dead_after_zero_runs is None:
        raise DiagnosticsError("HEALTH_THRESHOLDS_MISSING")   # fail-closed: no guessed defaults
    return criteria.feeder_dead_after_hours, criteria.feeder_dead_after_zero_runs


def is_feeder_dead(last_success: datetime | None, zero_runs: int, now: datetime, criteria: Criteria) -> bool:
    hours, runs = _require_health(criteria)
    _aware(now, "now"); _aware(last_success, "last_success")
    if last_success is None or now - last_success > timedelta(hours=hours):
        return True
    return zero_runs >= runs


def source_health(last_success_by_source: Mapping[str, datetime | None], now: datetime,
                  criteria: Criteria) -> dict[str, bool]:
    """Per-source aging; True = dead. One dead source stays visible."""
    return {s: is_feeder_dead(t, 0, now, criteria) for s, t in sorted(last_success_by_source.items())}


def classify_queue(snapshot: QueueSnapshot, now: datetime, criteria: Criteria) -> QueueReport:
    _aware(now, "now"); _aware(snapshot.last_discover_at, "last_discover_at")
    dead = is_feeder_dead(snapshot.last_successful_discover_at, snapshot.consecutive_zero_runs, now, criteria)
    detail = {"last_discover_at": snapshot.last_discover_at.isoformat() if snapshot.last_discover_at else None,
              "discovered": snapshot.discovered, "filtered": snapshot.filtered,
              "needs_user": snapshot.needs_user, "apply": snapshot.applied_label,
              "awaiting_user_backlog": snapshot.awaiting_user_backlog}
    if snapshot.queue_depth > 0:
        return QueueReport("QUEUE_NONEMPTY", None, dead, detail)
    if snapshot.paused:
        reason = "paused"
    elif dead:
        reason = "feeder_dead"
    elif snapshot.awaiting_user_backlog > 0:
        reason = "awaiting_user_backlog"
    elif snapshot.discovered > 0 and snapshot.filtered >= snapshot.discovered:
        reason = "all_filtered"
    else:
        reason = "no_new_listings"
    return QueueReport("QUEUE_EMPTY", reason, dead, detail)


@dataclass(frozen=True)
class FeederAlert:
    immediate: bool
    dead_sources: tuple[str, ...]


def feeder_alert(health: Mapping[str, bool], overall_dead: bool, *, notify_immediately: bool = True) -> FeederAlert | None:
    dead = tuple(s for s, d in sorted(health.items()) if d)
    if not dead and not overall_dead:
        return None
    return FeederAlert(immediate=bool(notify_immediately), dead_sources=dead)   # independent of daily summary


def build_daily_summary(day: date, *, discovered: int, filtered: int, applied_label: int, needs_user: int,
                        queue_report: QueueReport, source_health_map: Mapping[str, bool],
                        needs_user_items: tuple[tuple[str, str], ...] = ()) -> dict:
    """Always built, also on a day with no events. Items are (listing_id, reason_code) only."""
    for n in (discovered, filtered, applied_label, needs_user):
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise DiagnosticsError("SUMMARY_COUNT_INVALID")
    return {"date": day.isoformat(), "discovered": discovered, "filtered": filtered,
            "apply_label": applied_label, "needs_user": needs_user,
            "queue_status": queue_report.status, "queue_empty_reason": queue_report.reason,
            "feeder_dead": queue_report.feeder_dead,
            "sources": {s: ("dead" if d else "ok") for s, d in sorted(source_health_map.items())},
            "needs_user_items": [{"listing_id": i, "reason_code": r} for i, r in needs_user_items],
            "no_events": discovered == filtered == applied_label == needs_user == 0}


def render_summary_markdown(summary: Mapping[str, object]) -> str:
    lines = [f"# EJS daily summary {summary['date']}", "",
             f"- discovered: {summary['discovered']}", f"- filtered: {summary['filtered']}",
             f"- apply label: {summary['apply_label']}", f"- needs_user: {summary['needs_user']}",
             f"- queue: {summary['queue_status']}" + (f" ({summary['queue_empty_reason']})" if summary["queue_empty_reason"] else ""),
             f"- feeder_dead: {summary['feeder_dead']}"]
    if summary["no_events"]:
        lines.append("- no events today")
    for s, st in summary["sources"].items():  # type: ignore[union-attr]
        lines.append(f"- source {s}: {st}")
    for it in summary["needs_user_items"]:  # type: ignore[union-attr]
        lines.append(f"- needs_user {it['listing_id']}: {it['reason_code']}")
    return "\n".join(lines) + "\n"


def write_daily_summary(out_dir: str | Path, summary: Mapping[str, object],
                        *, forbidden_roots: tuple[Path, ...] | None = None) -> Path:
    d = prepare_output_dir(out_dir) if forbidden_roots is None else prepare_output_dir(out_dir, forbidden_roots=forbidden_roots)
    return write_private_file(d, f"daily_summary_{summary['date']}.md", render_summary_markdown(summary).encode("utf-8"))
