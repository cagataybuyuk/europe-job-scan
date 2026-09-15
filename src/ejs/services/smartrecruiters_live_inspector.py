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
from ejs.services.smartrecruiters_shadow import inspect_shadow_form, summarize_shadow_form


PAGE_STRUCTURE_DIAGNOSTICS = r"""
() => {
  const all = [];
  const visit = (root) => {
    for (const el of Array.from(root.querySelectorAll('*'))) {
      all.push(el);
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(document);

  const tagCounts = new Map();
  const customTags = new Set();
  let openShadowRootCount = 0;
  let customControlHintCount = 0;

  for (const el of all) {
    const tag = el.tagName.toLowerCase();
    tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    if (tag.includes('-')) customTags.add(tag);
    if (el.shadowRoot) openShadowRootCount += 1;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (
      tag.includes('-') &&
      (['textbox','combobox','listbox','radio','checkbox','button'].includes(role) ||
       el.hasAttribute('aria-required') ||
       el.hasAttribute('aria-label'))
    ) customControlHintCount += 1;
  }

  return {
    ready_state: document.readyState,
    body_present: !!document.body,
    body_text_length: document.body ? (document.body.innerText || '').length : 0,
    element_count: all.length,
    form_count: document.querySelectorAll('form').length,
    native_control_count: document.querySelectorAll('input:not([type="hidden"]), textarea, select').length,
    action_candidate_count: document.querySelectorAll('button, input[type="submit"], input[type="button"], a[role="button"]').length,
    iframe_count: document.querySelectorAll('iframe').length,
    script_count: document.querySelectorAll('script').length,
    custom_element_count: Array.from(customTags).reduce((n, tag) => n + (tagCounts.get(tag) || 0), 0),
    custom_element_tags: Array.from(customTags).sort().slice(0, 40),
    open_shadow_root_count: openShadowRootCount,
    custom_control_hint_count: customControlHintCount
  };
}
"""


@dataclass(frozen=True)
class LiveInspectionRequest:
    application_url: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 8_000


