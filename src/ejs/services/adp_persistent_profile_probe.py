"""Local read-only diagnostic for ADP persistent-browser-profile reuse.

This probe never enters credentials, writes fields, uploads files, or clicks
application controls. It reopens a previously user-verified temporary Chromium
profile and tests the exact captured, target-bound postLogin URL.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_verified_session_inspector import (
    _load_direct_reuse_url,
    _probe_authenticated_postlogin,
)

PROBE_VERSION = "adp-persistent-profile-probe-v1"


@dataclass(frozen=True)
class AdpPersistentProfileProbeRequest:
    application_url: str
    user_data_dir: str
    direct_reuse_url_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpPersistentProfileProbeRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not request.user_data_dir:
        raise ValueError("ADP_PERSISTENT_PROFILE_USER_DATA_DIR_REQUIRED")
    profile = Path(request.user_data_dir)
    if not profile.exists() or not profile.is_dir():
        raise ValueError("ADP_PERSISTENT_PROFILE_USER_DATA_DIR_INVALID")
    if not request.direct_reuse_url_path:
        raise ValueError("ADP_PERSISTENT_PROFILE_DIRECT_REUSE_URL_REQUIRED")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_PERSISTENT_PROFILE_PROBE_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_PERSISTENT_PROFILE_PROBE_RENDER_WAIT")


def run_probe(request: AdpPersistentProfileProbeRequest) -> dict:
    validate_request(request)
    direct_reuse_url, direct_reuse_evidence = _load_direct_reuse_url(
        request.direct_reuse_url_path,
        request.application_url,
    )

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("PLAYWRIGHT_UNAVAILABLE") from exc

    base = {
        "probe_version": PROBE_VERSION,
        "persistent_profile_probe_only": True,
        "direct_reuse_url_evidence": direct_reuse_evidence,
        "navigation_click_attempts": 0,
        "form_value_write_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "final_submit_allowed": False,
        "file_upload_allowed": False,
        "raw_profile_data_exposed": False,
    }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            request.user_data_dir,
            headless=False,
            accept_downloads=False,
        )
        try:
            pages = list(context.pages)
            if len(pages) > 1:
                return {
                    **base,
                    "probe_status": "blocked",
                    "error_code": "ADP_PERSISTENT_PROFILE_UNREVIEWED_NEW_PAGE",
                    "profile_reuse_proven": False,
                }
            page = pages[0] if pages else context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            direct = _probe_authenticated_postlogin(
                page,
                request.application_url,
                request.render_wait_ms,
                direct_reuse_url=direct_reuse_url,
            )
            if direct.get("authenticated_postlogin_reused") is True:
                return {
                    **base,
                    "probe_status": "inspected",
                    "error_code": "",
                    "profile_reuse_proven": True,
                    "direct_postlogin_probe": direct,
                }
            return {
                **base,
                "probe_status": "blocked",
                "error_code": "ADP_PERSISTENT_PROFILE_NOT_REUSED",
                "profile_reuse_proven": False,
                "direct_postlogin_probe": direct,
            }
        finally:
            context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ADP persistent-profile reuse diagnostic")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--direct-reuse-url-file", required=True, dest="direct_reuse_url_path")
    parser.add_argument("--output", default="adp-persistent-profile-probe.json")
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    parser.add_argument("--render-wait-ms", type=int, default=10_000)
    args = parser.parse_args()

    report = run_probe(AdpPersistentProfileProbeRequest(
        application_url=args.application_url,
        user_data_dir=args.user_data_dir,
        direct_reuse_url_path=args.direct_reuse_url_path,
        timeout_ms=args.timeout_ms,
        render_wait_ms=args.render_wait_ms,
    ))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "probe_status": report.get("probe_status"),
        "error_code": report.get("error_code"),
        "profile_reuse_proven": report.get("profile_reuse_proven") is True,
        "direct_reuse_url_evidence": report.get("direct_reuse_url_evidence"),
        "direct_postlogin_probe": report.get("direct_postlogin_probe"),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "credential_entry_attempts": report.get("credential_entry_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("profile_reuse_proven") is True else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"ADP_PERSISTENT_PROFILE_PROBE_ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)
