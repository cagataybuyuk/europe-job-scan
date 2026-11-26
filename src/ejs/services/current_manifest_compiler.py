"""Offline composition of the current-approval, compiler and fact gates.

The caller supplies authenticated CURRENT approval state independently of the
manifest writer. This module provides byte and private local file entry points.
"""
from datetime import datetime
from pathlib import Path

from ejs.services.current_fact_approval import (
    MAX_APPROVAL_BYTES, MAX_REGISTRY_BYTES, MAX_SNAPSHOT_BYTES, resolve_current_fact_inputs,
)
from ejs.services.execution_manifest import MAX_MANIFEST_BYTES, load_execution_manifest
from ejs.services.private_source_files import _read_private_sources
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


def compile_current_manifest_from_files(
    approval_path: str | Path, template_path: str | Path,
    registry_path: str | Path, snapshot_path: str | Path, *,
    expected_approval_sha256: str, expected_generation: int,
    expected_profile_version: str, now: datetime,
) -> bytes:
    """Compile private local sources using the independently authenticated current context.

    Paths must be absolute and symlink-free, and each source must live in an
    owner-only directory. The current approval pin is supplied by the caller.
    The verified approval record provides the other three exact-byte pins.
    Current-state retrieval, publication and point-of-use checks belong to the
    authorized caller.
    """
    try:
        return compile_current_manifest(
            *_read_private_sources((
                (approval_path, MAX_APPROVAL_BYTES), (template_path, MAX_MANIFEST_BYTES),
                (registry_path, MAX_REGISTRY_BYTES), (snapshot_path, MAX_SNAPSHOT_BYTES),
            )),
            expected_approval_sha256=expected_approval_sha256,
            expected_generation=expected_generation,
            expected_profile_version=expected_profile_version, now=now,
        )
    except (OSError, ValueError, TypeError, AttributeError, NotImplementedError,
            OverflowError, RecursionError):
        pass
    # Keep private paths, pins, payloads and subordinate exceptions out of the
    # public traceback. The caller is responsible for its own diagnostic frames.
    del approval_path, template_path, registry_path, snapshot_path
    del expected_approval_sha256, expected_generation, expected_profile_version, now
    raise PermissionError(ERROR) from None
