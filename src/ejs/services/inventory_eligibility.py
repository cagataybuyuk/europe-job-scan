"""Offline W36-03 eligibility check against the owner-approved qualification inventory.

Owner decision (AIW-104, met_authority=inventory): a mandatory requirement is
met only by a deterministic exact match to one held, verified and fresh entry
of the pinned inventory. The match rule is fixed: Unicode NFC plus collapsed
whitespace, then string equality. Nothing is inferred. Each row's claimed
result is recomputed here and must agree; a non-met row rejects. The caller
must supply every pin from the trusted standing-envelope sources.

The vacancy country comes only from a separate owner-pinned vacancy-country
attestation. The package pins it by hash; it names the exact target and the
pinned F05 handoff by hash. Neither the package's own country field nor any
free-text location decides the country. How the owner reviews an attestation
before pinning it is an owner procedure (S4 runbook), not a code decision.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import unicodedata
from urllib.parse import urlsplit

from ejs.services.current_fact_approval import MAX_REGISTRY_BYTES, MAX_SNAPSHOT_BYTES
from ejs.services.detail_verification import LIVE_ORIGIN, MAX_LINE, SCHEMA_HANDOFF
from ejs.services.execution_manifest import (
    _EVIDENCE_REF, _HEX_64, _OPAQUE, _TARGET_KEYS, _exact_object, _reject_constant,
    _opaque, _strict_int, _unique_pairs, _utc, _validate_target, load_execution_manifest,
)
from ejs.services.single_application_inputs import _EXPLICIT_SOURCE
from ejs.services.suitability import _COUNTRY_NAMES
from ejs.services.vacancy_evidence_gate import MAX_FRESHNESS_WINDOW_HOURS

ERROR = "ELIGIBILITY_REVIEW_REQUIRED"
INVENTORY_SCHEMA = "EJS-QUALIFICATION-INVENTORY-1"
PACKAGE_SCHEMA = "EJS-BATCH-ELIGIBILITY-1"
ATTESTATION_SCHEMA = "EJS-VACANCY-COUNTRY-ATTESTATION-1"
MATCHING_RULE = "EJS-INVENTORY-EXACT-1"
MAX_EMPLOYER_EVIDENCE_AGE = timedelta(hours=MAX_FRESHNESS_WINDOW_HOURS)
MAX_INVENTORY_EVIDENCE_AGE = timedelta(days=30)  # same bound as verified registry facts
MAX_ENTRIES = 200
MAX_LINES = 40

_ATS = "smartrecruiters"
_RESULTS = frozenset({"met", "unmet", "unknown"})
_INVENTORY_KEYS = frozenset({"schema_version", "candidate_profile_version",
                             "matching_rule_version", "entries", "evidence_texts"})
_ENTRY_KEYS = frozenset({"key", "held", "requirement_texts", "source_ref", "verified",
                         "verified_at"})
_EVIDENCE_KEYS = frozenset({"ref", "text_sha256", "entry_key"})
_PACKAGE_KEYS = frozenset({"schema_version", "envelope_id", "generation", "execution_id",
                           "candidate_profile_version", "country", "target",
                           "matching_rule_version", "inventory_sha256", "handoff_sha256",
                           "snapshot_sha256", "country_attestation_sha256", "requirements"})
_ATTESTATION_KEYS = frozenset({"schema_version", "execution_id", "target", "country",
                               "source_ref", "attested_at"})
_ROW_KEYS = frozenset({"text", "result", "inventory_key"})


@dataclass(frozen=True, repr=False)
class EligibilityResult:
    """Pins for the caller's manifest recompilation; not an execution capability."""
    execution_id: str
    snapshot_sha256: str


@dataclass(frozen=True, repr=False)
class BoundVacancy:
    employer_display_name: str
    handoff_rank: int
    detail_sha256: str
    observed_at: datetime
    mandatory_lines: tuple[str, ...]


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _digest(value: object) -> bool:
    return type(value) is str and bool(_HEX_64.fullmatch(value))


def _pinned(raw: object, pin: object) -> None:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_REGISTRY_BYTES and _digest(pin))
    _require(hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin))


def _load(raw: bytes) -> object:
    # None of these schemas has a floating-point field.
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                      parse_constant=_reject_constant, parse_float=_reject_constant)


def _line(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= MAX_LINE and value == value.strip()
            and value.isprintable())


def canonical_text(text: str) -> str:
    """The only normalization of EJS-INVENTORY-EXACT-1; no case, synonym or stem folding."""
    return unicodedata.normalize("NFC", " ".join(text.split()))


def _fresh(stamp: object, now: datetime, age: timedelta) -> None:
    moment = _utc(stamp)
    _require(moment <= now and now - moment <= age)


