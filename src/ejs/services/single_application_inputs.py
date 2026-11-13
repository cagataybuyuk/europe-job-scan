"""Compile externally pinned private inputs for one reviewed application.

The external reviewer supplies both hashes and the source commit independently.
This module compares every proposed value with those exact verified facts; it
does not authenticate the sources, read a CV, or grant browser/submit authority.
No supplied paths, values, identifiers or source references appear in errors.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from ejs.services.oneclick_target import native_entry_policy_enabled


_ERROR = "APPLICATION_INPUT_REVIEW_REQUIRED"
_MAX_JSON_BYTES = 1_000_000
_HEX_64 = re.compile(r"[a-f0-9]{64}")
_HEX_40 = re.compile(r"[a-f0-9]{40}")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,511}")
_EXPLICIT_SOURCE = re.compile(r"(?:user[_-]decision|candidate[_-]verified):.+", re.I)
_PRIVACY_POLICY = re.compile(r"USER-[A-Z0-9][A-Z0-9_-]*-PRIVACY-[0-9]{8}")
_NEUTRAL_POLICIES = frozenset({"SUBMIT1-A6", "SUBMIT-1-A6"})
_PROFILE_FIELD = re.compile(
    r"profile\.(?:experience\.[0-9]{1,2}\.(?:title|company|dates|description|summary)"
    r"|education\.[0-9]{1,2}\.(?:school|degree|dates|description|summary))"
)
_PROFILE_EDIT_FIELD = re.compile(
    r"profile\.(?:experience\.[0-9]{1,2}\.(?:title|company|office_location|description|start_month|end_month|currently_working)"
    r"|education\.[0-9]{1,2}\.(?:school|major|degree|school_location|description|start_month|end_month))"
)
_PROFILE_EDITOR_FIELDS = {
    "experience": {"Title": "title", "Company": "company", "Office location": "office_location",
                   "Description": "description", "From": "start_month", "To": "end_month",
                   "I currently work here": "currently_working"},
    "education": {"Institution": "school", "Major": "major", "Degree": "degree",
                  "School location": "school_location", "Description": "description",
                  "From": "start_month", "To": "end_month"},
}

ALLOWED_CANONICAL_FIELDS = frozenset({
    "candidate.first_name", "candidate.last_name", "candidate.email",
    "candidate.confirm_email", "candidate.phone", "candidate.phone_country_code",
    "candidate.phone_country", "candidate.linkedin_url", "candidate.country",
    "candidate.city", "candidate.location",
    "address.line1", "address.line2", "address.line3", "address.city",
    "address.state", "address.state_or_territory", "address.postal_code",
    "address.country", "address.country_iso2",
    "location.country", "location.city", "location.postal_code",
    "employment.current_status", "employment.notice_period",
    "compensation.current_salary", "compensation.current_annual_gross",
    "compensation.expected_salary", "compensation.salary_expectation", "compensation.expected_annual_gross",
    "compensation.expected_salary_range", "compensation.currency",
    "work.authorization", "work.work_authorization", "work.sponsorship_required",
    "work.requires_sponsorship", "work.eligibility", "work.residency",
    "work.currently_lives_in_ireland", "work.country_of_residence",
    "demographic.gender", "demographic.gender_identity", "demographic.sexual_orientation",
    "demographic.caring_responsibilities", "demographic.disability", "demographic.disability_status",
    "demographic.ethnicity", "demographic.religion",
    "consent.privacy", "consent.privacy_acknowledgement",
    "consent.marketing", "consent.marketing_opt_in", "application.motivation_text",
})
_KINDS = frozenset({"text", "email", "tel", "number", "select", "radio", "checkbox"})
_MODES = frozenset({"Verified Fact", "Approved Policy", "Approved Neutral Choice",
                    "Default No", "Blank Optional"})
_PRIVACY_FIELDS = frozenset({"consent.privacy", "consent.privacy_acknowledgement"})
_MARKETING_FIELDS = frozenset({"consent.marketing", "consent.marketing_opt_in"})
_SALARY_POLICY_FIELDS = frozenset({"compensation.salary_expectation", "compensation.expected_salary",
                                  "compensation.expected_annual_gross", "compensation.expected_salary_range"})


def _require(condition: bool) -> None:
    if not condition:
        raise PermissionError(_ERROR)


def _opaque(value: object, max_length: int = 512) -> bool:
    return isinstance(value, str) and len(value) <= max_length and bool(_OPAQUE.fullmatch(value))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise PermissionError(_ERROR)


def _finite_float(value: str) -> float:
    result = float(value)
    _require(math.isfinite(result))
    return result


def _private_pinned_json(path: Path, expected_hash: str) -> dict:
    _require(isinstance(path, Path) and isinstance(expected_hash, str)
             and bool(_HEX_64.fullmatch(expected_hash)))
    # Open relative to an owner-only directory descriptor, then inspect the
    # actual opened inode. O_NONBLOCK avoids waiting on a substituted FIFO.
    _require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        _require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                 and stat.S_IMODE(parent.st_mode) == 0o700)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path.name, flags, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                     and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                     and 0 < before.st_size <= _MAX_JSON_BYTES)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_JSON_BYTES + 1)
            after = os.fstat(fd)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                      before.st_ctime_ns) ==
                     (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                      after.st_ctime_ns))
            _require(len(raw) <= _MAX_JSON_BYTES
                     and hashlib.sha256(raw).hexdigest() == expected_hash)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)
    result = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant,
                        parse_float=_finite_float)
    _require(isinstance(result, dict))
    return result


def _fresh(stamp: object, now: datetime, seconds: int) -> None:
    _require(isinstance(stamp, str) and len(stamp) <= 64)
    observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    _require(observed.tzinfo is not None and observed.utcoffset() is not None
             and 0 <= (now - observed).total_seconds() <= seconds)


def _scalar(value: object) -> bool:
    if type(value) is str:
        return len(value) <= 10_000 and "\x00" not in value
    if type(value) in (bool, int):
        return True
    return type(value) is float and math.isfinite(value)


def _canonical(value: object) -> bool:
    # Deliberately no trim/casefold/alias promotion of an unknown canonical key.
    return isinstance(value, str) and (value in ALLOWED_CANONICAL_FIELDS
                                      or bool(_PROFILE_FIELD.fullmatch(value))
                                      or bool(_PROFILE_EDIT_FIELD.fullmatch(value)))


def _bounded_text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and "\x00" not in value)


def _exact_https_url(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isascii():
        return False
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127
           for character in value) or any(character in value for character in ("\\", "*", "#")):
        return False
    url = urlsplit(value)
    return (url.scheme == "https" and bool(url.hostname) and url.netloc == url.hostname
            and not url.username and not url.password and not url.fragment
            and url.path.startswith("/") and not url.path.endswith("/"))


def _needs_explicit_source(canonical: str) -> bool:
    return (canonical.startswith(("work.", "compensation.", "consent."))
            or canonical in {"employment.current_status", "employment.notice_period"})


def _validate_facts(facts: dict, now: datetime) -> dict[str, dict]:
    _require(facts.get("schema_version") == "EJS-VERIFIED-FACTS-1"
             and _opaque(facts.get("candidate_profile_version"), 128))
    items = facts.get("facts")
    _require(isinstance(items, list) and 0 < len(items) <= 100)
    by_field = {}
    for fact in items:
        _require(isinstance(fact, dict))
        key = fact.get("canonical_field")
        _require(_canonical(key) and key not in by_field and _scalar(fact.get("value"))
                 and fact.get("verified") is True and fact.get("auto_fill_approved") is True
                 and _opaque(fact.get("source_ref")))
        _fresh(fact.get("verified_at"), now, 30 * 86_400)
        if _needs_explicit_source(key):
            _require(bool(_EXPLICIT_SOURCE.fullmatch(fact["source_ref"])))
        if key.endswith(".currently_working") and _PROFILE_EDIT_FIELD.fullmatch(key):
            _require(type(fact["value"]) is bool and bool(_EXPLICIT_SOURCE.fullmatch(fact["source_ref"])))
        elif _PROFILE_EDIT_FIELD.fullmatch(key):
            _require(type(fact["value"]) is str)
        elif _PROFILE_FIELD.fullmatch(key):
            _require(_bounded_text(fact["value"], 10_000))
        by_field[key] = fact
    return by_field


def _validate_field(field: object, facts: dict[str, dict], keys: set, selectors: set) -> None:
    _require(isinstance(field, dict))
    key, selector = field.get("key"), field.get("selector")
    _require(_opaque(key, 128) and key not in keys
             and isinstance(selector, str) and bool(selector.strip()) and len(selector) <= 512
             and "\x00" not in selector and selector not in selectors)
    label = field.get("label")
    _require(isinstance(label, str) and bool(label.strip()) and len(label) <= 1024
             and "\x00" not in label and field.get("kind") in _KINDS
             and type(field.get("required")) is bool
             and field.get("resolution_mode") in _MODES)
    canonical = field.get("canonical_field")
    # Profile cards are assertions only. Their facts never become a request to
    # edit an imported experience/education record through this field writer.
    _require(canonical in ALLOWED_CANONICAL_FIELDS and canonical in facts and _scalar(field.get("value")))
    fact = facts[canonical]
    value = field["value"]
    _require(type(value) is type(fact["value"]) and value == fact["value"]
             and field.get("provenance_ref") == fact["source_ref"])
    if field["required"] and isinstance(value, str):
        _require(bool(value.strip()))
    if field["kind"] == "checkbox":
        _require(type(value) is bool)
    elif field["kind"] == "number":
        _require(type(value) in (str, int, float))
        if isinstance(value, str) and value:
            _require(bool(re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value)))
    else:
        _require(type(value) is str)
    policy = field.get("policy_ref")
    mode = field["resolution_mode"]
    if policy is not None:
        _require(_opaque(policy))
    if mode in ("Approved Policy", "Approved Neutral Choice"):
        _require(_opaque(policy))
    if mode == "Default No":
        _require(canonical in _MARKETING_FIELDS and value is False)
    if mode == "Blank Optional":
        _require(field["required"] is False and (value == "" or value is False))
    if canonical.startswith("demographic."):
        if field["required"]:
            _require(mode == "Approved Neutral Choice" and value == "Prefer not to say"
                     and policy in _NEUTRAL_POLICIES)
        else:
            _require(mode == "Blank Optional" and (value == "" or value is False) and _opaque(policy))
    elif mode == "Approved Neutral Choice":
        _require(False)
    if canonical.startswith("work."):
        _require(mode == "Verified Fact")
    if canonical in _SALARY_POLICY_FIELDS:
        _require(mode == "Approved Policy" and _opaque(policy))
    if canonical in _PRIVACY_FIELDS:
        _require(field["kind"] == "checkbox" and type(value) is bool)
        if value:
            _require(mode == "Approved Policy" and isinstance(policy, str)
                     and bool(_PRIVACY_POLICY.fullmatch(policy)))
    if canonical in _MARKETING_FIELDS:
        _require(field["kind"] == "checkbox" and field["required"] is False and value is False
                 and mode in {"Default No", "Blank Optional"})
    if 'city_autocomplete' in field:
        widget = field['city_autocomplete']
        _require(canonical == 'candidate.city' and field['label'] == 'City' and field['kind'] == 'text'
                 and mode == 'Verified Fact' and type(value) is str and bool(value.strip())
                 and (bool(_EXPLICIT_SOURCE.fullmatch(fact['source_ref'])) or fact['source_ref'].startswith('approved_document:'))
                 and type(widget) is dict and set(widget) == {'type', 'host_selector'}
                 and widget['type'] == 'spl-city-text-v1' and _bounded_text(widget['host_selector'], 512))
    if 'phone_country_select' in field:
        widget = field['phone_country_select']
        country = facts.get('candidate.phone_country')
        _require(canonical == 'candidate.phone_country_code' and field['label'] == 'Country code'
                 and field['kind'] == 'select' and mode == 'Verified Fact'
                 and type(value) is str and bool(re.fullmatch(r'\+[1-9][0-9]{0,3}', value))
                 and (bool(_EXPLICIT_SOURCE.fullmatch(fact['source_ref'])) or fact['source_ref'].startswith('approved_document:'))
                 and 'city_autocomplete' not in field and type(widget) is dict
                 and set(widget) == {'type', 'host_selector', 'country_iso2', 'country_label', 'option_label', 'country_provenance_ref'}
                 and widget['type'] == 'spl-phone-country-v1' and _bounded_text(widget['host_selector'], 512)
                 and type(widget['country_iso2']) is str and bool(re.fullmatch(r'[A-Z]{2}', widget['country_iso2']))
                 and _bounded_text(widget['country_label'], 128)
                 and widget['option_label'] == widget['country_label'] + ' ' + value
                 and country is not None and country['value'] == widget['country_label']
                 and country['source_ref'] == widget['country_provenance_ref']
                 and (bool(_EXPLICIT_SOURCE.fullmatch(country['source_ref'])) or country['source_ref'].startswith('approved_document:')))
    keys.add(key)
    selectors.add(selector)


def _validate_profile_assertions(assertions: object, facts: dict[str, dict], selectors: set) -> None:
    _require(isinstance(assertions, list) and len(assertions) <= 50)
    seen = set()
    for assertion in assertions:
        _require(isinstance(assertion, dict))
        selector, canonical = assertion.get("selector"), assertion.get("canonical_field")
        _require(_bounded_text(selector, 512) and selector not in selectors
                 and isinstance(canonical, str) and bool(_PROFILE_FIELD.fullmatch(canonical))
                 and canonical in facts and canonical not in seen)
        if "label" in assertion:
            _require(_bounded_text(assertion["label"], 1024))
        fact, value = facts[canonical], assertion.get("value")
        _require(_bounded_text(value, 10_000) and type(value) is type(fact["value"])
                 and value == fact["value"] and assertion.get("provenance_ref") == fact["source_ref"])
        selectors.add(selector)
        seen.add(canonical)


def _validate_profile_corrections(step: dict, facts: dict[str, dict]) -> None:
    if "reviewed_profile_corrections" not in step:
        return
    phase = step["reviewed_profile_corrections"]
    _require(type(phase) is dict and set(phase) == {"container_selector", "card_selector", "cards", "edits"}
             and _bounded_text(phase["container_selector"], 512) and _bounded_text(phase["card_selector"], 512))
    cards, edits = phase["cards"], phase["edits"]
    _require(type(cards) is list and 0 < len(cards) <= 20 and type(edits) is list and 0 < len(edits) <= 10)
    assertions = {a["selector"]: a for a in step.get("profile_assertions", [])}
    by_selector, identities = {}, set()
    for card in cards:
        _require(type(card) is dict and set(card) == {"selector", "record_type", "record_index", "observed_text"}
                 and _bounded_text(card["selector"], 512) and card["selector"] not in by_selector
                 and card["record_type"] in _PROFILE_EDITOR_FIELDS
                 and type(card["record_index"]) is int and 0 <= card["record_index"] <= 99
                 and _bounded_text(card["observed_text"], 20_000))
        identity = (card["record_type"], card["record_index"])
        _require(identity not in identities)
        identities.add(identity)
        assertion = assertions.get(card["selector"])
        _require(assertion is not None and assertion["canonical_field"] ==
                 f"profile.{card['record_type']}.{card['record_index']}.summary")
        by_selector[card["selector"]] = card
    seen = set()
    for edit in edits:
        _require(type(edit) is dict and set(edit) == {"card_selector", "edit", "editor"}
                 and edit["card_selector"] in by_selector and edit["card_selector"] not in seen)
        seen.add(edit["card_selector"])
        card = by_selector[edit["card_selector"]]
        action, editor = edit["edit"], edit["editor"]
        action_name = "Edit " + card["record_type"]
        _require(type(action) is dict and set(action) == {"selector", "label"}
                 and _bounded_text(action["selector"], 512)
                 and isinstance(action["label"], str)
                 and bool(re.fullmatch(re.escape(action_name) + r"(?: [^\r\n]{1,1000})?", action["label"])))
        _require(type(editor) is dict and set(editor) == {"selector", "fingerprint", "fields", "save"}
                 and _bounded_text(editor["selector"], 512) and isinstance(editor["fingerprint"], str)
                 and bool(_HEX_64.fullmatch(editor["fingerprint"]))
                 and type(editor["fields"]) is list)
        expected = _PROFILE_EDITOR_FIELDS[card["record_type"]]
        _require(len(editor["fields"]) == len(expected))
        labels, selectors, keys, values = set(), set(), set(), {}
        for field in editor["fields"]:
            base = {"key", "selector", "label", "kind", "required", "canonical_field", "value", "provenance_ref"}
            _require(type(field) is dict and base <= set(field)
                     and set(field) <= base | {"omission_policy_ref", "month_picker", "text_autocomplete"}
                     and not ("month_picker" in field and "text_autocomplete" in field))
            label = field["label"]
            _require(label in expected and label not in labels and _opaque(field["key"], 128)
                     and field["key"] not in keys and _bounded_text(field["selector"], 512)
                     and field["selector"] not in selectors and type(field["required"]) is bool)
            canonical = f"profile.{card['record_type']}.{card['record_index']}.{expected[label]}"
            fact = facts.get(canonical)
            _require(field["canonical_field"] == canonical and fact is not None
                     and type(field["value"]) is type(fact["value"]) and field["value"] == fact["value"]
                     and field["provenance_ref"] == fact["source_ref"])
            if label in {"From", "To"}:
                _require(field["kind"] == "month" and isinstance(field["value"], str)
                         and (bool(re.fullmatch(r"(?:19|20)[0-9]{2}-(?:0[1-9]|1[0-2])", field["value"]))
                              or (field["value"] == "" and field["required"] is False
                                  and fact.get("omission_reason") == "month_unknown"
                                  and field.get("omission_policy_ref") == "PROFILE-OMIT-UNKNOWN-MONTH")))
                _require(field["value"] == "" or "omission_policy_ref" not in field)
                _require("text_autocomplete" not in field)
                if "month_picker" in field:
                    widget = field["month_picker"]
                    mandatory = {"type", "field_selector", "picker_selector", "calendar_selector", "calendar_fingerprint", "display_format"}
                    _require(type(widget) is dict and mandatory <= set(widget) <= mandatory | {"clear"}
                             and widget["type"] == "spl-month-year-v1"
                             and widget["display_format"] == "iso-month-start-transport-v1"
                             and all(_bounded_text(widget[key], 512) for key in ("field_selector", "picker_selector", "calendar_selector"))
                             and isinstance(widget["calendar_fingerprint"], str) and bool(_HEX_64.fullmatch(widget["calendar_fingerprint"])))
                    if "clear" in widget:
                        clear = widget["clear"]
                        _require(type(clear) is dict and set(clear) == {"selector", "label"}
                                 and _bounded_text(clear["selector"], 512) and clear["label"] in {"Clear", "Clear date"})
            elif label == "I currently work here":
                _require(field["kind"] == "checkbox" and type(field["value"]) is bool
                         and not ({"omission_policy_ref", "month_picker", "text_autocomplete"} & set(field)))
            else:
                _require(field["kind"] in {"text", "select"} and type(field["value"]) is str
                         and (not field["required"] or bool(field["value"].strip()))
                         and not ({"omission_policy_ref", "month_picker"} & set(field)))
                if "text_autocomplete" in field:
                    widget = field["text_autocomplete"]
                    _require(card["record_type"] == "experience" and label in {"Title", "Company", "Office location"}
                             and field["kind"] == "text" and type(widget) is dict
                             and set(widget) == {"type", "host_selector"} and widget["type"] == "spl-profile-text-v1"
                             and _bounded_text(widget["host_selector"], 512))
            labels.add(label); selectors.add(field["selector"]); keys.add(field["key"])
            values[label] = field["value"]
        _require(labels == set(expected) and (not values["From"] or not values["To"] or values["From"] <= values["To"]))
        save = editor["save"]
        _require(type(save) is dict and set(save) == {"selector", "label"}
                 and _bounded_text(save["selector"], 512) and save["selector"] not in selectors
                 and save["label"] == "Save")
    for selector, card in by_selector.items():
        _require(selector in seen or card["observed_text"] == assertions[selector]["value"])


def validate_reviewed_profile_corrections(step: dict, facts: dict, *, now: datetime) -> None:
    """Bind profile-only edits to verified facts; never broadens ordinary fields."""
    try:
        verified = _validate_facts(facts, now)
        _validate_profile_assertions(step.get('profile_assertions', []), verified, set())
        _validate_profile_corrections(step, verified)
    except Exception:
        raise PermissionError(_ERROR) from None


def _validate_upload(upload: object, keys: set, selectors: set) -> None:
    _require(isinstance(upload, dict) and _opaque(upload.get("key"), 128)
             and upload["key"] not in keys and _bounded_text(upload.get("selector"), 512)
             and upload["selector"] not in selectors and _bounded_text(upload.get("label"), 1024)
             and upload.get("kind") == "file" and type(upload.get("required")) is bool)
    keys.add(upload["key"])
    selectors.add(upload["selector"])


def _validate_button(button: object, labels: frozenset, selectors: set) -> None:
    _require(isinstance(button, dict) and _bounded_text(button.get("selector"), 512)
             and button["selector"] not in selectors and button.get("label") in labels)
    if "key" in button:
        _require(_opaque(button["key"], 128))
    selectors.add(button["selector"])


def _validate_runtime_sections(data: dict) -> None:
    native_entry_policy_enabled(data)
    urls = data.get("read_urls")
    _require(isinstance(urls, list) and len(urls) <= 1000
             and all(_exact_https_url(url) for url in urls) and len(set(urls)) == len(urls))
    confirmation = data.get("confirmation")
    _require(isinstance(confirmation, dict) and _bounded_text(confirmation.get("selector"), 512)
             and _bounded_text(confirmation.get("text"), 4096))


def _validate_entry_return_review(data: dict, now: datetime) -> None:
    if "entry_return_review" not in data:
        return
    review = data["entry_return_review"]
    _require(isinstance(review, dict) and set(review) == {
        "observation_id", "surface_fingerprint", "reviewed_at", "reviewer_ref", "return_url",
    })
    _require(isinstance(review.get("observation_id"), str)
             and bool(re.fullmatch(r"[a-f0-9]{48}", review["observation_id"]))
             and review.get("surface_fingerprint") == data["steps"][0]["fingerprint"]
             and review.get("return_url") == data["steps"][0]["url"]
             and review.get("reviewer_ref") == data["reviewer_ref"])
    _fresh(review.get("reviewed_at"), now, 120)
    stamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    _require(stamp.utcoffset().total_seconds() == 0)


def _validate_cv(cv: object) -> None:
    _require(isinstance(cv, dict) and isinstance(cv.get("sha256"), str)
             and bool(_HEX_64.fullmatch(cv["sha256"]))
             and type(cv.get("size_bytes")) is int and 0 < cv["size_bytes"] <= 10_000_000
             and cv.get("mime_type") == "application/pdf"
             and cv.get("user_approved") is True and cv.get("scope") == "one_cv_asset"
             and _opaque(cv.get("source_ref")))
    name = cv.get("approved_filename")
    _require(isinstance(name, str)
             and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}\.pdf", name, re.I)))


def load_reviewed_application_inputs(manifest_path: Path, facts_path: Path, *, manifest_sha256: str,
                                     facts_sha256: str, source_sha: str,
                                     now: datetime) -> tuple[dict, dict]:
    """Return the exact pinned manifest and facts after complete validation.

    Hashes and source SHA must be pinned outside both files. Caller obligations
    still include independent review, source authentication, real CV byte checks,
    target/endpoint binding and the independently scoped runtime authority.
    """
    try:
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() is not None and isinstance(source_sha, str)
                 and bool(_HEX_40.fullmatch(source_sha)))
        data = _private_pinned_json(manifest_path, manifest_sha256)
        facts = _private_pinned_json(facts_path, facts_sha256)
        _require(data.get("schema_version") == "EJS-SINGLE-APPLICATION-1"
                 and data.get("source_sha") == source_sha
                 and _opaque(data.get("reviewer_ref"), 128))
        _fresh(data.get("reviewed_at"), now, 86_400)
        by_field = _validate_facts(facts, now)
        _require(data.get("candidate_profile_version") == facts["candidate_profile_version"])
        _validate_runtime_sections(data)
        steps = data.get("steps")
        _require(isinstance(steps, list) and 0 < len(steps) <= 5)
        keys = set()
        upload_count = 0
        for index, step in enumerate(steps):
            _require(isinstance(step, dict) and isinstance(step.get("url"), str)
                     and len(step["url"]) <= 2048)
            url = urlsplit(step["url"])
            _require(url.scheme == "https" and url.hostname == "jobs.smartrecruiters.com"
                     and url.port in (None, 443) and not url.username and not url.password
                     and not url.fragment and bool(url.path))
            _require(isinstance(step.get("fingerprint"), str)
                     and bool(_HEX_64.fullmatch(step["fingerprint"])))
            fields = step.get("fields")
            _require(isinstance(fields, list) and 0 < len(fields) <= 100)
            selectors = set()
            for field in fields:
                _validate_field(field, by_field, keys, selectors)
                _require('city_autocomplete' not in field or index == 0)
                _require('phone_country_select' not in field or index == 0)
            _validate_profile_assertions(step.get("profile_assertions", []), by_field, selectors)
            _require("reviewed_profile_corrections" not in step or index == 0)
            _validate_profile_corrections(step, by_field)
            if "upload" in step:
                _validate_upload(step["upload"], keys, selectors)
                upload_count += 1
            if index < len(steps) - 1:
                _require("submit" not in step)
                _validate_button(step.get("next"), frozenset({"Next", "Continue"}), selectors)
            else:
                _require("next" not in step)
                _validate_button(step.get("submit"),
                                 frozenset({"Submit", "Submit application", "Send application"}), selectors)
        _require(upload_count == 1)
        _validate_entry_return_review(data, now)
        _validate_cv(data.get("cv"))
        return data, facts
    except Exception:
        raise PermissionError(_ERROR) from None


def load_reviewed_application(manifest_path: Path, facts_path: Path, *, manifest_sha256: str,
                              facts_sha256: str, source_sha: str, now: datetime) -> dict:
    """Compatibility entry point returning only the validated original manifest."""
    manifest, _ = load_reviewed_application_inputs(
        manifest_path, facts_path, manifest_sha256=manifest_sha256,
        facts_sha256=facts_sha256, source_sha=source_sha, now=now,
    )
    return manifest
