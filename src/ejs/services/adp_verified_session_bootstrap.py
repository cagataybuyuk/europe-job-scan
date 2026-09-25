"""Local/headful bootstrap for a user-verified ADP guest session.

This tool never reads or enters the verification code. The user drives the
browser manually. Disappearing OTP/identity fields alone are not success: a
stable, non-empty form surface must follow. Exported state is only a candidate;
the PowerShell helper must prove fresh-browser reuse before provisioning it.
An already authenticated, target-matched Personal Information surface can also
yield candidate state without falsely claiming an observed OTP transition.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_manifest import extract_same_page_manifest

BOOTSTRAP_VERSION = "adp-verified-session-bootstrap-v5"
OTP_CONTROL_ID = "oneTimePassWord"
IDENTITY_CONTROL_IDS = ("guestFirstName", "guestLastName", "guestEmail")
DEFAULT_TIMEOUT_SECONDS = 900
MIN_POST_VERIFICATION_SECONDS = 10
MIN_STABLE_SURFACE_SECONDS = 5
REVIEWED_POSTLOGIN_PATH = "/mascsr/applicant/mdf/recruitment/postLogin.html"


@dataclass(frozen=True)
class AdpVerifiedSessionBootstrapRequest:
    application_url: str
    storage_state_out: str
    report_out: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    session_storage_out: str = ""
    postlogin_url_out: str = ""
    user_data_dir: str = ""
    live_handoff_report_out: str = ""
    same_page_manifest_out: str = ""


def validate_request(request: AdpVerifiedSessionBootstrapRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not request.storage_state_out:
        raise ValueError("ADP_SESSION_BOOTSTRAP_REQUIRES_STORAGE_STATE_OUT")
    if not request.report_out:
        raise ValueError("ADP_SESSION_BOOTSTRAP_REQUIRES_REPORT_OUT")
    if request.timeout_seconds < 60 or request.timeout_seconds > 1800:
        raise ValueError("INVALID_ADP_SESSION_BOOTSTRAP_TIMEOUT")


def _open_reviewed_adp_target(page, application_url: str) -> dict:
    """Open the reviewed target without requiring ADP's full DOMContentLoaded event."""
    last_error = None
    for attempt in range(1, 3):
        try:
            page.goto(application_url, wait_until="commit", timeout=45_000)
            validate_adp_live_url(str(page.url))
            return {
                "navigation_attempts": attempt,
                "navigation_commit_observed": True,
                "navigation_timeout_tolerated": False,
            }
        except Exception as exc:
            last_error = exc
            # ADP can keep loading long after the reviewed document has already
            # committed. Only tolerate a timeout when the current page is still
            # on the reviewed ADP origin; never tolerate another origin.
            if type(exc).__name__ == "TimeoutError":
                try:
                    validate_adp_live_url(str(page.url))
                except ValueError:
                    pass
                else:
                    if str(page.url) not in {"", "about:blank"}:
                        return {
                            "navigation_attempts": attempt,
                            "navigation_commit_observed": False,
                            "navigation_timeout_tolerated": True,
                        }
            if attempt < 2:
                page.wait_for_timeout(2_000)
                continue
            break
    raise RuntimeError("ADP_SESSION_BOOTSTRAP_INITIAL_NAVIGATION_FAILED") from last_error


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


