"""No live operation grant can be obtained from metadata or a self-claimed flag."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from ejs.services.oneclick_target import OneClickTarget
from ejs.services.reviewed_execution_scope import (
    ERROR, EXPECTED_FUTURE_DECISION_REF, ReviewedExecutionScope,
    legacy_snapshot_fingerprint, require_issued_execution_scope, review_execution_scope,
)


NOW = datetime(2026, 11, 12, 14, tzinfo=timezone.utc)


def raw(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def execution_case(snapshot=None):
    if snapshot is None:
        snapshot = dict(status="Confirmed", submit_key_digest="a" * 64, execution_digest="b" * 64,
            requisition_digest=sha(b"REF7089X"), validation_digest="c" * 64,
            requested_at="2026-11-07T08:43:24+00:00", started_at="2026-11-07T08:43:25+00:00",
            confirmed_at="2026-11-07T08:44:46+00:00", evidence_digest="e" * 64)
    target = dict(company="Devoteam", company_id="5b8f87c1e4b07eb5549de6e8", posting_id="744000156831399",
        publication_id="3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e", requisition_id="REF00063",
        posting_url="https://jobs.smartrecruiters.com/Devoteam/744000156831399-digital-impulse-business-analyst-")
    manifest = dict(schema_version="EJS-SINGLE-APPLICATION-1", source_sha="a" * 40,
        execution_id="fixture:execution:second", candidate_profile_version="fixture:v2",
        reviewer_ref="review:fixture:independent", reviewed_at=NOW.isoformat(), target=target,
        cv=dict(sha256="c" * 64, user_approved=True),
        entry_policy=dict(strategy="native_verified_facts", decision_ref="USER-NATIVE-MVP-20261112"))
    facts = dict(schema_version="EJS-VERIFIED-FACTS-1", candidate_profile_version="fixture:v2")
    grant = dict(schema_version="EJS-REVIEWED-EXECUTION-SCOPE-1", scope_id="fixture:devoteam:scope2",
        user_decision_ref=EXPECTED_FUTURE_DECISION_REF,
        operations=dict(form_write=True, cv_upload=True, next=True, final_submit=True, canonical_scope_migration=True),
        source_sha="a" * 40, manifest_sha256=sha(raw(manifest)), facts_sha256=sha(raw(facts)),
        target=deepcopy(target), candidate_profile_version="fixture:v2", execution_id="fixture:execution:second",
        cv_sha256="c" * 64, reviewer_ref="review:fixture:independent", reviewed_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(hours=1)).isoformat(), legacy_snapshot_sha256=legacy_snapshot_fingerprint(snapshot))
    return dict(grant=grant, manifest=manifest, facts=facts, snapshot=snapshot,
                expected_target=OneClickTarget(**target))


def issue_scope(case, *, repin=True, **overrides):
    manifest, facts = raw(case["manifest"]), raw(case["facts"])
    if repin:
        case["grant"]["manifest_sha256"] = sha(manifest)
        case["grant"]["facts_sha256"] = sha(facts)
    grant = raw(case["grant"])
    args = dict(grant_payload=grant, manifest_payload=manifest, facts_payload=facts,
        grant_sha256=sha(grant), manifest_sha256=sha(manifest), facts_sha256=sha(facts),
        source_sha="a" * 40, expected_decision_ref=EXPECTED_FUTURE_DECISION_REF,
        expected_target=case["expected_target"], legacy_snapshot=case["snapshot"], now=NOW)
    args.update(overrides)
    return review_execution_scope(**args)


def blocked(fn):
    with pytest.raises(PermissionError) as caught:
        fn()
    assert str(caught.value) == ERROR


def test_exact_externally_approved_scope_binds_all_identity_without_mutation():
    case = execution_case()
    original = deepcopy(case)
    scope = issue_scope(case)
    require_issued_execution_scope(scope, now=NOW)
    assert scope.target == case["expected_target"]
    assert scope.legacy_snapshot_sha256 == legacy_snapshot_fingerprint(case["snapshot"])
    assert case == original


@pytest.mark.parametrize("decision", [None, True, 1, "", "USER-MVP-TEST-TARGET-20261112", "approved"])
def test_self_claimed_grant_bytes_do_not_supply_independent_user_decision(decision):
    blocked(lambda: issue_scope(execution_case(), expected_decision_ref=decision))


@pytest.mark.parametrize("name", ["form_write", "cv_upload", "next", "final_submit", "canonical_scope_migration"])
@pytest.mark.parametrize("value", [False, 0, 1, "true", None])
def test_every_operation_must_be_exactly_covered(name, value):
    case = execution_case()
    case["grant"]["operations"][name] = value
    blocked(lambda: issue_scope(case))


@pytest.mark.parametrize("mutation", ["extra_operation", "missing_operation", "extra_grant", "metadata_schema",
    "decision", "source", "profile", "execution", "cv", "reviewer", "legacy", "target", "native"])
def test_changed_reviewed_binding_or_extra_authority_stops(mutation):
    case = execution_case()
    grant = case["grant"]
    if mutation == "extra_operation":
        grant["operations"]["mailbox_read"] = True
    elif mutation == "missing_operation":
        del grant["operations"]["next"]
    elif mutation == "extra_grant":
        grant["ledger_path"] = "/fixture/replacement.sqlite3"
    elif mutation == "metadata_schema":
        grant["schema_version"] = "EJS-REVIEWED-NEXT-TEST-SCOPE-1"
    elif mutation == "decision":
        grant["user_decision_ref"] = "USER-MVP-TEST-TARGET-20261112"
    elif mutation == "source":
        grant["source_sha"] = "b" * 40
    elif mutation == "profile":
        case["facts"]["candidate_profile_version"] = "fixture:v3"
    elif mutation == "execution":
        grant["execution_id"] = "fixture:other"
    elif mutation == "cv":
        grant["cv_sha256"] = "d" * 64
    elif mutation == "reviewer":
        grant["reviewer_ref"] = "review:other"
    elif mutation == "legacy":
        grant["legacy_snapshot_sha256"] = "f" * 64
    elif mutation == "target":
        grant["target"]["company_id"] = "a" * 24
    else:
        del case["manifest"]["entry_policy"]
    blocked(lambda: issue_scope(case))


@pytest.mark.parametrize("name", ["scope_id", "operations", "legacy_snapshot_sha256", "expires_at"])
def test_missing_binding_stops(name):
    case = execution_case()
    del case["grant"][name]
    blocked(lambda: issue_scope(case))


@pytest.mark.parametrize("which", ["grant", "manifest", "facts"])
def test_external_byte_pins_are_independent(which):
    blocked(lambda: issue_scope(execution_case(), **{which + "_sha256": "f" * 64}))


def test_even_fully_repinned_different_target_cannot_open_another_scope():
    case = execution_case()
    for target in (case["grant"]["target"], case["manifest"]["target"]):
        target["requisition_id"] = "REF-OTHER"
    case["expected_target"] = OneClickTarget(**case["grant"]["target"])
    blocked(lambda: issue_scope(case))


@pytest.mark.parametrize("field,value", [
    ("reviewed_at", "2026-11-12T14:00:01+00:00"),
    ("reviewed_at", "2026-11-12T13:59:59+00:00"),
    ("reviewed_at", "2026-11-12T17:00:00+03:00"),
    ("reviewed_at", "2026-11-12T14:00:00"),
    ("expires_at", "2026-11-12T14:00:00+00:00"),
    ("expires_at", "2026-11-13T14:00:01+00:00"),
])
def test_operation_grant_is_current_short_utc(field, value):
    case = execution_case()
    case["grant"][field] = value
    blocked(lambda: issue_scope(case))


def test_stale_manifest_review_cannot_be_renewed_by_grant_alone():
    case = execution_case()
    case["manifest"]["reviewed_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
    blocked(lambda: issue_scope(case))


@pytest.mark.parametrize("change", ["missing", "extra", "status", "identity", "order"])
def test_unchanged_confirmed_legacy_snapshot_required(change):
    case = execution_case()
    snapshot = case["snapshot"]
    if change == "missing":
        del snapshot["evidence_digest"]
    elif change == "extra":
        snapshot["new_scope"] = True
    elif change == "status":
        snapshot["status"] = "Submitted / Unconfirmed"
    elif change == "identity":
        snapshot["requisition_digest"] = "f" * 64
    else:
        snapshot["started_at"] = "2026-11-06T00:00:00+00:00"
    blocked(lambda: issue_scope(case))


def test_metadata_or_reconstructed_dataclass_does_not_become_issued_grant():
    scope = issue_scope(execution_case())
    clone = replace(scope)
    assert clone is not scope
    blocked(lambda: require_issued_execution_scope(clone, now=NOW))
    blocked(lambda: require_issued_execution_scope(True, now=NOW))
    blocked(lambda: require_issued_execution_scope({}, now=NOW))
    object.__setattr__(scope, "execution_id", "fixture:forged")
    blocked(lambda: require_issued_execution_scope(scope, now=NOW))


@pytest.mark.parametrize("field,value", [
    ("company", "OtherCompany"), ("company_id", "a" * 24), ("posting_id", "744000199999999"),
    ("publication_id", "3d4a32a2-dc6b-4fd6-a0c2-f8e74336404f"), ("requisition_id", "REF-OTHER"),
    ("posting_url", "https://jobs.smartrecruiters.com/OtherCompany/744000156831399-other"),
])
def test_nested_target_mutation_is_detected_in_all_six_fields(field, value):
    scope = issue_scope(execution_case())
    object.__setattr__(scope.target, field, value)
    blocked(lambda: require_issued_execution_scope(scope, now=NOW))


def test_issued_target_does_not_alias_callers_expected_target():
    case = execution_case()
    scope = issue_scope(case)
    assert scope.target is not case["expected_target"]
    object.__setattr__(case["expected_target"], "company", "OtherCompany")
    object.__setattr__(case["expected_target"], "posting_url", "https://jobs.smartrecruiters.com/OtherCompany/744000156831399-other")
    require_issued_execution_scope(scope, now=NOW)
    assert scope.target.company == "Devoteam"


def test_expired_scope_cannot_begin_actions_but_issued_identity_can_reconcile():
    scope = issue_scope(execution_case())
    blocked(lambda: require_issued_execution_scope(scope, now=scope.expires_at))
    require_issued_execution_scope(scope, require_current=False)


@pytest.mark.parametrize("payload", [b'{"x":true,"x":false}', b'{"x":NaN}', b'[]', b'SYNTHETIC PRIVATE BYTES'])
def test_ambiguous_or_invalid_bytes_are_sanitized(payload):
    blocked(lambda: issue_scope(execution_case(), grant_payload=payload, grant_sha256=sha(payload)))
