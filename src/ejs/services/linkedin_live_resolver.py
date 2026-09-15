from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

LINKEDIN_HOST_SUFFIX = "linkedin.com"
LINKEDIN_EXTERNAL_APPLY_PATH = "/jobs/view/externalApply/"


def _is_linkedin_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == LINKEDIN_HOST_SUFFIX or host.endswith("." + LINKEDIN_HOST_SUFFIX)


def validate_linkedin_job_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _is_linkedin_host(parsed.hostname):
        raise ValueError("target_url must be an HTTPS LinkedIn URL")
    if "/jobs/view/" not in parsed.path:
        raise ValueError("target_url must be a LinkedIn job-view URL")


def extract_external_apply_target(href: str) -> str | None:
    """Resolve an employer/ATS target without navigating or clicking.

    LinkedIn public job pages may expose either a direct external HTTPS href or
    an /externalApply/ wrapper whose `url` query parameter contains the target.
    The resolver only parses the already-rendered href string.
    """
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme != "https":
        return None
    if not _is_linkedin_host(parsed.hostname):
        return href
    if LINKEDIN_EXTERNAL_APPLY_PATH not in parsed.path:
        return None
    values = parse_qs(parsed.query).get("url") or []
    if not values:
        return None
    candidate = unquote(values[0]).strip()
    target = urlparse(candidate)
    if target.scheme != "https" or not target.hostname or _is_linkedin_host(target.hostname):
        return None
    return candidate


def classify_source_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    anchors = snapshot.get("apply_anchors") or []
    buttons = snapshot.get("apply_buttons") or []
    body_signals = set(snapshot.get("body_signals") or [])

    for item in anchors:
        target = extract_external_apply_target(str(item.get("href") or ""))
        if target:
            return {
                "resolution_state": "external_apply_resolved",
                "error_code": None,
                "external_apply_url": target,
                "human_action_required": False,
                "ats_inspection_candidate": True,
            }

    anchor_kinds = {str(item.get("kind") or "") for item in anchors}
    button_kinds = {str(item.get("kind") or "") for item in buttons}
    if "easy_apply" in anchor_kinds or "easy_apply" in button_kinds or "easy_apply" in body_signals:
        return {
            "resolution_state": "linkedin_easy_apply",
            "error_code": "LINKEDIN_EASY_APPLY_BOUNDARY",
            "external_apply_url": None,
            "human_action_required": True,
            "ats_inspection_candidate": False,
        }
    if "login_boundary" in body_signals:
        return {
            "resolution_state": "login_boundary",
            "error_code": "LINKEDIN_LOGIN_BOUNDARY",
            "external_apply_url": None,
            "human_action_required": True,
            "ats_inspection_candidate": False,
        }
    if "challenge_boundary" in body_signals:
        return {
            "resolution_state": "challenge_boundary",
            "error_code": "LINKEDIN_CHALLENGE_BOUNDARY",
            "external_apply_url": None,
            "human_action_required": True,
            "ats_inspection_candidate": False,
        }
    if "external_apply_redirect" in anchor_kinds:
        return {
            "resolution_state": "external_apply_unresolved",
            "error_code": "LINKEDIN_EXTERNAL_APPLY_TARGET_NOT_EXPOSED",
            "external_apply_url": None,
            "human_action_required": True,
            "ats_inspection_candidate": False,
        }
    return {
        "resolution_state": "apply_target_not_discovered",
        "error_code": "LINKEDIN_APPLY_TARGET_NOT_DISCOVERED",
        "external_apply_url": None,
        "human_action_required": True,
        "ats_inspection_candidate": False,
    }


def _normalize_label(value: str) -> str:
    return " ".join(value.lower().split())


def _kind_for_label(label: str, href: str = "") -> str | None:
    normalized = _normalize_label(label)
    if "easy apply" in normalized:
        return "easy_apply"
    if normalized in {"apply", "apply now", "apply on company website", "apply on company site"}:
        if LINKEDIN_EXTERNAL_APPLY_PATH in href:
            return "external_apply_redirect"
        return "apply"
    return None