def validate_live_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("LIVE_INSPECTION_REQUIRES_HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("LIVE_INSPECTION_REJECTS_EMBEDDED_CREDENTIALS")
    if parsed.hostname != "jobs.smartrecruiters.com" and not (parsed.hostname or "").endswith(".smartrecruiters.com"):
        raise ValueError("LIVE_INSPECTION_REQUIRES_SMARTRECRUITERS")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    port = parsed.port
    default_port = (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80)
    suffix = "" if not port or default_port else f":{port}"
    return f"{parsed.scheme}://{parsed.hostname.lower()}{suffix}"


def same_origin_url(parent_url: str, child_url: str) -> bool:
    if child_url in {"", "about:blank"}:
        return True
    return bool(_origin(parent_url)) and _origin(parent_url) == _origin(child_url)


def _rebase_scope(value: str, scope: str) -> str:
    if value == "document":
        return scope
    if value.startswith("document/"):
        return scope + value[len("document"):]
    return f"{scope}/{value}" if value else scope


def scope_shadow_report(report: dict, scope: str) -> dict:
    controls = []
    for item in report.get("controls", []):
        rebased = dict(item)
        rebased["scope"] = _rebase_scope(item.get("scope", ""), scope)
        rebased["observation_key"] = _rebase_scope(item.get("observation_key", ""), scope)
        controls.append(rebased)
    actions = []
    for item in report.get("actions", []):
        rebased = dict(item)
        rebased["scope"] = _rebase_scope(item.get("scope", ""), scope)
        rebased["observation_key"] = _rebase_scope(item.get("observation_key", ""), scope)
        actions.append(rebased)
    return summarize_shadow_form({"controls": controls, "actions": actions})


def combine_shadow_reports(reports: list[dict]) -> dict:
    controls: list[dict] = []
    actions: list[dict] = []
    for report in reports:
        controls.extend(report.get("controls", []))
        actions.extend(report.get("actions", []))
    return summarize_shadow_form({"controls": controls, "actions": actions})


def discovery_state(form: dict) -> tuple[str, str]:
    if form.get("controls"):
        return "inspected", ""
    return "form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED"


def _wait_for_render_structure(page, *, budget_ms: int) -> tuple[dict, dict]:
    elapsed = 0
    interval_ms = 500
    minimum_wait_ms = min(3_000, budget_ms)
    previous_signature = None
    stable_rounds = 0
    last = page.evaluate(PAGE_STRUCTURE_DIAGNOSTICS)

    while elapsed < budget_ms:
        signature = (
            last.get("ready_state"),
            last.get("element_count"),
            last.get("native_control_count"),
            last.get("action_candidate_count"),
            last.get("iframe_count"),
            tuple(last.get("custom_element_tags", [])),
            last.get("open_shadow_root_count"),
        )
        obvious_structure = (
            last.get("form_count", 0) > 0
            or last.get("native_control_count", 0) > 0
            or last.get("action_candidate_count", 0) > 0
            or last.get("iframe_count", 0) > 0
            or last.get("custom_control_hint_count", 0) > 0
        )
        if obvious_structure and elapsed >= min(500, budget_ms):
            return last, {
                "budget_ms": budget_ms,
                "elapsed_ms": elapsed,
                "exit_reason": "structure_signal_observed",
            }

        if signature == previous_signature and last.get("ready_state") == "complete":
            stable_rounds += 1
        else:
            stable_rounds = 0
        if elapsed >= minimum_wait_ms and stable_rounds >= 3:
            return last, {
                "budget_ms": budget_ms,
                "elapsed_ms": elapsed,
                "exit_reason": "stable_without_structure",
            }

        previous_signature = signature
        wait_ms = min(interval_ms, budget_ms - elapsed)
        if wait_ms <= 0:
            break
        page.wait_for_timeout(wait_ms)
        elapsed += wait_ms
        last = page.evaluate(PAGE_STRUCTURE_DIAGNOSTICS)

    return last, {
        "budget_ms": budget_ms,
        "elapsed_ms": elapsed,
        "exit_reason": "render_wait_budget_exhausted",
    }


def inspect_live_page(request: LiveInspectionRequest, *, config: BrowserRuntimeConfig | None = None) -> dict:
    """Return an allowlisted structural report with explicit zero side effects."""
    validate_live_url(request.application_url)
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_INSPECTION_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
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
        context = None
        try:
            context = browser.new_context(ignore_https_errors=cfg.ignore_https_errors, accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            page.goto(request.application_url, wait_until="domcontentloaded", timeout=request.timeout_ms)

            try:
                page.wait_for_load_state("networkidle", timeout=min(5_000, request.render_wait_ms))
                network_idle_observed = True
            except Exception:
                network_idle_observed = False

            main_diag, render_wait = _wait_for_render_structure(page, budget_ms=request.render_wait_ms)
            final_url = page.url
            reports = [scope_shadow_report(inspect_shadow_form(page), "document")]
            frame_diagnostics = []
            same_origin_frames = 0
            cross_origin_frames = 0

            for index, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                frame_url = frame.url or "about:blank"
                same_origin = same_origin_url(final_url, frame_url)
                meta = {
                    "frame_index": index,
                    "same_origin": same_origin,
                    "origin": _origin(frame_url),
                    "name_present": bool(frame.name),
                }
                if same_origin:
                    same_origin_frames += 1
                    try:
                        meta["structure"] = frame.evaluate(PAGE_STRUCTURE_DIAGNOSTICS)
                        reports.append(scope_shadow_report(inspect_shadow_form(frame), f"frame:{index}/document"))
                        meta["inspection_state"] = "inspected"
                    except Exception as exc:
                        meta["inspection_state"] = "unavailable"
                        meta["error_type"] = type(exc).__name__
                else:
                    cross_origin_frames += 1
                    meta["inspection_state"] = "cross_origin_not_inspected"
                frame_diagnostics.append(meta)

            form = combine_shadow_reports(reports)
            body = (page.locator("body").inner_text(timeout=request.timeout_ms) or "").lower()
            captcha = any(x in body for x in ("captcha", "verify you are human", "checking your browser"))
            state, error_code = discovery_state(form)
            if captcha:
                state, error_code = "captcha_boundary", "CAPTCHA_BOUNDARY"

            diagnostics = {
                "network_idle_observed": network_idle_observed,
                "render_wait": render_wait,
                "main_document": main_diag,
                "frame_count": max(0, len(page.frames) - 1),
                "same_origin_frame_count": same_origin_frames,
                "cross_origin_frame_count": cross_origin_frames,
                "frames": frame_diagnostics,
            }
            return {
                "runtime_state": state,
                "error_code": error_code,
                "requested_url": request.application_url,
                "final_url": final_url,
                "page_title": page.title(),
                "form": form,
                "diagnostics": diagnostics,
                "captcha_observed": captcha,
                "inspection_only": True,
                "live_execution_ready": False,
                "form_value_write_attempts": 0,
                "file_upload_attempts": 0,
                "submit_attempts": 0,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SmartRecruiters structure inspector")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument("--render-wait-ms", type=int, default=8_000)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = inspect_live_page(LiveInspectionRequest(args.application_url, args.timeout_ms, args.render_wait_ms))
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("runtime_state") in {"inspected", "captcha_boundary", "form_structure_not_discovered"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LIVE_INSPECTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
