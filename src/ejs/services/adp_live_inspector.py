"""Read-only ADP Workforce Now inspection for application routing.

The inspector may navigate only to the exact approved URL. It never clicks
Apply, fills fields, enters credentials, uploads files, solves challenges, or
submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from ejs.services.browser_worker import BrowserRuntimeConfig
from ejs.services.smartrecruiters_live_inspector import (
    PAGE_STRUCTURE_DIAGNOSTICS,
    _origin,
    _wait_for_render_structure,
    combine_shadow_reports,
    same_origin_url,
    scope_shadow_report,
)
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


ADP_HOST = "workforcenow.adp.com"
ENTRY_LABELS = frozenset({"apply", "apply now", "start application", "apply for this job"})
CAPTCHA_HOST_SUFFIXES = (
    "captcha-delivery.com",
    "recaptcha.net",
    "hcaptcha.com",
    "arkoselabs.com",
    "funcaptcha.com",
)


@dataclass(frozen=True)
class AdpLiveInspectionRequest:
    application_url: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_adp_live_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("ADP_LIVE_INSPECTION_REQUIRES_HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("ADP_LIVE_INSPECTION_REJECTS_EMBEDDED_CREDENTIALS")
    if (parsed.hostname or "").lower() != ADP_HOST:
        raise ValueError("ADP_LIVE_INSPECTION_REQUIRES_WORKFORCENOW_ADP")


def is_challenge_frame_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in CAPTCHA_HOST_SUFFIXES):
        return True
    if (host == "google.com" or host.endswith(".google.com")) and "recaptcha" in path:
        return True
    return False


def _normalized_action_label(action: dict) -> str:
    return " ".join(str(action.get("label", "")).lower().split())


def application_entry_actions(form: dict) -> list[dict]:
    result = []
    for action in form.get("actions", []):
        label = _normalized_action_label(action)
        if action.get("visible") and label in ENTRY_LABELS:
            result.append({
                "scope": action.get("scope", ""),
                "observation_key": action.get("observation_key", ""),
                "label": action.get("label", ""),
            })
    return result


def classify_adp_state(*, form: dict, body_text: str, captcha_observed: bool) -> tuple[str, str, list[dict]]:
    entries = application_entry_actions(form)
    if captcha_observed:
        return "captcha_boundary", "CAPTCHA_BOUNDARY", entries
    if form.get("controls"):
        return "inspected", "", entries
    if entries:
        return "application_entry_observed", "APPLICATION_ENTRY_REQUIRES_NAVIGATION", entries
    body = " ".join((body_text or "").lower().split())
    auth_signals = (
        "sign in to apply",
        "sign in to continue",
        "create an account",
        "already have an account",
        "forgot your password",
    )
    if any(signal in body for signal in auth_signals):
        return "auth_boundary", "ADP_AUTH_BOUNDARY", entries
    return "form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED", entries


def inspect_adp_live_page(
    request: AdpLiveInspectionRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_adp_live_url(request.application_url)
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_INSPECTION_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "runtime_state": "unsupported",
            "error_code": "PLAYWRIGHT_UNAVAILABLE",
            "error_message": str(exc),
            "inspection_only": True,
            "live_execution_ready": False,
        }

    cfg = config or BrowserRuntimeConfig(
        navigation_timeout_ms=request.timeout_ms,
        settle_timeout_ms=2_000,
    )
    executable = cfg.resolved_executable_path()
    if not executable and not cfg.use_playwright_managed:
        return {
            "runtime_state": "unsupported",
            "error_code": "CHROMIUM_NOT_FOUND",
            "inspection_only": True,
            "live_execution_ready": False,
        }

    with sync_playwright() as p:
        launch = {
            "headless": cfg.headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        if executable:
            launch["executable_path"] = executable
        browser = p.chromium.launch(**launch)
        context = None
        try:
            context = browser.new_context(
                ignore_https_errors=cfg.ignore_https_errors,
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            page.goto(
                request.application_url,
                wait_until="domcontentloaded",
                timeout=request.timeout_ms,
            )

            try:
                page.wait_for_load_state(
                    "networkidle",
                    timeout=min(5_000, request.render_wait_ms),
                )
                network_idle_observed = True
            except Exception:
                network_idle_observed = False

            main_diag, render_wait = _wait_for_render_structure(
                page,
                budget_ms=request.render_wait_ms,
            )
            final_url = page.url
            reports = [scope_shadow_report(inspect_shadow_form(page), "document")]
            frame_diagnostics = []
            same_origin_frames = 0
            cross_origin_frames = 0
            challenge_frame_urls: list[str] = []

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
                        reports.append(
                            scope_shadow_report(
                                inspect_shadow_form(frame),
                                f"frame:{index}/document",
                            )
                        )
                        meta["inspection_state"] = "inspected"
                    except Exception as exc:
                        meta["inspection_state"] = "unavailable"
                        meta["error_type"] = type(exc).__name__
                else:
                    cross_origin_frames += 1
                    meta["inspection_state"] = "cross_origin_not_inspected"
                    if is_challenge_frame_url(frame_url):
                        meta["challenge_hint"] = "captcha"
                        challenge_frame_urls.append(frame_url)
                frame_diagnostics.append(meta)

            form = combine_shadow_reports(reports)
            body_text = page.locator("body").inner_text(timeout=request.timeout_ms) or ""
            body_lower = body_text.lower()
            body_captcha = any(
                signal in body_lower
                for signal in (
                    "captcha",
                    "verify you are human",
                    "checking your browser",
                    "security check",
                )
            )
            captcha = body_captcha or bool(challenge_frame_urls)
            state, error_code, entry_actions = classify_adp_state(
                form=form,
                body_text=body_text,
                captcha_observed=captcha,
            )

            captcha_evidence = []
            if body_captcha:
                captcha_evidence.append("body_text")
            if challenge_frame_urls:
                captcha_evidence.append("known_challenge_frame")

            return {
                "adapter": "adp-workforcenow",
                "adapter_version": "adp-live-inspection-v1",
                "runtime_state": state,
                "error_code": error_code,
                "requested_url": request.application_url,
                "final_url": final_url,
                "page_title": page.title(),
                "form": form,
                "application_entry_actions": entry_actions,
                "diagnostics": {
                    "network_idle_observed": network_idle_observed,
                    "render_wait": render_wait,
                    "main_document": main_diag,
                    "frame_count": max(0, len(page.frames) - 1),
                    "same_origin_frame_count": same_origin_frames,
                    "cross_origin_frame_count": cross_origin_frames,
                    "challenge_frame_origins": sorted(
                        {_origin(url) for url in challenge_frame_urls if _origin(url)}
                    ),
                    "captcha_evidence": captcha_evidence,
                    "frames": frame_diagnostics,
                },
                "captcha_observed": captcha,
                "inspection_only": True,
                "live_execution_ready": False,
                "navigation_click_attempts": 0,
                "credential_entry_attempts": 0,
                "form_value_write_attempts": 0,
                "file_upload_attempts": 0,
                "submit_attempts": 0,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ADP Workforce Now inspector")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument("--render-wait-ms", type=int, default=10_000)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = inspect_adp_live_page(
        AdpLiveInspectionRequest(
            args.application_url,
            args.timeout_ms,
            args.render_wait_ms,
        )
    )
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    acceptable = {
        "inspected",
        "application_entry_observed",
        "auth_boundary",
        "captcha_boundary",
        "form_structure_not_discovered",
    }
    return 0 if report.get("runtime_state") in acceptable else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_LIVE_INSPECTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
