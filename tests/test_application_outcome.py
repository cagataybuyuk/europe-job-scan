from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat

import pytest

from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.application_outcome import write_confirmed_outcome


NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
REF = "evidence:sha256:" + "b" * 64
URL = "https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst"


@pytest.fixture
def ledger(tmp_path):
    value = SqliteSubmissionLedger(tmp_path / "private" / "first.sqlite3")
    attempt = SubmissionAttempt("submit:fixture", "exec:fixture", "opportunity:fixture", "REF1234X", "profile:v1",
        "SUBMIT-1.0", "validation:fixture", NOW.isoformat(), SubmissionAttemptStatus.VALIDATED,
        ats_family="smartrecruiters", form_fingerprint="form:fixture")
    assert value.reserve_intent(attempt, NOW)
    return value


def confirm(ledger):
    ledger.mark_started("submit:fixture", "exec:fixture", NOW)
    proof = ConfirmationEvidence("submit:fixture", ConfirmationType.ATS_ACKNOWLEDGEMENT, REF,
        NOW.isoformat(), True, "exec:fixture", "REF1234X")
    assert ledger.confirm("submit:fixture", proof, NOW + timedelta(seconds=1))
    return ledger


def write(ledger, path=None, **changes):
    kwargs = dict(ledger=ledger, submit_key="submit:fixture", execution_id="exec:fixture", requisition_id="REF1234X",
        posting_url=URL, evidence_ref=REF)
    kwargs.update(changes)
    return write_confirmed_outcome(path or ledger.path.parent / "outcome.json", **kwargs)


def test_applied_requires_exact_durable_confirmation_and_has_private_metadata_only(ledger):
    confirm(ledger)
    path = ledger.path.parent / "outcome.json"
    out = write(ledger)
    assert out == {"status": "APPLIED_RECORDED", "applications_write_performed": False,
                   "local_outcome_write_performed": True, "idempotent": False}
    record = json.loads(path.read_text())
    assert record == {"schema_version": "EJS-CONFIRMED-APPLICATION-1", "status": "Applied",
        "source": "ATS_ACKNOWLEDGEMENT", "posting_url": URL, "requisition_id": "REF1234X",
        "execution_id": "exec:fixture", "evidence_ref": REF}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_nlink == 1
    assert not tuple(path.parent.glob(".ejs-outcome-*"))
    assert "submit:fixture" not in path.read_text()


def test_identical_outcome_survives_restart_without_rewrite(ledger):
    confirm(ledger)
    write(ledger)
    path = ledger.path.parent / "outcome.json"
    before = path.stat()
    reopened = SqliteSubmissionLedger(ledger.path)
    assert write(reopened) == {"status": "APPLIED_RECORDED", "applications_write_performed": False,
        "local_outcome_write_performed": False, "idempotent": True}
    after = path.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_size) == (after.st_ino, after.st_mtime_ns, after.st_size)


@pytest.mark.parametrize("state", ["empty", "validated", "unconfirmed"])
def test_no_applied_from_incomplete_or_uncertain_submission(ledger, state):
    if state == "empty":
        ledger = SqliteSubmissionLedger(ledger.path.parent / "empty.sqlite3")
    if state == "unconfirmed":
        ledger.mark_started("submit:fixture", "exec:fixture", NOW)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(ledger)
    assert not (ledger.path.parent / "outcome.json").exists()


@pytest.mark.parametrize("changes", [
    {"submit_key": "submit:other"}, {"execution_id": "exec:other"}, {"requisition_id": "REF9999X"},
    {"evidence_ref": "evidence:sha256:" + "c" * 64}, {"evidence_ref": "evidence:sha256:" + "B" * 64},
    {"evidence_ref": REF + "\n"}, {"submit_key": "PRIVATE@example.test"},
    {"execution_id": "/PRIVATE/path"}, {"requisition_id": "PRIVATE ID"},
    {"posting_url": URL + "?email=PRIVATE"}, {"posting_url": URL + "#private"},
    {"posting_url": URL.replace("https", "http")},
    {"posting_url": URL.replace(".com/", ".com:443/")},
    {"posting_url": URL.replace(".com/", ".com.evil.test/")},
    {"posting_url": URL.replace("/Fixture/", "/Fixture%2f")},
    {"posting_url": URL + "/"}, {"posting_url": None},
])
def test_wrong_identity_proof_or_noncanonical_target_rejected(ledger, changes):
    confirm(ledger)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$") as error:
        write(ledger, **changes)
    assert "PRIVATE" not in str(error.value)
    assert not (ledger.path.parent / "outcome.json").exists()


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o400])
def test_unsafe_existing_file_never_chmodded_or_overwritten(ledger, mode):
    confirm(ledger)
    path = ledger.path.parent / "outcome.json"
    path.write_bytes(b"PRIVATE_EXISTING_DATA")
    path.chmod(mode)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(ledger)
    assert path.read_bytes() == b"PRIVATE_EXISTING_DATA"
    assert path.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("payload", [b"", b"corrupt", b"{}", b"x" * 8193])