def resolve_linkedin_source(url: str, *, timeout_ms: int = 30000) -> dict[str, Any]:
    validate_linkedin_job_url(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        network_idle_observed = False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
                network_idle_observed = True
            except PlaywrightTimeoutError:
                pass

            snapshot = page.evaluate(
                """
                () => {
                  const norm = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
                  const anchors = [];
                  for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                    const label = norm(a.innerText || a.getAttribute('aria-label') || a.textContent);
                    if (!label) continue;
                    const lower = label.toLowerCase();
                    if (lower === 'apply' || lower === 'apply now' || lower.includes('easy apply') ||
                        lower.includes('apply on company')) {
                      anchors.push({label, href: a.href || ''});
                    }
                  }
                  const buttons = [];
                  for (const b of Array.from(document.querySelectorAll('button,[role="button"]'))) {
                    const label = norm(b.innerText || b.getAttribute('aria-label') || b.textContent);
                    if (!label) continue;
                    const lower = label.toLowerCase();
                    if (lower === 'apply' || lower === 'apply now' || lower.includes('easy apply')) {
                      buttons.push({label});
                    }
                  }
                  const body = norm(document.body ? document.body.innerText : '').toLowerCase();
                  const bodySignals = [];
                  if (body.includes('easy apply')) bodySignals.push('easy_apply');
                  if (body.includes('sign in') && (body.includes('join linkedin') || body.includes('new to linkedin'))) {
                    bodySignals.push('login_boundary');
                  }
                  if (body.includes('security verification') || body.includes('verify you are human') || body.includes('captcha')) {
                    bodySignals.push('challenge_boundary');
                  }
                  return {
                    ready_state: document.readyState,
                    body_present: Boolean(document.body),
                    body_text_length: body.length,
                    anchor_count: document.querySelectorAll('a[href]').length,
                    button_count: document.querySelectorAll('button,[role="button"]').length,
                    apply_anchors: anchors.slice(0, 12),
                    apply_buttons: buttons.slice(0, 12),
                    body_signals: bodySignals,
                  };
                }
                """
            )
        finally:
            final_url = page.url
            page_title = page.title()
            browser.close()

    enriched_anchors = []
    for item in snapshot.get("apply_anchors") or []:
        label = str(item.get("label") or "")
        href = str(item.get("href") or "")
        enriched_anchors.append({
            "kind": _kind_for_label(label, href),
            "href": href,
            "label_present": bool(label),
        })
    enriched_buttons = []
    for item in snapshot.get("apply_buttons") or []:
        label = str(item.get("label") or "")
        enriched_buttons.append({
            "kind": _kind_for_label(label),
            "label_present": bool(label),
        })
    safe_snapshot = {
        "ready_state": snapshot.get("ready_state"),
        "body_present": bool(snapshot.get("body_present")),
        "body_text_length": int(snapshot.get("body_text_length") or 0),
        "anchor_count": int(snapshot.get("anchor_count") or 0),
        "button_count": int(snapshot.get("button_count") or 0),
        "apply_anchors": enriched_anchors,
        "apply_buttons": enriched_buttons,
        "body_signals": list(snapshot.get("body_signals") or []),
    }
    classification = classify_source_snapshot(safe_snapshot)
    return {
        "inspection_only": True,
        "source": "linkedin",
        "source_url": url,
        "final_url": final_url,
        "page_title": page_title,
        "network_idle_observed": network_idle_observed,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "click_attempts": 0,
        "credential_entry_attempts": 0,
        "source_resolution_ready": classification["resolution_state"] == "external_apply_resolved",
        "automation_execution_ready": False,
        "diagnostics": safe_snapshot,
        **classification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a LinkedIn job source without mutation authority")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = resolve_linkedin_source(args.url)
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "resolution_state": report["resolution_state"],
        "error_code": report["error_code"],
        "source_resolution_ready": report["source_resolution_ready"],
        "inspection_only": report["inspection_only"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
