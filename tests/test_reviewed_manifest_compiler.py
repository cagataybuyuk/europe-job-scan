"""Synthetic independent registry and manifest template checks."""
from copy import deepcopy
from datetime import timedelta
import hashlib

import pytest

from ejs.services.execution_manifest import load_execution_manifest
from ejs.services.reviewed_manifest_compiler import ERROR, compile_reviewed_manifest
from tests.test_execution_manifest import NOW, PREPARED, encode
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


def compile_(template, registry, snap, *, registry_pin=None, snapshot_pin=None):
    rb, sb = encode(registry), encode(snap)
    return compile_reviewed_manifest(
        encode(template), rb, sb, now=NOW,
        expected_registry_sha256=hashlib.sha256(rb).hexdigest() if registry_pin is None else registry_pin,
        expected_snapshot_sha256=hashlib.sha256(sb).hexdigest() if snapshot_pin is None else snapshot_pin)


def reject(template, registry, snap, **kwargs):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as exc:
        compile_(template, registry, snap, **kwargs)
    assert exc.value.__context__ is None


def test_compiles_exact_reviewed_answers_without_mutating_template():
    doc, template, registry, snap = inputs()
    before = deepcopy(template)
    actual = load_execution_manifest(compile_(template, registry, snap), now=NOW)
    expected = load_execution_manifest(encode(doc), now=NOW)
    assert actual.fields == expected.fields
    assert actual.cv_sha256 == expected.cv_sha256
    assert template == before


@pytest.mark.parametrize("kwargs", [
    {"registry_pin": "0" * 64}, {"snapshot_pin": "0" * 64},
    {"registry_pin": ""}, {"snapshot_pin": ""},
])
def test_independent_pin_required(kwargs):
    _, t, r, s = inputs()
    reject(t, r, s, **kwargs)


@pytest.mark.parametrize("key,value", [
    ("verified", False), ("auto_fill_approved", False),
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


def test_prepopulated_answer_is_not_silently_repaired():
    _, t, r, s = inputs()
    t["form"]["fields"][0]["answer"]["value"] = "Synthetic"
    reject(t, r, s)


def test_missing_and_duplicate_registry_facts_reject():
    _, t, r, s = inputs()
    missing = deepcopy(r)
    missing["facts"].pop()
    reject(t, missing, s)
    r["facts"].append(deepcopy(r["facts"][0]))
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
        s["observed_at"] = PREPARED.isoformat()
    reject(t, r, s)


def test_duplicate_json_and_bad_types_reject_without_private_error_context():
    _, t, r, s = inputs()
    rb, sb = encode(r), encode(s)
    for raw in (b'{"form": {}, "form": {}}', b"[]", b"null", b"\xff"):
        with pytest.raises(PermissionError, match=f"^{ERROR}$") as exc:
            compile_reviewed_manifest(raw, rb, sb, now=NOW,
                                      expected_registry_sha256=hashlib.sha256(rb).hexdigest(),
                                      expected_snapshot_sha256=hashlib.sha256(sb).hexdigest())
        assert exc.value.__context__ is None
