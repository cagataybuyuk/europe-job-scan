"""Local/headful bootstrap for a user-verified ADP guest session.

This tool never reads or enters the verification code. The user drives the
browser manually. Disappearing OTP/identity fields alone are not success: a
stable, non-empty form surface must follow. Exported state is only a candidate;
the PowerShell helper must prove fresh-browser reuse before provisioning it.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from ejs.services.adp_live_inspector import validate_adp_live_url

BOOTSTRAP_VERSION = "adp-verified-session-bootstrap-v2"
OTP_CONTROL_ID = "oneTimePassWord"
IDENTITY_CONTROL_IDS = ("guestFirstName", "guestLastName", "guestEmail")
DEFAULT_TIMEOUT_SECONDS = 900
MIN_POST_VERIFICATION_SECONDS = 10
MIN_STABLE_SURFACE_SECONDS = 5


@dataclass(frozen=True)
class AdpVerifiedSessionBootstrapRequest:
    application_url: str
    storage_state_out: str
    report_out: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def validate_request(request: AdpVerifiedSessionBootstrapRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not request.storage_state_out:
        raise ValueError("ADP_SESSION_BOOTSTRAP_REQUIRES_STORAGE_STATE_OUT")
    if not request.report_out:
        raise ValueError("ADP_SESSION_BOOTSTRAP_REQUIRES_REPORT_OUT")
    if request.timeout_seconds < 60 or request.timeout_seconds > 1800:
        raise ValueError("INVALID_ADP_SESSION_BOOTSTRAP_TIMEOUT")


def _visible(page, selector: str) -> bool:
    # Detached/loading DOM errors must reset readiness, never mean "absent".
    locator = page.locator(selector)
    return any(locator.nth(i).is_visible() for i in range(locator.count()))


def bootstrap_stage(page) -> dict:
    otp_visible = _visible(page, f"#{OTP_CONTROL_ID}")
    identity_visible = {
        control_id: _visible(page, f"#{control_id}")
        for control_id in IDENTITY_CONTROL_IDS
    }
    return {
        "verification_code_visible": otp_visible,
        "identity_surface_visible": any(identity_visible.values()),
        "identity_controls_visible_count": sum(1 for value in identity_visible.values() if value),
        "raw_values_exposed": False,
    }


def _sanitized_post_verification_report(page, verification_seen: bool) -> dict:
    controls = []
    try:
        locator = page.locator("input, select, textarea, button, sdf-button, input[type=file]")
        count = locator.count()
        if count > 500:
            raise RuntimeError("ADP_SESSION_BOOTSTRAP_SURFACE_TOO_LARGE")
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                if len(controls) >= 80:
                    raise RuntimeError("ADP_SESSION_BOOTSTRAP_SURFACE_TOO_LARGE")
                tag = str(item.evaluate("el => el.tagName.toLowerCase()") or "")
                control_type = str(item.get_attribute("type") or "")
                control_id = str(item.get_attribute("id") or "")[:120]
                name = str(item.get_attribute("name") or "")[:120]
                aria_label = str(item.get_attribute("aria-label") or "")[:160]
                controls.append({
                    "tag": tag,
                    "type": control_type,
                    "id": control_id,
                    "name": name,
                    "aria_label": aria_label,
                    "required": item.get_attribute("required") is not None,
                    "disabled": item.is_disabled(),
                    "readonly": item.get_attribute("readonly") is not None,
                    "file_control": control_type.casefold() == "file",
                })
            except Exception:
                raise RuntimeError("ADP_SESSION_BOOTSTRAP_SURFACE_UNAVAILABLE") from None
    except Exception:
        raise RuntimeError("ADP_SESSION_BOOTSTRAP_SURFACE_UNAVAILABLE") from None
    parsed = urlsplit(str(page.url))
    return {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "verification_seen": verification_seen,
        "verification_completed": False,
        "post_verification_url": urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
        "visible_control_count": len(controls),
        "visible_controls": controls,
        "raw_values_exposed": False,
        "storage_state_exported": False,
    }


def _form_surface_signature(report: dict) -> tuple:
    """A readiness signal, not a reviewed application manifest or write grant."""
    controls = report["visible_controls"]
    if any(c["type"].casefold() == "password" for c in controls):
        return ()
    excluded_types = {"hidden", "button", "submit", "reset", "image", "search"}
    excluded_ids = {OTP_CONTROL_ID, *IDENTITY_CONTROL_IDS}
    return tuple(sorted(
        (c["tag"], c["type"], c["id"], c["name"])
        for c in controls
        if c["tag"] in {"input", "select", "textarea"}
        and c["type"].casefold() not in excluded_types
        and c["id"] not in excluded_ids
        and not c["id"].casefold().startswith(("onetrust", "ot-"))
        and not c["disabled"]
        and not c.get("readonly", False)
    ))


def run_bootstrap(request: AdpVerifiedSessionBootstrapRequest) -> dict:
    validate_request(request)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("PLAYWRIGHT_UNAVAILABLE") from exc

    storage_path = Path(request.storage_state_out)
    report_path = Path(request.report_out)
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=False)
        try:
            page = context.new_page()
            page.goto(request.application_url, wait_until="domcontentloaded", timeout=60_000)
            print(
                "ADP browser opened. Complete the application-entry steps manually. "
                "When the email verification code appears, enter it directly in the browser "
                "and click Verify. Do not paste the code into this terminal."
            )

            deadline = time.monotonic() + request.timeout_seconds
            verification_seen = False
            stable_since = None
            last_signature = None
            last_otp_seen = None
            last_control_count = 0
            transition_announced = False
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError("ADP_SESSION_BOOTSTRAP_BROWSER_CLOSED")
                if len(context.pages) != 1:
                    raise RuntimeError("ADP_SESSION_BOOTSTRAP_UNREVIEWED_NEW_PAGE")
                try:
                    validate_adp_live_url(str(page.url))
                except ValueError:
                    raise RuntimeError("ADP_SESSION_BOOTSTRAP_UNREVIEWED_ORIGIN") from None
                try:
                    stage = bootstrap_stage(page)
                    report = _sanitized_post_verification_report(page, verification_seen)
                except Exception:
                    stable_since = None
                    last_signature = None
                    page.wait_for_timeout(1_000)
                    continue
                now = time.monotonic()
                last_control_count = report["visible_control_count"]
                if stage["verification_code_visible"]:
                    verification_seen = True
                    last_otp_seen = now
                    stable_since = None
                    last_signature = None
                elif verification_seen and not stage["identity_surface_visible"]:
                    if not transition_announced:
                        print("Verification screen closed. Waiting for a stable form; keep the browser open.")
                        transition_announced = True
                    signature = _form_surface_signature(report)
                    surface_key = (str(page.url), signature)
                    if not signature:
                        stable_since = None
                        last_signature = None
                    elif surface_key != last_signature:
                        stable_since = now
                        last_signature = surface_key
                    elif (
                        stable_since is not None
                        and now - stable_since >= MIN_STABLE_SURFACE_SECONDS
                        and last_otp_seen is not None
                        and now - last_otp_seen >= MIN_POST_VERIFICATION_SECONDS
                    ):
                        context.storage_state(path=str(storage_path))
                        report.update({
                            "verification_seen": True,
                            "verification_completed": True,
                            "post_verification_surface_stable": True,
                            "visible_form_control_count": len(signature),
                            "storage_state_exported": True,
                            "session_reuse_proven": False,
                        })
                        report_path.write_text(
                            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        print(
                            "Stable post-verification form observed. Candidate session exported locally; "
                            "fresh-browser reuse must pass before secret provisioning."
                        )
                        return report
                else:
                    stable_since = None
                    last_signature = None
                page.wait_for_timeout(1_000)

            print(json.dumps({
                "verification_seen": verification_seen,
                "verification_completed": False,
                "visible_control_count": last_control_count,
                "storage_state_exported": False,
                "raw_values_exposed": False,
            }, sort_keys=True))
            reason = "FORM_NOT_READY" if verification_seen else "VERIFICATION_TIMEOUT"
            raise TimeoutError(f"ADP_SESSION_BOOTSTRAP_{reason}")
        finally:
            context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local ADP verified-session bootstrap")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--storage-state-out", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    report = run_bootstrap(AdpVerifiedSessionBootstrapRequest(
        application_url=args.application_url,
        storage_state_out=args.storage_state_out,
        report_out=args.report_out,
        timeout_seconds=args.timeout_seconds,
    ))
    print(json.dumps({
        "verification_seen": report.get("verification_seen") is True,
        "verification_completed": report.get("verification_completed") is True,
        "visible_control_count": report.get("visible_control_count", 0),
        "storage_state_exported": report.get("storage_state_exported") is True,
        "raw_values_exposed": report.get("raw_values_exposed") is True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_VERIFIED_SESSION_BOOTSTRAP_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
