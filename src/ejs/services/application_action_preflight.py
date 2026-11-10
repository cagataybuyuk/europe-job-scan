"""Pure preflight for an exact-target employer mutation request.

This function never executes an ATS action and never grants network authority.
A successful preflight is only one prerequisite for separately reviewed execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlsplit
import unicodedata

from .bound_work_rights_gate import evaluate_bound_work_rights

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
    if (host.endswith(".") or "%" in host or host == "localhost"
            or host.endswith((".localhost", ".local"))):
        return denied
    try:
        address = ip_address(host)
    except ValueError:
        pass  # Domain names require independent DNS/redirect checks at execution.
    else:
        if not address.is_global:
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
    if not isinstance(expiry, datetime) or expiry.tzinfo is None:
        return denied
    if not isinstance(now, datetime) or now.tzinfo is None:
        return denied
    if expiry <= now:
        return denied
    # Still not an executable capability: the ATS writer must enforce its own
    # kill switch, endpoint allowlist, asset hashes, readback and idempotency.
    return {**denied, "status": "PREFLIGHT_READY", "reason": None}
