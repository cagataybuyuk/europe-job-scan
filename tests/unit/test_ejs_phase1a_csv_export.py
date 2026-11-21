import ast
import csv
import socket
from pathlib import Path

import pytest

from ejs.services import csv_export, owner_only_files
from ejs.services.csv_export import (APPLICATION_COLUMNS, ExportError, export_applications, export_daily_summary,
                                     export_outcome_events)

ROW = {"application_id": "A1", "company": "Acme", "role_title": "Dev", "location": "Berlin", "state": "triaged",
       "score": 70.5, "decision": "auto_apply", "reason_code": "score_at_or_above_min", "rule_version": "R1"}


def test_applications_csv_format_and_permissions(tmp_path):
    out = tmp_path / "export"
    p = export_applications(out, [ROW, {**ROW, "application_id": "A2"}])
    assert (p.stat().st_mode & 0o777) == 0o600 and (out.stat().st_mode & 0o777) == 0o700
    lines = p.read_text().splitlines()
    assert lines[0] == "# export_version=EJS-EXPORT-1"
    rows = list(csv.reader(lines[1:]))
    assert tuple(rows[0]) == APPLICATION_COLUMNS and len(rows) == 3
    assert rows[1][0] == "A1" and rows[1][APPLICATION_COLUMNS.index("score")] == "70.5"


def test_regenerated_each_time_and_other_files(tmp_path):
    export_applications(tmp_path, [ROW])
    p = export_applications(tmp_path, [])
    assert len(p.read_text().splitlines()) == 2
    export_outcome_events(tmp_path, [{"source_kind": "csv", "source_id": "m1", "event_class": "received"}])
    export_daily_summary(tmp_path, [{"date": "2026-11-21", "discovered": 0}])
    assert sorted(x.name for x in tmp_path.iterdir()) == ["applications.csv", "daily_summary.csv", "outcome_events.csv"]


def test_unknown_or_personal_columns_rejected_before_any_write(tmp_path):
    out = tmp_path / "e"
    with pytest.raises(ExportError, match="UNKNOWN_COLUMN"):
        export_applications(out, [{**ROW, "phone": "x"}])
    assert not out.exists()


def test_formula_cells_neutralized(tmp_path):
    p = export_applications(tmp_path, [{**ROW, "company": "=HYPERLINK(\"x\")", "role_title": "+1", "location": "a\nb"}])
    r = list(csv.reader(p.read_text().splitlines()[1:]))
    assert r[1][1] == "'=HYPERLINK(\"x\")" and r[1][2] == "'+1" and r[1][3] == "a b"


def test_refuses_repo_and_relative_dirs(tmp_path):
    with pytest.raises(PermissionError):
        export_applications(owner_only_files.REPO_ROOT / "export", [ROW])
    with pytest.raises(PermissionError):
        export_applications("export", [ROW])


def test_no_sheet_write_path_negative(monkeypatch, tmp_path):
    src = Path(csv_export.__file__).read_text().lower()
    for word in ("apps_script", "gspread", "spreadsheet", "sheets.googleapis", "urllib", "requests", "socket", "values().update"):
        assert word not in src
    tree = ast.parse(Path(csv_export.__file__).read_text())
    imported = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert imported <= {"__future__", "csv", "io", "pathlib", "typing", "ejs"}
    assert not [n for n in dir(csv_export) if "sheet" in n.lower() or "upload" in n.lower() or "post" in n.lower()]

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    export_applications(tmp_path / "ok", [ROW])


def test_parent_symlink_component_rejected(tmp_path):
    actual = tmp_path / "actual"; actual.mkdir()
    (tmp_path / "alias").symlink_to(actual)
    with pytest.raises(PermissionError):
        export_applications(tmp_path / "alias" / "out", [])
    assert not (actual / "out").exists()
