"""GD-006 step 5: owner-only queue, runtime switch and result files (synthetic)."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.engine_queue import (
    EngineQueue, QueueError, RUNTIME_SCHEMA, _engine_lock, prepare_root, read_switch,
)
from tests.test_execution_manifest import NOW, base_document, encode
from tests.test_policy_application_runtime import PDF, Clock, FakeBrowser, observe_doc, two_page


def private_write(path: Path, data: bytes, mode=0o600):
    path.write_bytes(data)
    os.chmod(path, mode)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "engine-queue"
    cv_dir = tmp_path / "cv"
    cv_dir.mkdir(mode=0o700)
    os.chmod(cv_dir, 0o700)
    private_write(cv_dir / "Synthetic_CV.pdf", PDF)
    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "private" / "ledger.db"), now=NOW)
    doc = base_document()
    doc["cv"].update(sha256=hashlib.sha256(PDF).hexdigest(), size_bytes=len(PDF))
    doc["policy"].update(rollout_stage="R4", daily_cap=None)
    return root, cv_dir, ledger, doc


def make_queue(root, cv_dir, ledger, doc, *, browsers=None, pages=None):
    clock = Clock()
    made = browsers if browsers is not None else []

    def factory(_document):
        browser = FakeBrowser(doc, clock, pages=pages)
        made.append(browser)
        return browser

    return EngineQueue(root, ledger=ledger, cv_dir=cv_dir, browser_factory=factory, clock=clock), made


def enable(root):
    private_write(root / "control" / "runtime.json",
                  json.dumps({"schema_version": RUNTIME_SCHEMA, "final_submit": True}).encode())


def results(root):
    return {p.name: json.loads(p.read_text()) for p in (root / "outbox").iterdir()}


def test_layout_is_owner_only(setup):
    root, *_ = setup
    prepare_root(root)
    for name in ("inbox", "processing", "outbox", "archive", "control"):
        assert oct((root / name).stat().st_mode & 0o777) == "0o700"
    os.chmod(root / "inbox", 0o755)
    with pytest.raises(QueueError):
        prepare_root(root)


def test_switch_is_safe_by_default(setup):
    root, *_ = setup
    prepare_root(root)
    assert read_switch(root).kill_switch_engaged and not read_switch(root).final_submit_enabled
    enable(root)
    switch = read_switch(root)
    assert switch.final_submit_enabled and not switch.kill_switch_engaged
    private_write(root / "control" / "STOP", b"")
    assert read_switch(root).kill_switch_engaged
    os.remove(root / "control" / "STOP")
    os.chmod(root / "control" / "runtime.json", 0o644)
    assert not read_switch(root).final_submit_enabled
    private_write(root / "control" / "runtime.json", b'{"schema_version": "EJS-ENGINE-RUNTIME-1", "final_submit": 1}')
    assert not read_switch(root).final_submit_enabled


def test_enabled_queue_submits_writes_result_and_archives(setup):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "inbox" / "a.json", encode(doc))
    reports = queue.run_once()
    assert [r.status for r in reports] == ["EMPLOYER_CONFIRMED"]
    out = results(root)
    (name, document), = out.items()
    assert name == "exec-20261117-example-1.result.json"
    assert document["status"] == "EMPLOYER_CONFIRMED" and document["submit_clicks"] == 1
    assert document["manifest_sha256"] == hashlib.sha256(encode(doc)).hexdigest()
    text = json.dumps(document)
    assert "synthetic@example.invalid" not in text and "Synthetic" not in text
    assert oct((root / "outbox" / name).stat().st_mode & 0o777) == "0o600"
    assert not list((root / "inbox").iterdir()) and not list((root / "processing").iterdir())
    assert [p.name for p in (root / "archive").iterdir()] == ["a.json"]
    assert made[0].clicks == 1


def test_disabled_queue_never_opens_a_form(setup):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    private_write(root / "inbox" / "a.json", encode(doc))
    private_write(root / "control" / "STOP", b"")
    enable(root)
    assert [r.status for r in queue.run_once()] == ["KILL_SWITCH"]
    assert all(b.calls == [] for b in made)


def test_unsafe_manifest_is_left_untouched(setup):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "inbox" / "a.json", encode(doc), mode=0o644)
    assert [r.status for r in queue.run_once()] == ["SKIPPED_UNSAFE"]
    assert (root / "inbox" / "a.json").exists() and made == []


def test_interrupted_item_is_reported_not_rerun(setup):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "processing" / "a.json", encode(doc))
    reports = queue.run_once()
    assert [(r.status, r.code) for r in reports] == [("INTERRUPTED", "ENGINE_INTERRUPTED")]
    assert made == [] and (root / "archive" / "a.json").exists()
    assert list(results(root).values())[0]["status"] == "INTERRUPTED"


def test_interrupted_after_dispatch_is_reported_as_unconfirmed(setup):
    root, cv_dir, ledger, doc = setup
    ids = dict(opportunity_key="opp:example:sr:744000000000001", requisition_id="REF001",
               profile_version="profile-2026-11-16")
    assert ledger.reserve(**ids, execution_id="exec-20261117-example-1", manifest_sha256="a" * 64,
                          rollout_stage="R4", daily_cap=None, now=NOW) == "RESERVED"
    ledger.mark_started(**ids, execution_id="exec-20261117-example-1", now=NOW)
    queue, made = make_queue(root, cv_dir, ledger, doc)
    private_write(root / "processing" / "a.json", encode(doc))
    assert [r.status for r in queue.run_once()] == ["SUBMITTED_UNCONFIRMED"]
    assert made == []


def test_observe_pass_result_carries_structure_only(setup):
    root, cv_dir, ledger, doc = setup
    full = two_page(doc)
    queue, made = make_queue(root, cv_dir, ledger, observe_doc(full), pages=FakeBrowser._pages_from(full))
    enable(root)
    private_write(root / "inbox" / "a.json", encode(observe_doc(full)))
    assert [r.status for r in queue.run_once()] == ["OBSERVED"]
    document = list(results(root).values())[0]
    assert document["observation"]["step"] == 1
    assert {f["key"] for f in document["observation"]["fields"]} == {"work_auth", "sponsorship", "motivation", "gender"}
    assert "synthetic@example.invalid" not in json.dumps(document)


def test_only_one_engine_at_a_time(setup):
    root, *_ = setup
    prepare_root(root)
    with _engine_lock(root):
        with pytest.raises(QueueError):
            with _engine_lock(root):
                pass


def test_browser_factory_failure_reserves_nothing(setup):
    root, cv_dir, ledger, doc = setup

    def broken(_document):
        raise RuntimeError("no chromium")

    queue = EngineQueue(root, ledger=ledger, cv_dir=cv_dir, browser_factory=broken, clock=Clock())
    enable(root)
    private_write(root / "inbox" / "a.json", encode(doc))
    assert [(r.status, r.code) for r in queue.run_once()] == [("BLOCKED", "ENGINE_BROWSER_UNAVAILABLE")]
    assert ledger.status("opp:example:sr:744000000000001", "REF001", "profile-2026-11-16") is None


def test_odd_names_are_ignored(setup):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "inbox" / "notes.txt", b"x")
    private_write(root / "inbox" / ".hidden.json", encode(doc))
    assert queue.run_once() == [] and made == []


def test_cli_prepare_creates_owner_only_layout(tmp_path, monkeypatch, capsys):
    import ejs.apps.policy_engine.__main__ as cli
    monkeypatch.setattr(cli, "QUEUE_ROOT", tmp_path / "engine-queue")
    assert cli.main(["--prepare"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "PREPARED"}
    assert oct((tmp_path / "engine-queue" / "control").stat().st_mode & 0o777) == "0o700"
