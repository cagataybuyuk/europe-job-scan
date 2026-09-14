"""Read-only live SmartRecruiters inspection for manifest preparation.

The inspector never fills fields, clicks controls, uploads files, or submits.
It is intentionally separate from the canary executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ejs.services.browser_worker import BrowserRuntimeConfig
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


@dataclass(frozen=True)
class LiveInspectionRequest:
    application_url: str
    timeout_ms: int = 20_000


def validate_live_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("LIVE_INSPECTION_REQUIRES_HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("LIVE_INSPECTION_REJECTS_EMBEDDED_CREDENTIALS")
    if parsed.hostname != "jobs.smartrecruiters.com" and not (parsed.hostname or "").endswith(".smartrecruiters.com"):
        raise ValueError("LIVE_INSPECTION_REQUIRES_SMARTRECRUITERS")


def inspect_live_page(request: LiveInspectionRequest, *, config: BrowserRuntimeConfig | None = None) -> dict:
    """Return an allowlisted structural report with explicit zero side effects."""
    validate_live_url(request.application_url)
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_INSPECTION_TIMEOUT")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"runtime_state": "unsupported", "error_code": "PLAYWRIGHT_UNAVAILABLE", "error_message": str(exc), "inspection_only": True}
    cfg = config or BrowserRuntimeConfig(navigation_timeout_ms=request.timeout_ms, settle_timeout_ms=2_000)
    executable = cfg.resolved_executable_path()
    if not executable and not cfg.use_playwright_managed:
        return {"runtime_state": "unsupported", "error_code": "CHROMIUM_NOT_FOUND", "inspection_only": True}
    with sync_playwright() as p:
        launch = {"headless": cfg.headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if executable:
            launch["executable_path"] = executable
        browser = p.chromium.launch(**launch)
        try:
            context = browser.new_context(ignore_https_errors=cfg.ignore_https_errors, accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            page.goto(request.application_url, wait_until="domcontentloaded", timeout=request.timeout_ms)
            page.wait_for_timeout(min(cfg.settle_timeout_ms, 5_000))
            report = inspect_shadow_form(page)
            body = (page.locator("body").inner_text(timeout=request.timeout_ms) or "").lower()
            captcha = any(x in body for x in ("captcha", "verify you are human", "checking your browser"))
            return {
                "runtime_state": "captcha_boundary" if captcha else "inspected",
                "requested_url": request.application_url,
                "final_url": page.url,
                "page_title": page.title(),
                "form": report,
                "captcha_observed": captcha,
                "inspection_only": True,
                "form_value_write_attempts": 0,
                "file_upload_attempts": 0,
                "submit_attempts": 0,
            }
        finally:
            context.close()
            browser.close()
