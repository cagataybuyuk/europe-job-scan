"""Read-only live SmartRecruiters inspection for manifest preparation.

The inspector never fills fields, clicks controls, uploads files, or submits.
It is intentionally separate from the canary executor.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from ejs.services.browser_worker import BrowserRuntimeConfig
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


STRUCTURE_POLL_INTERVAL_MS = 500
STRUCTURE_POLL_BUDGET_MS = 10_000

FRAME_STRUCTURE_EXTRACTOR = r"""
() => {
  const all = Array.from(document.querySelectorAll('*'));
  return {
    element_count: all.length,
    iframe_count: document.querySelectorAll('iframe').length,
    native_control_count: document.querySelectorAll('input,textarea,select,button').length,
    open_shadow_host_count: all.filter(el => !!el.shadowRoot).length,
    custom_element_count: all.filter(el => el.tagName && el.tagName.includes('-')).length
  };
}
"""


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


def _frame_origin(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"
    return parsed.scheme or ""


def _frame_structure(frame) -> dict:
    try:
        raw = frame.evaluate(FRAME_STRUCTURE_EXTRACTOR)
    except Exception as exc:
        return {
            "element_count": 0,
            "iframe_count": 0,
            "native_control_count": 0,
            "open_shadow_host_count": 0,
            "custom_element_count": 0,
            "structure_error": type(exc).__name__,
        }
    keys = (
        "element_count",
        "iframe_count",
        "native_control_count",
        "open_shadow_host_count",
        "custom_element_count",
    )
    return {key: int(raw.get(key, 0) or 0) for key in keys}


def _inspect_frames_once(page) -> tuple[dict, int, list[dict]]:
    """Inspect every current frame and select the richest structural form report."""
    frames = list(page.frames)
    selected_form = None
    selected_index = 0
    selected_score = (-1, -1)

    diagnostics = []
    for index, frame in enumerate(frames):
        structure = _frame_structure(frame)
        try:
            form = inspect_shadow_form(frame)
            control_count = len(form.get("controls", []))
            action_count = len(form.get("actions", []))
            score = (control_count, action_count)
            if score > selected_score:
                selected_form = form
                selected_index = index
                selected_score = score
            error = ""
        except Exception as exc:
            control_count = 0
            action_count = 0
            error = type(exc).__name__

        diagnostics.append(
            {
                "frame_index": index,
                "is_main_frame": index == 0,
                "origin": _frame_origin(getattr(frame, "url", "")),
                "control_count": control_count,
                "action_count": action_count,
                **structure,
                **({"inspection_error": error} if error else {}),
            }
        )

    if selected_form is None:
        selected_form = {
            "adapter_version": "smartrecruiters-shadow-inspection-v1",
            "controls": [],
            "actions": [],
            "schema_fingerprint": "",
            "unscoped_locator_collisions": [],
            "file_control_keys": [],
            "next_observed": False,
            "inspection_only": True,
            "live_execution_ready": False,
            "form_value_write_attempts": 0,
            "file_upload_attempts": 0,
            "submit_attempts": 0,
        }
    return selected_form, selected_index, diagnostics


def _inspect_until_signal(page, poll_budget_ms: int) -> tuple[dict, int, list[dict], int]:
    """Poll read-only frame structure until a form control is observed or budget expires."""
    elapsed_ms = 0
    form, selected_index, diagnostics = _inspect_frames_once(page)
    while not form.get("controls") and elapsed_ms < poll_budget_ms:
        wait_ms = min(STRUCTURE_POLL_INTERVAL_MS, poll_budget_ms - elapsed_ms)
        if wait_ms <= 0:
            break
        page.wait_for_timeout(wait_ms)
        elapsed_ms += wait_ms
        form, selected_index, diagnostics = _inspect_frames_once(page)
    return form, selected_index, diagnostics, elapsed_ms


def _captcha_observed(page, timeout_ms: int) -> bool:
    needles = ("captcha", "verify you are human", "checking your browser")
    for frame in list(page.frames):
        try:
            body = (frame.locator("body").inner_text(timeout=timeout_ms) or "").lower()
        except Exception:
            continue
        if any(needle in body for needle in needles):
            return True
    return False


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
            poll_budget_ms = min(STRUCTURE_POLL_BUDGET_MS, request.timeout_ms)
            report, selected_frame_index, frame_diagnostics, poll_elapsed_ms = _inspect_until_signal(page, poll_budget_ms)
            captcha = _captcha_observed(page, request.timeout_ms)
            return {
                "runtime_state": "captcha_boundary" if captcha else "inspected",
                "requested_url": request.application_url,
                "final_url": page.url,
                "page_title": page.title(),
                "form": report,
                "captcha_observed": captcha,
                "frame_count": len(frame_diagnostics),
                "selected_frame_index": selected_frame_index,
                "frame_diagnostics": frame_diagnostics,
                "structure_poll_elapsed_ms": poll_elapsed_ms,
                "inspection_only": True,
                "form_value_write_attempts": 0,
                "file_upload_attempts": 0,
                "submit_attempts": 0,
            }
        finally:
            context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SmartRecruiters structure inspector")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = inspect_live_page(LiveInspectionRequest(args.application_url, args.timeout_ms))
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("runtime_state") in {"inspected", "captcha_boundary"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LIVE_INSPECTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
