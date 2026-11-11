"""Pure preflight for an exact-target employer mutation request.

This function never executes an ATS action and never grants network authority.
A successful preflight is only one prerequisite for separately reviewed execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from ipaddress import ip_address
import re
from urllib.parse import urlsplit
import unicodedata

from .bound_work_rights_gate import _aware_datetime, _future_expiry, evaluate_bound_work_rights

_ACTIONS = frozenset({"write", "upload", "submit"})


def evaluate_application_action_preflight(
    evidence: object, target: object, permission: object,
    action: object, now: datetime,
) -> dict:
    denied = {"status": "BLOCKED", "reason": "ACTION_NOT_AUTHORIZED",
              "write_authorized": False, "upload_authorized": False,
              "submit_authorized": False, "network_authorized": False}
    if not isinstance(action, str) or action not in _ACTIONS:
        return denied
    if not isinstance(target, Mapping) or not isinstance(permission, Mapping):
        return denied
    # Consent cannot make a non-HTTPS, credential-bearing or ambiguous URL safe.
    url = target.get("vacancy_url")
    if (not isinstance(url, str) or any(
            ch.isspace() or ch == "\\" or unicodedata.category(ch).startswith("C")
            for ch in url)):
        return denied
    # Encoded ASCII controls can be decoded by a downstream ATS client or proxy.
    # Reject them before an exact-target consent can reach the transport layer.
    if re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|5c|7f)", url, re.IGNORECASE):
        return denied
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)):
            return denied
    except ValueError:
        return denied
    # Do not accept loopback, link-local, private or otherwise non-public
    # destinations as employer ATS endpoints, even with matching consent.
    host = parsed.hostname
    # IDNA/Unicode hostnames can be remapped by downstream DNS or HTTP clients.
    # Keep mutation preflight ASCII-only until the transport pins a reviewed host.
    if not host.isascii():
        return denied
    if (host.endswith(".") or "%" in host or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".home.arpa", ".onion", ".invalid", ".localdomain"))):
        return denied
    # Reject malformed DNS labels before transport-specific host normalization.
    if (len(host) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.IGNORECASE)
            for label in host.split("."))):
        return denied
    # Single-label hosts and numeric pseudo-TLDs may resolve via local search
    # domains or transport-specific name parsing; never treat them as ATS FQDNs.
    labels = host.split(".")
    if len(labels) < 2 or not re.fullmatch(r"[a-z]{2,63}", labels[-1], re.IGNORECASE):
        return denied
    # Numeric/legacy IPv4 spellings may resolve to private addresses even when
    # ip_address() rejects their syntax (e.g. 2130706433 or 0177.0.0.1).
    # Never allow ambiguous numeric hostnames through to a later ATS transport.
    labels = host.split(".")
    if all(label.isascii() and (label.isdecimal() or label.lower().startswith("0x"))
           for label in labels):
        return denied
    try:
        address = ip_address(host)
    except ValueError:
        pass  # Domain names require independent DNS/redirect checks at execution.
    else:
        if not address.is_global:
            return denied
    if not _aware_datetime(now):
        return denied
    eligibility = evaluate_bound_work_rights(evidence, target, now)
    if eligibility["status"] != "REVIEW_READY":
        return denied
    # Exact-target, exact-action, time-bounded, independently approved consent.
    keys = ("candidate_id", "employer_id", "vacancy_url")
    if any(not isinstance(permission.get(key), str)
           or permission[key] != target.get(key) for key in keys):
        return denied
    if permission.get("action") != action or permission.get("approved") is not True:
        return denied
    if permission.get("reviewed") is not True or permission.get("revoked") is not False:
        return denied
    expiry = permission.get("expires_at")
    if not _future_expiry(expiry, now):
        return denied
    # Still not an executable capability: the ATS writer must enforce its own
    # kill switch, endpoint allowlist, asset hashes, readback and idempotency.
    return {**denied, "status": "PREFLIGHT_READY", "reason": None}
