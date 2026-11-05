"""Real SQLite durability, cross-process exclusion and strict proof-boundary tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3

import pytest

from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence.submission_ledger import SqliteSubmissionLedger, SubmissionLedgerError

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
START = NOW + timedelta(seconds=1)
CONFIRMED = NOW + timedelta(seconds=2)
REF = "evidence:sha256:" + "a" * 64


def attempt(suffix="1"):
    return SubmissionAttempt(
        submit_key="submit:" + suffix, execution_id="exec:" + suffix, opportunity_key="opportunity:" + suffix,
        requisition_id="requisition:" + suffix, candidate_profile_version="profile:" + suffix,
        policy_version="SUBMIT-1.0", validation_key="validation:" + suffix, requested_at=NOW.isoformat(),
        status=SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:" + suffix)


def proof(**changes):
    return replace(ConfirmationEvidence(
        submit_key="submit:1", evidence_type=ConfirmationType.ATS_ACKNOWLEDGEMENT, evidence_ref=REF,
        observed_at=CONFIRMED.isoformat(), exact_identity_match=True, execution_id="exec:1", requisition_id="requisition:1"), **changes)


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "private" / "first-application.db"


def started(path):
    ledger = SqliteSubmissionLedger(path)
    assert ledger.reserve_intent(attempt(), NOW)
    ledger.mark_started("submit:1", "exec:1", START)
    return ledger


def test_one_slot_survives_restart_and_changed_profile_target_or_key(ledger_path):
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.snapshot() is None
    assert not ledger.has_intent()
    assert ledger.reserve_intent(attempt(), NOW)
    assert ledger.has_submit_key("submit:1")
    assert not ledger.has_submit_key("submit:other")
    assert not ledger.reserve_intent(attempt(), NOW)
    reopened = SqliteSubmissionLedger(ledger_path)
    assert reopened.has_intent()
    assert not reopened.reserve_intent(attempt("entirely-new-profile-target-permit"), NOW)
    assert reopened.snapshot()["status"] == SubmissionAttemptStatus.VALIDATED.value


def test_private_permissions_and_metadata_only_snapshot(ledger_path):
    ledger = SqliteSubmissionLedger(ledger_path)
    raw = "reference:opaque-private-sentinel"
    private_notes = "PRIVATE_CANDIDATE@example.test /private/cv.pdf SECRET_OTP_123456"
    value = replace(attempt(), submit_key=raw, execution_id=raw, opportunity_key=raw, requisition_id=raw,
        candidate_profile_version=raw, validation_key=raw, notes=private_notes, evidence_ref=private_notes)
    assert ledger.reserve_intent(value, NOW)
    assert ledger_path.stat().st_mode & 0o777 == 0o600
    assert ledger_path.parent.stat().st_mode & 0o777 == 0o700
    assert raw.encode() not in ledger_path.read_bytes()
    assert private_notes.encode() not in ledger_path.read_bytes()
    assert hashlib.sha256(private_notes.encode()).hexdigest().encode() not in ledger_path.read_bytes()
    snapshot = ledger.snapshot()
    assert raw not in repr(snapshot)
    assert snapshot["validation_digest"] == hashlib.sha256(raw.encode()).hexdigest()
    assert set(snapshot) == {"status", "submit_key_digest", "execution_digest", "requisition_digest", "validation_digest", "requested_at", "started_at", "confirmed_at", "evidence_digest"}


def test_start_is_committed_before_return_and_cannot_be_repeated(ledger_path):
    ledger = started(ledger_path)
    assert SqliteSubmissionLedger(ledger_path).snapshot()["status"] == SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value
    with pytest.raises(SubmissionLedgerError, match="LEDGER_START_REJECTED"):
        ledger.mark_started("submit:1", "exec:1", START)
    assert not ledger.reserve_intent(attempt("2"), CONFIRMED)


@pytest.mark.parametrize("key,execution,time", [
    ("wrong", "exec:1", START), ("submit:1", "wrong", START), ("submit:1", "exec:1", NOW - timedelta(seconds=1)),
])
def test_start_rejects_wrong_binding_or_order_without_releasing_slot(ledger_path, key, execution, time):
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.reserve_intent(attempt(), NOW)
    with pytest.raises(SubmissionLedgerError):
        ledger.mark_started(key, execution, time)
    assert ledger.has_intent()
    assert not ledger.reserve_intent(attempt("2"), NOW)


def test_start_requires_an_intent(ledger_path):
    with pytest.raises(SubmissionLedgerError):
        SqliteSubmissionLedger(ledger_path).mark_started("submit:1", "exec:1", START)


def test_exact_confirmation_is_idempotent_and_forever_consumes_slot(ledger_path):
    ledger = started(ledger_path)
    assert ledger.confirm("submit:1", proof(), CONFIRMED)
    reopened = SqliteSubmissionLedger(ledger_path)
    assert reopened.snapshot()["status"] == SubmissionAttemptStatus.CONFIRMED.value
    assert reopened.snapshot()["evidence_digest"] == "a" * 64
    assert reopened.confirm("submit:1", proof(), CONFIRMED + timedelta(seconds=1))
    assert not reopened.confirm("submit:1", proof(evidence_ref="evidence:sha256:" + "b" * 64), CONFIRMED)
    assert not reopened.reserve_intent(attempt("2"), CONFIRMED)


def test_confirmation_cannot_skip_submitted_unconfirmed(ledger_path):
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.reserve_intent(attempt(), NOW)
    assert not ledger.confirm("submit:1", proof(), CONFIRMED)
    assert ledger.snapshot()["status"] == SubmissionAttemptStatus.VALIDATED.value


@pytest.mark.parametrize("changes", [
    {"submit_key": "wrong"}, {"execution_id": "wrong"}, {"requisition_id": "wrong"},
    {"execution_id": ""}, {"requisition_id": ""}, {"exact_identity_match": False},
    {"exact_identity_match": 1}, {"exact_identity_match": "true"},
    {"evidence_type": "ATS Acknowledgement"}, {"evidence_ref": "https://employer.example/PRIVATE"},
    {"evidence_ref": "evidence:sha256:" + "G" * 64}, {"evidence_ref": REF + "\n"},
    {"observed_at": NOW.isoformat()}, {"observed_at": (CONFIRMED + timedelta(seconds=1)).isoformat()},
    {"observed_at": CONFIRMED.replace(tzinfo=None).isoformat()}, {"observed_at": "invalid"},
])
def test_ambiguous_confirmation_rejected_without_retry_budget(ledger_path, changes):
    ledger = started(ledger_path)
    assert not ledger.confirm("submit:1", proof(**changes), CONFIRMED)
    assert ledger.snapshot()["status"] == SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value
    assert not ledger.reserve_intent(attempt("2"), CONFIRMED)


@pytest.mark.parametrize("changes", [
    {"status": SubmissionAttemptStatus.REQUESTED}, {"status": "Validated"},
    {"submit_key": ""}, {"execution_id": " "}, {"requisition_id": ""}, {"candidate_profile_version": ""},
    {"policy_version": ""}, {"validation_key": ""}, {"form_fingerprint": ""}, {"ats_family": ""},
    {"requested_at": (NOW + timedelta(seconds=1)).isoformat()}, {"requested_at": "invalid"},
    {"requested_at": NOW.replace(tzinfo=None).isoformat()},
])
def test_reservation_requires_complete_validated_utc_identity(ledger_path, changes):
    ledger = SqliteSubmissionLedger(ledger_path)
    with pytest.raises(SubmissionLedgerError):
        ledger.reserve_intent(replace(attempt(), **changes), NOW)
    assert not ledger.has_intent()


@pytest.mark.parametrize("time", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=3))), "2026-11-05"])
def test_non_utc_clock_rejected(ledger_path, time):
    with pytest.raises(SubmissionLedgerError, match="LEDGER_UTC_TIME_REQUIRED"):
        SqliteSubmissionLedger(ledger_path).reserve_intent(attempt(), time)


def test_insecure_parent_rejected_not_repaired(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_PARENT_NOT_PRIVATE"):
        SqliteSubmissionLedger(parent / "ledger.db")
    assert parent.stat().st_mode & 0o777 == 0o755
    assert not (parent / "ledger.db").exists()


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o400])
def test_existing_insecure_file_rejected_not_chmodded(ledger_path, mode):
    ledger_path.parent.mkdir(mode=0o700)
    ledger_path.write_bytes(b"existing private data")
    ledger_path.chmod(mode)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_FILE_UNSAFE"):
        SqliteSubmissionLedger(ledger_path)
    assert ledger_path.read_bytes() == b"existing private data"
    assert ledger_path.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("payload", [b"", b"not a sqlite database"])
def test_existing_empty_or_corrupt_file_never_rebuilt(ledger_path, payload):
    ledger_path.parent.mkdir(mode=0o700)
    ledger_path.write_bytes(payload)
    ledger_path.chmod(0o600)
    with pytest.raises(SubmissionLedgerError):
        SqliteSubmissionLedger(ledger_path)
    assert ledger_path.read_bytes() == payload


def test_symlink_file_and_parent_rejected(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    real = private / "real.db"
    SqliteSubmissionLedger(real)
    link = private / "linked.db"
    link.symlink_to(real)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_FILE_UNSAFE"):
        SqliteSubmissionLedger(link)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(private, target_is_directory=True)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_PATH_UNSAFE"):
        SqliteSubmissionLedger(parent_link / "other.db")


def test_hardlink_file_rejected(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    real = private / "real.db"
    SqliteSubmissionLedger(real)
    alias = private / "alias.db"
    alias.hardlink_to(real)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_FILE_UNSAFE"):
        SqliteSubmissionLedger(alias)


def test_permission_drift_after_open_fails_closed(ledger_path):
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.reserve_intent(attempt(), NOW)
    ledger_path.chmod(0o644)
    with pytest.raises(SubmissionLedgerError):
        ledger.has_intent()
    with pytest.raises(SubmissionLedgerError):
        ledger.reserve_intent(attempt("2"), NOW)


def _reserve_worker(path, number, ready, queue):
    try:
        ledger = SqliteSubmissionLedger(path)
        ready.wait(10)
        queue.put(ledger.reserve_intent(attempt(str(number)), NOW))
    except SubmissionLedgerError:
        queue.put("fail_closed")


def test_competing_processes_cannot_reserve_different_keys(ledger_path):
    SqliteSubmissionLedger(ledger_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    queue = context.Queue()
    processes = [context.Process(target=_reserve_worker, args=(str(ledger_path), n, ready, queue)) for n in range(6)]
    for process in processes:
        process.start()
    ready.set()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert results.count(True) == 1
    assert results.count(False) == 5
    assert SqliteSubmissionLedger(ledger_path).has_intent()


def _crash_worker(path, phase):
    ledger = SqliteSubmissionLedger(path)
    ledger.reserve_intent(attempt(), NOW)
    if phase == "started":
        ledger.mark_started("submit:1", "exec:1", START)
    elif phase == "uncommitted":
        connection = sqlite3.connect(path)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE first_application SET status='Confirmed'")
    os._exit(23)


@pytest.mark.parametrize("phase,status", [
    ("reserved", SubmissionAttemptStatus.VALIDATED.value),
    ("started", SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value),
    ("uncommitted", SubmissionAttemptStatus.VALIDATED.value),
])
def test_restart_after_process_crash_never_reopens_budget(ledger_path, phase, status):
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_worker, args=(str(ledger_path), phase))
    process.start()
    process.join(20)
    assert process.exitcode == 23
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.snapshot()["status"] == status
    assert not ledger.reserve_intent(attempt("2"), CONFIRMED)


def test_transaction_error_is_sanitized_and_cannot_clear_reserved_intent(ledger_path, monkeypatch):
    ledger = started(ledger_path)
    original = sqlite3.connect

    class FailingConnection:
        def __init__(self, underlying):
            object.__setattr__(self, "underlying", underlying)

        def __getattr__(self, name):
            return getattr(self.underlying, name)

        def __setattr__(self, name, value):
            setattr(self.underlying, name, value)

        def execute(self, query, *args):
            if query == "COMMIT":
                raise sqlite3.OperationalError("PRIVATE_DATABASE_PATH_AND_MAIL_CODE")
            return self.underlying.execute(query, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: FailingConnection(original(*args, **kwargs)))
    with pytest.raises(SubmissionLedgerError, match="LEDGER_STORAGE_FAILED") as caught:
        ledger.confirm("submit:1", proof(), CONFIRMED)
    assert "PRIVATE_DATABASE_PATH_AND_MAIL_CODE" not in str(caught.value)
    monkeypatch.setattr(sqlite3, "connect", original)
    reopened = SqliteSubmissionLedger(ledger_path)
    assert reopened.snapshot()["status"] == SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value
    assert not reopened.reserve_intent(attempt("2"), CONFIRMED)


@pytest.mark.parametrize("field", ["submit_key", "execution_id", "opportunity_key", "requisition_id", "candidate_profile_version", "policy_version", "validation_key", "ats_family", "form_fingerprint"])
@pytest.mark.parametrize("value", ["PRIVATE_CANDIDATE@example.test", "/private/cv.pdf", "https://employer.example?secret=PRIVATE", "private answer text"])
def test_raw_private_content_cannot_be_promoted_to_opaque_reference(ledger_path, field, value):
    ledger = SqliteSubmissionLedger(ledger_path)
    with pytest.raises(SubmissionLedgerError, match="LEDGER_IDENTITY_INVALID"):
        ledger.reserve_intent(replace(attempt(), **{field: value}), NOW)
    assert not ledger.has_intent()
    assert value.encode() not in ledger_path.read_bytes()


@pytest.mark.parametrize("change", [
    "UPDATE first_application SET status='Confirmed'",
    "UPDATE first_application SET execution_id_sha256='not-a-digest'",
    "UPDATE first_application SET reserved_at='2020-01-01T00:00:00+00:00'",
])
def test_corrupt_state_metadata_rejected_before_reading_as_confirmed(ledger_path, change):
    ledger = SqliteSubmissionLedger(ledger_path)
    assert ledger.reserve_intent(attempt(), NOW)
    connection = sqlite3.connect(ledger_path)
    try:
        # Ignore CHECK only to model corruption, never through ledger API.
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(change)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SubmissionLedgerError):
        ledger.snapshot()
    with pytest.raises(SubmissionLedgerError):
        ledger.reserve_intent(attempt("different"), CONFIRMED)
