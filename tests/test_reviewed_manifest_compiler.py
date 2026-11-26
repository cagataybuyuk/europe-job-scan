"""Synthetic independent registry and manifest template checks."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sys
import traceback

import pytest

from ejs.services.execution_manifest import load_execution_manifest
from ejs.services.reviewed_manifest_compiler import ERROR, compile_reviewed_manifest
from tests.test_execution_manifest import NOW, OBSERVED, PREPARED, encode, field, refingerprint
from tests.test_trusted_fact_snapshot import snapshot_fixture


def inputs():
    doc, snap = snapshot_fixture()
    registry = dict(schema_version="EJS-VERIFIED-FACTS-1",
                    candidate_profile_version=snap["profile_version"], facts=[])
    for row in snap["facts"]:
        row["provenance"] = "candidate_verified:synthetic-" + row["key"]
        registry["facts"].append(dict(canonical_field=row["canonical_field"],
                                      value=row["value"], source_ref=row["provenance"],
                                      verified=True, auto_fill_approved=True,
                                      verified_at=PREPARED.isoformat()))
    template = deepcopy(doc)
    for field in template["form"]["fields"]:
        if field["answer"]["mode"] == "Verified Fact":
            field["answer"].update(value=None, source_ref=None)
    return doc, template, registry, snap


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def compile_bytes(template, registry, snap, *, template_pin=None, registry_pin=None,
                  snapshot_pin=None, now=NOW):
    return compile_reviewed_manifest(
        template, registry, snap, now=now,
        expected_template_sha256=digest(template) if template_pin is None else template_pin,
        expected_registry_sha256=digest(registry) if registry_pin is None else registry_pin,
        expected_snapshot_sha256=digest(snap) if snapshot_pin is None else snapshot_pin)


def compile_(template, registry, snap, **kwargs):
    return compile_bytes(encode(template), encode(registry), encode(snap), **kwargs)


def reject(template, registry, snap, **kwargs):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as exc:
        compile_(template, registry, snap, **kwargs)
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None


def test_compiles_exact_reviewed_answers_without_mutating_template():
    doc, template, registry, snap = inputs()
    before = deepcopy(template)
    compiled = compile_(template, registry, snap)
    assert json.loads(compiled) == doc
    assert template == before


def test_absent_template_pin_fails_closed():
    _, t, r, s = inputs()
    rb, sb = encode(r), encode(s)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        compile_reviewed_manifest(encode(t), rb, sb, now=NOW,
                                  expected_registry_sha256=digest(rb),
                                  expected_snapshot_sha256=digest(sb))


@pytest.mark.parametrize("kwargs", [
    {"template_pin": "0" * 64}, {"registry_pin": "0" * 64}, {"snapshot_pin": "0" * 64},
    {"template_pin": ""}, {"registry_pin": ""}, {"snapshot_pin": ""},
    {"template_pin": 64}, {"registry_pin": True}, {"snapshot_pin": b"0" * 64},
])
def test_independent_pin_required(kwargs):
    _, t, r, s = inputs()
    reject(t, r, s, **kwargs)


@pytest.mark.parametrize("index", range(3), ids=["template", "registry", "snapshot"])
def test_equivalent_json_with_different_bytes_rejects_original_pin(index):
    _, t, r, s = inputs()
    raw = [encode(t), encode(r), encode(s)]
    pins = dict(zip(("template_pin", "registry_pin", "snapshot_pin"), map(digest, raw)))
    raw[index] += b"\n"
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        compile_bytes(*raw, **pins)


@pytest.mark.parametrize("edit", [
    lambda t: t["form"]["fields"][-1]["answer"].update(value="Modified synthetic evidence."),
    lambda t: t["form"]["fields"][-1]["answer"].update(source_ref="evidence:synthetic-other"),
    lambda t: t["form"]["fields"][-1].update(canonical_field="application.cover_letter"),
    lambda t: t["policy"].update(rollout_stage="R4", daily_cap=None),
    lambda t: t["cv"].update(filename="Other_Synthetic.pdf", library_name="Other", size_bytes=1),
    lambda t: t.update({"pass": "observe"}),
    lambda t: t.update(execution_id="exec-alternate-synthetic"),
    lambda t: t.update(prepared_by_run="20261117-0800", prepared_at=NOW.isoformat()),
    lambda t: t.update(expires_at=(NOW + timedelta(hours=20)).isoformat()),
], ids=["evidence-value", "evidence-ref", "nonfact-canonical", "rollout", "cv-metadata",
        "pass", "execution-id", "run", "expiry"])
def test_pinned_template_rejects_schema_valid_changes_outside_the_snapshot(edit):
    _, t, r, s = inputs()
    approved_pin = digest(encode(t))
    edit(t)
    # The fact snapshot and registry still match. Only the original template pin detects this.
    compile_(t, r, s)
    reject(t, r, s, template_pin=approved_pin)


@pytest.mark.parametrize("key,value", [
    ("verified", False), ("verified", 1), ("auto_fill_approved", False),
    ("auto_fill_approved", "true"),
    ("verified_at", (NOW - timedelta(days=31)).isoformat()),
    ("verified_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("source_ref", "candidate_verified:other"),
    ("value", "Other synthetic value"),
])
def test_registry_approval_freshness_provenance_and_values(key, value):
    _, t, r, s = inputs()
    r["facts"][0][key] = value
    reject(t, r, s)


def test_no_implicit_option_conversion_even_if_registry_is_verified():
    _, t, r, s = inputs()
    r["facts"][3]["value"] = False
    reject(t, r, s)


def test_work_rights_requires_explicit_review_source():
    _, t, r, s = inputs()
    r["facts"][3]["source_ref"] = s["facts"][3]["provenance"] = "cv:synthetic"
    reject(t, r, s)


@pytest.mark.parametrize("key,value", [("value", "Synthetic"),
                                      ("source_ref", "user_fact:identity.first_name")])
def test_prepopulated_answer_is_not_silently_repaired(key, value):
    _, t, r, s = inputs()
    t["form"]["fields"][0]["answer"][key] = value
    reject(t, r, s)


@pytest.mark.parametrize("value,matches", [("different@example.invalid", False),
                                          ("synthetic@example.invalid", True)])
def test_shared_fact_key_requires_consistent_values(value, matches):
    _, t, r, s = inputs()
    original = s["facts"][2]
    canonical = "candidate.confirm_email"
    row = dict(original, key="confirm_email", canonical_field=canonical, value=value)
    r["facts"].append(dict(r["facts"][2], canonical_field=canonical, value=value))
    s["facts"].append(row)
    confirmation = field(row["key"], canonical, None, control="email", label="Confirm Email")
    confirmation["answer"]["source_ref"] = None
    t["form"]["fields"].append(confirmation)
    refingerprint(t)
    s["form_fingerprint"] = t["form"]["fingerprint"]
    if matches:
        result = load_execution_manifest(compile_(t, r, s), now=NOW)
        assert result.fields[-1].answer == result.fields[2].answer
    else:
        reject(t, r, s)


def test_missing_and_duplicate_registry_facts_reject():
    _, t, r, s = inputs()
    missing = deepcopy(r)
    missing["facts"].pop()
    reject(t, missing, s)
    r["facts"].append(deepcopy(r["facts"][0]))
    reject(t, r, s)


@pytest.mark.parametrize("mutate", [
    lambda s: s["facts"].pop(),
    lambda s: s["facts"].append(deepcopy(s["facts"][0])),
    lambda s: s["facts"][0].update(fact_key="identity.last_name"),
    lambda s: s["facts"][0].update(canonical_field="candidate.last_name"),
    lambda s: s["facts"][0].update(step=False),
    lambda s: s["facts"][3].update(value="no"),
    lambda s: s.update(issued_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda s: s.update(expires_at=NOW.isoformat()),
], ids=["missing", "duplicate", "fact-key", "canonical", "bool-step", "option-case",
        "future-issue", "expired"])
def test_invalid_snapshot_fails_with_all_pins_matching_bytes(mutate):
    _, t, r, s = inputs()
    mutate(s)
    reject(t, r, s)


def test_template_cannot_promote_alias_without_matching_registry_entry():
    _, t, r, s = inputs()
    canonical = "work.work_authorization"  # same fact key as work.authorization
    t["form"]["fields"][3]["canonical_field"] = canonical
    s["facts"][3]["canonical_field"] = canonical
    reject(t, r, s)


@pytest.mark.parametrize("registry_value,snapshot_value", [(True, True), (1, True), (1, 1)])
def test_matching_pins_do_not_allow_integer_checkbox_answers(registry_value, snapshot_value):
    _, t, r, s = inputs()
    t["form"]["fields"][3].update(control="checkbox", options=None)
    refingerprint(t)
    s["form_fingerprint"] = t["form"]["fingerprint"]
    s["facts"][3].update(control="checkbox", options=None, value=snapshot_value)
    r["facts"][3].update(value=registry_value)
    if registry_value is True:
        result = load_execution_manifest(compile_(t, r, s), now=NOW)
        assert result.fields[3].answer.value is True
    else:
        reject(t, r, s)


def test_manifest_only_field_cannot_be_added_to_the_registry_contract():
    _, t, r, s = inputs()
    canonical = "employment.current_title"
    t["form"]["fields"][0].update(canonical_field=canonical, label="Current Title")
    refingerprint(t)
    r["facts"][0].update(canonical_field=canonical)
    s["form_fingerprint"] = t["form"]["fingerprint"]
    s["facts"][0].update(canonical_field=canonical, fact_key=canonical)
    reject(t, r, s)


@pytest.mark.parametrize("kind", ["profile", "cv", "target", "options", "observed"])
def test_snapshot_context_mismatch_rejects(kind):
    _, t, r, s = inputs()
    if kind == "profile":
        r["candidate_profile_version"] = "other"
    elif kind == "cv":
        s["cv_sha256"] = "0" * 64
    elif kind == "target":
        s["target"]["requisition_id"] = "OTHER"
    elif kind == "options":
        s["facts"][3]["options"] = ["No", "Yes"]
    else:
        t["form"]["observed_at"] = (OBSERVED + timedelta(seconds=1)).isoformat()
    reject(t, r, s)


@pytest.mark.parametrize("raw", [b'{"synthetic-private-payload": 1,}',
                                 b'{"nested": {"key": 1, "key": 1}}',
                                 b'{"item": 1e999}', b"[]", b"null", b"\xff"])
@pytest.mark.parametrize("input_index", range(3), ids=["template", "registry", "snapshot"])
def test_bad_input_keeps_private_json_out_of_compiler_traceback(raw, input_index):
    _, t, r, s = inputs()
    payloads = [encode(t), encode(r), encode(s)]
    payloads[input_index] = raw
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as exc:
        compile_bytes(*payloads)
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None
    module = sys.modules[compile_reviewed_manifest.__module__]
    compiler_frames = [frame for frame, _ in traceback.walk_tb(exc.value.__traceback__)
                       if frame.f_globals is vars(module)]
    assert [frame.f_code.co_name for frame in compiler_frames] == ["compile_reviewed_manifest"]
    assert not compiler_frames[0].f_locals
