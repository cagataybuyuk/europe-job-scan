"""LinkedIn single-URL -> external ATS URL (ROADMAP Faz 1.3). Pure + injected carrier; NO bulk fetch.

Only one ``/jobs/view/`` URL is accepted. Search/list/collection pages are rejected before any fetch.
The page body is supplied by the injected ``fetch`` (no default; absent carrier -> needs_user).
Unresolvable -> ``needs_user`` (never guessed). No login, CAPTCHA or MFA handling.
Semantics of the href handling mirror ``linkedin_live_resolver.extract_external_apply_target`` (which
imports playwright and so is not imported here).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

_APPLY_PATH = "/jobs/view/externalApply/"
_ANCHOR = re.compile(r"""<a\b([^>]*?)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')([^>]*)>(.*?)</a\s*>""", re.I | re.S)
_JOB_ID = re.compile(r"(?:^|-)\d{5,}$")
_TAG = re.compile(r"<[^>]*>")
_APPLY_WORDS = re.compile(r"\b(apply|bewerb\w*|postuler|solicitar|candidatar\w*|candidat\w*)\b", re.I)


@dataclass(frozen=True)
class ResolveResult:
    status: str                 # resolved | needs_user | rejected
    reason: str
    target_url: str | None = None


def _li(host: str | None) -> bool:
    h = (host or "").lower().rstrip(".")
    return h == "linkedin.com" or h.endswith(".linkedin.com")


def is_single_job_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return (p.scheme == "https" and _li(p.hostname) and p.path.startswith("/jobs/view/")
                and not p.path.startswith(_APPLY_PATH)
                and len(segs := [s for s in p.path.split("/") if s]) == 3 and bool(_JOB_ID.search(segs[2])))
    except ValueError:
        return False


def _is_apply_wrapper(href: str) -> bool:
    """True only if the PARSED host is LinkedIn and the PARSED path is the externalApply path (not a query substring)."""
    try:
        p = urlparse(href)
        return p.scheme == "https" and _li(p.hostname) and p.path.startswith(_APPLY_PATH)
    except ValueError:
        return False


def extract_target(href: str) -> str | None:
    """Malformed URLs (e.g. invalid IPv6 brackets) yield None, never an exception."""
    try:
        p = urlparse(href)
        if p.scheme != "https" or not p.hostname:
            return None
        if not _li(p.hostname):
            return href
        if _APPLY_PATH not in p.path:
            return None
        vals = parse_qs(p.query).get("url") or []
        cand = unquote(vals[0]).strip() if vals else ""
        t = urlparse(cand)
        return cand if t.scheme == "https" and t.hostname and not _li(t.hostname) else None
    except ValueError:
        return None


def resolve_from_html(page: str) -> ResolveResult:
    targets = []
    for m in _ANCHOR.finditer(page or ""):
        href = html.unescape(m.group(2) if m.group(2) is not None else m.group(3))
        label = html.unescape(_TAG.sub(" ", m.group(5)))
        # An external https anchor counts only if it is the LinkedIn apply wrapper or is labelled as an apply action
        # (privacy/terms/social links are not apply targets).
        if not _is_apply_wrapper(href) and not _APPLY_WORDS.search(label):
            continue
        t = extract_target(href)
        if t and t not in targets:
            targets.append(t)
    if len(targets) == 1:
        return ResolveResult("resolved", "external_apply_found", targets[0])
    return ResolveResult("needs_user", "no_external_apply" if not targets else "ambiguous_apply_targets")


def resolve_linkedin_url(url: str, fetch: Callable[[str], "bytes | str"] | None) -> ResolveResult:
    if not is_single_job_url(url):
        return ResolveResult("rejected", "not_single_job_view_url")
    if fetch is None:
        return ResolveResult("needs_user", "no_carrier")
    try:
        body = fetch(url)
    except Exception as e:
        return ResolveResult("needs_user", f"fetch_failed:{type(e).__name__}")
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8")
        except UnicodeDecodeError:
            return ResolveResult("needs_user", "undecodable_page")
    if not isinstance(body, str) or not body.strip():
        return ResolveResult("needs_user", "empty_page")
    return resolve_from_html(body)
