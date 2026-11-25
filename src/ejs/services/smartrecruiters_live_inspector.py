"""Read-only live SmartRecruiters inspection for manifest preparation.

The inspector never fills fields, clicks controls, uploads files, or submits.
It is intentionally separate from the canary executor.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse

from ejs.services.browser_worker import BrowserRuntimeConfig, installed_chrome
from ejs.services.smartrecruiters_shadow import inspect_shadow_form, summarize_shadow_form



# A narrowly scoped exception to passive inspection: one reviewed entry into
# SmartRecruiters' application UI, never a form write/upload/submit.
# Only the explicitly opt-in CLI/workflow mode uses this capability.
_BLOCKING_DENIALS = {"NON_GET_REQUEST", "UNTRUSTED_NAVIGATION", "REDIRECT_UNREVIEWED",
                     "INVALID_REQUEST_URL", "WEBSOCKET_UNSUPPORTED", "READ_BUDGET_EXHAUSTED",
                     "HTTP_ACCESS_OR_RATE_BOUNDARY", "HTTP_RESPONSE_BOUNDARY", "READ_TIMEOUT",
                     "READ_TRANSPORT_FAILED"}
_CHALLENGE_MARKERS = ("verify you are human", "checking your browser",
                      "complete the captcha", "captcha challenge")
_APPLY_LABELS = frozenset({"i'm interested", "apply", "apply now", "apply for this job"})
_APPLY_CTA_SCAN = r"""
() => {
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    for (let node = el; node; node = node.parentElement || (node.getRootNode().host || null)) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || node.hidden) return false;
    }
    return el.getClientRects().length > 0;
  };
  const results = [];
  const visit = root => {
    for (const el of Array.from(root.querySelectorAll('*'))) {
      if (el.matches('a[href],button')) {
        const label = normalize(el.getAttribute('aria-label') || el.textContent);
        const tag = el.tagName.toLowerCase();
        results.push({
          tag, label,
          href: tag === 'a' ? el.href : '',
          type: el.getAttribute('type') || '',
          visible: visible(el),
          disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
          in_form: !!el.closest('form')
        });
      }
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(document);
  return results;
}
"""


def _is_trusted_apply_destination(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "jobs.smartrecruiters.com"
        and parsed.port in (None, 443)
        and not parsed.username and not parsed.password
        and (parsed.path == "/oneclick-ui" or parsed.path.startswith("/oneclick-ui/"))
    )


def _is_trusted_navigation_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "jobs.smartrecruiters.com"
        and parsed.port in (None, 443)
        and not parsed.username and not parsed.password
    )


def select_apply_entry(observed: list[dict], posting_url: str) -> dict:
    """Pure, fail-closed choice; never guesses a URL from a requisition ID."""
    if not _is_trusted_navigation_url(posting_url):
        return {"status": "UNTRUSTED_POSTING_URL"}
    matches = []
    for item in observed:
        if not isinstance(item, dict) or item.get("visible") is not True or item.get("disabled") is not False:
            continue
        if item.get("in_form") is not False:
            continue
        label = " ".join(str(item.get("label", "")).replace("’", "'").casefold().split())
        if label not in _APPLY_LABELS:
            continue
        tag = item.get("tag")
        if tag == "a":
            href = str(item.get("href") or "")
            if _is_trusted_apply_destination(href) and href != posting_url:
                matches.append({"kind": "link", "href": href, "label": item.get("label", "")})
        elif tag == "button" and str(item.get("type") or "").casefold() in ("", "button"):
            matches.append({"kind": "button", "label": item.get("label", "")})
    # Responsive job pages often render desktop and mobile CTA anchors
    # pointing at the *same observed destination*. This is one navigation
    # decision, not two conflicting destinations. Never deduplicate buttons:
    # clicking a button may have page-specific side effects.
    distinct = {}
    for match in matches:
        key = ("link", match["href"]) if match["kind"] == "link" else (
            "button", len(distinct)
        )
        distinct[key] = match
    if len(distinct) != 1:
        return {
            "status": "NO_APPROVED_CTA" if not distinct else "AMBIGUOUS_APPLY_CTA",
            "candidate_count": len(matches),
            "distinct_destination_count": len(distinct),
            "link_candidate_count": sum(x["kind"] == "link" for x in matches),
            "button_candidate_count": sum(x["kind"] == "button" for x in matches),
        }
    return {"status": "APPROVED_CTA", **next(iter(distinct.values())),
            "candidate_count": len(matches), "distinct_destination_count": 1}


def classify_blocked_request_purpose(method: str, resource_type: str, url: str, is_navigation: bool) -> str:
    """Conservative, fixed-category hypothesis only; no endpoint or payload retention.

    A POST is never treated as harmless bootstrap without independent evidence.
    Classification does not grant network or execution permissions.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if is_navigation and host != "jobs.smartrecruiters.com" and not host.endswith(".smartrecruiters.com"):
        return "external_navigation_unknown"
    if method.upper() not in {"GET", "HEAD"}:
        return "non_read_request_unknown"
    if resource_type in {"xhr", "fetch"}:
        return "read_api_request_unverified"
    if resource_type == "document":
        return "document_navigation_unverified"
    return "asset_or_other_unverified"


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_REDIRECT_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OTHER"})
_REDIRECT_RESOURCE_TYPES = frozenset({
    "document", "script", "stylesheet", "image", "font", "xhr", "fetch", "other",
})
_REDIRECT_TRUST_CLASSES = frozenset({
    "trusted_https_host", "untrusted_or_external", "invalid_or_missing",
})
_REDIRECT_PATH_CATEGORIES = frozenset({
    "posting_detail", "oneclick_application", "api", "auth_or_account", "asset",
    "company_page", "root", "other_same_origin", "external", "invalid_or_missing",
})
_REDIRECT_SCHEME_TRANSITIONS = frozenset({
    "https_to_https", "https_to_http", "http_to_https", "http_to_http",
    "non_http", "unknown",
})
_REDIRECT_CLASSIFICATIONS = frozenset({
    "normal_ats_navigation_candidate", "same_origin_navigation_unreviewed",
    "cross_origin_redirect", "subresource_redirect", "malformed_redirect",
    "non_redirect_3xx",
})
_REDIRECT_DENY_REASONS = frozenset({
    "REDIRECT_LOCATION_MISSING", "REDIRECT_LOCATION_INVALID",
    "REDIRECT_CREDENTIALS_REJECTED", "REDIRECT_SCHEME_UNSUPPORTED",
    "REDIRECT_CROSS_ORIGIN", "REDIRECT_SUBRESOURCE_UNREVIEWED",
    "REDIRECT_SAME_ORIGIN_UNREVIEWED", "REDIRECT_STATUS_UNSUPPORTED",
    "REDIRECT_SAME_URL",
})
_EVIDENCE_HOSTS = frozenset({"jobs.smartrecruiters.com", "api.smartrecruiters.com", "careers.smartrecruiters.com"})
_REDIRECT_DOCUMENT_CATEGORIES = frozenset({"posting_detail", "oneclick_application"})
_REDIRECT_DIAGNOSTIC_FIELDS = (
    "status", "method", "resource_type", "navigation", "source_host",
    "location_present", "destination_valid", "destination_host",
    "destination_trust_class", "same_origin", "destination_path_category",
    "scheme_transition", "classification", "deny_reason", "policy_candidate",
    "permission_granted", "access_restriction_evidence", "review_status",
)
_REDIRECT_DIAGNOSTIC_FIELD_SET = frozenset(_REDIRECT_DIAGNOSTIC_FIELDS)
REDIRECT_DIAGNOSTIC_LIMIT = 12


def _parse_url(url: str):
    try:
        return urlparse(url)
    except (TypeError, ValueError):
        return None


def _evidence_host(url: str) -> tuple[str, str]:
    """Return a host safe to persist plus its trust class; never a path or query."""
    parsed = _parse_url(url)
    if parsed is None:
        return "", "invalid_or_missing"
    try:
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return "", "invalid_or_missing"
    if (parsed.scheme == "https" and host and not parsed.username and not parsed.password
            and port in (None, 443)
            and host in _EVIDENCE_HOSTS):
        return host, "trusted_https_host"
    if host:
        return "external_redacted", "untrusted_or_external"
    return "", "invalid_or_missing"


def _redirect_path_category(path: str) -> str:
    """Map a path to a fixed category without retaining the path itself."""
    path = path or "/"
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return "root"
    first = segments[0].casefold()
    if first == "oneclick-ui":
        return "oneclick_application"
    if first in {"api", "v1"}:
        return "api"
    if first in {"login", "signin", "sign-in", "auth", "oauth", "account", "accounts"}:
        return "auth_or_account"
    extension = path.rsplit("/", 1)[-1].rsplit(".", 1)[-1].casefold()
    if first in {"assets", "static", "fonts", "images", "css", "js"} or extension in {
        "js", "css", "png", "jpg", "jpeg", "gif", "svg", "ico", "woff", "woff2", "ttf",
    }:
        return "asset"
    if len(segments) >= 2 and re.fullmatch(r"\d{6,20}(?:-[A-Za-z0-9_-]+)?", segments[1]):
        return "posting_detail"
    if len(segments) == 1:
        return "company_page"
    return "other_same_origin"


def _scheme_transition(source_scheme: str, destination_scheme: str) -> str:
    if source_scheme not in {"http", "https"} or destination_scheme not in {"http", "https"}:
        return "non_http"
    return f"{source_scheme}_to_{destination_scheme}"


def summarize_redirect(*, status: int, method: str, resource_type: str, source_url: str,
                       location: str | None, is_navigation: bool) -> dict:
    """Bounded, sanitized evidence for one intercepted 3xx response.

    The raw Location header, path, query, fragment and credentials are never
    retained. ``policy_candidate`` only marks a same-origin document redirect
    that could be reviewed for a future narrow policy; it never grants
    permission and never changes the network boundary.
    """
    if type(status) is not int or not 300 <= status <= 399:
        raise ValueError("REDIRECT_STATUS_INVALID")
    if location is not None and not isinstance(location, str):
        raise ValueError("REDIRECT_LOCATION_TYPE")
    method_value = (method.upper() if isinstance(method, str) else "OTHER")
    if method_value not in _REDIRECT_METHODS:
        method_value = "OTHER"
    resource_value = resource_type if resource_type in _REDIRECT_RESOURCE_TYPES else "other"
    source_parsed = _parse_url(source_url)
    source_host, _ = _evidence_host(source_url)
    location_present = isinstance(location, str) and bool(location.strip())
    destination_valid = False
    destination_host = ""
    destination_trust = "invalid_or_missing"
    same_origin = False
    path_category = "invalid_or_missing"
    transition = "unknown"
    deny_reason = "REDIRECT_LOCATION_MISSING"
    if location_present:
        if any(ord(character) < 0x20 or ord(character) == 0x7f for character in location):
            deny_reason = "REDIRECT_LOCATION_INVALID"
        else:
            try:
                stripped = location.strip()
                raw_destination = _parse_url(stripped)
                if raw_destination is not None and raw_destination.scheme:
                    # Never let urljoin repair a malformed absolute Location
                    # (for example "https://" or "https:///host") into the
                    # source URL.
                    absolute = stripped
                    destination = raw_destination
                else:
                    absolute = urljoin(source_url, stripped)
                    destination = _parse_url(absolute)
                if destination is not None:
                    _ = destination.port
                    if "\\" in stripped:
                        destination = None
            except (TypeError, ValueError):
                destination = None
            if destination is not None and destination.scheme and destination.scheme not in {"http", "https"}:
                deny_reason = "REDIRECT_SCHEME_UNSUPPORTED"
            elif destination is None or not destination.scheme or not destination.hostname:
                deny_reason = "REDIRECT_LOCATION_INVALID"
            elif destination.username or destination.password:
                deny_reason = "REDIRECT_CREDENTIALS_REJECTED"
            elif absolute.split("#", 1)[0] == source_url.split("#", 1)[0]:
                deny_reason = "REDIRECT_SAME_URL"
            else:
                destination_valid = True
                destination_host, destination_trust = _evidence_host(absolute)
                source_origin = _origin(source_url) if source_parsed else ""
                destination_origin = _origin(absolute)
                same_origin = bool(source_origin) and source_origin == destination_origin
                path_category = (_redirect_path_category(destination.path)
                                 if destination_trust == "trusted_https_host" else "external")
                transition = _scheme_transition(
                    source_parsed.scheme if source_parsed else "", destination.scheme
                )
    policy_candidate = False
    if status not in _REDIRECT_STATUSES:
        classification = "non_redirect_3xx"
        deny_reason = "REDIRECT_STATUS_UNSUPPORTED"
    elif not destination_valid:
        classification = "malformed_redirect"
    elif not same_origin:
        classification = "cross_origin_redirect"
        deny_reason = "REDIRECT_CROSS_ORIGIN"
    elif not is_navigation:
        classification = "subresource_redirect"
        deny_reason = "REDIRECT_SUBRESOURCE_UNREVIEWED"
    elif (resource_value == "document" and method_value in {"GET", "HEAD"} and transition == "https_to_https"
          and destination_trust == "trusted_https_host"
          and path_category in _REDIRECT_DOCUMENT_CATEGORIES):
        classification = "normal_ats_navigation_candidate"
        deny_reason = "REDIRECT_SAME_ORIGIN_UNREVIEWED"
        policy_candidate = True
    else:
        classification = "same_origin_navigation_unreviewed"
        deny_reason = "REDIRECT_SAME_ORIGIN_UNREVIEWED"
    return {
        "status": status,
        "method": method_value,
        "resource_type": resource_value,
        "navigation": bool(is_navigation),
        "source_host": source_host,
        "location_present": location_present,
        "destination_valid": destination_valid,
        "destination_host": destination_host,
        "destination_trust_class": destination_trust,
        "same_origin": same_origin,
        "destination_path_category": path_category,
        "scheme_transition": transition,
        "classification": classification,
        "deny_reason": deny_reason,
        "policy_candidate": policy_candidate,
        "permission_granted": False,
        "access_restriction_evidence": False,
        "review_status": "unreviewed",
    }


def sanitize_redirect_diagnostics(value) -> list[dict]:
    """Strictly rebuild redirect evidence for persistence; reject unknown fields."""
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list) or len(value) > REDIRECT_DIAGNOSTIC_LIMIT:
        raise ValueError("REDIRECT_DIAGNOSTICS_INVALID")
    cleaned = []
    for item in value:
        if not isinstance(item, dict) or set(item) != _REDIRECT_DIAGNOSTIC_FIELD_SET:
            raise ValueError("REDIRECT_DIAGNOSTIC_FIELDS")
        if type(item["status"]) is not int or not 300 <= item["status"] <= 399:
            raise ValueError("REDIRECT_DIAGNOSTIC_STATUS")
        if item["method"] not in _REDIRECT_METHODS:
            raise ValueError("REDIRECT_DIAGNOSTIC_METHOD")
        if item["resource_type"] not in _REDIRECT_RESOURCE_TYPES:
            raise ValueError("REDIRECT_DIAGNOSTIC_RESOURCE")
        for key in ("navigation", "location_present", "destination_valid", "same_origin",
                    "policy_candidate", "permission_granted", "access_restriction_evidence"):
            if type(item[key]) is not bool:
                raise ValueError("REDIRECT_DIAGNOSTIC_BOOLEAN")
        for key in ("source_host", "destination_host"):
            host = item[key]
            if not isinstance(host, str) or not (
                    host in {"", "external_redacted"} or host in _EVIDENCE_HOSTS):
                raise ValueError("REDIRECT_DIAGNOSTIC_HOST")
        if item["destination_trust_class"] not in _REDIRECT_TRUST_CLASSES:
            raise ValueError("REDIRECT_DIAGNOSTIC_TRUST")
        if item["destination_path_category"] not in _REDIRECT_PATH_CATEGORIES:
            raise ValueError("REDIRECT_DIAGNOSTIC_PATH")
        if item["scheme_transition"] not in _REDIRECT_SCHEME_TRANSITIONS:
            raise ValueError("REDIRECT_DIAGNOSTIC_SCHEME")
        if item["classification"] not in _REDIRECT_CLASSIFICATIONS:
            raise ValueError("REDIRECT_DIAGNOSTIC_CLASSIFICATION")
        if item["deny_reason"] not in _REDIRECT_DENY_REASONS:
            raise ValueError("REDIRECT_DIAGNOSTIC_REASON")
        if item["permission_granted"] is not False:
            raise ValueError("REDIRECT_DIAGNOSTIC_PERMISSION")
        if item["access_restriction_evidence"] is not False:
            raise ValueError("REDIRECT_DIAGNOSTIC_ACCESS_EVIDENCE")
        if item["review_status"] != "unreviewed":
            raise ValueError("REDIRECT_DIAGNOSTIC_REVIEW")
        if item["destination_valid"] and not item["location_present"]:
            raise ValueError("REDIRECT_DIAGNOSTIC_CONSISTENCY")
        if item["destination_valid"] and item["destination_trust_class"] == "invalid_or_missing":
            raise ValueError("REDIRECT_DIAGNOSTIC_CONSISTENCY")
        if item["same_origin"] and not item["destination_valid"]:
            raise ValueError("REDIRECT_DIAGNOSTIC_CONSISTENCY")
        if item["policy_candidate"] != (item["classification"] == "normal_ats_navigation_candidate"):
            raise ValueError("REDIRECT_DIAGNOSTIC_CONSISTENCY")
        cleaned.append({key: item[key] for key in _REDIRECT_DIAGNOSTIC_FIELDS})
    return cleaned


