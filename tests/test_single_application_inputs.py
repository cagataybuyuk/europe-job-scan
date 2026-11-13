from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from ejs.services.single_application_inputs import (
    load_reviewed_application, load_reviewed_application_inputs,
)


NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
SOURCE = "a" * 40
ERROR = "APPLICATION_INPUT_REVIEW_REQUIRED"
URL = "https://jobs.smartrecruiters.com/oneclick-ui/company/Example/publication/fixture"


def inputs():
    fact = dict(canonical_field="candidate.first_name", value="Synthetic",
                source_ref="candidate_verified:fixture-first", verified=True,
                auto_fill_approved=True, verified_at=STAMP)
    field = dict(key="first", label="First name", selector="#first", kind="text",
                 required=True, canonical_field=fact["canonical_field"], value=fact["value"],
                 provenance_ref=fact["source_ref"], resolution_mode="Verified Fact")
    manifest = dict(schema_version="EJS-SINGLE-APPLICATION-1", source_sha=SOURCE,
                    reviewed_at=STAMP, reviewer_ref="review:fixture:independent",
                    candidate_profile_version="fixture-v1", steps=[dict(
                        url=URL, fingerprint="b" * 64, fields=[field],
                        upload=dict(key="resume", selector="#resume", label="Resume", kind="file", required=True),
                        submit=dict(selector="#submit", label="Submit"))],
                    read_urls=[], confirmation=dict(selector="#confirmation", text="Application submitted"), cv=dict(
                        sha256="c" * 64, size_bytes=1234, mime_type="application/pdf",
                        approved_filename="CV.pdf", user_approved=True,
                        scope="one_cv_asset", source_ref="approved_document:fixture-cv"))
    facts = dict(schema_version="EJS-VERIFIED-FACTS-1", candidate_profile_version="fixture-v1",
                 facts=[fact])
    return manifest, facts


def add_field(manifest, facts, canonical, value, *, kind="select", required=True,
              mode="Verified Fact", source="user_decision:fixture", policy=None):
    index = len(facts["facts"])
    facts["facts"].append(dict(canonical_field=canonical, value=value, source_ref=source,
                               verified=True, auto_fill_approved=True, verified_at=STAMP))
    field = dict(key=f"field-{index}", label=f"Synthetic field {index}", selector=f"#field-{index}",
                 kind=kind, required=required, canonical_field=canonical, value=value,
                 provenance_ref=source, resolution_mode=mode)
    if policy is not None:
        field["policy_ref"] = policy
    manifest["steps"][0]["fields"].append(field)
    return field


def write_private(path, content):
    path.write_bytes(content)
    path.chmod(0o600)
    return hashlib.sha256(content).hexdigest()


def pinned(tmp_path, manifest=None, facts=None):
    if manifest is None:
        manifest, facts = inputs()
    private = tmp_path / "private-inputs"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    mp, fp = private / "manifest.json", private / "facts.json"
    md = write_private(mp, json.dumps(manifest).encode())
    fd = write_private(fp, json.dumps(facts).encode())
    return mp, fp, dict(manifest_sha256=md, facts_sha256=fd, source_sha=SOURCE, now=NOW)


def load(tmp_path, manifest=None, facts=None):
    mp, fp, kw = pinned(tmp_path, manifest, facts)
    return load_reviewed_application(mp, fp, **kw)


def assert_blocked(fn):
    with pytest.raises(PermissionError) as caught:
        fn()
    assert str(caught.value) == ERROR


def test_pinned_manifest_is_returned_unchanged_and_facts_available_to_trusted_caller(tmp_path):
    manifest, facts = inputs()
    mp, fp, kw = pinned(tmp_path, manifest, facts)
    assert load_reviewed_application(mp, fp, **kw) == manifest
    assert load_reviewed_application_inputs(mp, fp, **kw) == (manifest, facts)
    assert "submit_authorized" not in load_reviewed_application(mp, fp, **kw)


def entry_return_review(manifest):
    return dict(observation_id="d" * 48, surface_fingerprint=manifest["steps"][0]["fingerprint"],
                reviewed_at=STAMP, reviewer_ref=manifest["reviewer_ref"], return_url=URL)


def test_fresh_return_review_is_part_of_the_external_manifest_pin(tmp_path):
    manifest, facts = inputs()
    manifest["entry_return_review"] = entry_return_review(manifest)
    mp, fp, kw = pinned(tmp_path, manifest, facts)
    assert load_reviewed_application(mp, fp, **kw) == manifest
    manifest["entry_return_review"]["observation_id"] = "e" * 48
    write_private(mp, json.dumps(manifest).encode())
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


