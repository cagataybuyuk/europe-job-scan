import ast
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ejs.services import autoreview, criteria_loader, csv_export, dedupe, opportunity_score, owner_only_files, queue_diagnostics
from ejs.services.autoreview import ListingFacts, decide
from ejs.services.dedupe import dedupe_key, dedupe_listings
from ejs.services.queue_diagnostics import (DiagnosticsError, QueueSnapshot, build_daily_summary, classify_queue,
                                            feeder_alert, is_feeder_dead, source_health, write_daily_summary)
from tests.unit.phase1a_support import criteria, listing_rows, uniform

C = criteria()
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def snap(**kw):
    base = dict(queue_depth=0, paused=False, last_discover_at=NOW - timedelta(hours=2),
                last_successful_discover_at=NOW - timedelta(hours=2), consecutive_zero_runs=0,
                discovered=5, filtered=5, needs_user=0, applied_label=0, awaiting_user_backlog=0)
    base.update(kw)
    return QueueSnapshot(**base)


def test_dedupe_fixture_12_listings():
    rows = listing_rows()
    assert len(rows) >= 10
    res = dedupe_listings(rows)
    assert [r["id"] for r in res.kept] == ["L00", "L01", "L02", "L04", "L05", "L07", "L08", "L10"]
    assert [(r["id"], why) for r, why in res.duplicates] == [("L03", "duplicate_in_batch"), ("L06", "duplicate_in_batch"), ("L09", "duplicate_in_batch")]
    assert [r["id"] for r in res.unkeyed] == ["L11"]


def test_dedupe_against_existing_and_stable():
    rows = listing_rows()
    first = dedupe_listings(rows)
    again = dedupe_listings(rows, existing_keys=first.new_keys)
    assert again.kept == () and len(again.duplicates) == 11
    assert {why for _, why in again.duplicates} == {"duplicate_of_existing"}
    assert dedupe_listings(rows) == first
    assert dedupe_key("Acme GmbH", "Dev (m/w/d)", "Berlin, X") == dedupe_key("acme", "dev", "berlin")
    assert dedupe_key("A", "", "B") is None


def test_queue_empty_never_done_and_reasons():
    assert classify_queue(snap(queue_depth=2), NOW, C).status == "QUEUE_NONEMPTY"
    cases = [
        (snap(paused=True, discovered=0), "paused"),
        (snap(last_successful_discover_at=NOW - timedelta(hours=37)), "feeder_dead"),
        (snap(last_successful_discover_at=None), "feeder_dead"),
        (snap(consecutive_zero_runs=3, discovered=0), "feeder_dead"),
        (snap(awaiting_user_backlog=4), "awaiting_user_backlog"),
        (snap(), "all_filtered"),
        (snap(discovered=0, filtered=0), "no_new_listings"),
    ]
    for s, reason in cases:
        r = classify_queue(s, NOW, C)
        assert r.status == "QUEUE_EMPTY" and r.status != "DONE" and r.reason == reason, (reason, r)
    assert classify_queue(snap(paused=True, last_successful_discover_at=None), NOW, C).reason == "paused"


def test_feeder_dead_threshold_boundary_injected_clock():
    assert not is_feeder_dead(NOW - timedelta(hours=36), 0, NOW, C)
    assert is_feeder_dead(NOW - timedelta(hours=36, seconds=1), 0, NOW, C)
    assert not is_feeder_dead(NOW, 2, NOW, C) and is_feeder_dead(NOW, 3, NOW, C)
    with pytest.raises(DiagnosticsError):
        is_feeder_dead(NOW, 0, datetime(2026, 11, 21), C)


def test_per_source_alert_is_immediate_and_independent_of_summary():
    h = source_health({"src_a": NOW - timedelta(hours=1), "src_b": NOW - timedelta(hours=80), "src_c": None}, NOW, C)
    assert h == {"src_a": False, "src_b": True, "src_c": True}
    a = feeder_alert(h, False)
    assert a is not None and a.immediate and a.dead_sources == ("src_b", "src_c")
    assert feeder_alert({"src_a": False}, False) is None


