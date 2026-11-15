"""Validate one EJS-EXECUTION-MANIFEST-1 package (GD-006 step 2).

The daily Claude run observes an application form read-only and writes one
private manifest per candidate opportunity. The local Playwright engine later
compares the live form with that manifest and may fill, upload the approved CV
and submit once under SUBMIT-1.0 and the daily cap.

This module only validates bytes that a trusted caller has already read. It
opens no file, browser or network connection, reads no CV and grants no submit
authority on its own. Errors never echo supplied values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from ejs.services.single_application_inputs import ALLOWED_CANONICAL_FIELDS


SCHEMA_VERSION = "EJS-EXECUTION-MANIFEST-1"
POLICY_VERSION = "SUBMIT-1.0"
ERROR = "EXECUTION_MANIFEST_REVIEW_REQUIRED"

MAX_MANIFEST_BYTES = 512_000
MAX_VALIDITY = timedelta(hours=24)
MAX_OBSERVATION_AGE = timedelta(hours=6)
MAX_RUN_DURATION = timedelta(hours=2)  # prepared_at must fall inside the run
CLOCK_SKEW = timedelta(seconds=60)
MAX_CV_BYTES = 10 * 1024 * 1024
MAX_FIELDS = 80
MAX_OPTIONS = 300
MAX_TEXT_ANSWER = 5000

# One reviewed adapter family at a time. A family is added here only together
# with its engine adapter, fixtures and an approved GD/TEST record.
ATS_HOSTS: dict[str, frozenset[str]] = {
    "smartrecruiters": frozenset({"jobs.smartrecruiters.com"}),
}
ROLLOUT_CAPS = {"R2": 1, "R3": 5}

CONTROLS = frozenset({"text", "email", "tel", "textarea", "select", "radio", "checkbox"})
MODES = frozenset({"Verified Fact", "Approved Policy", "Approved Neutral Choice",
                   "Default No", "Blank Optional", "Evidence Text"})
FREE_TEXT_FIELDS = frozenset({"application.motivation_text", "application.cover_letter"})
# Employment facts the reviewed input compiler does not list but real forms ask.
EXTRA_CANONICAL_FIELDS = frozenset({"employment.current_company", "employment.current_title"})

# Verified Fact answers must cite the User Fact Registry key bound to their
# canonical field. A field without a binding cannot be a Verified Fact.
FACT_KEYS: dict[str, str] = {
    "candidate.first_name": "identity.first_name",
    "candidate.last_name": "identity.last_name",
    "candidate.email": "contact.email",
    "candidate.confirm_email": "contact.email",
    "candidate.phone": "contact.phone",
    "candidate.linkedin_url": "profile.linkedin",
    "candidate.city": "location.current_city",
    "location.city": "location.current_city",
    "candidate.country": "location.current_country",
    "location.country": "location.current_country",
    "work.country_of_residence": "location.current_country",
    "candidate.location": "location.current_full",
    "employment.current_company": "employment.current_company",
    "employment.current_title": "employment.current_title",
    "employment.notice_period": "employment.notice_period",
    "work.authorization": "work_right.eu_eea",
    "work.work_authorization": "work_right.eu_eea",
    "work.eligibility": "work_right.eu_eea",
    "work.sponsorship_required": "work_right.sponsorship_required",
    "work.requires_sponsorship": "work_right.sponsorship_required",
}

# Synonymous canonical fields: at most one member of a group per manifest.
_ALIAS_GROUPS = (
    frozenset({"work.authorization", "work.work_authorization", "work.eligibility"}),
    frozenset({"work.sponsorship_required", "work.requires_sponsorship"}),
    frozenset({"consent.privacy", "consent.privacy_acknowledgement"}),
    frozenset({"consent.marketing", "consent.marketing_opt_in"}),
    frozenset({"compensation.salary_expectation", "compensation.expected_salary",
               "compensation.expected_annual_gross", "compensation.expected_salary_range"}),
)

# A label (or question slug) about a guarded topic must use that topic's
# canonical family, so free text or another family cannot answer it.
_GUARDED_TOPICS = (
    ("work.", re.compile(r"authori[sz]|right[\s_-]*to[\s_-]*work|work[\s_-]*(?:right|permit|visa)"
                         r"|permit|visa|sponsor|eligib|citizen|nationalit|immigra", re.I)),
    ("compensation.", re.compile(r"salary|compensation|remuneration|wage|pay[\s_-]*(?:range|expect)"
                                 r"|gross|\bpay\b", re.I)),
    ("consent.", re.compile(r"consent|privacy|gdpr|data[\s_-]*(?:processing|protection|retention)"
                            r"|marketing|talent[\s_-]*pool|newsletter|terms", re.I)),
    ("demographic.", re.compile(r"gender|\bsex\b|ethnic|\brace\b|disab|religio|orientation"
                                r"|veteran|pronoun|caring[\s_-]*responsib", re.I)),
)
_CHECKBOX_FAMILIES = ("consent.", "work.")
_QUESTION_FIELD = re.compile(r"application\.question\.[a-z0-9][a-z0-9_]{0,63}")
_PRIVACY_FIELDS = frozenset({"consent.privacy", "consent.privacy_acknowledgement"})
_MARKETING_FIELDS = frozenset({"consent.marketing", "consent.marketing_opt_in"})
_SALARY_FIELDS = frozenset({"compensation.salary_expectation", "compensation.expected_salary",
                            "compensation.expected_annual_gross",
                            "compensation.expected_salary_range"})

_HEX_64 = re.compile(r"[a-f0-9]{64}")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,199}")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")  # no ':' (submit-key part)
_UTC_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:\+00:00|Z)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_TEL = re.compile(r"\+?[0-9][0-9 ()-]{4,29}")
_ISTANBUL = ZoneInfo("Europe/Istanbul")
_RUN_ID = re.compile(r"[0-9]{8}-[0-9]{4}")
_FIELD_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_CV_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.pdf")
_POLICY_REF = re.compile(r"policy:SUBMIT-1\.0:(A[1-9]|G[1-7])")
_FACT_REF = re.compile(r"user_fact:([a-z0-9_]+(?:\.[a-z0-9_]+)+)")
_EVIDENCE_REF = re.compile(r"evidence:[A-Za-z0-9][A-Za-z0-9:_.-]{0,199}")

_TOP_KEYS = frozenset({"schema_version", "execution_id", "prepared_by_run", "prepared_at",
                       "expires_at", "candidate_profile_version", "submit_key", "target",
                       "policy", "form", "cv"})
_TARGET_KEYS = frozenset({"ats_family", "company", "posting_id", "requisition_id",
                          "opportunity_key", "apply_url"})
_POLICY_KEYS = frozenset({"version", "rollout_stage", "daily_cap"})
_FORM_KEYS = frozenset({"observed_at", "fingerprint", "fields", "cv_upload"})
_FIELD_KEYS = frozenset({"key", "label", "control", "required", "options",
                         "canonical_field", "answer"})
_ANSWER_KEYS = frozenset({"mode", "value", "source_ref"})
_UPLOAD_KEYS = frozenset({"key", "label", "required"})
_CV_KEYS = frozenset({"library_name", "filename", "sha256", "size_bytes", "mime_type"})


@dataclass(frozen=True)
class ManifestAnswer:
    mode: str
    value: object
    source_ref: str | None


@dataclass(frozen=True)
class ManifestField:
    key: str
    label: str
    control: str
    required: bool
    options: tuple[str, ...] | None
    canonical_field: str
    answer: ManifestAnswer


@dataclass(frozen=True)
class ExecutionManifest:
    sha256: str
    execution_id: str
    prepared_by_run: str
    prepared_at: datetime
    expires_at: datetime
    candidate_profile_version: str
    submit_key: str
    ats_family: str
    company: str
    posting_id: str
    requisition_id: str
    opportunity_key: str
    apply_url: str
    rollout_stage: str
    daily_cap: int
    observed_at: datetime
    form_fingerprint: str
    fields: tuple[ManifestField, ...]
    cv_upload_key: str
    cv_upload_label: str
    cv_library_name: str
    cv_filename: str
    cv_sha256: str
    cv_size_bytes: int


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise PermissionError(ERROR)


def _finite_float(value: str) -> float:
    result = float(value)
    _require(math.isfinite(result))
    return result


def _exact_object(value: object, keys: frozenset[str]) -> dict:
    _require(isinstance(value, dict) and set(value) == keys)
    return value  # type: ignore[return-value]


def _opaque(value: object) -> bool:
    return isinstance(value, str) and bool(_OPAQUE.fullmatch(value))


def _text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and "\x00" not in value)


def _utc(value: object) -> datetime:
    _require(isinstance(value, str) and bool(_UTC_STAMP.fullmatch(value)))
    try:
        parsed = datetime.fromisoformat(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise PermissionError(ERROR) from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, low: int, high: int) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high)
    return value  # type: ignore[return-value]


def _apply_url(value: object, family: str, posting_id: str) -> str:
    _require(isinstance(value, str) and len(value) <= 2048 and value.isascii()
             and value.isprintable() and not any(ch.isspace() for ch in value)
             and "%" not in value and "\\" not in value)
    parts = urlsplit(value)  # type: ignore[arg-type]
    _require(parts.scheme == "https" and parts.hostname in ATS_HOSTS[family]
             and parts.netloc == parts.hostname and parts.port is None
             and not parts.query and not parts.fragment and parts.path.startswith("/")
             and ".." not in parts.path)
    segments = [item for item in parts.path.split("/") if item]
    _require(any(item == posting_id or item.startswith(posting_id + "-") for item in segments))
    return value  # type: ignore[return-value]


def form_fingerprint(fields: list[dict] | tuple[ManifestField, ...], cv_upload: dict) -> str:
    """Return the canonical SHA-256 of the observed form inventory.

    Only structure is hashed (key, label, control, requiredness, options and
    the CV upload control); answers are excluded so the engine can compare its
    own live observation without knowing candidate values.
    """
    rows = []
    for field in fields:
        if isinstance(field, ManifestField):
            row = [field.key, field.label, field.control, field.required,
                   list(field.options) if field.options is not None else None]
        else:
            row = [field["key"], field["label"], field["control"], field["required"],
                   field["options"]]
        rows.append(row)
    rows.sort(key=lambda item: item[0])
    upload = [cv_upload["key"], cv_upload["label"], cv_upload["required"]]
    canonical = json.dumps({"fields": rows, "cv_upload": upload}, ensure_ascii=False,
                           separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_field_allowed(name: object) -> bool:
    return isinstance(name, str) and (name in ALLOWED_CANONICAL_FIELDS
                                      or name in EXTRA_CANONICAL_FIELDS
                                      or name in FREE_TEXT_FIELDS
                                      or bool(_QUESTION_FIELD.fullmatch(name)))


def _validate_answer(field: dict) -> ManifestAnswer:
    answer = _exact_object(field["answer"], _ANSWER_KEYS)
    mode, value, ref = answer["mode"], answer["value"], answer["source_ref"]
    canonical, control, required = field["canonical_field"], field["control"], field["required"]
    options = field["options"]
    _require(isinstance(mode, str) and mode in MODES)

    # Mode-level source rules.
    if mode == "Verified Fact":
        match = _FACT_REF.fullmatch(ref) if isinstance(ref, str) else None
        _require(match and canonical in FACT_KEYS and match.group(1) == FACT_KEYS[canonical])
    elif mode in ("Approved Policy", "Approved Neutral Choice"):
        _require(isinstance(ref, str) and _POLICY_REF.fullmatch(ref))
    elif mode == "Evidence Text":
        _require(isinstance(ref, str) and _EVIDENCE_REF.fullmatch(ref))
    elif mode == "Default No":
        _require(ref is None or ref == "policy:SUBMIT-1.0:A5")
    else:  # Blank Optional
        _require(ref is None and value is None and required is False)

    # Value shape against the observed control.
    if mode != "Blank Optional":
        if control == "checkbox":
            _require(isinstance(value, bool) and canonical.startswith(_CHECKBOX_FAMILIES)
                     and (value or not field["required"]))  # required box unchecked blocks submit
        elif control in ("select", "radio"):
            _require(isinstance(value, str) and value in options)
        elif control == "textarea":
            _require(_text(value, MAX_TEXT_ANSWER))
        elif control == "email":
            _require(isinstance(value, str) and _EMAIL.fullmatch(value))
        elif control == "tel":
            _require(isinstance(value, str) and _TEL.fullmatch(value))
        else:
            _require(_text(value, 500))

    # Field-family rules (SUBMIT-1.0 A2-A7).
    if canonical.startswith("work."):
        _require(mode == "Verified Fact")  # A2
    if canonical in _SALARY_FIELDS:
        _require(mode == "Approved Policy" and ref == "policy:SUBMIT-1.0:A3")  # A3
    elif canonical.startswith("compensation."):
        _require(mode in ("Verified Fact", "Blank Optional"))
    if canonical in _PRIVACY_FIELDS:
        _require(mode == "Approved Policy" and ref == "policy:SUBMIT-1.0:A4"
                 and control == "checkbox" and value is True)  # A4
    if canonical in _MARKETING_FIELDS:
        _require(mode in ("Default No", "Blank Optional") and value is not True)  # A5
        if mode == "Default No":
            _require(value is False or (isinstance(value, str) and value.casefold() == "no"))
    elif mode == "Default No":
        _require(False)
    if canonical.startswith("demographic."):
        _require(mode in ("Approved Neutral Choice", "Blank Optional"))  # A6
        if mode == "Approved Neutral Choice":
            _require(ref == "policy:SUBMIT-1.0:A6" and isinstance(value, str)
                     and value.casefold() == "prefer not to say")
    elif mode == "Approved Neutral Choice":
        _require(False)
    if mode == "Approved Policy":
        _require(canonical in _PRIVACY_FIELDS or canonical in _SALARY_FIELDS)
    if mode == "Evidence Text":
        _require((canonical in FREE_TEXT_FIELDS or _QUESTION_FIELD.fullmatch(canonical))
                 and control in ("text", "textarea"))  # A7
    elif canonical in FREE_TEXT_FIELDS:
        _require(mode == "Blank Optional")

    if required:
        _require(mode != "Blank Optional" and value is not None)
    return ManifestAnswer(mode=mode, value=value, source_ref=ref)


def _validate_field(raw: object, seen_keys: set[str]) -> ManifestField:
    field = _exact_object(raw, _FIELD_KEYS)
    key, label, control = field["key"], field["label"], field["control"]
    _require(isinstance(key, str) and _FIELD_KEY.fullmatch(key) and key not in seen_keys)
    seen_keys.add(key)
    _require(_text(label, 500) and isinstance(control, str) and control in CONTROLS
             and isinstance(field["required"], bool))
    options = field["options"]
    if control in ("select", "radio"):
        _require(isinstance(options, list) and 0 < len(options) <= MAX_OPTIONS
                 and all(_text(item, 300) for item in options)
                 and len(set(options)) == len(options))
    else:
        _require(options is None)
    canonical = field["canonical_field"]
    _require(_canonical_field_allowed(canonical))
    topic_text = label + " " + (canonical.replace("_", " ") if canonical.startswith("application.") else "")
    for prefix, pattern in _GUARDED_TOPICS:
        if pattern.search(topic_text):
            _require(canonical.startswith(prefix))
    answer = _validate_answer(field)
    return ManifestField(key=key, label=label, control=control, required=field["required"],
                         options=tuple(options) if options is not None else None,
                         canonical_field=field["canonical_field"], answer=answer)


def load_execution_manifest(raw: bytes, *, now: datetime,
                            expected_sha256: str | None = None) -> ExecutionManifest:
    """Validate manifest bytes and return an immutable view, or raise PermissionError."""
    _require(isinstance(raw, (bytes, bytearray)) and 0 < len(raw) <= MAX_MANIFEST_BYTES)
    _require(isinstance(now, datetime) and now.tzinfo is not None)
    now = now.astimezone(timezone.utc)
    digest = hashlib.sha256(bytes(raw)).hexdigest()
    if expected_sha256 is not None:
        _require(isinstance(expected_sha256, str) and _HEX_64.fullmatch(expected_sha256)
                 and expected_sha256 == digest)
    try:
        text = bytes(raw).decode("utf-8")
        document = json.loads(text, object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant, parse_float=_finite_float)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PermissionError(ERROR) from exc

    top = _exact_object(document, _TOP_KEYS)
    _require(top["schema_version"] == SCHEMA_VERSION)
    _require(_opaque(top["execution_id"]))
    _require(isinstance(top["candidate_profile_version"], str)
             and _SEGMENT.fullmatch(top["candidate_profile_version"]))
    _require(isinstance(top["prepared_by_run"], str) and _RUN_ID.fullmatch(top["prepared_by_run"]))

    target = _exact_object(top["target"], _TARGET_KEYS)
    family = target["ats_family"]
    _require(isinstance(family, str) and family in ATS_HOSTS)
    _require(_text(target["company"], 200))
    for name in ("posting_id", "requisition_id"):
        _require(isinstance(target[name], str) and _SEGMENT.fullmatch(target[name]))
    _require(_opaque(target["opportunity_key"]))
    apply_url = _apply_url(target["apply_url"], family, target["posting_id"])
    expected_key = (f"submit:{target['opportunity_key']}:{target['requisition_id']}:"
                    f"{top['candidate_profile_version']}")
    _require(top["submit_key"] == expected_key)

    policy = _exact_object(top["policy"], _POLICY_KEYS)
    _require(policy["version"] == POLICY_VERSION and isinstance(policy["rollout_stage"], str)
             and policy["rollout_stage"] in ROLLOUT_CAPS)
    cap = _strict_int(policy["daily_cap"], 1, ROLLOUT_CAPS[policy["rollout_stage"]])

    prepared_at, expires_at = _utc(top["prepared_at"]), _utc(top["expires_at"])
    form = _exact_object(top["form"], _FORM_KEYS)
    observed_at = _utc(form["observed_at"])
    _require(observed_at <= prepared_at <= now + CLOCK_SKEW)
    _require(prepared_at - observed_at <= MAX_OBSERVATION_AGE)
    _require(prepared_at < expires_at <= prepared_at + MAX_VALIDITY and now < expires_at)
    try:
        run_started = datetime.strptime(top["prepared_by_run"], "%Y%m%d-%H%M").replace(tzinfo=_ISTANBUL)
    except ValueError as exc:
        raise PermissionError(ERROR) from exc
    _require(run_started - CLOCK_SKEW <= prepared_at <= run_started + MAX_RUN_DURATION)

    raw_fields = form["fields"]
    _require(isinstance(raw_fields, list) and 0 < len(raw_fields) <= MAX_FIELDS)
    seen: set[str] = set()
    fields = tuple(_validate_field(item, seen) for item in raw_fields)
    groups = [next((min(group) for group in _ALIAS_GROUPS if f.canonical_field in group),
                   f.canonical_field) for f in fields]
    _require(len(set(groups)) == len(groups))

    upload = _exact_object(form["cv_upload"], _UPLOAD_KEYS)
    _require(isinstance(upload["key"], str) and _FIELD_KEY.fullmatch(upload["key"])
             and upload["key"] not in seen and _text(upload["label"], 500)
             and upload["required"] is True)
    fingerprint = form["fingerprint"]
    _require(isinstance(fingerprint, str) and _HEX_64.fullmatch(fingerprint)
             and fingerprint == form_fingerprint(raw_fields, upload))

    cv = _exact_object(top["cv"], _CV_KEYS)
    _require(_text(cv["library_name"], 120)
             and isinstance(cv["filename"], str) and _CV_FILENAME.fullmatch(cv["filename"])
             and isinstance(cv["sha256"], str) and _HEX_64.fullmatch(cv["sha256"])
             and cv["mime_type"] == "application/pdf")
    size = _strict_int(cv["size_bytes"], 1, MAX_CV_BYTES)

    return ExecutionManifest(
        sha256=digest, execution_id=top["execution_id"], prepared_by_run=top["prepared_by_run"],
        prepared_at=prepared_at, expires_at=expires_at,
        candidate_profile_version=top["candidate_profile_version"], submit_key=top["submit_key"],
        ats_family=family, company=target["company"], posting_id=target["posting_id"],
        requisition_id=target["requisition_id"], opportunity_key=target["opportunity_key"],
        apply_url=apply_url, rollout_stage=policy["rollout_stage"], daily_cap=cap,
        observed_at=observed_at, form_fingerprint=fingerprint, fields=fields,
        cv_upload_key=upload["key"], cv_upload_label=upload["label"],
        cv_library_name=cv["library_name"], cv_filename=cv["filename"],
        cv_sha256=cv["sha256"], cv_size_bytes=size,
    )