def summarize_blocked_request(method: str, resource_type: str, url: str, is_navigation: bool) -> dict:
    """Bounded, non-identifying network-fence evidence; never retain URLs or query strings."""
    host, trust_class = _evidence_host(url)
    trusted_host = trust_class == "trusted_https_host"
    if not trusted_host:
        host = "external_redacted"
    return {
        "method": method.upper() if method.upper() in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"} else "OTHER",
        "resource_type": resource_type if resource_type in {
            "document", "script", "stylesheet", "image", "font", "xhr", "fetch", "other"
        } else "other",
        "host": host,
        "navigation": bool(is_navigation),
        "deny_reason": "NON_GET_REQUEST" if method.upper() not in {"GET", "HEAD"} else "UNTRUSTED_NAVIGATION",
        "trust_class": "trusted_https_host" if trusted_host else "untrusted_or_external",
        "purpose_hypothesis": classify_blocked_request_purpose(method, resource_type, url, is_navigation),
        "permission_granted": False,
    }


class InspectionNetworkFence:
    """Deny unsupported requests for the lifetime of the inspection context."""

    def __init__(self, posting_url: str, *, fetch_timeout_ms: int):
        self.origin = _origin(posting_url)
        self.fetch_timeout_ms = fetch_timeout_ms
        self.denials: set[str] = set()
        self.samples: list[dict] = []
        self.redirects: list[dict] = []
        self.redirect_count = 0
        self.challenge_destination_denied = False
        self.http_status = None
        self.retry_after = None
        self.reads = 0

    def route(self, route):
        request = route.request
        method, resource, url = request.method, request.resource_type, request.url
        navigation = request.is_navigation_request()
        try:
            origin = _origin(url)
            parsed = urlparse(url)
            same_surface = (origin == self.origin and not parsed.username and not parsed.password)
        except ValueError:
            self._deny(route, "INVALID_REQUEST_URL")
            return
        if method.upper() not in {"GET", "HEAD"}:
            self._deny(route, "NON_GET_REQUEST")
        elif not same_surface:
            self.challenge_destination_denied |= navigation and is_known_captcha_origin(origin)
            self._deny(route, "UNTRUSTED_NAVIGATION" if navigation else "UNSUPPORTED_EXTERNAL_RESOURCE")
        elif self.http_status in {401, 403, 429}:
            self._deny(route, "HTTP_ACCESS_OR_RATE_BOUNDARY")
        elif self.reads >= 100:
            self._deny(route, "READ_BUDGET_EXHAUSTED")
        else:
            self.reads += 1
            # Playwright only guarantees routing the initial URL of a redirect.
            # Fetch a single hop and stop on redirects instead of fulfilling one.
            try:
                response = route.fetch(max_redirects=0, max_retries=0, timeout=self.fetch_timeout_ms)
            except Exception as exc:
                self._deny(route, "READ_TIMEOUT" if type(exc).__name__ == "TimeoutError" else "READ_TRANSPORT_FAILED")
                return
            try:
                status = response.status
                if status in {401, 403, 429} or (navigation and status >= 400):
                    if self.http_status not in {401, 403, 429} or status == 429:
                        self.http_status = status
                        self.retry_after = response.headers.get("retry-after")
                if 300 <= status < 400:
                    self._record_redirect(response, request)
                    self._deny(route, "REDIRECT_UNREVIEWED")
                elif status in {401, 403, 429} or (navigation and status >= 400):
                    self._deny(route, "HTTP_RESPONSE_BOUNDARY")
                else:
                    route.fulfill(response=response)
            finally:
                response.dispose()

    def _record_redirect(self, response, request) -> None:
        """Retain bounded, sanitized evidence for a denied 3xx response."""
        self.redirect_count += 1
        if len(self.redirects) >= REDIRECT_DIAGNOSTIC_LIMIT:
            return
        headers = getattr(response, "headers", None) or {}
        try:
            location = headers.get("location")
            if location is None:
                location = headers.get("Location")
        except Exception:
            location = None
        try:
            diagnostic = summarize_redirect(
                status=response.status,
                method=request.method,
                resource_type=request.resource_type,
                source_url=request.url,
                location=location,
                is_navigation=request.is_navigation_request(),
            )
        except Exception:
            # Diagnostics must never weaken or crash the fail-closed fence.
            return
        self.redirects.append(diagnostic)

    def _deny(self, route, code: str):
        self.denials.add(code)
        if len(self.samples) < 12 and code != "INVALID_REQUEST_URL":
            req = route.request
            sample = summarize_blocked_request(req.method, req.resource_type, req.url, req.is_navigation_request())
            sample["deny_reason"] = code
            self.samples.append(sample)
        route.abort("blockedbyclient")

    def deny_socket(self, socket):
        socket.close()
        self.denials.add("WEBSOCKET_UNSUPPORTED")

    def report(self) -> dict:
        return {"blocked_request_types": sorted(self.denials),
                "blocked_request_samples": list(self.samples),
                "blocked_sample_limit": 12,
                "redirect_diagnostics": list(self.redirects),
                "redirect_diagnostic_limit": REDIRECT_DIAGNOSTIC_LIMIT,
                "redirect_count": self.redirect_count,
                "challenge_destination_denied": self.challenge_destination_denied}


