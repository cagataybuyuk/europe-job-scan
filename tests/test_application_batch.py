"""Offline W3.1 batch/fence/checkpoint regression; no employer network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from ejs.persistence.policy_submission_ledger import read_consumed_requisitions, requisition_hashes
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.application_batch import new_state, run_application_batch
from ejs.services.browser_worker import BrowserRuntimeConfig, installed_chrome
from ejs.services.smartrecruiters_live_inspector import InspectionNetworkFence
from ejs.services.smartrecruiters_shadow import summarize_shadow_form

NOW = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
P1, P2, P3 = "744000000000001", "744000000000002", "744000000000003"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("unexpected network use in W3.1 offline regression")
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)


def row(posting, tenant="ExampleCo"):
    return {"ats_kind": "smartrecruiters", "region": "global", "tenant": tenant,
            "posting_id": posting, "url": f"https://jobs.smartrecruiters.com/{tenant}/{posting}",
            "company": tenant, "role": "Synthetic Analyst", "location": "Example"}


def discovery(*rows):
    return {"schema": "EJS-SHADOW-REPORT-2", "vacancies": list(rows), "listings_kept": len(rows)}


def detail(url, *, same_req=False):
    tenant, posting = url.split("/")[-3], url.split("/")[-1]
    return json.dumps({"id": posting, "company": {"identifier": tenant}, "active": True,
                       "refNumber": "REF001" if same_req else "REF" + posting[-3:]}).encode()


def inspected(request, **changes):
    form = summarize_shadow_form({"controls": [
        {"scope": "document", "observation_key": "document/input@0", "tag": "input",
         "type": "text", "label": "First name", "visible": True, "required": True},
        {"scope": "document", "observation_key": "document/input@1", "tag": "input",
         "type": "file", "label": "CV", "visible": True, "required": True, "accept": "application/pdf"},
    ], "actions": []})
    return dict({"requested_url": request.application_url, "runtime_state": "inspected", "error_code": "",
                 "captcha_observed": False, "inspection_only": True, "live_execution_ready": False,
                 "form_value_write_attempts": 0, "file_upload_attempts": 0, "submit_attempts": 0,
                 "http_status": None, "network_fence": {"blocked_request_types": [],
                     "challenge_destination_denied": False}, "form": form}, **changes)


def run(rows, state, *, inspect=inspected, transport=detail, now=NOW, budget=5,
        consumed=lambda: set(), checkpoints=None, **kwargs):
    return run_application_batch(discovery(*rows), state, budget=budget, detail=transport,
        inspect=inspect, consumed_requisitions=consumed,
        checkpoint=lambda value: checkpoints.append(deepcopy(value)) if checkpoints is not None else None,
        clock=lambda: now, **kwargs)


def event(report, posting):
    return next(item for item in report["events"] if item["posting_id"] == posting)


def test_failure_isolation_and_manifest_requirements():
    state, checkpoints = new_state(), []
    def inspect(request):
        if request.application_url.endswith(P1):
            raise OSError("synthetic private payload must not be logged")
        return inspected(request)
    report = run([row(P1), row(P2)], state, inspect=inspect, checkpoints=checkpoints)
    assert event(report, P1)["error_code"] == "INSPECT_ADAPTER_FAILED"
    success = event(report, P2)
    assert success["outcome"] == "form_observed"
    assert success["manifest_inputs"]["target"]["posting_id"] == P2
    assert len(success["manifest_inputs"]["source_detail_sha256"]) == 64
    assert success["manifest_inputs"]["required_cv_upload_binding_observed"]
    assert not report["submit_authorized"] and report["inspection_only"]
    assert "private payload" not in json.dumps(report)
    assert checkpoints[-1] == state and len(checkpoints) == 6


def test_retry_backoff_persists_across_json_roundtrip():
    state = new_state()
    report = run([row(P1)], state, transport=lambda _: (_ for _ in ()).throw(OSError("offline")))
    first = event(report, P1)
    assert first["retry_eligible"] and first["attempt_count"] == 1
    state = json.loads(json.dumps(state))
    later = run([row(P1)], state, now=NOW + timedelta(minutes=1),
                transport=lambda _: pytest.fail("backoff not respected"))
    assert later["skipped_counts"]["retry_backoff"] == 1
    final = run([row(P1)], state, now=NOW + timedelta(minutes=16))
    assert event(final, P1)["attempt_count"] == 2
    assert event(final, P1)["outcome"] == "form_observed"


def test_fresh_observation_expires_without_becoming_a_submission():
    state = new_state()
    run([row(P1)], state)
    fresh = run([row(P1)], state, now=NOW + timedelta(hours=1),
                transport=lambda _: pytest.fail("fresh observation should be reused"))
    assert fresh["skipped_counts"]["fresh_form_observation"] == 1
    stale = run([row(P1)], state, now=NOW + timedelta(hours=7))
    assert event(stale, P1)["attempt_count"] == 2
    assert not stale["submit_authorized"]


def test_duplicate_after_temporary_failure_is_deferred_not_discarded():
    state = new_state()
    calls = []
    def temporary(request):
        calls.append(request.application_url)
        return inspected(request, runtime_state="form_structure_not_discovered", error_code="FORM_STRUCTURE_NOT_DISCOVERED")
    report = run([row(P1), row(P2)], state, inspect=temporary, transport=lambda url: detail(url, same_req=True))
    assert len(calls) == 1
    duplicate = event(report, P2)
    assert duplicate["error_code"] == "DUPLICATE_REQUISITION_DEFERRED" and duplicate["retry_eligible"]
    report = run([row(P2)], state, now=NOW + timedelta(minutes=16), transport=lambda url: detail(url, same_req=True))
    assert event(report, P2)["outcome"] == "form_observed"


def test_duplicate_success_uses_observation_expiry():
    state = new_state()
    report = run([row(P1), row(P2)], state, transport=lambda url: detail(url, same_req=True))
    assert event(report, P1)["outcome"] == "form_observed"
    assert event(report, P2)["next_eligible_run"] == (NOW + timedelta(hours=6)).isoformat()


def test_canonical_and_private_exclusions_never_inspect():
    calls = []
    def transport(url):
        calls.append(url)
        return detail(url)
    report = run([row(P1), row(P2), row(P3)], new_state(), transport=transport,
                 inspect=lambda _: pytest.fail("excluded target inspected"),
                 excluded_postings={("exampleco", P1)},
                 excluded_requisitions={("exampleco", x) for x in requisition_hashes("REF002")},
                 consumed=lambda: requisition_hashes("REF003"))
    assert report["skipped_counts"]["private_exclusion"] == 2 and len(calls) == 2
    assert event(report, P3)["error_code"] == "CANONICAL_REQUISITION_CONSUMED"
    assert not event(report, P3)["retry_eligible"]


def test_access_hold_does_not_block_other_tenant_or_retry_challenge():
    state = new_state()
    def inspect(request):
        if "/ExampleCo/" in request.application_url:
            return inspected(request, captcha_observed=True, runtime_state="captcha_boundary", error_code="CAPTCHA_BOUNDARY")
        return inspected(request)
    report = run([row(P1), row(P2), row(P3, "OtherCo")], state, inspect=inspect)
    assert event(report, P1)["error_code"] == "CAPTCHA_BOUNDARY"
    assert not event(report, P1)["retry_eligible"]
    assert report["skipped_counts"]["employer_access_hold"] == 1
    assert event(report, P3)["outcome"] == "form_observed"
    later = run([row(P2)], state, now=NOW + timedelta(days=1), transport=lambda _: pytest.fail("access hold ignored"))
    assert later["skipped_counts"]["employer_access_hold"] == 1


def test_429_respects_retry_after_and_service_pause():
    state = new_state()
    def limited(url):
        raise HTTPError(url, 429, "synthetic", {"Retry-After": "3600"}, None)
    report = run([row(P1), row(P2, "OtherCo")], state, transport=limited)
    assert event(report, P1)["next_eligible_run"] == (NOW + timedelta(hours=1)).isoformat()
    assert report["skipped_counts"]["detail_rate_limit"] == 1


@pytest.mark.parametrize("change", [
    {"captcha_observed": True}, {"submit_attempts": 1}, {"file_upload_attempts": 1},
    {"form_value_write_attempts": 1}, {"inspection_only": False}, {"live_execution_ready": True},
    {"requested_url": "https://jobs.smartrecruiters.com/OtherCo/744000000000009"},
    {"network_fence": {"blocked_request_types": ["NON_GET_REQUEST"], "challenge_destination_denied": False}},
    {"network_fence": {"blocked_request_types": ["READ_TIMEOUT"], "challenge_destination_denied": False}},
    {"network_fence": {"blocked_request_types": [], "challenge_destination_denied": True}},
    {"network_fence": []}, {"http_status": []},
])
def test_invalid_success_cannot_promote_form(change):
    def inspect(request):
        if request.application_url.endswith(P1):
            return inspected(request, **change)
        return inspected(request)
    report = run([row(P1), row(P2, "OtherCo")], new_state(), inspect=inspect)
    assert event(report, P1)["outcome"] == "failure"
    assert "manifest_inputs" not in event(report, P1)
    assert event(report, P2)["outcome"] == "form_observed"


def test_malformed_state_stops_before_network():
    state = new_state()
    state["cooldowns"] = {"detail": "invalid"}
    with pytest.raises(ValueError):
        run([row(P1)], state, transport=lambda _: pytest.fail("malformed state used"))


def test_read_only_ledger_does_not_modify_existing_file(tmp_path):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    ledger = SqliteSubmissionLedger(parent / "ledger.sqlite3")
    before = ledger.path.read_bytes()
    assert read_consumed_requisitions(ledger) == set()
    assert ledger.path.read_bytes() == before
    assert sorted(p.name for p in parent.iterdir()) == ["ledger.sqlite3"]


def test_checkpoint_replace_and_nonoverwrite(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    persist = runpy.run_path(str(root / "scripts/application_batch.py"))["persist"]
    directory = tmp_path.resolve()
    directory.chmod(0o700)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        persist(fd, "retry-state.json", {"attempt": 1}, replace=True)
        persist(fd, "retry-state.json", {"attempt": 2}, replace=True)
        assert json.loads((directory / "retry-state.json").read_bytes()) == {"attempt": 2}
        persist(fd, "report.json", {"result": 1})
        with pytest.raises(FileExistsError):
            persist(fd, "report.json", {"result": 2})
        assert json.loads((directory / "report.json").read_bytes()) == {"result": 1}
        assert sorted(x.name for x in directory.iterdir()) == ["report.json", "retry-state.json"]
    finally:
        os.close(fd)


def test_chrome_resolver_does_not_install_and_rejects_directory(tmp_path):
    fake = tmp_path / "chrome"
    fake.write_text("synthetic")
    fake.chmod(0o700)
    chromium = SimpleNamespace(executable_path=str(fake))
    assert installed_chrome(BrowserRuntimeConfig(use_playwright_managed=True), chromium) == str(fake)
    assert installed_chrome(BrowserRuntimeConfig(executable_path=str(tmp_path)), chromium) == ""


class FakeRoute:
    def __init__(self, url, method="GET", resource="document", status=200):
        self.request = SimpleNamespace(url=url, method=method, resource_type=resource,
                                       is_navigation_request=lambda: resource == "document")
        self.fetched = self.fulfilled = self.aborted = self.disposed = False
        self.status = status
    def fetch(self, **kwargs):
        self.fetched = True
        assert kwargs["max_redirects"] == 0 and kwargs["max_retries"] == 0
        return SimpleNamespace(status=self.status, headers={}, dispose=lambda: setattr(self, "disposed", True))
    def fulfill(self, **kwargs):
        self.fulfilled = True
    def abort(self, *_):
        self.aborted = True


def test_network_fence_denies_post_external_and_redirect_without_fetching_denied_targets():
    url = row(P1)["url"]
    fence = InspectionNetworkFence(url, fetch_timeout_ms=1000)
    post = FakeRoute(url, method="POST", resource="xhr")
    external = FakeRoute("https://outside.example/script.js", resource="script")
    redirect = FakeRoute(url, status=302)
    for route in (post, external, redirect):
        fence.route(route)
        assert route.aborted and not route.fulfilled
    assert not post.fetched and not external.fetched and redirect.fetched and redirect.disposed
    assert fence.denials == {"NON_GET_REQUEST", "UNSUPPORTED_EXTERNAL_RESOURCE", "REDIRECT_UNREVIEWED"}


def test_network_fence_holds_after_access_denial():
    url = row(P1)["url"]
    fence = InspectionNetworkFence(url, fetch_timeout_ms=1000)
    first, second = FakeRoute(url, status=403), FakeRoute(url)
    fence.route(first)
    fence.route(second)
    assert first.aborted and first.fetched and first.disposed
    assert fence.http_status == 403 and second.aborted and not second.fetched
