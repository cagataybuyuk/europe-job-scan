"""Synthetic tests for EJS-EXECUTION-MANIFEST-1 (GD-006 step 2).

All values are synthetic placeholders; no candidate data is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from ejs.services.execution_manifest import (
    ERROR, FACT_KEYS, ExecutionManifest, form_fingerprint, load_execution_manifest,
)


NOW = datetime(2026, 11, 17, 5, 30, tzinfo=timezone.utc)
PREPARED = NOW - timedelta(minutes=45)
OBSERVED = PREPARED - timedelta(minutes=5)
URL = "https://jobs.smartrecruiters.com/ExampleCo/744000000000001-synthetic-analyst"


def iso(value: datetime) -> str:
    return value.isoformat()


def field(key, canonical, value, *, mode="Verified Fact", ref=None, control="text",
          required=True, options=None, label=None):
    if ref is None and mode == "Verified Fact":
        ref = f"user_fact:{FACT_KEYS.get(canonical, canonical)}"
    return dict(key=key, label=label or key.replace("_", " ").title(), control=control,
                required=required, options=options, canonical_field=canonical,
                answer=dict(mode=mode, value=value, source_ref=ref))


def base_document():
    fields = [
        field("first_name", "candidate.first_name", "Synthetic"),
        field("last_name", "candidate.last_name", "Applicant"),
        field("email", "candidate.email", "synthetic@example.invalid", control="email"),
        field("work_auth", "work.authorization", "No", control="select",
              options=["Yes", "No"]),
        field("sponsorship", "work.sponsorship_required", "Yes", control="radio",
              options=["Yes", "No"]),
        field("privacy", "consent.privacy_acknowledgement", True, mode="Approved Policy",
              ref="policy:SUBMIT-1.0:A4", control="checkbox"),
        field("marketing", "consent.marketing_opt_in", False, mode="Default No",
              control="checkbox", required=False, ref=None),
        field("gender", "demographic.gender", None, mode="Blank Optional", control="select",
              options=["Female", "Male", "Prefer not to say"], required=False, ref=None),
        field("motivation", "application.question.d365_experience",
              "Synthetic grounded answer.", mode="Evidence Text",
              ref="evidence:cv:base-erp:experience", control="textarea"),
    ]
    upload = dict(key="resume", label="Resume", required=True)
    return dict(
        schema_version="EJS-EXECUTION-MANIFEST-1",
        execution_id="exec-20261117-example-1",
        prepared_by_run="20261117-0741",
        prepared_at=iso(PREPARED),
        expires_at=iso(PREPARED + timedelta(hours=20)),
        candidate_profile_version="profile-2026-11-16",
        submit_key="submit:opp:example:sr:744000000000001:REF001:profile-2026-11-16",
        target=dict(ats_family="smartrecruiters", company="ExampleCo",
                    posting_id="744000000000001", requisition_id="REF001",
                    opportunity_key="opp:example:sr:744000000000001", apply_url=URL),
        policy=dict(version="SUBMIT-1.0", rollout_stage="R3", daily_cap=5),
        form=dict(observed_at=iso(OBSERVED), fingerprint=form_fingerprint(fields, upload),
                  fields=fields, cv_upload=upload),
        cv=dict(library_name="Base - ERP / D365", filename="Synthetic_CV.pdf",
                sha256="c" * 64, size_bytes=60470, mime_type="application/pdf"),
    )


def encode(document) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def refingerprint(document):
    form = document["form"]
    form["fingerprint"] = form_fingerprint(form["fields"], form["cv_upload"])
    return document


def rejected(document=None, raw=None, **kwargs):
    payload = raw if raw is not None else encode(document)
    with pytest.raises(PermissionError) as info:
        load_execution_manifest(payload, now=kwargs.pop("now", NOW), **kwargs)
    assert str(info.value) == ERROR
    return info


def test_valid_manifest_returns_frozen_view():
    raw = encode(base_document())
    result = load_execution_manifest(raw, now=NOW, expected_sha256=hashlib.sha256(raw).hexdigest())
    assert isinstance(result, ExecutionManifest)
    assert result.daily_cap == 5 and result.rollout_stage == "R3"
    assert result.cv_filename == "Synthetic_CV.pdf"
    assert [f.key for f in result.fields][0] == "first_name"
    assert form_fingerprint(result.fields, dict(key=result.cv_upload_key,
                                                label=result.cv_upload_label,
                                                required=True)) == result.form_fingerprint
    with pytest.raises(Exception):
        result.daily_cap = 9  # type: ignore[misc]


def test_errors_do_not_echo_values():
    doc = base_document()
    doc["target"]["company"] = "SECRET-VALUE " * 40
    info = rejected(doc)
    assert "SECRET" not in str(info.value)


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", "{\"a\": NaN}".encode(), b"\xff\xfe",
    b'{"schema_version": "EJS-EXECUTION-MANIFEST-1", "schema_version": "x"}',
    b" " * 600_000,
])
def test_malformed_bytes_reject(raw):
    rejected(raw=raw)


def test_expected_hash_mismatch_rejects():
    rejected(base_document(), expected_sha256="0" * 64)


@pytest.mark.parametrize("path", [
    (), ("target",), ("policy",), ("form",), ("cv",), ("form", "cv_upload"),
])
def test_unknown_keys_reject_at_every_level(path):
    doc = base_document()
    node = doc
    for part in path:
        node = node[part]
    node["unexpected_path"] = "/etc/passwd"
    rejected(doc)


def test_unknown_key_in_field_and_answer_rejects():
    doc = base_document()
    doc["form"]["fields"][0]["selector"] = "#x"
    rejected(doc)
    doc = base_document()
    doc["form"]["fields"][0]["answer"]["execute"] = True
    rejected(doc)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version="EJS-EXECUTION-MANIFEST-2"),
    lambda d: d.update(prepared_by_run="today"),
    lambda d: d.update(execution_id="../x"),
    lambda d: d.update(submit_key="submit:other"),
    lambda d: d["target"].update(ats_family="workday"),
    lambda d: d["target"].update(apply_url=URL.replace("https", "http")),
    lambda d: d["target"].update(apply_url=URL + "?utm=1"),
    lambda d: d["target"].update(apply_url=URL + "#frag"),
    lambda d: d["target"].update(apply_url=URL.replace("jobs.smartrecruiters.com", "evil.example")),
    lambda d: d["target"].update(apply_url=URL.replace("jobs.smartrecruiters.com", "user@jobs.smartrecruiters.com")),
    lambda d: d["target"].update(apply_url=URL.replace("jobs.smartrecruiters.com", "jobs.smartrecruiters.com:8443")),
    lambda d: d["policy"].update(version="SUBMIT-2.0"),
    lambda d: d["policy"].update(rollout_stage="R9"),
    lambda d: d["policy"].update(daily_cap=6),
    lambda d: d["policy"].update(daily_cap=True),
    lambda d: d["policy"].update(daily_cap=0),
    lambda d: d["policy"].update(rollout_stage="R2", daily_cap=5),
])
def test_identity_target_and_policy_rules(mutate):
    doc = base_document()
    mutate(doc)
    rejected(doc)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(prepared_at="2026-11-17T08:00:00+03:00"),  # not UTC
    lambda d: d.update(prepared_at="2026-11-17T05:00:00"),  # naive
    lambda d: d.update(prepared_at=iso(NOW + timedelta(minutes=5))),  # future
    lambda d: d.update(expires_at=iso(NOW - timedelta(seconds=1))),  # expired
    lambda d: d.update(expires_at=iso(PREPARED + timedelta(hours=25))),  # too long
    lambda d: d.update(expires_at=d["prepared_at"]),
    lambda d: d["form"].update(observed_at=iso(PREPARED + timedelta(minutes=1))),
    lambda d: d["form"].update(observed_at=iso(PREPARED - timedelta(hours=7))),
])
def test_time_rules(mutate):
    doc = base_document()
    mutate(doc)
    rejected(doc)


def test_expired_at_later_now_rejects():
    rejected(base_document(), now=PREPARED + timedelta(hours=21))


def test_fingerprint_mismatch_and_inventory_drift_reject():
    doc = base_document()
    doc["form"]["fingerprint"] = "0" * 64
    rejected(doc)
    doc = base_document()
    doc["form"]["fields"][0]["label"] = "Given name"  # changed structure, stale fingerprint
    rejected(doc)
    doc = base_document()
    doc["form"]["cv_upload"]["label"] = "Cover letter"
    rejected(doc)


def test_answer_change_does_not_change_fingerprint():
    doc = base_document()
    before = doc["form"]["fingerprint"]
    doc["form"]["fields"][0]["answer"]["value"] = "Other"
    assert form_fingerprint(doc["form"]["fields"], doc["form"]["cv_upload"]) == before
    load_execution_manifest(encode(doc), now=NOW)


@pytest.mark.parametrize("mutate", [
    lambda f: f.update(key="first_name"),  # duplicate key (applied to second field)
])
def test_duplicate_field_key_rejects(mutate):
    doc = base_document()
    mutate(doc["form"]["fields"][1])
    rejected(refingerprint(doc))


def test_duplicate_canonical_field_rejects():
    doc = base_document()
    doc["form"]["fields"][1]["canonical_field"] = "candidate.first_name"
    doc["form"]["fields"][1]["answer"]["source_ref"] = "user_fact:identity.first_name"
    rejected(refingerprint(doc))


@pytest.mark.parametrize("index,mutate", [
    (0, lambda f: f.update(canonical_field="candidate.favourite_colour")),
    (0, lambda f: f["answer"].update(source_ref="manual:typed")),
    (0, lambda f: f["answer"].update(source_ref=None)),
    (0, lambda f: f["answer"].update(mode="Guess")),
    (0, lambda f: f["answer"].update(value="")),
    (0, lambda f: f["answer"].update(value=" padded ")),
    (0, lambda f: f["answer"].update(value=None)),
    (0, lambda f: f.update(control="file")),
    (0, lambda f: f.update(options=["a"])),
    (0, lambda f: f.update(required="yes")),
    (3, lambda f: f["answer"].update(value="Maybe")),  # not an observed option
    (3, lambda f: f.update(options=[])),
    (3, lambda f: f.update(options=["Yes", "Yes"])),
    (3, lambda f: f["answer"].update(mode="Approved Policy", source_ref="policy:SUBMIT-1.0:A2")),  # A2
    (4, lambda f: f["answer"].update(mode="Evidence Text", source_ref="evidence:x")),
    (5, lambda f: f["answer"].update(source_ref="policy:SUBMIT-1.0:A5")),  # A4 ref
    (5, lambda f: f["answer"].update(value=False)),
    (5, lambda f: f["answer"].update(value="true")),
    (6, lambda f: f["answer"].update(value=True)),  # A5 opt-in
    (6, lambda f: f["answer"].update(mode="Approved Policy", source_ref="policy:SUBMIT-1.0:A4")),
    (7, lambda f: f["answer"].update(mode="Verified Fact", value="Male",
                                     source_ref="user_fact:demographic.gender")),  # A6
    (7, lambda f: f["answer"].update(mode="Approved Neutral Choice", value="Female",
                                     source_ref="policy:SUBMIT-1.0:A6")),
    (7, lambda f: f["answer"].update(value="Male")),  # Blank Optional with value
    (8, lambda f: f["answer"].update(source_ref="user_fact:cv.text")),  # A7 provenance
    (8, lambda f: f["answer"].update(value="x" * 5001)),
    (8, lambda f: f.update(canonical_field="application.question.Bad-Key")),
])
def test_field_and_answer_rules(index, mutate):
    doc = base_document()
    mutate(doc["form"]["fields"][index])
    rejected(refingerprint(doc))


@pytest.mark.parametrize("canonical,answer", [
    ("candidate.city", dict(mode="Approved Policy", value="X", source_ref="policy:SUBMIT-1.0:A1")),
    ("candidate.city", dict(mode="Approved Neutral Choice", value="Prefer not to say",
                            source_ref="policy:SUBMIT-1.0:A6")),
    ("compensation.current_salary", dict(mode="Approved Policy", value="1",
                                         source_ref="policy:SUBMIT-1.0:A3")),
    ("application.cover_letter", dict(mode="Verified Fact", value="Text",
                                      source_ref="user_fact:application.cover_letter")),
])
def test_modes_are_scoped_to_their_field_families(canonical, answer):
    doc = base_document()
    target = doc["form"]["fields"][0]
    target["canonical_field"] = canonical
    target["answer"] = answer
    rejected(refingerprint(doc))


def test_verified_fact_without_registry_binding_rejects():
    doc = base_document()
    doc["form"]["fields"].append(field("currency", "compensation.currency", "EUR"))
    rejected(refingerprint(doc))


def test_current_employment_facts_are_accepted():
    doc = base_document()
    doc["form"]["fields"].append(field("company", "employment.current_company", "ExampleEmployer"))
    doc["form"]["fields"].append(field("title", "employment.current_title", "Synthetic Title"))
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


def test_unparseable_timestamp_rejects():
    doc = base_document()
    doc["prepared_at"] = "2026-13-45T99:00:00+00:00"
    rejected(doc)


def test_required_field_cannot_be_blank():
    doc = base_document()
    target = doc["form"]["fields"][0]
    target["answer"] = dict(mode="Blank Optional", value=None, source_ref=None)
    rejected(refingerprint(doc))


def test_default_no_only_for_marketing():
    doc = base_document()
    doc["form"]["fields"][0]["answer"] = dict(mode="Default No", value="No", source_ref=None)
    rejected(refingerprint(doc))


def test_salary_requires_exact_policy():
    doc = base_document()
    doc["form"]["fields"].append(field("salary", "compensation.salary_expectation", "60000",
                                       mode="Verified Fact"))
    rejected(refingerprint(doc))
    doc = base_document()
    doc["form"]["fields"].append(field("salary", "compensation.salary_expectation", "60000",
                                       mode="Approved Policy", ref="policy:SUBMIT-1.0:A3"))
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


def test_neutral_demographic_choice_is_accepted():
    doc = base_document()
    doc["form"]["fields"][7]["answer"] = dict(mode="Approved Neutral Choice",
                                              value="Prefer not to say",
                                              source_ref="policy:SUBMIT-1.0:A6")
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(filename="../Synthetic_CV.pdf"),
    lambda c: c.update(filename="/Users/x/Synthetic_CV.pdf"),
    lambda c: c.update(filename="Synthetic_CV.docx"),
    lambda c: c.update(sha256="C" * 64),
    lambda c: c.update(sha256="c" * 63),
    lambda c: c.update(size_bytes=0),
    lambda c: c.update(size_bytes=11 * 1024 * 1024),
    lambda c: c.update(size_bytes=60470.0),
    lambda c: c.update(mime_type="application/msword"),
])
def test_cv_rules(mutate):
    doc = base_document()
    mutate(doc["cv"])
    rejected(doc)


def test_cv_upload_must_be_required_and_distinct():
    doc = base_document()
    doc["form"]["cv_upload"]["required"] = False
    rejected(refingerprint(doc))
    doc = base_document()
    doc["form"]["cv_upload"]["key"] = "first_name"
    rejected(refingerprint(doc))


def test_too_many_fields_rejects():
    doc = base_document()
    extra = [field(f"q_{i}", f"application.question.q_{i}", "Synthetic.", mode="Evidence Text",
                   ref="evidence:x", control="textarea") for i in range(80)]
    doc["form"]["fields"].extend(extra)
    rejected(refingerprint(doc))


def test_input_document_is_not_mutated():
    doc = base_document()
    original = deepcopy(doc)
    load_execution_manifest(encode(doc), now=NOW)
    assert doc == original


# ---- Findings from the independent review of PR #107 -------------------------

@pytest.mark.parametrize("label,canonical,control,options,value", [
    ("Are you legally authorised to work in the EU?", "application.question.right_to_work_eu",
     "text", None, "Yes"),
    ("Do you need a visa?", "application.question.need_visa", "textarea", None, "No"),
    ("Desired salary", "application.question.desired_salary", "text", None, "90000 EUR"),
    ("Gender", "application.question.gender", "text", None, "Male"),
    ("I consent to a background check", "application.question.background_check", "text", None, "Yes"),
    ("Tell us more", "application.question.right_to_work", "textarea", None, "Yes"),  # slug only
])
def test_evidence_text_cannot_answer_guarded_topics(label, canonical, control, options, value):
    doc = base_document()
    doc["form"]["fields"].append(field("guarded", canonical, value, mode="Evidence Text",
                                       ref="evidence:x", control=control, options=options,
                                       label=label))
    rejected(refingerprint(doc))


@pytest.mark.parametrize("control,options,value", [
    ("select", ["Yes", "No"], "Yes"), ("radio", ["Yes", "No"], "No"), ("checkbox", None, True),
])
def test_evidence_text_is_free_text_only(control, options, value):
    doc = base_document()
    doc["form"]["fields"].append(field("q", "application.question.availability", value,
                                       mode="Evidence Text", ref="evidence:x", control=control,
                                       options=options, label="Availability"))
    rejected(refingerprint(doc))


def test_guarded_label_must_use_matching_family():
    doc = base_document()
    target = doc["form"]["fields"][3]
    target["label"] = "Are you authorised to work in the Netherlands?"
    target["canonical_field"] = "candidate.country"
    target["answer"]["source_ref"] = "user_fact:location.current_country"
    rejected(refingerprint(doc))


def test_visa_question_answered_from_work_right_fact_is_accepted():
    doc = base_document()
    target = doc["form"]["fields"][3]
    target.update(label="What type of visa or passport do you have for working in the EU?",
                  control="textarea", options=None)
    target["answer"]["value"] = "Synthetic verified work-right statement."
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


def test_verified_fact_reference_must_match_field_binding():
    doc = base_document()
    doc["form"]["fields"][3]["answer"]["source_ref"] = "user_fact:identity.first_name"
    rejected(refingerprint(doc))


def test_alias_canonical_fields_cannot_both_appear():
    doc = base_document()
    doc["form"]["fields"].append(field("wa2", "work.work_authorization", "Yes", control="select",
                                       options=["Yes", "No"], label="Eligible?"))
    rejected(refingerprint(doc))


@pytest.mark.parametrize("mutate", [
    lambda d: d["policy"].update(rollout_stage=["R3"]),
    lambda d: d["form"]["fields"][0]["answer"].update(mode=["Verified Fact"]),
    lambda d: d["form"]["fields"][0].update(control={"a": 1}),
    lambda d: d["target"].update(ats_family=["smartrecruiters"]),
])
def test_wrong_types_raise_permission_error(mutate):
    doc = base_document()
    mutate(doc)
    rejected(refingerprint(doc))


def test_deep_nesting_raises_permission_error():
    rejected(raw=b"[" * 100_000 + b"]" * 100_000)


@pytest.mark.parametrize("url", [
    URL + "\n", " " + URL, URL + "\t",
    URL.replace("/ExampleCo", "/%2e%2e/OtherCo"),
    "https://jobs.smartrecruiters.com/OtherCo/999000000000001-other",
    "https://jobs.smartrecruiters.com/ExampleCo/x744000000000001-synthetic",
])
def test_apply_url_bound_to_posting_and_clean(url):
    doc = base_document()
    doc["target"]["apply_url"] = url
    rejected(doc)


def test_submit_key_segments_cannot_contain_colons():
    doc = base_document()
    doc["target"]["requisition_id"] = "REF001:profile"
    doc["submit_key"] = ("submit:opp:example:sr:744000000000001:REF001:profile:"
                         "profile-2026-11-16")
    rejected(doc)
    doc = base_document()
    doc["candidate_profile_version"] = "profile:2026"
    doc["submit_key"] = "submit:opp:example:sr:744000000000001:REF001:profile:2026"
    rejected(doc)


def test_default_no_policy_reference_must_be_a5():
    doc = base_document()
    doc["form"]["fields"][6]["answer"]["source_ref"] = "policy:SUBMIT-1.0:A4"
    rejected(refingerprint(doc))
    doc = base_document()
    doc["form"]["fields"][6]["answer"]["source_ref"] = "policy:SUBMIT-1.0:A5"
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


@pytest.mark.parametrize("run", ["19990101-9999", "20261116-0741", "20261117-1200", "20261117-0300"])
def test_run_id_bound_to_preparation_time(run):
    doc = base_document()
    doc["prepared_by_run"] = run
    rejected(doc)


@pytest.mark.parametrize("index,value", [(2, "not an email"), (2, "a@b")])
def test_email_format(index, value):
    doc = base_document()
    doc["form"]["fields"][index]["answer"]["value"] = value
    rejected(refingerprint(doc))


def test_tel_format():
    doc = base_document()
    doc["form"]["fields"].append(field("phone", "candidate.phone", "call me", control="tel",
                                       label="Phone"))
    rejected(refingerprint(doc))
    doc = base_document()
    doc["form"]["fields"].append(field("phone", "candidate.phone", "+90 500 000 00 00",
                                       control="tel", label="Phone"))
    load_execution_manifest(encode(refingerprint(doc)), now=NOW)


def test_checkbox_only_for_consent_and_work_families():
    doc = base_document()
    doc["form"]["fields"].append(field("cb", "candidate.city", True, control="checkbox",
                                       label="Agree"))
    rejected(refingerprint(doc))


@pytest.mark.parametrize("stamp,ok", [
    ("20261117T044000Z", False), ("2026-11-17T04:45Z", False),
    ("2026-11-17T04:45:00Z", True), ("2026-11-17T04:45:00.123456+00:00", True),
])
def test_timestamp_format(stamp, ok):
    doc = base_document()
    doc["prepared_at"] = stamp
    if ok:
        load_execution_manifest(encode(doc), now=NOW)
    else:
        rejected(doc)


def test_required_checkbox_left_unchecked_rejects():
    doc = base_document()
    doc["form"]["fields"][6]["required"] = True
    rejected(refingerprint(doc))
