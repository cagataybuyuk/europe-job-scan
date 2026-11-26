"""Synthetic-only W3.6 offline binding regression tests."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from ejs.services.execution_manifest import FACT_KEYS, load_execution_manifest
from ejs.services.trusted_fact_snapshot import ERROR, verify_fact_snapshot
from test_execution_manifest import NOW, PREPARED, base_document, encode, refingerprint


def snapshot_fixture():
    # Independent synthetic review fixture. Never construct real reviewed facts
    # by copying the candidate values out of a proposed manifest.
    doc = base_document()
    rows = [
        ("first_name", "candidate.first_name", "text", None, "Synthetic"),
        ("last_name", "candidate.last_name", "text", None, "Applicant"),
        ("email", "candidate.email", "email", None, "synthetic@example.invalid"),
        ("work_auth", "work.authorization", "select", ["Yes", "No"], "No"),
        ("sponsorship", "work.sponsorship_required", "radio", ["Yes", "No"], "Yes"),
    ]
    snap = {
        "schema_version": "EJS-TRUSTED-FACT-SNAPSHOT-1",
        "profile_version": "profile-2026-11-16",
        "cv_sha256": "c" * 64,
        "policy": "SUBMIT-1.0",
        "issued_at": PREPARED.isoformat(),
        "expires_at": (PREPARED + timedelta(hours=2)).isoformat(),
        "target": deepcopy(doc["target"]),
        "form_fingerprint": doc["form"]["fingerprint"],
        "facts": [
            dict(key=key, canonical_field=canonical, fact_key=FACT_KEYS[canonical],
                 step=0, control=control, options=options, value=value,
                 provenance="synthetic:independent-review-1")
            for key, canonical, control, options, value in rows
        ],
    }
    return doc, snap


def verify(doc, snap, *, pin=None, now=NOW):
    raw = encode(snap)
    verify_fact_snapshot(encode(doc), raw, now=now,
                         expected_snapshot_sha256=hashlib.sha256(raw).hexdigest()
                         if pin is None else pin)


def rejected(doc, snap, **kwargs):
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        verify(doc, snap, **kwargs)


def test_verified_independent_snapshot_matches():
    doc, snap = snapshot_fixture()
    assert verify(doc, snap) is None


def test_manifest_answer_tamper_preserves_fingerprint_but_fails_binding():
    doc, snap = snapshot_fixture()
    doc["form"]["fields"][3]["answer"]["value"] = "Yes"
    load_execution_manifest(encode(doc), now=NOW)
    rejected(doc, snap)


def test_exact_option_identity():
    doc, snap = snapshot_fixture()
    doc["form"]["fields"][3]["options"] = ["No", "Yes"]
    refingerprint(doc)
    # Even with refreshed structural binding, the approved option list differs.
    snap["form_fingerprint"] = doc["form"]["fingerprint"]
    rejected(doc, snap)


@pytest.mark.parametrize("key,value", [
    ("profile_version", "old-profile"),
    ("cv_sha256", "0" * 64),
    ("policy", "SUBMIT-0.9"),
    ("form_fingerprint", "0" * 64),
    ("schema_version", "unknown"),
    ("issued_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("expires_at", NOW.isoformat()),
    ("expires_at", (NOW + timedelta(days=2)).isoformat()),
])
def test_snapshot_context_and_freshness_fail_closed(key, value):
    doc, snap = snapshot_fixture()
    snap[key] = value
    rejected(doc, snap)


@pytest.mark.parametrize("key,value", [
    ("canonical_field", "candidate.last_name"),
    ("fact_key", "identity.last_name"),
    ("control", "textarea"),
    ("step", True),
    ("step", 1),
    ("provenance", ""),
    ("provenance", " "),
    ("value", "private-mismatch-sentinel"),
    ("options", []),
])
def test_binding_metadata_mismatch(key, value):
    doc, snap = snapshot_fixture()
    snap["facts"][0][key] = value
    rejected(doc, snap)


def test_missing_duplicate_or_extra_fact_reject():
    doc, snap = snapshot_fixture()
    missing = deepcopy(snap)
    missing["facts"].pop()
    rejected(doc, missing)
    duplicate = deepcopy(snap)
    duplicate["facts"][-1] = deepcopy(duplicate["facts"][0])
    rejected(doc, duplicate)
    extra = deepcopy(snap)
    extra["facts"].append(deepcopy(extra["facts"][0]))
    rejected(doc, extra)


def test_target_mismatch_reject():
    doc, snap = snapshot_fixture()
    snap["target"]["requisition_id"] = "OTHER"
    rejected(doc, snap)


def test_external_pin_mismatch_reject():
    doc, snap = snapshot_fixture()
    rejected(doc, snap, pin="0" * 64)
    rejected(doc, snap, pin="")


@pytest.mark.parametrize("raw", [
    b"", b"[]", b"{}", b"not-json", b"\xff",
    b'{"facts": [], "facts": []}', b'{"facts": NaN}', b" " * 500001,
])
def test_bad_snapshot_bytes_fail_closed(raw):
    doc, _ = snapshot_fixture()
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        verify_fact_snapshot(encode(doc), raw, now=NOW,
                             expected_snapshot_sha256=hashlib.sha256(raw).hexdigest())


def test_unknown_keys_reject():
    doc, snap = snapshot_fixture()
    snap["extra"] = True
    rejected(doc, snap)
    del snap["extra"]
    snap["facts"][0]["extra"] = True
    rejected(doc, snap)


def test_invalid_manifest_not_accepted():
    doc, snap = snapshot_fixture()
    doc["policy"]["version"] = "other"
    rejected(doc, snap)
