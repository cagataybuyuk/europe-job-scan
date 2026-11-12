"""Offline integration of reviewed inputs, browser observations and real ledger.

Every employer effect is a recording fake. No legacy execution receipt is
constructed and no public/employer request is made by this suite.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import Barrier

import pytest

from ejs.contracts.submit import SubmitAuthority
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.oneclick_target import OneClickTarget
from ejs.services.single_application_inputs import load_reviewed_application_inputs
from ejs.services.single_application_runtime import (
    ConfirmationObservation, SingleApplicationRuntime, UploadObservation,
)


NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
ON = SubmitAuthority(browser_external_form_write=True, final_submit_runtime_flag=True,
                     kill_switch_engaged=False)
SOURCE = "a" * 40


class FixedClock:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return NOW


class RecordingBrowser:
    """Own independent actual surfaces and values, not self-asserted receipts."""
    def __init__(self, manifest, target, ledger, clock):
        self.screens = deepcopy(manifest["steps"])
        self.target, self.ledger, self.clock = target, ledger, clock
        self.index = 0
        self.calls = []
        self.values = {}
        self.flags = {}
        self.after_write = self.after_upload = self.after_advance = None
        self.read_override = self.upload_override = None
        self.submit_error = None
        self.confirmation_changes = {}
        self.entry_barrier = None

    def count(self, action):
        return sum(call[0] == action for call in self.calls)

    def open(self, url):
        self.calls.append(("open", url))
        assert url == self.target.entry_url
        self.index = 0

    def preferred_entry(self):
        self.calls.append(("preferred_entry",))
        if self.entry_barrier is not None:
            self.entry_barrier.wait(timeout=10)

    def correct_reviewed_profile(self):
        self.calls.append(("profile_correction",))
        if getattr(self, "profile_error", None):
            raise self.profile_error

    def accept_reviewed_entry_return(self, observation_id, manifest):
        self.calls.append(("accept_return", observation_id))
        if observation_id != "fixture-reviewed-return":
            raise PermissionError("APPLICATION_RETURN_OBSERVATION_REQUIRED")
        assert manifest["steps"][0]["url"] == self.target.entry_url

    def inspect(self):
        self.calls.append(("inspect", self.index))
        screen = self.screens[self.index]
        required = {field["key"] for field in screen["fields"] if field["required"]}
        if "upload" in screen:
            required.add(screen["upload"]["key"])
        result = dict(url=screen["url"], fingerprint=screen["fingerprint"], required_keys=required,
                      challenge=False, unexpected_write=False, unreviewed_prefill=False,
                      form_valid=True, submit_visible=self.index == len(self.screens) - 1)
        result.update(deepcopy(self.flags))
        return result

    def write(self, binding):
        actual = next(field for field in self.screens[self.index]["fields"]
                      if field["key"] == binding["key"])
        assert binding["selector"] == actual["selector"]
        self.calls.append(("write", binding["key"]))
        self.values[binding["key"]] = binding["value"]
        if self.after_write:
            self.after_write(self, binding)

    def readback(self, binding):
        self.calls.append(("readback", binding["key"]))
        if self.read_override:
            changed, value = self.read_override(self, binding)
            if changed:
                return value
        return self.values.get(binding["key"])

    def upload(self, binding, payload, filename):
        assert binding["key"] == self.screens[self.index]["upload"]["key"]
        self.calls.append(("upload", hashlib.sha256(payload).hexdigest(), len(payload), filename))
        observed = UploadObservation(hashlib.sha256(payload).hexdigest(), len(payload), filename,
                                     "d" * 64, self.target.resume_upload_url(filename), True)
        if self.after_upload:
            self.after_upload(self)
        return self.upload_override(observed) if self.upload_override else observed

    def advance(self, binding, expected_url):
        assert binding["selector"] == self.screens[self.index]["next"]["selector"]
        self.calls.append(("advance", expected_url))
        self.index += 1
        if self.after_advance:
            self.after_advance(self)

    def submit_once(self, binding):
        assert binding["selector"] == self.screens[self.index]["submit"]["selector"]
        assert self.count("submit") == 0
        # Assert on the actual SQLite state at the external-effect boundary.
        state = self.ledger.snapshot()
        assert state["status"] == "Submitted / Unconfirmed" and state["started_at"] is not None
        self.calls.append(("submit",))
        if self.submit_error:
            raise self.submit_error
        confirmation = ConfirmationObservation(self.target.application_url, NOW, 201, "e" * 64,
                                               True, True, False)
        return replace(confirmation, **self.confirmation_changes)


@dataclass
class Scenario:
    manifest: dict
    facts: dict
    target: OneClickTarget
    cv_path: Path
    cv_payload: bytes
    posting: bytes
    ledger: SqliteSubmissionLedger
    browser: RecordingBrowser
    clock: FixedClock

    def run(self, **kwargs):
        runtime = SingleApplicationRuntime(self.browser, self.ledger, clock=self.clock)
        return runtime.run(self.manifest, self.facts, self.target, self.cv_path,
                           authority=kwargs.pop("authority", ON),
                           posting_payload=kwargs.pop("posting_payload", self.posting), **kwargs)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    # Resolve pytest's Darwin /var alias before strict CV/ledger path checks.
    private = tmp_path.resolve() / "private"
    private.mkdir(mode=0o700)
    cv_path = private / "CV.pdf"
    cv_payload = b"%PDF-1.4\n% SYNTHETIC RESUME FIXTURE ONLY\n%%EOF\n"
    cv_path.write_bytes(cv_payload)
    cv_path.chmod(0o600)
    target = OneClickTarget("Example", "f" * 24, "744000000000001",
                            "12345678-1234-4234-8234-123456789abc", "REF-FIXTURE",
                            "https://jobs.smartrecruiters.com/Example/744000000000001-synthetic-role")
    posting = json.dumps(dict(active=True, id=target.posting_id, uuid=target.publication_id,
                              refNumber=target.requisition_id, company=dict(identifier=target.company))).encode()
    facts = dict(schema_version="EJS-VERIFIED-FACTS-1", candidate_profile_version="synthetic-profile-v1", facts=[])

    def field(key, canonical, value, *, kind="text", mode="Verified Fact", policy=None):
        source = "user_decision:" + key
        facts["facts"].append(dict(canonical_field=canonical, value=value, source_ref=source,
                                   verified=True, auto_fill_approved=True, verified_at=NOW.isoformat()))
        out = dict(key=key, label="Synthetic " + key, selector="#" + key, kind=kind,
                   required=True, canonical_field=canonical, value=value, provenance_ref=source,
                   resolution_mode=mode)
        if policy:
            out["policy_ref"] = policy
        return out

    first = [field("first", "candidate.first_name", "Synthetic"),
             field("email", "candidate.email", "synthetic@example.invalid", kind="email")]
    last = [field("salary", "compensation.expected_annual_gross", "37000", kind="number",
                  mode="Approved Policy", policy="USER-IE-SALARY-20261106"),
            field("current", "compensation.current_salary", "0", kind="number"),
            field("notice", "employment.notice_period", "Immediate", kind="select"),
            field("eligibility", "work.eligibility", "I require sponsorship", kind="select"),
            field("gender", "demographic.gender", "Prefer not to say", kind="select",
                  mode="Approved Neutral Choice", policy="SUBMIT1-A6"),
            field("privacy", "consent.privacy_acknowledgement", True, kind="checkbox",
                  mode="Approved Policy", policy="USER-EXAMPLE-PRIVACY-20261106")]
    for key, value in (("work.authorization", False), ("work.requires_sponsorship", True)):
        facts["facts"].append(dict(canonical_field=key, value=value, source_ref="candidate_verified:work-rights",
                                   verified=True, auto_fill_approved=True, verified_at=NOW.isoformat()))
    manifest = dict(schema_version="EJS-SINGLE-APPLICATION-1", source_sha=SOURCE,
                    candidate_profile_version=facts["candidate_profile_version"], reviewed_at=NOW.isoformat(),
                    reviewer_ref="review:independent:synthetic", execution_id="fixture:execution:1",
                    opportunity_key="fixture:opportunity:1", requisition_id=target.requisition_id,
                    steps=[dict(url=target.entry_url, fingerprint="1" * 64, fields=first,
                                upload=dict(key="cv", selector="#resume", label="Resume", kind="file", required=True),
                                next=dict(key="next", selector="#next", label="Next")),
                           dict(url=target.screening_url, fingerprint="2" * 64, fields=last,
                                submit=dict(key="submit", selector="#submit", label="Submit"))],
                    cv=dict(sha256=hashlib.sha256(cv_payload).hexdigest(), size_bytes=len(cv_payload),
                            mime_type="application/pdf", approved_filename="CV.pdf", user_approved=True,
                            scope="one_cv_asset", source_ref="approved_document:synthetic-cv"),
                    vacancy=dict(reviewed_at=NOW.isoformat(), posting_sha256=hashlib.sha256(posting).hexdigest(),
                                 role_fit_verified=True, mandatory_requirements_verified=True,
                                 sponsorship_supported=None, explicit_no_sponsorship=False,
                                 existing_work_rights_required=False, allow_unknown_sponsorship=True,
                                 sponsorship_decision_ref="USER-FIRST-SCOPE-20261105"),
                    read_urls=[], confirmation=dict(selector="#confirmation", text="Application submitted"))
    mp, fp = private / "manifest.json", private / "facts.json"
    for path, value in ((mp, manifest), (fp, facts)):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    manifest, facts = load_reviewed_application_inputs(mp, fp,
        manifest_sha256=hashlib.sha256(mp.read_bytes()).hexdigest(),
        facts_sha256=hashlib.sha256(fp.read_bytes()).hexdigest(), source_sha=SOURCE, now=NOW)
    ledger = SqliteSubmissionLedger(private / "first.sqlite3")
    clock = FixedClock()
    browser = RecordingBrowser(manifest, target, ledger, clock)
    def forbidden_network(*args, **kwargs):
        raise AssertionError("No public or employer network in runtime integration tests")
    monkeypatch.setattr("ejs.services.single_application_runtime.fetch_posting", forbidden_network)
    return Scenario(manifest, facts, target, cv_path, cv_payload, posting, ledger, browser, clock)


def test_reviewed_two_step_flow_records_before_one_submit_and_confirms(scenario):
    result = scenario.run()
    assert result["status"] == "EMPLOYER_CONFIRMED" and result["applied"] is True
    assert result["submit_click_attempts"] == 1
    assert result["applications_write_performed"] is False
    assert scenario.ledger.snapshot()["status"] == "Confirmed"
    counts = Counter(call[0] for call in scenario.browser.calls)
    assert counts["open"] == counts["preferred_entry"] == counts["upload"] == counts["advance"] == counts["submit"] == 1
    assert counts["write"] == 8
    assert scenario.browser.calls.index(("preferred_entry",)) < next(
        index for index, call in enumerate(scenario.browser.calls) if call[0] == "write")
    assert scenario.browser.values["privacy"] is True and scenario.browser.values["salary"] == "37000"


@pytest.mark.parametrize("authority", [None, SubmitAuthority(), replace(ON, kill_switch_engaged=True),
                                         replace(ON, final_submit_runtime_flag=False)])
def test_disabled_runtime_has_no_browser_effect_or_intent(scenario, authority):
    assert scenario.run(authority=authority)["status"] == "RUNTIME_DISABLED"
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


@pytest.mark.parametrize("mutation", ["changed", "not_pdf", "permissions", "missing", "hardlink"])
def test_unapproved_cv_bytes_or_storage_stop_before_browser(scenario, mutation):
    if mutation == "changed": scenario.cv_path.write_bytes(scenario.cv_payload + b"CHANGED")
    if mutation == "not_pdf":
        scenario.cv_path.write_bytes(b"not a PDF")
        scenario.manifest["cv"]["sha256"] = hashlib.sha256(b"not a PDF").hexdigest()
        scenario.manifest["cv"]["size_bytes"] = len(b"not a PDF")
    if mutation == "permissions": scenario.cv_path.chmod(0o640)
    if mutation == "missing": scenario.cv_path.unlink()
    if mutation == "hardlink": scenario.cv_path.with_name("linked.pdf").hardlink_to(scenario.cv_path)
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


@pytest.mark.parametrize("where", ["entry", "screening"])
def test_unreviewed_route_alias_never_opens_browser(scenario, where):
    index = 0 if where == "entry" else -1
    scenario.manifest["steps"][index]["url"] += "&unreviewed=alias"
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


@pytest.mark.parametrize("flag,value", [("challenge", True), ("unexpected_write", True),
                                         ("unreviewed_prefill", True), ("fingerprint", "3" * 64),
                                         ("required_keys", {"first", "email", "cv", "new-question"})])
def test_unreviewed_initial_surface_cannot_write_or_upload(scenario, flag, value):
    scenario.browser.flags[flag] = value
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("write") == scenario.browser.count("upload") == scenario.browser.count("submit") == 0
    assert not scenario.ledger.has_intent()


def test_new_required_field_after_write_stops_before_resume_or_next(scenario):
    def react(browser, binding):
        browser.flags["required_keys"] = {"first", "email", "cv", "unreviewed-required"}
    scenario.browser.after_write = react
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("write") == 1
    assert scenario.browser.count("upload") == scenario.browser.count("advance") == scenario.browser.count("submit") == 0


def test_delayed_autosave_detection_prevents_continuation(scenario):
    scenario.browser.after_upload = lambda browser: browser.flags.update(unexpected_write=True)
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("upload") == 1
    assert scenario.browser.count("advance") == scenario.browser.count("submit") == 0
    assert not scenario.ledger.has_intent()


def test_native_step_navigation_reinspects_actual_next_screen(scenario):
    scenario.browser.after_advance = lambda browser: browser.flags.update(fingerprint="wrong-live-fingerprint")
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("upload") == scenario.browser.count("advance") == 1
    assert scenario.browser.count("submit") == 0 and not scenario.ledger.has_intent()


def test_readback_mismatch_is_not_accepted_as_filled(scenario):
    scenario.browser.read_override = lambda browser, binding: (binding["key"] == "email", "DIFFERENT PRIVATE VALUE")
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("upload") == scenario.browser.count("submit") == 0
    assert not scenario.ledger.has_intent()


@pytest.mark.parametrize("change", [{"payload_sha256": "a" * 64}, {"payload_size": 1},
    {"filename": "Other.pdf"}, {"attachment_ref_sha256": "not-a-hash"},
    {"acknowledged_endpoint": "https://attacker.invalid/upload"}, {"ui_attachment_visible": False}])
def test_cv_requires_exact_bytes_and_matching_visible_attachment_ack(scenario, change):
    scenario.browser.upload_override = lambda observation: replace(observation, **change)
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("upload") == 1 and scenario.browser.count("advance") == 0
    assert scenario.browser.count("submit") == 0 and not scenario.ledger.has_intent()


@pytest.mark.parametrize("change", [{"endpoint": "https://attacker.invalid/application"},
    {"status": 202}, {"success_acknowledged": False}, {"confirmation_visible": False},
    {"challenge_detected": True}, {"acknowledgement_sha256": "not-a-hash"},
    {"observed_at": NOW - timedelta(seconds=1)}, {"observed_at": NOW + timedelta(seconds=1)}])
def test_wrong_or_ambiguous_confirmation_consumes_slot_without_applied(scenario, change):
    scenario.browser.confirmation_changes = change
    result = scenario.run()
    assert result["status"] == "SUBMITTED_UNCONFIRMED" and result["applied"] is False
    assert scenario.browser.count("submit") == 1
    assert scenario.ledger.snapshot()["status"] == "Submitted / Unconfirmed"
    previous = list(scenario.browser.calls)
    assert scenario.run()["status"] == "DUPLICATE_OR_UNCERTAIN"
    assert scenario.browser.calls == previous


def test_timeout_after_dispatch_is_never_retried_after_restart(scenario):
    scenario.browser.submit_error = TimeoutError("SYNTHETIC SENSITIVE FAILURE")
    result = scenario.run()
    assert result["status"] == "SUBMITTED_UNCONFIRMED" and result["submit_click_attempts"] is None
    assert result["dispatch_intent_recorded"] is True
    reopened = SqliteSubmissionLedger(scenario.ledger.path)
    replacement = RecordingBrowser(scenario.manifest, scenario.target, reopened, FixedClock())
    result = SingleApplicationRuntime(replacement, reopened, clock=FixedClock()).run(
        scenario.manifest, scenario.facts, scenario.target, scenario.cv_path,
        authority=ON, posting_payload=scenario.posting)
    assert result["status"] == "DUPLICATE_OR_UNCERTAIN" and result["applied"] is False
    assert replacement.calls == []


def test_confirmed_application_cannot_prepare_or_upload_again_with_new_execution_profile(scenario):
    assert scenario.run()["status"] == "EMPLOYER_CONFIRMED"
    scenario.manifest["execution_id"] = "fixture:execution:new"
    scenario.manifest["candidate_profile_version"] = "synthetic-profile-v2"
    reopened = SqliteSubmissionLedger(scenario.ledger.path)
    replacement = RecordingBrowser(scenario.manifest, scenario.target, reopened, FixedClock())
    result = SingleApplicationRuntime(replacement, reopened, clock=FixedClock()).run(
        scenario.manifest, scenario.facts, scenario.target, scenario.cv_path,
        authority=ON, posting_payload=scenario.posting)
    assert result["status"] == "DUPLICATE_OR_UNCERTAIN"
    assert replacement.calls == []


@pytest.mark.parametrize("key", ["privacy", "salary"])
def test_real_submit_policy_refuses_missing_privacy_or_salary_policy(scenario, key):
    field = next(field for field in scenario.manifest["steps"][-1]["fields"] if field["key"] == key)
    field.pop("policy_ref")
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("submit") == 0 and not scenario.ledger.has_intent()


@pytest.mark.parametrize("key", ["privacy", "salary"])
def test_real_submit_policy_refuses_relabelled_privacy_or_salary_verified_fact(scenario, key):
    field = next(field for field in scenario.manifest["steps"][-1]["fields"] if field["key"] == key)
    field["resolution_mode"] = "Verified Fact"
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("submit") == 0 and not scenario.ledger.has_intent()


def test_numeric_control_drifting_to_equal_boolean_only_at_last_read_cannot_submit(scenario):
    field = next(field for field in scenario.manifest["steps"][-1]["fields"] if field["key"] == "salary")
    field["value"] = 1
    fact = next(fact for fact in scenario.facts["facts"] if fact["canonical_field"] == field["canonical_field"])
    fact["value"] = 1
    scenario.browser.read_override = lambda browser, binding: (
        binding["key"] == "salary" and browser.clock.calls >= 2, True)
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.count("submit") == 0 and not scenario.ledger.has_intent()


@pytest.mark.parametrize("mutation", ["inactive", "posting", "publication", "requisition", "company", "hash"])
def test_fresh_exact_public_posting_required_before_any_browser_effect(scenario, mutation):
    data = json.loads(scenario.posting)
    if mutation == "inactive": data["active"] = False
    if mutation == "posting": data["id"] = "744000000000999"
    if mutation == "publication": data["uuid"] = "12345678-1234-4234-8234-123456789999"
    if mutation == "requisition": data["refNumber"] = "REF-OTHER"
    if mutation == "company": data["company"]["identifier"] = "Other"
    payload = json.dumps(data).encode()
    scenario.manifest["vacancy"]["posting_sha256"] = hashlib.sha256(payload).hexdigest()
    if mutation == "hash": payload += b" "
    assert scenario.run(posting_payload=payload)["status"] == "BLOCKED"
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


@pytest.mark.parametrize("mutation", ["mandatory", "no_sponsorship", "rights_required", "unknown_not_allowed", "stale"])
def test_unsuitable_or_unreviewed_vacancy_stops_before_upload(scenario, mutation):
    review = scenario.manifest["vacancy"]
    if mutation == "mandatory": review["mandatory_requirements_verified"] = False
    if mutation == "no_sponsorship": review["explicit_no_sponsorship"] = True
    if mutation == "rights_required": review["existing_work_rights_required"] = True
    if mutation == "unknown_not_allowed": review["allow_unknown_sponsorship"] = False
    if mutation == "stale": review["reviewed_at"] = (NOW - timedelta(days=2)).isoformat()
    assert scenario.run()["status"] == "BLOCKED"
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


def test_competing_runtimes_share_one_atomic_slot_and_one_submit(scenario):
    second_ledger = SqliteSubmissionLedger(scenario.ledger.path)
    second_clock = FixedClock()
    second = RecordingBrowser(scenario.manifest, scenario.target, second_ledger, second_clock)
    barrier = Barrier(2)
    scenario.browser.entry_barrier = second.entry_barrier = barrier
    def compete(browser, ledger, clock):
        return SingleApplicationRuntime(browser, ledger, clock=clock).run(
            scenario.manifest, scenario.facts, scenario.target, scenario.cv_path,
            authority=ON, posting_payload=scenario.posting)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(compete, scenario.browser, scenario.ledger, scenario.clock)
        other = pool.submit(compete, second, second_ledger, second_clock)
        results = [first.result(timeout=20), other.result(timeout=20)]
    assert sum(browser.count("submit") for browser in (scenario.browser, second)) == 1
    assert sum(result["applied"] for result in results) == 1
    assert scenario.ledger.snapshot()["status"] == "Confirmed"


def test_private_values_cv_name_and_exception_text_are_not_exposed(scenario, capsys):
    scenario.browser.submit_error = RuntimeError("PRIVATE SENSITIVE EXCEPTION")
    result = scenario.run()
    public = json.dumps(result)
    for private in ("Synthetic", "synthetic@example.invalid", "CV.pdf", "37000", "PRIVATE SENSITIVE"):
        assert private not in public and private.encode() not in scenario.ledger.path.read_bytes()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_reviewed_profile_phase_precedes_regular_fields_and_resume_upload(scenario):
    from tests.test_single_application_inputs import correction_plan
    correction_plan(scenario.manifest, scenario.facts)
    assert scenario.run()['status'] == 'EMPLOYER_CONFIRMED'
    actions = [call[0] for call in scenario.browser.calls]
    assert actions.count('profile_correction') == 1
    assert actions.index('profile_correction') < actions.index('write') < actions.index('upload')


def test_unverified_profile_edit_value_stops_before_any_browser_action(scenario):
    from tests.test_single_application_inputs import correction_plan
    phase = correction_plan(scenario.manifest, scenario.facts)
    phase['edits'][0]['editor']['fields'][0]['value'] = 'Unverified imported value'
    assert scenario.run()['status'] == 'BLOCKED'
    assert scenario.browser.calls == [] and not scenario.ledger.has_intent()


def test_profile_failure_never_continues_to_fields_cv_or_intent(scenario):
    from tests.test_single_application_inputs import correction_plan
    correction_plan(scenario.manifest, scenario.facts)
    scenario.browser.profile_error = PermissionError('APPLICATION_PROFILE_EDITOR_CHANGED')
    assert scenario.run()['status'] == 'BLOCKED'
    assert scenario.browser.count('profile_correction') == 1
    assert scenario.browser.count('write') == scenario.browser.count('upload') == scenario.browser.count('submit') == 0
    assert not scenario.ledger.has_intent()


def test_occupied_first_application_budget_blocks_profile_correction_too(scenario):
    assert scenario.run()['status'] == 'EMPLOYER_CONFIRMED'
    before = list(scenario.browser.calls)
    from tests.test_single_application_inputs import correction_plan
    correction_plan(scenario.manifest, scenario.facts)
    assert scenario.run()['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert scenario.browser.calls == before and scenario.browser.count('profile_correction') == 0


def resume(scenario, observation_id="fixture-reviewed-return", authority=ON):
    return SingleApplicationRuntime(scenario.browser, scenario.ledger, clock=scenario.clock).resume_reviewed_return(
        scenario.manifest, scenario.facts, scenario.target, scenario.cv_path,
        observation_id=observation_id, authority=authority, posting_payload=scenario.posting)


def test_reviewed_return_continues_owned_page_without_navigation(scenario):
    result = resume(scenario)
    assert result['status'] == 'EMPLOYER_CONFIRMED'
    assert scenario.browser.count('open') == 0
    assert scenario.browser.count('accept_return') == scenario.browser.count('preferred_entry') == 1
    assert scenario.browser.count('upload') == scenario.browser.count('submit') == 1


@pytest.mark.parametrize('token', [None, '', ' ', 1, True, 'x' * 513])
def test_invalid_return_reference_cannot_fall_back_to_native_navigation(scenario, token):
    assert resume(scenario, token)['status'] == 'BLOCKED'
    assert not scenario.browser.calls and not scenario.ledger.has_intent()


def test_rejected_return_never_opens_or_mutates_another_page(scenario):
    assert resume(scenario, 'wrong-reference')['status'] == 'BLOCKED'
    assert scenario.browser.count('accept_return') == 1
    assert scenario.browser.count('open') == scenario.browser.count('write') == scenario.browser.count('upload') == 0
    assert not scenario.ledger.has_intent()


def test_return_does_not_enable_runtime_or_consume_review_when_authority_off(scenario):
    assert resume(scenario, authority=SubmitAuthority())['status'] == 'RUNTIME_DISABLED'
    assert not scenario.browser.calls and not scenario.ledger.has_intent()


def test_completed_single_scope_stops_before_consuming_return_capability(scenario):
    scenario.run()
    before = len(scenario.browser.calls)
    assert resume(scenario)['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert len(scenario.browser.calls) == before


def test_handoff_is_explicit_and_never_falls_back_or_consumes_intent(scenario):
    def handoff():
        raise PermissionError('APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED')
    scenario.browser.preferred_entry = handoff
    result = scenario.run()
    assert result['status'] == 'HUMAN_HANDOFF_REQUIRED'
    assert result['fresh_return_review_required'] is True and result['native_fallback_allowed'] is False
    assert scenario.browser.count('open') == 1
    assert scenario.browser.count('write') == scenario.browser.count('upload') == scenario.browser.count('submit') == 0
    assert not scenario.ledger.has_intent()


@pytest.mark.parametrize("drift", ["requisition", "entry_url", "posting_hash", "review_expired"])
def test_reviewed_return_rejects_target_or_review_drift_before_employer_effect(scenario, drift):
    """A reviewed-return token is not authority to switch target or reuse stale review."""
    if drift == "requisition":
        scenario.manifest["requisition_id"] = "REF-OTHER"
    elif drift == "entry_url":
        scenario.manifest["steps"][0]["url"] = "https://jobs.smartrecruiters.com/Other/744000000000999"
    elif drift == "posting_hash":
        scenario.manifest["vacancy"]["posting_sha256"] = "0" * 64
    else:
        scenario.manifest["reviewed_at"] = (NOW - timedelta(days=2)).isoformat()
    result = resume(scenario)
    assert result["status"] == "BLOCKED"
    assert scenario.browser.count("open") == 0
    assert scenario.browser.count("accept_return") == 0
    assert scenario.browser.count("write") == scenario.browser.count("upload") == scenario.browser.count("submit") == 0
    assert not scenario.ledger.has_intent()
