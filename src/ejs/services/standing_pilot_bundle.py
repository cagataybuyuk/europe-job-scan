"""Build a deterministic private review archive from one prepared standing pilot."""

from datetime import datetime
import hashlib
import io
import json
import stat
from zipfile import ZipFile, ZipInfo

from ejs.services.standing_admission import admit_standing_batch
from ejs.services.standing_envelope import MAX_STANDING_TARGETS
from ejs.services.standing_guard import _PACKAGE_FILES, package_name
from ejs.services.standing_pilot_preparation import PilotDraft

ERROR = "STANDING_PILOT_BUNDLE_REVIEW_REQUIRED"


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _review_entries(draft: PilotDraft, now: datetime) -> dict[str, bytes]:
    _require(isinstance(draft, PilotDraft) and 0 < len(draft.packages) <= MAX_STANDING_TARGETS)
    approval = draft.approval_draft
    entries = {
        "approval-draft/current.json": approval.pointer,
        "approval-draft/envelope.json": approval.envelope,
        "approval-draft/eligibility-index.json": approval.eligibility_index,
    }
    indexed = {}
    for ordinal, item in enumerate(draft.packages, 1):
        decision = admit_standing_batch(approval, item.inputs, item.evidence, now=now)
        _require(decision.execution_id == item.execution_id and item.execution_id not in indexed)
        indexed[item.execution_id] = hashlib.sha256(item.inputs.eligibility).hexdigest()
        entries[f"manifest-drafts/{ordinal:02d}.json"] = item.inputs.manifest
        folder = f"packages/{package_name(decision.execution_id)}"
        inputs = vars(item.inputs) | vars(item.evidence)
        for key, filename, _ in _PACKAGE_FILES:
            entries[f"{folder}/{filename}"] = inputs[key]
    _require(json.loads(approval.eligibility_index)["entries"] == indexed
             and json.loads(approval.envelope)["targets"] == [item.ranked_target.target for item in draft.packages])
    return entries


def build_pilot_review_bundle(draft: PilotDraft, *, now: datetime) -> bytes:
    """Return a ZIP archive with exact draft/package bytes and per-file SHA256 values.

    Only the reviewed manifest references the CV. The ZIP contains private package
    inputs and belongs in an owner-only location. Every path is review-only. This
    function has no filesystem, approval publication or queue access.
    """
    try:
        entries = _review_entries(draft, now)
        entries["SHA256SUMS"] = "".join(
            f"{hashlib.sha256(raw).hexdigest()}  {name}\n" for name, raw in sorted(entries.items())
        ).encode("ascii")
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            for name, raw in sorted(entries.items()):
                entry = ZipInfo(name)
                entry.create_system = 3  # Unix file modes
                entry.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(entry, raw)
        return buffer.getvalue()
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del draft, now
    entries = buffer = archive = raw = None
    raise PermissionError(ERROR) from None