def _authenticated_form_diagnostics(page, application_url: str) -> dict:
    """Return value-free evidence explaining authenticated-form classification."""
    evidence = {
        "reviewed_origin_valid": False,
        "postlogin_path_match": False,
        "cid_match": False,
        "ccid_match": False,
        "jobid_match": False,
        "sign_out_visible": False,
        "my_applications_visible": False,
        "personal_information_visible": False,
        "resume_visible": False,
        "questions_visible": False,
        "review_application_visible": False,
        "self_attest_submit_visible": False,
        "authenticated_portal_observed": False,
        "authenticated_application_steps_observed": False,
        "authenticated_form_observed": False,
        "observation_succeeded": False,
        "raw_values_exposed": False,
    }
    try:
        validate_adp_live_url(str(page.url))
        evidence["reviewed_origin_valid"] = True
        current = urlsplit(str(page.url))
        expected = urlsplit(application_url)
        evidence["postlogin_path_match"] = (
            current.netloc == expected.netloc and current.path == REVIEWED_POSTLOGIN_PATH
        )

        def query_parameters(query: str) -> dict:
            result = {}
            for key, values in parse_qs(query, keep_blank_values=True).items():
                result.setdefault(key.casefold(), []).extend(values)
            return result

        actual_query = query_parameters(current.query)
        expected_query = query_parameters(expected.query)
        query_matches = {}
        for key in ("cid", "ccid", "jobid"):
            target_values = expected_query.get(key, [])
            actual_values = actual_query.get(key, [])
            target_unique = {value for value in target_values if value}
            actual_unique = {value for value in actual_values if value}
            query_matches[key] = (
                len(target_unique) == 1
                and len(actual_values) >= 1
                and len(actual_unique) == 1
                and actual_unique == target_unique
                and all(bool(value) for value in actual_values)
            )
            evidence[f"{key}_match"] = query_matches[key]

        def visible_label(label: str) -> bool:
            exact = page.get_by_text(label, exact=True)
            if any(exact.nth(i).is_visible() for i in range(exact.count())):
                return True

            # ADP can split a step label across nested elements/whitespace.
            # Fall back only to visible nodes whose normalized inner text still
            # exactly matches the reviewed label; broad substring matches do
            # not establish authenticated-form evidence.
            target = " ".join(label.split())
            candidates = page.get_by_text(label, exact=False)
            for index in range(candidates.count()):
                item = candidates.nth(index)
                if not item.is_visible():
                    continue
                normalized = " ".join(str(item.inner_text() or "").split())
                if normalized == target:
                    return True
            return False

        labels = {
            "sign_out_visible": "Sign Out",
            "my_applications_visible": "My Applications",
            "personal_information_visible": "Personal Information",
            "resume_visible": "Resume",
            "questions_visible": "Questions",
            "review_application_visible": "Review Your Application",
            "self_attest_submit_visible": "Self-Attest & Submit",
        }
        if evidence["postlogin_path_match"] and all(query_matches.values()):
            for key, label in labels.items():
                evidence[key] = visible_label(label)

        portal = evidence["sign_out_visible"] and evidence["my_applications_visible"]
        application_steps = all(
            evidence[key]
            for key in (
                "resume_visible",
                "questions_visible",
                "review_application_visible",
                "self_attest_submit_visible",
            )
        )
        evidence["authenticated_portal_observed"] = portal
        evidence["authenticated_application_steps_observed"] = application_steps
        evidence["authenticated_form_observed"] = (
            evidence["postlogin_path_match"]
            and all(query_matches.values())
            and evidence["personal_information_visible"]
            and (portal or application_steps)
        )
        evidence["observation_succeeded"] = True
        return evidence
    except Exception:
        return evidence


