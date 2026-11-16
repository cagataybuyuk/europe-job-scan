"""GD-006 step 3: policy-submission ledger (v3) and policy authority. Synthetic only."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from ejs.contracts.submit import ROLLOUT_DAILY_CAPS, RolloutStage, submit_identity
from ejs.domain.submission import (
    ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus,
)
from ejs.persistence.policy_submission_ledger import (
    AWAITING_TTL, RESERVATION_TTL, PolicySubmissionLedger, enable_policy_submissions,
)
from ejs.persistence.submission_ledger import SqliteSubmissionLedger, SubmissionLedgerError
from ejs.services.policy_submission import PolicyDecision, RuntimeSwitch, authorize_manifest

from tests.test_execution_manifest import NOW as MANIFEST_NOW, base_document, encode

T0 = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)  # 09:00 Istanbul
REF = "evidence:sha256:" + "b" * 64
MANIFEST_SHA = "c" * 64
ON = RuntimeSwitch(final_submit_enabled=True, kill_switch_engaged=False)


def legacy_attempt():
    return SubmissionAttempt(
        submit_key="submit:legacy", execution_id="exec:legacy", opportunity_key="opp:legacy",
        requisition_id="REF7089X", candidate_profile_version="profile-1", policy_version="SUBMIT-1.0",
        validation_key="validation:legacy", requested_at=T0.isoformat(),
        status=SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:1")


def ident(n=1, profile="profile-2026-11-16"):
    return dict(opportunity_key=f"opp:synthetic:{n}", requisition_id=f"REQ{n}", profile_version=profile)


def reserve(ledger, n=1, *, now=T0, stage="R4", cap=None, execution=None, profile="profile-2026-11-16"):
    return ledger.reserve(**ident(n, profile), execution_id=execution or f"exec-{n}",
                          manifest_sha256=MANIFEST_SHA, rollout_stage=stage, daily_cap=cap, now=now)


def start(ledger, n=1, *, now=T0 + timedelta(seconds=5), execution=None):
    ledger.mark_started(**ident(n), execution_id=execution or f"exec-{n}", now=now)


def evidence(n=1, **changes):
    item = ConfirmationEvidence(
        submit_key=submit_identity(f"opp:synthetic:{n}", f"REQ{n}", "profile-2026-11-16"),
        evidence_type=ConfirmationType.CONFIRMATION_PAGE, evidence_ref=REF,
        observed_at=(T0 + timedelta(seconds=10)).isoformat(), exact_identity_match=True,
        execution_id=f"exec-{n}", requisition_id=f"REQ{n}")
    return replace(item, **changes)


@pytest.fixture
def legacy(tmp_path):
    return SqliteSubmissionLedger(tmp_path / "private" / "first-application.db")


@pytest.fixture
def ledger(legacy):
    return enable_policy_submissions(legacy, now=T0)


def confirmed_legacy(legacy):
    assert legacy.reserve_intent(legacy_attempt(), T0)
    legacy.mark_started("submit:legacy", "exec:legacy", T0 + timedelta(seconds=1))
    proof = ConfirmationEvidence(
        submit_key="submit:legacy", evidence_type=ConfirmationType.EMPLOYER_EMAIL, evidence_ref=REF,
        observed_at=(T0 + timedelta(seconds=2)).isoformat(), exact_identity_match=True,
        execution_id="exec:legacy", requisition_id="REF7089X")
    assert legacy.confirm("submit:legacy", proof, T0 + timedelta(seconds=3))
    return legacy


# ---- migration -------------------------------------------------------------------

def test_migration_preserves_confirmed_first_slot_and_is_idempotent(legacy):
    confirmed_legacy(legacy)
    before = legacy.snapshot()
    ledger = enable_policy_submissions(legacy, now=T0)
    assert legacy.snapshot() == before and before["status"] == "Confirmed"
    again = enable_policy_submissions(legacy, now=T0)
    assert type(again) is PolicySubmissionLedger
    reopened = SqliteSubmissionLedger(legacy.path)  # legacy view still validates v3
    assert reopened.snapshot() == before
    assert reopened.has_intent() and not reopened.reserve_intent(legacy_attempt(), T0)
    assert reserve(ledger) == "RESERVED"


def test_policy_view_requires_v3(legacy):
    with pytest.raises(SubmissionLedgerError):
        PolicySubmissionLedger(legacy)
    with pytest.raises(SubmissionLedgerError):
        enable_policy_submissions(object(), now=T0)  # type: ignore[arg-type]


def test_tampered_policy_rows_fail_closed(ledger, legacy):
    assert reserve(ledger) == "RESERVED"
    connection = sqlite3.connect(legacy.path)
    connection.execute("UPDATE policy_submissions SET status='Confirmed'")
    connection.commit()
    connection.close()
    with pytest.raises(SubmissionLedgerError):
        legacy.snapshot()


def test_unknown_extra_table_fails_closed(ledger, legacy):
    connection = sqlite3.connect(legacy.path)
    connection.execute("CREATE TABLE extra (x)")
    connection.commit()
    connection.close()
    with pytest.raises(SubmissionLedgerError):
        reserve(ledger)


# ---- duplicates across slots ---------------------------------------------------

def test_first_slot_vacancy_blocks_policy_reservation_under_any_profile(legacy):
    confirmed_legacy(legacy)
    ledger = enable_policy_submissions(legacy, now=T0)
    outcome = ledger.reserve(opportunity_key="opp:legacy", requisition_id="REF7089X",
                             profile_version="profile-2026-11-16", execution_id="exec-x",
                             manifest_sha256=MANIFEST_SHA, rollout_stage="R4", daily_cap=None, now=T0)
    assert outcome == "DUPLICATE"


def test_started_or_confirmed_key_is_consumed_forever(ledger):
    assert reserve(ledger) == "RESERVED"
    start(ledger)
    assert reserve(ledger, now=T0 + timedelta(days=3)) == "DUPLICATE"
    assert ledger.confirm(**ident(), execution_id="exec-1", evidence=evidence(), now=T0 + timedelta(seconds=11))
    assert reserve(ledger, now=T0 + timedelta(days=30)) == "DUPLICATE"
    # Same vacancy under a new profile version is still the same application.
    assert reserve(ledger, profile="profile-2027", now=T0 + timedelta(days=30)) == "DUPLICATE"
    assert ledger.status(**ident()) == "Confirmed"


def test_started_row_cannot_be_released_or_restarted(ledger):
    assert reserve(ledger) == "RESERVED"
    start(ledger)
    with pytest.raises(SubmissionLedgerError):
        ledger.release(**ident(), execution_id="exec-1", now=T0 + timedelta(seconds=6))
    with pytest.raises(SubmissionLedgerError):
        start(ledger, now=T0 + timedelta(seconds=7))


# ---- pre-dispatch release, busy, stale and awaiting user -------------------------

def test_release_before_dispatch_allows_a_later_attempt(ledger):
    assert reserve(ledger) == "RESERVED"
    ledger.release(**ident(), execution_id="exec-1", now=T0 + timedelta(seconds=1))
    assert ledger.status(**ident()) == "Released"
    assert reserve(ledger, execution="exec-1b", now=T0 + timedelta(hours=1)) == "RESERVED"
    start(ledger, execution="exec-1b", now=T0 + timedelta(hours=1, seconds=1))


def test_one_dispatch_in_flight_and_stale_reservation_expires(ledger):
    assert reserve(ledger, 1) == "RESERVED"
    assert reserve(ledger, 2, now=T0 + timedelta(minutes=1)) == "BUSY"
    later = T0 + RESERVATION_TTL + timedelta(seconds=1)
    assert reserve(ledger, 2, now=later) == "RESERVED"
    assert ledger.status(**ident(1)) == "Released"
    with pytest.raises(SubmissionLedgerError):
        start(ledger, 1, now=later)


def test_start_after_reservation_ttl_is_rejected(ledger):
    assert reserve(ledger) == "RESERVED"
    with pytest.raises(SubmissionLedgerError):
        start(ledger, now=T0 + RESERVATION_TTL + timedelta(seconds=1))


def test_awaiting_user_does_not_block_others_and_resumes(ledger):
    assert reserve(ledger, 1) == "RESERVED"
    ledger.await_user(**ident(1), execution_id="exec-1", now=T0 + timedelta(minutes=1))
    with pytest.raises(SubmissionLedgerError):
        start(ledger, 1, now=T0 + timedelta(minutes=2))  # never dispatch while waiting
    assert reserve(ledger, 2, now=T0 + timedelta(minutes=2)) == "RESERVED"
    start(ledger, 2, now=T0 + timedelta(minutes=3))
    resumed = T0 + timedelta(hours=2)
    ledger.resume(**ident(1), execution_id="exec-1", now=resumed)
    start(ledger, 1, now=resumed + timedelta(seconds=30))
    assert ledger.status(**ident(1)) == "Submitted / Unconfirmed"


def test_awaiting_user_expires_without_dispatch(ledger):
    assert reserve(ledger, 1) == "RESERVED"
    ledger.await_user(**ident(1), execution_id="exec-1", now=T0)
    late = T0 + AWAITING_TTL + timedelta(seconds=1)
    with pytest.raises(SubmissionLedgerError):
        ledger.resume(**ident(1), execution_id="exec-1", now=late)
    assert reserve(ledger, 2, now=late) == "RESERVED"
    assert ledger.status(**ident(1)) == "Released"


def test_resume_refused_while_another_dispatch_is_reserved(ledger):
    assert reserve(ledger, 1) == "RESERVED"
    ledger.await_user(**ident(1), execution_id="exec-1", now=T0)
    assert reserve(ledger, 2, now=T0 + timedelta(minutes=1)) == "RESERVED"
    with pytest.raises(SubmissionLedgerError):
        ledger.resume(**ident(1), execution_id="exec-1", now=T0 + timedelta(minutes=2))


def test_wrong_execution_cannot_drive_a_row(ledger):
    assert reserve(ledger) == "RESERVED"
    for call in (ledger.release, ledger.mark_started, ledger.await_user):
        with pytest.raises(SubmissionLedgerError):
            call(**ident(), execution_id="exec-other", now=T0 + timedelta(seconds=1))


# ---- caps --------------------------------------------------------------------------

def _dispatch(ledger, n, when, stage="R3", cap=5):
    outcome = reserve(ledger, n, now=when, stage=stage, cap=cap)
    if outcome == "RESERVED":
        start(ledger, n, now=when + timedelta(seconds=1))
    return outcome


def test_r3_cap_counts_istanbul_day_and_resets_next_day(ledger):
    for n in range(5):
        assert _dispatch(ledger, n, T0 + timedelta(minutes=n)) == "RESERVED"
    assert ledger.started_today(T0) == 5
    assert _dispatch(ledger, 99, T0 + timedelta(hours=1)) == "CAP_REACHED"
    # 21:30 UTC is 00:30 next day in Istanbul.
    next_day = datetime(2026, 11, 17, 21, 30, tzinfo=timezone.utc)
    assert _dispatch(ledger, 99, next_day) == "RESERVED"


def test_r4_has_no_daily_cap(ledger):
    for n in range(25):
        assert _dispatch(ledger, n, T0 + timedelta(minutes=n), stage="R4", cap=None) == "RESERVED"
    assert ledger.started_today(T0) == 25


def test_contract_caps_match_owner_decision():
    assert ROLLOUT_DAILY_CAPS[RolloutStage.R3_BOUNDED_ATS] == 5
    assert ROLLOUT_DAILY_CAPS[RolloutStage.R4_MULTI_ATS] < 0


@pytest.mark.parametrize("kwargs", [
    dict(stage="R9"), dict(stage="R4", cap=0), dict(stage="R4", cap=True), dict(execution="../x"), dict(execution=" "),
])
def test_bad_reservation_inputs_fail_closed(ledger, kwargs):
    with pytest.raises(SubmissionLedgerError):
        reserve(ledger, **kwargs)


# ---- confirmation ------------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    dict(exact_identity_match=False), dict(evidence_ref="evidence:other"), dict(execution_id="exec-2"),
    dict(requisition_id="REQ2"), dict(submit_key="submit:other"),
    dict(observed_at=(T0 - timedelta(hours=1)).isoformat()),
    dict(observed_at=(T0 + timedelta(days=1)).isoformat()),
])
def test_confirmation_must_bind_exactly(ledger, changes):
    assert reserve(ledger) == "RESERVED"
    start(ledger)
    assert not ledger.confirm(**ident(), execution_id="exec-1", evidence=evidence(**changes),
                              now=T0 + timedelta(seconds=11))
    assert ledger.status(**ident()) == "Submitted / Unconfirmed"


def test_confirmation_requires_a_started_row_and_is_idempotent(ledger):
    assert reserve(ledger) == "RESERVED"
    when = T0 + timedelta(seconds=11)
    assert not ledger.confirm(**ident(), execution_id="exec-1", evidence=evidence(), now=when)
    start(ledger)
    assert ledger.confirm(**ident(), execution_id="exec-1", evidence=evidence(), now=when)
    assert ledger.confirm(**ident(), execution_id="exec-1", evidence=evidence(), now=when)
    other = evidence(evidence_ref="evidence:sha256:" + "d" * 64)
    assert not ledger.confirm(**ident(), execution_id="exec-1", evidence=other, now=when)


# ---- policy authority -------------------------------------------------------------------

def manifest_bytes(**policy):
    doc = base_document()
    if policy:
        doc["policy"].update(policy)
    raw = encode(doc)
    return raw, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("switch,decision", [
    (RuntimeSwitch(), PolicyDecision.KILL_SWITCH),
    (RuntimeSwitch(final_submit_enabled=True), PolicyDecision.KILL_SWITCH),
    (RuntimeSwitch(kill_switch_engaged=False), PolicyDecision.RUNTIME_DISABLED),
    (RuntimeSwitch(final_submit_enabled=1, kill_switch_engaged=False), PolicyDecision.RUNTIME_DISABLED),
])
def test_runtime_switch_defaults_to_no_submit(ledger, switch, decision):
    raw, digest = manifest_bytes()
    result = authorize_manifest(ledger, raw, now=MANIFEST_NOW, switch=switch, expected_sha256=digest)
    assert result.decision is decision and not result.dispatch_allowed and result.manifest is None


def test_manifest_is_reserved_once(ledger):
    raw, digest = manifest_bytes(rollout_stage="R4", daily_cap=None)
    first = authorize_manifest(ledger, raw, now=MANIFEST_NOW, switch=ON, expected_sha256=digest)
    assert first.dispatch_allowed and first.manifest.rollout_stage == "R4"
    m = first.manifest
    ledger.mark_started(opportunity_key=m.opportunity_key, requisition_id=m.requisition_id,
                        profile_version=m.candidate_profile_version, execution_id=m.execution_id,
                        now=MANIFEST_NOW + timedelta(seconds=1))
    second = authorize_manifest(ledger, raw, now=MANIFEST_NOW + timedelta(minutes=1), switch=ON,
                                expected_sha256=digest)
    assert second.decision is PolicyDecision.DUPLICATE


def test_rejected_or_tampered_manifest_reserves_nothing(ledger):
    raw, digest = manifest_bytes()
    result = authorize_manifest(ledger, raw, now=MANIFEST_NOW, switch=ON, expected_sha256="0" * 64)
    assert result.decision is PolicyDecision.MANIFEST_REJECTED
    raw_r4_with_cap, digest2 = manifest_bytes(rollout_stage="R4", daily_cap=5)
    assert authorize_manifest(ledger, raw_r4_with_cap, now=MANIFEST_NOW, switch=ON,
                              expected_sha256=digest2).decision is PolicyDecision.MANIFEST_REJECTED
    assert ledger.started_today(MANIFEST_NOW) == 0


def test_wrong_ledger_type_fails_closed(legacy):
    raw, digest = manifest_bytes()
    result = authorize_manifest(legacy, raw, now=MANIFEST_NOW, switch=ON, expected_sha256=digest)  # type: ignore[arg-type]
    assert result.decision is PolicyDecision.LEDGER_ERROR



# ---- independent review of #108 ------------------------------------------------------

@pytest.mark.parametrize("opportunity,requisition", [
    ("opp:legacy", "ref7089x"), ("OPP:legacy", "REF7089X"), ("opp:other", " Ref7089x "),
])
def test_first_slot_requisition_blocks_case_and_space_variants(legacy, opportunity, requisition):
    confirmed_legacy(legacy)
    ledger = enable_policy_submissions(legacy, now=T0)
    assert ledger.reserve(opportunity_key=opportunity, requisition_id=requisition, profile_version="p2",
                          execution_id="exec-x", manifest_sha256=MANIFEST_SHA, rollout_stage="R4",
                          daily_cap=None, now=T0) == "DUPLICATE"


def test_separator_variants_are_one_vacancy(ledger):
    def go(req, profile, execution, when):
        return ledger.reserve(opportunity_key="opp:x", requisition_id=req, profile_version=profile,
                              execution_id=execution, manifest_sha256=MANIFEST_SHA, rollout_stage="R4",
                              daily_cap=None, now=when)
    assert go("REF-1", "p1", "e1", T0) == "RESERVED"
    ledger.mark_started(opportunity_key="opp:x", requisition_id="REF-1", profile_version="p1",
                        execution_id="e1", now=T0 + timedelta(seconds=1))
    for variant in ("REF_1", "ref.1", "Ref 1"):
        assert go(variant, "p2", "e2", T0 + timedelta(minutes=1)) == "DUPLICATE"


def test_awaiting_user_cannot_exceed_cap(ledger):
    assert reserve(ledger, 1, stage="R2", cap=1) == "RESERVED"
    ledger.await_user(**ident(1), execution_id="exec-1", now=T0 + timedelta(seconds=1))
    assert reserve(ledger, 2, stage="R2", cap=1, now=T0 + timedelta(seconds=2)) == "CAP_REACHED"


def test_cap_is_rechecked_at_dispatch(ledger, legacy):
    assert reserve(ledger, 1, stage="R2", cap=1) == "RESERVED"
    start(ledger, 1)
    assert reserve(ledger, 2, stage="R4", cap=None, now=T0 + timedelta(minutes=1)) == "RESERVED"
    connection = sqlite3.connect(legacy.path)  # simulate a capped row reserved before the start
    connection.execute("UPDATE policy_submissions SET daily_cap=1 WHERE status='Reserved'")
    connection.commit()
    connection.close()
    with pytest.raises(SubmissionLedgerError):
        start(ledger, 2, now=T0 + timedelta(minutes=2))


def test_expired_reservation_cannot_be_revived_through_awaiting(ledger):
    assert reserve(ledger) == "RESERVED"
    with pytest.raises(SubmissionLedgerError):
        ledger.await_user(**ident(), execution_id="exec-1", now=T0 + RESERVATION_TTL + timedelta(seconds=1))


# ---- v2 ledgers (reviewed scope) ---------------------------------------------------------

@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    from ejs.persistence import reviewed_scope_ledger as module
    from tests.test_reviewed_scope_ledger import NOW, execution_case, issue_scope, make_legacy, migrate_and_claim
    monkeypatch.setattr(module, "_now", lambda: NOW)
    path = tmp_path / "private" / "first-application.db"
    legacy = make_legacy(path)
    case = execution_case(legacy.snapshot())
    scope = issue_scope(case)
    view = migrate_and_claim((path, legacy, case, scope))
    return legacy, view, NOW


def test_v2_with_open_reviewed_attempt_cannot_migrate(reviewed):
    legacy, _view, now = reviewed
    with pytest.raises(SubmissionLedgerError):
        enable_policy_submissions(legacy, now=now)


def test_v2_after_grant_expiry_migrates_and_blocks_reviewed_target(reviewed):
    legacy, _view, now = reviewed
    ledger = enable_policy_submissions(legacy, now=now + timedelta(days=2))
    for requisition in ("REF00063", "ref00063"):
        assert ledger.reserve(opportunity_key="opp:any", requisition_id=requisition, profile_version="p9",
                              execution_id="exec-9", manifest_sha256=MANIFEST_SHA, rollout_stage="R4",
                              daily_cap=None, now=now + timedelta(days=2)) == "DUPLICATE"
    assert ledger.reserve(**ident(1), execution_id="exec-1", manifest_sha256=MANIFEST_SHA,
                          rollout_stage="R4", daily_cap=None, now=now + timedelta(days=2)) == "RESERVED"
