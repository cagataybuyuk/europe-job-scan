"""Offline manifest compilation from independently pinned review inputs.

No local source authentication, publication, queue or execution authority.
The trusted caller must obtain *current*, unrevoked pins from a review
channel independent of the proposed manifest, and read private bytes there.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math
import re

from ejs.services.execution_manifest import FACT_KEYS, MAX_MANIFEST_BYTES
from ejs.services.single_application_inputs import _validate_facts
from ejs.services.trusted_fact_snapshot import verify_fact_snapshot

ERROR = "MANIFEST_COMPILATION_REVIEW_REQUIRED"
_MAX_REGISTRY_BYTES = 1_000_000
_MAX_SNAPSHOT_BYTES = 500_000
_HEX = re.compile(r"[a-f0-9]{64}")


def _require(condition):
    if not condition:
        raise PermissionError(ERROR)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_):
    raise PermissionError(ERROR)


def _finite(value):
    result = float(value)
    _require(math.isfinite(result))
    return result


def _decode(raw, limit, pin=None, *, pinned=False):
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    if pinned:
        _require(type(pin) is str and _HEX.fullmatch(pin))
        _require(hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin))
    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                     parse_constant=_constant, parse_float=_finite)
    _require(type(doc) is dict)
    return doc


def compile_reviewed_manifest(template_raw: bytes, registry_raw: bytes,
                              snapshot_raw: bytes, *,
                              expected_registry_sha256: str,
                              expected_snapshot_sha256: str,
                              now: datetime) -> bytes:
    """Fill only explicit Verified Fact placeholders, using exact reviewed values.

    Registry uses EJS-VERIFIED-FACTS-1 and its existing 30-day freshness,
    explicit-source and auto-fill approval rules. Snapshot binds field mapping,
    exact options, target, observed time, policy and CV metadata. Both pins
    must be supplied independently; neither is computed from a proposal.

    Non-fact modes are unchanged, not independently approved by this function.
    No value conversion, option guessing, CV read, file write or authority.
    """
    try:
        _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
        template = _decode(template_raw, MAX_MANIFEST_BYTES)
        registry = _decode(registry_raw, _MAX_REGISTRY_BYTES,
                           expected_registry_sha256, pinned=True)
        snapshot = _decode(snapshot_raw, _MAX_SNAPSHOT_BYTES,
                           expected_snapshot_sha256, pinned=True)
        facts = _validate_facts(registry, now)
        _require(registry["candidate_profile_version"] ==
                 template["candidate_profile_version"] == snapshot["profile_version"])
        _require(type(snapshot["facts"]) is list)
        reviewed = {}
        for row in snapshot["facts"]:
            _require(type(row) is dict and type(row["key"]) is str
                     and row["key"] not in reviewed)
            reviewed[row["key"]] = row
        fields = template["form"]["fields"]
        _require(type(fields) is list)
        for field in fields:
            answer = field["answer"]
            if answer["mode"] != "Verified Fact":
                continue
            # Do not silently repair a writer-supplied candidate answer.
            _require(answer["value"] is None and answer["source_ref"] is None)
            canonical = field["canonical_field"]
            _require(canonical in FACT_KEYS and canonical in facts)
            fact, row = facts[canonical], reviewed.pop(field["key"])
            _require(row["canonical_field"] == canonical
                     and row["fact_key"] == FACT_KEYS[canonical]
                     and row["provenance"] == fact["source_ref"]
                     and type(row["value"]) is type(fact["value"])
                     and row["value"] == fact["value"])
            answer["value"] = fact["value"]
            answer["source_ref"] = f"user_fact:{FACT_KEYS[canonical]}"
        _require(not reviewed)
        compiled = json.dumps(template, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(",", ":")).encode("utf-8")
        verify_fact_snapshot(compiled, snapshot_raw,
                             expected_snapshot_sha256=expected_snapshot_sha256,
                             now=now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        # Do not chain private JSON/registry exceptions.
        pass
    else:
        return compiled
    raise PermissionError(ERROR) from None
