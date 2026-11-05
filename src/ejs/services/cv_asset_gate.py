"""MVP-06: offline, fail-closed CV asset metadata gate; never opens or uploads files."""
from __future__ import annotations

import hmac
import re
from collections.abc import Mapping

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 10 * 1024 * 1024


def evaluate_cv_asset(asset: object, approval: object) -> dict:
    """Compare observed private asset metadata against separately approved metadata.

    This does not establish that the file was read, nor grant ATS upload authority.
    """
    reasons: list[str] = []
    if not isinstance(asset, Mapping) or not isinstance(approval, Mapping):
        reasons.append("MISSING_ASSET_OR_APPROVAL")
    else:
        sha = asset.get("sha256")
        expected_sha = approval.get("sha256")
        if not all(isinstance(x, str) and _SHA256.fullmatch(x) for x in (sha, expected_sha)):
            reasons.append("INVALID_SHA256")
        elif not hmac.compare_digest(sha, expected_sha):
            reasons.append("ASSET_CHANGED")
        size = asset.get("size_bytes")
        approved_size = approval.get("size_bytes")
        if type(size) is not int or not 0 < size <= _MAX_BYTES or size != approved_size:
            reasons.append("INVALID_OR_CHANGED_SIZE")
        if asset.get("mime_type") != "application/pdf" or approval.get("mime_type") != "application/pdf":
            reasons.append("UNSUPPORTED_TYPE")
        if asset.get("approved_filename") != approval.get("approved_filename") or not (
            isinstance(asset.get("approved_filename"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\\.pdf", asset["approved_filename"], re.IGNORECASE)
        ):
            reasons.append("INVALID_OR_CHANGED_FILENAME")
        if approval.get("user_approved") is not True or approval.get("scope") != "one_cv_asset":
            reasons.append("APPROVAL_REQUIRED")
    return {
        "status": "BLOCKED" if reasons else "METADATA_MATCH",
        "reasons": reasons,
        "file_read_performed": False,
        "upload_authorized": False,
        "submit_authorized": False,
    }
