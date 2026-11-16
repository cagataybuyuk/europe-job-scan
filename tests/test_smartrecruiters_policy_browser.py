"""GD-006 A1 4b-2: SmartRecruiters policy browser on real Chromium, fully offline.

Every request is fulfilled locally after the PolicyNetworkBoundary decided. The
employer, candidate and CV are synthetic. No cookie, real employer or network.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from playwright.sync_api import sync_playwright

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.execution_manifest import form_fingerprint
from ejs.services.policy_application_runtime import PolicyApplicationRuntime
from ejs.services.policy_submission import RuntimeSwitch
from ejs.services.smartrecruiters_policy_browser import (
    PolicyNetworkBoundary, SmartRecruitersPolicyBrowser, control_key,
)
from tests.test_execution_manifest import NOW, URL, base_document, encode, field

ON = RuntimeSwitch(final_submit_enabled=True, kill_switch_engaged=False)
PDF = b"%PDF-1.4\n% SYNTHETIC OFFLINE RESUME ONLY\n%%EOF\n"
COMPANY, POSTING = "ExampleCo", "744000000000001"
PUBLICATION = "01234567-89ab-4cde-8f01-234567890abc"
COMPANY_ID = "a" * 24
BASE = f"https://jobs.smartrecruiters.com/oneclick-ui/company/{COMPANY}/publication/{PUBLICATION}"
ENTRY, SCREENING, SUCCESS = (BASE + "?dcr_ci=" + COMPANY, BASE + "/screening?dcr_ci=" + COMPANY,
                             BASE + "/success?dcr_ci=" + COMPANY)
APPLICATION = f"https://jobs.smartrecruiters.com/oneclick-ui/api/company/{COMPANY}/publication/{PUBLICATION}/application"
UPLOAD = (f"https://jobs.smartrecruiters.com/oneclick-ui/api/company/{COMPANY_ID}/attachment/resume"
          "?filename=Synthetic_CV.pdf")


def _executable():
    with sync_playwright() as playwright:
        managed = playwright.chromium.executable_path
    for candidate in (os.environ.get("EJS_TEST_CHROME"), managed, shutil.which("google-chrome")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def chromium():
    executable = _executable()
    if executable is None:
        pytest.skip("No Chromium available")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        yield browser
        browser.close()


SCREENING_HTML = """
  <label for="salary">Expected salary</label><input id="salary" type="number" required>
  <label for="notice">Notice period</label><select id="notice" required>
    <option>Choose</option><option>Immediate</option><option>One month</option></select>
  <div id="sponsor" role="radiogroup" aria-label="Do you require sponsorship?" aria-required="true">
    <input type="radio" name="sponsor" aria-label="Yes" required><input type="radio" name="sponsor" aria-label="No"></div>
  <label for="privacy">Privacy acknowledgement</label><input id="privacy" type="checkbox" required>
  EXTRA
  <button id="submit" type="button">Submit</button>
