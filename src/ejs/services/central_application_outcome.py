"""Bounded confirmed-outcome synchronization; no built-in network transport.

The trusted host pins the source, binding, canonical ledger and reviewed transport.
This module neither authenticates a reviewer nor turns a claimed atomic transport
into one. Direct Sheets read-then-write is deliberately not an implementation of
the locked compare-and-set contract below. No runtime wires this TEST service.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import unicodedata
from typing import Protocol
from zoneinfo import ZoneInfo

from ejs.contracts.mw5 import opportunity_key
from ejs.contracts.submit import submit_identity
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.persistence.reviewed_scope_ledger import ReviewedScopeLedger, require_owned_scope_view
from ejs.services.mw5_workspace import CellPatch


_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,1023}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SOURCE = re.compile(r"[a-f0-9]{40}\Z")
_EVIDENCE = re.compile(r"evidence:sha256:[a-f0-9]{64}\Z")
_POSTING = re.compile(r"https://jobs\.smartrecruiters\.com/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/[0-9]{8,20}(?:-[A-Za-z0-9_-]{1,200})?\Z")
_LATER_STATES = frozenset({"Recruiter Contacted", "Screening", "Assessment", "Interview 1",
    "Interview 2", "Final Interview", "Offer", "Accepted", "Rejected", "Withdrawn", "Closed", "Hired"})
_ERROR = "CENTRAL_APPLICATION_SYNC_REJECTED"
_TRACKER_TIMEZONE = "Europe/Istanbul"
_APPLICATION_HEADERS = (
    "Date Added", "Company", "Role", "Country", "City", "Source URL",
    "Role Fit Score", "Sponsorship", "Role Priority", "Status", "Applied Date",
    "CV Persona", "Recruiter", "Recruiter Contacted", "Salary Expectation",
    "Next Action", "Next Action Date", "Notes", "Application Source",
    "Recruiter LinkedIn", "Outreach Date", "Follow-up Due", "Follow-up Status",
    "Last Update",
)


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError(_ERROR)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _hash(value: str) -> str:
    _require(type(value) is str and _OPAQUE.fullmatch(value) is not None)
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _utc(value: datetime) -> datetime:
    _require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() == timedelta(0))
    return value.astimezone(timezone.utc)


def _time(value: str) -> datetime:
    _require(type(value) is str and len(value) <= 40)
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True)
class CentralApplicationBinding:
    """Externally pinned destination and public target identity, never credentials."""

    spreadsheet_id: str
    sheet_id: int
    row_number: int
    headers: tuple[str, ...]
    company: str
    role: str
    country: str
    tracked_url: str
    opportunity_key: str
    posting_url: str
    requisition_id: str
    candidate_profile_version: str
    submit_key: str
    execution_id: str
    evidence_ref: str
    source_sha: str
    reviewed_at: str
    sheet_name: str = "Applications"
    time_zone: str = _TRACKER_TIMEZONE
    confirmation_kind: str = "ATS_ACKNOWLEDGEMENT"


@dataclass(frozen=True)
class CentralApplicationRow:
    row_number: int
    cells: tuple[str, ...]  # Complete A:X displayed/normalized values.


@dataclass(frozen=True)
class CentralApplicationRead:
    spreadsheet_id: str
    sheet_id: int
    sheet_name: str
    headers: tuple[str, ...]
    identity_matches: tuple[CentralApplicationRow, ...]
    status_allowed_values: tuple[str, ...]


@dataclass(frozen=True)
class CentralApplicationWrite:
    spreadsheet_id: str
    sheet_id: int
    sheet_name: str
    row_number: int
    identity_key: tuple[str, str, str]
    operation_key: str
    expected_fingerprint: str
    patches: tuple[CellPatch, ...]


@dataclass(frozen=True)
class CentralApplicationReceipt:
    operation_key: str
    comparison_succeeded: bool
    wrote: bool


class LockedCentralApplicationTransport(Protocol):
    """Trusted transport with an actually serialized destination transaction.

    Reads must return ALL exact company/role/country identity matches, not only
    the requested row. CAS must under the same writer lock re-read the binding,
    headers, validation, ALL matching identity rows and their complete A:X
    fingerprint, reject missing/duplicate matches, and commit only the three
    supplied cells. A duplicate inserted after the initial read must prevent the
    write, not merely fail its readback. The lock must cover every competing writer;
    Apps Script LockService alone does not serialize direct connector/manual
    edits. A reviewed implementation must account for that limitation.
    """

    atomic_row_compare_and_set: bool

    def read_bound_rows(self, binding: CentralApplicationBinding) -> CentralApplicationRead: ...

    def compare_and_set(self, request: CentralApplicationWrite) -> CentralApplicationReceipt: ...


def central_binding_sha256(binding: CentralApplicationBinding) -> str:
    """Reviewer helper only; calculating this digest grants no authority."""
    _require(type(binding) is CentralApplicationBinding)
    return hashlib.sha256(_canonical(asdict(binding))).hexdigest()


def _validate_binding(binding: CentralApplicationBinding, pin: str, source_sha: str, now: datetime) -> None:
    _require(type(binding) is CentralApplicationBinding)
    _require(type(pin) is str and _SHA.fullmatch(pin) is not None
             and hmac.compare_digest(central_binding_sha256(binding), pin))
    _require(type(source_sha) is str and _SOURCE.fullmatch(source_sha) is not None
             and binding.source_sha == source_sha)
    _require(type(binding.spreadsheet_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{20,100}", binding.spreadsheet_id) is not None)
    _require(type(binding.sheet_id) is int and 0 <= binding.sheet_id <= 2**31 - 1)
    _require(type(binding.row_number) is int and 2 <= binding.row_number <= 1_000_000)
    _require(binding.sheet_name == "Applications" and binding.time_zone == _TRACKER_TIMEZONE
             and binding.confirmation_kind == "ATS_ACKNOWLEDGEMENT")
    # Column letters below have fixed business meaning. An independently pinned
    # reordered/renamed schema is still incompatible with those write positions.
    _require(type(binding.headers) is tuple and binding.headers == _APPLICATION_HEADERS)
    _require(all(type(x) is str and 0 < len(x) <= 500 and not any(ord(c) < 32 for c in x)
                 for x in (binding.company, binding.role, binding.country)))
    _require(binding.opportunity_key == opportunity_key(binding.company, binding.role, binding.country))
    _require(type(binding.tracked_url) is str and len(binding.tracked_url) <= 2048
             and re.fullmatch(r"https://[^\s?#@]+", binding.tracked_url) is not None)
    _require(type(binding.posting_url) is str and _POSTING.fullmatch(binding.posting_url) is not None)
    _require(type(binding.evidence_ref) is str and _EVIDENCE.fullmatch(binding.evidence_ref) is not None)
    for value in (binding.submit_key, binding.execution_id, binding.requisition_id,
                  binding.opportunity_key, binding.candidate_profile_version):
        _hash(value)
    _require(binding.submit_key == submit_identity(binding.opportunity_key,
        binding.requisition_id, binding.candidate_profile_version))
    reviewed = _time(binding.reviewed_at)
    _require(timedelta(0) <= now - reviewed <= timedelta(minutes=5))


def _confirmed_dates(ledger: SqliteSubmissionLedger | ReviewedScopeLedger, binding: CentralApplicationBinding,
                     now: datetime) -> tuple[str, str]:
    _require(type(ledger) in (SqliteSubmissionLedger, ReviewedScopeLedger))
    if type(ledger) is ReviewedScopeLedger:
        require_owned_scope_view(ledger)
    snapshot = ledger.snapshot()
    _require(type(snapshot) is dict and snapshot.get("status") == "Confirmed")
    for key, expected in (("submit_key_digest", _hash(binding.submit_key)),
                          ("execution_digest", _hash(binding.execution_id)),
                          ("requisition_digest", _hash(binding.requisition_id)),
                          ("evidence_digest", binding.evidence_ref.removeprefix("evidence:sha256:"))):
        _require(type(snapshot.get(key)) is str and hmac.compare_digest(snapshot[key], expected))
    confirmed = _time(snapshot["confirmed_at"])
    _require(confirmed <= now)
    local = ZoneInfo(binding.time_zone)
    return confirmed.astimezone(local).date().isoformat(), now.astimezone(local).date().isoformat()


def _row(read: CentralApplicationRead, binding: CentralApplicationBinding) -> CentralApplicationRow:
    _require(type(read) is CentralApplicationRead
             and (read.spreadsheet_id, read.sheet_id, read.sheet_name, read.headers)
             == (binding.spreadsheet_id, binding.sheet_id, binding.sheet_name, binding.headers))
    _require(type(read.status_allowed_values) is tuple
             and all(type(x) is str and x and x == x.strip()
                     and unicodedata.normalize("NFKC", x) == x
                     and not any(unicodedata.category(ch) in {"Cf", "Cc", "Zl", "Zp"} for ch in x)
                     for x in read.status_allowed_values)
             and len(set(read.status_allowed_values)) == len(read.status_allowed_values)
             and "Applied" in read.status_allowed_values)
    _require(type(read.identity_matches) is tuple and len(read.identity_matches) == 1)
    row = read.identity_matches[0]
    _require(type(row) is CentralApplicationRow and type(row.row_number) is int
             and row.row_number == binding.row_number and type(row.cells) is tuple
             and len(row.cells) == 24 and all(type(x) is str and len(x) <= 10000 for x in row.cells))
    _require((row.cells[1], row.cells[2], row.cells[3], row.cells[5])
             == (binding.company, binding.role, binding.country, binding.tracked_url))
    return row


def _fingerprint(read: CentralApplicationRead) -> str:
    return hashlib.sha256(_canonical((read.spreadsheet_id, read.sheet_id, read.sheet_name,
        read.headers, read.status_allowed_values,
        tuple((match.row_number, match.cells) for match in read.identity_matches)))).hexdigest()


def _result(status: str, wrote: bool | None = False, *, readback: bool = False) -> dict:
    return {"status": status, "applications_write_performed": wrote,
            "central_readback_verified": readback, "submit_attempt_performed": False}


def sync_confirmed_application(*, ledger: SqliteSubmissionLedger | ReviewedScopeLedger,
                               binding: CentralApplicationBinding, binding_sha256: str,
                               reviewed_source_sha: str, transport: LockedCentralApplicationTransport,
                               now: datetime, write_enabled: bool = False) -> dict:
    """One exact confirmed record, three cells, at most one CAS; never submit.

    The caller independently reviews the actual ATS acknowledgement and target
    URL; a ledger snapshot alone does not authenticate either. All exceptions and
    results are sanitized. An uncertain transport result is not retried here.
    """
    attempted = False
    wrote: bool | None = False
    try:
        now = _utc(now)
        _validate_binding(binding, binding_sha256, reviewed_source_sha, now)
        applied_date, update_date = _confirmed_dates(ledger, binding, now)
        _require(type(write_enabled) is bool)
        first_read = transport.read_bound_rows(binding)
        first = _row(first_read, binding)
        status = first.cells[9]
        if status in _LATER_STATES:
            return _result("LATER_STATE_PRESERVED", readback=True)
        if status == "Applied":
            _require(first.cells[10] == applied_date)
            return _result("ALREADY_RECORDED", readback=True)
        _require(status == "To Apply" and first.cells[10] == "")
        if not write_enabled:
            return _result("CENTRAL_WRITE_DISABLED")
        _require(getattr(transport, "atomic_row_compare_and_set", None) is True)
        request = CentralApplicationWrite(binding.spreadsheet_id, binding.sheet_id,
            binding.sheet_name, binding.row_number,
            (binding.company, binding.role, binding.country),
            hashlib.sha256(_canonical(("EJS-CENTRAL-APPLICATION-1", central_binding_sha256(binding),
                binding.evidence_ref, applied_date))).hexdigest(), _fingerprint(first_read),
            (CellPatch("J", binding.row_number, "Applied"), CellPatch("K", binding.row_number, applied_date),
             CellPatch("X", binding.row_number, update_date)))
        # Re-read the canonical confirmation immediately before the sole write.
        _require(_confirmed_dates(ledger, binding, now) == (applied_date, update_date))
        attempted = True
        wrote = None
        receipt = transport.compare_and_set(request)
        _require(type(receipt) is CentralApplicationReceipt and receipt.operation_key == request.operation_key
                 and type(receipt.comparison_succeeded) is bool and type(receipt.wrote) is bool
                 and (receipt.comparison_succeeded or not receipt.wrote))
        wrote = receipt.wrote
        if not receipt.comparison_succeeded:
            return _result("CONCURRENT_CHANGE")
        after_read = transport.read_bound_rows(binding)
        after = _row(after_read, binding)
        expected = list(first.cells)
        for patch in request.patches:
            expected[{"J": 9, "K": 10, "X": 23}[patch.column]] = patch.value
        if after.cells == tuple(expected):
            return _result("CENTRAL_RECORDED" if wrote else "ALREADY_RECORDED", wrote, readback=True)
        if after.cells[9] in _LATER_STATES:
            # A later result must never be repaired back to Applied. This is not
            # proof that the original three-cell postcondition was observed.
            return _result("LATER_STATE_PRESERVED", wrote, readback=False)
        return _result("CENTRAL_READBACK_MISMATCH", wrote)
    except Exception:
        return _result("CENTRAL_SYNC_UNCERTAIN" if attempted else "CENTRAL_SYNC_BLOCKED", wrote)
