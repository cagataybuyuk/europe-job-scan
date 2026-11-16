"""GD-006 step 4a: manifest-driven runtime with a synthetic in-memory browser."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import os

import pytest

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.policy_application_runtime import (
    PolicyApplicationRuntime, SubmitObservation, UploadResult,
)
from ejs.services.policy_submission import RuntimeSwitch
from tests.test_execution_manifest import NOW, URL, base_document, encode

ON = RuntimeSwitch(final_submit_enabled=True, kill_switch_engaged=False)
PDF = b"%PDF-1.7 synthetic test document\n" + b"0" * 200


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class FakeBrowser:
    """Synthetic SmartRecruiters-like page; values live only in memory."""

    def __init__(self, doc, clock, **behaviour):
        self.doc, self.clock = doc, clock
        self.fields = deepcopy(doc["form"]["fields"])
        self.upload_control = dict(doc["form"]["cv_upload"])
        self.values: dict[str, object] = {}
        self.calls: list[str] = []
        self.clicks = 0
        self.b = dict(challenge_at=None, owner_solves=True, overwrite=None, drift=False, url=URL,
                      ack=True, visible=True, upload_hash=None, submit_raises=False)
        self.b.update(behaviour)
        self.inspections = 0

    def open(self, url):
        self.calls.append("open")
        assert url == URL

    def inspect(self):
        self.inspections += 1
        fields = deepcopy(self.fields)
        if self.b["drift"]:
            fields.append(dict(key="new_required", label="New", control="text", required=True, options=None))
        challenge = self.b["challenge_at"] == self.inspections
        return dict(url=self.b["url"], fields=[{k: f[k] for k in ("key", "label", "control", "required", "options")}
                                               for f in fields],
                    cv_upload=self.upload_control, challenge=challenge, form_valid=True, submit_visible=True)

    def write(self, key, control, value):
        self.calls.append("write:" + key)
        self.values[key] = value

    def readback(self, key, control):
        if self.b["overwrite"] == key and "upload" in self.calls:
            return "changed"
        return self.values.get(key)

    def upload(self, key, payload, filename):
        self.calls.append("upload")
        return UploadResult(self.b["upload_hash"] or hashlib.sha256(payload).hexdigest(), len(payload),
                            filename, True)

    def wait_for_owner(self, timeout_seconds):
        self.calls.append("wait_for_owner")
        if self.b["owner_solves"]:
            self.b["challenge_at"] = None
        return self.b["owner_solves"]

    def submit_once(self):
        self.calls.append("submit")
        self.clicks += 1
        if self.b["submit_raises"]:
            raise RuntimeError("network")
        return SubmitObservation(self.clock(), self.b["ack"], self.b["visible"], "e" * 64, False)


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "cv"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    cv = folder / "Synthetic_CV.pdf"
    cv.write_bytes(PDF)
    os.chmod(cv, 0o600)
    doc = base_document()
    doc["cv"].update(sha256=hashlib.sha256(PDF).hexdigest(), size_bytes=len(PDF))
    doc["policy"].update(rollout_stage="R4", daily_cap=None)
    legacy = SqliteSubmissionLedger(tmp_path / "private" / "first-application.db")
    ledger = enable_policy_submissions(legacy, now=NOW)
    return doc, cv, ledger


def run(env, *, switch=ON, doc=None, cv=None, **behaviour):
    base, cv_default, ledger = env
    doc = doc or base
    clock = Clock()
    browser = FakeBrowser(doc, clock, **behaviour)
    raw = encode(doc)
    result = PolicyApplicationRuntime(browser, ledger, clock=clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv or cv_default, switch=switch)
    return result, browser, ledger, clock


def status(ledger):
    return ledger.status(opportunity_key="opp:example:sr:744000000000001", requisition_id="REF001",
                         profile_version="profile-2026-11-16")


def test_happy_path_fills_uploads_submits_once_and_confirms(env):
    result, browser, ledger, _ = run(env)
    assert result.status == "EMPLOYER_CONFIRMED" and result.applied and result.submit_clicks == 1
    assert browser.clicks == 1 and status(ledger) == "Confirmed"
    assert "write:gender" not in browser.calls  # Blank Optional is never written
    assert browser.calls.index("upload") < browser.calls.index("submit")
    assert browser.values["marketing"] is False and browser.values["privacy"] is True


def test_second_run_is_duplicate_without_opening_the_form(env):
    run(env)
    result, browser, _, _ = run(env)
    assert result.status == "DUPLICATE" and browser.calls == [] and browser.clicks == 0


@pytest.mark.parametrize("switch,expected", [
    (RuntimeSwitch(), "KILL_SWITCH"), (RuntimeSwitch(kill_switch_engaged=False), "RUNTIME_DISABLED"),
])
def test_switch_blocks_before_any_browser_action(env, switch, expected):
    result, browser, ledger, _ = run(env, switch=switch)
    assert result.status == expected and browser.calls == [] and status(ledger) is None


@pytest.mark.parametrize("behaviour,code", [
    (dict(drift=True), "POLICY_APP_FORM_DRIFT"),
    (dict(url=URL + "x"), "POLICY_APP_SURFACE_CHANGED"),
    (dict(overwrite="first_name"), "POLICY_APP_READBACK_MISMATCH"),
    (dict(upload_hash="0" * 64), "POLICY_APP_UPLOAD_UNVERIFIED"),
    (dict(challenge_at=1, owner_solves=False), "POLICY_APP_OWNER_CHALLENGE_UNRESOLVED"),
])
def test_pre_dispatch_failures_release_and_never_click(env, behaviour, code):
    result, browser, ledger, _ = run(env, **behaviour)
    assert result.status == "BLOCKED" and result.code == code and result.submit_clicks == 0
    assert browser.clicks == 0 and status(ledger) == "Released"
    # Released before dispatch: a later, fresh run may try again.
    again, browser2, _, _ = run(env)
    assert again.status == "EMPLOYER_CONFIRMED" and browser2.clicks == 1


@pytest.mark.parametrize("when", [1, 2])  # on open, or after the upload
def test_owner_solves_challenge_then_runtime_reverifies_and_submits(env, when):
    result, browser, ledger, _ = run(env, challenge_at=when)
    assert result.status == "EMPLOYER_CONFIRMED"
    assert "wait_for_owner" in browser.calls and browser.calls.index("wait_for_owner") < browser.calls.index("submit")


@pytest.mark.parametrize("behaviour", [dict(ack=False), dict(visible=False), dict(submit_raises=True)])
def test_unverified_outcome_after_click_is_never_retried(env, behaviour):
    result, browser, ledger, _ = run(env, **behaviour)
    assert result.status == "SUBMITTED_UNCONFIRMED" and result.submit_clicks == -1 and not result.applied
    assert browser.clicks == 1 and status(ledger) == "Submitted / Unconfirmed"
    again, browser2, _, _ = run(env)
    assert again.status == "DUPLICATE" and browser2.calls == []


def test_cv_must_match_manifest_and_be_owner_only(env):
    doc, cv, _ = env
    cv.write_bytes(PDF + b"tampered")
    result, browser, ledger, _ = run(env)
    assert result.code == "POLICY_APP_CV" and browser.calls == [] and status(ledger) == "Released"
    cv.write_bytes(PDF)
    os.chmod(cv, 0o644)
    result, _, _, _ = run(env)
    assert result.code == "POLICY_APP_CV"


def test_cv_symlink_is_refused(env, tmp_path):
    doc, cv, _ = env
    link_dir = tmp_path / "linked"
    link_dir.mkdir(mode=0o700)
    link = link_dir / "Synthetic_CV.pdf"
    link.symlink_to(cv)
    result, _, _, _ = run(env, cv=link)
    assert result.code == "POLICY_APP_CV"


def test_invalid_manifest_reserves_nothing(env):
    doc, _, _ = env
    bad = deepcopy(doc)
    bad["form"]["fields"][3]["answer"]["source_ref"] = "user_fact:identity.first_name"
    result, browser, ledger, _ = run(env, doc=bad)
    assert result.status == "MANIFEST_REJECTED" and browser.calls == [] and status(ledger) is None


def test_results_carry_no_candidate_values(env):
    result, _, _, _ = run(env, overwrite="email")
    text = repr(result)
    assert "synthetic@example.invalid" not in text and "Synthetic" not in text
