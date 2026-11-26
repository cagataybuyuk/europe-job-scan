"""Offline composition of the current-approval, compiler and fact gates.

The caller supplies authenticated CURRENT approval state independently of the
manifest writer. This is not a publisher, file reader, runtime or authority.
"""
from datetime import datetime

from ejs.services.current_fact_approval import resolve_current_fact_inputs
from ejs.services.execution_manifest import load_execution_manifest
from ejs.services.reviewed_manifest_compiler import compile_reviewed_manifest

ERROR = "CURRENT_MANIFEST_REVIEW_REQUIRED"


def compile_current_manifest(
    approval_raw: bytes, template_raw: bytes, registry_raw: bytes,
    snapshot_raw: bytes, *,
    expected_approval_sha256: str, expected_generation: int,
    expected_profile_version: str, now: datetime,
) -> bytes:
    """Return compiled bytes only when all three offline gates agree.

    The independently supplied current profile must also equal the compiled
    profile, not merely the approval record's claimed profile. The result
    is not a reusable approval token. A future authorized consumer must
    retrieve current state and recheck at use; this function does no I/O.
    """
    try:
        pins = resolve_current_fact_inputs(
            approval_raw, template_raw, registry_raw, snapshot_raw,
            expected_approval_sha256=expected_approval_sha256,
            expected_generation=expected_generation,
            expected_profile_version=expected_profile_version, now=now,
        )
        compiled = compile_reviewed_manifest(
            template_raw, registry_raw, snapshot_raw,
            expected_template_sha256=pins.template_sha256,
            expected_registry_sha256=pins.registry_sha256,
            expected_snapshot_sha256=pins.snapshot_sha256, now=now,
        )
        manifest = load_execution_manifest(compiled, now=now)
        if manifest.candidate_profile_version != expected_profile_version:
            raise PermissionError(ERROR)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    else:
        return compiled
    # Avoid retaining private bytes, parsed manifest or pins in this frame.
    del approval_raw, template_raw, registry_raw, snapshot_raw
    del expected_approval_sha256, expected_generation, expected_profile_version, now
    pins = compiled = manifest = None
    raise PermissionError(ERROR) from None
