"""Local Chromium proofs of preparation network and effect-evidence boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright

from ejs.apps.github_runner.safe_fill_upload_canary import _fixture_plans
from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionRequest
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.prepare_network_fence import PrepareBoundaryError, PrepareNetworkFence
from ejs.services.safe_field_writer import PlaywrightSafeFieldWriter
from ejs.services.smartrecruiters_executor import PlaywrightSmartRecruitersExecutor

FIXTURE = Path(__file__).parent / "fixtures" / "browser" / "smartrecruiters_e2e_form.html"
CV = FIXTURE.with_name("test_cv.pdf")


@pytest.fixture(scope="module")
def chromium_config():
    with sync_playwright() as p:
        executable = p.chromium.executable_path
    if not Path(executable).exists():
        pytest.skip("Playwright Chromium not installed")
    return BrowserRuntimeConfig(executable_path=executable, navigation_timeout_ms=3000, settle_timeout_ms=20)


@contextmanager
def local_form(script=""):
    requests = []
    html = FIXTURE.read_text().replace("</head>", '<link rel="icon" href="data:,"></head>').replace("</body>", f"<script>{script}</script></body>")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/application", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request_for(config, target):
    inspected = PlaywrightBrowserWorker(config).inspect(BrowserInspectionRequest(
        bridge_request_id="bridge:test", route_key="route:test", opportunity_key="fixture:test",
        requisition_id="fixture-001", application_url=target, adapter_key="ats:smartrecruiters",
        observed_at="2026-11-04T18:00:00Z"))
    assert inspected.form_fingerprint, inspected.error_code
    fields, uploads, resolutions = _fixture_plans(CV)
    return SmartRecruitersExecutionRequest(
        execution_id="execution:test", bridge_request_id="bridge:test", route_key="route:test",
        opportunity_key="fixture:test", requisition_id="fixture-001", application_url=target,
        observed_at="2026-11-04T18:00:00Z", expected_form_fingerprint=inspected.form_fingerprint,
        candidate_profile_version="fixture-v1", application_status="To Apply", readiness_confidence="High",
        unresolved_required=0, field_plan=fields, upload_plan=uploads, control_resolutions=resolutions)


@pytest.mark.parametrize("method,kind,navigation,url", [
    ("POST", "fetch", False, "https://jobs.example/application"),
    ("GET", "fetch", False, "https://jobs.example/private?email=SECRET"),
    ("GET", "image", False, "https://jobs.example/pixel?email=SECRET"),
    ("GET", "script", False, "https://external.example/app.js"),
    ("GET", "document", True, "https://jobs.example/other-job"),
    ("GET", "document", True, "https://jobs.example:444/application"),
    ("GET", "document", True, "https://user:SECRET@jobs.example/application"),
])
def test_unknown_channels_denied(method, kind, navigation, url):
    fence = PrepareNetworkFence("https://jobs.example/application")
    assert not fence.allowed(url=url, method=method, resource_type=kind, navigation=navigation)
    assert "SECRET" not in repr(fence.__dict__)


def test_exact_entry_and_static_read_allowed_only_before_preparation():
    fence = PrepareNetworkFence("https://jobs.example/application?job=123")
    assert fence.allowed(url=fence.target_url, method="GET", resource_type="document", navigation=True)
    assert fence.allowed(url="https://jobs.example/app.js", method="GET", resource_type="script", navigation=False)
    fence.begin_preparation()
    assert not fence.allowed(url="https://jobs.example/app.js", method="GET", resource_type="script", navigation=False)


def test_file_fixture_denies_all_network():
    fence = PrepareNetworkFence(FIXTURE.as_uri())
    assert not fence.allowed(url="https://jobs.example/app.js", method="GET", resource_type="script", navigation=False)


def test_missing_websocket_interceptor_fails_closed():
    with pytest.raises(PrepareBoundaryError, match="PREPARE_WEBSOCKET_FENCE_UNAVAILABLE"):
        PrepareNetworkFence(FIXTURE.as_uri()).install(object())


def test_file_fixture_still_reaches_pre_submit(chromium_config):
    request = request_for(chromium_config, FIXTURE.as_uri())
    result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request)
    assert result.runtime_state.value == "pre_submit_ready"
    assert result.form_value_write_attempts == 7
    assert result.file_upload_attempts == 1
    assert result.submit_attempts == 0


@pytest.mark.parametrize("effect", [
    "fetch('/autosave',{method:'POST',body:this.value}).catch(()=>{})",
    "fetch('/autosave?email='+encodeURIComponent(this.value)).catch(()=>{})",
    "navigator.sendBeacon('/autosave',this.value)",
    "(new Image()).src='/pixel?email='+encodeURIComponent(this.value)",
    "new WebSocket('ws://'+location.host+'/socket')",
    "this.form.action='/submitted'; this.form.submit()",
])
def test_input_network_side_effect_never_reaches_server(chromium_config, effect):
    with local_form(f"firstName.addEventListener('input',function(){{{effect}}})") as (target, seen):
        request = request_for(chromium_config, target)
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request)
        assert result.error_code == "PREPARE_NETWORK_BLOCKED"
        assert result.form_value_write_attempts == 1
        assert result.file_upload_attempts == 0
        assert result.submit_attempts == 0
        assert all(path == "/application" for _, path in seen)
        assert all(method == "GET" for method, _ in seen)
        assert "Canary" not in result.error_message


def test_cv_autosave_blocked_and_attempt_count_preserved(chromium_config):
    script = "cv.addEventListener('change',()=>fetch('/cv-upload',{method:'POST',body:new FormData(applicationForm)}).catch(()=>{}))"
    with local_form(script) as (target, seen):
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request_for(chromium_config, target))
        assert result.error_code == "PREPARE_NETWORK_BLOCKED"
        assert result.form_value_write_attempts == 7
        assert result.file_upload_attempts == 1
        assert result.review_gate_reached is False
        assert all(method == "GET" and path == "/application" for method, path in seen)


@pytest.mark.parametrize("effect,code", [
    ("history.pushState({},'', '/different-job')", "PREPARE_TARGET_DRIFT"),
    ("document.body.insertAdjacentHTML('beforeend','<p>Sign in to continue</p>')", "AUTH_BOUNDARY"),
    ("document.body.insertAdjacentHTML('beforeend','<div id=\"captcha\"></div>')", "CAPTCHA_BOUNDARY"),
    ("lastName.maxLength=1", "PREPARE_FORM_DRIFT"),
    ("window.open('about:blank')", "PREPARE_NETWORK_BLOCKED"),
])
def test_late_boundary_stops_before_second_write(chromium_config, effect, code):
    with local_form(f"firstName.addEventListener('input',()=>{{{effect}}})") as (target, seen):
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request_for(chromium_config, target))
        assert result.error_code == code
        assert result.form_value_write_attempts == 1
        assert result.file_upload_attempts == 0
        assert result.submit_attempts == 0


def test_readback_exception_retains_attempt_and_redacts_private_value(chromium_config, monkeypatch):
    request = request_for(chromium_config, FIXTURE.as_uri())
    readback = PlaywrightSafeFieldWriter._readback
    count = 0

    def failing_readback(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("PRIVATE_VALUE_AND_LOCAL_CV_PATH")
        return readback(*args)

    monkeypatch.setattr(PlaywrightSafeFieldWriter, "_readback", staticmethod(failing_readback))
    result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request)
    assert result.error_code == "SMARTRECRUITERS_EXECUTION_ERROR"
    assert result.form_value_write_attempts == 1
    assert "PRIVATE_VALUE_AND_LOCAL_CV_PATH" not in repr(result)


def test_post_upload_exception_retains_both_counters(chromium_config):
    class FailingRepository:
        def append_artifact(self, artifact):
            raise RuntimeError("PRIVATE_VALUE_AND_LOCAL_CV_PATH")

    request = request_for(chromium_config, FIXTURE.as_uri())
    result = PlaywrightSmartRecruitersExecutor(chromium_config, artifact_repository=FailingRepository()).execute(request)
    assert result.form_value_write_attempts == 7
    assert result.file_upload_attempts == 1
    assert result.submit_attempts == 0
    assert result.review_gate_reached is False
    assert "PRIVATE_VALUE_AND_LOCAL_CV_PATH" not in repr(result)


def test_cv_changed_after_precheck_is_rejected_before_upload(chromium_config, monkeypatch, tmp_path):
    request = request_for(chromium_config, FIXTURE.as_uri())
    private_copy = tmp_path / CV.name
    private_copy.write_bytes(CV.read_bytes())
    plan = request.upload_plan[0]
    request = replace(request, upload_plan=(replace(plan, asset=replace(plan.asset, local_path=str(private_copy))),))
    readback = PlaywrightSafeFieldWriter._readback
    tampered = False

    def mutate_after_initial_precheck(*args):
        nonlocal tampered
        if not tampered:
            private_copy.write_bytes(b"%PDF-UNAPPROVED_CHANGED_BYTES")
            tampered = True
        return readback(*args)

    monkeypatch.setattr(PlaywrightSafeFieldWriter, "_readback", staticmethod(mutate_after_initial_precheck))
    result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request)
    assert result.error_code == "PREPARE_ASSET_CHANGED"
    assert result.form_value_write_attempts == 7
    assert result.file_upload_attempts == 0
    assert result.review_gate_reached is False
    assert str(private_copy) not in repr(result)


def test_same_origin_history_drift_during_initial_render_stops_before_writes(chromium_config):
    request = request_for(chromium_config, FIXTURE.as_uri())
    with local_form("history.replaceState({},'', '/other-requisition')") as (target, seen):
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(replace(request, application_url=target))
        assert result.error_code == "PREPARE_TARGET_DRIFT"
        assert result.form_value_write_attempts == 0
        assert result.file_upload_attempts == 0


def test_service_worker_registration_is_blocked_before_script_request(chromium_config):
    script = "firstName.addEventListener('input',()=>navigator.serviceWorker.register('/worker.js').catch(()=>{}))"
    with local_form(script) as (target, seen):
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request_for(chromium_config, target))
        assert result.runtime_state.value == "pre_submit_ready"
        assert result.form_value_write_attempts == 7
        assert result.file_upload_attempts == 1
        assert all(method == "GET" and path == "/application" for method, path in seen)


def test_locator_identity_drift_cannot_hide_behind_same_semantic_fingerprint(chromium_config):
    script = """firstName.addEventListener('input',()=>{
      document.querySelector('label[for="lastName"]').htmlFor='renamedLast';
      document.getElementById('lastName').id='renamedLast';
    })"""
    with local_form(script) as (target, seen):
        result = PlaywrightSmartRecruitersExecutor(chromium_config).execute(request_for(chromium_config, target))
        assert result.error_code == "PREPARE_FORM_DRIFT"
        assert result.form_value_write_attempts == 1
        assert result.file_upload_attempts == 0
        assert result.submit_attempts == 0
