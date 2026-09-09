from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
import re
from typing import Any, Mapping

EJS_GH_EXEC_VERSION = "EJS-GH-EXEC-0.1"
MAX_REQUEST_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 30
ALLOWED_OPERATIONS = frozenset(
    {"health", "claim", "get_execution_payload", "get_approved_asset", "reconcile_result"}
)
_SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ExecutionProvider(str, Enum):
    GITHUB_HOSTED = "github_hosted"
    SELF_HOSTED = "self_hosted"
    CLOUD_RUN_FALLBACK = "cloud_run_fallback"


@dataclass(frozen=True)
class CapabilityFlags:
    browser_read: bool = True
    form_value_write: bool = False
    approved_file_upload: bool = False
    final_submit: bool = False

    def validate_gd004(self) -> None:
        if not self.browser_read:
            raise PermissionError("GD-004 requires browser_read=true")
        forbidden = {
            "form_value_write": self.form_value_write,
            "approved_file_upload": self.approved_file_upload,
            "final_submit": self.final_submit,
        }
        enabled = [name for name, value in forbidden.items() if value]
        if enabled:
            raise PermissionError(f"GD-004 mutating capabilities are forbidden: {', '.join(enabled)}")

    def to_dict(self) -> dict[str, bool]:
        return {
            "browser_read": self.browser_read,
            "form_value_write": self.form_value_write,
            "approved_file_upload": self.approved_file_upload,
            "final_submit": self.final_submit,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilityFlags":
        return cls(
            browser_read=bool(value.get("browser_read")),
            form_value_write=bool(value.get("form_value_write")),
            approved_file_upload=bool(value.get("approved_file_upload")),
            final_submit=bool(value.get("final_submit")),
        )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hmac_sha256_hex(value: str | bytes, secret: str) -> str:
    if not secret:
        raise ValueError("shared secret must not be empty")
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SignedExecutionRequest:
    contract_version: str
    request_id: str
    execution_id: str
    operation: str
    created_at: str
    expires_at: str
    nonce: str
    source_sha: str
    environment: str
    capability_flags: CapabilityFlags
    payload: Mapping[str, Any]
    payload_hash: str
    signature: str

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "execution_id": self.execution_id,
            "operation": self.operation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "source_sha": self.source_sha,
            "environment": self.environment,
            "capability_flags": self.capability_flags.to_dict(),
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        value = self.unsigned_dict()
        value["signature"] = self.signature
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SignedExecutionRequest":
        return cls(
            contract_version=str(value.get("contract_version", "")),
            request_id=str(value.get("request_id", "")),
            execution_id=str(value.get("execution_id", "")),
            operation=str(value.get("operation", "")),
            created_at=str(value.get("created_at", "")),
            expires_at=str(value.get("expires_at", "")),
            nonce=str(value.get("nonce", "")),
            source_sha=str(value.get("source_sha", "")),
            environment=str(value.get("environment", "")),
            capability_flags=CapabilityFlags.from_mapping(value.get("capability_flags") or {}),
            payload=value.get("payload") or {},
            payload_hash=str(value.get("payload_hash", "")),
            signature=str(value.get("signature", "")),
        )


def sign_execution_request(
    *,
    secret: str,
    request_id: str,
    execution_id: str,
    operation: str,
    source_sha: str,
    nonce: str,
    payload: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
    ttl_seconds: int = 180,
    capability_flags: CapabilityFlags | None = None,
) -> SignedExecutionRequest:
    if ttl_seconds < 1 or ttl_seconds > MAX_REQUEST_TTL_SECONDS:
        raise ValueError("ttl_seconds outside GD-004 bound")
    created = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = created + timedelta(seconds=ttl_seconds)
    flags = capability_flags or CapabilityFlags()
    flags.validate_gd004()
    payload_value = dict(payload or {})
    payload_hash = sha256_hex(canonical_json(payload_value))
    unsigned = SignedExecutionRequest(
        contract_version=EJS_GH_EXEC_VERSION,
        request_id=request_id,
        execution_id=execution_id,
        operation=operation,
        created_at=_format_time(created),
        expires_at=_format_time(expires),
        nonce=nonce,
        source_sha=source_sha,
        environment="TEST",
        capability_flags=flags,
        payload=payload_value,
        payload_hash=payload_hash,
        signature="",
    )
    signature = hmac_sha256_hex(canonical_json(unsigned.unsigned_dict()), secret)
    return SignedExecutionRequest(**{**unsigned.__dict__, "signature": signature})


def verify_execution_request(
    value: Mapping[str, Any], *, secret: str, now: datetime | None = None
) -> SignedExecutionRequest:
    request = SignedExecutionRequest.from_mapping(value)
    if request.contract_version != EJS_GH_EXEC_VERSION:
        raise ValueError("unsupported contract_version")
    if not request.request_id or not request.execution_id or not request.nonce:
        raise ValueError("request_id, execution_id and nonce are required")
    if request.operation not in ALLOWED_OPERATIONS:
        raise PermissionError("operation not allowlisted")
    if request.environment != "TEST":
        raise PermissionError("GD-004 request must target TEST")
    if not _SOURCE_SHA_RE.fullmatch(request.source_sha):
        raise ValueError("source_sha must be an immutable 40-char lowercase git SHA")
    request.capability_flags.validate_gd004()
    if not isinstance(request.payload, Mapping):
        raise ValueError("payload must be an object")
    expected_payload_hash = sha256_hex(canonical_json(dict(request.payload)))
    if not hmac.compare_digest(request.payload_hash, expected_payload_hash):
        raise ValueError("payload_hash mismatch")

    created = _parse_time(request.created_at)
    expires = _parse_time(request.expires_at)
    ttl = (expires - created).total_seconds()
    if ttl < 1 or ttl > MAX_REQUEST_TTL_SECONDS:
        raise ValueError("request TTL outside GD-004 bound")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if created > current + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise PermissionError("request created_at is too far in the future")
    if current > expires:
        raise PermissionError("request expired")

    expected_signature = hmac_sha256_hex(canonical_json(request.unsigned_dict()), secret)
    if not hmac.compare_digest(request.signature, expected_signature):
        raise PermissionError("invalid HMAC signature")
    return request


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    execution_id: str
    operation: str
    source_sha: str
    provider: ExecutionProvider
    started_at: str
    completed_at: str
    typed_status: str
    form_fingerprint: str = ""
    runtime_fingerprint: str = ""
    mutation_count: int = 0
    upload_count: int = 0
    submit_count: int = 0
    evidence_summary: str = ""
    result_hash: str = ""

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "execution_id": self.execution_id,
            "operation": self.operation,
            "source_sha": self.source_sha,
            "provider": self.provider.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "typed_status": self.typed_status,
            "form_fingerprint": self.form_fingerprint,
            "runtime_fingerprint": self.runtime_fingerprint,
            "mutation_count": self.mutation_count,
            "upload_count": self.upload_count,
            "submit_count": self.submit_count,
            "evidence_summary": self.evidence_summary,
        }

    def with_hash(self) -> "ExecutionResult":
        digest = sha256_hex(canonical_json(self.unsigned_dict()))
        return ExecutionResult(**{**self.__dict__, "result_hash": digest})

    def validate_gd004(self) -> None:
        if not _SOURCE_SHA_RE.fullmatch(self.source_sha):
            raise ValueError("invalid result source_sha")
        if any((self.mutation_count, self.upload_count, self.submit_count)):
            raise PermissionError("GD-004 execution result reports a forbidden side effect")
        expected = sha256_hex(canonical_json(self.unsigned_dict()))
        if self.result_hash and not hmac.compare_digest(self.result_hash, expected):
            raise ValueError("result_hash mismatch")
