"""Exactly-one-click ADP navigation canary.

The canary is intentionally narrower than safe-fill. It may click one approved
visible application-entry action after stable preflight evidence matches. It
never enters credentials, writes form values, uploads files, solves a
challenge, performs a second click, or submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from ejs.services.adp_live_inspector import (
    classify_adp_state,
    is_challenge_frame_url,
    validate_adp_live_url,
    visible_application_controls,
)
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

CANARY_VERSION = "adp-navigation-canary-v2"
OBSERVATION_KEY_RE = re.compile(r"^document/([a-z][a-z0-9-]*)@(\d+)$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AdpNavigationCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    entry_ordinal: int
    expected_label: str = "Apply"
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_canary_request(request: AdpNavigationCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not FINGERPRINT_RE.fullmatch(request.expected_navigation_surface_fingerprint):
        raise ValueError("INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT")
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if " ".join(request.expected_label.lower().split()) not in {"apply", "apply now"}:
        raise ValueError("ADP_NAVIGATION_CANARY_REQUIRES_APPLY_LABEL")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")


def _normalize(value: str) -> str:
    return " ".join((value or "").lower().split())


def _auth_observed(body_text: str, form: dict) -> bool:
    body = _normalize(body_text)
    signals = (
        "sign in to apply",
        "sign in to continue",
        "create an account",
        "already have an account",
        "forgot your password",
    )
    if any(signal in body for signal in signals):
        return True
    for control in visible_application_controls(form):
        if str(control.get("type", "")).lower() == "password":
            return True
    return False


def navigation_surface_descriptor(snapshot: dict) -> dict:
    entries = snapshot.get("application_entry_actions", [])
    if not isinstance(entries, list):
        entries = []
    signatures = sorted(
        (
            {
                "scope": str(item.get("scope", "")),
                "label": _normalize(str(item.get("label", ""))),
            }
            for item in entries
            if isinstance(item, dict)
        ),
        key=lambda item: (item["scope"], item["label"]),
    )
    controls = snapshot.get("visible_application_control_keys", [])
    if not isinstance(controls, list):
        controls = []
    return {
        "runtime_state": str(snapshot.get("runtime_state", "")),
        "error_code": str(snapshot.get("error_code", "")),
        "captcha_observed": snapshot.get("captcha_observed") is True,
        "visible_application_control_count": len(controls),
        "entry_actions": signatures,
    }


def navigation_surface_fingerprint(snapshot: dict) -> str:
    payload = json.dumps(
        navigation_surface_descriptor(snapshot),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _snapshot(page, *, requested_url: str, timeout_ms: int, render_wait_ms: int) -> dict:
    try:
        page.wait_for_load_state("networkidle", timeout=min(5_000, render_wait_ms))
        network_idle_observed = True
    except Exception:
        network_idle_observed = False

    main_diag, render_wait = _wait_for_render_structure(page, budget_ms=render_wait_ms)
    final_url = page.url
    reports = [scope_shadow_report(inspect_shadow_form(page), "document")]
    frame_diagnostics = []
    challenge_frame_urls: list[str] = []
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
    body_text = page.locator("body").inner_text(timeout=timeout_ms) or ""
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
    state, error_code, entries = classify_adp_state(
        form=form,
        body_text=body_text,
        captcha_observed=captcha,
    )
    snapshot = {
        "runtime_state": state,
        "error_code": error_code,
        "requested_url": requested_url,
        "final_url": final_url,
        "page_title": page.title(),
        "form": form,
        "visible_application_control_keys": [
            control.get("observation_key", "")
            for control in visible_application_controls(form)
        ],
        "application_entry_actions": entries,
        "captcha_observed": captcha,
        "auth_observed": _auth_observed(body_text, form),
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
            "frames": frame_diagnostics,
        },
    }
    snapshot["navigation_surface_descriptor"] = navigation_surface_descriptor(snapshot)
    snapshot["navigation_surface_fingerprint"] = navigation_surface_fingerprint(snapshot)
    return snapshot


def _approved_entry(snapshot: dict, entry_ordinal: int, expected_label: str) -> dict:
    candidates = [
        item for item in snapshot.get("application_entry_actions", [])
        if isinstance(item, dict)
        and item.get("scope") == "document"
        and _normalize(str(item.get("label", ""))) == _normalize(expected_label)
    ]
    if entry_ordinal >= len(candidates):
        raise PermissionError("APPROVED_ADP_ENTRY_ORDINAL_NOT_AVAILABLE")
    candidate = candidates[entry_ordinal]
    if not OBSERVATION_KEY_RE.fullmatch(str(candidate.get("observation_key", ""))):
        raise PermissionError("APPROVED_ADP_ENTRY_OBSERVATION_KEY_UNSUPPORTED")
    return candidate


def _resolve_document_locator(page, observation_key: str):
    match = OBSERVATION_KEY_RE.fullmatch(observation_key)
    if not match:
        raise PermissionError("UNSUPPORTED_ADP_ENTRY_SCOPE")
    expected_tag, index_text = match.groups()
    locator = page.locator("*").nth(int(index_text))
    actual_tag = locator.evaluate("el => el.tagName.toLowerCase()")
    if actual_tag != expected_tag:
        raise PermissionError("ADP_ENTRY_OBSERVATION_KEY_DRIFT")
    return locator


def next_route(snapshot: dict) -> dict:
    common = {
        "automation_resume_allowed": False,
        "safe_fill_allowed": False,
        "final_submit_allowed": False,
    }
    if snapshot.get("captcha_observed") is True:
        return {**common, "route": "human_handoff", "reason_code": "CAPTCHA_BOUNDARY"}
    if snapshot.get("auth_observed") is True:
        return {**common, "route": "human_handoff", "reason_code": "ADP_AUTH_BOUNDARY"}
    if snapshot.get("visible_application_control_keys"):
        return {
            **common,
            "route": "manifest_review_candidate",
            "reason_code": "VISIBLE_APPLICATION_CONTROLS_OBSERVED",
        }
    return {
        **common,
        "route": "diagnostic_review",
        "reason_code": snapshot.get("error_code") or "POST_NAVIGATION_STRUCTURE_NOT_READY",
    }


def _base_report(request: AdpNavigationCanaryRequest) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "navigation_canary_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
        "approved_entry_label": request.expected_label,
        "navigation_click_attempts": 0,
        "navigation_click_successes": 0,
        "credential_entry_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "safe_fill_allowed": False,
        "final_submit_allowed": False,
    }


def _blocked_report(
    request: AdpNavigationCanaryRequest,
    *,
    error_code: str,
    pre_navigation: dict,
    click_attempts: int = 0,
    resolved_observation_key: str = "",
) -> dict:
    return {
        **_base_report(request),
        "canary_status": "blocked",
        "error_code": error_code,
        "pre_navigation": pre_navigation,
        "resolved_entry_observation_key": resolved_observation_key,
        "navigation_click_attempts": click_attempts,
        "next_route": {
            "route": "diagnostic_review",
            "reason_code": error_code,
            "automation_resume_allowed": False,
            "safe_fill_allowed": False,
            "final_submit_allowed": False,
        },
    }


def run_adp_navigation_canary(
    request: AdpNavigationCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_canary_request(request)
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

            pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if pre.get("runtime_state") != "application_entry_observed":
                return _blocked_report(
                    request,
                    error_code="ADP_CANARY_PREFLIGHT_REQUIRES_APPLICATION_ENTRY",
                    pre_navigation=pre,
                )
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _blocked_report(
                    request,
                    error_code="ADP_CANARY_PREFLIGHT_BOUNDARY_OBSERVED",
                    pre_navigation=pre,
                )
            if pre.get("visible_application_control_keys"):
                return _blocked_report(
                    request,
                    error_code="ADP_CANARY_PREFLIGHT_FORM_ALREADY_VISIBLE",
                    pre_navigation=pre,
                )
            actual_surface = pre.get("navigation_surface_fingerprint", "")
            if actual_surface != request.expected_navigation_surface_fingerprint:
                return _blocked_report(
                    request,
                    error_code="ADP_CANARY_PREFLIGHT_SURFACE_FINGERPRINT_MISMATCH",
                    pre_navigation=pre,
                )
            try:
                approved = _approved_entry(pre, request.entry_ordinal, request.expected_label)
                observation_key = str(approved.get("observation_key", ""))
                locator = _resolve_document_locator(page, observation_key)
            except PermissionError as exc:
                return _blocked_report(
                    request,
                    error_code=str(exc),
                    pre_navigation=pre,
                )
            if not locator.is_visible() or not locator.is_enabled():
                return _blocked_report(
                    request,
                    error_code="APPROVED_ADP_ENTRY_NOT_ACTIONABLE",
                    pre_navigation=pre,
                    resolved_observation_key=observation_key,
                )
            actual_label = locator.evaluate(
                "el => ((el.textContent || el.getAttribute('aria-label') || '')).replace(/\\s+/g, ' ').trim()"
            )
            if _normalize(actual_label) != _normalize(request.expected_label):
                return _blocked_report(
                    request,
                    error_code="APPROVED_ADP_ENTRY_LABEL_DRIFT",
                    pre_navigation=pre,
                    resolved_observation_key=observation_key,
                )

            try:
                locator.click(timeout=request.timeout_ms)
            except Exception as exc:
                return _blocked_report(
                    request,
                    error_code=f"ADP_APPROVED_ENTRY_CLICK_FAILED:{type(exc).__name__}",
                    pre_navigation=pre,
                    click_attempts=1,
                    resolved_observation_key=observation_key,
                )

            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                post = {
                    "runtime_state": "unsupported",
                    "error_code": "ADP_NAVIGATION_OPENED_NEW_PAGE",
                    "final_url": page.url,
                    "visible_application_control_keys": [],
                    "captcha_observed": False,
                    "auth_observed": False,
                }
            else:
                post = _snapshot(
                    page,
                    requested_url=request.application_url,
                    timeout_ms=request.timeout_ms,
                    render_wait_ms=request.render_wait_ms,
                )
            route = next_route(post)
            return {
                **_base_report(request),
                "canary_status": "clicked",
                "error_code": "",
                "resolved_entry_observation_key": observation_key,
                "pre_navigation": pre,
                "post_navigation": post,
                "next_route": route,
                "navigation_click_attempts": 1,
                "navigation_click_successes": 1,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP exactly-one-click navigation canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-label", default="Apply")
    parser.add_argument("--output", default="adp-navigation-canary.json")
    args = parser.parse_args()
    request = AdpNavigationCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_label=args.expected_label,
    )
    report = run_adp_navigation_canary(request)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_NAVIGATION_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
