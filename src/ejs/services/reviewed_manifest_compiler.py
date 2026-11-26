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


def _decode(raw, limit, pin):
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    _require(type(pin) is str and _HEX.fullmatch(pin))
    _require(hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin))
    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                     parse_constant=_constant, parse_float=_finite)
    _require(type(doc) is dict)
    return doc


def _compile(template_raw, registry_raw, snapshot_raw, expected_template_sha256,
             expected_registry_sha256, expected_snapshot_sha256, now):
    _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
    template = _decode(template_raw, MAX_MANIFEST_BYTES, expected_template_sha256)
    registry = _decode(registry_raw, _MAX_REGISTRY_BYTES, expected_registry_sha256)
    snapshot = _decode(snapshot_raw, _MAX_SNAPSHOT_BYTES, expected_snapshot_sha256)
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
    values_by_fact_key = {}
    for field in fields:
        answer = field["answer"]
        if answer["mode"] != "Verified Fact":
            continue
        # Do not silently repair a writer-supplied candidate answer.
        _require(answer["value"] is None and answer["source_ref"] is None)
        canonical = field["canonical_field"]
        _require(canonical in FACT_KEYS and canonical in facts)
        fact, row = facts[canonical], reviewed.pop(field["key"])
        fact_key = FACT_KEYS[canonical]
        value = fact["value"]
        _require(row["canonical_field"] == canonical
                 and row["fact_key"] == fact_key
                 and row["provenance"] == fact["source_ref"]
                 and type(row["value"]) is type(value) and row["value"] == value)
        # Two canonical fields can cite the same fact, such as email and confirmation.
        previous = values_by_fact_key.setdefault(fact_key, value)
        _require(type(previous) is type(value) and previous == value)
        answer["value"] = value
        answer["source_ref"] = f"user_fact:{fact_key}"
    _require(not reviewed)
    compiled = json.dumps(template, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    verify_fact_snapshot(compiled, snapshot_raw,
                         expected_snapshot_sha256=expected_snapshot_sha256,
                         now=now)
    return compiled


def compile_reviewed_manifest(template_raw: bytes, registry_raw: bytes,
                              snapshot_raw: bytes, *,
                              expected_registry_sha256: str,
                              expected_snapshot_sha256: str,
                              expected_template_sha256: str | None = None,
                              now: datetime) -> bytes:
    """Fill explicit Verified Fact placeholders in an independently pinned template.

    The caller supplies the current review pins for all three exact-byte
    inputs. The template pin binds non-fact answers and package metadata.
    Fact validation uses the current registry and field snapshot. The caller
    owns approval of non-fact answers, pin authentication and revocation.
    """
    try:
        return _compile(template_raw, registry_raw, snapshot_raw, expected_template_sha256,
                        expected_registry_sha256, expected_snapshot_sha256, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        # Drop the private parser error and its traceback before raising.
        pass
    # The public traceback can expose frame locals to error reporters.
    del template_raw, registry_raw, snapshot_raw
    del expected_template_sha256, expected_registry_sha256, expected_snapshot_sha256, now
    raise PermissionError(ERROR) from None
