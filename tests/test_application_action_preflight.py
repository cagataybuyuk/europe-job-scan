from datetime import datetime, timedelta, timezone

import pytest

from ejs.services.application_action_preflight import evaluate_application_action_preflight

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
TARGET = {"candidate_id": "candidate-a", "employer_id": "employer-a",
          "vacancy_url": "https://employer.example/jobs/1", "country": "Portugal"}


def evidence():
    return {**TARGET, "state": "VERIFIED", "source_type": "user_explicit",
            "verified": True, "current": True, "source_ref": "private:proof",
            "expires_at": NOW + timedelta(days=1)}


def permission(action="write"):
    return {k: TARGET[k] for k in ("candidate_id", "employer_id", "vacancy_url")} | {
        "action": action, "approved": True, "reviewed": True, "revoked": False,
        "expires_at": NOW + timedelta(minutes=10)}


def check(ev=None, target=None, grant=None, action="write", now=NOW):
    return evaluate_application_action_preflight(
        evidence() if ev is None else ev,
        TARGET if target is None else target,
        permission(action) if grant is None else grant, action, now)


def test_matching_preflight_never_grants_ats_or_network_authority():
    for action in ("write", "upload", "submit"):
        result = check(action=action)
        assert result["status"] == "PREFLIGHT_READY"
        assert result["network_authorized"] is False
        assert all(result[k] is False for k in
                   ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("action", ["", "delete", "Applied", None, 1, [], {}])
def test_unknown_actions_blocked(action):
    assert check(action=action)["status"] == "BLOCKED"


@pytest.mark.parametrize("change", [
    {"approved": False}, {"approved": "true"}, {"reviewed": False},
    {"reviewed": "true"}, {"revoked": True}, {"revoked": None},
    {"action": "upload"}, {"expires_at": NOW},
    {"expires_at": NOW - timedelta(seconds=1)}, {"expires_at": "tomorrow"},
    {"expires_at": datetime(2027, 1, 1)}, {"candidate_id": "someone-else"},
    {"employer_id": "other"}, {"vacancy_url": "https://employer.example/jobs/2"},
])
def test_unapproved_unbound_expired_or_changed_consent_blocks(change):
    assert check(grant={**permission(), **change})["status"] == "BLOCKED"


@pytest.mark.parametrize("change", [
    {"candidate_id": "other"}, {"employer_id": "other"},
    {"vacancy_url": "https://employer.example/jobs/2"},
    {"country": "Germany"}, {"verified": False}, {"current": False},
    {"expires_at": NOW},
])
def test_unverified_or_unbound_eligibility_blocks(change):
    assert check(ev={**evidence(), **change})["status"] == "BLOCKED"


@pytest.mark.parametrize("bad", [None, {}, [], "permission"])
def test_missing_or_malformed_permission_blocks(bad):
    assert check(grant=bad)["status"] == "BLOCKED"


def test_permission_does_not_transfer_to_other_action():
    assert check(grant=permission("write"), action="submit")["status"] == "BLOCKED"


def test_target_substitution_blocks():
    assert check(target={**TARGET, "vacancy_url": "https://other.example/1"})["status"] == "BLOCKED"


def test_naive_or_invalid_clock_blocks():
    assert check(now=datetime(2026, 11, 9))["status"] == "BLOCKED"
    assert check(now=None)["status"] == "BLOCKED"


@pytest.mark.parametrize("url", [
    "http://employer.example/jobs/1",
    "javascript:alert(1)",
    "https://user:pass@employer.example/jobs/1",
    "https://employer.example:8443/jobs/1",
    "https://employer.example/jobs/1#submit",
    "https://employer.example/jobs/1\\n",
    "https://employer.example:invalid/jobs/1",
    "https:///jobs/1",
])
def test_unsafe_exact_match_url_never_makes_preflight_ready(url):
    # Even if all three records agree, an unsafe target must fail closed.
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    grant = {**permission(), "vacancy_url": url}
    result = check(ev=ev, target=target, grant=grant)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert all(result[k] is False for k in
               ("write_authorized", "upload_authorized", "submit_authorized"))
