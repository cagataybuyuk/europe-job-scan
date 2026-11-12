"""Same-database migration, permanent preparation claims and scoped durability."""
from dataclasses import replace
from copy import copy
from datetime import timedelta
import multiprocessing
import os
import sqlite3
from threading import Thread

import pytest

from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence import reviewed_scope_ledger as module
from ejs.persistence.reviewed_scope_ledger import (
    ReviewedScopeLedger, claim_reviewed_preparation, register_reviewed_execution_scope,
)
from ejs.persistence.submission_ledger import SqliteSubmissionLedger, SubmissionLedgerError
from tests.test_reviewed_execution_scope import NOW, execution_case, issue_scope


def legacy_attempt():
    return SubmissionAttempt(submit_key="submit:legacy", execution_id="exec:legacy", opportunity_key="opportunity:legacy",
        requisition_id="REF7089X", candidate_profile_version="fixture:v1", policy_version="SUBMIT-1.0",
        validation_key="validation:legacy", requested_at=(NOW - timedelta(days=5)).isoformat(),
        status=SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:legacy")


def make_legacy(path):
    ledger = SqliteSubmissionLedger(path)
    before = NOW - timedelta(days=5)
    assert ledger.reserve_intent(legacy_attempt(), before)
    ledger.mark_started("submit:legacy", "exec:legacy", before + timedelta(seconds=1))
    assert ledger.confirm("submit:legacy", ConfirmationEvidence(submit_key="submit:legacy",
        evidence_type=ConfirmationType.ATS_ACKNOWLEDGEMENT, evidence_ref="evidence:sha256:" + "a" * 64,
        observed_at=(before + timedelta(seconds=2)).isoformat(), exact_identity_match=True,
        execution_id="exec:legacy", requisition_id="REF7089X"), before + timedelta(seconds=3))
    return ledger


def attempt(**changes):
    return replace(SubmissionAttempt(submit_key="submit:fixture:scope2", execution_id="fixture:execution:second",
        opportunity_key="opportunity:fixture:scope2", requisition_id="REF00063", candidate_profile_version="fixture:v2",
        policy_version="SUBMIT-1.0", validation_key="validation:scope2", requested_at=(NOW + timedelta(seconds=1)).isoformat(),
        status=SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:scope2"), **changes)


def proof(**changes):
    return replace(ConfirmationEvidence(submit_key="submit:fixture:scope2", execution_id="fixture:execution:second",
        requisition_id="REF00063", evidence_type=ConfirmationType.ATS_ACKNOWLEDGEMENT,
        evidence_ref="evidence:sha256:" + "b" * 64, observed_at=(NOW + timedelta(seconds=3)).isoformat(),
        exact_identity_match=True), **changes)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    path = tmp_path / "private" / "first-application.db"
    legacy = make_legacy(path)
    case = execution_case(legacy.snapshot())
    scope = issue_scope(case)
    monkeypatch.setattr(module, "_now", lambda: NOW)
    return path, legacy, case, scope


def migrate_and_claim(fixture):
    path, legacy, case, scope = fixture
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)
    view = claim_reviewed_preparation(legacy, scope, now=NOW)
    assert view is not None
    return view


def rows(path, table="first_application"):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT * FROM " + table).fetchall()


def rejected(fn):
    with pytest.raises(SubmissionLedgerError) as caught:
        fn()
    assert str(caught.value) == "REVIEWED_SCOPE_LEDGER_REJECTED"


def test_explicit_same_db_migration_preserves_legacy_row_and_old_view(fixture):
    path, legacy, case, scope = fixture
    original = rows(path)
    snapshot = legacy.snapshot()
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)
    assert rows(path) == original
    reopened = SqliteSubmissionLedger(path)
    assert reopened.snapshot() == snapshot
    assert reopened.has_intent()
    assert not reopened.reserve_intent(legacy_attempt(), NOW)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not register_reviewed_execution_scope(legacy, scope, now=NOW)


def test_no_automatic_migration_or_replacement_budget(fixture):
    path, legacy, case, scope = fixture
    rejected(lambda: claim_reviewed_preparation(legacy, scope, now=NOW))
    rejected(lambda: register_reviewed_execution_scope(legacy, True, now=NOW))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall() == [("first_application",)]
    assert legacy.snapshot()["status"] == "Confirmed"


def test_migration_failure_rolls_back_schema_and_grant_atomically(fixture, monkeypatch):
    path, legacy, case, scope = fixture
    original = rows(path)
    real = module.check_reviewed_scope_extension
    monkeypatch.setattr(module, "check_reviewed_scope_extension", lambda c: (_ for _ in ()).throw(RuntimeError("SYNTHETIC PRIVATE ERROR")))
    rejected(lambda: register_reviewed_execution_scope(legacy, scope, now=NOW))
    monkeypatch.setattr(module, "check_reviewed_scope_extension", real)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert len(connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()) == 1
    assert rows(path) == original
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)