def _authenticated_form_evidence(page, application_url: str) -> dict:
    diagnostics = _authenticated_form_diagnostics(page, application_url)
    return {
        "authenticated_portal_observed": diagnostics["authenticated_portal_observed"],
        "authenticated_application_steps_observed": diagnostics["authenticated_application_steps_observed"],
        "authenticated_form_observed": diagnostics["authenticated_form_observed"],
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


def _capture_session_storage(page) -> tuple[dict[str, str], dict]:
    """Capture tab-scoped state locally without exposing keys or values in diagnostics."""
    raw = page.evaluate(
        """() => {
          const out = {};
          for (let i = 0; i < window.sessionStorage.length; i += 1) {
            const key = window.sessionStorage.key(i);
            if (key !== null) out[key] = window.sessionStorage.getItem(key) ?? "";
          }
          return out;
        }"""
    )
    if not isinstance(raw, dict) or len(raw) > 200:
        raise RuntimeError("ADP_SESSION_BOOTSTRAP_SESSION_STORAGE_INVALID")
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError("ADP_SESSION_BOOTSTRAP_SESSION_STORAGE_INVALID")
        normalized[key] = value
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    byte_count = len(payload.encode("utf-8"))
    if byte_count > 47_000:
        raise RuntimeError("ADP_SESSION_BOOTSTRAP_SESSION_STORAGE_TOO_LARGE")
    return normalized, {
        "session_storage_entry_count": len(normalized),
        "session_storage_byte_count": byte_count,
        "session_storage_values_exposed": False,
    }


def _sanitized_cookie_metadata(cookies: list[dict]) -> dict:
    """Report known OneTrust persistence markers without exposing cookie values."""
    consent = []
    alert_closed = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name", ""))
        if name == "OptanonConsent":
            consent.append(cookie)
        elif name == "OptanonAlertBoxClosed":
            alert_closed.append(cookie)

    def scopes(items: list[dict]) -> dict:
        return {
            "count": len(items),
            "domain_count": len({str(item.get("domain", "")) for item in items}),
            "root_path_count": sum(1 for item in items if str(item.get("path", "")) == "/"),
        }

    consent_scope = scopes(consent)
    alert_scope = scopes(alert_closed)
    return {
        "onetrust_consent_cookie_present": bool(consent),
        "onetrust_consent_cookie_count": consent_scope["count"],
        "onetrust_consent_cookie_domain_count": consent_scope["domain_count"],
        "onetrust_consent_cookie_root_path_count": consent_scope["root_path_count"],
        "onetrust_alert_closed_cookie_present": bool(alert_closed),
        "onetrust_alert_closed_cookie_count": alert_scope["count"],
        "onetrust_alert_closed_cookie_domain_count": alert_scope["domain_count"],
        "onetrust_alert_closed_cookie_root_path_count": alert_scope["root_path_count"],
        "cookie_values_exposed": False,
    }


def _export_storage_state(context, path: Path) -> dict:
    """Export cookies/localStorage plus IndexedDB without exposing raw values."""
    try:
        state = context.storage_state(path=str(path), indexed_db=True)
    except TypeError as exc:
        raise RuntimeError("PLAYWRIGHT_INDEXED_DB_STORAGE_STATE_UNAVAILABLE") from exc
    if not isinstance(state, dict):
        raise RuntimeError("ADP_SESSION_BOOTSTRAP_STORAGE_STATE_INVALID")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise RuntimeError("ADP_SESSION_BOOTSTRAP_STORAGE_STATE_INVALID")
    local_storage_count = 0
    indexed_db_database_count = 0
    indexed_db_origin_count = 0
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        local_storage = origin.get("localStorage", [])
        if isinstance(local_storage, list):
            local_storage_count += len(local_storage)
        indexed_db = origin.get("indexedDB", [])
        if isinstance(indexed_db, list) and indexed_db:
            indexed_db_origin_count += 1
            indexed_db_database_count += len(indexed_db)
    return {
        "storage_cookie_count": len(cookies),
        "storage_origin_count": len(origins),
        "storage_local_storage_entry_count": local_storage_count,
        "storage_indexed_db_origin_count": indexed_db_origin_count,
        "storage_indexed_db_database_count": indexed_db_database_count,
        **_sanitized_cookie_metadata(cookies),
        "storage_raw_values_exposed": False,
    }


def _session_storage_restore_script(
    application_url: str,
    session_storage: dict[str, str],
) -> str:
    hostname = urlsplit(application_url).hostname or ""
    return (
        "(() => {"
        f"if (window.location.hostname !== {json.dumps(hostname, ensure_ascii=False)}) return;"
        f"const restored = {json.dumps(session_storage, ensure_ascii=False, sort_keys=True)};"
        "for (const [key, value] of Object.entries(restored)) "
        "window.sessionStorage.setItem(key, value);"
        "})();"
    )


def _probe_authenticated_page(page, application_url: str, budget_ms: int) -> dict:
    hostname = urlsplit(application_url).hostname or ""
    elapsed = 0
    stable_ms = 0
    last_key = None
    while elapsed <= budget_ms:
        try:
            stage = bootstrap_stage(page)
            authenticated = _authenticated_form_evidence(page, application_url)
            report = _sanitized_post_verification_report(page, verification_seen=False)
            signature = _form_surface_signature(report)
        except Exception:
            stage = {
                "verification_code_visible": False,
                "identity_surface_visible": False,
            }
            authenticated = {"authenticated_form_observed": False}
            signature = ()

        valid = (
            authenticated.get("authenticated_form_observed") is True
            and stage.get("verification_code_visible") is not True
            and stage.get("identity_surface_visible") is not True
            and bool(signature)
        )
        key = (str(page.url), signature)
        if valid:
            if key == last_key:
                stable_ms += 250
            else:
                stable_ms = 0
                last_key = key
            if stable_ms >= 1_000:
                return {
                    "reuse_proven": True,
                    "visible_form_control_count": len(signature),
                    "final_reviewed_origin": (urlsplit(str(page.url)).hostname or "") == hostname,
                    "navigation_click_attempts": 0,
                    "form_value_write_attempts": 0,
                    "credential_entry_attempts": 0,
                    "file_upload_attempts": 0,
                    "submit_attempts": 0,
                    "raw_values_exposed": False,
                }
        else:
            stable_ms = 0
            last_key = None

        if elapsed >= budget_ms:
            break
        interval = min(250, budget_ms - elapsed)
        page.wait_for_timeout(interval)
        elapsed += interval

    return {
        "reuse_proven": False,
        "visible_form_control_count": 0,
        "final_reviewed_origin": (urlsplit(str(page.url)).hostname or "") == hostname,
        "navigation_click_attempts": 0,
        "form_value_write_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "raw_values_exposed": False,
    }


def _navigate_and_probe(
    page,
    application_url: str,
    canonical_postlogin_url: str,
    session_storage: dict[str, str],
    budget_ms: int,
) -> dict:
    page.set_default_timeout(max(1_000, budget_ms))
    page.set_default_navigation_timeout(max(1_000, budget_ms))
    if session_storage:
        page.add_init_script(
            script=_session_storage_restore_script(application_url, session_storage)
        )
    page.goto(
        canonical_postlogin_url,
        wait_until="domcontentloaded",
        timeout=max(1_000, budget_ms),
    )
    result = _probe_authenticated_page(page, application_url, budget_ms)
    return {
        **result,
        "session_storage_entry_count": len(session_storage),
    }


def _live_handoff_probe(
    playwright,
    source_browser,
    source_context,
    application_url: str,
    storage_state_path: Path,
    session_storage: dict[str, str],
    canonical_postlogin_url: str,
    *,
    budget_ms: int = 10_000,
) -> dict:
    """Compare reuse scopes while the verified source page stays alive."""
    matrix = {}

    same_context_page = source_context.new_page()
    try:
        matrix["same_context_new_page"] = _navigate_and_probe(
            same_context_page,
            application_url,
            canonical_postlogin_url,
            session_storage,
            budget_ms,
        )
    finally:
        same_context_page.close()

    if source_browser is not None:
        same_browser_context = source_browser.new_context(
            accept_downloads=False,
            storage_state=str(storage_state_path),
        )
        try:
            same_browser_page = same_browser_context.new_page()
            matrix["same_browser_new_context"] = _navigate_and_probe(
                same_browser_page,
                application_url,
                canonical_postlogin_url,
                session_storage,
                budget_ms,
            )
        finally:
            same_browser_context.close()
    else:
        matrix["same_browser_new_context"] = {
            "reuse_proven": False,
            "not_tested_reason": "persistent_source_context",
            "raw_values_exposed": False,
        }

    separate_browser = playwright.chromium.launch(headless=False)
    separate_context = separate_browser.new_context(
        accept_downloads=False,
        storage_state=str(storage_state_path),
    )
    try:
        separate_page = separate_context.new_page()
        matrix["separate_browser_process"] = _navigate_and_probe(
            separate_page,
            application_url,
            canonical_postlogin_url,
            session_storage,
            budget_ms,
        )
    finally:
        separate_context.close()
        separate_browser.close()

    if matrix["separate_browser_process"].get("reuse_proven") is True:
        scope = "separate_browser_process"
    elif matrix["same_browser_new_context"].get("reuse_proven") is True:
        scope = "same_browser_process"
    elif matrix["same_context_new_page"].get("reuse_proven") is True:
        scope = "same_context"
    else:
        scope = "none"

    return {
        "live_handoff_reuse_proven": scope != "none",
        "strongest_reusable_scope": scope,
        "same_context_reuse_proven": matrix["same_context_new_page"].get("reuse_proven") is True,
        "same_browser_process_reuse_proven": matrix["same_browser_new_context"].get("reuse_proven") is True,
        "separate_browser_reuse_proven": matrix["separate_browser_process"].get("reuse_proven") is True,
        "reuse_matrix": matrix,
        "session_storage_entry_count": len(session_storage),
        "navigation_click_attempts": 0,
        "form_value_write_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "raw_values_exposed": False,
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
    session_storage_path = Path(request.session_storage_out) if request.session_storage_out else None
    postlogin_url_path = Path(request.postlogin_url_out) if request.postlogin_url_out else None
    live_handoff_report_path = Path(request.live_handoff_report_out) if request.live_handoff_report_out else None
    same_page_manifest_path = Path(request.same_page_manifest_out) if request.same_page_manifest_out else None
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if session_storage_path is not None:
        session_storage_path.parent.mkdir(parents=True, exist_ok=True)
    if postlogin_url_path is not None:
        postlogin_url_path.parent.mkdir(parents=True, exist_ok=True)
    if live_handoff_report_path is not None:
        live_handoff_report_path.parent.mkdir(parents=True, exist_ok=True)
    if same_page_manifest_path is not None:
        same_page_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        if request.user_data_dir:
            context = p.chromium.launch_persistent_context(
                request.user_data_dir,
                headless=False,
                accept_downloads=False,
            )
            pages = list(context.pages)
            if len(pages) > 1:
                context.close()
                raise RuntimeError("ADP_SESSION_BOOTSTRAP_UNREVIEWED_NEW_PAGE")
            page = pages[0] if pages else context.new_page()
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
        try:
            navigation = _open_reviewed_adp_target(page, request.application_url)
            if navigation["navigation_attempts"] > 1 or navigation["navigation_timeout_tolerated"]:
                print(json.dumps({
                    "initial_navigation_attempts": navigation["navigation_attempts"],
                    "initial_navigation_timeout_tolerated": navigation["navigation_timeout_tolerated"],
                    "raw_values_exposed": False,
                }, sort_keys=True))
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
            portal_hint_announced = False
            authenticated_portal_seen = False
            last_live_diagnostic = None
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
                    stage_observation_succeeded = True
                except Exception:
                    stage = {
                        "verification_code_visible": False,
                        "identity_surface_visible": False,
                        "identity_controls_visible_count": 0,
                        "raw_values_exposed": False,
                    }
                    stage_observation_succeeded = False

                authenticated_diagnostics = {
                    "authenticated_portal_observed": False,
                    "authenticated_application_steps_observed": False,
                    "authenticated_form_observed": False,
                    "observation_succeeded": False,
                    "raw_values_exposed": False,
                }
                if (
                    stage_observation_succeeded
                    and not stage["verification_code_visible"]
                    and not stage["identity_surface_visible"]
                ):
                    authenticated_diagnostics = _authenticated_form_diagnostics(
                        page,
                        request.application_url,
                    )
                authenticated = {
                    "authenticated_portal_observed": authenticated_diagnostics.get(
                        "authenticated_portal_observed", False
                    ),
                    "authenticated_application_steps_observed": authenticated_diagnostics.get(
                        "authenticated_application_steps_observed", False
                    ),
                    "authenticated_form_observed": authenticated_diagnostics.get(
                        "authenticated_form_observed", False
                    ),
                }

                try:
                    report = _sanitized_post_verification_report(page, verification_seen)
                    report_observation_succeeded = True
                    report_error_type = ""
                except Exception as exc:
                    report = None
                    report_observation_succeeded = False
                    report_error_type = type(exc).__name__

                now = time.monotonic()
                live_diagnostic = {
                    "stage_observation_succeeded": stage_observation_succeeded,
                    "verification_code_visible": stage.get("verification_code_visible") is True,
                    "identity_surface_visible": stage.get("identity_surface_visible") is True,
                    **authenticated_diagnostics,
                    "form_report_observation_succeeded": report_observation_succeeded,
                    "form_report_error_type": report_error_type,
                    "visible_control_count": (
                        report.get("visible_control_count", 0) if report is not None else 0
                    ),
                    "raw_values_exposed": False,
                }
                if (
                    live_diagnostic != last_live_diagnostic
                    and (
                        authenticated_diagnostics.get("postlogin_path_match") is True
                        or authenticated_diagnostics.get("authenticated_form_observed") is True
                        or not stage_observation_succeeded
                        or not report_observation_succeeded
                    )
                ):
                    print(json.dumps(
                        {"authenticated_surface_diagnostic": live_diagnostic},
                        sort_keys=True,
                    ))
                    last_live_diagnostic = live_diagnostic

                if not stage_observation_succeeded or report is None:
                    stable_since = None
                    last_signature = None
                    page.wait_for_timeout(1_000)
                    continue

                last_control_count = report["visible_control_count"]
                authenticated_portal_seen = authenticated_portal_seen or authenticated["authenticated_portal_observed"]
                if authenticated["authenticated_portal_observed"] and not authenticated["authenticated_form_observed"] and not portal_hint_announced:
                    print("Signed-in portal observed. Open Complete Your Application manually to show Personal Information; do not edit fields or click Next.")
                    portal_hint_announced = True
                if stage["verification_code_visible"]:
                    verification_seen = True
                    last_otp_seen = now
                    stable_since = None
                    last_signature = None
                elif (verification_seen or authenticated["authenticated_form_observed"]) and not stage["identity_surface_visible"]:
                    if not transition_announced:
                        if verification_seen:
                            print("Verification screen closed. Waiting for a stable form; keep the browser open.")
                        else:
                            print("Authenticated Personal Information surface observed without an OTP observation. Waiting for a stable form; keep the browser open.")
                        transition_announced = True
                    signature = _form_surface_signature(report)
                    basis = "observed_otp_transition" if verification_seen else "authenticated_postlogin_form"
                    surface_key = (str(page.url), signature, basis)
                    if not signature:
                        stable_since = None
                        last_signature = None
                    elif surface_key != last_signature:
                        stable_since = now
                        last_signature = surface_key
                    elif (
                        stable_since is not None
                        and now - stable_since >= (MIN_STABLE_SURFACE_SECONDS if verification_seen else MIN_POST_VERIFICATION_SECONDS)
                        and (not verification_seen or (
                            last_otp_seen is not None and now - last_otp_seen >= MIN_POST_VERIFICATION_SECONDS
                        ))
                    ):
                        session_storage = {}
                        session_storage_evidence = {
                            "session_storage_entry_count": 0,
                            "session_storage_byte_count": 0,
                            "session_storage_values_exposed": False,
                        }
                        if session_storage_path is not None:
                            session_storage, session_storage_evidence = _capture_session_storage(page)
                            session_storage_path.write_text(
                                json.dumps(session_storage, ensure_ascii=False, sort_keys=True) + "\n",
                                encoding="utf-8",
                            )
                        canonical_url_evidence = _authenticated_form_diagnostics(
                            page,
                            request.application_url,
                        )
                        canonical_url_exported = False
                        if postlogin_url_path is not None:
                            if not (
                                canonical_url_evidence.get("postlogin_path_match") is True
                                and canonical_url_evidence.get("cid_match") is True
                                and canonical_url_evidence.get("ccid_match") is True
                                and canonical_url_evidence.get("jobid_match") is True
                            ):
                                raise RuntimeError("ADP_SESSION_BOOTSTRAP_CANONICAL_POSTLOGIN_URL_INVALID")
                            postlogin_url_path.write_text(str(page.url) + "\n", encoding="utf-8")
                            canonical_url_exported = True

                        storage_evidence = _export_storage_state(context, storage_path)

                        same_page_manifest = None
                        if same_page_manifest_path is not None:
                            same_page_manifest = extract_same_page_manifest(
                                page,
                                request.application_url,
                                timeout_ms=20_000,
                                render_wait_ms=5_000,
                            )
                            same_page_manifest_path.write_text(
                                json.dumps(
                                    same_page_manifest,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    indent=2,
                                ) + "\n",
                                encoding="utf-8",
                            )
                            print(json.dumps({
                                "same_page_manifest": {
                                    "manifest_version": same_page_manifest.get("manifest_version", ""),
                                    "surface_fingerprint": same_page_manifest.get("surface_fingerprint", ""),
                                    "visible_control_count": same_page_manifest.get("visible_control_count", 0),
                                    "visible_action_count": same_page_manifest.get("visible_action_count", 0),
                                    "file_control_count": same_page_manifest.get("file_control_count", 0),
                                    "password_control_count": same_page_manifest.get("password_control_count", 0),
                                    "form_value_write_attempts": 0,
                                    "file_upload_attempts": 0,
                                    "submit_attempts": 0,
                                    "raw_values_exposed": False,
                                }
                            }, sort_keys=True))

                        live_handoff = None
                        if live_handoff_report_path is not None:
                            live_handoff = _live_handoff_probe(
                                p,
                                browser,
                                context,
                                request.application_url,
                                storage_path,
                                session_storage,
                                str(page.url),
                            )
                            live_handoff_report_path.write_text(
                                json.dumps(
                                    live_handoff,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    indent=2,
                                ) + "\n",
                                encoding="utf-8",
                            )
                            print(json.dumps(
                                {"live_handoff_probe": live_handoff},
                                sort_keys=True,
                            ))

                        report.update({
                            "verification_seen": verification_seen,
                            "verification_completed": verification_seen,
                            "verification_basis": basis,
                            **authenticated,
                            "post_verification_surface_stable": True,
                            "visible_form_control_count": len(signature),
                            "storage_state_exported": True,
                            "canonical_postlogin_url_exported": canonical_url_exported,
                            **storage_evidence,
                            "session_storage_exported": session_storage_path is not None,
                            **session_storage_evidence,
                            "session_reuse_proven": False,
                            "same_page_manifest_exported": isinstance(same_page_manifest, dict),
                            "same_page_manifest_fingerprint": (
                                str(same_page_manifest.get("surface_fingerprint", ""))
                                if isinstance(same_page_manifest, dict)
                                else ""
                            ),
                            "same_page_manifest_visible_control_count": (
                                int(same_page_manifest.get("visible_control_count", 0))
                                if isinstance(same_page_manifest, dict)
                                else 0
                            ),
                            "same_page_manifest_file_control_count": (
                                int(same_page_manifest.get("file_control_count", 0))
                                if isinstance(same_page_manifest, dict)
                                else 0
                            ),
                            "live_handoff_reuse_proven": (
                                live_handoff.get("live_handoff_reuse_proven") is True
                                if isinstance(live_handoff, dict)
                                else False
                            ),
                            "live_handoff_strongest_reusable_scope": (
                                str(live_handoff.get("strongest_reusable_scope", ""))
                                if isinstance(live_handoff, dict)
                                else ""
                            ),
                        })
                        report_path.write_text(
                            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        label = "post-verification" if verification_seen else "authenticated Personal Information"
                        print(f"Stable {label} form observed. Candidate session exported locally; fresh-browser reuse must pass before secret provisioning.")
                        return report
                else:
                    stable_since = None
                    last_signature = None
                page.wait_for_timeout(1_000)

            print(json.dumps({
                "verification_seen": verification_seen,
                "authenticated_portal_seen": authenticated_portal_seen,
                "verification_completed": False,
                "visible_control_count": last_control_count,
                "storage_state_exported": False,
                "raw_values_exposed": False,
            }, sort_keys=True))
            reason = "FORM_NOT_READY" if verification_seen or authenticated_portal_seen else "VERIFICATION_TIMEOUT"
            raise TimeoutError(f"ADP_SESSION_BOOTSTRAP_{reason}")
        finally:
            context.close()
            if browser is not None:
                browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local ADP verified-session bootstrap")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--storage-state-out", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--session-storage-out", default="")
    parser.add_argument("--postlogin-url-out", default="")
    parser.add_argument("--user-data-dir", default="")
    parser.add_argument("--live-handoff-report-out", default="")
    parser.add_argument("--same-page-manifest-out", default="")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    report = run_bootstrap(AdpVerifiedSessionBootstrapRequest(
        application_url=args.application_url,
        storage_state_out=args.storage_state_out,
        report_out=args.report_out,
        session_storage_out=args.session_storage_out,
        postlogin_url_out=args.postlogin_url_out,
        user_data_dir=args.user_data_dir,
        live_handoff_report_out=args.live_handoff_report_out,
        same_page_manifest_out=args.same_page_manifest_out,
        timeout_seconds=args.timeout_seconds,
    ))
    print(json.dumps({
        "verification_seen": report.get("verification_seen") is True,
        "verification_completed": report.get("verification_completed") is True,
        "verification_basis": report.get("verification_basis", ""),
        "authenticated_form_observed": report.get("authenticated_form_observed") is True,
        "visible_control_count": report.get("visible_control_count", 0),
        "storage_state_exported": report.get("storage_state_exported") is True,
        "canonical_postlogin_url_exported": report.get("canonical_postlogin_url_exported") is True,
        "same_page_manifest_exported": report.get("same_page_manifest_exported") is True,
        "same_page_manifest_fingerprint": report.get("same_page_manifest_fingerprint", ""),
        "same_page_manifest_visible_control_count": report.get("same_page_manifest_visible_control_count", 0),
        "same_page_manifest_file_control_count": report.get("same_page_manifest_file_control_count", 0),
        "live_handoff_reuse_proven": report.get("live_handoff_reuse_proven") is True,
        "live_handoff_strongest_reusable_scope": report.get("live_handoff_strongest_reusable_scope", ""),
        "storage_indexed_db_database_count": report.get("storage_indexed_db_database_count", 0),
        "storage_indexed_db_origin_count": report.get("storage_indexed_db_origin_count", 0),
        "onetrust_consent_cookie_present": report.get("onetrust_consent_cookie_present") is True,
        "onetrust_alert_closed_cookie_present": report.get("onetrust_alert_closed_cookie_present") is True,
        "session_storage_exported": report.get("session_storage_exported") is True,
        "session_storage_entry_count": report.get("session_storage_entry_count", 0),
        "session_storage_values_exposed": False,
        "raw_values_exposed": report.get("raw_values_exposed") is True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_VERIFIED_SESSION_BOOTSTRAP_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
