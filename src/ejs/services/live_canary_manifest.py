"""Private, independently pinned live preparation inputs. No browser authority.

Digest approval must come from the reviewer/environment, never from these files.
Fact values and source references are private; errors intentionally contain no data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ejs.contracts.prefill import (
    PrefillFieldPlan, FieldOwnership, ResolverStatus, MappingConfidence,
)
from ejs.services.prefill_writer import validate_prefill_plan


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError("LIVE_MANIFEST_REVIEW_REQUIRED")


def _pinned_json(path: Path, digest: str) -> dict:
    _require(isinstance(digest, str) and bool(re.fullmatch(r"[a-f0-9]{64}", digest)))
    try:
        # Bound input before parsing; never include path, bytes or values in errors.
        _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000)
        with path.open("rb") as fh:
            raw = fh.read(1_000_001)
        _require(len(raw) <= 1_000_000 and hashlib.sha256(raw).hexdigest() == digest)
        def unique_pairs(pairs):
            out = {}
            for key, value in pairs:
                _require(key not in out)
                out[key] = value
            return out
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        _require(isinstance(value, dict))
        return value
    except Exception:
        raise PermissionError("LIVE_MANIFEST_REVIEW_REQUIRED") from None


def _fresh(value: str, now: datetime, max_age_seconds: int) -> None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= max_age_seconds)
    except Exception:
        raise PermissionError("LIVE_MANIFEST_REVIEW_REQUIRED") from None


def validate_reviewed_manifest(data: dict, facts: dict, *, source_sha: str,
                               target_url: str, now: datetime | None = None) -> None:
    """Resolve every proposed value against pinned verified facts, fail closed.

    This is permission to construct a prepare-only request, never evidence that
    a vacancy is suitable or that an employer upload/submit endpoint is approved.
    """
    now = now or datetime.now(timezone.utc)
    try:
        _require(data.get("schema_version") == "EJS-LIVE-PREPARE-1")
        _require(facts.get("schema_version") == "EJS-VERIFIED-FACTS-1")
        _require(bool(re.fullmatch(r"[a-f0-9]{40}", source_sha)))
        _require(data.get("source_sha") == source_sha)
        parsed = urlsplit(target_url)
        _require(parsed.scheme == "https" and parsed.hostname == "jobs.smartrecruiters.com"
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password
                 and not parsed.fragment)
        _require(data.get("target_url") == target_url)
        _require(bool(re.fullmatch(r"[a-f0-9]{24}", data.get("expected_form_fingerprint", ""))))
        _require(isinstance(data.get("candidate_profile_version"), str)
                 and bool(data["candidate_profile_version"].strip())
                 and data["candidate_profile_version"] == facts.get("candidate_profile_version"))
        _fresh(data.get("reviewed_at", ""), now, 86_400)
        _require(isinstance(data.get("reviewer_ref"), str) and bool(data["reviewer_ref"].strip()))
        for key in ("opportunity_key", "requisition_id", "observed_at"):
            _require(isinstance(data.get(key), str) and bool(data[key].strip()))
        _fresh(data["observed_at"], now, 86_400)
        fields, uploads, resolutions = (data.get(k) for k in ("field_plan", "upload_plan", "control_resolutions"))
        _require(all(isinstance(x, list) and 0 < len(x) <= 100 for x in (fields, uploads, resolutions)))
        fact_list = facts.get("facts")
        _require(isinstance(fact_list, list) and 0 < len(fact_list) <= 100)
        by_field = {}
        for fact in fact_list:
            _require(isinstance(fact, dict))
            key = fact.get("canonical_field")
            _require(isinstance(key, str) and key not in by_field)
            by_field[key] = fact
        plans = []
        controls = set()
        fact_by_control = {}
        for item in fields:
            _require(isinstance(item, dict))
            _require(type(item.get("review_required")) is bool and item["review_required"] is False)
            _require(type(item.get("required")) is bool)
            _require(isinstance(item.get("value"), str) and isinstance(item.get("control_key"), str))
            _require(item["control_key"] not in controls)
            controls.add(item["control_key"])
            fact = by_field.get(item.get("canonical_field"))
            _require(isinstance(fact, dict) and fact.get("verified") is True
                     and fact.get("auto_fill_approved") is True)
            _require(isinstance(fact.get("source_ref"), str) and bool(fact["source_ref"].strip()))
            _fresh(fact.get("verified_at", ""), now, 30 * 86_400)
            _require(fact.get("value") == item["value"] and item.get("provenance_ref") == fact["source_ref"])
            plan = PrefillFieldPlan(
                item["field_plan_key"], item["control_key"], item["canonical_field"], item["control_type"],
                item["value"], item["provenance_ref"], FieldOwnership(item["ownership"]),
                ResolverStatus(item["resolver_status"]), MappingConfidence(item["mapping_confidence"]),
                item["review_required"], item["required"],
            )
            plans.append(plan)
            fact_by_control[item["control_key"]] = item
        _require(not validate_prefill_plan(tuple(plans)))
        upload_by_control = {}
        for item in uploads:
            _require(isinstance(item, dict) and item.get("approved") is True
                     and type(item.get("expected_size_bytes")) is int
                     and type(item.get("required")) is bool
                     and item.get("mapping_confidence") == "High")
            _require(isinstance(item.get("control_key"), str) and item["control_key"] not in controls)
            _require(item.get("canonical_field") == "document.cv" and item.get("artifact_type") == "cv"
                     and item.get("expected_mime_type") == "application/pdf"
                     and isinstance(item.get("asset_ref"), str) and bool(item["asset_ref"].strip()))
            controls.add(item["control_key"])
            upload_by_control[item["control_key"]] = item
        _require(len(uploads) == 1)
        resolved = set()
        for item in resolutions:
            _require(isinstance(item, dict) and item.get("mapping_confidence") == "High")
            _require(type(item.get("required")) is bool)
            for key in ("grounded", "explicit_user_fact", "option_available"):
                if key in item:
                    _require(type(item[key]) is bool)
            key = item.get("control_key")
            _require(key in controls and key not in resolved)
            resolved.add(key)
            if key in fact_by_control:
                plan = fact_by_control[key]
                _require(item.get("canonical_field") == plan["canonical_field"]
                         and item.get("provenance_ref") == plan["provenance_ref"]
                         and item["required"] == plan["required"])
                if plan["ownership"] == "DYNAMIC_AI":
                    _require(item.get("resolution_mode") == "Grounded Generated" and item.get("grounded") is True)
                else:
                    _require(item.get("resolution_mode") == "Verified Fact")
            else:
                plan = upload_by_control[key]
                _require(item.get("canonical_field") == plan["canonical_field"]
                         and item["required"] == plan["required"]
                         and item.get("resolution_mode") == "Approved Artifact"
                         and item.get("artifact_ref") == plan["asset_ref"])
        _require(resolved == controls)
    except Exception:
        raise PermissionError("LIVE_MANIFEST_REVIEW_REQUIRED") from None


def load_reviewed_manifest(path: Path, *, facts_path: Path, manifest_sha256: str,
                           facts_sha256: str, source_sha: str, target_url: str) -> dict:
    data = _pinned_json(path, manifest_sha256)
    facts = _pinned_json(facts_path, facts_sha256)
    validate_reviewed_manifest(data, facts, source_sha=source_sha, target_url=target_url)
    return data
