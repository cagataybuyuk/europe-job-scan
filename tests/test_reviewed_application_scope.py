"""Different-target TEST pins must never become a new execution budget."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import json

import pytest

from ejs.services.reviewed_application_scope import (
    history_fingerprint, review_next_test_scope,
)


NOW = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
ERROR = "APPLICATION_NEXT_TEST_SCOPE_REVIEW_REQUIRED"


def raw(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case():
    legacy = dict(company="Version1", company_id="593fe2a3e4b0ac9d00fb37f8",
                  posting_id="744000159638089", publication_id="aaf6587c-8336-4454-adb1-e09661d9bdfd",
                  requisition_id="REF7089X",
                  posting_url="https://jobs.smartrecruiters.com/Version1/744000159638089--senior-business-analyst")
    target = dict(company="Example", company_id="a" * 24, posting_id="744000199999999",
                  publication_id="aaf6587c-8336-4454-adb1-e09661d9bdfe", requisition_id="REF-SYNTHETIC",
                  posting_url="https://jobs.smartrecruiters.com/Example/744000199999999-synthetic")
    manifest = dict(schema_version="EJS-SINGLE-APPLICATION-1", source_sha="a" * 40,
                    candidate_profile_version="synthetic:v2", reviewer_ref="review:synthetic",
                    reviewed_at=NOW.isoformat(), target=target,
                    entry_policy=dict(strategy="native_verified_facts", decision_ref="USER-NATIVE-MVP-20261112"),
                    cv=dict(sha256="c" * 64, user_approved=True))
    facts = dict(schema_version="EJS-VERIFIED-FACTS-1", candidate_profile_version="synthetic:v2")
    history = [dict(target=legacy, submission_state="Confirmed")]
    snapshot = dict(status="Confirmed", requisition_digest=sha(b"REF7089X"), evidence_digest="e" * 64)
    scope = dict(schema_version="EJS-REVIEWED-NEXT-TEST-SCOPE-1", scope_id="synthetic:test:scope",
                 decision_ref="USER-MVP-TEST-TARGET-20261112", mode="review_only",
                 source_sha="a" * 40, manifest_sha256=sha(raw(manifest)), facts_sha256=sha(raw(facts)),
                 history_sha256=history_fingerprint(history, snapshot), target=deepcopy(target),
                 candidate_profile_version="synthetic:v2", cv_sha256="c" * 64,
                 reviewer_ref="review:synthetic", reviewed_at=NOW.isoformat(),
                 expires_at=(NOW + timedelta(hours=1)).isoformat())
    return dict(scope=scope, manifest=manifest, facts=facts, history=history, snapshot=snapshot)


def call(case, *, repin=True, **overrides):
    manifest, facts = raw(case["manifest"]), raw(case["facts"])
    if repin:
        case["scope"]["manifest_sha256"] = sha(manifest)
        case["scope"]["facts_sha256"] = sha(facts)
    scope = raw(case["scope"])
    args = dict(scope_payload=scope, manifest_payload=manifest, facts_payload=facts,
                scope_sha256=sha(scope), manifest_sha256=sha(manifest), facts_sha256=sha(facts),
                source_sha="a" * 40, history=case["history"], legacy_snapshot=case["snapshot"], now=NOW)
    args.update(overrides)
    return review_next_test_scope(**args)


def blocked(fn):
    with pytest.raises(PermissionError) as caught:
        fn()
    assert str(caught.value) == ERROR


def test_different_pinned_test_target_has_no_actions_or_mutations(case):
    original = deepcopy(case)
    result = call(case)
    assert result.target.company == "Example"
    assert result.scope_id == "synthetic:test:scope"
    for name in ("browser_action_authorized", "form_write_authorized", "cv_upload_authorized",
                 "next_authorized", "final_submit_authorized", "ledger_create_authorized"):
        assert getattr(result, name) is False
    assert case == original
    with pytest.raises(FrozenInstanceError):
        result.final_submit_authorized = True
    with pytest.raises(ValueError):
        replace(result, final_submit_authorized=True)


@pytest.mark.parametrize("key,value", [
    ("final_submit", True), ("browser_action_authorized", True), ("ledger", "/synthetic/new.sqlite3"),
    ("ledger_create_authorized", False), ("approved", True), ("command", "submit"),
    ("mode", "execute"), ("mode", "prepare"), ("scope_id", "../new-budget"),
    ("schema_version", "EJS-REVIEWED-NEXT-SCOPE-1"), ("decision_ref", "USER-NATIVE-MVP-20261112"),
    ("source_sha", "b" * 40), ("candidate_profile_version", "synthetic:v3"),
    ("cv_sha256", "d" * 64), ("reviewer_ref", "review:other"),
])
def test_forged_permissions_paths_or_binding_changes_stop(case, key, value):
    case["scope"][key] = value
    blocked(lambda: call(case))


@pytest.mark.parametrize("missing", ["target", "reviewer_ref", "cv_sha256", "expires_at", "history_sha256"])
def test_missing_review_binding_stops(case, missing):
    del case["scope"][missing]
    blocked(lambda: call(case))


@pytest.mark.parametrize("which", ["scope", "manifest", "facts"])
def test_wrong_external_byte_pin_stops(case, which):
    blocked(lambda: call(case, **{which + "_sha256": "f" * 64}))


@pytest.mark.parametrize("mutation", ["profile", "reviewer", "source", "cv_approval", "native", "target"])
def test_repinning_input_bytes_cannot_change_reviewed_identity(case, mutation):
    m = case["manifest"]
    if mutation == "profile":
        case["facts"]["candidate_profile_version"] = "synthetic:other"
    elif mutation == "reviewer":
        m["reviewer_ref"] = "review:other"
    elif mutation == "source":
        m["source_sha"] = "b" * 40
    elif mutation == "cv_approval":
        m["cv"]["user_approved"] = 1
    elif mutation == "native":
        del m["entry_policy"]
    else:
        m["target"]["requisition_id"] = "REF-OTHER"
    blocked(lambda: call(case))


@pytest.mark.parametrize("field,stamp", [
    ("reviewed_at", "2026-11-12T15:00:00+00:00"),
    ("reviewed_at", "2026-11-12T13:59:59+00:00"),
    ("reviewed_at", "2026-11-12T17:00:00+03:00"),
    ("reviewed_at", "2026-11-12T14:00:00"),
    ("reviewed_at", None),
    ("expires_at", "2026-11-12T14:00:00+00:00"),
    ("expires_at", "2026-11-13T14:00:01+00:00"),
    ("expires_at", "2026-11-13T13:00:00+03:00"),
])
def test_review_time_must_follow_manifest_and_be_current_short_utc(case, field, stamp):
    case["scope"][field] = stamp
    blocked(lambda: call(case))


def test_stale_manifest_cannot_be_refreshed_only_in_scope(case):
    case["manifest"]["reviewed_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
    blocked(lambda: call(case))


class BadUTC(tzinfo):
    def utcoffset(self, dt):
        raise ValueError("SYNTHETIC PRIVATE TIME ERROR")


@pytest.mark.parametrize("now", [NOW.replace(tzinfo=None), NOW.replace(tzinfo=BadUTC()), True])
def test_invalid_now_is_sanitized(case, now):
    blocked(lambda: call(case, now=now))


@pytest.mark.parametrize("state", ["Validated", "Submitted / Unconfirmed", "Confirmed"])
def test_prior_target_consumed_in_any_state_blocks_changed_execution_or_profile(case, state):
    case["history"].append(dict(target=deepcopy(case["scope"]["target"]), submission_state=state))
    case["scope"]["history_sha256"] = history_fingerprint(case["history"], case["snapshot"])
    blocked(lambda: call(case))


@pytest.mark.parametrize("identity", ["posting", "publication", "requisition"])
def test_aliases_cannot_reopen_legacy_vacancy(case, identity):
    target = case["scope"]["target"]
    old = case["history"][0]["target"]
    if identity == "posting":
        target["posting_id"] = old["posting_id"]
        target["posting_url"] = "https://jobs.smartrecruiters.com/Example/" + old["posting_id"] + "-changed-slug"
    elif identity == "publication":
        target["publication_id"] = old["publication_id"]
    else:
        target["company_id"] = old["company_id"]
        target["requisition_id"] = "ref7089x"
    case["manifest"]["target"] = deepcopy(target)
    blocked(lambda: call(case))


@pytest.mark.parametrize("mutation", ["empty", "missing_legacy", "changed_snapshot", "uncertain_legacy",
                                      "duplicate_legacy", "unknown_state", "extra_key", "fingerprint"])
def test_unknown_or_changed_canonical_history_stops(case, mutation):
    if mutation == "empty":
        case["history"] = []
    elif mutation == "missing_legacy":
        case["history"][0]["target"]["requisition_id"] = "REF-MISSING"
    elif mutation == "changed_snapshot":
        case["snapshot"]["requisition_digest"] = "f" * 64
    elif mutation == "uncertain_legacy":
        case["snapshot"]["status"] = "Submitted / Unconfirmed"
    elif mutation == "duplicate_legacy":
        case["history"].append(deepcopy(case["history"][0]))
    elif mutation == "unknown_state":
        case["history"][0]["submission_state"] = "Reset"
    elif mutation == "extra_key":
        case["history"][0]["new_slot"] = True
    else:
        case["scope"]["history_sha256"] = "f" * 64
    if mutation not in {"empty", "fingerprint"}:
        case["scope"]["history_sha256"] = history_fingerprint(case["history"], case["snapshot"])
    blocked(lambda: call(case))


def test_unrepinned_history_or_input_drift_stops(case):
    case["snapshot"]["evidence_digest"] = "a" * 64
    blocked(lambda: call(case))


@pytest.mark.parametrize("payload", [b'{"scope_id":"one","scope_id":"two"}', b'{"x":NaN}', b'[]', b'not-json'])
def test_ambiguous_or_non_json_pinned_input_stops(case, payload):
    blocked(lambda: call(case, scope_payload=payload, scope_sha256=sha(payload)))


def test_sensitive_payload_errors_do_not_escape(case):
    payload = b'{"SYNTHETIC PRIVATE VALUE":broken}'
    blocked(lambda: call(case, facts_payload=payload, facts_sha256=sha(payload)))