@pytest.mark.parametrize("mutation", [
    "null", "bool", "empty", "token_missing", "token_bool", "token_short", "token_upper",
    "fingerprint", "return_url", "reviewer", "reviewer_missing", "extra_grant",
    "candidate_match", "stale", "future", "naive", "non_utc", "stamp_bool",
])
def test_return_review_is_exact_fresh_and_bound_to_first_step(tmp_path, mutation):
    manifest, facts = inputs()
    review = entry_return_review(manifest)
    manifest["entry_return_review"] = review
    if mutation == "null": manifest["entry_return_review"] = None
    if mutation == "bool": manifest["entry_return_review"] = True
    if mutation == "empty": review.clear()
    if mutation == "token_missing": review.pop("observation_id")
    if mutation == "token_bool": review["observation_id"] = True
    if mutation == "token_short": review["observation_id"] = "d" * 47
    if mutation == "token_upper": review["observation_id"] = "D" * 48
    if mutation == "fingerprint": review["surface_fingerprint"] = "e" * 64
    if mutation == "return_url": review["return_url"] = URL + "/screening"
    if mutation == "reviewer": review["reviewer_ref"] = "another:reviewer"
    if mutation == "reviewer_missing": review.pop("reviewer_ref")
    if mutation == "extra_grant": review["submit_authorized"] = True
    if mutation == "candidate_match": review["candidate_profile_match"] = True
    if mutation == "stale": review["reviewed_at"] = (NOW - timedelta(seconds=121)).isoformat()
    if mutation == "future": review["reviewed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    if mutation == "naive": review["reviewed_at"] = NOW.replace(tzinfo=None).isoformat()
    if mutation == "non_utc": review["reviewed_at"] = NOW.astimezone(timezone(timedelta(hours=3))).isoformat()
    if mutation == "stamp_bool": review["reviewed_at"] = True
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_return_review_accepts_exact_expiry_boundary_in_utc(tmp_path):
    manifest, facts = inputs()
    manifest["entry_return_review"] = entry_return_review(manifest)
    manifest["entry_return_review"]["reviewed_at"] = (NOW - timedelta(seconds=120)).isoformat()
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("which", ["manifest", "facts"])
def test_external_hash_rejects_postreview_tampering(tmp_path, which):
    mp, fp, kw = pinned(tmp_path)
    changed = mp if which == "manifest" else fp
    changed.write_bytes(changed.read_bytes() + b" ")
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


@pytest.mark.parametrize("key,value", [
    ("manifest_sha256", "g" * 64), ("facts_sha256", "A" * 64),
    ("source_sha", "b" * 40), ("source_sha", "a" * 39),
    ("now", NOW.replace(tzinfo=None)),
])
def test_independent_pins_and_aware_clock_required(tmp_path, key, value):
    mp, fp, kw = pinned(tmp_path)
    kw[key] = value
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


@pytest.mark.parametrize("subject", ["file", "parent", "symlink", "parent_symlink", "hardlink", "fifo"])
def test_private_regular_owned_unlinked_files_required(tmp_path, subject):
    mp, fp, kw = pinned(tmp_path)
    if subject == "file":
        mp.chmod(0o640)
    elif subject == "parent":
        mp.parent.chmod(0o750)
    elif subject == "symlink":
        target = mp.with_name("actual.json")
        mp.rename(target)
        mp.symlink_to(target)
    elif subject == "parent_symlink":
        linked = tmp_path / "linked-private"
        linked.symlink_to(mp.parent, target_is_directory=True)
        mp = linked / mp.name
    elif subject == "hardlink":
        os.link(mp, mp.with_name("second.json"))
    else:
        mp.unlink()
        os.mkfifo(mp, mode=0o600)
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


def test_wrong_owner_and_nonpath_input_fail_closed(tmp_path, monkeypatch):
    mp, fp, kw = pinned(tmp_path)
    assert_blocked(lambda: load_reviewed_application(str(mp), fp, **kw))
    actual_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


@pytest.mark.parametrize("raw", [b"", b"x" * 1_000_001, b"[]", b"{", b'{"x":NaN}',
                                   b'{"x":Infinity}', b'{"x":-Infinity}',
                                   b'{"x":1e9999}',
                                   b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}'])
def test_bounded_json_rejects_nonobjects_duplicates_and_nonfinite_constants(tmp_path, raw):
    mp, fp, kw = pinned(tmp_path)
    kw["manifest_sha256"] = write_private(mp, raw)
    assert_blocked(lambda: load_reviewed_application(mp, fp, **kw))


@pytest.mark.parametrize("mutation", [
    "old_schema", "facts_schema", "profile", "reviewer_email", "reviewer_url", "reviewer_blank",
    "stale_review", "future_review", "naive_review", "stale_fact", "future_fact", "unverified",
    "unapproved", "unknown_fact", "duplicate_fact", "missing_fact", "value", "source", "unknown_field",
    "whitespace_field", "duplicate_field", "duplicate_selector", "bad_required", "bad_kind", "bad_mode",
    "no_steps", "six_steps", "no_fields", "too_many_fields", "fingerprint", "http", "offsite", "auth_url",
    "fragment_url", "not_literal_verified", "blank_required",
])
def test_review_facts_and_step_bindings_fail_closed(tmp_path, mutation):
    manifest, facts = inputs()
    field, fact, step = manifest["steps"][0]["fields"][0], facts["facts"][0], manifest["steps"][0]
    if mutation == "old_schema": manifest["schema_version"] = "EJS-LIVE-PREPARE-1"
    if mutation == "facts_schema": facts["schema_version"] = "EJS-OTHER-1"
    if mutation == "profile": facts["candidate_profile_version"] = "different-profile"
    if mutation == "reviewer_email": manifest["reviewer_ref"] = "private@example.invalid"
    if mutation == "reviewer_url": manifest["reviewer_ref"] = "https://example.invalid/review"
    if mutation == "reviewer_blank": manifest["reviewer_ref"] = " "
    if mutation == "stale_review": manifest["reviewed_at"] = (NOW - timedelta(days=1, seconds=1)).isoformat()
    if mutation == "future_review": manifest["reviewed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    if mutation == "naive_review": manifest["reviewed_at"] = NOW.replace(tzinfo=None).isoformat()
    if mutation == "stale_fact": fact["verified_at"] = (NOW - timedelta(days=30, seconds=1)).isoformat()
    if mutation == "future_fact": fact["verified_at"] = (NOW + timedelta(seconds=1)).isoformat()
    if mutation == "unverified": fact["verified"] = False
    if mutation == "unapproved": fact["auto_fill_approved"] = False
    if mutation == "unknown_fact": fact["canonical_field"] = "technical.password"
    if mutation == "duplicate_fact": facts["facts"].append(deepcopy(fact))
    if mutation == "missing_fact": facts["facts"] = []
    if mutation == "value": field["value"] = "DIFFERENT_PRIVATE_VALUE"
    if mutation == "source": field["provenance_ref"] = "unreviewed:source"
    if mutation == "unknown_field": field["canonical_field"] = "work.authorization.hidden"
    if mutation == "whitespace_field": field["canonical_field"] = " candidate.first_name "
    if mutation == "duplicate_field": step["fields"].append(deepcopy(field))
    if mutation == "duplicate_selector":
        duplicate = deepcopy(field); duplicate["key"] = "second"; step["fields"].append(duplicate)
    if mutation == "bad_required": field["required"] = 1
    if mutation == "bad_kind": field["kind"] = "password"
    if mutation == "bad_mode": field["resolution_mode"] = "Grounded Generated"
    if mutation == "no_steps": manifest["steps"] = []
    if mutation == "six_steps": manifest["steps"] = [deepcopy(step) for _ in range(6)]
    if mutation == "no_fields": step["fields"] = []
    if mutation == "too_many_fields": step["fields"] = [deepcopy(field) for _ in range(101)]
    if mutation == "fingerprint": step["fingerprint"] = "f" * 24
    if mutation == "http": step["url"] = URL.replace("https:", "http:")
    if mutation == "offsite": step["url"] = URL.replace("jobs.smartrecruiters.com", "attacker.invalid")
    if mutation == "auth_url": step["url"] = URL.replace("https://", "https://user@")
    if mutation == "fragment_url": step["url"] = URL + "#other"
    if mutation == "not_literal_verified": fact["verified"] = 1
    if mutation == "blank_required": field["value"] = fact["value"] = " "
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_freshness_inclusive_boundaries_and_timezone_offsets(tmp_path):
    manifest, facts = inputs()
    manifest["reviewed_at"] = (NOW - timedelta(days=1)).isoformat()
    facts["facts"][0]["verified_at"] = (NOW - timedelta(days=30)).isoformat()
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("bad_source", ["approved_document:cv", "linkedin:prefill", "fact:untyped-source"])
@pytest.mark.parametrize("canonical", ["work.authorization", "work.requires_sponsorship",
                                         "compensation.current_salary", "employment.notice_period"])
def test_legal_financial_and_current_employment_never_inferred_from_cv(tmp_path, canonical, bad_source):
    manifest, facts = inputs()
    add_field(manifest, facts, canonical, "Synthetic option", source=bad_source)
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_values_compare_with_no_bool_number_or_string_coercion(tmp_path):
    manifest, facts = inputs()
    field = add_field(manifest, facts, "compensation.current_salary", 1, kind="number")
    field["value"] = True
    assert_blocked(lambda: load(tmp_path, manifest, facts))
    field["value"] = "1"
    assert_blocked(lambda: load(tmp_path, manifest, facts))
    field["value"] = 1.0
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "1e99999"])
def test_number_controls_reject_boolean_and_nonfinite_forms_even_in_matched_fact(tmp_path, value):
    manifest, facts = inputs()
    add_field(manifest, facts, "compensation.current_salary", value, kind="number")
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("canonical", ["compensation.salary_expectation", "compensation.expected_annual_gross",
                                        "compensation.expected_salary_range"])
def test_salary_requires_explicit_policy_even_with_matching_verified_fact(tmp_path, canonical):
    manifest, facts = inputs()
    field = add_field(manifest, facts, canonical, "Synthetic salary", mode="Approved Policy",
                      policy="USER-EXAMPLE-SALARY-20261106")
    assert load(tmp_path, manifest, facts) == manifest
    field["resolution_mode"] = "Verified Fact"
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("canonical", ["consent.privacy", "consent.privacy_acknowledgement"])
def test_privacy_acceptance_needs_specific_action_policy_and_exact_fact(tmp_path, canonical):
    manifest, facts = inputs()
    field = add_field(manifest, facts, canonical, True, kind="checkbox", mode="Approved Policy",
                      policy="USER-EXAMPLE-PRIVACY-20261106")
    assert load(tmp_path, manifest, facts) == manifest
    field["policy_ref"] = "any-future-consent"
    assert_blocked(lambda: load(tmp_path, manifest, facts))
    field["policy_ref"] = "USER-EXAMPLE-PRIVACY-20261106"
    field["resolution_mode"] = "Verified Fact"
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("mode", ["Default No", "Blank Optional"])
def test_optional_marketing_can_only_be_false_with_reviewed_default_or_blank(tmp_path, mode):
    manifest, facts = inputs()
    field = add_field(manifest, facts, "consent.marketing_opt_in", False, kind="checkbox",
                      required=False, mode=mode)
    assert load(tmp_path, manifest, facts) == manifest
    field["value"] = facts["facts"][-1]["value"] = True
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_mandatory_demographics_only_reviewed_neutral_not_inferred(tmp_path):
    manifest, facts = inputs()
    field = add_field(manifest, facts, "demographic.gender", "Prefer not to say",
                      mode="Approved Neutral Choice", policy="SUBMIT1-A6")
    assert load(tmp_path, manifest, facts) == manifest
    field["value"] = facts["facts"][-1]["value"] = "Synthetic protected answer"
    assert_blocked(lambda: load(tmp_path, manifest, facts))
    field["value"] = facts["facts"][-1]["value"] = "Prefer not to say"
    field["policy_ref"] = "unknown-policy"
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_optional_demographics_remain_blank_from_explicit_policy_fact(tmp_path):
    manifest, facts = inputs()
    field = add_field(manifest, facts, "demographic.religion", "", required=False,
                      mode="Blank Optional", policy="SUBMIT1-A6")
    assert load(tmp_path, manifest, facts) == manifest
    field["resolution_mode"] = "Approved Neutral Choice"
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("canonical,mode,value", [
    ("candidate.first_name", "Default No", False),
    ("work.eligibility", "Approved Policy", "Synthetic option"),
    ("candidate.first_name", "Approved Neutral Choice", "Prefer not to say"),
    ("candidate.first_name", "Blank Optional", "Not blank"),
])
def test_policy_modes_cannot_relabel_unrelated_values(tmp_path, canonical, mode, value):
    manifest, facts = inputs()
    manifest["steps"][0]["fields"] = []
    facts["facts"] = []
    add_field(manifest, facts, canonical, value, kind="checkbox" if type(value) is bool else "select",
              required=False, mode=mode, policy="SUBMIT1-A6")
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("key,value", [("sha256", "x" * 64), ("size_bytes", True), ("size_bytes", 1.0),
    ("size_bytes", 0), ("size_bytes", 10_000_001), ("mime_type", "text/plain"),
    ("user_approved", 1), ("scope", "all-cv-assets"), ("approved_filename", "../CV.pdf"),
    ("approved_filename", "CV.pdf.exe"), ("source_ref", "")])
def test_cv_approval_is_exact_bounded_one_asset_metadata(tmp_path, key, value):
    manifest, facts = inputs()
    manifest["cv"][key] = value
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_fixed_private_errors_emit_no_input_values_paths_or_sources(tmp_path, capsys):
    manifest, facts = inputs()
    manifest["steps"][0]["fields"][0]["value"] = "PRIVATE_SECRET_FIXTURE"
    assert_blocked(lambda: load(tmp_path, manifest, facts))
    out = capsys.readouterr()
    assert out.out == out.err == ""


def profile_assertion(manifest, facts, canonical="profile.experience.0.summary"):
    value = "Synthetic Company\nBusiness Analyst\n2022–2023\nVerified synthetic responsibilities."
    source = "approved_document:sha256:" + "d" * 64
    facts["facts"].append(dict(canonical_field=canonical, value=value, source_ref=source,
                               verified=True, auto_fill_approved=True, verified_at=STAMP))
    assertion = dict(selector="#experience-card-0", label="Verified experience card",
                     canonical_field=canonical, value=value, provenance_ref=source)
    manifest["steps"][0]["profile_assertions"] = [assertion]
    return assertion


@pytest.mark.parametrize("canonical", ["profile.experience.0.title", "profile.experience.99.company",
    "profile.experience.2.dates", "profile.experience.4.description", "profile.experience.5.summary",
    "profile.education.0.school", "profile.education.1.degree", "profile.education.2.dates",
    "profile.education.3.description", "profile.education.4.summary"])
def test_profile_assertions_compare_exact_visible_card_text_to_pinned_cv_fact(tmp_path, canonical):
    manifest, facts = inputs()
    profile_assertion(manifest, facts, canonical)
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("mutation", ["changed_text", "source", "missing_fact", "unverified_fact",
    "bad_type", "blank", "wrong_canonical", "duplicate", "selector_collision", "bad_label",
    "missing_selector", "too_long_selector", "too_many", "not_list", "fact_not_text"])
def test_linkedin_profile_text_never_promoted_without_exact_approved_source(tmp_path, mutation):
    manifest, facts = inputs()
    assertion = profile_assertion(manifest, facts)
    if mutation == "changed_text": assertion["value"] += "\nUNSUPPORTED CURRENT ROLE CLAIM"
    if mutation == "source": assertion["provenance_ref"] = "linkedin:prefill"
    if mutation == "missing_fact": facts["facts"].pop()
    if mutation == "unverified_fact": facts["facts"][-1]["verified"] = False
    if mutation == "bad_type": assertion["value"] = 123
    if mutation == "blank": assertion["value"] = facts["facts"][-1]["value"] = " "
    if mutation == "wrong_canonical": assertion["canonical_field"] = "candidate.first_name"
    if mutation == "duplicate": manifest["steps"][0]["profile_assertions"].append(deepcopy(assertion))
    if mutation == "selector_collision": assertion["selector"] = "#first"
    if mutation == "bad_label": assertion["label"] = ""
    if mutation == "missing_selector": assertion.pop("selector")
    if mutation == "too_long_selector": assertion["selector"] = "x" * 513
    if mutation == "too_many": manifest["steps"][0]["profile_assertions"] = [deepcopy(assertion)] * 51
    if mutation == "not_list": manifest["steps"][0]["profile_assertions"] = {"selector": "#card"}
    if mutation == "fact_not_text": facts["facts"][-1]["value"] = True
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("canonical", ["profile.experience.100.title", "profile.experience.-1.title",
    "profile.experience.0.authorization", "profile.education.0.company", "profile.education.1.work.authorization",
    "profile.education.1.degree ", "profile.education.1.degree.hidden"])
def test_profile_fact_namespace_is_bounded_and_cannot_hide_legal_fields(tmp_path, canonical):
    manifest, facts = inputs()
    profile_assertion(manifest, facts, canonical)
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_profile_fact_cannot_become_a_profile_edit_request(tmp_path):
    manifest, facts = inputs()
    assertion = profile_assertion(manifest, facts)
    field = manifest["steps"][0]["fields"][0]
    field.update(canonical_field=assertion["canonical_field"], value=assertion["value"],
                 provenance_ref=assertion["provenance_ref"])
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def correction_plan(manifest, facts, *, record_type="experience", omit_month=False):
    prefix = f"profile.{record_type}.0."
    values = ({"Title": ("title", "Verified Analyst"), "Company": ("company", "Synthetic Employer"),
        "Office location": ("office_location", "Istanbul"), "Description": ("description", "Verified responsibilities."),
        "From": ("start_month", "2025-01"), "To": ("end_month", "2026-04"),
        "I currently work here": ("currently_working", False)} if record_type == "experience" else
        {"Institution": ("school", "Verified Institute"), "Major": ("major", "Materials"), "Degree": ("degree", "Bachelor"),
        "School location": ("school_location", "Istanbul"), "Description": ("description", "2018–2023."),
        "From": ("start_month", "2018-09"), "To": ("end_month", "2023-06")})
    fields = []
    for index, (label, (suffix, value)) in enumerate(values.items()):
        kind = "month" if label in ("From", "To") else "checkbox" if type(value) is bool else "text"
        required = label not in ("Office location", "School location", "Description", "I currently work here")
        if omit_month and kind == "month": value, required = "", False
        source = "user_decision:current-status" if type(value) is bool else "approved_document:fixture-cv"
        fact = dict(canonical_field=prefix + suffix, value=value, source_ref=source,
            verified=True, auto_fill_approved=True, verified_at=STAMP)
        field = dict(key=f"profile-field-{index}", selector=f"#profile-field-{index}", label=label, kind=kind,
            required=required, canonical_field=prefix + suffix, value=value, provenance_ref=source)
        if omit_month and kind == "month":
            fact["omission_reason"] = "month_unknown"
            field["omission_policy_ref"] = "PROFILE-OMIT-UNKNOWN-MONTH"
        facts["facts"].append(fact); fields.append(field)
    summary = ("Verified Analyst\nSynthetic Employer\n2025-01 – 2026-04\nVerified responsibilities." if record_type == "experience" else
               "Verified Institute\nBachelor\n" + ("2018–2023." if omit_month else "2018-09 – 2023-06"))
    other_kind = "education" if record_type == "experience" else "experience"
    assertions = []
    for selector, canonical, value in (("#profile-target", prefix + "summary", summary),
            ("#profile-other", f"profile.{other_kind}.0.summary", "Verified untouched record")):
        fact = dict(canonical_field=canonical, value=value, source_ref="approved_document:fixture-cv",
                    verified=True, auto_fill_approved=True, verified_at=STAMP)
        facts["facts"].append(fact)
        assertions.append(dict(selector=selector, canonical_field=canonical, value=value, provenance_ref=fact["source_ref"]))
    phase = dict(container_selector="#profile", card_selector=".profile-card", cards=[
        dict(selector="#profile-target", record_type=record_type, record_index=0, observed_text="Imported unverified record"),
        dict(selector="#profile-other", record_type=other_kind, record_index=0, observed_text="Verified untouched record")],
        edits=[dict(card_selector="#profile-target", edit=dict(selector="button", label="Edit " + record_type + " Imported record"),
            editor=dict(selector="#profile-editor", fingerprint="e" * 64, fields=fields,
                        save=dict(selector="#profile-save", label="Save")))])
    manifest["steps"][0]["profile_assertions"] = assertions
    manifest["steps"][0]["reviewed_profile_corrections"] = phase
    return phase


@pytest.mark.parametrize("record_type,omit_month", [("experience", False), ("education", False), ("education", True)])
def test_profile_corrections_are_distinct_source_bound_complete_native_plans(tmp_path, record_type, omit_month):
    manifest, facts = inputs()
    correction_plan(manifest, facts, record_type=record_type, omit_month=omit_month)
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("mutation", ["value", "source", "missing", "extra_control", "foreign_record", "delete", "submit_save",
    "day_guess", "month_missing_policy", "month_required", "month_no_evidence", "inferred_current", "uncovered_card"])
def test_profile_correction_cannot_selfapprove_unknown_source_dates_records_or_actions(tmp_path, mutation):
    manifest, facts = inputs()
    phase = correction_plan(manifest, facts, omit_month=mutation.startswith("month_"))
    editor = phase["edits"][0]["editor"]
    first = editor["fields"][0]
    if mutation == "value": first["value"] = "LinkedIn guessed value"
    if mutation == "source": first["provenance_ref"] = "linkedin:prefill"
    if mutation == "missing": editor["fields"].pop()
    if mutation == "extra_control": editor["fields"].append(deepcopy(first))
    if mutation == "foreign_record": first["canonical_field"] = "profile.experience.1.title"
    if mutation == "delete": phase["edits"][0]["edit"]["label"] = "Delete experience"
    if mutation == "submit_save": editor["save"]["label"] = "Submit"
    month = next(field for field in editor["fields"] if field["label"] == "From")
    if mutation == "day_guess": month["kind"] = "text"
    if mutation == "month_missing_policy": month.pop("omission_policy_ref")
    if mutation == "month_required": month["required"] = True
    if mutation == "month_no_evidence":
        next(f for f in facts["facts"] if f["canonical_field"] == month["canonical_field"]).pop("omission_reason")
    if mutation == "inferred_current":
        current = next(field for field in editor["fields"] if field["kind"] == "checkbox")
        current["provenance_ref"] = "approved_document:old-cv"
        next(f for f in facts["facts"] if f["canonical_field"] == current["canonical_field"])["source_ref"] = current["provenance_ref"]
    if mutation == "uncovered_card": phase["cards"][1]["observed_text"] = "Unverified other LinkedIn record"
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("policy", [{"strategy": "native_verified_facts", "decision_ref": "USER-NATIVE-MVP-20261112"}])
def test_explicit_native_entry_policy_is_independently_pinned(tmp_path, policy):
    manifest, facts = inputs(); manifest["entry_policy"] = policy
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("policy", [None, True, {}, {"strategy": "native_verified_facts", "decision_ref": "old"},
    {"strategy": "native_verified_facts", "decision_ref": "USER-NATIVE-MVP-20261112", "oauth_allowed": True}])
def test_native_entry_cannot_be_inferred_or_expand_oauth_authority(tmp_path, policy):
    manifest, facts = inputs(); manifest["entry_policy"] = policy
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def spl_correction_plan(manifest, facts, *, record_type="experience", omit_month=False):
    phase = correction_plan(manifest, facts, record_type=record_type, omit_month=omit_month)
    for field in phase['edits'][0]['editor']['fields']:
        if field['kind'] == 'month':
            host_id = 'spl-' + field['key']
            field['selector'] = f'spl-date-field#{host_id} input[aria-label="{field["label"]}"]'
            field['month_picker'] = dict(type='spl-month-year-v1', field_selector=f'spl-date-field#{host_id}',
                picker_selector='spl-date-picker', calendar_selector='#calendar-' + field['key'],
                calendar_fingerprint='b' * 64, display_format='iso-month-start-transport-v1',
                clear=dict(selector='[role="button"][aria-label="Clear date"]', label='Clear date'))
        elif field['label'] in {'Title', 'Company', 'Office location'}:
            field['selector'] = 'input#' + field['key']
            field['text_autocomplete'] = dict(type='spl-profile-text-v1', host_selector='#spl-' + field['key'])
    return phase


@pytest.mark.parametrize('record_type,omit_month', [('experience', False), ('education', True)])
def test_profile_spl_month_precision_and_source_text_are_explicit_reviewed_bindings(tmp_path, record_type, omit_month):
    manifest, facts = inputs()
    spl_correction_plan(manifest, facts, record_type=record_type, omit_month=omit_month)
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize('mutation', ['type', 'format', 'hash', 'unknown_key', 'clear_delete', 'missing_selector',
    'day_fact', 'text_on_month', 'month_on_title', 'text_widget_type', 'missing_text_host'])
def test_profile_spl_binding_never_becomes_a_generic_day_picker_or_suggestion_writer(tmp_path, mutation):
    manifest, facts = inputs(); phase = spl_correction_plan(manifest, facts)
    fields = phase['edits'][0]['editor']['fields']
    month = next(field for field in fields if field['label'] == 'From')
    title = fields[0]
    if mutation == 'type': month['month_picker']['type'] = 'generic-date'
    if mutation == 'format': month['month_picker']['display_format'] = 'guess-MM/DD/YYYY'
    if mutation == 'hash': month['month_picker']['calendar_fingerprint'] = 'invalid'
    if mutation == 'unknown_key': month['month_picker']['allow_submit'] = True
    if mutation == 'clear_delete': month['month_picker']['clear']['label'] = 'Delete'
    if mutation == 'missing_selector': month['month_picker'].pop('picker_selector')
    if mutation == 'day_fact':
        month['value'] = '2025-01-01'
        next(f for f in facts['facts'] if f['canonical_field'] == month['canonical_field'])['value'] = month['value']
    if mutation == 'text_on_month': month['text_autocomplete'] = deepcopy(title['text_autocomplete'])
    if mutation == 'month_on_title': title['month_picker'] = deepcopy(month['month_picker'])
    if mutation == 'text_widget_type': title['text_autocomplete']['type'] = 'pick-a-company-suggestion'
    if mutation == 'missing_text_host': title['text_autocomplete'].pop('host_selector')
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def city_binding(manifest, facts):
    field = add_field(manifest, facts, 'candidate.city', 'Synthetic City', kind='text', required=False,
                      source='candidate_verified:fixture-city')
    field.update(label='City', selector='input#city', city_autocomplete=dict(type='spl-city-text-v1', host_selector='#city-host'))
    return field


def test_city_autocomplete_is_explicit_verified_candidate_city_only(tmp_path):
    manifest, facts = inputs(); city_binding(manifest, facts)
    assert load(tmp_path, manifest, facts) == manifest


def phone_country_binding(manifest, facts):
    field = add_field(manifest, facts, 'candidate.phone_country_code', '+90', required=False)
    source = 'candidate_verified:fixture-phone-country'
    facts['facts'].append(dict(canonical_field='candidate.phone_country', value='Turkey', source_ref=source,
                              verified=True, auto_fill_approved=True, verified_at=STAMP))
    field.update(label='Country code', selector='button#phone-country', phone_country_select=dict(
        type='spl-phone-country-v1', host_selector='spl-select#phone-country', country_iso2='TR',
        country_label='Turkey', option_label='Turkey +90', country_provenance_ref=source))
    return field


def test_phone_country_select_keeps_verified_dial_code_separate_from_reviewed_display(tmp_path):
    manifest, facts = inputs(); phone_country_binding(manifest, facts)
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize('mutation', ['canonical', 'label', 'kind', 'mode', 'display_as_fact', 'iso', 'country', 'provenance', 'missing_country', 'type', 'extra', 'host', 'second_step'])
def test_phone_country_select_requires_exact_source_bound_contract(tmp_path, mutation):
    manifest, facts = inputs(); field = phone_country_binding(manifest, facts); spec = field['phone_country_select']
    if mutation == 'canonical': field['canonical_field'] = 'candidate.phone'
    if mutation == 'label': field['label'] = 'Country'
    if mutation == 'kind': field['kind'] = 'text'
    if mutation == 'mode': field['resolution_mode'] = 'Blank Optional'
    if mutation == 'display_as_fact': field['value'] = facts['facts'][1]['value'] = 'Turkey +90'
    if mutation == 'iso': spec['country_iso2'] = 'tr'
    if mutation == 'country': facts['facts'][-1]['value'] = 'Portugal'
    if mutation == 'provenance': spec['country_provenance_ref'] = 'unreviewed:source'
    if mutation == 'missing_country': facts['facts'].pop()
    if mutation == 'type': spec['type'] = 'nearest-host-value'
    if mutation == 'extra': spec['guess_country'] = True
    if mutation == 'host': spec['host_selector'] = ''
    if mutation == 'second_step':
        manifest['steps'].append(deepcopy(manifest['steps'][0])); manifest['steps'][1]['url'] += '/screening'
        manifest['steps'][0]['fields'].remove(field)
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize('mutation', ['canonical', 'label', 'kind', 'mode', 'value', 'source', 'linkedin_fact', 'type', 'extra', 'host', 'second_step'])
def test_city_binding_cannot_reinterpret_other_fields_or_promote_unverified_suggestions(tmp_path, mutation):
    manifest, facts = inputs(); field = city_binding(manifest, facts)
    if mutation == 'canonical': field['canonical_field'] = 'candidate.first_name'
    if mutation == 'label': field['label'] = 'Office location'
    if mutation == 'kind': field['kind'] = 'select'
    if mutation == 'mode': field['resolution_mode'] = 'Blank Optional'
    if mutation == 'value': field['value'] = 'Guessed suggestion'
    if mutation == 'source': field['provenance_ref'] = 'linkedin:prefill'
    if mutation == 'linkedin_fact': field['provenance_ref'] = facts['facts'][-1]['source_ref'] = 'linkedin:prefill'
    if mutation == 'type': field['city_autocomplete']['type'] = 'any-combobox'
    if mutation == 'extra': field['city_autocomplete']['select_suggestions'] = True
    if mutation == 'host': field['city_autocomplete']['host_selector'] = ''
    if mutation == 'second_step':
        manifest['steps'][0]['fields'].remove(field)
        second = deepcopy(manifest['steps'][0]); second['url'] += '/screening'; second['fields'] = [field]
        second.pop('upload'); manifest['steps'][0].pop('submit')
        manifest['steps'][0]['next'] = dict(selector='#next', label='Next')
        manifest['steps'].append(second)
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("mutation", ["absent", "two_uploads", "missing_kind", "wrong_kind",
    "string_required", "missing_required", "empty_label", "duplicate_key", "selector_collision",
    "oversize_selector", "not_object"])
def test_only_one_reviewed_typed_upload_binding_is_accepted(tmp_path, mutation):
    manifest, facts = inputs()
    step = manifest["steps"][0]
    upload = step["upload"]
    if mutation == "absent": step.pop("upload")
    if mutation == "two_uploads":
        second = deepcopy(step)
        step.pop("submit"); step["next"] = dict(selector="#next", label="Next")
        second["fields"][0].update(key="confirm-first", selector="#confirm-first")
        second["upload"].update(key="resume-again", selector="#resume-again")
        manifest["steps"].append(second)
    if mutation == "missing_kind": upload.pop("kind")
    if mutation == "wrong_kind": upload["kind"] = "image"
    if mutation == "string_required": upload["required"] = "true"
    if mutation == "missing_required": upload.pop("required")
    if mutation == "empty_label": upload["label"] = ""
    if mutation == "duplicate_key": upload["key"] = "first"
    if mutation == "selector_collision": upload["selector"] = "#first"
    if mutation == "oversize_selector": upload["selector"] = "x" * 513
    if mutation == "not_object": step["upload"] = []
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def two_steps(manifest):
    first = manifest["steps"][0]
    second = deepcopy(first)
    second.pop("upload")
    second["url"] = URL + "/screening"
    second["fingerprint"] = "e" * 64
    second["fields"][0].update(key="confirm-first", selector="#confirm-first")
    first.pop("submit")
    first["next"] = dict(selector="#next", label="Next")
    manifest["steps"].append(second)
    return first, second


def test_reviewed_multistep_next_and_last_submit_bindings_are_accepted(tmp_path):
    manifest, facts = inputs()
    first, last = two_steps(manifest)
    first["next"]["label"] = "Continue"
    last["submit"]["label"] = "Send application"
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("mutation", ["early_submit", "last_next", "missing_next", "missing_submit",
    "unknown_next_label", "unknown_submit_label", "missing_next_selector", "submit_selector_collision",
    "button_not_object", "button_oversize_selector"])
def test_step_actions_cannot_smuggle_early_submit_or_unknown_button(tmp_path, mutation):
    manifest, facts = inputs()
    first, last = two_steps(manifest)
    if mutation == "early_submit": first["submit"] = deepcopy(last["submit"])
    if mutation == "last_next": last["next"] = deepcopy(first["next"])
    if mutation == "missing_next": first.pop("next")
    if mutation == "missing_submit": last.pop("submit")
    if mutation == "unknown_next_label": first["next"]["label"] = "Apply"
    if mutation == "unknown_submit_label": last["submit"]["label"] = "Allow"
    if mutation == "missing_next_selector": first["next"].pop("selector")
    if mutation == "submit_selector_collision": last["submit"]["selector"] = "#confirm-first"
    if mutation == "button_not_object": last["submit"] = "Submit"
    if mutation == "button_oversize_selector": last["submit"]["selector"] = "x" * 513
    assert_blocked(lambda: load(tmp_path, manifest, facts))


@pytest.mark.parametrize("value", [None, "https://cdn.example.invalid/app.js", ["http://cdn.example.invalid/app.js"],
    ["https://user@cdn.example.invalid/app.js"], ["https://cdn.example.invalid/app.js#fragment"],
    ["https://cdn.example.invalid/*"], ["https://cdn.example.invalid/app.js "] ,
    ["https://cdn.example.invalid:443/app.js"], ["https://cdn.example.invalid/app.js"] * 2,
    ["https://cdn.example.invalid/"], ["https://cdn.example.invalid/a\\b"],
    ["https://cdn.example.invalid/app.js"] * 1001])
def test_read_allowlist_is_bounded_exact_https_without_aliases(tmp_path, value):
    manifest, facts = inputs()
    manifest["read_urls"] = value
    assert_blocked(lambda: load(tmp_path, manifest, facts))


def test_reviewed_exact_cdn_read_urls_are_retained(tmp_path):
    manifest, facts = inputs()
    manifest["read_urls"] = ["https://cdn.example.invalid/app.a1b2.js", "https://cdn.example.invalid/chunk.css?v=1"]
    assert load(tmp_path, manifest, facts) == manifest


@pytest.mark.parametrize("value", [None, {}, {"selector": "", "text": "Application submitted"},
    {"selector": "#success", "text": ""}, {"selector": "x" * 513, "text": "Success"},
    {"selector": "#success", "text": "x" * 4097}, {"selector": "#success", "text": True}])
def test_confirmation_marker_requires_reviewed_bounded_selector_and_text(tmp_path, value):
    manifest, facts = inputs()
    manifest["confirmation"] = value
    assert_blocked(lambda: load(tmp_path, manifest, facts))
