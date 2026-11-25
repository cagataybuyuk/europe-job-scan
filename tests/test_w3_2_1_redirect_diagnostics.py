"""Offline W3.2.1 redirect diagnostics, persistence and bounded recovery tests.

No employer network is used. Every redirect here is simulated.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import socket
from types import SimpleNamespace

import pytest

from ejs.services.application_batch import (
    new_state,
    run_application_batch,
)
from ejs.services.smartrecruiters_live_inspector import (
    InspectionNetworkFence,
    REDIRECT_DIAGNOSTIC_LIMIT,
    sanitize_redirect_diagnostics,
    summarize_redirect,
)
from ejs.services.smartrecruiters_shadow import summarize_shadow_form

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)
P1, P2 = "744000000000001", "744000000000002"
TENANT = "ExampleCo"
POSTING = f"https://jobs.smartrecruiters.com/{TENANT}/{P1}"
CANONICAL = f"{POSTING}-senior-business-analyst"
@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("unexpected network use in W3.2.1 offline regression")
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)


def diagnostic(location, *, status=302, source=POSTING, method="GET",
               resource="document", navigation=True):
    return summarize_redirect(
        status=status, method=method, resource_type=resource, source_url=source,
        location=location, is_navigation=navigation,
    )


def row(posting=P1, tenant=TENANT):
    return {"ats_kind": "smartrecruiters", "region": "global", "tenant": tenant,
            "posting_id": posting, "url": f"https://jobs.smartrecruiters.com/{tenant}/{posting}",
            "company": tenant, "role": "Synthetic Analyst", "location": "Example"}


def discovery(*rows):
    return {"schema": "EJS-SHADOW-REPORT-2", "vacancies": list(rows), "listings_kept": len(rows)}


def detail(url):
    tenant, posting = url.split("/")[-3], url.split("/")[-1]
    return json.dumps({"id": posting, "company": {"identifier": tenant}, "active": True,
                       "refNumber": "REF" + posting[-3:]}).encode()


def inspection(request, *, fence=None, **changes):
    form = summarize_shadow_form({"controls": [{
        "scope": "document", "observation_key": "document/input@0", "tag": "input",
        "type": "text", "label": "First name", "visible": True, "required": True,
    }], "actions": []})
    base = {
        "requested_url": request.application_url, "runtime_state": "inspected", "error_code": "",
        "captcha_observed": False, "inspection_only": True, "live_execution_ready": False,
        "form_value_write_attempts": 0, "file_upload_attempts": 0, "submit_attempts": 0,
        "http_status": None,
        "network_fence": {"blocked_request_types": [], "redirect_diagnostics": [],
                          "challenge_destination_denied": False},
        "form": form,
    }
    if fence is not None:
        base["network_fence"] = fence
    base.update(changes)
    return base


def redirect_fence(*diagnostics, denials=("REDIRECT_UNREVIEWED",)):
    return {"blocked_request_types": list(denials),
            "redirect_diagnostics": list(diagnostics),
            "challenge_destination_denied": False}


def redirect_failure(request, *diagnostics, denials=("REDIRECT_UNREVIEWED",)):
    return inspection(
        request, fence=redirect_fence(*diagnostics, denials=denials),
        runtime_state="network_boundary", error_code="NETWORK_FENCE_BLOCKED",
    )


def run(rows, state, *, inspect=inspection, transport=detail, now=NOW, budget=5,
        checkpoints=None):
    return run_application_batch(
        discovery(*rows), state, budget=budget, detail=transport, inspect=inspect,
        consumed_requisitions=lambda: set(),
        checkpoint=lambda value: (checkpoints.append(deepcopy(value))
                                  if checkpoints is not None else None),
        clock=lambda: now,
    )


def event(report, posting=P1):
    return next(item for item in report["events"] if item["posting_id"] == posting)


def result_key(posting=P1, tenant=TENANT):
    return f"smartrecruiters:global:{tenant.casefold()}:{posting}"


# ---------------------------------------------------------------------------
# Pure redirect diagnostics
# ---------------------------------------------------------------------------

def test_same_origin_document_redirect_is_a_review_candidate_not_permission():
    value = diagnostic(CANONICAL + "?utm_source=secret#fragment")
    assert value["status"] == 302
    assert value["method"] == "GET"
    assert value["resource_type"] == "document"
    assert value["navigation"] is True
    assert value["source_host"] == "jobs.smartrecruiters.com"
    assert value["destination_host"] == "jobs.smartrecruiters.com"
    assert value["same_origin"] is True
    assert value["destination_path_category"] == "posting_detail"
    assert value["scheme_transition"] == "https_to_https"
    assert value["classification"] == "normal_ats_navigation_candidate"
    assert value["deny_reason"] == "REDIRECT_SAME_ORIGIN_UNREVIEWED"
    assert value["policy_candidate"] is True
    assert value["permission_granted"] is False
    assert value["access_restriction_evidence"] is False
    assert value["review_status"] == "unreviewed"
    serialized = json.dumps(value)
    assert "senior-business-analyst" not in serialized
    assert "utm_source" not in serialized
    assert "secret" not in serialized
    assert "fragment" not in serialized


def test_oneclick_relative_redirect_is_recognized_without_retaining_the_path():
    value = diagnostic("/oneclick-ui/ExampleCo/744000000000001?token=secret")
    assert value["destination_path_category"] == "oneclick_application"
    assert value["same_origin"] is True
    assert value["classification"] == "normal_ats_navigation_candidate"
    assert value["policy_candidate"] is True
    assert "token" not in json.dumps(value)
    assert "744000000000001" not in json.dumps(value)


def test_cross_origin_redirect_is_redacted_and_never_a_candidate():
    value = diagnostic("https://evil.example/collect?email=person@example.com#fragment")
    assert value["destination_host"] == "external_redacted"
    assert value["destination_trust_class"] == "untrusted_or_external"
    assert value["destination_path_category"] == "external"
    assert value["same_origin"] is False
    assert value["classification"] == "cross_origin_redirect"
    assert value["deny_reason"] == "REDIRECT_CROSS_ORIGIN"
    assert value["policy_candidate"] is False
    assert value["permission_granted"] is False
    serialized = json.dumps(value)
    for secret in ("evil.example", "collect", "person@example.com", "fragment"):
        assert secret not in serialized


def test_same_vendor_cross_origin_is_visible_but_still_denied():
    value = diagnostic("https://api.smartrecruiters.com/v1/session?token=secret")
    assert value["destination_host"] == "api.smartrecruiters.com"
    assert value["destination_trust_class"] == "trusted_https_host"
    assert value["same_origin"] is False
    assert value["classification"] == "cross_origin_redirect"
    assert value["policy_candidate"] is False
    assert "token" not in json.dumps(value)


@pytest.mark.parametrize("location,reason", [
    (None, "REDIRECT_LOCATION_MISSING"),
    ("", "REDIRECT_LOCATION_MISSING"),
    ("https://", "REDIRECT_LOCATION_INVALID"),
    ("https:///nohost", "REDIRECT_LOCATION_INVALID"),
    ("http://[bad", "REDIRECT_LOCATION_INVALID"),
    ("\nhttps://jobs.smartrecruiters.com/x", "REDIRECT_LOCATION_INVALID"),
    ("javascript:alert(1)", "REDIRECT_SCHEME_UNSUPPORTED"),
    ("data:text/html,secret", "REDIRECT_SCHEME_UNSUPPORTED"),
    ("https://user:pass@jobs.smartrecruiters.com/x", "REDIRECT_CREDENTIALS_REJECTED"),
    ("#fragment-only", "REDIRECT_SAME_URL"),
    (POSTING, "REDIRECT_SAME_URL"),
])
def test_malformed_locations_fail_closed_without_retaining_the_value(location, reason):
    value = diagnostic(location)
    assert value["location_present"] is (location is not None and bool(location.strip()))
    assert value["destination_valid"] is False
    assert value["classification"] == "malformed_redirect"
    assert value["deny_reason"] == reason
    assert value["policy_candidate"] is False
    assert value["permission_granted"] is False
    assert value["destination_host"] == ""
    serialized = json.dumps(value)
    assert "pass" not in serialized
    assert "secret" not in serialized
    assert "alert" not in serialized


@pytest.mark.parametrize("status", [300, 304])
def test_non_redirect_3xx_is_not_treated_as_navigation(status):
    value = diagnostic(CANONICAL, status=status)
    assert value["classification"] == "non_redirect_3xx"
    assert value["deny_reason"] == "REDIRECT_STATUS_UNSUPPORTED"
    assert value["policy_candidate"] is False


def test_subresource_and_auth_redirects_are_not_normal_apply_navigation():
    subresource = diagnostic("/api/session", resource="xhr", navigation=False)
    assert subresource["classification"] == "subresource_redirect"
    assert subresource["deny_reason"] == "REDIRECT_SUBRESOURCE_UNREVIEWED"
    assert subresource["policy_candidate"] is False

    auth = diagnostic("/login?return=posting")
    assert auth["destination_path_category"] == "auth_or_account"
    assert auth["classification"] == "same_origin_navigation_unreviewed"
    assert auth["policy_candidate"] is False
    assert auth["access_restriction_evidence"] is False


def test_diagnostics_are_strictly_sanitized_for_persistence():
    valid = diagnostic(CANONICAL)
    assert sanitize_redirect_diagnostics([valid]) == [valid]
    assert sanitize_redirect_diagnostics(json.loads(json.dumps([valid]))) == [valid]

    tampered = dict(valid, url="https://jobs.smartrecruiters.com/private?token=secret")
    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_FIELDS"):
        sanitize_redirect_diagnostics([tampered])

    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_PERMISSION"):
        sanitize_redirect_diagnostics([dict(valid, permission_granted=True)])

    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTICS_INVALID"):
        sanitize_redirect_diagnostics([valid] * (REDIRECT_DIAGNOSTIC_LIMIT + 1))

    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_CONSISTENCY"):
        sanitize_redirect_diagnostics([dict(valid, location_present=False)])
    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_CONSISTENCY"):
        sanitize_redirect_diagnostics([dict(valid, classification="malformed_redirect")])
    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_CONSISTENCY"):
        sanitize_redirect_diagnostics([dict(valid, same_origin=True, destination_valid=False)])


# ---------------------------------------------------------------------------
# Network fence
# ---------------------------------------------------------------------------

class FakeRoute:
    def __init__(self, url, method="GET", resource="document", status=200, headers=None):
        self.request = SimpleNamespace(
            url=url, method=method, resource_type=resource,
            is_navigation_request=lambda: resource == "document",
        )
        self.status = status
        self.headers = headers or {}
        self.fetched = self.fulfilled = self.aborted = self.disposed = False

    def fetch(self, **kwargs):
        self.fetched = True
        assert kwargs["max_redirects"] == 0 and kwargs["max_retries"] == 0
        return SimpleNamespace(status=self.status, headers=self.headers,
                               dispose=lambda: setattr(self, "disposed", True))

    def fulfill(self, **kwargs):
        self.fulfilled = True

    def abort(self, *_):
        self.aborted = True


def test_fence_records_sanitized_same_origin_redirect_evidence():
    fence = InspectionNetworkFence(POSTING, fetch_timeout_ms=1000)
    route = FakeRoute(POSTING, status=301,
                      headers={"location": CANONICAL + "?token=SECRET#fragment"})
    fence.route(route)
    assert route.aborted and not route.fulfilled and route.fetched and route.disposed
    assert fence.denials == {"REDIRECT_UNREVIEWED"}
    report = fence.report()
    assert report["redirect_count"] == 1
    assert report["redirect_diagnostic_limit"] == REDIRECT_DIAGNOSTIC_LIMIT
    assert len(report["redirect_diagnostics"]) == 1
    evidence = report["redirect_diagnostics"][0]
    assert evidence["classification"] == "normal_ats_navigation_candidate"
    assert evidence["policy_candidate"] is True
    assert evidence["permission_granted"] is False
    serialized = json.dumps(report)
    for secret in ("SECRET", "token", "fragment", "senior-business-analyst"):
        assert secret not in serialized


def test_fence_redacts_cross_origin_redirect_destination():
    fence = InspectionNetworkFence(POSTING, fetch_timeout_ms=1000)
    route = FakeRoute(POSTING, status=302,
                      headers={"location": "https://evil.example/collect?token=SECRET"})
    fence.route(route)
    evidence = fence.report()["redirect_diagnostics"][0]
    assert evidence["classification"] == "cross_origin_redirect"
    assert evidence["destination_host"] == "external_redacted"
    assert evidence["policy_candidate"] is False
    assert "evil.example" not in json.dumps(fence.report())
    assert "SECRET" not in json.dumps(fence.report())


def test_fence_denies_malformed_redirect_without_location_leak():
    fence = InspectionNetworkFence(POSTING, fetch_timeout_ms=1000)
    route = FakeRoute(POSTING, status=302, headers={})
    fence.route(route)
    evidence = fence.report()["redirect_diagnostics"][0]
    assert evidence["classification"] == "malformed_redirect"
    assert evidence["deny_reason"] == "REDIRECT_LOCATION_MISSING"
    assert route.aborted


def test_fence_bounds_redirect_samples_but_keeps_a_total_count():
    fence = InspectionNetworkFence(POSTING, fetch_timeout_ms=1000)
    for index in range(REDIRECT_DIAGNOSTIC_LIMIT + 3):
        route = FakeRoute(POSTING, status=302,
                          headers={"location": f"/Version1/{P1}-variant-{index}"})
        fence.route(route)
        assert route.aborted
    report = fence.report()
    assert report["redirect_count"] == REDIRECT_DIAGNOSTIC_LIMIT + 3
    assert len(report["redirect_diagnostics"]) == REDIRECT_DIAGNOSTIC_LIMIT
    assert all(item["status"] == 302 for item in report["redirect_diagnostics"])


def test_fence_access_boundary_does_not_become_redirect_evidence():
    fence = InspectionNetworkFence(POSTING, fetch_timeout_ms=1000)
    denied = FakeRoute(POSTING, status=403)
    held = FakeRoute(POSTING)
    fence.route(denied)
    fence.route(held)
    assert denied.aborted and denied.fetched and denied.disposed
    assert fence.http_status == 403
    assert held.aborted and not held.fetched
    assert fence.report()["redirect_diagnostics"] == []
    assert fence.report()["redirect_count"] == 0
    assert fence.denials == {"HTTP_ACCESS_OR_RATE_BOUNDARY", "HTTP_RESPONSE_BOUNDARY"}


# ---------------------------------------------------------------------------
# Batch persistence
# ---------------------------------------------------------------------------

def test_redirect_failure_evidence_persists_without_creating_an_access_hold():
    state, checkpoints = new_state(), []
    evidence = diagnostic(CANONICAL + "?token=SECRET")
    report = run([row()], state, checkpoints=checkpoints,
                 inspect=lambda request: redirect_failure(request, evidence))
    failed = event(report)
    assert failed["error_code"] == "NETWORK_FENCE_BLOCKED"
    assert failed["retry_eligible"] is False
    assert failed["network_denial_types"] == ["REDIRECT_UNREVIEWED"]
    assert failed["redirect_diagnostics"] == [evidence]
    assert state["access_holds"] == {}
    stored = state["results"][result_key()]
    assert stored["redirect_diagnostics"] == [evidence]
    assert checkpoints[-1]["results"][result_key()]["redirect_diagnostics"] == [evidence]
    assert json.loads(json.dumps(state))["results"][result_key()]["redirect_diagnostics"] == [evidence]
    for payload in (report, state, checkpoints[-1]):
        serialized = json.dumps(payload)
        assert "SECRET" not in serialized
        assert "senior-business-analyst" not in serialized


def test_malformed_redirect_evidence_fails_closed_and_is_not_persisted():
    state = new_state()
    report = run([row()], state, inspect=lambda request: inspection(
        request,
        fence={"blocked_request_types": ["REDIRECT_UNREVIEWED"],
               "redirect_diagnostics": [{"url": "https://secret.example/?token=SECRET"}],
               "challenge_destination_denied": False},
        runtime_state="network_boundary", error_code="NETWORK_FENCE_BLOCKED",
    ))
    failed = event(report)
    assert failed["error_code"] == "INSPECT_ADAPTER_FAILED"
    assert failed["redirect_diagnostics"] == []
    assert state["results"][result_key()]["redirect_diagnostics"] == []
    assert "SECRET" not in json.dumps(report)
    assert "secret.example" not in json.dumps(state)


def test_tampered_persisted_redirect_evidence_is_rejected_before_network():
    state = new_state()
    run([row()], state, inspect=lambda request: redirect_failure(request, diagnostic(CANONICAL)))
    state["results"][result_key()]["redirect_diagnostics"][0]["url"] = "https://secret.example/"
    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_FIELDS"):
        run([row()], state, now=NOW + timedelta(minutes=1),
            transport=lambda _: pytest.fail("tampered state reached the network"))


def test_legacy_state_without_redirect_evidence_still_loads():
    state = new_state()
    run([row()], state, inspect=lambda request: redirect_failure(request, diagnostic(CANONICAL)))
    del state["results"][result_key()]["redirect_diagnostics"]
    later = run([row()], state, now=NOW + timedelta(minutes=1),
                transport=lambda _: pytest.fail("legacy state reached the network"))
    assert later["skipped_counts"]["already_processed"] == 1


def test_invalid_redirect_port_is_not_a_candidate():
    value = diagnostic("https://jobs.smartrecruiters.com:bad/ExampleCo/744000000000001")
    assert value["classification"] == "malformed_redirect"
    assert value["policy_candidate"] is False


def test_unreviewed_subdomain_is_redacted():
    value = diagnostic("https://secret.smartrecruiters.com/private")
    assert value["destination_host"] == "external_redacted"
    assert "secret" not in json.dumps(value)


def test_persisted_unknown_host_is_rejected():
    value = diagnostic(CANONICAL)
    with pytest.raises(ValueError, match="REDIRECT_DIAGNOSTIC_HOST"):
        sanitize_redirect_diagnostics([
            dict(value, destination_host="secret.example")
        ])
