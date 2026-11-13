"""Actual Chromium/DOM integration, with every HTTPS request fulfilled locally.

Synthetic employer, candidate and resume only. The real OneClick network boundary
runs before the fixture fulfiller, so denied requests never reach even a fixture
endpoint. No cookies, real employer traffic, OAuth grants or mailbox are used.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from ejs.contracts.submit import SubmitAuthority
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.oneclick_browser import OneClickBrowser
from ejs.services.oneclick_target import OneClickNetworkBoundary, OneClickTarget, native_entry_policy_enabled
from ejs.services.single_application_runtime import SingleApplicationRuntime, digest


ON = SubmitAuthority(browser_external_form_write=True, final_submit_runtime_flag=True,
                     kill_switch_engaged=False)


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
        if not Path(executable).is_file():
            pytest.skip("Managed Playwright Chromium is not installed")
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        yield browser
        browser.close()


@dataclass
class Fixture:
    target: OneClickTarget
    manifest: dict
    facts: dict
    cv: Path
    cv_payload: bytes
    posting: bytes
    ledger: SqliteSubmissionLedger


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    private = tmp_path.resolve() / "private"
    private.mkdir(mode=0o700)
    now = datetime.now(timezone.utc).isoformat()
    target = OneClickTarget("Fixture", "a" * 24, "123456789012345",
        "01234567-89ab-4cde-8f01-234567890abc", "REF1234X",
        "https://jobs.smartrecruiters.com/Fixture/123456789012345--synthetic-analyst")
    cv_payload = b"%PDF-1.4\n% SYNTHETIC OFFLINE RESUME ONLY\n%%EOF\n"
    cv = private / "CV.pdf"
    cv.write_bytes(cv_payload)
    cv.chmod(0o600)
    facts = dict(schema_version="EJS-VERIFIED-FACTS-1", candidate_profile_version="fixture-profile-v1", facts=[])

    def field(key, canonical, value, kind, *, policy=None, mode="Verified Fact", selector=None, label=None):
        source = "user_decision:" + key
        facts["facts"].append(dict(canonical_field=canonical, value=value, source_ref=source,
            verified=True, auto_fill_approved=True, verified_at=now))
        item = dict(key=key, canonical_field=canonical, value=value, kind=kind, required=True,
            selector=selector or "#" + key, label=label or key.title(), provenance_ref=source,
            resolution_mode=mode)
        if policy:
            item["policy_ref"] = policy
        return item

    first = [field("first", "candidate.first_name", "Synthetic", "text", label="First name"),
             field("email", "candidate.email", "synthetic@example.invalid", "email", label="Email")]
    second = [
        field("salary", "compensation.expected_annual_gross", "37000", "number",
              mode="Approved Policy", policy="USER-IE-SALARY-20261106", label="Expected salary"),
        field("notice", "employment.notice_period", "Immediate", "select", label="Notice"),
        field("eligibility", "work.eligibility", "I require sponsorship", "select", label="Eligibility"),
        field("residency", "work.currently_lives_in_ireland", "No", "radio", label="Living in Ireland No"),
        field("privacy", "consent.privacy_acknowledgement", True, "checkbox",
              mode="Approved Policy", policy="USER-FIXTURE-PRIVACY-20261106", label="Privacy"),
    ]
    for canonical, value in (("work.authorization", False), ("work.requires_sponsorship", True)):
        facts["facts"].append(dict(canonical_field=canonical, value=value,
            source_ref="candidate_verified:work-rights", verified=True, auto_fill_approved=True, verified_at=now))
    posting = json.dumps(dict(active=True, id=target.posting_id, uuid=target.publication_id,
        refNumber=target.requisition_id, company=dict(identifier=target.company))).encode()
    manifest = dict(schema_version="EJS-SINGLE-APPLICATION-1", source_sha="a" * 40,
        execution_id="fixture:execution:browser", opportunity_key="fixture:opportunity:browser",
        requisition_id=target.requisition_id, candidate_profile_version=facts["candidate_profile_version"],
        reviewer_ref="independent:fixture", reviewed_at=now, read_urls=[],
        steps=[dict(url=target.entry_url, fingerprint="1" * 64, fields=first,
            upload=dict(key="cv", selector="#resume", label="Resume", kind="file", required=True),
            next=dict(key="next", selector="#next", label="Next")),
            dict(url=target.screening_url, fingerprint="2" * 64, fields=second,
            submit=dict(key="submit", selector="#submit", label="Submit"))],
        cv=dict(sha256=hashlib.sha256(cv_payload).hexdigest(), size_bytes=len(cv_payload),
            mime_type="application/pdf", approved_filename="CV.pdf", user_approved=True,
            scope="one_cv_asset", source_ref="approved_document:fixture-resume"),
        vacancy=dict(reviewed_at=now, posting_sha256=hashlib.sha256(posting).hexdigest(),
            role_fit_verified=True, mandatory_requirements_verified=True, sponsorship_supported=None,
            explicit_no_sponsorship=False, existing_work_rights_required=False,
            allow_unknown_sponsorship=True, sponsorship_decision_ref="USER-FIRST-SCOPE-20261105"),
        confirmation=dict(selector="#confirmation", text="Application received"))
    ledger = SqliteSubmissionLedger(private / "first.sqlite3")
    monkeypatch.setattr("ejs.services.single_application_runtime.fetch_posting",
        lambda *_: pytest.fail("Public network must not be used by the offline browser suite"))
    return Fixture(target, manifest, facts, cv, cv_payload, posting, ledger)


def html_for(fixture, *, autosave=False, retry=False, copied_success=False,
             success_ack=True, awli=False, shadow_radio=False, unknown_required_role=False,
             custom_combobox=False, roleless_options=False, ambiguous_option=False,
             unavailable_option=False, aria_value_combobox=False, radio_group=False,
             ambiguous_radio=False, unavailable_radio=False, unknown_required_group=False,
             native_widget=False, unexpected_profile=False):
    target = fixture.target
    screening = """
      <label for="salary">Expected salary</label><input id="salary" type="number" required>
      <label for="notice">Notice</label><select id="notice" required>
        <option value="">Choose</option><option>Immediate</option></select>
      <label for="eligibility">Eligibility</label><select id="eligibility" required>
        <option value="">Choose</option><option>I require sponsorship</option></select>
      RADIO_PLACEHOLDER
      <label for="privacy">Privacy</label><input id="privacy" type="checkbox" required>
      <button id="submit" type="button">Submit</button>
    """
    if radio_group and shadow_radio:
        screening = screening.replace("RADIO_PLACEHOLDER", '<div id="residency" role="radiogroup" aria-label="Living in Ireland" aria-required="true"></div>')
        radio_setup = """
          const shadow=document.querySelector('#residency').attachShadow({mode:'open'});
          shadow.innerHTML='<div role="radio" aria-label="Yes" aria-checked="false" tabindex="0">Yes</div><div role="radio" aria-label="No" aria-checked="false" tabindex="0">No</div>';
          for(const option of shadow.children)option.addEventListener('click',()=>{
            for(const other of shadow.children)other.setAttribute('aria-checked',String(other===option));
          });
        """
        radio_read = "document.querySelector('#residency').shadowRoot.querySelector('[aria-label=No]').getAttribute('aria-checked')==='true'"
    elif radio_group:
        radios = '<input type="radio" name="residency" value="Yes" aria-label="Yes" required>'
        if not unavailable_radio:
            radios += '<input type="radio" name="residency" value="No" aria-label="No">'
        if ambiguous_radio:
            radios += '<input type="radio" name="residency" value="No-duplicate" aria-label="No">'
        screening = screening.replace("RADIO_PLACEHOLDER", '<div id="residency" role="radiogroup" aria-label="Living in Ireland" aria-required="true">' + radios + '</div>')
        radio_setup, radio_read = "", "document.querySelector('#residency input[value=No]')?.checked===true"
    elif shadow_radio:
        screening = screening.replace("RADIO_PLACEHOLDER", '<div id="residency-host"></div>')
        radio_setup = """
          const shadow = document.querySelector('#residency-host').attachShadow({mode:'open'});
          shadow.innerHTML = '<div id="residency" role="radio" aria-label="Living in Ireland No" aria-required="true" aria-checked="false" tabindex="0">No</div>';
          shadow.querySelector('#residency').addEventListener('click', event => event.currentTarget.setAttribute('aria-checked','true'));
        """
        radio_read = "document.querySelector('#residency-host').shadowRoot.querySelector('#residency').getAttribute('aria-checked')==='true'"
    else:
        screening = screening.replace("RADIO_PLACEHOLDER", '<input id="residency" type="radio" name="residency" aria-label="Living in Ireland No" required>')
        radio_setup, radio_read = "", "document.querySelector('#residency').checked"
    combo_setup = ""
    if custom_combobox:
        for key, label, value in (("notice", "Notice", "Immediate"), ("eligibility", "Eligibility", "I require sponsorship")):
            # Real reviewed ATS widgets may expose an INPUT/button combobox,
            # without any native SELECT element. Options are visible on click.
            control = (f'<button id="{key}" type="button" role="combobox" aria-label="{label}" aria-required="true" aria-valuetext="" aria-controls="{key}-list">Choose</button>'
                       if aria_value_combobox else
                       f'<input id="{key}" type="text" role="combobox" aria-label="{label}" aria-required="true" aria-controls="{key}-list" readonly>')
            values = (["Later"] if key == "notice" and unavailable_option else [value, "Later"])
            if key == "notice" and ambiguous_option:
                values.append(value)
            option_role = "" if roleless_options else ' role="option"'
            items = ''.join(f'<span{option_role} data-value="{option}">{option}</span>' for option in values)
            widget = control + f'<div id="{key}-list" role="listbox" hidden>{items}</div>'
            start = screening.index(f'<label for="{key}">')
            end = screening.index('</select>', start) + len('</select>')
            screening = screening[:start] + widget + screening[end:]
            combo_setup += f"""
              document.querySelector('#{key}').addEventListener('click',()=>document.querySelector('#{key}-list').hidden=false);
              for(const option of document.querySelector('#{key}-list').children)option.addEventListener('click',()=>{{
                const control=document.querySelector('#{key}');
                {'control.setAttribute("aria-valuetext",option.dataset.value);control.textContent=option.dataset.value;' if aria_value_combobox else 'control.value=option.dataset.value;'}
                document.querySelector('#{key}-list').hidden=true;
              }});
            """
        if unavailable_option:
            screening += '<p>Immediate</p>'  # Arbitrary matching page text must never be selected.
    notice_read = "document.querySelector('#notice').getAttribute('aria-valuetext')" if aria_value_combobox else "document.querySelector('#notice').value"
    eligibility_read = "document.querySelector('#eligibility').getAttribute('aria-valuetext')" if aria_value_combobox else "document.querySelector('#eligibility').value"
    if not custom_combobox:
        notice_read = "document.querySelector('#notice').selectedOptions[0].textContent"
        eligibility_read = "document.querySelector('#eligibility').selectedOptions[0].textContent"
    if unknown_required_role:
        screening += '<div role="checkbox" aria-label="Unknown mandatory disclosure" aria-required="true" aria-checked="false" tabindex="0">Unknown mandatory disclosure</div>'
    if unknown_required_group:
        screening += '<div role="radiogroup" aria-label="Unknown required group"><input type="radio" aria-label="Yes" name="unreviewed-group" required></div>'
    if copied_success:
        dispatch = "showSuccess();"
    else:
        dispatch = f"""
          const response = await fetch({json.dumps(target.application_url)}, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(answers)}});
          const data = await response.json();
          {'await fetch(' + json.dumps(target.application_url) + ",{method:'POST',body:JSON.stringify(answers)}).catch(()=>{});" if retry else ''}
          if (response.ok) showSuccess();
        """
    profile_effect = ('document.querySelector("#form").insertAdjacentHTML("beforeend",'
        "'<section>Unreviewed parsed experience<button aria-label=\"Edit experience Unsupported parsed role\"></button></section>');"
        if unexpected_profile else '')
    return f"""<!doctype html><html><head><link rel="icon" href="data:,"></head><body>
      <main id="form"><label for="first">First name</label><input id="first" required>
      <label for="email">Email</label><input id="email" type="email" required>
      <label for="resume">Resume</label><input id="resume" type="file" required><span id="uploaded"></span>
      {'<button type="button" onclick="window.awliClicked=true">Apply with LinkedIn</button>' if awli else ''}
      {'<iframe src="' + WIDGET_URL + '"></iframe>' if native_widget else ''}
      <button id="next" type="button">Next</button></main>
      <script>
        const initial = document.querySelector('#form').innerHTML;
        const showSuccess = () => {{history.pushState({{}},'',{json.dumps(target.screening_url.replace('/screening?', '/success?'))});document.querySelector('#form').innerHTML='<h1 id="confirmation">Application received</h1>';}};
        function showScreening() {{
          history.pushState({{}},'',{json.dumps(target.screening_url)});
          document.querySelector('#form').innerHTML={json.dumps(screening)};
          {radio_setup}
          {combo_setup}
          document.querySelector('#submit').addEventListener('click', async () => {{
            const answers = {{salary:document.querySelector('#salary').value,notice:{notice_read},
              eligibility:{eligibility_read},residency:{radio_read},privacy:document.querySelector('#privacy').checked}};
            {dispatch}
          }});
        }}
        document.querySelector('#next').addEventListener('click', showScreening);
        document.querySelector('#resume').addEventListener('change', async event => {{
          const body = new FormData();body.append('file',event.target.files[0]);
          const response = await fetch({json.dumps(target.resume_upload_url('CV.pdf'))},{{method:'POST',body}});
          const data = await response.json();document.querySelector('#uploaded').textContent=data.fileName;
          {profile_effect}
        }});
        {'document.querySelector("#email").addEventListener("input",()=>fetch("/autosave",{method:"POST",body:"SYNTHETIC"}).catch(()=>{}));' if autosave else ''}
      </script></body></html>"""


@contextmanager
def actual_driver(chromium, fixture, monkeypatch, **options):
    context = chromium.new_context(service_workers="block")
    seen, observed = [], []
    target = fixture.target
    html = html_for(fixture, **options)

    def fulfill(route):
        request = route.request
        seen.append((request.method, request.url, request.post_data_buffer))
        if request.method == "GET" and request.url == target.entry_url:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif request.method == "POST" and request.url == target.resume_upload_url("CV.pdf"):
            route.fulfill(status=201, content_type="application/json",
                body=json.dumps(dict(fileId="synthetic-attachment-id", fileName="CV.pdf")))
        elif request.method == "POST" and request.url == target.application_url:
            assert fixture.ledger.snapshot()["status"] == "Submitted / Unconfirmed"
            route.fulfill(status=201, content_type="application/json",
                body=json.dumps(dict(success=options.get("success_ack", True))))
        elif options.get('native_widget') and request.method == 'GET' and request.url == WIDGET_URL.split('#')[0]:
            route.fulfill(status=200, content_type='text/html', body='<button>Apply with LinkedIn</button>')
        else:
            pytest.fail("An unreviewed request escaped the real network boundary")

    context.route("**/*", fulfill)  # The later production route runs first.
    context.on("request", lambda request: observed.append((request.method, request.url)))
    boundary = OneClickNetworkBoundary(target, read_urls=tuple(fixture.manifest.get('read_urls', [])), resume_filename="CV.pdf",
                                      reviewed_native_entry=native_entry_policy_enabled(fixture.manifest))
    browser = OneClickBrowser(context, boundary, fixture.manifest)
    original_expect_response = browser.page.expect_response
    monkeypatch.setattr(browser.page, "expect_response",
        lambda predicate, **kwargs: original_expect_response(predicate, timeout=1000))
    try:
        yield browser, boundary, seen, observed
    finally:
        context.close()


def review_shapes(chromium, fixture, monkeypatch, **options):
    # A separate read-only review context observes the same synthetic DOM; the
    # actual runtime starts in a fresh owned context with no imported session.
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, _, seen, _):
        browser.open(fixture.target.entry_url)
        fixture.manifest["steps"][0]["fingerprint"] = browser.inspect()["fingerprint"]
        browser.advance(fixture.manifest["steps"][0]["next"], fixture.target.screening_url)
        fixture.manifest["steps"][1]["fingerprint"] = browser.inspect()["fingerprint"]
        assert all(method == 'GET' for method, _, _ in seen)


def run(fixture, browser):
    return SingleApplicationRuntime(browser, fixture.ledger).run(fixture.manifest, fixture.facts,
        fixture.target, fixture.cv, authority=ON, posting_payload=fixture.posting)


def test_actual_two_step_browser_upload_answers_one_submit_and_confirmation(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch)
    with actual_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "EMPLOYER_CONFIRMED", (result, browser.page.url, browser.inspect())
        assert result["applied"] is True and result["submit_click_attempts"] == 1
        assert fixture.ledger.snapshot()["status"] == "Confirmed"
        assert boundary.application_attempts == boundary.application_requests_allowed == boundary.upload_requests_allowed == 1
        assert [method for method, _, _ in seen] == ["GET", "POST", "POST"]
        upload = next(body for method, url, body in seen if url == fixture.target.resume_upload_url("CV.pdf"))
        assert fixture.cv_payload in upload and b'name="file"; filename="CV.pdf"' in upload
        submitted = json.loads(next(body for _, url, body in seen if url == fixture.target.application_url))
        assert submitted == dict(salary="37000", notice="Immediate", eligibility="I require sponsorship", residency=True, privacy=True)
        assert browser.page.locator("#confirmation").inner_text() == "Application received"


def test_actual_shadow_radio_is_observed_filled_and_confirmed(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, shadow_radio=True)
    with actual_driver(chromium, fixture, monkeypatch, shadow_radio=True) as (browser, _, _, _):
        result = run(fixture, browser)
        assert result["status"] == "EMPLOYER_CONFIRMED", (result, browser.page.url)
        assert fixture.ledger.snapshot()["status"] == "Confirmed"


def test_unknown_autosave_is_blocked_before_upload_or_submission(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, autosave=True)
    with actual_driver(chromium, fixture, monkeypatch, autosave=True) as (browser, boundary, seen, observed):
        result = run(fixture, browser)
        assert result["status"] == "BLOCKED"
        assert boundary.blocked_write_count >= 1
        assert any(method == "POST" and url.endswith("/autosave") for method, url in observed)
        assert [method for method, _, _ in seen] == ["GET"]
        assert not fixture.ledger.has_intent()


def test_vendor_automatic_second_post_is_denied_and_cannot_confirm(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, retry=True)
    with actual_driver(chromium, fixture, monkeypatch, retry=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "SUBMITTED_UNCONFIRMED" and result["applied"] is False
        assert boundary.application_attempts == 2 and boundary.application_requests_allowed == 1
        assert sum(url == fixture.target.application_url for _, url, _ in seen) == 1
        assert fixture.ledger.snapshot()["status"] == "Submitted / Unconfirmed"
        before = list(seen)
        assert run(fixture, browser)["status"] == "DUPLICATE_OR_UNCERTAIN"
        assert seen == before


def test_copied_success_dom_without_application_response_never_confirms(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, copied_success=True)
    with actual_driver(chromium, fixture, monkeypatch, copied_success=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "SUBMITTED_UNCONFIRMED" and result["applied"] is False
        assert browser.page.locator("#confirmation").inner_text() == "Application received"
        assert boundary.application_attempts == boundary.application_requests_allowed == 0
        assert sum(url == fixture.target.application_url for _, url, _ in seen) == 0
        assert fixture.ledger.snapshot()["status"] == "Submitted / Unconfirmed"


def test_false_acknowledgement_even_with_success_dom_never_confirms(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, success_ack=False)
    with actual_driver(chromium, fixture, monkeypatch, success_ack=False) as (browser, _, _, _):
        result = run(fixture, browser)
        assert result["status"] == "SUBMITTED_UNCONFIRMED" and result["applied"] is False
        assert fixture.ledger.snapshot()["status"] == "Submitted / Unconfirmed"


def test_visible_awli_hands_off_before_any_fill_upload_or_intent(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, awli=True)
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "HUMAN_HANDOFF_REQUIRED"
        assert browser.page.locator("#first").input_value() == browser.page.locator("#email").input_value() == ""
        assert [method for method, _, _ in seen] == ["GET"]
        assert boundary.upload_requests_allowed == boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()


def test_unknown_required_custom_checkbox_blocks_before_dispatch(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, unknown_required_role=True)
    with actual_driver(chromium, fixture, monkeypatch, unknown_required_role=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "BLOCKED", result
        assert boundary.application_requests_allowed == 0
        assert sum(url == fixture.target.application_url for _, url, _ in seen) == 0
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize("roleless_options,aria_value_combobox", [(False, False), (True, False), (False, True)])
def test_custom_combobox_values_are_selected_and_verified_in_actual_dom(chromium, fixture, monkeypatch,
        roleless_options, aria_value_combobox):
    options = dict(custom_combobox=True, roleless_options=roleless_options, aria_value_combobox=aria_value_combobox)
    review_shapes(chromium, fixture, monkeypatch, **options)
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "EMPLOYER_CONFIRMED", (result, browser.page.url)
        submitted = json.loads(next(body for _, url, body in seen if url == fixture.target.application_url))
        assert submitted["notice"] == "Immediate" and submitted["eligibility"] == "I require sponsorship"
        assert boundary.application_requests_allowed == 1


def test_combobox_readback_is_actual_selected_value_not_reviewed_expected_value(chromium, fixture, monkeypatch):
    with actual_driver(chromium, fixture, monkeypatch, custom_combobox=True) as (browser, _, _, _):
        browser.open(fixture.target.entry_url)
        browser.advance(fixture.manifest["steps"][0]["next"], fixture.target.screening_url)
        binding = next(field for field in fixture.manifest["steps"][1]["fields"] if field["key"] == "notice")
        assert browser.readback(binding) == ""
        browser.page.locator("#notice").click()
        browser.page.get_by_role("option", name="Later", exact=True).click()
        assert browser.readback(binding) == "Later"
        browser.write(binding)
        assert browser.readback(binding) == "Immediate"


@pytest.mark.parametrize("roleless_options,ambiguous_option,unavailable_option", [
    (False, True, False), (True, True, False), (False, False, True), (True, False, True)])
def test_ambiguous_or_unavailable_combobox_option_stops_before_submit(chromium, fixture, monkeypatch,
        roleless_options, ambiguous_option, unavailable_option):
    options = dict(custom_combobox=True, roleless_options=roleless_options,
                   ambiguous_option=ambiguous_option, unavailable_option=unavailable_option)
    review_shapes(chromium, fixture, monkeypatch, **options)
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result["status"] == "BLOCKED", result
        assert boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()


def radio_group_binding(fixture):
    binding = next(field for field in fixture.manifest["steps"][1]["fields"] if field["key"] == "residency")
    binding["label"] = "Living in Ireland"
    return binding


def test_reviewed_required_yes_no_group_is_one_control_and_actual_answer(chromium, fixture, monkeypatch):
    radio_group_binding(fixture)
    review_shapes(chromium, fixture, monkeypatch, radio_group=True)
    with actual_driver(chromium, fixture, monkeypatch, radio_group=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "EMPLOYER_CONFIRMED", (result, browser.page.url)
        submitted = json.loads(next(body for _, url, body in seen if url == fixture.target.application_url))
        assert submitted["residency"] is True
        assert boundary.application_requests_allowed == 1


def test_radio_group_readback_uses_real_checked_label(chromium, fixture, monkeypatch):
    binding = radio_group_binding(fixture)
    with actual_driver(chromium, fixture, monkeypatch, radio_group=True) as (browser, _, _, _):
        browser.open(fixture.target.entry_url)
        browser.advance(fixture.manifest["steps"][0]["next"], fixture.target.screening_url)
        assert browser.readback(binding) == ""
        browser.page.locator("#residency").get_by_role("radio", name="Yes", exact=True).check()
        assert browser.readback(binding) == "Yes"
        browser.write(binding)
        assert browser.readback(binding) == "No"
        snapshot = browser.inspect()
        assert snapshot["required_keys"] == {field["key"] for field in fixture.manifest["steps"][1]["fields"]}


@pytest.mark.parametrize("ambiguous_radio,unavailable_radio", [(True, False), (False, True)])
def test_ambiguous_or_unavailable_radio_group_option_stops_before_submit(chromium, fixture, monkeypatch,
        ambiguous_radio, unavailable_radio):
    radio_group_binding(fixture)
    options = dict(radio_group=True, ambiguous_radio=ambiguous_radio, unavailable_radio=unavailable_radio)
    review_shapes(chromium, fixture, monkeypatch, **options)
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result["status"] == "BLOCKED", result
        assert boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()


def test_open_shadow_radio_group_is_observed_as_one_control(chromium, fixture, monkeypatch):
    radio_group_binding(fixture)
    options = dict(radio_group=True, shadow_radio=True, custom_combobox=True)
    review_shapes(chromium, fixture, monkeypatch, **options)
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result["status"] == "EMPLOYER_CONFIRMED", (result, browser.page.url)
        submitted = json.loads(next(body for _, url, body in seen if url == fixture.target.application_url))
        assert submitted["residency"] is True and boundary.application_requests_allowed == 1


def test_unreviewed_group_with_required_native_child_remains_a_blocker(chromium, fixture, monkeypatch):
    review_shapes(chromium, fixture, monkeypatch, unknown_required_group=True)
    with actual_driver(chromium, fixture, monkeypatch, unknown_required_group=True) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result["status"] == "BLOCKED", result
        assert boundary.application_requests_allowed == 0 and not fixture.ledger.has_intent()


@contextmanager
def spl_driver(chromium, fixture, *, bad_wrapper=None, ambiguous_option=False,
               unavailable_option=False, association_missing=False):
    """Reviewed SPL shapes: duplicate IDs in nested open shadow hosts only."""
    fields = [
        dict(key="salary", selector="input#salary", label="Expected salary", kind="number", required=True, value="37000"),
        dict(key="notice", selector="input#notice", label="Notice", kind="select", required=True, value="Immediate"),
        dict(key="postal", selector="input#postal", label="Postal code", kind="text", required=True, value="00000"),
        dict(key="privacy", selector="input#privacy", label="Privacy", kind="checkbox", required=True, value=True),
        dict(key="residency", selector='fieldset[role="radiogroup"]', label="Living in Ireland", kind="radio", required=True, value="No"),
    ]
    config = json.dumps(dict(badWrapper=bad_wrapper, ambiguousOption=ambiguous_option,
        unavailableOption=unavailable_option, associationMissing=association_missing))
    html = """<!doctype html><html><head><link rel="icon" href="data:,"></head><body>
      <main id="form"></main>
      <div id="menu-question_fixture_notice" role="listbox" hidden></div>
      <div role="listbox"><div role="option">Immediate</div></div>
      <script>
      const config=CONFIG;
      const mount = (tag,id,parent=document.querySelector('#form')) => {
        const host=document.createElement(tag);host.id=id;host.setAttribute('required','');
        host.style.display='block';parent.append(host);return host.attachShadow({mode:'open'});
      };
      const field = (id,label,type,tags) => {
        let parent=document.querySelector('#form');
        for(const tag of tags)parent=mount(tag,id,parent);
        const input=document.createElement('input');input.id=id;input.type=type;
        input.setAttribute('aria-label',label);input.setAttribute('aria-required','true');parent.append(input);
        return input;
      };
      const salary=field('salary','Expected salary','number',['spl-number-field','spl-input']);
      const notice=field('notice','Notice','text',['spl-autocomplete','spl-input']);
      notice.readOnly=true;notice.setAttribute('role','combobox');
      notice.setAttribute('aria-controls',config.associationMissing?'unknown-menu':'menu-question_fixture_notice');
      const postal=field('postal','Postal code','text',['spl-input']);
      const privacy=field('privacy','Privacy','checkbox',['spl-checkbox']);
      const groupRoot=mount('spl-radio-group','residency');
      const group=document.createElement('fieldset');group.setAttribute('role','radiogroup');
      group.setAttribute('aria-label','Living in Ireland');group.setAttribute('aria-required','true');groupRoot.append(group);
      const radios=[];
      for(const choice of ['Yes','No']){
        const host=document.createElement('spl-radio');host.style.display='inline-block';group.append(host);
        const root=host.attachShadow({mode:'open'});const radio=document.createElement('input');
        radio.type='radio';radio.setAttribute('aria-label',choice);root.append(radio);radios.push(radio);
        radio.addEventListener('change',()=>{for(const other of radios)if(other!==radio)other.checked=false;});
      }
      const menu=document.querySelector('#menu-question_fixture_notice');
      const choices=config.unavailableOption?['Later']:config.ambiguousOption?['Immediate','Immediate']:['Immediate','Later'];
      for(const choice of choices){
        const row=document.createElement('spl-select-option');row.style.display='block';row.style.padding='4px';
        row.innerHTML='<div><div><spl-typography-body style="display:contents"><spl-truncate style="display:contents">'+choice+'</spl-truncate></spl-typography-body></div></div>';
        row.addEventListener('click',()=>{notice.value=choice;menu.hidden=true;});menu.append(row);
      }
      for(let i=0;i<2;i++){const tooltip=document.createElement('spl-typography-body');tooltip.hidden=true;tooltip.textContent='Immediate';menu.append(tooltip);}
      notice.addEventListener('click',()=>{menu.hidden=false;});
      const outer=document.querySelector('spl-number-field');
      if(config.badWrapper==='unknown'||config.badWrapper==='unknown-contents'){
        const unknown=document.createElement('unreviewed-required-field');unknown.id='salary';
        unknown.setAttribute('required','');unknown.style.display=config.badWrapper==='unknown-contents'?'contents':'block';outer.replaceWith(unknown);
        unknown.attachShadow({mode:'open'}).append(outer);
      }
      if(config.badWrapper==='mismatched-id')outer.id='different-salary';
      if(config.badWrapper==='multiple-owned'){
        const extra=document.createElement('input');extra.setAttribute('aria-label','Extra optional field');outer.shadowRoot.append(extra);
      }
      if(config.badWrapper==='not-propagated')salary.removeAttribute('aria-required');
      </script></body></html>""".replace("CONFIG", config)
    context = chromium.new_context(service_workers="block")
    seen = []
    def fulfill(route):
        assert route.request.method == "GET" and route.request.url == fixture.target.entry_url
        seen.append((route.request.method, route.request.url))
        route.fulfill(status=200, content_type="text/html", body=html)
    context.route("**/*", fulfill)
    manifest = dict(steps=[dict(url=fixture.target.entry_url, fields=fields, fingerprint="a" * 64)])
    boundary = OneClickNetworkBoundary(fixture.target, read_urls=(), resume_filename="CV.pdf")
    browser = OneClickBrowser(context, boundary, manifest)
    try:
        browser.open(fixture.target.entry_url)
        yield browser, boundary, manifest, seen
    finally:
        context.close()


def test_reviewed_spl_open_shadow_wrappers_are_exactly_one_required_control_each(chromium, fixture):
    with spl_driver(chromium, fixture) as (browser, boundary, manifest, seen):
        expected = {field["key"] for field in manifest["steps"][0]["fields"]}
        snapshot = browser.inspect()
        assert snapshot["required_keys"] == expected
        assert len(browser._controls()) == len(expected)
        assert all(control[1]["required"] for control in browser._controls())
        # The actual SPL radio FIELDSET has no ID; the host owns its identity.
        assert browser.page.locator('fieldset[role="radiogroup"]').get_attribute("id") is None
        for binding in manifest["steps"][0]["fields"]:
            browser.write(binding)
            assert browser.readback(binding) == binding["value"]
        assert browser.inspect()["required_keys"] == expected
        assert browser.inspect()["unreviewed_prefill"] is False
        assert seen == [("GET", fixture.target.entry_url)]
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0


@pytest.mark.parametrize("bad_wrapper", ["unknown", "unknown-contents", "mismatched-id", "multiple-owned", "not-propagated"])
def test_spl_dedup_never_hides_unknown_ambiguous_or_unpropagated_required_wrapper(chromium, fixture, bad_wrapper):
    with spl_driver(chromium, fixture, bad_wrapper=bad_wrapper) as (browser, boundary, manifest, _):
        with pytest.raises(PermissionError):
            snapshot = browser.inspect()
            manifest["steps"][0]["fingerprint"] = snapshot["fingerprint"]
            SingleApplicationRuntime(browser, fixture.ledger)._surface(manifest["steps"][0])
        assert boundary.application_requests_allowed == 0 and not fixture.ledger.has_intent()


def test_spl_option_uses_visible_host_in_exact_associated_menu_not_hidden_tooltips(chromium, fixture):
    with spl_driver(chromium, fixture) as (browser, boundary, manifest, _):
        binding = next(field for field in manifest["steps"][0]["fields"] if field["key"] == "notice")
        browser.write(binding)
        assert browser.readback(binding) == "Immediate"
        # An unrelated, visible role=option also says Immediate. It is never
        # clicked or treated as an ambiguous match for this combobox's menu.
        assert browser.page.locator('#menu-question_fixture_notice').is_hidden()
        assert browser.page.get_by_role("option", name="Immediate", exact=True).is_visible()
        assert boundary.application_requests_allowed == 0


@pytest.mark.parametrize("options", [dict(ambiguous_option=True), dict(unavailable_option=True), dict(association_missing=True)])
def test_spl_option_requires_unique_visible_host_in_its_real_associated_menu(chromium, fixture, options):
    with spl_driver(chromium, fixture, **options) as (browser, boundary, manifest, _):
        binding = next(field for field in manifest["steps"][0]["fields"] if field["key"] == "notice")
        with pytest.raises(PermissionError):
            browser.write(binding)
        assert browser.readback(binding) == ""
        assert boundary.application_requests_allowed == 0 and not fixture.ledger.has_intent()


WIDGET_URL = ('https://www.linkedin.com/talentwidgets/apply-with-linkedin'
    '#xdOrigin=https%3A%2F%2Fjobs.smartrecruiters.com&xdChannel=01234567-89ab-4cde-8f01-234567890abc')


def reviewed_return(browser, fixture):
    observation = browser.observe_entry_return()
    assert set(observation) == {'observation_id', 'surface_fingerprint', 'observed_at'}
    assert len(observation['observation_id']) == 48
    reviewed = deepcopy(fixture.manifest)
    reviewed['steps'][0]['fingerprint'] = observation['surface_fingerprint']
    reviewed['entry_return_review'] = dict(observation_id=observation['observation_id'],
        surface_fingerprint=observation['surface_fingerprint'], reviewed_at=datetime.now(timezone.utc).isoformat(),
        reviewer_ref=reviewed['reviewer_ref'], return_url=fixture.target.entry_url)
    return observation, reviewed


def pending_return(browser, fixture):
    browser.open(fixture.target.entry_url)
    with pytest.raises(PermissionError, match='^APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED$'):
        browser.preferred_entry()
    return reviewed_return(browser, fixture)


def test_entry_return_actual_resume_preserves_page_then_uploads_advances_and_submits_once(chromium, fixture, monkeypatch):
    # External pin/fact-loader validation has its own suite. This fixture tests
    # the trusted caller seam and actual retained browser+ledger integration.
    review_shapes(chromium, fixture, monkeypatch, awli=True)
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, _, seen, _):
        assert run(fixture, browser)['status'] == 'HUMAN_HANDOFF_REQUIRED'
        page = browser.page
        observation, reviewed = reviewed_return(browser, fixture)
        monkeypatch.setattr(browser, 'open', lambda *_: pytest.fail('Resume must not navigate/open again'))
        result = SingleApplicationRuntime(browser, fixture.ledger).resume_reviewed_return(
            reviewed, fixture.facts, fixture.target, fixture.cv, authority=ON,
            observation_id=observation['observation_id'], posting_payload=fixture.posting)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert browser.page is page and [method for method, _, _ in seen] == ['GET', 'POST', 'POST']
        assert fixture.ledger.snapshot()['status'] == 'Confirmed'
        with pytest.raises(PermissionError):
            browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)


@pytest.mark.parametrize('mutation', ['field', 'body', 'dom', 'reload', 'expired', 'old-review', 'wrong-token'])
def test_entry_return_rejects_changed_stale_or_unreviewed_owned_surface(chromium, fixture, monkeypatch, mutation):
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, _, _, _):
        observation, reviewed = pending_return(browser, fixture)
        if mutation == 'field': browser.page.locator('#first').fill('Changed synthetic value')
        if mutation == 'body': browser.page.locator('body').evaluate("el=>el.append('Changed review body')")
        if mutation == 'dom': browser.page.locator('#first').evaluate("el=>el.style.marginTop='1px'")
        if mutation == 'reload': browser.page.reload()
        if mutation == 'expired':
            import ejs.services.oneclick_browser as module
            stamp = browser._entry_observation['observed_at']
            monkeypatch.setattr(module, 'monotonic', lambda: stamp + 121)
        if mutation == 'old-review': reviewed.pop('entry_return_review')
        token = 'f' * 48 if mutation == 'wrong-token' else observation['observation_id']
        with pytest.raises(PermissionError, match='^APPLICATION_ENTRY_RETURN_REJECTED$'):
            browser.accept_reviewed_entry_return(token, reviewed)
        with pytest.raises(PermissionError):  # Rejected capabilities are consumed too.
            browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize('foreign_page', [False, True])
def test_entry_return_rejects_foreign_page_and_new_popup_even_when_closed(chromium, fixture, monkeypatch, foreign_page):
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, _, _, _):
        observation, reviewed = pending_return(browser, fixture)
        other = browser.context.new_page()
        if foreign_page:
            browser.page = other
        else:
            other.close()
        with pytest.raises(PermissionError):
            browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize('challenge', ['otp', 'password', 'unknown-required', 'write', 'blocked-get'])
def test_entry_return_rejects_auth_unknown_controls_and_faults_before_observing_values(chromium, fixture, monkeypatch, challenge):
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, boundary, _, _):
        browser.open(fixture.target.entry_url)
        with pytest.raises(PermissionError): browser.preferred_entry()
        if challenge == 'otp':
            browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<input aria-label=\"Verification code\" autocomplete=\"one-time-code\" value=\"SYNTHETIC_CODE_DO_NOT_HASH\">')")
        if challenge == 'password':
            browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<input type=password aria-label=\"Password\">')")
        if challenge == 'unknown-required':
            browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<input required aria-label=\"Unknown required\">')")
        if challenge == 'write': browser.write(fixture.manifest['steps'][0]['fields'][0])
        if challenge == 'blocked-get':
            browser.page.evaluate("async()=>{await fetch('/unreviewed-read').catch(()=>{});}")
            assert browser._entry_network_fault and boundary.blocked_write_count == 0
        with pytest.raises(PermissionError): browser.observe_entry_return()
        assert not fixture.ledger.has_intent()


def test_entry_return_closed_historical_popup_is_rechecked_and_readiness_consumed_once(chromium, fixture, monkeypatch):
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, _, seen, _):
        browser.open(fixture.target.entry_url)
        with pytest.raises(PermissionError): browser.preferred_entry()
        popup = browser.context.new_page()
        popup.close()
        observation, reviewed = reviewed_return(browser, fixture)
        browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
        browser.preferred_entry()
        with pytest.raises(PermissionError, match='^APPLICATION_ENTRY_RETURN_CONSUMED$'):
            browser.preferred_entry()
        assert len(seen) == 1 and not fixture.ledger.has_intent()


def test_entry_return_only_same_loaded_public_widget_is_exempt_from_challenge(chromium, fixture, monkeypatch):
    with actual_driver(chromium, fixture, monkeypatch) as (browser, _, _, _):
        # Only this synthetic public widget document is fulfilled by the test
        # page route. Production LinkedIn navigation remains denied unchanged.
        browser.page.route(WIDGET_URL.split('#')[0], lambda route: route.fulfill(
            status=200, content_type='text/html', body='<button>Apply with LinkedIn</button>'))
        browser.open(fixture.target.entry_url)
        browser.page.evaluate("url=>{const f=document.createElement('iframe');f.src=url;document.body.append(f);}", WIDGET_URL)
        browser.page.frame_locator('iframe').get_by_role('button', name='Apply with LinkedIn').wait_for()
        with pytest.raises(PermissionError): browser.preferred_entry()
        observation, reviewed = reviewed_return(browser, fixture)
        browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
        assert browser.inspect()['challenge'] is False
        browser.preferred_entry()
        browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<iframe srcdoc=\"unknown auth\"></iframe>')")
        assert browser.inspect()['challenge'] is True


@pytest.mark.parametrize('bad_url', [
    WIDGET_URL.replace('https://www.linkedin.com', 'http://www.linkedin.com'),
    WIDGET_URL.replace('www.linkedin.com', 'linkedin.com.evil.test'),
    WIDGET_URL.replace('/talentwidgets/apply-with-linkedin', '/oauth/authorize'),
    WIDGET_URL.replace('jobs.smartrecruiters.com', 'evil.test'),
    WIDGET_URL.replace('#xdOrigin=', '?xdOrigin='),
    WIDGET_URL + '&xdOrigin=https%3A%2F%2Fjobs.smartrecruiters.com',
])
def test_entry_return_widget_url_recognition_never_expands_to_foreign_auth(bad_url):
    from ejs.services.oneclick_browser import _widget_url
    assert not _widget_url(bad_url)


def enable_native_fixture(fixture, *, widget=False):
    fixture.manifest['entry_policy'] = dict(strategy='native_verified_facts', decision_ref='USER-NATIVE-MVP-20261112')
    fixture.manifest['read_urls'] = [WIDGET_URL.split('#')[0]] if widget else []
    # This legacy synthetic fixture alias is corrected locally, without editing
    # the constructor lines concurrently owned by the controller branch.
    for field in fixture.manifest['steps'][1]['fields']:
        if field['canonical_field'] == 'location.residency': field['canonical_field'] = 'work.currently_lives_in_ireland'
    for fact in fixture.facts['facts']:
        if fact['canonical_field'] == 'location.residency': fact['canonical_field'] = 'work.currently_lives_in_ireland'


@pytest.mark.parametrize('widget', [False, True])
def test_native_verified_facts_complete_without_awli_click_even_when_offered(chromium, fixture, monkeypatch, widget):
    enable_native_fixture(fixture, widget=widget)
    options = dict(awli=not widget, native_widget=widget)
    review_shapes(chromium, fixture, monkeypatch, **options)
    with actual_driver(chromium, fixture, monkeypatch, **options) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert boundary.application_requests_allowed == 1
        assert sum(method == 'GET' and url == WIDGET_URL.split('#')[0] for method, url, _ in seen) == int(widget)
        assert browser.page.evaluate('window.awliClicked||false') is False


def test_native_preference_cannot_be_injected_after_awli_handoff(chromium, fixture, monkeypatch):
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, _, _, _):
        browser.open(fixture.target.entry_url)
        with pytest.raises(PermissionError): browser.preferred_entry()
        enable_native_fixture(fixture)
        with pytest.raises(PermissionError, match='^APPLICATION_ENTRY_POLICY_CHANGED$'): browser.preferred_entry()
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize('mutation', ['auth-frame', 'policy-after-choice'])
def test_native_choice_stops_on_auth_surface_or_mutated_policy(chromium, fixture, monkeypatch, mutation):
    enable_native_fixture(fixture)
    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, boundary, _, _):
        browser.open(fixture.target.entry_url)
        if mutation == 'auth-frame':
            browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<iframe srcdoc=\"Unknown authentication\"></iframe>')")
            with pytest.raises(PermissionError): browser.preferred_entry()
        else:
            browser.preferred_entry()
            fixture.manifest['entry_policy']['decision_ref'] = 'UNREVIEWED-CHANGE'
            with pytest.raises(PermissionError, match='^APPLICATION_ENTRY_POLICY_CHANGED$'): browser.inspect()
        assert boundary.upload_requests_allowed == boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()


def test_native_unknown_parsed_profile_after_cv_stops_before_any_application_post(chromium, fixture, monkeypatch):
    enable_native_fixture(fixture)
    review_shapes(chromium, fixture, monkeypatch, unexpected_profile=True)
    with actual_driver(chromium, fixture, monkeypatch, unexpected_profile=True) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED' and result['applied'] is False
        assert boundary.upload_requests_allowed == 1 and boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()
        assert [method for method, _, _ in seen] == ['GET', 'POST']


@contextmanager
def profile_driver(chromium, fixture, monkeypatch, **faults):
    from html import escape
    phase = fixture.manifest['steps'][0]['reviewed_profile_corrections']
    edit = phase['edits'][0]; plan = edit['editor']
    markup = '<div id="profile"><section id="profile-target" class="profile-card"><span id="profile-target-text" style="white-space:pre-line">'
    markup += escape('WRONG IDENTITY' if faults.get('identity') else phase['cards'][0]['observed_text'])
    markup += '</span><button id="profile-edit" aria-label="' + escape(edit['edit']['label']) + '"></button></section>'
    markup += '<section id="profile-other" class="profile-card"><span id="profile-other-text">Verified untouched record</span><button aria-label="Edit '
    markup += phase['cards'][1]['record_type'] + ' Other record"></button></section></div><div id="profile-editor" hidden>'
    for field in plan['fields']:
        field_id = field['key']
        markup += '<label for="' + field_id + '">' + escape(field['label']) + '</label>'
        required = ' required' if field['required'] else ''
        kind = 'date' if faults.get('day') and field['label'] == 'From' else field['kind']
        initial = '2026-09-01' if kind == 'date' else '2026-09' if kind == 'month' else 'Imported value'
        if field['label'] == 'Description':
            markup += '<textarea id="' + field_id + '"' + required + '>Imported description</textarea>'
        else:
            markup += '<input id="' + field_id + '" type="' + kind + '" value="' + initial + '"' + required + '>'
    markup += '<button id="profile-save" type="button">Save</button><button id="profile-cancel" type="button">Cancel</button></div>'
    script = """<script>
      const profileFields=FIELDS;const profileFaults=FAULTS;const kind=KIND;
      const profileControl = field => {
        const scan=root=>{for(const input of root.querySelectorAll('input,textarea,select'))if(input.dataset.profileKey===field.key)return input;
          for(const host of root.querySelectorAll('*'))if(host.shadowRoot){const found=scan(host.shadowRoot);if(found)return found;}};
        return scan(document);
      };
      for(const field of profileFields){
        const input=document.getElementById(field.key);input.dataset.profileKey=field.key;
        const mount=(tag,id,parent)=>{const host=document.createElement(tag);if(id)host.id=id;host.style.display='block';parent.append(host);return host;};
        if(field.text_autocomplete){
          const tags={'Title':'oc-job-title-autocomplete','Company':'oc-company-autocomplete','Office location':'oc-location-autocomplete'};
          const host=document.createElement(tags[field.label]);host.id='spl-'+field.key;input.replaceWith(host);
          const root=host.attachShadow({mode:'open'});const auto=mount('spl-autocomplete',input.id,root).attachShadow({mode:'open'});
          const dropdown=mount('spl-dropdown','',auto).attachShadow({mode:'open'});const wrapper=mount('spl-input',input.id,dropdown);
          if(field.required)wrapper.setAttribute('required','');input.removeAttribute('required');input.setAttribute('aria-label',field.label);input.setAttribute('role','combobox');
          wrapper.attachShadow({mode:'open'}).append(input);
        }
        if(field.month_picker){
          const host=document.createElement('spl-date-field');host.id='spl-'+field.key;host.setAttribute('type',profileFaults.precision?'full':'month-year');
          if(field.required||profileFaults.required_month)host.setAttribute('required','');input.replaceWith(host);
          const picker=mount('spl-date-picker','',host.attachShadow({mode:'open'}));picker.setAttribute('type','month-year');picker.setAttribute('aria-labelledby',host.id+'-label');
          if(field.required||profileFaults.required_month)picker.setAttribute('required','');const root=picker.attachShadow({mode:'open'});
          input.type='text';input.value='2026-09-01';input.id='';input.removeAttribute('required');input.setAttribute('aria-label',field.label);input.placeholder='Pick a date';input.className='flatpickr-input';
          input.setAttribute('aria-controls','calendar-'+field.key);root.append(input);
          const clear=document.createElement('span');clear.setAttribute('role','button');clear.setAttribute('aria-label','Clear date');clear.textContent='Clear';root.append(clear);
          const calendar=document.createElement('div');calendar.id='calendar-'+field.key;calendar.setAttribute('role','dialog');calendar.setAttribute('aria-label',profileFaults.wrong_calendar?'Wrong picker':field.label);calendar.hidden=true;root.append(calendar);
          let year=2026;
          const render=()=>{
            calendar.replaceChildren();const spin=document.createElement('input');spin.type='number';spin.setAttribute('aria-label','Year');spin.value=String(year);calendar.append(spin);
            for(const [label,delta] of [['Previous year',-1],['Next year',1]]){const button=document.createElement('button');button.type='button';button.setAttribute('aria-label',label);button.textContent=label;
              button.addEventListener('click',()=>{year+=delta*(profileFaults.year_leap?2:1);render();});calendar.append(button);}
            const months=['January','February','March','April','May','June','July','August','September','October','November','December'];
            for(let n=0;n<(profileFaults.day_calendar?31:12);n++){const cell=document.createElement('span');const name=months[n%12];cell.setAttribute('role','gridcell');
              cell.setAttribute('aria-label',name+' '+(profileFaults.day_calendar?n+1:1)+', '+year);cell.textContent=name.slice(0,3);
              cell.addEventListener('click',()=>{input.value=profileFaults.format?name+' '+year:String(year)+'-'+String(n+1).padStart(2,'0')+'-01';calendar.hidden=true;clear.hidden=false;});calendar.append(cell);}
            if(profileFaults.calendar_extra){const extra=document.createElement('input');extra.setAttribute('aria-label','Unreviewed calendar input');extra.tabIndex=-1;calendar.append(extra);}
            if(profileFaults.calendar_role_extra){const extra=document.createElement('div');extra.setAttribute('role','combobox');extra.setAttribute('aria-label','Unreviewed calendar combobox');extra.textContent='Unknown choice';extra.tabIndex=-1;calendar.append(extra);}
          };render();
          input.addEventListener('click',()=>{calendar.hidden=false;if(profileFaults.extra_on_open){const extra=document.createElement('input');extra.setAttribute('aria-label','Unreviewed editor input');document.querySelector('#profile-editor').append(extra);}});
          clear.addEventListener('click',()=>{input.value='';clear.hidden=true;calendar.hidden=true;});
        }
      }
      document.querySelector('#profile-edit').addEventListener('click',()=>{
        document.querySelector('#profile-editor').hidden=false;
        if(profileFaults.extra){const input=document.createElement('input');input.setAttribute('aria-label','Unexpected optional field');document.querySelector('#profile-editor').append(input);}
      });
      document.querySelector('#profile-cancel').addEventListener('click',()=>document.querySelector('#profile-editor').hidden=true);
      document.querySelector('#profile-save').addEventListener('click',()=>{
        const values={};for(const field of profileFields){const input=profileControl(field);values[field.label]=field.kind==='checkbox'?input.checked:field.month_picker&&/^\\d{4}-\\d{2}-01$/.test(input.value)?input.value.slice(0,7):input.value;}
        let summary;
        if(kind==='experience')summary=values.Title+'\\n'+values.Company+'\\n'+values.From+' – '+values.To+'\\n'+values.Description;
        else summary=values.Institution+'\\n'+values.Degree+'\\n'+(values.From&&values.To?values.From+' – '+values.To:values.Description);
        document.querySelector('#profile-target-text').textContent=summary;
        document.querySelector('#profile-editor').hidden=true;
        if(profileFaults.other)document.querySelector('#profile-other-text').textContent='UNREVIEWED OTHER CHANGE';
        if(profileFaults.network)fetch('/unreviewed-profile-save',{method:'POST',body:'SYNTHETIC'}).catch(()=>{});
      });
    </script>""".replace('FIELDS', json.dumps(plan['fields'])).replace('FAULTS', json.dumps(faults)).replace('KIND', json.dumps(phase['cards'][0]['record_type']))
    rendered = html_for(fixture).replace('<head>', '<head><meta charset="utf-8">').replace('</main>', markup + '</main>').replace('</body>', script + '</body>')
    with monkeypatch.context() as local:
        local.setitem(globals(), 'html_for', lambda *_args, **_options: rendered)
        with actual_driver(chromium, fixture, monkeypatch) as state:
            yield state


def review_profile_shapes(chromium, fixture, monkeypatch):
    from ejs.services.oneclick_browser import _DESCRIBE
    plan = fixture.manifest['steps'][0]['reviewed_profile_corrections']['edits'][0]['editor']
    with profile_driver(chromium, fixture, monkeypatch) as (browser, _, _, _):
        browser.open(fixture.target.entry_url)
        browser.preferred_entry()
        fixture.manifest['steps'][0]['fingerprint'] = browser.inspect()['fingerprint']
        browser.page.locator('#profile-edit').click()  # Independent synthetic reviewer, never a live discovery click.
        editor = browser.page.locator('#profile-editor')
        for binding in plan['fields']:
            if 'month_picker' in binding:
                browser._locator(binding, editor).click()
                observed = browser._profile_month_calendar_snapshot(binding, editor)
                binding['month_picker']['calendar_fingerprint'] = observed['fingerprint']
                observed['calendar'].evaluate('el=>el.hidden=true')  # Synthetic reviewer closes the local fixture only.
        controls, clear = browser._profile_editor_controls(plan, editor)
        shape = {'controls': sorted([info for _, info in controls], key=lambda x: json.dumps(x, sort_keys=True)),
            'actions': sorted([dict(label=b.get_attribute('aria-label') or b.inner_text().strip(), disabled=not b.is_enabled(), type=b.get_attribute('type') or '')
                               for b in editor.locator('button,[role="button"]').all() if b.is_visible()
                               and not any(b.evaluate('(el, clear)=>el===clear', c.element_handle()) for c in clear)], key=lambda x: json.dumps(x, sort_keys=True))}
        plan['fingerprint'] = digest(shape)
        browser.page.locator('#profile-cancel').click()
        browser.advance(fixture.manifest['steps'][0]['next'], fixture.target.screening_url)
        fixture.manifest['steps'][1]['fingerprint'] = browser.inspect()['fingerprint']


@pytest.mark.parametrize('record_type,omit_month', [('experience', False), ('education', True)])
def test_profile_source_backed_native_edit_save_then_normal_flow_preserves_other_cards(chromium, fixture, monkeypatch, record_type, omit_month):
    from tests.test_single_application_inputs import correction_plan
    enable_native_fixture(fixture)
    correction_plan(fixture.manifest, fixture.facts, record_type=record_type, omit_month=omit_month)
    review_profile_shapes(chromium, fixture, monkeypatch)
    with profile_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert browser._profile_corrections_applied is True
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 1
        assert [method for method, _, _ in seen] == ['GET', 'POST', 'POST']


@pytest.mark.parametrize('fault', ['identity', 'extra', 'other', 'network', 'day'])
def test_profile_edit_identity_complete_controls_other_cards_and_write_boundaries_fail_closed(chromium, fixture, monkeypatch, fault):
    from tests.test_single_application_inputs import correction_plan
    enable_native_fixture(fixture)
    correction_plan(fixture.manifest, fixture.facts)
    review_profile_shapes(chromium, fixture, monkeypatch)
    with profile_driver(chromium, fixture, monkeypatch, **{fault: True}) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED', result
        assert boundary.upload_requests_allowed == boundary.application_requests_allowed == 0
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize('ordinary_prefill', [False, True])
def test_retained_return_defers_only_exact_reviewed_profile_card_not_other_prefill(chromium, fixture, monkeypatch, ordinary_prefill):
    from tests.test_single_application_inputs import correction_plan
    enable_native_fixture(fixture)
    fixture.manifest.pop('entry_policy')  # Separate, unchanged retained-return library lane.
    correction_plan(fixture.manifest, fixture.facts)
    review_profile_shapes(chromium, fixture, monkeypatch)
    with profile_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        browser.open(fixture.target.entry_url)
        browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<button>Apply with LinkedIn</button>')")
        if ordinary_prefill:
            browser.page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<input aria-label=\"Unreviewed optional field\" value=\"UNVERIFIED\">')")
        with pytest.raises(PermissionError): browser.preferred_entry()
        observation, reviewed = reviewed_return(browser, fixture)
        if ordinary_prefill:
            with pytest.raises(PermissionError): browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
            assert browser.page.locator('#profile-editor').is_hidden()
        else:
            browser.accept_reviewed_entry_return(observation['observation_id'], reviewed)
            browser.preferred_entry()
            browser.correct_reviewed_profile()
            assert browser.page.locator('#profile-target').inner_text().strip() == reviewed['steps'][0]['profile_assertions'][0]['value']
            assert browser.page.locator('#profile-other').inner_text().strip() == 'Verified untouched record'
        assert [method for method, _, _ in seen] == ['GET']
        assert boundary.upload_requests_allowed == boundary.application_requests_allowed == 0 and not fixture.ledger.has_intent()


@pytest.mark.parametrize('record_type,omit_month', [('experience', False), ('education', True)])
def test_reviewed_spl_profile_widgets_use_month_precision_and_verified_text_only(chromium, fixture, monkeypatch, record_type, omit_month):
    from tests.test_single_application_inputs import spl_correction_plan
    enable_native_fixture(fixture)
    spl_correction_plan(fixture.manifest, fixture.facts, record_type=record_type, omit_month=omit_month)
    review_profile_shapes(chromium, fixture, monkeypatch)
    with profile_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert browser._profile_corrections_applied is True
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 1
        assert [method for method, _, _ in seen] == ['GET', 'POST', 'POST']


def test_spl_month_day_one_transport_is_never_a_candidate_day_fact(chromium, fixture, monkeypatch):
    from tests.test_single_application_inputs import spl_correction_plan
    enable_native_fixture(fixture); phase = spl_correction_plan(fixture.manifest, fixture.facts)
    review_profile_shapes(chromium, fixture, monkeypatch)
    plan = phase['edits'][0]['editor']; binding = next(field for field in plan['fields'] if field['label'] == 'From')
    with profile_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        browser.open(fixture.target.entry_url); browser.preferred_entry(); browser.page.locator('#profile-edit').click()
        editor = browser._profile_editor_surface(plan)
        assert browser._profile_readback(binding, editor) is None
        browser._profile_write(binding, editor, plan)
        assert browser._locator(binding, editor).input_value() == '2025-01-01'
        assert browser._profile_readback(binding, editor) == '2025-01'
        assert next(f for f in fixture.facts['facts'] if f['canonical_field'] == binding['canonical_field'])['value'] == '2025-01'
        assert [method for method, _, _ in seen] == ['GET']
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0


@pytest.mark.parametrize('fault', ['precision', 'day_calendar', 'wrong_calendar', 'format', 'year_leap', 'calendar_extra', 'calendar_role_extra', 'extra_on_open', 'network'])
def test_spl_profile_month_picker_rejects_precision_calendar_format_drift_and_network_save(chromium, fixture, monkeypatch, fault):
    from tests.test_single_application_inputs import spl_correction_plan
    enable_native_fixture(fixture); spl_correction_plan(fixture.manifest, fixture.facts)
    review_profile_shapes(chromium, fixture, monkeypatch)
    with profile_driver(chromium, fixture, monkeypatch, **{fault: True}) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED', result
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0
        assert not fixture.ledger.has_intent()


@pytest.mark.parametrize('fault', ['required_month', 'missing_clear'])
def test_unknown_spl_month_is_never_guessed_or_preserved_without_reviewed_clear(chromium, fixture, monkeypatch, fault):
    from tests.test_single_application_inputs import spl_correction_plan
    enable_native_fixture(fixture); phase = spl_correction_plan(fixture.manifest, fixture.facts, record_type='education', omit_month=True)
    review_profile_shapes(chromium, fixture, monkeypatch)
    if fault == 'missing_clear':
        for binding in phase['edits'][0]['editor']['fields']:
            if 'month_picker' in binding: binding['month_picker'].pop('clear')
    with profile_driver(chromium, fixture, monkeypatch, **{fault: True}) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED', result
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0
        assert not fixture.ledger.has_intent()


@contextmanager
def city_driver(chromium, fixture, monkeypatch, **faults):
    script = """<script>
      const cityFaults=FAULTS;const form=document.querySelector('#form');
      const host=document.createElement(cityFaults.wrong_host?'oc-company-autocomplete':'oc-location-autocomplete');host.id='city-host';form.append(host);
      const mount=(tag,parent)=>{const node=document.createElement(tag);node.id=tag==='spl-dropdown'?'':'city';node.style.display='block';parent.append(node);return node.attachShadow({mode:'open'});};
      const auto=mount('spl-autocomplete',host.attachShadow({mode:'open'}));const drop=mount('spl-dropdown',auto);const root=mount('spl-input',drop);
      const input=document.createElement('input');input.id='city';input.type='text';input.setAttribute('role','combobox');input.setAttribute('aria-label','City');root.append(input);
      const options=document.createElement('div');options.id='city-options';options.setAttribute('role','listbox');options.setAttribute('data-clicks','0');
      options.innerHTML='<span role="option">Synthetic City</span>';form.append(options);
      options.addEventListener('click',()=>options.setAttribute('data-clicks','1'));
      const decoy=document.createElement('input');decoy.id='city-decoy';decoy.type='text';decoy.setAttribute('role','combobox');decoy.setAttribute('aria-label','City');form.append(decoy);
      input.addEventListener('input',()=>{
        if(cityFaults.extra){const unknown=document.createElement('input');unknown.setAttribute('aria-label','New optional control');form.append(unknown);}
        if(cityFaults.search)fetch('/unreviewed-city-search').catch(()=>{});
        if(cityFaults.selection)input.setCustomValidity('Select an independently verified city option');
        if(cityFaults.readback)input.value='Wrong city';
      });
    </script>""".replace('FAULTS', json.dumps(faults))
    rendered = html_for(fixture).replace('</body>', script + '</body>')
    with monkeypatch.context() as local:
        local.setitem(globals(), 'html_for', lambda *_args, **_kwargs: rendered)
        with actual_driver(chromium, fixture, monkeypatch) as state:
            yield state


def review_city_shapes(chromium, fixture, monkeypatch):
    with city_driver(chromium, fixture, monkeypatch) as (browser, _, _, _):
        browser.open(fixture.target.entry_url); browser.preferred_entry()
        fixture.manifest['steps'][0]['fingerprint'] = browser.inspect()['fingerprint']
        browser.advance(fixture.manifest['steps'][0]['next'], fixture.target.screening_url)
        fixture.manifest['steps'][1]['fingerprint'] = browser.inspect()['fingerprint']


def test_explicit_city_widget_fills_verified_native_value_without_selecting_suggestion(chromium, fixture, monkeypatch):
    from tests.test_single_application_inputs import city_binding
    enable_native_fixture(fixture); binding = city_binding(fixture.manifest, fixture.facts)
    review_city_shapes(chromium, fixture, monkeypatch)
    with city_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        browser.open(fixture.target.entry_url); browser.preferred_entry()
        browser.write(binding)
        assert browser.readback(binding) == 'Synthetic City'
        assert browser.page.locator('#city-options').get_attribute('data-clicks') == '0'
        assert [method for method, _, _ in seen] == ['GET']
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0


def test_explicit_city_widget_is_part_of_actual_native_two_step_runtime(chromium, fixture, monkeypatch):
    from tests.test_single_application_inputs import city_binding
    enable_native_fixture(fixture); city_binding(fixture.manifest, fixture.facts)
    review_city_shapes(chromium, fixture, monkeypatch)
    with city_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert boundary.application_requests_allowed == 1
        assert [method for method, _, _ in seen] == ['GET', 'POST', 'POST']


@pytest.mark.parametrize('fault', ['wrong_host', 'extra', 'search', 'selection', 'readback', 'retarget'])
def test_city_widget_never_guesses_suggestions_ignores_changes_or_targets_other_inputs(chromium, fixture, monkeypatch, fault):
    from tests.test_single_application_inputs import city_binding
    enable_native_fixture(fixture); binding = city_binding(fixture.manifest, fixture.facts)
    review_city_shapes(chromium, fixture, monkeypatch)
    if fault == 'retarget': binding['selector'] = 'input#city-decoy'
    with city_driver(chromium, fixture, monkeypatch, **{fault: True}) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED', result
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0
        assert not fixture.ledger.has_intent()


@contextmanager
def phone_country_driver(chromium, fixture, monkeypatch, **faults):
    script = """<script>
      const phoneFaults=FAULTS,form=document.querySelector('#form');
      const host=document.createElement(phoneFaults.wrong_host?'spl-autocomplete':'spl-select');
      host.id='phone-country';host.className='c-spl-phone-field-select';host.setAttribute('value','PT');host.setAttribute('splarialabel','Country code');
      Object.defineProperty(host,'value',{get(){return host.getAttribute('value')}});form.append(host);
      const prefix=document.createElement('div');prefix.slot='triggerPrefix';prefix.className='c-spl-phone-field-selected';
      const flag=document.createElement('spl-country-flag');flag.style.cssText='display:inline-block;width:16px;height:16px';
      flag.setAttribute('countryisocode',phoneFaults.wrong_flag?'TR':'PT');flag.setAttribute('alt','Portugal');
      const dial=document.createElement('spl-typography-body');dial.textContent=phoneFaults.shared_code?'+90':'+351';prefix.append(flag,dial);host.append(prefix);
      if(phoneFaults.duplicate_prefix)host.append(prefix.cloneNode(true));
      const shadow=host.attachShadow({mode:'open'});shadow.innerHTML='<spl-dropdown><button id="phone-country" type="button" role="combobox" aria-label="Country code" aria-required="false" aria-controls="phone-country-menu"><slot name="triggerPrefix"></slot> </button><div id="phone-country-menu" role="listbox" hidden><slot></slot></div></spl-dropdown>';
      const button=shadow.querySelector('button'),menu=shadow.querySelector('[role=listbox]');
      for(const [iso,label,code] of [['PT','Portugal','+351'],['TR','Turkey','+90']]){
        const currentCode=iso==='PT'&&phoneFaults.shared_code?'+90':code;
        const option=document.createElement('spl-select-option');option.setAttribute('value',iso);option.setAttribute('label',phoneFaults.target_label&&iso==='TR'?'Unreviewed country':label);
        option.className='c-spl-phone-field-select-option';option.style.display='block';option.textContent=label+' '+currentCode;
        const item=document.createElement('spl-dropdown-item');if(iso==='PT'||phoneFaults.ambiguous_selected)item.setAttribute('selected','');
        item.append(document.createElement('slot'));option.attachShadow({mode:'open'}).append(item);host.append(option);
        option.addEventListener('click',()=>{
          for(const other of host.querySelectorAll('spl-select-option'))other.shadowRoot.querySelector('spl-dropdown-item').removeAttribute('selected');
          item.setAttribute('selected','');host.setAttribute('value',iso);flag.setAttribute('countryisocode',iso);flag.setAttribute('alt',label);dial.textContent=phoneFaults.readback?'+999':code;menu.hidden=true;
          if(phoneFaults.network)fetch('/unreviewed-phone-save',{method:'POST',body:'synthetic'}).catch(()=>{});
        });
      }
      if(phoneFaults.selected_mismatch)host.setAttribute('value','TR');
      button.addEventListener('click',()=>{menu.hidden=false;if(phoneFaults.extra){const unknown=document.createElement('input');unknown.setAttribute('aria-label','New optional phone field');form.append(unknown);}});
      const decoy=document.createElement('button');decoy.id='phone-decoy';decoy.type='button';decoy.setAttribute('role','combobox');decoy.setAttribute('aria-label','Country code');form.append(decoy);
    </script>""".replace('FAULTS', json.dumps(faults))
    rendered = html_for(fixture).replace('</body>', script + '</body>')
    with monkeypatch.context() as local:
        local.setitem(globals(), 'html_for', lambda *_args, **_kwargs: rendered)
        with actual_driver(chromium, fixture, monkeypatch) as state:
            yield state


def review_phone_country_shapes(chromium, fixture, monkeypatch):
    with phone_country_driver(chromium, fixture, monkeypatch) as (browser, _, _, _):
        browser.open(fixture.target.entry_url); browser.preferred_entry()
        fixture.manifest['steps'][0]['fingerprint'] = browser.inspect()['fingerprint']
        browser.advance(fixture.manifest['steps'][0]['next'], fixture.target.screening_url)
        fixture.manifest['steps'][1]['fingerprint'] = browser.inspect()['fingerprint']


@pytest.mark.parametrize('shared_code', [False, True])
def test_phone_country_uses_owned_selected_country_and_visible_assigned_prefix(chromium, fixture, monkeypatch, shared_code):
    from tests.test_single_application_inputs import phone_country_binding
    enable_native_fixture(fixture); binding = phone_country_binding(fixture.manifest, fixture.facts)
    review_phone_country_shapes(chromium, fixture, monkeypatch)
    with phone_country_driver(chromium, fixture, monkeypatch, shared_code=shared_code) as (browser, boundary, seen, _):
        browser.open(fixture.target.entry_url); browser.preferred_entry()
        assert browser.page.locator(binding['selector']).evaluate('el=>el.value') == ''
        assert browser.readback(binding) == (None if shared_code else '+351')
        browser.write(binding)
        assert browser.readback(binding) == '+90'
        assert browser.page.locator('spl-select#phone-country').get_attribute('value') == 'TR'
        generic = {key: value for key, value in binding.items() if key != 'phone_country_select'}
        assert browser.readback(generic) == ''  # No generic nearest-host fallback.
        assert [method for method, _, _ in seen] == ['GET']
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0


def test_phone_country_is_verified_in_actual_native_two_step_runtime(chromium, fixture, monkeypatch):
    from tests.test_single_application_inputs import phone_country_binding
    enable_native_fixture(fixture); phone_country_binding(fixture.manifest, fixture.facts)
    review_phone_country_shapes(chromium, fixture, monkeypatch)
    with phone_country_driver(chromium, fixture, monkeypatch) as (browser, boundary, seen, _):
        result = run(fixture, browser)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 1
        assert [method for method, _, _ in seen] == ['GET', 'POST', 'POST']


@pytest.mark.parametrize('fault', ['wrong_host', 'wrong_flag', 'duplicate_prefix', 'ambiguous_selected', 'selected_mismatch', 'target_label', 'readback', 'extra', 'network', 'retarget'])
def test_phone_country_rejects_unowned_inconsistent_ambiguous_or_changed_surfaces(chromium, fixture, monkeypatch, fault):
    from tests.test_single_application_inputs import phone_country_binding
    enable_native_fixture(fixture); binding = phone_country_binding(fixture.manifest, fixture.facts)
    review_phone_country_shapes(chromium, fixture, monkeypatch)
    if fault == 'retarget': binding['selector'] = '#phone-decoy'
    with phone_country_driver(chromium, fixture, monkeypatch, **{fault: True}) as (browser, boundary, _, _):
        result = run(fixture, browser)
        assert result['status'] == 'BLOCKED', result
        assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0
        assert not fixture.ledger.has_intent()


def test_retained_cli_controller_actual_page_private_pins_and_one_confirmed_fixture_application(
        chromium, fixture, monkeypatch, tmp_path, capsys):
    """TEST same-page return only: no OAuth bridge, grant or employer traffic.

    Source-clean Git probing is isolated here; the controller's pure suite covers
    its rejection paths. The browser, input compiler, runtime and SQLite ledger
    are real. Every browser HTTPS request is fulfilled by actual_driver locally.
    """
    from dataclasses import asdict
    from types import SimpleNamespace
    import ejs.services.retained_entry_controller as controller_module
    from ejs.services.retained_entry_controller import RetainedEntryController

    fixture.manifest['target'] = asdict(fixture.target)
    review_shapes(chromium, fixture, monkeypatch, awli=True)
    private_state = tmp_path.resolve() / 'controller-state'
    private_state.mkdir(mode=0o700)
    control = private_state / 'entry-return-controller'
    def source_probe(command, **kwargs):
        assert command[:2] == ['git', '-C']
        assert 0 < kwargs['timeout'] <= 10
        return SimpleNamespace(stdout='' if 'status' in command else fixture.manifest['source_sha'])
    monkeypatch.setattr(controller_module.subprocess, 'run', source_probe)
    def public_posting(target):
        assert target is fixture.target
        return fixture.posting
    monkeypatch.setattr('ejs.services.single_application_runtime.fetch_posting', public_posting)

    with actual_driver(chromium, fixture, monkeypatch, awli=True) as (browser, boundary, seen, _):
        runtime = SingleApplicationRuntime(browser, fixture.ledger)
        handoff = runtime.run(fixture.manifest, fixture.facts, fixture.target, fixture.cv,
                             authority=ON, posting_payload=fixture.posting)
        assert handoff['status'] == 'HUMAN_HANDOFF_REQUIRED'
        assert fixture.ledger.has_intent() is False
        page, context = browser.page, browser.context
        page.evaluate("""window.syntheticAwliClicks=0;
          document.querySelector('button').addEventListener('click',()=>window.syntheticAwliClicks++);""")
        monkeypatch.setattr(browser, 'open', lambda *_: pytest.fail('Retained controller cannot open another page'))
        original_tick = page.wait_for_timeout
        ticks = []
        def trusted_private_operator(milliseconds):
            # Keep the real Playwright event pump; only the independent local
            # reviewer/control-file writer is simulated by this test.
            original_tick(milliseconds)
            ticks.append(milliseconds)
            session = json.loads((control / 'session.json').read_text())
            if len(ticks) == 1:
                command = dict(session_id=session['session_id'], sequence=1, action='observe_return')
            elif len(ticks) == 2:
                observation = json.loads((control / 'observation.json').read_text())
                reviewed = deepcopy(fixture.manifest)
                reviewed['steps'][0]['fingerprint'] = observation['surface_fingerprint']
                reviewed['entry_return_review'] = dict(
                    observation_id=observation['observation_id'],
                    surface_fingerprint=observation['surface_fingerprint'],
                    reviewed_at=datetime.now(timezone.utc).isoformat(),
                    reviewer_ref=reviewed['reviewer_ref'], return_url=fixture.target.entry_url)
                pins = {}
                for filename, data, pin in (
                        ('reviewed-manifest.json', reviewed, 'manifest_sha256'),
                        ('reviewed-facts.json', fixture.facts, 'facts_sha256')):
                    raw = json.dumps(data).encode()
                    path = control / filename
                    path.write_bytes(raw); path.chmod(0o600)
                    pins[pin] = hashlib.sha256(raw).hexdigest()
                command = dict(session_id=session['session_id'], sequence=2,
                               action='resume_pinned_review', **pins)
            else:
                pytest.fail('Expected one observe and one resumed private review')
            path = control / 'request.json'
            path.write_text(json.dumps(command)); path.chmod(0o600)
        monkeypatch.setattr(page, 'wait_for_timeout', trusted_private_operator)
        controller = RetainedEntryController(private_state, private_state / 'synthetic-source',
                                             fixture.manifest['source_sha'], timeout_seconds=10)
        result = controller.wait_for_review(runtime, browser, fixture.ledger, fixture.manifest,
            fixture.facts, fixture.target, fixture.cv, authority=ON)
        assert result['status'] == 'EMPLOYER_CONFIRMED', result
        assert result['applied'] is True and result['submit_click_attempts'] == 1
        assert browser.page is page and browser.context is context
        assert page.evaluate('window.syntheticAwliClicks') == 0
        assert ticks == [100, 100]
        assert fixture.ledger.snapshot()['status'] == 'Confirmed'
        assert boundary.application_attempts == boundary.application_requests_allowed == 1
        assert boundary.upload_requests_allowed == 1
        assert [method for method, _, _ in seen] == ['GET', 'POST', 'POST']
        assert sum(url == fixture.target.application_url for _, url, _ in seen) == 1
        assert capsys.readouterr().out == ''
        assert not (control / 'active.lock').exists()
        assert not (control / 'session.json').exists()
        assert not (control / 'observation.json').exists()


@pytest.mark.parametrize('bad_url', [
    WIDGET_URL.replace('www.linkedin.com', 'www.linkedin.com:443'),
    WIDGET_URL.replace('www.linkedin.com', 'user@www.linkedin.com'),
    WIDGET_URL.replace('https://www.linkedin.com', 'https://WWW.LINKEDIN.COM'),
    WIDGET_URL.replace('www.linkedin.com', 'www.linkedin.com.'),
    WIDGET_URL.replace('https://www.linkedin.com', 'https://www.linkedin.com\\\\@evil.test'),
    WIDGET_URL + '&xdChannel=00000000-0000-0000-0000-000000000000',
    WIDGET_URL + '&redirect_uri=https%3A%2F%2Fevil.test',
    WIDGET_URL.replace('#xdOrigin=', '#unknown=1&xdOrigin='),
    WIDGET_URL.replace('xdChannel=', 'xdChannel=%00'),
    WIDGET_URL + '%0A',
])
def test_entry_return_widget_url_rejects_authority_and_fragment_smuggling(bad_url):
    from ejs.services.oneclick_browser import _widget_url
    assert not _widget_url(bad_url)