def test_preparation_is_permanent_and_only_owner_sees_unused_submit_intent(fixture):
    path, legacy, case, scope = fixture
    view = migrate_and_claim(fixture)
    assert type(view) is ReviewedScopeLedger and view.path == path
    assert not view.has_intent() and view.snapshot() is None
    assert rows(path, "reviewed_scope_attempts")[0][2] == "Preparing"
    assert claim_reviewed_preparation(SqliteSubmissionLedger(path), issue_scope(case), now=NOW) is None
    assert legacy.has_intent() and legacy.snapshot()["status"] == "Confirmed"
    assert view.reserve_intent(attempt(), NOW + timedelta(seconds=1))
    assert view.has_intent()
    assert not view.reserve_intent(attempt(), NOW + timedelta(seconds=1))


def test_same_scope_confirms_once_and_preserves_legacy(fixture):
    path, legacy, case, scope = fixture
    old = rows(path)
    view = migrate_and_claim(fixture)
    assert view.reserve_intent(attempt(), NOW + timedelta(seconds=1))
    view.mark_started("submit:fixture:scope2", "fixture:execution:second", NOW + timedelta(seconds=2))
    assert view.snapshot()["status"] == "Submitted / Unconfirmed"
    assert view.confirm("submit:fixture:scope2", proof(), NOW + timedelta(seconds=4))
    assert view.confirm("submit:fixture:scope2", proof(), NOW + timedelta(seconds=5))
    assert view.snapshot()["status"] == "Confirmed"
    assert rows(path) == old
    assert legacy.snapshot()["status"] == "Confirmed"
    assert claim_reviewed_preparation(legacy, scope, now=NOW) is None


@pytest.mark.parametrize("change", [dict(scope_id="fixture:other"), dict(candidate_profile_version="fixture:v3"),
    dict(execution_id="fixture:other"), dict(cv_sha256="d" * 64), dict(source_sha="b" * 40)])
def test_new_grant_profile_source_cannot_replace_second_slot(fixture, change):
    path, legacy, case, scope = fixture
    migrate_and_claim(fixture)
    case["grant"].update(change)
    for key in ("candidate_profile_version", "execution_id", "source_sha"):
        if key in change:
            case["manifest"][key] = change[key]
    if "candidate_profile_version" in change:
        case["facts"]["candidate_profile_version"] = change["candidate_profile_version"]
    if "cv_sha256" in change:
        case["manifest"]["cv"]["sha256"] = change["cv_sha256"]
    new_scope = issue_scope(case, source_sha=change.get("source_sha", "a" * 40))
    rejected(lambda: register_reviewed_execution_scope(legacy, new_scope, now=NOW))
    assert len(rows(path, "reviewed_scope_grants")) == 1
    assert len(rows(path, "reviewed_scope_attempts")) == 1


@pytest.mark.parametrize("change", [dict(execution_id="fixture:other"), dict(candidate_profile_version="fixture:v3"),
    dict(requisition_id="REF7089X"), dict(submit_key="submit:legacy"), dict(status=SubmissionAttemptStatus.REQUESTED)])
