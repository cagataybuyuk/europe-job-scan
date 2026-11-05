"""Actual synthetic browser readback feeds durable intent; no submit is issued."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.test_durable_submission import receipts, ON, CV, URL
from ejs.apps.github_runner.safe_fill_upload_canary import _fixture_plans
from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionRequest
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.smartrecruiters_executor import PlaywrightSmartRecruitersExecutor
from ejs.services.durable_submission import DurableSingleSubmission
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.prepare_network_fence import PrepareNetworkFence


def test_synthetic_readback_integrates_with_one_durable_intent_without_submit(tmp_path, monkeypatch):
    with sync_playwright() as p:
        executable = p.chromium.executable_path
    assert Path(executable).is_file(), 'Managed Chromium required for this integration proof'
    cfg = BrowserRuntimeConfig(executable_path=executable, settle_timeout_ms=20)
    target = URL
    html = CV.with_name('smartrecruiters_e2e_form.html').read_text().replace('</head>', '<link rel="icon" href="data:,"></head>')
    intercepted = []
    original_install = PrepareNetworkFence.install
    def synthetic_transport(fence, context):
        original_install(fence, context)
        def route_only_synthetic(route):
            intercepted.append((route.request.method, route.request.url))
            if route.request.url == target and route.request.method == 'GET' and route.request.is_navigation_request():
                route.fulfill(status=200, content_type='text/html', body=html)
            else:
                fence.deny('TEST_UNEXPECTED_REQUEST')
                route.abort()
        context.route('**/*', route_only_synthetic)
    monkeypatch.setattr(PrepareNetworkFence, 'install', synthetic_transport)
    now = datetime.now(timezone.utc)
    request, _ = receipts()
    inspector = PlaywrightBrowserWorker(cfg).inspect(BrowserInspectionRequest(
        'fixture:bridge', 'fixture:route', request.opportunity_key, request.requisition_id,
        CV.with_name('smartrecruiters_e2e_form.html').as_uri(), 'ats:smartrecruiters', now.isoformat()))
    assert inspector.form_fingerprint
    request = replace(request, apply_url=target, form_fingerprint=inspector.form_fingerprint)
    plans, uploads, _ = _fixture_plans(CV)
    preparation = SmartRecruitersExecutionRequest(
        request.execution_id, 'fixture:bridge', 'fixture:route', request.opportunity_key, request.requisition_id,
        target, now.isoformat(), request.form_fingerprint, request.candidate_profile_version,
        'To Apply', 'High', 0, plans, uploads, request.controls)
    observed = PlaywrightSmartRecruitersExecutor(cfg).execute(preparation)
    assert observed.runtime_state.value == 'pre_submit_ready'
    assert observed.submit_attempts == 0
    assert intercepted == [('GET', target)]  # fulfilled locally; no employer request sent
    coordinator = DurableSingleSubmission(SqliteSubmissionLedger(tmp_path / 'private' / 'intent.sqlite3'))
    assert coordinator.reserve(request, observed, observed_at=now)['status'] == 'RUNTIME_DISABLED'
    assert not coordinator.ledger.has_intent()
    assert coordinator.reserve(request, observed, observed_at=now, authority=ON)['status'] == 'INTENT_RECORDED'
    result = coordinator.record_before_dispatch(request, observed, observed_at=now, authority=ON)
    assert result['status'] == 'DISPATCH_INTENT_RECORDED'
    assert result['network_action_performed'] is False
    assert result['applications_write_performed'] is False
    assert coordinator.reserve(request, observed, observed_at=now, authority=ON)['status'] == 'DUPLICATE_OR_SINGLE_SCOPE_CONSUMED'