def _inventory(raw: bytes, profile: str, now: datetime) -> tuple[dict, dict]:
    doc = _exact_object(_load(raw), _INVENTORY_KEYS)
    _require(doc["schema_version"] == INVENTORY_SCHEMA
             and doc["matching_rule_version"] == MATCHING_RULE
             and doc["candidate_profile_version"] == profile)
    entries = doc["entries"]
    _require(type(entries) is list and 0 < len(entries) <= MAX_ENTRIES)
    by_key, by_text = {}, {}
    for entry in entries:
        _exact_object(entry, _ENTRY_KEYS)
        key, texts, source = entry["key"], entry["requirement_texts"], entry["source_ref"]
        _require(type(key) is str and _OPAQUE.fullmatch(key) and key not in by_key
                 and type(entry["held"]) is bool and entry["verified"] is True
                 and type(source) is str and len(source) <= 512 and source.isprintable()
                 and _EXPLICIT_SOURCE.fullmatch(source)
                 and type(texts) is list and 0 < len(texts) <= MAX_LINES)
        _fresh(entry["verified_at"], now, MAX_INVENTORY_EVIDENCE_AGE)
        for text in texts:
            _require(_line(text))
            # One canonical text maps to one entry. Overlap is ambiguous, or
            # contradictory when held differs, so the whole inventory rejects.
            _require(canonical_text(text) not in by_text)
            by_text[canonical_text(text)] = entry
        by_key[key] = entry
    evidence = doc["evidence_texts"]
    _require(type(evidence) is list)
    refs = {}
    for item in evidence:
        _exact_object(item, _EVIDENCE_KEYS)
        ref, owner = item["ref"], item["entry_key"]
        _require(type(ref) is str and _EVIDENCE_REF.fullmatch(ref) and ref not in refs
                 and _digest(item["text_sha256"])
                 and type(owner) is str and owner in by_key and by_key[owner]["held"] is True)
        refs[ref] = item["text_sha256"]
    return by_text, refs


def _employer_vacancy(raw: bytes, target: dict, now: datetime) -> BoundVacancy:
    _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
    target = _validate_target(target)
    tenant = target["company"]
    _require(target["ats_family"] == _ATS
             and urlsplit(target["apply_url"]).path.split("/")[1] == tenant)
    handoff = _load(raw)
    _require(type(handoff) is dict and handoff.get("schema") == SCHEMA_HANDOFF
             and handoff.get("evidence_origin") == LIVE_ORIGIN
             and handoff.get("real_evidence") is True
             and handoff.get("function06_eligible") is True
             and handoff.get("executable") is False
             and handoff.get("submit_authorized") is False)
    candidates = handoff.get("candidates")
    _require(type(candidates) is list)
    matches = [c for c in candidates if type(c) is dict and c.get("ats") == _ATS
               and c.get("posting_id") == target["posting_id"]
               and (c.get("tenant") == tenant or c.get("canonical_url") == target["apply_url"])]
    _require(len(matches) == 1)
    candidate = matches[0]
    display_name, rank = candidate.get("company"), candidate.get("handoff_rank")
    _require(candidate.get("region") == "global" and candidate.get("tenant") == tenant
             and candidate.get("requisition_id") == target["requisition_id"]
             and candidate.get("canonical_url") == target["apply_url"]
             and _line(display_name) and type(rank) is int and rank > 0
             and candidate.get("status") == "candidate_for_review"
             and candidate.get("status_reasons") == []
             and candidate.get("evidence_origin") == LIVE_ORIGIN
             and _digest(candidate.get("detail_sha256")))
    observed = _utc(candidate.get("observed_at"))
    _fresh(candidate.get("observed_at"), now, MAX_EMPLOYER_EVIDENCE_AGE)
    lines = candidate.get("mandatory_qualification_lines")
    # An empty list is not "no requirements": F05 may simply have found none.
    _require(type(lines) is list and 0 < len(lines) <= MAX_LINES
             and all(map(_line, lines)) and len(set(lines)) == len(lines))
    return BoundVacancy(display_name, rank, candidate["detail_sha256"], observed, tuple(lines))