def test_scope_attempt_cannot_change_reviewed_identity_or_reuse_old_submit_key(fixture, change):
    view = migrate_and_claim(fixture)
    rejected(lambda: view.reserve_intent(attempt(**change), NOW + timedelta(seconds=1)))
    assert view.snapshot() is None


@pytest.mark.parametrize("change", [dict(exact_identity_match=False), dict(execution_id="fixture:other"),
    dict(requisition_id="REF7089X"), dict(evidence_ref="SYNTHETIC PRIVATE BODY"),
    dict(observed_at=(NOW - timedelta(seconds=1)).isoformat())])
def test_ambiguous_confirmation_does_not_release_intent(fixture, change):
    path, legacy, case, scope = fixture
    view = migrate_and_claim(fixture)
    assert view.reserve_intent(attempt(), NOW + timedelta(seconds=1))
    view.mark_started("submit:fixture:scope2", "fixture:execution:second", NOW + timedelta(seconds=2))
    assert not view.confirm("submit:fixture:scope2", proof(**change), NOW + timedelta(seconds=4))
    assert view.snapshot()["status"] == "Submitted / Unconfirmed"
    assert claim_reviewed_preparation(legacy, scope, now=NOW) is None


def test_expiry_stops_effects_but_preserves_late_authentic_result(fixture, monkeypatch):
    path, legacy, case, scope = fixture
    view = migrate_and_claim(fixture)
    assert view.reserve_intent(attempt(), NOW + timedelta(seconds=1))
    view.mark_started("submit:fixture:scope2", "fixture:execution:second", NOW + timedelta(seconds=2))
    monkeypatch.setattr(module, "_now", lambda: NOW + timedelta(hours=2))
    rejected(view.has_intent)
    assert view.confirm("submit:fixture:scope2", proof(), NOW + timedelta(hours=2))
    assert view.snapshot()["status"] == "Confirmed"
    rejected(lambda: claim_reviewed_preparation(legacy, scope, now=NOW + timedelta(hours=2)))


def test_invalid_or_tampered_view_cannot_claim_memory_ownership(fixture):
    view = migrate_and_claim(fixture)
    object.__setattr__(view, "_owner", b"x" * 32)
    rejected(view.has_intent)
    rejected(lambda: view.reserve_intent(attempt(), NOW + timedelta(seconds=1)))


def test_view_repr_cannot_expose_owner_capability_or_private_ledger_details(fixture):
    view = migrate_and_claim(fixture)
    assert repr(view) == "ReviewedScopeLedger()"
    assert repr(view._owner) not in repr(view)
    assert str(view.path) not in repr(view)


@pytest.mark.parametrize("clone", ["copy", "constructed"])
def test_copied_or_reconstructed_view_cannot_duplicate_preparation_owner(fixture, clone):
    view = migrate_and_claim(fixture)
    if clone == "copy":
        other = copy(view)
    else:
        other = object.__new__(ReviewedScopeLedger)
        for key in ("_legacy", "_scope", "_owner"):
            object.__setattr__(other, key, getattr(view, key))
    rejected(other.has_intent)
    rejected(other.snapshot)
    rejected(lambda: other.reserve_intent(attempt(), NOW + timedelta(seconds=1)))
    rejected(lambda: other.path)
    assert not view.has_intent()


def test_view_cannot_move_to_another_thread(fixture):
    view = migrate_and_claim(fixture)
    results = []
    def other_thread():
        try:
            view.has_intent()
        except SubmissionLedgerError:
            results.append("blocked")
    worker = Thread(target=other_thread)
    worker.start()
    worker.join(timeout=5)
    assert results == ["blocked"]
    assert not view.has_intent()


def _inherited_view_worker(view, queue):
    try:
        view.has_intent()
    except SubmissionLedgerError:
        queue.put("blocked")
    else:
        queue.put("accepted")


