from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import json

import pytest

from ejs.contracts.mw5 import opportunity_key
from ejs.contracts.submit import submit_identity
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.central_application_outcome import (
    CentralApplicationBinding, CentralApplicationRead, CentralApplicationReceipt,
    CentralApplicationRow, central_binding_sha256, sync_confirmed_application,
)


NOW = datetime(2026, 11, 12, 22, 30, tzinfo=timezone.utc)
SOURCE = "a" * 40
EVIDENCE = "evidence:sha256:" + "b" * 64
SPREADSHEET = "synthetic_tracker_id_123456789"
HEADERS = tuple("Date Added,Company,Role,Country,City,Source URL,Role Fit Score,Sponsorship,Role Priority,Status,Applied Date,CV Persona,Recruiter,Recruiter Contacted,Salary Expectation,Next Action,Next Action Date,Notes,Application Source,Recruiter LinkedIn,Outreach Date,Follow-up Due,Follow-up Status,Last Update".split(","))
OPPORTUNITY = opportunity_key("Fixture", "Analyst", "Ireland")
SUBMIT = submit_identity(OPPORTUNITY, "REF1234X", "profile:v1")


@pytest.fixture
def ledger(tmp_path):
    value = SqliteSubmissionLedger(tmp_path / "private" / "ledger.sqlite3")
    attempt = SubmissionAttempt(SUBMIT, "exec:fixture", OPPORTUNITY, "REF1234X", "profile:v1",
        "SUBMIT-1.0", "validation:fixture", (NOW - timedelta(seconds=3)).isoformat(),
        SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:fixture")
    assert value.reserve_intent(attempt, NOW - timedelta(seconds=3))
    return value


def confirm(ledger):
    ledger.mark_started(SUBMIT, "exec:fixture", NOW - timedelta(seconds=2))
    evidence = ConfirmationEvidence(SUBMIT, ConfirmationType.ATS_ACKNOWLEDGEMENT, EVIDENCE,
        (NOW - timedelta(seconds=1)).isoformat(), True, "exec:fixture", "REF1234X")
    assert ledger.confirm(SUBMIT, evidence, NOW - timedelta(seconds=1))
    return ledger


@pytest.fixture
def binding():
    return CentralApplicationBinding(SPREADSHEET, 77, 8, HEADERS, "Fixture", "Analyst", "Ireland",
        "https://fixture.example/careers/analyst", OPPORTUNITY,
        "https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst", "REF1234X",
        "profile:v1", SUBMIT, "exec:fixture", EVIDENCE, SOURCE, NOW.isoformat())


class SyntheticLockedTransport:
    atomic_row_compare_and_set = True

    def __init__(self, binding):
        self.binding = binding
        self.headers = binding.headers
        self.allowed = ("To Apply", "Applied", "Rejected")
        cells = [""] * 24
        for index, value in {0: "2026-11-12", 1: binding.company, 2: binding.role,
                3: binding.country, 4: "Dublin", 5: binding.tracked_url, 7: "Unknown",
                9: "To Apply", 17: "SYNTHETIC untouched notes", 23: "2026-11-12"}.items():
            cells[index] = value
        self.rows = [CentralApplicationRow(binding.row_number, tuple(cells))]
        self.requests = []
        self.writes = 0
        self.reads = 0
        self.before_cas = None
        self.after_cas = None
        self.raise_after_commit = False
        self.receipt_override = None

    def read_bound_rows(self, binding):
        self.reads += 1
        return CentralApplicationRead(binding.spreadsheet_id, binding.sheet_id, binding.sheet_name,
            self.headers, tuple(self.rows), self.allowed)

    def set_cell(self, index, value):
        cells = list(self.rows[0].cells)
        cells[index] = value
        self.rows[0] = replace(self.rows[0], cells=tuple(cells))

    def fingerprint(self):
        data = (self.binding.spreadsheet_id, self.binding.sheet_id, self.binding.sheet_name,
                self.headers, self.allowed, tuple((r.row_number, r.cells) for r in self.rows))
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def compare_and_set(self, request):
        self.requests.append(request)
        assert request.identity_key == (self.binding.company, self.binding.role, self.binding.country)
        if self.before_cas:
            self.before_cas()
        if self.receipt_override:
            return self.receipt_override(request)
        if len(self.rows) != 1 or request.expected_fingerprint != self.fingerprint():
            return CentralApplicationReceipt(request.operation_key, False, False)
        for patch in request.patches:
            assert patch.row == self.binding.row_number
            self.set_cell({"J": 9, "K": 10, "X": 23}[patch.column], patch.value)
        self.writes += 1
        if self.after_cas:
            self.after_cas()
        if self.raise_after_commit:
            raise RuntimeError("PRIVATE_TRANSPORT_FAILURE with secret-token")
        return CentralApplicationReceipt(request.operation_key, True, True)


def sync(ledger, binding, transport=None, **changes):
    kwargs = dict(ledger=ledger, binding=binding, binding_sha256=central_binding_sha256(binding),
        reviewed_source_sha=SOURCE, transport=transport or SyntheticLockedTransport(binding),
        now=NOW, write_enabled=True)
    kwargs.update(changes)
    return sync_confirmed_application(**kwargs)


def test_confirmed_actual_ledger_updates_only_exact_three_cells_and_reads_back(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    before = transport.rows[0].cells
    result = sync(ledger, binding, transport)
    assert result == {"status": "CENTRAL_RECORDED", "applications_write_performed": True,
        "central_readback_verified": True, "submit_attempt_performed": False}
    assert transport.reads == 2 and transport.writes == 1
    assert [(p.column, p.row, p.value) for p in transport.requests[0].patches] == [
        ("J", 8, "Applied"), ("K", 8, "2026-11-13"), ("X", 8, "2026-11-13")]
    assert all(before[i] == transport.rows[0].cells[i] for i in range(24) if i not in {9, 10, 23})
    assert ledger.snapshot()["status"] == "Confirmed"


def test_idempotent_restart_with_new_binding_review_does_not_write_again(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    assert sync(ledger, binding, transport)["status"] == "CENTRAL_RECORDED"
    reopened = SqliteSubmissionLedger(ledger.path)
    fresh = replace(binding, reviewed_at=(NOW + timedelta(minutes=2)).isoformat())
    assert sync(reopened, fresh, transport, now=NOW + timedelta(minutes=2))["status"] == "ALREADY_RECORDED"
    assert transport.writes == 1 and len(transport.requests) == 1


@pytest.mark.parametrize("headers", [
    tuple(reversed(HEADERS)),
    HEADERS[:9] + ("Status renamed",) + HEADERS[10:],
    HEADERS[:9] + (HEADERS[10], HEADERS[9]) + HEADERS[11:],
    (HEADERS[0], HEADERS[2], HEADERS[1]) + HEADERS[3:],
])
def test_freshly_repinned_incompatible_column_schema_blocks_before_read_or_write(ledger, binding, headers):
    confirm(ledger)
    changed = replace(binding, headers=headers)
    transport = SyntheticLockedTransport(changed)
    result = sync(ledger, changed, transport)
    assert result["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == transport.writes == 0
    assert transport.requests == []


def test_duplicate_matching_row_inserted_during_cas_stops_before_write(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    initial = transport.rows[0]
    transport.before_cas = lambda: transport.rows.append(replace(initial, row_number=9))
    result = sync(ledger, binding, transport)
    assert result["status"] == "CONCURRENT_CHANGE"
    assert result["applications_write_performed"] is False
    assert transport.writes == 0 and len(transport.requests) == 1
    assert all(row.cells[9] == "To Apply" for row in transport.rows)


@pytest.mark.parametrize("status", ["Rejected", "Recruiter Contacted", "Screening", "Assessment", "Interview 1",
    "Interview 2", "Final Interview", "Offer", "Accepted", "Withdrawn", "Closed", "Hired"])
def test_later_states_preserved_without_even_correcting_other_metadata(ledger, binding, status):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.set_cell(9, status)
    transport.set_cell(10, "2026-11-07")
    transport.set_cell(23, "2026-11-14")
    before = transport.rows[0]
    assert sync(ledger, binding, transport)["status"] == "LATER_STATE_PRESERVED"
    assert transport.rows[0] == before and not transport.requests


@pytest.mark.parametrize("state", ["empty", "validated", "unconfirmed"])
def test_incomplete_ledger_cannot_read_or_write_central_state(ledger, binding, state):
    if state == "empty":
        ledger = SqliteSubmissionLedger(ledger.path.parent / "empty.sqlite3")
    if state == "unconfirmed":
        ledger.mark_started(SUBMIT, "exec:fixture", NOW - timedelta(seconds=2))
    transport = SyntheticLockedTransport(binding)
    assert sync(ledger, binding, transport)["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == 0 and transport.writes == 0


@pytest.mark.parametrize("changes", [
    {"execution_id": "exec:other"}, {"requisition_id": "REF9999X"},
    {"evidence_ref": "evidence:sha256:" + "c" * 64}, {"submit_key": "submit:other"},
    {"candidate_profile_version": "profile:v2"}, {"opportunity_key": "opportunity:other"},
    {"posting_url": "https://jobs.smartrecruiters.com.evil.test/Fixture/123456789012345"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture/123456789012345?PRIVATE=secret"},
    {"tracked_url": "https://user:secret@fixture.example/path"},
    {"reviewed_at": (NOW - timedelta(minutes=5, seconds=1)).isoformat()},
    {"reviewed_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"row_number": True}, {"sheet_id": True}, {"row_number": 1}, {"sheet_name": "User Fact Registry"},
    {"time_zone": "UTC"}, {"confirmation_kind": "EMAIL"}, {"source_sha": "c" * 40},
    {"headers": HEADERS[:-1]}, {"headers": tuple(["same"] * 24)},
])
def test_bad_pinned_bindings_fail_before_transport_reads(ledger, binding, changes):
    confirm(ledger)
    changed = replace(binding, **changes)
    transport = SyntheticLockedTransport(binding)
    result = sync(ledger, changed, transport)
    assert result["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == 0 and not transport.requests
    assert "PRIVATE" not in json.dumps(result) and "secret" not in json.dumps(result)


def test_external_binding_pin_cannot_be_replaced_by_claimed_review(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    assert sync(ledger, binding, transport, binding_sha256="0" * 64)["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == 0


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_row", "wrong_company", "wrong_role", "wrong_country", "wrong_url",
    "wrong_headers", "unknown_status", "wrong_applied_date", "missing_validation"])
def test_missing_duplicate_drifted_or_inconsistent_rows_never_write(ledger, binding, kind):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    if kind == "missing": transport.rows = []
    elif kind == "duplicate": transport.rows.append(replace(transport.rows[0], row_number=9))
    elif kind == "wrong_row": transport.rows[0] = replace(transport.rows[0], row_number=9)
    elif kind.startswith("wrong_") and kind in {"wrong_company", "wrong_role", "wrong_country", "wrong_url"}:
        transport.set_cell({"wrong_company": 1, "wrong_role": 2, "wrong_country": 3, "wrong_url": 5}[kind], "OTHER")
    elif kind == "wrong_headers": transport.headers = tuple(reversed(HEADERS))
    elif kind == "unknown_status": transport.set_cell(9, "Unknown")
    elif kind == "wrong_applied_date":
        transport.set_cell(9, "Applied")
        transport.set_cell(10, "2026-01-01")
    elif kind == "missing_validation": transport.allowed = ()
    result = sync(ledger, binding, transport)
    assert result["status"] == "CENTRAL_SYNC_BLOCKED"
    assert not transport.requests and transport.writes == 0


def test_default_is_read_only_even_with_confirmed_proof(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    assert sync(ledger, binding, transport, write_enabled=False)["status"] == "CENTRAL_WRITE_DISABLED"
    assert transport.reads == 1 and not transport.requests


@pytest.mark.parametrize("capability", [False, None, 1, "true"])
def test_plain_read_write_transport_cannot_claim_atomic_cas(ledger, binding, capability):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.atomic_row_compare_and_set = capability
    assert sync(ledger, binding, transport)["status"] == "CENTRAL_SYNC_BLOCKED"
    assert not transport.requests


@pytest.mark.parametrize("drift", ["later_status", "notes", "headers", "validation"])
def test_atomic_comparison_rejects_change_between_read_and_write_without_retry(ledger, binding, drift):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    def race():
        if drift == "later_status": transport.set_cell(9, "Rejected")
        elif drift == "notes": transport.set_cell(17, "SYNTHETIC concurrent edit")
        elif drift == "headers": transport.headers = tuple(reversed(HEADERS))
        else: transport.allowed = ("To Apply", "Rejected")
    transport.before_cas = race
    assert sync(ledger, binding, transport)["status"] == "CONCURRENT_CHANGE"
    assert len(transport.requests) == 1 and transport.writes == 0


def test_commit_then_transport_timeout_is_uncertain_and_never_retried(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.raise_after_commit = True
    result = sync(ledger, binding, transport)
    assert result["status"] == "CENTRAL_SYNC_UNCERTAIN"
    assert result["applications_write_performed"] is None
    assert "PRIVATE" not in json.dumps(result) and "secret-token" not in json.dumps(result)
    assert transport.writes == 1 and len(transport.requests) == 1
    transport.raise_after_commit = False
    assert sync(ledger, binding, transport)["status"] == "ALREADY_RECORDED"
    assert transport.writes == 1


@pytest.mark.parametrize("mutation,expected", [(lambda t: t.set_cell(17, "SYNTHETIC drift"), "CENTRAL_READBACK_MISMATCH"),
    (lambda t: t.set_cell(9, "Rejected"), "LATER_STATE_PRESERVED")])
def test_postwrite_drift_or_newer_outcome_is_reported_without_repair(ledger, binding, mutation, expected):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.after_cas = lambda: mutation(transport)
    result = sync(ledger, binding, transport)
    assert result["status"] == expected and result["central_readback_verified"] is False
    assert result["applications_write_performed"] is True
    assert transport.writes == 1


@pytest.mark.parametrize("receipt", [lambda r: CentralApplicationReceipt("0" * 64, True, True),
    lambda r: CentralApplicationReceipt(r.operation_key, False, True),
    lambda r: CentralApplicationReceipt(r.operation_key, 1, True),
    lambda r: {"operation_key": r.operation_key, "comparison_succeeded": True, "wrote": True}])
def test_invalid_or_forged_transport_receipts_are_uncertain(ledger, binding, receipt):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.receipt_override = receipt
    result = sync(ledger, binding, transport)
    assert result["status"] == "CENTRAL_SYNC_UNCERTAIN" and result["applications_write_performed"] is None
    assert len(transport.requests) == 1


def test_ledger_subclass_or_storage_failure_cannot_publish(ledger, binding):
    class FakeLedger(SqliteSubmissionLedger):
        def snapshot(self):
            raise AssertionError("must not trust a fake snapshot")
    fake = object.__new__(FakeLedger)
    fake.path = ledger.path
    transport = SyntheticLockedTransport(binding)
    assert sync(fake, binding, transport)["status"] == "CENTRAL_SYNC_BLOCKED"
    confirm(ledger)
    ledger.path.chmod(0o644)
    assert sync(ledger, binding, transport)["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == 0


def test_invalid_clock_and_boolean_write_authority_fail_closed(ledger, binding):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    assert sync(ledger, binding, transport, now=NOW.replace(tzinfo=None))["status"] == "CENTRAL_SYNC_BLOCKED"
    assert sync(ledger, binding, transport, now=NOW - timedelta(seconds=2))["status"] == "CENTRAL_SYNC_BLOCKED"
    assert sync(ledger, binding, transport, write_enabled=1)["status"] == "CENTRAL_SYNC_BLOCKED"
    assert transport.reads == 0


@pytest.mark.parametrize("allowed", [
    ("To Apply", "Applied", "Applied", "Rejected"),
    ("To Apply", "Applied", ""),
    ("To Apply", "Applied", "Rejected", "Rejected"),
    ("To Apply", "Applied ", "Rejected"),
    ("To Apply", " Applied", "Rejected"),
    ("To Apply", "Applied", "Rejected\t"),
    ("To Apply", "Applied", " Rejected", "Rejected"),
    ("To Apply", "Applied", "Rejected\u200b"),
    ("To Apply", "Applied", "Rejected\ufeff"),
    ("To Apply", "Applied", "Ａｐｐｌｉｅｄ"),
    ("To Apply", "Applied", "Rejected\u00a0"),
    ("To Apply", "Applied", "Rejected\u2028"),
    ("To Apply", "Applied", "Rejected\u2029"),
    ("To Apply", "Applied", "Rej\u0000ected"),
    ("To Apply", "Applied", "Rej\u202eected"),
    ("To Apply", "Applied", "Rej\u000dected"),
    ("To Apply", "Applied", "Rej\u000aected"),
])
def test_ambiguous_status_validation_never_publishes_applied(ledger, binding, allowed):
    confirm(ledger)
    transport = SyntheticLockedTransport(binding)
    transport.allowed = allowed
    result = sync(ledger, binding, transport)
    assert result["status"] == "CENTRAL_SYNC_BLOCKED"
    assert result["applications_write_performed"] is False
    assert transport.writes == 0 and transport.requests == []
    assert transport.rows[0].cells[9] == "To Apply"
