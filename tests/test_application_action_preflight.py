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


@pytest.mark.parametrize("url", [
    "https://employer.example\\\\@attacker.example/jobs/1",
    "https://employer.example/jobs/1\\\\other",
    "https://employer.example/jobs/1\\x00",
    "https://employer.example/jobs/1\\x1f",
    "https://employer.example/jobs/1\\u200b",
    "https://employer.example/jobs/1\\u202e",
])
def test_control_and_backslash_url_ambiguity_denied_even_with_matching_consent(url):
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    grant = {**permission(), "vacancy_url": url}
    result = check(ev=ev, target=target, grant=grant)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert result["submit_authorized"] is False

@pytest.mark.parametrize("url", [
    "https://localhost/jobs/1",
    
    "https://internal.local/jobs/1",
    "https://service.localhost/jobs/1",
    "https://127.0.0.1/jobs/1",
    "https://10.0.0.8/jobs/1",
    "https://192.168.1.20/jobs/1",
    "https://169.254.169.254/latest/meta-data",
    "https://[::1]/jobs/1",
    "https://[fc00::1]/jobs/1",
    "https://0.0.0.0/jobs/1",
])
def test_nonpublic_ats_destinations_denied_even_with_matching_evidence(url):
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    grant = {**permission(), "vacancy_url": url}
    for action in ("write", "upload", "submit"):
        grant_for_action = {**grant, "action": action}
        result = check(ev=ev, target=target, grant=grant_for_action, action=action)
        assert result["status"] == "BLOCKED"
        assert result["network_authorized"] is False
        assert all(result[k] is False for k in (
            "write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("url", [
    "https://localhost./jobs/1",
    "https://service.localhost./jobs/1",
    "https://internal.local./jobs/1",
    "https://employer.example./jobs/1",
    "https://local%68ost/jobs/1",
    "https://employer%2eexample/jobs/1",
])
def test_ambiguous_dns_host_denied_for_every_mutation_action(url):
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    for action in ("write", "upload", "submit"):
        grant = {**permission(action), "vacancy_url": url}
        result = check(ev=ev, target=target, grant=grant, action=action)
        assert result["status"] == "BLOCKED"
        assert result["network_authorized"] is False
        assert all(result[k] is False for k in (
            "write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("url", [
    "https://ats.internal/jobs/1",
    "https://gateway.home.arpa/jobs/1",
    "https://hidden.onion/jobs/1",
    "https://nonexistent.invalid/jobs/1",
])
def test_special_use_hosts_never_authorize_employer_mutation(url):
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    for action in ("write", "upload", "submit"):
        grant = {**permission(action), "vacancy_url": url}
        result = check(ev=ev, target=target, grant=grant, action=action)
        assert result["status"] == "BLOCKED"
        assert result["network_authorized"] is False
        assert all(result[k] is False for k in (
            "write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("bad_now", [None, "2026-11-09", 0, {}, [], datetime(2026, 11, 9)])
def test_invalid_clock_fails_closed_before_eligibility_gate(bad_now, monkeypatch):
    def must_not_evaluate(*args, **kwargs):
        raise AssertionError("eligibility must not execute for invalid clock")
    monkeypatch.setattr(
        "ejs.services.application_action_preflight.evaluate_bound_work_rights",
        must_not_evaluate,
    )
    result = check(now=bad_now)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert result["write_authorized"] is False
    assert result["upload_authorized"] is False
    assert result["submit_authorized"] is False


@pytest.mark.parametrize("eligibility_state", ["UNKNOWN", "INELIGIBLE"])
@pytest.mark.parametrize("action", ["write", "upload", "submit"])
def test_no_foreign_work_rights_never_grants_any_ats_mutation(eligibility_state, action):
    """A general application preference cannot override missing work authorization.

    Sponsorship possibility is not evidence of a current country-specific right.
    This is a pure preflight: no ATS transport or employer mutation occurs.
    """
    ev = {**evidence(), "state": eligibility_state}
    result = check(ev=ev, grant=permission(action), action=action)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert all(result[key] is False for key in (
        "write_authorized", "upload_authorized", "submit_authorized"
    ))


@pytest.mark.parametrize("action", ["write", "upload", "submit"])
@pytest.mark.parametrize("sponsorship", ["UNKNOWN", "POSSIBLE", "OFFERED", True])
def test_sponsorship_claim_cannot_replace_verified_country_work_right(action, sponsorship):
    """Unverified sponsorship claims must not become employer mutation authority."""
    ev = {**evidence(), "state": "UNKNOWN", "sponsorship": sponsorship}
    target = {**TARGET, "sponsorship": sponsorship}
    grant = {**permission(action), "sponsorship": sponsorship}
    result = check(ev=ev, target=target, grant=grant, action=action)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert result["write_authorized"] is False
    assert result["upload_authorized"] is False
    assert result["submit_authorized"] is False


@pytest.mark.parametrize("url", [
    "https://2130706433/jobs/1",
    "https://0177.0.0.1/jobs/1",
    "https://0x7f000001/jobs/1",
    "https://0x7f.0x0.0x0.0x1/jobs/1",
    "https://192.168.001.001/jobs/1",
])
def test_legacy_numeric_ip_aliases_never_grant_mutation_preflight(url):
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    for action in ("write", "upload", "submit"):
        grant = {**permission(action), "vacancy_url": url}
        result = check(ev=ev, target=target, grant=grant, action=action)
        assert result["status"] == "BLOCKED"
        assert result["network_authorized"] is False
        assert not any(result[k] for k in (
            "write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("url", [
    "https://employer.example/jobs/1%0aInjected",
    "https://employer.example/jobs/1%0D%0AHeader",
    "https://employer.example/jobs/1?token=%00",
    "https://employer.example/jobs/1%1f",
    "https://employer.example/jobs/1%7F",
])
@pytest.mark.parametrize("action", ["write", "upload", "submit"])
def test_percent_encoded_control_bytes_block_all_ats_mutations(url, action):
    """Consent to an exact URL does not permit encoded control-byte ambiguity."""
    target = {**TARGET, "vacancy_url": url}
    ev = {**evidence(), "vacancy_url": url}
    grant = {**permission(action), "vacancy_url": url}
    result = check(ev=ev, target=target, grant=grant, action=action)
    assert result["status"] == "BLOCKED"
    assert result["network_authorized"] is False
    assert all(result[key] is False for key in (
        "write_authorized", "upload_authorized", "submit_authorized"
    ))
