"""Read-only ADP inspector using a previously user-verified browser session.

Authority is limited to loading protected Playwright storage state, opening the
reviewed ADP target and clicking the already-reviewed Apply entry once. It
performs zero field writes, zero credential entry, zero upload and zero Submit.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from ejs.services.adp_live_inspector import validate_adp_live_url, visible_application_controls
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _approved_entry,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.adp_continue_diagnostic_canary import VERIFICATION_CODE_CONTROL_ID
from ejs.services.adp_continue_canary import action_surface_descriptor
from ejs.services.browser_worker import BrowserRuntimeConfig

INSPECTOR_VERSION = "adp-verified-session-inspector-v3"
IDENTITY_CONTROL_IDS = {"guestFirstName", "guestLastName", "guestEmail"}
COOKIE_POLL_MS = 250
COOKIE_CLEAR_STABLE_MS = 1_000


@dataclass(frozen=True)
class AdpVerifiedSessionInspectorRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    entry_ordinal: int
    storage_state_json_path: str
    session_storage_json_path: str = ""
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpVerifiedSessionInspectorRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not FINGERPRINT_RE.fullmatch(request.expected_navigation_surface_fingerprint):
        raise ValueError("INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT")
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if not request.storage_state_json_path:
        raise ValueError("ADP_VERIFIED_SESSION_STORAGE_STATE_REQUIRED")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_INSPECTOR_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_INSPECTOR_RENDER_WAIT")


def _cookie_visibility(page) -> dict:
    """Observe exact reviewed OneTrust containers; no labels or cookie values."""
    try:
        visible = {}
        for key, selector in (
            ("banner_visible", "#onetrust-banner-sdk"),
            ("preference_center_visible", "#onetrust-pc-sdk"),
        ):
            locator = page.locator(selector)
            visible[key] = any(locator.nth(i).is_visible() for i in range(locator.count()))
        return {"observation_succeeded": True, **visible}
    except Exception:
        return {
            "observation_succeeded": False,
            "banner_visible": None,
            "preference_center_visible": None,
        }


def _cookie_surface_clear(surface: dict) -> bool:
    return (
        surface.get("observation_succeeded") is True
        and surface.get("banner_visible") is False
        and surface.get("preference_center_visible") is False
    )


def _observe_cookie_settling(page, budget_ms: int) -> dict:
    # Observe the entire bounded window, even if absent initially: OneTrust may
    # be injected later, or briefly render before restored consent is applied.
    initial = _cookie_visibility(page)
    current = initial
    elapsed = 0
    clear_ms = 0
    failures = int(not initial["observation_succeeded"])
    visible_seen = initial["banner_visible"] is True or initial["preference_center_visible"] is True
    while elapsed < budget_ms:
        interval = min(COOKIE_POLL_MS, budget_ms - elapsed)
        page.wait_for_timeout(interval)
        following = _cookie_visibility(page)
        if _cookie_surface_clear(current) and _cookie_surface_clear(following):
            clear_ms += interval
        else:
            clear_ms = 0
        elapsed += interval
        failures += int(not following["observation_succeeded"])
        visible_seen = visible_seen or following["banner_visible"] is True or following["preference_center_visible"] is True
        current = following
    return {
        "initial": initial,
        "final": current,
        "observation_window_ms": elapsed,
        "clear_stable_ms": clear_ms,
        "visible_during_observation": visible_seen,
        "observation_error_count": failures,
        "surface_clear": _cookie_surface_clear(current) and clear_ms >= COOKIE_CLEAR_STABLE_MS,
        "cookie_click_attempts": 0,
        "raw_values_exposed": False,
    }


def validate_storage_state(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ADP_VERIFIED_SESSION_STORAGE_STATE_INVALID")
    cookies = raw.get("cookies")
    origins = raw.get("origins")
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("ADP_VERIFIED_SESSION_STORAGE_STATE_SHAPE_INVALID")
    return {
        "cookie_count": len(cookies),
        "origin_count": len(origins),
        "raw_storage_state_exposed": False,
    }


def _load_session_storage(path: str) -> tuple[dict[str, str], dict]:
    if not path:
        return {}, {
            "session_storage_loaded": False,
            "session_storage_entry_count": 0,
            "session_storage_byte_count": 0,
            "raw_session_storage_exposed": False,
        }
    payload = Path(path).read_text(encoding="utf-8")
    byte_count = len(payload.encode("utf-8"))
    if byte_count > 47_000:
        raise ValueError("ADP_VERIFIED_SESSION_SESSION_STORAGE_TOO_LARGE")
    raw = json.loads(payload)
    if not isinstance(raw, dict) or len(raw) > 200:
        raise ValueError("ADP_VERIFIED_SESSION_SESSION_STORAGE_INVALID")
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("ADP_VERIFIED_SESSION_SESSION_STORAGE_INVALID")
        normalized[key] = value
    return normalized, {
        "session_storage_loaded": True,
        "session_storage_entry_count": len(normalized),
        "session_storage_byte_count": byte_count,
        "raw_session_storage_exposed": False,
    }


def _session_storage_init_script(application_url: str, storage: dict[str, str]) -> str:
    hostname = urlsplit(application_url).hostname or ""
    hostname_json = json.dumps(hostname, ensure_ascii=False)
    storage_json = json.dumps(storage, ensure_ascii=False, sort_keys=True)
    return (
        "(() => {"
        f"if (window.location.hostname !== {hostname_json}) return;"
        f"const restored = {storage_json};"
        "for (const [key, value] of Object.entries(restored)) "
        "window.sessionStorage.setItem(key, value);"
        "})();"
    )


def _surface_descriptor(page, snapshot: dict) -> dict:
    controls = visible_application_controls(snapshot.get("form", {}))
    visible = [
        {
            "tag": str(c.get("tag", "")),
            "type": str(c.get("type", "")),
            "id": str(c.get("id", "")),
            "name": str(c.get("name", "")),
            "label": str(c.get("label", "")),
            "required": c.get("required") is True,
            "disabled": c.get("disabled") is True,
            "accept": str(c.get("accept", "")),
            "multiple": c.get("multiple") is True,
        }
        for c in controls
    ]
    ids = {item["id"] for item in visible if item["id"]}
    otp_present = VERIFICATION_CODE_CONTROL_ID in ids
    identity_present = bool(IDENTITY_CONTROL_IDS.intersection(ids))
    file_controls = [item for item in visible if item["type"].casefold() == "file"]
    return {
        "visible_application_control_count": len(visible),
        "visible_application_controls": visible,
        "visible_button_surface": action_surface_descriptor(page),
        "verification_code_surface_present": otp_present,
        "guest_identity_surface_present": identity_present,
        "file_control_count": len(file_controls),
        "raw_values_exposed": False,
    }


def run_inspector(
    request: AdpVerifiedSessionInspectorRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    storage_evidence = validate_storage_state(request.storage_state_json_path)
    session_storage, session_storage_evidence = _load_session_storage(request.session_storage_json_path)
    base = {
        "inspector_version": INSPECTOR_VERSION,
        "verified_session_inspector_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "entry_ordinal": request.entry_ordinal,
        "storage_state_loaded": True,
        "storage_evidence": storage_evidence,
        "session_storage_evidence": session_storage_evidence,
        "navigation_click_attempts": 0,
        "navigation_click_successes": 0,
        "form_value_write_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "final_submit_allowed": False,
        "file_upload_allowed": False,
    }

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
            context = browser.new_context(
                storage_state=request.storage_state_json_path,
                ignore_https_errors=cfg.ignore_https_errors,
                accept_downloads=False,
            )
            if session_storage:
                context.add_init_script(
                    script=_session_storage_init_script(request.application_url, session_storage)
                )
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            page.goto(request.application_url, wait_until="domcontentloaded", timeout=request.timeout_ms)

            cookie_gate = _observe_cookie_settling(page, request.render_wait_ms)
            base["cookie_gate"] = cookie_gate
            if not cookie_gate["surface_clear"]:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED",
                }

            pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            # Rendering can outlast the observation window. Recheck immediately
            # before the reviewed navigation; never click through a late banner.
            pre_apply_cookie_surface = _cookie_visibility(page)
            cookie_gate["pre_apply"] = pre_apply_cookie_surface
            if not _cookie_surface_clear(pre_apply_cookie_surface):
                cookie_gate["surface_clear"] = False
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED",
                }
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_PREFLIGHT_BOUNDARY_OBSERVED",
                }
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_NAVIGATION_SURFACE_MISMATCH",
                }

            try:
                approved = _approved_entry(pre, request.entry_ordinal, "Apply")
                entry = _resolve_document_locator(page, str(approved.get("observation_key", "")))
                base["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                base["navigation_click_successes"] = 1
            except Exception as exc:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": f"ADP_VERIFIED_SESSION_APPLY_CLICK_FAILED:{type(exc).__name__}",
                }

            page.wait_for_timeout(2_000)
            if len(context.pages) != 1:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_APPLY_OPENED_NEW_PAGE",
                }

            post = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if post.get("captcha_observed") is True or post.get("auth_observed") is True:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_POST_APPLY_BOUNDARY_OBSERVED",
                }
            surface = _surface_descriptor(page, post)
            if surface["verification_code_surface_present"]:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_REQUIRES_VERIFICATION",
                    "post_apply_surface": surface,
                }
            if surface["guest_identity_surface_present"]:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_NOT_RECOGNIZED_IDENTITY_SURFACE",
                    "post_apply_surface": surface,
                }
            if surface["visible_application_control_count"] == 0:
                return {
                    **base,
                    "inspector_status": "blocked",
                    "error_code": "ADP_VERIFIED_SESSION_POST_APPLY_SURFACE_EMPTY",
                    "post_apply_surface": surface,
                }
            return {
                **base,
                "inspector_status": "inspected",
                "error_code": "",
                "session_reused": True,
                "post_apply_url": str(page.url),
                "post_apply_surface": surface,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ADP verified-session inspector")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", type=int, required=True)
    parser.add_argument("--storage-state-json", required=True, dest="storage_state_json_path")
    parser.add_argument("--session-storage-json", default="", dest="session_storage_json_path")
    parser.add_argument("--output", default="adp-verified-session-inspector.json")
    parser.add_argument("--playwright-managed", action="store_true",
                        help="Use installed Playwright Chromium (including on Windows)")
    args = parser.parse_args()
    report = run_inspector(AdpVerifiedSessionInspectorRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        storage_state_json_path=args.storage_state_json_path,
        session_storage_json_path=args.session_storage_json_path,
    ), config=BrowserRuntimeConfig(use_playwright_managed=True) if args.playwright_managed else None)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "inspector_status": report.get("inspector_status"),
        "error_code": report.get("error_code"),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "credential_entry_attempts": report.get("credential_entry_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
        "storage_evidence": report.get("storage_evidence"),
        "session_storage_evidence": report.get("session_storage_evidence"),
        "cookie_gate": report.get("cookie_gate"),
    }, sort_keys=True))
    return 0 if report.get("inspector_status") == "inspected" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_VERIFIED_SESSION_INSPECTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
