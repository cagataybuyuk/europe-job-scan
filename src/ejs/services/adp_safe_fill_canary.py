"""Bounded ADP identity safe-fill canary.

Authority is limited to one reviewed Apply navigation click followed by exactly
three AUTO_SAFE identity writes: first name, last name, and email. When a known
OneTrust cookie banner blocks the reviewed Apply action, a separately approved
privacy-preserving policy may click only the exact "Deny" non-essential-cookie
control before the Apply click.

It never accepts optional cookies, enters credentials, changes phone/country,
uploads files, clicks another application action, bypasses a challenge, or
submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, SafeFieldWriterAuthority, value_hash
from ejs.services.adp_live_inspector import validate_adp_live_url, visible_application_controls
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _approved_entry,
    _normalize,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-safe-fill-canary-v2"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
COOKIE_POLICY_BLOCK = "block"
COOKIE_POLICY_DENY_OPTIONAL = "deny_optional"
COOKIE_POLICIES = frozenset({COOKIE_POLICY_BLOCK, COOKIE_POLICY_DENY_OPTIONAL})
ONETRUST_BANNER_SELECTOR = "#onetrust-banner-sdk"
ONETRUST_REJECT_SELECTOR = "#onetrust-reject-all-handler"
ONETRUST_ACCEPT_SELECTOR = "#onetrust-accept-btn-handler"
ONETRUST_REJECT_LABEL = "deny"
ONETRUST_ACCEPT_LABEL = "agree and proceed"
TARGETS = (
    ("candidate.first_name", "guestFirstName", "First Name", "text", True),
    ("candidate.last_name", "guestLastName", "Last Name", "text", True),
    ("candidate.email", "guestEmail", "Email", "text", True),
)


@dataclass(frozen=True)
class AdpSafeFillCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    profile_manifest_path: str
    expected_label: str = "Apply"
    cookie_policy: str = COOKIE_POLICY_BLOCK
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def _normalize_label(value: str) -> str:
    return " ".join((value or "").lower().split())


def validate_request(request: AdpSafeFillCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value, code in (
        (request.expected_navigation_surface_fingerprint, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"),
        (request.expected_safe_fill_surface_fingerprint, "INVALID_EXPECTED_SAFE_FILL_SURFACE_FINGERPRINT"),
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError(code)
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if _normalize(request.expected_label) not in {"apply", "apply now"}:
        raise ValueError("ADP_SAFE_FILL_REQUIRES_APPLY_LABEL")
    if request.cookie_policy not in COOKIE_POLICIES:
        raise ValueError("INVALID_ADP_COOKIE_POLICY")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
    if not request.profile_manifest_path:
        raise ValueError("ADP_SAFE_FILL_REQUIRES_PROFILE_MANIFEST")
    SafeFieldWriterAuthority().validate()
    if not {x[0] for x in TARGETS}.issubset(SAFE_VERIFIED_FIELDS):
        raise PermissionError("ADP_SAFE_FILL_TARGET_NOT_AUTO_SAFE")


def load_identity_profile(path: str) -> tuple[dict[str, str], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    field_plan = data.get("field_plan", [])
    if not isinstance(field_plan, list):
        raise ValueError("PROFILE_MANIFEST_FIELD_PLAN_INVALID")
    wanted = {item[0] for item in TARGETS}
    found: dict[str, list[str]] = {key: [] for key in wanted}
    for item in field_plan:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical_field", ""))
        if canonical in wanted:
            found[canonical].append(str(item.get("value", "")))
    profile: dict[str, str] = {}
    for canonical in sorted(wanted):
        values = found[canonical]
        if len(values) != 1 or not values[0].strip():
            raise ValueError(f"PROFILE_MANIFEST_REQUIRES_EXACT_{canonical.upper().replace('.', '_')}")
        profile[canonical] = values[0]
    if not EMAIL_RE.fullmatch(profile["candidate.email"]):
        raise ValueError("PROFILE_MANIFEST_EMAIL_INVALID")
    return profile, str(data.get("candidate_profile_version", ""))


def safe_fill_surface_descriptor(snapshot: dict) -> dict:
    controls = visible_application_controls(snapshot.get("form", {}))
    signatures = []
    for control in controls:
        signatures.append({
            "tag": str(control.get("tag", "")),
            "type": str(control.get("type", "")),
            "id": str(control.get("id", "")),
            "name": str(control.get("name", "")),
            "label": _normalize_label(str(control.get("label", ""))),
            "required": control.get("required") is True,
            "disabled": control.get("disabled") is True,
            "accept": str(control.get("accept", "")),
            "multiple": control.get("multiple") is True,
        })
    signatures.sort(key=lambda item: (
        item["id"], item["name"], item["label"], item["type"], item["tag"]
    ))
    return {
        "runtime_state": str(snapshot.get("runtime_state", "")),
        "captcha_observed": snapshot.get("captcha_observed") is True,
        "auth_observed": snapshot.get("auth_observed") is True,
        "visible_controls": signatures,
    }


def safe_fill_surface_fingerprint(snapshot: dict) -> str:
    payload = json.dumps(
        safe_fill_surface_descriptor(snapshot),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _control_by_id(snapshot: dict, element_id: str) -> dict:
    matches = [
        control for control in visible_application_controls(snapshot.get("form", {}))
        if control.get("id") == element_id
    ]
    if len(matches) != 1:
        raise PermissionError(f"ADP_SAFE_FILL_CONTROL_NOT_UNIQUE:{element_id}")
    return matches[0]


def _validate_target_control(snapshot: dict, spec: tuple[str, str, str, str, bool]) -> dict:
    canonical, element_id, label, control_type, required = spec
    control = _control_by_id(snapshot, element_id)
    if str(control.get("type", "")).lower() != control_type:
        raise PermissionError(f"ADP_SAFE_FILL_CONTROL_TYPE_DRIFT:{canonical}")
    if _normalize_label(str(control.get("label", ""))) != _normalize_label(label):
        raise PermissionError(f"ADP_SAFE_FILL_CONTROL_LABEL_DRIFT:{canonical}")
    if (control.get("required") is True) != required:
        raise PermissionError(f"ADP_SAFE_FILL_REQUIREDNESS_DRIFT:{canonical}")
    if control.get("disabled") is True or control.get("visible") is not True:
        raise PermissionError(f"ADP_SAFE_FILL_CONTROL_NOT_ACTIONABLE:{canonical}")
    return control


def _visible_button_labels(page) -> list[dict]:
    results = []
    buttons = page.get_by_role("button")
    for index in range(min(buttons.count(), 30)):
        loc = buttons.nth(index)
        try:
            if not loc.is_visible():
                continue
            name = loc.get_attribute("aria-label") or loc.inner_text() or ""
            name = " ".join(name.split())
            if not name:
                continue
            results.append({"ordinal": index, "label": name[:120], "enabled": loc.is_enabled()})
        except Exception:
            continue
    return results


def _one_trust_boundary(page) -> dict:
    banner = page.locator(ONETRUST_BANNER_SELECTOR)
    reject = page.locator(ONETRUST_REJECT_SELECTOR)
    accept = page.locator(ONETRUST_ACCEPT_SELECTOR)

    def _visible(locator) -> bool:
        try:
            return locator.count() == 1 and locator.is_visible()
        except Exception:
            return False

    def _label(locator) -> str:
        try:
            if locator.count() != 1:
                return ""
            return _normalize_label(locator.get_attribute("aria-label") or locator.inner_text() or "")
        except Exception:
            return ""

    return {
        "banner_present": banner.count() == 1,
        "banner_visible": _visible(banner),
        "reject_present": reject.count() == 1,
        "reject_visible": _visible(reject),
        "reject_enabled": reject.count() == 1 and reject.is_enabled(),
        "reject_label": _label(reject),
        "accept_present": accept.count() == 1,
        "accept_visible": _visible(accept),
        "accept_label": _label(accept),
    }


def _validate_one_trust_boundary(boundary: dict) -> None:
    if boundary.get("banner_visible") is not True:
        raise PermissionError("ADP_COOKIE_BOUNDARY_NOT_VISIBLE")
    if boundary.get("reject_present") is not True or boundary.get("reject_visible") is not True:
        raise PermissionError("ADP_COOKIE_DENY_CONTROL_NOT_AVAILABLE")
    if boundary.get("reject_enabled") is not True:
        raise PermissionError("ADP_COOKIE_DENY_CONTROL_DISABLED")
    if boundary.get("reject_label") != ONETRUST_REJECT_LABEL:
        raise PermissionError("ADP_COOKIE_DENY_LABEL_DRIFT")
    if boundary.get("accept_present") is not True or boundary.get("accept_label") != ONETRUST_ACCEPT_LABEL:
        raise PermissionError("ADP_COOKIE_ACCEPT_SURFACE_DRIFT")


def _base_report(request: AdpSafeFillCanaryRequest, profile_version: str) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "safe_fill_canary_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
        "candidate_profile_version": profile_version,
        "cookie_policy": request.cookie_policy,
        "cookie_preference_click_attempts": 0,
        "cookie_preference_click_successes": 0,
        "optional_cookie_accept_attempts": 0,
        "navigation_click_attempts": 0,
        "navigation_click_successes": 0,
        "credential_entry_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "second_action_click_attempts": 0,
        "submit_attempts": 0,
        "further_navigation_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _blocked(
    base: dict,
    error_code: str,
    *,
    pre=None,
    form=None,
    cookie_boundary=None,
    cookie_attempts=0,
    cookie_successes=0,
    writes=0,
    click_attempts=0,
    click_successes=0,
) -> dict:
    report = {
        **base,
        "canary_status": "blocked",
        "error_code": error_code,
        "cookie_preference_click_attempts": cookie_attempts,
        "cookie_preference_click_successes": cookie_successes,
        "navigation_click_attempts": click_attempts,
        "navigation_click_successes": click_successes,
        "form_value_write_attempts": writes,
    }
    if pre is not None:
        report["pre_navigation"] = pre
    if form is not None:
        report["pre_fill"] = form
    if cookie_boundary is not None:
        report["cookie_boundary"] = cookie_boundary
    return report


def run_adp_safe_fill_canary(
    request: AdpSafeFillCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    profile, profile_version = load_identity_profile(request.profile_manifest_path)
    base = _base_report(request, profile_version)
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
                return _blocked(base, "ADP_SAFE_FILL_PREFLIGHT_REQUIRES_APPLICATION_ENTRY", pre=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _blocked(base, "ADP_SAFE_FILL_PREFLIGHT_BOUNDARY_OBSERVED", pre=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _blocked(base, "ADP_SAFE_FILL_NAVIGATION_SURFACE_MISMATCH", pre=pre)

            cookie_boundary = _one_trust_boundary(page)
            cookie_attempts = 0
            cookie_successes = 0
            if cookie_boundary.get("banner_visible") is True:
                if request.cookie_policy != COOKIE_POLICY_DENY_OPTIONAL:
                    return _blocked(
                        base,
                        "ADP_COOKIE_CONSENT_BOUNDARY",
                        pre=pre,
                        cookie_boundary=cookie_boundary,
                    )
                try:
                    _validate_one_trust_boundary(cookie_boundary)
                except PermissionError as exc:
                    return _blocked(
                        base,
                        str(exc),
                        pre=pre,
                        cookie_boundary=cookie_boundary,
                    )
                reject = page.locator(ONETRUST_REJECT_SELECTOR)
                try:
                    cookie_attempts = 1
                    reject.click(timeout=min(request.timeout_ms, 10_000))
                    cookie_successes = 1
                except Exception as exc:
                    return _blocked(
                        base,
                        f"ADP_COOKIE_DENY_CLICK_FAILED:{type(exc).__name__}",
                        pre=pre,
                        cookie_boundary=cookie_boundary,
                        cookie_attempts=cookie_attempts,
                        cookie_successes=cookie_successes,
                    )
                page.wait_for_timeout(300)
                after_cookie = _one_trust_boundary(page)
                if after_cookie.get("banner_visible") is True or after_cookie.get("reject_visible") is True:
                    return _blocked(
                        base,
                        "ADP_COOKIE_BOUNDARY_PERSISTED_AFTER_DENY",
                        pre=pre,
                        cookie_boundary=after_cookie,
                        cookie_attempts=cookie_attempts,
                        cookie_successes=cookie_successes,
                    )

            try:
                approved = _approved_entry(pre, request.entry_ordinal, request.expected_label)
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
            except PermissionError as exc:
                return _blocked(
                    base,
                    str(exc),
                    pre=pre,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                )
            if not entry.is_visible() or not entry.is_enabled():
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_ENTRY_NOT_ACTIONABLE",
                    pre=pre,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                )
            actual_label = entry.get_attribute("aria-label") or entry.inner_text() or ""
            if _normalize(actual_label) != _normalize(request.expected_label):
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_ENTRY_LABEL_DRIFT",
                    pre=pre,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                )
            try:
                entry.click(timeout=request.timeout_ms)
            except Exception as exc:
                return _blocked(
                    base,
                    f"ADP_SAFE_FILL_ENTRY_CLICK_FAILED:{type(exc).__name__}",
                    pre=pre,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                )
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_NAVIGATION_OPENED_NEW_PAGE",
                    pre=pre,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                    click_successes=1,
                )

            form = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if form.get("captcha_observed") is True or form.get("auth_observed") is True:
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_POST_NAV_BOUNDARY_OBSERVED",
                    pre=pre,
                    form=form,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                    click_successes=1,
                )
            if form.get("runtime_state") != "inspected":
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_FORM_NOT_INSPECTED",
                    pre=pre,
                    form=form,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                    click_successes=1,
                )
            actual_surface = safe_fill_surface_fingerprint(form)
            if actual_surface != request.expected_safe_fill_surface_fingerprint:
                return _blocked(
                    base,
                    "ADP_SAFE_FILL_SURFACE_MISMATCH",
                    pre=pre,
                    form=form,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                    click_successes=1,
                )

            targets = []
            try:
                for spec in TARGETS:
                    targets.append((spec, _validate_target_control(form, spec)))
            except PermissionError as exc:
                return _blocked(
                    base,
                    str(exc),
                    pre=pre,
                    form=form,
                    cookie_boundary=cookie_boundary,
                    cookie_attempts=cookie_attempts,
                    cookie_successes=cookie_successes,
                    click_attempts=1,
                    click_successes=1,
                )

            results = []
            writes = 0
            for spec, _control in targets:
                canonical, element_id, _label, _ctype, _required = spec
                locator = page.locator(f"#{element_id}")
                if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
                    return _blocked(
                        base,
                        f"ADP_SAFE_FILL_RUNTIME_LOCATOR_DRIFT:{canonical}",
                        pre=pre,
                        form=form,
                        cookie_boundary=cookie_boundary,
                        cookie_attempts=cookie_attempts,
                        cookie_successes=cookie_successes,
                        writes=writes,
                        click_attempts=1,
                        click_successes=1,
                    )
                desired = profile[canonical]
                before = locator.input_value()
                executed = before != desired
                if executed:
                    try:
                        locator.fill(desired, timeout=request.timeout_ms)
                    except Exception as exc:
                        return _blocked(
                            base,
                            f"ADP_SAFE_FILL_WRITE_FAILED:{canonical}:{type(exc).__name__}",
                            pre=pre,
                            form=form,
                            cookie_boundary=cookie_boundary,
                            cookie_attempts=cookie_attempts,
                            cookie_successes=cookie_successes,
                            writes=writes + 1,
                            click_attempts=1,
                            click_successes=1,
                        )
                    writes += 1
                readback = locator.input_value()
                valid = locator.evaluate("el => el.checkValidity ? el.checkValidity() : true")
                matched = readback == desired and bool(valid)
                results.append({
                    "canonical_field": canonical,
                    "control_id": element_id,
                    "value_hash": value_hash(desired),
                    "readback_hash": value_hash(readback),
                    "write_executed": executed,
                    "readback_match": matched,
                    "valid": bool(valid),
                })
                if not matched:
                    return {
                        **_blocked(
                            base,
                            f"ADP_SAFE_FILL_READBACK_MISMATCH:{canonical}",
                            pre=pre,
                            form=form,
                            cookie_boundary=cookie_boundary,
                            cookie_attempts=cookie_attempts,
                            cookie_successes=cookie_successes,
                            writes=writes,
                            click_attempts=1,
                            click_successes=1,
                        ),
                        "field_results": results,
                    }

            post = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if safe_fill_surface_fingerprint(post) != actual_surface:
                return {
                    **_blocked(
                        base,
                        "ADP_SAFE_FILL_POST_WRITE_SURFACE_DRIFT",
                        pre=pre,
                        form=form,
                        cookie_boundary=cookie_boundary,
                        cookie_attempts=cookie_attempts,
                        cookie_successes=cookie_successes,
                        writes=writes,
                        click_attempts=1,
                        click_successes=1,
                    ),
                    "field_results": results,
                    "post_fill": post,
                }
            return {
                **base,
                "canary_status": "filled_verified",
                "error_code": "",
                "resolved_entry_observation_key": observation_key,
                "pre_navigation": pre,
                "cookie_boundary": cookie_boundary,
                "pre_fill": form,
                "post_fill": post,
                "observed_safe_fill_surface_fingerprint": actual_surface,
                "field_results": results,
                "visible_button_accessibility": _visible_button_labels(page),
                "cookie_preference_click_attempts": cookie_attempts,
                "cookie_preference_click_successes": cookie_successes,
                "navigation_click_attempts": 1,
                "navigation_click_successes": 1,
                "form_value_write_attempts": writes,
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP bounded identity safe-fill canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--profile-manifest", required=True)
    parser.add_argument("--cookie-policy", choices=sorted(COOKIE_POLICIES), default=COOKIE_POLICY_BLOCK)
    parser.add_argument("--output", default="adp-safe-fill-canary.json")
    args = parser.parse_args()
    report = run_adp_safe_fill_canary(AdpSafeFillCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
        profile_manifest_path=args.profile_manifest,
        cookie_policy=args.cookie_policy,
    ))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "cookie_preference_click_attempts": report.get("cookie_preference_click_attempts", 0),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "filled_verified" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_SAFE_FILL_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