def bind_f05_vacancy(handoff_raw: bytes, target: dict, *, expected_handoff_sha256: str,
                    now: datetime) -> BoundVacancy:
    """Bind the exact manifest target to one row of an independently pinned F05 handoff.

    SmartRecruiters manifest ``company`` is the ATS tenant. The employer's display
    name comes from the pinned row. The manifest's opportunity key remains bound by
    the separately reviewed template and snapshot. This check does no I/O.
    """
    try:
        _pinned(handoff_raw, expected_handoff_sha256)
        return _employer_vacancy(handoff_raw, target, now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del handoff_raw, target, expected_handoff_sha256, now
    raise PermissionError(ERROR) from None


def _employer_lines(raw: bytes, manifest, now: datetime) -> tuple[tuple[str, ...], datetime]:
    vacancy = _employer_vacancy(raw, {key: getattr(manifest, key) for key in _TARGET_KEYS}, now)
    return vacancy.mandatory_lines, vacancy.observed_at


def _attested_country(raw: bytes, manifest, handoff_sha256: str, observed: datetime,
                      now: datetime) -> str:
    """Return the country of the pinned, target-specific vacancy-country attestation."""
    doc = _exact_object(_load(raw), _ATTESTATION_KEYS)
    target = _exact_object(doc["target"], _TARGET_KEYS)
    country = doc["country"]
    _require(doc["schema_version"] == ATTESTATION_SCHEMA
             and doc["execution_id"] == manifest.execution_id
             and target == {key: getattr(manifest, key) for key in _TARGET_KEYS}
             and type(country) is str and country in _COUNTRY_NAMES
             # The evidence reference is the pinned handoff's hash, never free text.
             and type(doc["source_ref"]) is str
             and hmac.compare_digest(doc["source_ref"], f"sha256:{handoff_sha256}"))
    attested = _utc(doc["attested_at"])
    # Not before the evidence it reviews, not future, and as fresh as that evidence.
    _require(observed <= attested <= now and now - attested <= MAX_EMPLOYER_EVIDENCE_AGE)
    return country


def recompute(text: str, by_text: dict) -> tuple[str, str | None]:
    entry = by_text.get(canonical_text(text))
    if entry is None:
        return "unknown", None
    return ("met" if entry["held"] else "unmet"), entry["key"]


def _check_evidence_texts(manifest, refs: dict) -> None:
    for field in manifest.fields:
        if field.answer.mode == "Evidence Text":
            pin = refs.get(field.answer.source_ref)
            _require(pin is not None and hmac.compare_digest(
                pin, hashlib.sha256(field.answer.value.encode("utf-8")).hexdigest()))


def _draft_package(inventory_raw, handoff_raw, attestation_raw, manifest_raw, snapshot_raw, *,
                   expected_inventory_sha256, envelope_id, generation, now) -> bytes:
    _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
    _require(_opaque(envelope_id))
    _strict_int(generation, 1, 2**63 - 1)
    _pinned(inventory_raw, expected_inventory_sha256)
    _require(all(type(raw) is bytes and 0 < len(raw) <= MAX_REGISTRY_BYTES
                 for raw in (handoff_raw, attestation_raw)))
    _require(type(snapshot_raw) is bytes and 0 < len(snapshot_raw) <= MAX_SNAPSHOT_BYTES)
    manifest = load_execution_manifest(manifest_raw, now=now)
    _require(manifest.ats_family == _ATS and manifest.pass_kind == "submit")
    by_text, refs = _inventory(inventory_raw, manifest.candidate_profile_version, now)
    lines, observed = _employer_lines(handoff_raw, manifest, now)
    handoff_sha256 = hashlib.sha256(handoff_raw).hexdigest()
    country = _attested_country(attestation_raw, manifest, handoff_sha256, observed, now)
    _check_evidence_texts(manifest, refs)

    rows = []
    for line in lines:
        result, key = recompute(line, by_text)
        _require(result == "met")
        rows.append(dict(text=line, result=result, inventory_key=key))
    package = dict(schema_version=PACKAGE_SCHEMA, envelope_id=envelope_id, generation=generation,
                   execution_id=manifest.execution_id,
                   candidate_profile_version=manifest.candidate_profile_version, country=country,
                   target={key: getattr(manifest, key) for key in _TARGET_KEYS},
                   matching_rule_version=MATCHING_RULE, inventory_sha256=expected_inventory_sha256,
                   handoff_sha256=handoff_sha256,
                   snapshot_sha256=hashlib.sha256(snapshot_raw).hexdigest(),
                   country_attestation_sha256=hashlib.sha256(attestation_raw).hexdigest(),
                   requirements=rows)
    raw = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _require(len(raw) <= MAX_REGISTRY_BYTES)
    return raw


def draft_eligibility_package(
    inventory_raw: bytes, handoff_raw: bytes, attestation_raw: bytes, manifest_raw: bytes,
    snapshot_raw: bytes, *, expected_inventory_sha256: str, envelope_id: str,
    generation: int, now: datetime,
) -> bytes:
    """Prepare reviewable package bytes from supplied inputs, without I/O.

    The caller supplies the owner-reviewed inventory pin and a vacancy-country
    attestation. Review must establish genuine employer provenance and attestation
    authorship. The returned bytes do not publish an index or authorize admission.
    Only standing admission verifies the root pins and exact recompilation.
    """
    try:
        return _draft_package(inventory_raw, handoff_raw, attestation_raw, manifest_raw, snapshot_raw,
                              expected_inventory_sha256=expected_inventory_sha256,
                              envelope_id=envelope_id, generation=generation, now=now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    del inventory_raw, handoff_raw, attestation_raw, manifest_raw, snapshot_raw
    del expected_inventory_sha256, envelope_id, generation, now
    raise PermissionError(ERROR) from None


def _evaluate(package_raw, inventory_raw, handoff_raw, attestation_raw, manifest_raw, *,
              expected_package_sha256, expected_inventory_sha256, envelope_id, generation,
              country, matching_rule_version, now) -> EligibilityResult:
    _require(isinstance(now, datetime) and now.utcoffset() == timedelta(0))
    _pinned(package_raw, expected_package_sha256)
    _pinned(inventory_raw, expected_inventory_sha256)
    manifest = load_execution_manifest(manifest_raw, now=now)
    package = _exact_object(_load(package_raw), _PACKAGE_KEYS)
    _require(package["schema_version"] == PACKAGE_SCHEMA
             and package["envelope_id"] == envelope_id
             and _strict_int(package["generation"], 1, 2**63 - 1) == generation
             and package["execution_id"] == manifest.execution_id
             and package["candidate_profile_version"] == manifest.candidate_profile_version
             and type(country) is str and country in _COUNTRY_NAMES
             and package["country"] == country
             and matching_rule_version == package["matching_rule_version"] == MATCHING_RULE
             and package["inventory_sha256"] == expected_inventory_sha256
             and _digest(package["snapshot_sha256"]))
    target = _exact_object(package["target"], _TARGET_KEYS)
    _require(target == {key: getattr(manifest, key) for key in _TARGET_KEYS})
    _pinned(handoff_raw, package["handoff_sha256"])
    _pinned(attestation_raw, package["country_attestation_sha256"])

    by_text, refs = _inventory(inventory_raw, manifest.candidate_profile_version, now)
    lines, observed = _employer_lines(handoff_raw, manifest, now)
    # The package's country is only an assertion; the attestation must agree.
    _require(_attested_country(attestation_raw, manifest, package["handoff_sha256"],
                               observed, now) == country)
    rows = package["requirements"]
    # Required rows match the employer's mandatory lines exactly: same text, same
    # order, nothing missing and nothing extra.
    _require(type(rows) is list and len(rows) == len(lines))
    for row, line in zip(rows, lines):
        _exact_object(row, _ROW_KEYS)
        _require(type(row["text"]) is str and row["text"] == line
                 and type(row["result"]) is str and row["result"] in _RESULTS)
        result, key = recompute(line, by_text)
        # The file's claim never decides; it must equal the recomputation.
        _require(row["result"] == result and row["inventory_key"] == key and result == "met")

    _check_evidence_texts(manifest, refs)
    return EligibilityResult(manifest.execution_id, package["snapshot_sha256"])


def evaluate_inventory_eligibility(
    package_raw: bytes, inventory_raw: bytes, handoff_raw: bytes, attestation_raw: bytes,
    manifest_raw: bytes, *,
    expected_package_sha256: str, expected_inventory_sha256: str, envelope_id: str,
    generation: int, country: str, matching_rule_version: str, now: datetime,
) -> EligibilityResult:
    """Recompute every mandatory requirement from the pinned inventory, without I/O.

    The package pin comes from the trusted eligibility index and the inventory
    pin from the trusted envelope. The package pins the F05 handoff, which must
    be fresh live employer evidence, and the vacancy-country attestation, whose
    country must equal the caller's (envelope) country. Unknown, unmet,
    ambiguous, contradictory or unresolved results reject with one opaque error.
    """
    try:
        return _evaluate(package_raw, inventory_raw, handoff_raw, attestation_raw, manifest_raw,
                         expected_package_sha256=expected_package_sha256,
                         expected_inventory_sha256=expected_inventory_sha256,
                         envelope_id=envelope_id, generation=generation, country=country,
                         matching_rule_version=matching_rule_version, now=now)
    except (PermissionError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError):
        pass
    # Keep private inputs out of the public error frame.
    del package_raw, inventory_raw, handoff_raw, attestation_raw, manifest_raw
    del expected_package_sha256, expected_inventory_sha256, envelope_id, generation
    del country, matching_rule_version, now
    raise PermissionError(ERROR) from None
