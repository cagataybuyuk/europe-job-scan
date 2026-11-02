"""GD-005 TEST manifest *schema* validation; never execution approval.

This module parses untrusted, synthetic metadata without I/O, dispatch, or
side effects. Even a structurally valid manifest is NOT a trusted approval:
a caller-provided SHA and approval_state cannot establish authority. Future
execution requires an independent, reviewed governance/runner boundary.
"""

from dataclasses import dataclass
import json
import re


_SCHEMA = "GD005-OFFLINE-EXEC-1"
_REVISION = "v1"
_TASK_IDS = frozenset({"pytest_core", "secret_scan", "authority_boundaries"})
_FIELDS = frozenset({
    "schema_version", "environment", "task_id", "source_sha",
    "allowlist_revision", "approval_state", "network", "workspace",
    "max_wall_seconds", "max_retries", "payload_class",
})
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_JSON_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ManifestValidation:
    """Structural feedback only; never a capability or permission token.

    Deliberately omit authorization fields from the constructor: even a future
    caller cannot instantiate a positive authorization through this type.
    """

    schema_valid: bool
    reason: str

    @property
    def execution_authorized(self) -> bool:
        """Always false; no execution-authorizing state is representable."""
        return False

    @property
    def permitted_actions(self) -> tuple[str, ...]:
        """Always empty; validation cannot grant permissions."""
        return ()


def _deny(reason: str) -> ManifestValidation:
    return ManifestValidation(False, reason)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("non-json constant")


def validate_manifest_json(raw_json: str, *, expected_source_sha: str) -> ManifestValidation:
    """Check a proposed TEST manifest; do not execute or authorize anything.

    expected_source_sha is supplied by the caller and only enables equality
    checking. This function does NOT authenticate that caller, repository,
    manifest publisher, branch protection or purported approval record.
    No raw input is included in error messages or results.
    """
    if type(expected_source_sha) is not str or not _SHA.fullmatch(expected_source_sha):
        return _deny("invalid_expected_source_sha")
    if type(raw_json) is not str:
        return _deny("invalid_manifest_encoding")
    try:
        size = len(raw_json.encode("utf-8"))
    except UnicodeError:
        return _deny("invalid_manifest_encoding")
    if not 0 < size <= _MAX_JSON_BYTES:
        return _deny("manifest_size_out_of_bounds")
    try:
        data = json.loads(
            raw_json,
            object_pairs_hook=_unique_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return _deny("malformed_manifest_json")
    if type(data) is not dict or set(data) != _FIELDS:
        return _deny("unexpected_manifest_fields")
    if any(type(data[key]) is not str for key in _FIELDS - {"max_wall_seconds", "max_retries"}):
        return _deny("invalid_manifest_field_type")
    if type(data["max_wall_seconds"]) is not int or type(data["max_retries"]) is not int:
        return _deny("invalid_manifest_field_type")
    if data["schema_version"] != _SCHEMA or data["allowlist_revision"] != _REVISION:
        return _deny("unsupported_manifest_version")
    if data["environment"] != "TEST" or data["task_id"] not in _TASK_IDS:
        return _deny("unsupported_test_task")
    if not _SHA.fullmatch(data["source_sha"]) or data["source_sha"] != expected_source_sha:
        return _deny("source_sha_mismatch")
    # A caller may only submit a review-required *proposal*. It cannot mark
    # itself approved, even if its source SHA or task ID is otherwise valid.
    if data["approval_state"] != "review_required":
        return _deny("untrusted_approval_state")
    if data["network"] != "denied" or data["workspace"] != "isolated":
        return _deny("unsupported_execution_boundary")
    if not 1 <= data["max_wall_seconds"] <= 300 or data["max_retries"] != 0:
        return _deny("unsupported_execution_limits")
    if data["payload_class"] != "synthetic_only":
        return _deny("unsupported_payload_class")

    # Valid schema is *not* permission to spawn a task or dispatch a provider.
    return ManifestValidation(True, "schema_valid_not_authorized")
