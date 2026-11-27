"""GD-006 step 4a: manifest-driven runtime with a synthetic in-memory browser."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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


def ALLOW(point):
    """Explicit always-allow runtime guard for tests that exercise unrelated behavior."""


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class FakeBrowser:
    """Synthetic SmartRecruiters-like page; values live only in memory."""

    def __init__(self, doc, clock, *, pages=None, **behaviour):
        self.doc, self.clock = doc, clock
        # pages: list of field lists per page (the live form). Default: the manifest's own.
        self.pages = pages or self._pages_from(doc)
        self.upload_control = {k: v for k, v in doc["form"]["cv_upload"].items() if k != "step"}
        self.upload_step = doc["form"]["cv_upload"].get("step", 0)
        self.page = 0
        self.values: dict[str, object] = {}
        self.calls: list[str] = []
        self.clicks = 0
        self.b = dict(challenge_at=None, owner_solves=True, overwrite=None, drift=False, url=URL,
                      ack=True, visible=True, upload_hash=None, submit_raises=False,
                      drop_attachment_on_wait=False, observed_tz=timezone.utc, owner_submits=False)
        self.b.update(behaviour)
        self.inspections = 0
        self.attachment = None

    @staticmethod
    def _pages_from(doc):
        pages = {}
        for item in doc["form"]["fields"]:
            pages.setdefault(item.get("step", 0), []).append(item)
        return [pages[i] for i in sorted(pages)]

    def open(self, url):
        self.calls.append("open")
        assert url == URL

    def inspect(self):
        self.inspections += 1
        fields = deepcopy(self.pages[self.page]) if self.page < len(self.pages) else []
        if self.b["drift"] and self.page == self.b.get("drift_page", 0):
            fields.append(dict(key="new_required", label="New", control="text", required=True, options=None))
        challenge = self.b["challenge_at"] == self.inspections
        last = self.page >= len(self.pages) - 1
        url = self.b["url"] if self.page == 0 else "https://jobs.smartrecruiters.com/oneclick-ui/screening"
        return dict(url=url, step=self.page,
                    fields=[{k: f[k] for k in ("key", "label", "control", "required", "options")} for f in fields],
                    cv_upload=self.upload_control if self.page == self.upload_step else None,
                    challenge=challenge, form_valid=True, submit_visible=last, next_visible=not last,
                    cv_attachment=self.attachment if self.page == self.upload_step else None)

    def advance(self):
        self.calls.append("advance")
        self.page += 1

    def write(self, key, control, value):
        self.calls.append("write:" + key)
        self.values[key] = value

    def readback(self, key, control):
        if self.b["overwrite"] == key and "upload" in self.calls:
            return "changed"
        return self.values.get(key)

    def upload(self, key, payload, filename):
        self.calls.append("upload")
        self.attachment = dict(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload), filename=filename,
                               visible=True)
        return UploadResult(self.b["upload_hash"] or hashlib.sha256(payload).hexdigest(), len(payload),
                            filename, True)

    def wait_for_owner(self, timeout_seconds):
        self.calls.append("wait_for_owner")
        if self.b["owner_solves"]:
            self.b["challenge_at"] = None
        if self.b["drop_attachment_on_wait"]:
            self.attachment = None
        if self.b["owner_submits"]:
            self.b["url"] = URL + "/thanks"
        return self.b["owner_solves"]

    def submit_once(self):
        self.calls.append("submit")
        self.clicks += 1
        if self.b["submit_raises"]:
            raise RuntimeError("network")
        return SubmitObservation(self.clock().astimezone(self.b["observed_tz"]), self.b["ack"], self.b["visible"], "e" * 64, False)


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
    result = PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock).run(
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
    (dict(url="https://evil.example/apply"), "POLICY_APP_SURFACE_CHANGED"),
    (dict(overwrite="first_name"), "POLICY_APP_READBACK_MISMATCH"),
    (dict(upload_hash="0" * 64), "POLICY_APP_UPLOAD_UNVERIFIED"),
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


def test_unresolved_owner_handoff_keeps_row_awaiting_and_never_clicks(env):
    result, browser, ledger, _ = run(env, challenge_at=1, owner_solves=False)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_OWNER_CHALLENGE_UNRESOLVED"
    assert browser.clicks == 0 and status(ledger) == "Awaiting User"


def test_non_utc_confirmation_time_is_recorded_in_utc(env):
    result, _, ledger, _ = run(env, observed_tz=timezone(timedelta(hours=3)))
    assert result.status == "EMPLOYER_CONFIRMED" and status(ledger) == "Confirmed"


class BackwardsClock(Clock):
    def __call__(self):
        value = super().__call__()
        return value - timedelta(hours=1) if self.now > NOW + timedelta(seconds=8) else value


def test_backwards_clock_keeps_ledger_consistent(env):
    base, cv, ledger = env
    clock = BackwardsClock()
    browser = FakeBrowser(base, clock, drift=False)
    raw = encode(base)
    runtime = PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock)
    result = runtime.run(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    # The per-boundary expiry reads consume clock ticks, so the backwards jump may now land after the
    # click: an observation older than mark_started is then (correctly) left unconfirmed, never confirmed.
    assert result.status in ("EMPLOYER_CONFIRMED", "BLOCKED", "SUBMITTED_UNCONFIRMED") and browser.clicks <= 1
    assert status(ledger) in ("Confirmed", "Released", "Submitted / Unconfirmed")  # ledger still readable


class Interrupt(BaseException):
    pass


def test_base_exception_before_start_releases_and_returns(env):
    base, cv, ledger = env
    clock = Clock()
    browser = FakeBrowser(base, clock)
    browser.upload = lambda *a: (_ for _ in ()).throw(Interrupt())
    raw = encode(base)
    result = PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    assert result.status == "BLOCKED" and status(ledger) == "Released"


def test_keyboard_interrupt_after_start_is_reraised_with_row_consumed(env):
    base, cv, ledger = env
    clock = Clock()
    browser = FakeBrowser(base, clock)
    def interrupted():
        browser.clicks += 1
        raise KeyboardInterrupt
    browser.submit_once = interrupted
    raw = encode(base)
    with pytest.raises(KeyboardInterrupt):
        PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock).run(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    assert browser.clicks == 1 and status(ledger) == "Submitted / Unconfirmed"


def test_naive_clock_fails_closed(env):
    base, cv, ledger = env
    raw = encode(base)
    runtime = PolicyApplicationRuntime(FakeBrowser(base, Clock()), ledger, guard=ALLOW, clock=lambda: datetime(2026, 11, 17))
    result = runtime.run(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_CLOCK" and status(ledger) is None


@pytest.mark.parametrize("behaviour", [
    dict(challenge_at=2, owner_submits=True), dict(challenge_at=2, drop_attachment_on_wait=True),
])
def test_after_owner_handoff_the_key_is_held_for_the_owner(env, behaviour):
    result, browser, ledger, clock = run(env, **behaviour)
    assert result.status == "BLOCKED" and browser.clicks == 0 and status(ledger) == "Awaiting User"
    again, browser2, _, _ = run(env)
    assert again.status == "DUPLICATE" and browser2.clicks == 0


def test_non_bytes_manifest_returns_a_result(env):
    base, cv, ledger = env
    runtime = PolicyApplicationRuntime(FakeBrowser(base, Clock()), ledger, guard=ALLOW, clock=Clock())
    result = runtime.run("not-bytes", expected_sha256="0" * 64, cv_path=cv, switch=ON)  # type: ignore[arg-type]
    assert result.status in ("BLOCKED", "MANIFEST_REJECTED") and status(ledger) is None


class SlowFillClock(Clock):
    """Jumps 31 minutes during the fill, after the owner resumed."""

    def __init__(self, browser_ref):
        super().__init__()
        self.browser_ref = browser_ref

    def __call__(self):
        value = super().__call__()
        browser = self.browser_ref[0]
        if browser is not None and "upload" in browser.calls and not getattr(self, "jumped", False):
            self.jumped = True
            self.now += timedelta(minutes=31)
            return self.now
        return value


def test_owner_hold_survives_an_expired_reservation_after_resume(env):
    base, cv, ledger = env
    ref = [None]
    clock = SlowFillClock(ref)
    browser = FakeBrowser(base, clock, challenge_at=1)
    ref[0] = browser
    raw = encode(base)
    result = PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    assert result.status == "BLOCKED" and browser.clicks == 0
    assert status(ledger) == "Awaiting User"
    again, browser2, _, _ = run(env)
    assert again.status == "DUPLICATE" and browser2.clicks == 0



# ---- GD-006 A1: multi-page forms and the observe pass ------------------------------------

def two_page(doc):
    """Personal page + CV (step 0); screening questions (step 1)."""
    doc = deepcopy(doc)
    for item in doc["form"]["fields"]:
        if item["key"] in ("work_auth", "sponsorship", "motivation", "gender"):
            item["step"] = 1
    from ejs.services.execution_manifest import form_fingerprint
    doc["form"]["fingerprint"] = form_fingerprint(doc["form"]["fields"], doc["form"]["cv_upload"])
    return doc


def observe_doc(doc):
    """Only page 0 is known; the engine must report page 1 and stop."""
    doc = deepcopy(doc)
    doc["pass"] = "observe"
    doc["form"]["fields"] = [f for f in doc["form"]["fields"] if f.get("step", 0) == 0]
    from ejs.services.execution_manifest import form_fingerprint
    doc["form"]["fingerprint"] = form_fingerprint(doc["form"]["fields"], doc["form"]["cv_upload"])
    return doc


def test_two_page_submit_pass_fills_each_page_and_submits_once(env):
    base, _, _ = env
    doc = two_page(base)
    result, browser, ledger, _ = run(env, doc=doc)
    assert result.status == "EMPLOYER_CONFIRMED" and browser.clicks == 1
    assert browser.calls.index("upload") < browser.calls.index("advance") < browser.calls.index("write:work_auth")
    assert status(ledger) == "Confirmed"


def test_observe_pass_reports_next_page_and_never_clicks(env):
    base, _, _ = env
    full = two_page(base)
    live_pages = FakeBrowser._pages_from(full)
    result, browser, ledger, _ = run(env, doc=observe_doc(full), pages=live_pages)
    assert result.status == "OBSERVED" and browser.clicks == 0 and "submit" not in browser.calls
    assert result.observation["step"] == 1 and result.observation["final"] is True
    assert {f["key"] for f in result.observation["fields"]} == {"work_auth", "sponsorship", "motivation", "gender"}
    assert all("value" not in f and "answer" not in f for f in result.observation["fields"])
    assert status(ledger) == "Released"
    # Pass 2 with the full manifest completes the application.
    done, browser2, _, _ = run(env, doc=full)
    assert done.status == "EMPLOYER_CONFIRMED" and browser2.clicks == 1


def test_observe_pass_does_not_echo_candidate_values(env):
    base, _, _ = env
    full = two_page(base)
    result, _, _, _ = run(env, doc=observe_doc(full), pages=FakeBrowser._pages_from(full))
    assert "synthetic@example.invalid" not in repr(result) and "Synthetic" not in repr(result)


def test_second_page_drift_blocks_before_click(env):
    base, _, _ = env
    result, browser, ledger, _ = run(env, doc=two_page(base), drift=True, drift_page=1)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_FORM_DRIFT" and browser.clicks == 0
    assert status(ledger) == "Released"


def test_observe_with_no_next_page_fails_closed(env):
    base, _, _ = env
    single = observe_doc(base)  # live form has only one page; nothing to advance to
    result, browser, ledger, _ = run(env, doc=single, pages=FakeBrowser._pages_from(single))
    assert result.status == "BLOCKED" and browser.clicks == 0 and status(ledger) == "Released"


class EchoBrowser(FakeBrowser):
    """Page 1 echoes a filled value into its label."""

    def inspect(self):
        snap = super().inspect()
        if self.page == 1 and snap["fields"]:
            snap["fields"][0]["label"] = "Confirm your email " + str(self.values.get("email"))
        return snap


@pytest.mark.parametrize("bad", [
    dict(label="x" * 600), dict(required="maybe"), dict(control={"evil": 1}), dict(key="k" * 300),
    dict(options=["Yes", {"nested": "Applicant"}]), dict(control="select", options=None),
])
def test_observation_rows_are_validated(env, bad):
    base, cv, ledger = env
    full = two_page(base)
    doc = observe_doc(full)
    pages = FakeBrowser._pages_from(full)
    pages[1] = deepcopy(pages[1])
    pages[1][0] = {**{k: pages[1][0][k] for k in ("key", "label", "control", "required", "options")}, **bad}
    result, browser, ledger, _ = run(env, doc=doc, pages=pages)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_OBSERVATION_INVALID"
    assert browser.clicks == 0 and result.observation is None


def test_observation_refuses_echoed_candidate_values(env):
    base, cv, ledger = env
    full = two_page(base)
    doc = observe_doc(full)
    clock = Clock()
    browser = EchoBrowser(doc, clock, pages=FakeBrowser._pages_from(full))
    raw = encode(doc)
    result = PolicyApplicationRuntime(browser, ledger, guard=ALLOW, clock=clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_OBSERVATION_INVALID"
    assert "synthetic@example.invalid" not in repr(result)


def test_submit_refused_when_live_form_has_another_page(env):
    base, _, _ = env
    full = two_page(base)
    pages = FakeBrowser._pages_from(full) + [[dict(key="extra", label="Extra", control="text",
                                                   required=False, options=None)]]
    result, browser, ledger, _ = run(env, doc=full, pages=pages)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_FORM_NOT_READY" and browser.clicks == 0



@pytest.mark.parametrize("disguise", [
    lambda v: v.upper(), lambda v: " ".join(v), lambda v: "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in v),
])
def test_disguised_echo_is_refused(env, disguise):
    base, cv, ledger = env
    full = two_page(base)
    doc = observe_doc(full)

    class Disguised(FakeBrowser):
        def inspect(self):
            snap = super().inspect()
            if self.page == 1:
                snap["fields"][0]["label"] = "Check " + disguise(str(self.values.get("email")))
            return snap

    clock = Clock()
    raw = encode(doc)
    result = PolicyApplicationRuntime(Disguised(doc, clock, pages=FakeBrowser._pages_from(full)), ledger,
                                      guard=ALLOW, clock=clock).run(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                                       cv_path=cv, switch=ON)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_OBSERVATION_INVALID"
