"""One-click ADP OneTrust preference-center inspection canary.

Authority is limited to exactly one reviewed click on the exact OneTrust
banner preference-center opener `#onetrust-pc-btn-handler`. Two live-observed
accessible-label variants are accepted for that same exact element. After that
click the canary only inspects the visible preference-center control surface. It never changes a
cookie toggle, saves/accepts/rejects preferences, clicks Apply, writes candidate
fields, uploads a file, enters credentials, solves a challenge, or submits.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-cookie-preferences-canary-v1"
PREFERENCES_LABEL = "To manage your preferences, click here"
PREFERENCES_BUTTON_ID = "onetrust-pc-btn-handler"
PREFERENCES_REVIEWED_LABEL_PREFIXES = (
    "Set your preferences",
    "To manage your preferences, click here",
)


@dataclass(frozen=True)
class AdpCookiePreferencesCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def _is_reviewed_preferences_label(value: str) -> bool:
    normalized = _normalize(value).casefold()
    return any(
        normalized.startswith(_normalize(prefix).casefold())
        for prefix in PREFERENCES_REVIEWED_LABEL_PREFIXES
    )


def reviewed_preferences_button(page):
    candidate = page.locator(f"#{PREFERENCES_BUTTON_ID}")
    if candidate.count() != 1:
        raise PermissionError("ADP_COOKIE_PREFS_REVIEWED_CONTROL_NOT_UNIQUE")
    if not candidate.is_visible() or not candidate.is_enabled():
        raise PermissionError("ADP_COOKIE_PREFS_REVIEWED_CONTROL_NOT_ACTIONABLE")
    actual = candidate.get_attribute("aria-label") or candidate.inner_text() or ""
    if not _is_reviewed_preferences_label(actual):
        raise PermissionError("ADP_COOKIE_PREFS_REVIEWED_CONTROL_LABEL_DRIFT")
    return candidate


def validate_request(request: AdpCookiePreferencesCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not FINGERPRINT_RE.fullmatch(request.expected_navigation_surface_fingerprint):
        raise ValueError("INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")


def _visible_button_evidence(page) -> list[dict]:
    rows: list[dict] = []
    buttons = page.get_by_role("button")
    for index in range(min(buttons.count(), 50)):
        locator = buttons.nth(index)
        try:
            if not locator.is_visible():
                continue
            label = locator.get_attribute("aria-label") or locator.inner_text() or ""
            label = _normalize(label)
            if not label:
                continue
            rows.append({
                "ordinal": index,
                "id": str(locator.get_attribute("id") or ""),
                "label": label[:160],
                "enabled": locator.is_enabled(),
            })
        except Exception:
            continue
    rows.sort(key=lambda item: (item["id"], item["label"], item["ordinal"]))
    return rows


def _visible_checkbox_evidence(page) -> list[dict]:
    rows: list[dict] = []
    boxes = page.locator('input[type="checkbox"]')
    for index in range(min(boxes.count(), 50)):
        locator = boxes.nth(index)
        try:
            if not locator.is_visible():
                continue
            element_id = str(locator.get_attribute("id") or "")
            aria = str(locator.get_attribute("aria-label") or "")
            label = ""
            if element_id:
                associated = page.locator(f'label[for="{element_id}"]')
                if associated.count() == 1:
                    label = _normalize(associated.inner_text() or "")
            rows.append({
                "ordinal": index,
                "id": element_id,
                "label": label or _normalize(aria),
                "checked": locator.is_checked(),
                "enabled": locator.is_enabled(),
            })
        except Exception:
            continue
    rows.sort(key=lambda item: (item["id"], item["label"], item["ordinal"]))
    return rows


def preference_surface_descriptor(page) -> dict:
    center = page.locator("#onetrust-pc-sdk")
    banner = page.locator("#onetrust-banner-sdk")
    return {
        "preference_center_present": center.count() == 1,
        "preference_center_visible": center.count() == 1 and center.is_visible(),
        "banner_present": banner.count() == 1,
        "banner_visible": banner.count() == 1 and banner.is_visible(),
        "visible_buttons": _visible_button_evidence(page),
        "visible_checkboxes": _visible_checkbox_evidence(page),
    }


def preference_surface_fingerprint(descriptor: dict) -> str:
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _base_report(request: AdpCookiePreferencesCanaryRequest) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "cookie_preferences_inspection_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "preference_navigation_click_attempts": 0,
        "preference_navigation_click_successes": 0,
        "cookie_toggle_write_attempts": 0,
        "cookie_save_attempts": 0,
        "cookie_accept_attempts": 0,
        "cookie_reject_attempts": 0,
        "application_navigation_click_attempts": 0,
        "credential_entry_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "safe_fill_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def run_adp_cookie_preferences_canary(
    request: AdpCookiePreferencesCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    base = _base_report(request)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("PLAYWRIGHT_UNAVAILABLE") from exc

    cfg = config or BrowserRuntimeConfig(
        navigation_timeout_ms=request.timeout_ms,
        settle_timeout_ms=2_000,
    )
    executable = cfg.resolved_executable_path()
    if not executable and not cfg.use_playwright_managed:
        raise RuntimeError("CHROMIUM_NOT_FOUND")

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

            pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if pre.get("runtime_state") != "application_entry_observed":
                return {**base, "canary_status": "blocked", "error_code": "ADP_COOKIE_PREFS_REQUIRES_APPLICATION_ENTRY", "pre_navigation": pre}
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return {**base, "canary_status": "blocked", "error_code": "ADP_COOKIE_PREFS_BOUNDARY_OBSERVED", "pre_navigation": pre}
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return {**base, "canary_status": "blocked", "error_code": "ADP_COOKIE_PREFS_NAVIGATION_SURFACE_MISMATCH", "pre_navigation": pre}

            try:
                candidate = reviewed_preferences_button(page)
            except PermissionError as exc:
                return {**base, "canary_status": "blocked", "error_code": str(exc), "pre_navigation": pre}

            try:
                candidate.click(timeout=request.timeout_ms)
            except Exception as exc:
                return {
                    **base,
                    "canary_status": "blocked",
                    "error_code": f"ADP_COOKIE_PREFS_CLICK_FAILED:{type(exc).__name__}",
                    "pre_navigation": pre,
                    "preference_navigation_click_attempts": 1,
                }
            page.wait_for_timeout(500)
            descriptor = preference_surface_descriptor(page)
            fingerprint = preference_surface_fingerprint(descriptor)
            if not descriptor["preference_center_visible"]:
                return {
                    **base,
                    "canary_status": "blocked",
                    "error_code": "ADP_COOKIE_PREFS_CENTER_NOT_VISIBLE",
                    "pre_navigation": pre,
                    "post_preference_surface": descriptor,
                    "post_preference_surface_fingerprint": fingerprint,
                    "preference_navigation_click_attempts": 1,
                    "preference_navigation_click_successes": 1,
                }
            return {
                **base,
                "canary_status": "inspected",
                "error_code": "",
                "pre_navigation": pre,
                "post_preference_surface": descriptor,
                "post_preference_surface_fingerprint": fingerprint,
                "preference_navigation_click_attempts": 1,
                "preference_navigation_click_successes": 1,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP OneTrust preference-center one-click inspection canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--output", default="adp-cookie-preferences-canary.json")
    args = parser.parse_args()
    report = run_adp_cookie_preferences_canary(AdpCookiePreferencesCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
    ))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "preference_navigation_click_attempts": report.get("preference_navigation_click_attempts", 0),
        "cookie_toggle_write_attempts": report.get("cookie_toggle_write_attempts", 0),
        "application_navigation_click_attempts": report.get("application_navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "inspected" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_COOKIE_PREFERENCES_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