"""


def page_html(*, extra="", next_label="Next", captcha=False, success=True):
    screening = SCREENING_HTML.replace("EXTRA", extra)
    return f"""<!doctype html><html><head><link rel="icon" href="data:,"></head><body>
      <main id="form"><label for="first">First name</label><input id="first" required>
      <label for="email">Email</label><input id="email" type="email" required>
      <label for="resume">Resume</label><input id="resume" type="file" required><span id="uploaded"></span>
      <button id="next" type="button">{next_label}</button></main>
      <script>
        function showScreening() {{
          history.pushState({{}}, '', {json.dumps(SCREENING)});
          document.querySelector('#form').innerHTML = {json.dumps(screening)};
          {"document.body.insertAdjacentHTML('beforeend','<p>Verify you are human</p>');" if captcha else ""}
          document.querySelector('#submit').addEventListener('click', async () => {{
            const response = await fetch({json.dumps(APPLICATION)}, {{method: 'POST', body: '{{}}'}});
            const data = await response.json();
            if (response.ok && data.success) {{
              history.pushState({{}}, '', {json.dumps(SUCCESS)});
              document.querySelector('#form').innerHTML = '<h1>Application received</h1>';
            }}
          }});
        }}
        document.querySelector('#next').addEventListener('click', showScreening);
        document.querySelector('#resume').addEventListener('change', async event => {{
          const body = new FormData(); body.append('file', event.target.files[0]);
          const response = await fetch({json.dumps(UPLOAD)}, {{method: 'POST', body}});
          const data = await response.json();
          document.querySelector('#uploaded').textContent = data.fileName;
        }});
      </script></body></html>"""


POSTING_HTML = f"""<!doctype html><html><body><h1>Synthetic Analyst</h1>
  <a href="{ENTRY}">I'm interested</a></body></html>"""


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def manifest_doc(*, observe=False):
    doc = base_document()
    page0 = [field(control_key("First name"), "candidate.first_name", "Synthetic", label="First name"),
             field(control_key("Email"), "candidate.email", "synthetic@example.invalid", control="email",
                   label="Email")]
    page1 = [field(control_key("Expected salary"), "compensation.salary_expectation", "37000",
                   mode="Approved Policy", ref="policy:SUBMIT-1.0:A3", label="Expected salary"),
             field(control_key("Notice period"), "employment.notice_period", "Immediate", control="select",
                   options=["Choose", "Immediate", "One month"], label="Notice period"),
             field(control_key("Do you require sponsorship?"), "work.sponsorship_required", "Yes",
                   control="radio", options=["Yes", "No"], label="Do you require sponsorship?"),
             field(control_key("Privacy acknowledgement"), "consent.privacy_acknowledgement", True,
                   mode="Approved Policy", ref="policy:SUBMIT-1.0:A4", control="checkbox",
                   label="Privacy acknowledgement")]
    for item in page1:
        item["step"] = 1
    doc["form"]["fields"] = page0 if observe else page0 + page1
    doc["form"]["cv_upload"] = dict(key=control_key("Resume"), label="Resume", required=True)
    doc["form"]["fingerprint"] = form_fingerprint(doc["form"]["fields"], doc["form"]["cv_upload"])
    doc["cv"].update(sha256=hashlib.sha256(PDF).hexdigest(), size_bytes=len(PDF))
    doc["policy"].update(rollout_stage="R4", daily_cap=None)
    if observe:
        doc["pass"] = "observe"
    return doc


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "cv"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    cv = folder / "Synthetic_CV.pdf"
    cv.write_bytes(PDF)
    os.chmod(cv, 0o600)
    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "private" / "ledger.db"), now=NOW)
    return cv, ledger


@contextmanager
def session(chromium, *, html=None, ack=True, upload_name="Synthetic_CV.pdf"):
    context = chromium.new_context(service_workers="block")
    seen = []
    html = html or page_html()

    def fulfill(route):
        request = route.request
        seen.append((request.method, request.url))
        if request.method == "GET" and request.url == URL:
            route.fulfill(status=200, content_type="text/html", body=POSTING_HTML)
        elif request.method == "GET" and request.url == ENTRY:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif request.method == "POST" and request.url == UPLOAD:
            route.fulfill(status=201, content_type="application/json",
                          body=json.dumps(dict(fileId="synthetic-file", fileName=upload_name)))
        elif request.method == "POST" and request.url == APPLICATION:
            route.fulfill(status=201, content_type="application/json", body=json.dumps(dict(success=ack)))
        else:
            route.fulfill(status=404, body="")

    context.route("**/*", fulfill)
    clock = Clock()
    boundary = PolicyNetworkBoundary(COMPANY, POSTING, "Synthetic_CV.pdf")
    browser = SmartRecruitersPolicyBrowser(context, boundary, poll_seconds=0.05, clock=clock)
    try:
        yield browser, boundary, seen, clock
    finally:
        context.close()


def run(browser, ledger, clock, doc, cv, *, wait=0.2):
    raw = encode(doc)
    return PolicyApplicationRuntime(browser, ledger, clock=clock, owner_wait_seconds=wait).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)


def status(ledger):
    return ledger.status(opportunity_key="opp:example:sr:744000000000001", requisition_id="REF001",
                         profile_version="profile-2026-11-16")


def posts(seen):
    return [url for method, url in seen if method == "POST"]