def follow_one_read_only_apply_entry(page, posting_url: str, *, timeout_ms: int,
                                     session_fence: InspectionNetworkFence) -> dict:
    """One CTA navigation with no field writes or submission."""
    try:
        entry = select_apply_entry(page.evaluate(_APPLY_CTA_SCAN), posting_url)
    except Exception as exc:
        return {"status": "CTA_SCAN_FAILED", "error_type": type(exc).__name__, "attempt_count": 0}
    if entry["status"] != "APPROVED_CTA":
        return {**entry, "attempt_count": 0}

    try:
        if entry["kind"] == "link":
            page.goto(entry["href"], wait_until="domcontentloaded", timeout=timeout_ms)
        else:
            locator = page.get_by_role("button", name=entry["label"], exact=True)
            if locator.count() != 1:
                return {"status": "CTA_LOCATOR_CHANGED", "attempt_count": 0}
            # Only an exact, unique, visible non-submit application-entry button.
            locator.click(timeout=min(timeout_ms, 5000), no_wait_after=True)
        page.wait_for_timeout(1200)
        if _BLOCKING_DENIALS & session_fence.denials:
            return {"status": "NETWORK_FENCE_BLOCKED", "attempt_count": 1, **session_fence.report()}
        if not _is_trusted_navigation_url(page.url):
            return {"status": "UNTRUSTED_FINAL_URL", "attempt_count": 1}
        return {"status": "FOLLOWED", "kind": entry["kind"],
                "candidate_count": entry.get("candidate_count", 1),
                "distinct_destination_count": entry.get("distinct_destination_count", 1),
                "url_changed": page.url != posting_url, "attempt_count": 1}
    except Exception as exc:
        return {"status": "ENTRY_NAVIGATION_FAILED", "attempt_count": 1, "error_type": type(exc).__name__}


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
    // Count controls in open shadow roots too; these are read-only render signals.
    form_count: all.filter(el => el.matches('form')).length,
    native_control_count: all.filter(el => el.matches('input:not([type="hidden"]), textarea, select')).length,
    action_candidate_count: all.filter(el => el.matches('button, input[type="submit"], input[type="button"], a[role="button"]')).length,
    iframe_count: document.querySelectorAll('iframe').length,
    script_count: document.querySelectorAll('script').length,
    custom_element_count: Array.from(customTags).reduce((n, tag) => n + (tagCounts.get(tag) || 0), 0),
    custom_element_tags: Array.from(customTags).sort().slice(0, 40),
    open_shadow_root_count: openShadowRootCount,
    custom_control_hint_count: customControlHintCount
  };
}
"""


CAPTCHA_FRAME_HOST_SUFFIXES = ("captcha-delivery.com",)


@dataclass(frozen=True)
class LiveInspectionRequest:
    application_url: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 8_000
    auto_open_apply: bool = False


def validate_live_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("LIVE_INSPECTION_REQUIRES_HTTPS")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
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


def is_known_captcha_origin(origin: str) -> bool:
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in CAPTCHA_FRAME_HOST_SUFFIXES)


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


def summarize_step_boundary(form: dict) -> dict:
    """Describe only the currently observed step; never advance a form."""
    controls = form.get("controls", [])
    actions = form.get("actions", [])
    visible_controls = sum(
        c.get("visible") is True and c.get("disabled") is not True
        for c in controls if isinstance(c, dict)
    )
    next_labels = {"next", "continue", "proceed"}
    next_actions = [
        a for a in actions if isinstance(a, dict)
        and a.get("visible") is True and a.get("disabled") is not True
        and " ".join(str(a.get("label", "")).casefold().split()) in next_labels
    ]
    return {
        "current_step_only": True,
        "visible_enabled_control_count": visible_controls,
        "next_action_count": len(next_actions),
        "additional_steps_possible": bool(next_actions),
        "later_steps_inspected": False,
        "step_navigation_attempts": 0,
        "multi_step_complete": False,
        "requires_separate_review_for_step_navigation": bool(next_actions),
    }


def discovery_state(form: dict) -> tuple[str, str]:
    # Job-status/cookie/share UI must not masquerade as an application form.
    actions = form.get("actions", [])
    expired_labels = {"sorry, this job has expired", "this job has expired"}
    if any(isinstance(a, dict) and a.get("visible") is True
           and " ".join(str(a.get("label", "")).casefold().split()) in expired_labels
           for a in actions):
        return "job_expired", "JOB_EXPIRED"
    controls = [c for c in form.get("controls", [])
                if isinstance(c, dict) and c.get("visible") is True]
    if any(str(c.get("type", "")).casefold() == "password" for c in controls):
        return "auth_boundary", "AUTH_BOUNDARY"
    if controls:
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
        # Shell actions (cookie/share/apply buttons), opaque iframes, and
        # custom-element hints are not evidence of an application form.
        # Keep observing within the existing budget until real native
        # controls or a form appear; downstream checks still validate
        # visibility, origin, auth boundaries, and actual field semantics.
        form_structure = (
            last.get("form_count", 0) > 0
            or last.get("native_control_count", 0) > 0
        )
        if form_structure and elapsed >= min(500, budget_ms):
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
    except ImportError:
        return {"runtime_state": "unsupported", "error_code": "PLAYWRIGHT_UNAVAILABLE", "inspection_only": True}
    cfg = config or BrowserRuntimeConfig(navigation_timeout_ms=request.timeout_ms, settle_timeout_ms=2_000)
    with sync_playwright() as p:
        executable = installed_chrome(cfg, p.chromium)
        if not executable:
            return {"runtime_state": "unsupported", "error_code": "CHROMIUM_NOT_FOUND", "inspection_only": True}
        try:
            browser = p.chromium.launch(headless=cfg.headless, executable_path=executable,
                                        args=["--disable-dev-shm-usage"])
        except Exception:
            return {"runtime_state": "unsupported", "error_code": "BROWSER_LAUNCH_FAILED",
                    "inspection_only": True}
        context = None
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block")
            if not callable(getattr(context, "route_web_socket", None)):
                return {"runtime_state": "unsupported", "error_code": "WEBSOCKET_FENCE_UNAVAILABLE",
                        "inspection_only": True}
            fence = InspectionNetworkFence(request.application_url, fetch_timeout_ms=request.timeout_ms)
            context.route("**/*", fence.route)
            context.route_web_socket("**/*", fence.deny_socket)
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            try:
                page.goto(request.application_url, wait_until="domcontentloaded", timeout=request.timeout_ms)
            except Exception:
                code = ("KNOWN_CHALLENGE_DESTINATION_DENIED" if fence.challenge_destination_denied
                        else "NETWORK_FENCE_BLOCKED" if "REDIRECT_UNREVIEWED" in fence.denials
                        else "POSTING_NAVIGATION_FAILED")
                return {"runtime_state": "navigation_failed", "error_code": code,
                        "network_fence": fence.report(), "http_status": fence.http_status,
                        "retry_after": fence.retry_after, "inspection_only": True}

            try:
                page.wait_for_load_state("networkidle", timeout=min(5_000, request.render_wait_ms))
                network_idle_observed = True
            except Exception:
                network_idle_observed = False

            main_diag, render_wait = _wait_for_render_structure(page, budget_ms=request.render_wait_ms)
            apply_navigation = {"status": "NOT_REQUESTED", "attempt_count": 0}
            if request.auto_open_apply:
                # Do not interact at all on an expired, auth, CAPTCHA or already
                # visible form surface. The sole permitted action is an entry CTA.
                pre_form = inspect_shadow_form(page)
                pre_state, _ = discovery_state(pre_form)
                pre_body = (page.locator("body").inner_text(timeout=request.timeout_ms) or "").casefold()
                challenged = any(x in pre_body for x in _CHALLENGE_MARKERS)
                expired = any(x in pre_body for x in ("sorry, this job has expired", "this job has expired"))
                if (pre_state == "form_structure_not_discovered" and not challenged and not expired
                        and not (_BLOCKING_DENIALS & fence.denials) and fence.http_status is None):
                    apply_navigation = follow_one_read_only_apply_entry(
                        page, request.application_url, timeout_ms=request.timeout_ms, session_fence=fence
                    )
                    if apply_navigation["status"] == "FOLLOWED" and fence.http_status is None:
                        main_diag, render_wait = _wait_for_render_structure(page, budget_ms=request.render_wait_ms)
                else:
                    apply_navigation = {"status": "ALREADY_FORM_OR_BOUNDARY", "attempt_count": 0}
            final_url = page.url
            reports = [scope_shadow_report(inspect_shadow_form(page), "document")]
            frame_diagnostics = []
            same_origin_frames = 0
            cross_origin_frames = 0
            challenge_frame_origins: list[str] = []
            same_origin_frame_observation_failed = False

            for index, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                frame_url = frame.url or "about:blank"
                same_origin = same_origin_url(final_url, frame_url)
                origin = _origin(frame_url)
                meta = {
                    "frame_index": index,
                    "same_origin": same_origin,
                    "origin": origin,
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
                        same_origin_frame_observation_failed = True
                        meta["error_type"] = type(exc).__name__
                else:
                    cross_origin_frames += 1
                    meta["inspection_state"] = "cross_origin_not_inspected"
                    if is_known_captcha_origin(origin):
                        meta["challenge_hint"] = "captcha_delivery"
                        challenge_frame_origins.append(origin)
                frame_diagnostics.append(meta)

            form = combine_shadow_reports(reports)
            body = (page.locator("body").inner_text(timeout=request.timeout_ms) or "").lower()
            body_captcha = any(x in body for x in _CHALLENGE_MARKERS)
            captcha = body_captcha or bool(challenge_frame_origins)
            state, error_code = discovery_state(form)
            if captcha:
                state, error_code = "captcha_boundary", "CAPTCHA_BOUNDARY"
            elif same_origin_frame_observation_failed:
                # A partial same-origin frame read cannot certify complete form discovery.
                state, error_code = "form_structure_not_discovered", "FRAME_OBSERVATION_INCOMPLETE"
            elif fence.challenge_destination_denied:
                state, error_code = "challenge_destination_denied", "KNOWN_CHALLENGE_DESTINATION_DENIED"
            elif _BLOCKING_DENIALS & fence.denials or fence.http_status is not None:
                state, error_code = "network_boundary", "NETWORK_FENCE_BLOCKED"
            elif apply_navigation["status"] == "ENTRY_NAVIGATION_FAILED":
                state, error_code = "navigation_failed", "ENTRY_NAVIGATION_FAILED"
            # A blocked/failed navigation must not promote partially rendered
            # controls to a manifest-review candidate.
            if not captcha and apply_navigation["status"] in {
                "NETWORK_FENCE_BLOCKED", "UNTRUSTED_FINAL_URL", "ENTRY_NAVIGATION_FAILED",
                "CTA_SCAN_FAILED", "CTA_LOCATOR_CHANGED",
            } and state == "inspected":
                state, error_code = "form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED"

            captcha_evidence = []
            if body_captcha:
                captcha_evidence.append("body_text")
            if challenge_frame_origins:
                captcha_evidence.append("known_challenge_frame_origin")

            diagnostics = {
                "network_idle_observed": network_idle_observed,
                "render_wait": render_wait,
                "main_document": main_diag,
                "frame_count": max(0, len(page.frames) - 1),
                "same_origin_frame_count": same_origin_frames,
                "cross_origin_frame_count": cross_origin_frames,
                "challenge_frame_origins": sorted(set(challenge_frame_origins)),
                "captcha_evidence": captcha_evidence,
                "apply_navigation": apply_navigation,
                "frames": frame_diagnostics,
            }
            # Explicit human handoff only: no automatic challenge solving or resume.
            human_handoff = {
                "required": state == "captcha_boundary",
                "reason": "CAPTCHA_BOUNDARY" if state == "captcha_boundary" else "",
                "manual_verification_only": state == "captcha_boundary",
                "automatic_resume_allowed": False,
                "resume_requires_new_read_only_inspection": state == "captcha_boundary",
            }
            return {
                "network_fence": fence.report(),
                "http_status": fence.http_status,
                "retry_after": fence.retry_after,
                "human_handoff": human_handoff,
                "step_boundary": summarize_step_boundary(form),
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
    parser.add_argument("--auto-open-apply", action="store_true",
                        help="One bounded SmartRecruiters application-entry navigation, never form input")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = inspect_live_page(LiveInspectionRequest(
        args.application_url, args.timeout_ms, args.render_wait_ms, args.auto_open_apply
    ))
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("runtime_state") in {"inspected", "captcha_boundary", "form_structure_not_discovered", "job_expired", "auth_boundary"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LIVE_INSPECTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