def test_missing_health_thresholds_fail_closed():
    from dataclasses import replace
    with pytest.raises(DiagnosticsError, match="HEALTH"):
        is_feeder_dead(NOW, 0, NOW, replace(C, feeder_dead_after_hours=None))


def test_daily_summary_on_quiet_day_no_candidate_data(tmp_path):
    rep = classify_queue(snap(discovered=0, filtered=0), NOW, C)
    s = build_daily_summary(date(2026, 11, 21), discovered=0, filtered=0, applied_label=0, needs_user=0,
                            queue_report=rep, source_health_map={"src_a": False})
    assert s["no_events"] and s["queue_status"] == "QUEUE_EMPTY"
    p = write_daily_summary(tmp_path / "queue" / "out", s)
    assert p.read_text().startswith("# EJS daily summary 2026-11-21") and "no events today" in p.read_text()
    assert (p.stat().st_mode & 0o777) == 0o600 and (p.parent.stat().st_mode & 0o777) == 0o700
    s2 = build_daily_summary(date(2026, 11, 21), discovered=3, filtered=1, applied_label=1, needs_user=1,
                             queue_report=rep, source_health_map={}, needs_user_items=(("L05", "contradiction"),))
    assert s2["needs_user_items"] == [{"listing_id": "L05", "reason_code": "contradiction"}]
    with pytest.raises(DiagnosticsError):
        build_daily_summary(date(2026, 11, 21), discovered=-1, filtered=0, applied_label=0, needs_user=0, queue_report=rep, source_health_map={})


def test_output_only_in_given_dir_outside_repo_and_not_control(tmp_path):
    rep = classify_queue(snap(), NOW, C)
    s = build_daily_summary(date(2026, 11, 21), discovered=1, filtered=1, applied_label=0, needs_user=0, queue_report=rep, source_health_map={})
    with pytest.raises(PermissionError):
        write_daily_summary(owner_only_files.REPO_ROOT / "out", s)
    with pytest.raises(PermissionError):
        write_daily_summary(tmp_path / "engine-queue" / "control", s)
    with pytest.raises(PermissionError):
        write_daily_summary("relative/dir", s)
    assert not (owner_only_files.REPO_ROOT / "out").exists()
    link = tmp_path / "link"; link.symlink_to(tmp_path)
    with pytest.raises(PermissionError):
        write_daily_summary(link, s)
    d = tmp_path / "q"
    write_daily_summary(d, s)
    assert sorted(p.name for p in d.iterdir()) == ["daily_summary_2026-11-21.md"]


def test_end_to_end_offline_with_network_blocked(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")
    for name in ("connect", "connect_ex", "bind", "sendto"):
        monkeypatch.setattr(socket.socket, name, boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    kept = dedupe_listings(listing_rows()).kept
    labels = [decide(ListingFacts(r["id"], uniform(60 + 4 * i), vacancy_open=True), C).label for i, r in enumerate(kept)]
    assert labels == ["auto_skip", "auto_skip", "auto_apply", "auto_apply", "auto_apply", "auto_apply", "auto_apply", "auto_apply"]


def test_modules_import_no_network_libraries():
    banned = {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "ssl", "smtplib", "gspread", "googleapiclient", "playwright"}
    for mod in (criteria_loader, opportunity_score, autoreview, dedupe, queue_diagnostics, csv_export, owner_only_files):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.split(".")[0] in banned for n in names), (mod.__name__, names)


def test_summary_parent_symlink_component_rejected(tmp_path):
    actual = tmp_path / "actual"; actual.mkdir()
    (tmp_path / "alias").symlink_to(actual)
    rep = classify_queue(snap(), NOW, C)
    s = build_daily_summary(date(2026, 11, 21), discovered=0, filtered=0, applied_label=0, needs_user=0, queue_report=rep, source_health_map={})
    with pytest.raises(PermissionError):
        write_daily_summary(tmp_path / "alias" / "out", s)
    assert not (actual / "out").exists()