def test_control_key_is_deterministic():
    assert control_key("Expected salary (EUR)") == "expected-salary-eur"
    assert control_key("  Do you require sponsorship? ") == "do-you-require-sponsorship"
    assert control_key("E-mail") == control_key("e mail") == "e-mail"


def test_submit_pass_fills_both_pages_uploads_and_submits_once(chromium, env):
    cv, ledger = env
    with session(chromium) as (browser, boundary, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(), cv)
    assert result.status == "EMPLOYER_CONFIRMED", result
    assert posts(seen) == [UPLOAD, APPLICATION]
    assert boundary.applications_allowed == 1 and boundary.blocked_writes == 0
    assert status(ledger) == "Confirmed"


def test_observe_pass_reports_screening_and_never_posts_application(chromium, env):
    cv, ledger = env
    with session(chromium) as (browser, boundary, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(observe=True), cv)
    assert result.status == "OBSERVED", result
    assert APPLICATION not in posts(seen) and boundary.applications_allowed == 0
    keys = {row["key"] for row in result.observation["fields"]}
    assert keys == {"expected-salary", "notice-period", "do-you-require-sponsorship", "privacy-acknowledgement"}
    assert result.observation["final"] is True and status(ledger) == "Released"
    # The full manifest built from that observation completes in pass 2.
    with session(chromium) as (browser, _, seen2, clock):
        done = run(browser, ledger, clock, manifest_doc(), cv)
    assert done.status == "EMPLOYER_CONFIRMED" and posts(seen2) == [UPLOAD, APPLICATION]


def test_unknown_required_screening_question_blocks_before_click(chromium, env):
    cv, ledger = env
    extra = '<label for="x">Unexpected question</label><input id="x" required>'
    with session(chromium, html=page_html(extra=extra)) as (browser, _, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(), cv)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_FORM_DRIFT"
    assert APPLICATION not in posts(seen) and status(ledger) == "Released"


def test_unacknowledged_application_stays_unconfirmed(chromium, env):
    cv, ledger = env
    with session(chromium, ack=False) as (browser, _, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(), cv)
    assert result.status == "SUBMITTED_UNCONFIRMED" and posts(seen).count(APPLICATION) == 1
    assert status(ledger) == "Submitted / Unconfirmed"


def test_challenge_waits_for_owner_and_never_clicks(chromium, env):
    cv, ledger = env
    with session(chromium, html=page_html(captcha=True)) as (browser, _, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(), cv, wait=0.2)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_OWNER_CHALLENGE_UNRESOLVED"
    assert APPLICATION not in posts(seen) and status(ledger) == "Awaiting User"


def test_wrong_upload_acknowledgement_blocks(chromium, env):
    cv, ledger = env
    with session(chromium, upload_name="Other.pdf") as (browser, _, seen, clock):
        result = run(browser, ledger, clock, manifest_doc(), cv)
    assert result.status == "BLOCKED" and APPLICATION not in posts(seen)


def test_boundary_rules():
    boundary = PolicyNetworkBoundary(COMPANY, POSTING, "Synthetic_CV.pdf")
    assert boundary.allows("GET", URL, "document", True)
    assert not boundary.allows("GET", ENTRY, "document", True)  # publication not yet learned
    boundary.set_publication(PUBLICATION)
    assert boundary.allows("GET", ENTRY, "document", True)
    assert not boundary.allows("GET", "https://evil.example/x", "script", False)
    assert not boundary.allows("POST", UPLOAD, "fetch", False)  # not in the upload phase
    boundary.begin_upload()
    assert not boundary.allows("POST", UPLOAD.replace("Synthetic_CV", "Other"), "fetch", False)
    assert boundary.allows("POST", UPLOAD, "fetch", False)
    assert not boundary.allows("POST", UPLOAD, "fetch", False)  # once
    boundary.end_upload()
    assert not boundary.allows("POST", APPLICATION, "fetch", False)  # premature attempt closes it
    with pytest.raises(PermissionError):
        boundary.begin_submit()
    with pytest.raises(PermissionError):
        boundary.set_publication(PUBLICATION)