def test_fork_inherited_view_cannot_reopen_preparation(fixture):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("fork inheritance is platform-specific")
    view = migrate_and_claim(fixture)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    worker = context.Process(target=_inherited_view_worker, args=(view, queue))
    worker.start()
    worker.join(timeout=10)
    assert worker.exitcode == 0
    assert queue.get(timeout=5) == "blocked"
    assert not view.has_intent()


def test_mutating_legacy_object_path_does_not_retarget_issued_view(fixture, tmp_path):
    view = migrate_and_claim(fixture)
    original = view._legacy.path
    view._legacy.path = tmp_path / "replacement.db"
    rejected(view.has_intent)
    assert not view._legacy.path.exists()
    view._legacy.path = original
    assert not view.has_intent()


@pytest.mark.parametrize("phase", ["Preparing", "Validated", "Submitted / Unconfirmed"])
def test_restart_without_owner_never_reopens_scope(fixture, phase):
    path, legacy, case, scope = fixture
    view = migrate_and_claim(fixture)
    if phase != "Preparing":
        assert view.reserve_intent(attempt(), NOW + timedelta(seconds=1))
    if phase == "Submitted / Unconfirmed":
        view.mark_started("submit:fixture:scope2", "fixture:execution:second", NOW + timedelta(seconds=2))
    del view
    reopened = SqliteSubmissionLedger(path)
    assert not register_reviewed_execution_scope(reopened, issue_scope(case), now=NOW)
    assert claim_reviewed_preparation(reopened, issue_scope(case), now=NOW) is None
    assert rows(path, "reviewed_scope_attempts")[0][2] == phase


def _claim_worker(path, case, ready, queue):
    try:
        legacy = SqliteSubmissionLedger(path)
        scope = issue_scope(case)
        ready.wait()
        view = claim_reviewed_preparation(legacy, scope, now=NOW)
        queue.put(view is not None)
    except Exception:
        queue.put("error")


def test_processes_serialize_preparation_before_any_browser_or_upload(fixture):
    path, legacy, case, scope = fixture
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)
    context = multiprocessing.get_context("spawn")
    ready, queue = context.Event(), context.Queue()
    workers = [context.Process(target=_claim_worker, args=(str(path), case, ready, queue)) for _ in range(4)]
    for worker in workers:
        worker.start()
    ready.set()
    results = [queue.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0
    assert results.count(True) == 1 and results.count(False) == 3
    assert claim_reviewed_preparation(legacy, scope, now=NOW) is None


def _crash_worker(path, case):
    view = claim_reviewed_preparation(SqliteSubmissionLedger(path), issue_scope(case), now=NOW)
    if view is None:
        os._exit(2)
    os._exit(0)


def test_real_process_crash_consumes_preparation(fixture):
    path, legacy, case, scope = fixture
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)
    worker = multiprocessing.get_context("spawn").Process(target=_crash_worker, args=(str(path), case))
    worker.start()
    worker.join(timeout=20)
    assert worker.exitcode == 0
    assert claim_reviewed_preparation(SqliteSubmissionLedger(path), issue_scope(case), now=NOW) is None
    assert rows(path, "reviewed_scope_attempts")[0][2] == "Preparing"


def test_changed_legacy_history_stops_before_migration(fixture):
    path, legacy, case, scope = fixture
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE first_application SET validation_key_sha256=?", ("d" * 64,))
    rejected(lambda: register_reviewed_execution_scope(legacy, scope, now=NOW))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.parametrize("sql", [
    "CREATE TABLE unreviewed (x TEXT)",
    "ALTER TABLE reviewed_scope_attempts ADD COLUMN reset INTEGER",
    "UPDATE reviewed_scope_grants SET decision_sha256='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
    "UPDATE reviewed_scope_attempts SET status='Confirmed'",
])
def test_unknown_schema_or_corrupt_extension_rejects_legacy_and_new_views(fixture, sql):
    path, legacy, case, scope = fixture
    view = migrate_and_claim(fixture)
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    with pytest.raises(SubmissionLedgerError):
        SqliteSubmissionLedger(path).snapshot()
    rejected(view.has_intent)