def test_conflict_empty_or_corrupt_file_is_preserved(ledger, payload):
    confirm(ledger)
    path = ledger.path.parent / "outcome.json"
    path.write_bytes(payload)
    path.chmod(0o600)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(ledger)
    assert path.read_bytes() == payload


def test_existing_valid_but_conflicting_outcome_cannot_be_replaced(ledger):
    confirm(ledger)
    write(ledger)
    path = ledger.path.parent / "outcome.json"
    before = path.read_bytes()
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(ledger, posting_url=URL + "-different")
    assert path.read_bytes() == before


def test_symlink_file_and_any_symlink_parent_rejected(ledger, tmp_path):
    confirm(ledger)
    real = ledger.path.parent / "real.json"
    real.write_bytes(b"PRIVATE_EXISTING_DATA")
    real.chmod(0o600)
    link = ledger.path.parent / "outcome.json"
    link.symlink_to(real)
    with pytest.raises(PermissionError):
        write(ledger)
    assert real.read_bytes() == b"PRIVATE_EXISTING_DATA"
    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(ledger.path.parent, target_is_directory=True)
    with pytest.raises(PermissionError):
        write(ledger, parent_link / "other.json")
    assert not (ledger.path.parent / "other.json").exists()


def test_hardlink_file_rejected_without_mutation(ledger):
    confirm(ledger)
    real = ledger.path.parent / "real.json"
    real.write_bytes(b"PRIVATE_EXISTING_DATA")
    real.chmod(0o600)
    (ledger.path.parent / "outcome.json").hardlink_to(real)
    with pytest.raises(PermissionError):
        write(ledger)
    assert real.read_bytes() == b"PRIVATE_EXISTING_DATA"


def test_insecure_or_missing_parent_not_created_or_repaired(ledger, tmp_path):
    confirm(ledger)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(PermissionError):
        write(ledger, shared / "outcome.json")
    assert shared.stat().st_mode & 0o777 == 0o755
    assert not (shared / "outcome.json").exists()
    with pytest.raises(PermissionError):
        write(ledger, tmp_path / "missing" / "outcome.json")
    assert not (tmp_path / "missing").exists()


def test_relative_or_ledger_output_path_rejected(ledger):
    confirm(ledger)
    for path in (Path("outcome.json"), ledger.path, ledger.path.parent / ".." / "outcome.json"):
        with pytest.raises(PermissionError):
            write(ledger, path)
    assert ledger.snapshot()["status"] == "Confirmed"


def test_fsync_and_exclusive_publication_failure_never_replace_existing(ledger, monkeypatch):
    confirm(ledger)
    path = ledger.path.parent / "outcome.json"
    original_link = os.link
    def racing_publication(*args, **kwargs):
        path.write_bytes(b"PRIVATE_RACE_WINNER")
        path.chmod(0o600)
        return original_link(*args, **kwargs)
    monkeypatch.setattr(os, "link", racing_publication)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$") as error:
        write(ledger)
    assert path.read_bytes() == b"PRIVATE_RACE_WINNER"
    assert "PRIVATE" not in str(error.value)
    assert not tuple(path.parent.glob(".ejs-outcome-*"))


def test_ledger_storage_failure_does_not_create_outcome(ledger):
    confirm(ledger)
    ledger.path.chmod(0o644)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(ledger)
    assert not (ledger.path.parent / "outcome.json").exists()


def test_unsynced_temp_bytes_are_never_published(ledger, monkeypatch):
    confirm(ledger)
    original_fsync = os.fsync
    def storage_failure(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("PRIVATE_STORAGE_SENTINEL")
        return original_fsync(fd)
    monkeypatch.setattr(os, "fsync", storage_failure)
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$") as error:
        write(ledger)
    assert "PRIVATE" not in str(error.value)
    assert not (ledger.path.parent / "outcome.json").exists()
    assert not tuple(ledger.path.parent.glob(".ejs-outcome-*"))
    assert ledger.snapshot()["status"] == "Confirmed"


def test_fabricated_snapshot_object_cannot_create_outcome(ledger):
    class FakeLedger(SqliteSubmissionLedger):
        def snapshot(self):
            raise AssertionError("The untrusted subclass must not be called")
    fake = object.__new__(FakeLedger)
    fake.path = ledger.path
    with pytest.raises(PermissionError, match="^APPLICATION_OUTCOME_REJECTED$"):
        write(fake)
    assert not (ledger.path.parent / "outcome.json").exists()
