"""FUNCTION-04: evidence-based candidate-listing suitability. Pure, offline, deterministic.

Reuses the approved engine unchanged: weights, thresholds and hard blockers come from ``Criteria``;
scoring is ``compute_opportunity``; the label is ``autoreview.decide``. This module only turns
evidence into the tri-state facts and 0..100 ratings those functions already accept.

Two evidence levels are kept apart on every row:

* ``listing_metadata`` - title, location and the feed's country provenance. Priority only.
* ``vacancy_details``  - identity-bound posting text. The only level that can yield a verified label.

Unknown stays Unknown: no component rating, blocker signal or candidate fact is ever guessed.
A label is not authority; every output states ``execution/submit authority: false``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from fractions import Fraction
from hashlib import sha256
from typing import Any, Mapping

from ejs.services.autoreview import BLOCKER_FIELDS, Decision, ListingFacts, decide
from ejs.services.criteria_loader import SCORE_COMPONENTS, Criteria
from ejs.services.dedupe import _fold
from ejs.services.opportunity_score import compute_opportunity
from ejs.services.shadow_run import SCHEMA as DISCOVERY_SCHEMA
from ejs.services.suitability_evidence import (DetailEvidence, VacancyDetail, extract_detail_evidence)

SCHEMA = "EJS-SUITABILITY-1"
POLICY_VERSION = "SUITABILITY-1.0-PROVISIONAL"
IDENTITY_FIELDS = ("ats_kind", "region", "tenant", "posting_id")
ROW_FIELDS = ("company", "role", "location", "url", "ats_kind", "source", "tenant", "region")
AUTHORITY = {"execute": False, "submit": False, "form_access": False, "network": False,
             "label_is_not_authority": True}

# ISO code -> accepted spellings. Unknown country strings are compared casefolded as-is.
_COUNTRY_NAMES = {
    "NL": ("netherlands", "the netherlands", "nederland", "holland"), "IE": ("ireland", "eire"),
    "DE": ("germany", "deutschland"), "BE": ("belgium", "belgie", "belgique"), "SE": ("sweden", "sverige"),
    "DK": ("denmark", "danmark"), "ES": ("spain", "espana"), "PT": ("portugal",), "IT": ("italy", "italia"),
    "FR": ("france",), "AT": ("austria", "osterreich"), "PL": ("poland", "polska"), "CH": ("switzerland",),
    "FI": ("finland",), "NO": ("norway",), "LU": ("luxembourg",),
}
_COUNTRY_LOOKUP = {name: code for code, names in _COUNTRY_NAMES.items() for name in (code.casefold(), *names)}
_PLACEHOLDER = re.compile(r"^<[^<>]+>$")
_TOKEN = re.compile(r"[a-z0-9+#]+")


class SuitabilityError(ValueError):
    """Fail-closed input problem (profile, report shape). Nothing is scored."""


@dataclass(frozen=True)
class SuitabilityPolicy:
    """Rating percentages per evidence outcome. PROVISIONAL: not owner-approved (see owner decisions)."""
    version: str = POLICY_VERSION
    role_primary: int = 100
    role_secondary: int = 70
    role_no_title_match: int = 0
    seniority_include: int = 100
    seniority_exclude: int = 0
    compensation_meets: int = 100
    compensation_below: int = 0
    sponsorship_offered: int = 100
    sponsorship_refused: int = 0
    english_met: int = 100
    english_unmet: int = 0
    # Explicit title words that make a role "too junior". Owner may override via seniority.include.
    junior_markers: tuple[str, ...] = ("junior", "intern", "internship", "trainee", "graduate",
                                       "apprentice", "werkstudent", "stagiair")


@dataclass(frozen=True)
class Profile:
    primary_roles: tuple[str, ...]
    secondary_roles: tuple[str, ...]
    seniority_include: tuple[str, ...]
    seniority_exclude: tuple[str, ...]
    tier_1: frozenset[str]
    other_countries: frozenset[str]
    languages_met: frozenset[str]
    target_net_monthly_eur: float | None
    work_authorization: bool | None
    gaps: tuple[str, ...]


def canon_country(value: str) -> str:
    key = _fold(value).strip()
    return _COUNTRY_LOOKUP.get(key, key)


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
        raise SuitabilityError(f"PROFILE_INVALID:{name}")
    if any(_PLACEHOLDER.match(i.strip()) for i in items):
        raise SuitabilityError(f"PROFILE_PLACEHOLDER:{name}")
    return tuple(i.strip() for i in items)


def profile_from_criteria(criteria: Criteria) -> Profile:
    """Read the owner's preference sections from already-validated criteria. Missing section = a reported gap."""
    raw = criteria.raw
    sec = lambda n: raw.get(n) if isinstance(raw.get(n), dict) else {}  # noqa: E731
    primary = _strings(sec("role_tracks").get("primary"), "role_tracks.primary")
    secondary = _strings(sec("role_tracks").get("secondary"), "role_tracks.secondary")
    include = _strings(sec("seniority").get("include"), "seniority.include")
    exclude = _strings(sec("seniority").get("exclude"), "seniority.exclude")
    tier_1 = frozenset(canon_country(c) for c in _strings(sec("countries").get("tier_1"), "countries.tier_1"))
    others = frozenset(canon_country(c) for c in _strings(sec("countries").get("others"), "countries.others"))
    langs = frozenset(_fold(l).strip() for l in _strings(raw.get("languages_required_met"), "languages_required_met"))
    target = sec("compensation").get("target_net_monthly_eur_min")
    if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float)) or target <= 0):
        raise SuitabilityError("PROFILE_INVALID:compensation.target_net_monthly_eur_min")
    auth = sec("work_rights").get("eu_work_authorization")
    if auth is not None and not isinstance(auth, bool):
        raise SuitabilityError("PROFILE_INVALID:work_rights.eu_work_authorization")
    gaps = tuple(g for g, missing in (
        ("role_tracks", not primary and not secondary), ("seniority", not include and not exclude),
        ("countries", not tier_1 and not others), ("languages_required_met", not langs),
        ("compensation_target", target is None), ("work_authorization", auth is None)) if missing)
    return Profile(primary, secondary, include, exclude, tier_1, others, langs,
                   float(target) if target is not None else None, auth, gaps)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_fold(text)))


def _matches(title: frozenset[str], phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        toks = _tokens(phrase)
        if toks and toks <= title:
            return phrase
    return None


def _assess_listing(row: Mapping[str, Any], profile: Profile, policy: SuitabilityPolicy
                    ) -> tuple[dict[str, int | None], dict[str, Any], dict[str, bool | None]]:
    """Ratings, dimensions and blocker signals from title/location/country provenance only."""
    title = _tokens(row["role"])
    ratings: dict[str, int | None] = {c: None for c in SCORE_COMPONENTS}
    dims: dict[str, Any] = {}
    signals: dict[str, bool | None] = {}

    if not (profile.primary_roles or profile.secondary_roles):
        dims["role_track"] = {"status": "unknown", "need": "role_tracks_not_configured"}
    elif (hit := _matches(title, profile.primary_roles)):
        ratings["role_fit"] = policy.role_primary
        dims["role_track"] = {"status": "primary_match", "matched": hit}
    elif (hit := _matches(title, profile.secondary_roles)):
        ratings["role_fit"] = policy.role_secondary
        dims["role_track"] = {"status": "secondary_match", "matched": hit}
    else:
        ratings["role_fit"] = policy.role_no_title_match
        dims["role_track"] = {"status": "no_title_match", "note": "title only; description not read"}

    included = _matches(title, profile.seniority_include)
    excluded = _matches(title, profile.seniority_exclude)
    junior = [m for m in policy.junior_markers if m in title and m not in {_fold(i) for i in profile.seniority_include}]
    if included and not excluded:
        ratings["seniority_title"] = policy.seniority_include
        dims["seniority"] = {"status": "include_match", "matched": included}
    elif excluded and not included:
        ratings["seniority_title"] = policy.seniority_exclude
        dims["seniority"] = {"status": "exclude_match", "matched": excluded}
    elif included and excluded:
        dims["seniority"] = {"status": "conflicting_title_terms", "matched": [included, excluded]}
    else:
        dims["seniority"] = {"status": "not_stated_in_title"}
    if junior:
        signals["junior_or_technical_only"] = True
        dims["seniority"]["junior_marker"] = junior[0]

    de = row.get("discovery_evidence")
    codes = [canon_country(c) for c in (de.get("countries") if isinstance(de, dict) else None) or ()
             if isinstance(c, str) and c.strip()]
    if not codes:
        dims["geography"] = {"status": "unknown", "need": "country_provenance"}
    elif not (profile.tier_1 or profile.other_countries):
        dims["geography"] = {"status": "unknown", "countries": sorted(set(codes)), "need": "countries_not_configured"}
    else:
        tiers = {"tier_1" if c in profile.tier_1 else "other" if c in profile.other_countries else "not_configured"
                 for c in codes}
        status = ("not_configured" if "not_configured" in tiers else "other" if "other" in tiers else "tier_1")
        dims["geography"] = {"status": status, "countries": sorted(set(codes))}
    return ratings, dims, signals


def _assess_details(ev: DetailEvidence, profile: Profile, policy: SuitabilityPolicy,
                    ratings: dict[str, int | None], signals: dict[str, bool | None]) -> dict[str, Any]:
    dims: dict[str, Any] = {}
    q = ev.quotes
    if ev.explicit_no_sponsorship:
        ratings["sponsorship"] = policy.sponsorship_refused
        signals["explicit_no_sponsorship"] = True
        dims["sponsorship"] = {"status": "explicitly_refused", "quote": q.get("sponsorship_no")}
    elif ev.explicit_sponsorship_offered:
        ratings["sponsorship"] = policy.sponsorship_offered
        dims["sponsorship"] = {"status": "explicitly_offered", "quote": q.get("sponsorship_yes")}
    else:
        dims["sponsorship"] = {"status": "hedged" if "sponsorship" in ev.hedged else "not_stated",
                               "need": "explicit_employer_sponsorship_statement"}

    if ev.requires_unlimited_work_right:
        signals["requires_unlimited_work_right"] = profile.work_authorization is not True
        dims["work_authorization"] = {"status": "unrestricted_right_required", "quote": q.get("unlimited_work_right"),
                                      "candidate_authorization": profile.work_authorization}
    elif ev.generic_right_to_work_required:
        dims["work_authorization"] = {"status": "right_to_work_required_scope_unclear",
                                      "quote": q.get("generic_right_to_work"),
                                      "candidate_authorization": profile.work_authorization,
                                      "need": "owner_review_of_requirement_vs_candidate_facts"}
    else:
        dims["work_authorization"] = {"status": "not_stated"}

    langs = ev.mandatory_languages
    if langs:
        unmet = [l for l in langs if l not in profile.languages_met]
        if not profile.languages_met:
            dims["language"] = {"status": "mandatory_stated_candidate_languages_unknown", "mandatory": list(langs),
                                "need": "candidate_languages_in_criteria"}
        else:
            signals["mandatory_language_unmet"] = bool(unmet)
            dims["language"] = {"status": "mandatory_unmet" if unmet else "mandatory_met",
                                "mandatory": list(langs), "unmet": unmet}
    else:
        dims["language"] = {"status": "not_stated"}
    english_stated = "english" in langs or ev.english_working_language
    if english_stated and profile.languages_met:
        ratings["english_intl"] = policy.english_met if "english" in profile.languages_met else policy.english_unmet

    comp, target = ev.compensation, profile.target_net_monthly_eur
    if comp is None:
        dims["compensation"] = {"status": "not_stated", "need": "stated_eur_compensation_with_period"}
    elif target is None:
        dims["compensation"] = {"status": "stated_target_not_configured", "monthly_max": comp["monthly_max"],
                                "need": "compensation_target_in_criteria"}
    elif comp["monthly_max"] < target:
        ratings["compensation_living"] = policy.compensation_below
        dims["compensation"] = {"status": "below_target_confirmed", "monthly_max": comp["monthly_max"],
                                "basis": comp["basis"], "target_net_monthly_eur": target,
                                "note": "net cannot exceed the stated amount"}
    elif comp["basis"] == "net" and comp["monthly_min"] >= target:
        ratings["compensation_living"] = policy.compensation_meets
        dims["compensation"] = {"status": "meets_target_net_stated", "monthly_min": comp["monthly_min"],
                                "target_net_monthly_eur": target}
    else:
        dims["compensation"] = {"status": "net_comparison_undetermined", "basis": comp["basis"],
                                "monthly_min": comp["monthly_min"], "monthly_max": comp["monthly_max"],
                                "need": "net_monthly_figure_or_owner_tax_model"}
    return dims


# Hits here never approve; they only downgrade an apply label to needs_user.
_DOWNGRADE = {"geography_not_in_configured_countries", "geography_unknown", "identity_unknown",
              "work_right_requirement_needs_owner_review", "duplicate_similarity_group"}


def _order_key(r: dict[str, Any]) -> tuple:
    s = r["suitability"]
    stage_rank = {"verified_apply_candidate": 0, "verified_needs_review": 1, "preliminary_high": 2,
                  "preliminary_medium": 3, "preliminary_low": 4, "verified_below_threshold": 5,
                  "verified_blocked": 6, "preliminary_blocked": 6}[s["classification"]]
    tier = {"tier_1": 0, "other": 1}.get(s["dimensions"]["geography"]["status"], 2)
    return (stage_rank, -Fraction(s["score_exact"]), tier, r["ats_kind"], r["region"], r["tenant"],
            r["posting_id"] or "", r["url"] or "", r["company"], r["role"], r["location"])


def _preliminary_tier(dims: Mapping[str, Any]) -> str:
    """Listing-metadata priority from known dimensions. Threshold bands are NOT used here: with most
    components Unknown, the approved bands cannot be reached and would hide every difference."""
    level = {"primary_match": 0, "secondary_match": 1, "unknown": 1, "no_title_match": 2}[dims["role_track"]["status"]]
    if dims["seniority"]["status"] in ("exclude_match", "conflicting_title_terms"):
        level += 1
    geo = dims["geography"]["status"]
    if geo == "not_configured":
        level = 2
    elif geo == "unknown":
        level += 1
    return ("preliminary_high", "preliminary_medium", "preliminary_low")[min(level, 2)]


def _classify(decision: Decision, level: str, flags: list[str], blocked_preliminary: bool,
              dims: Mapping[str, Any]) -> str:
    if level == "vacancy_details":
        if decision.label == "auto_skip":
            return "verified_blocked" if decision.reason_code.startswith("blocker:") else "verified_below_threshold"
        return "verified_apply_candidate" if decision.label == "auto_apply" and not flags else "verified_needs_review"
    return "preliminary_blocked" if blocked_preliminary else _preliminary_tier(dims)


def assess_row(row: Mapping[str, Any], criteria: Criteria, profile: Profile, policy: SuitabilityPolicy, *,
               detail: VacancyDetail | None = None, reviewer: Mapping[str, Any] | None = None,
               similarity_duplicate: bool = False) -> dict[str, Any]:
    ratings, dims, signals = _assess_listing(row, profile, policy)
    level, evidence = "listing_metadata", None
    if detail is not None:
        evidence = extract_detail_evidence(detail)
        level = "vacancy_details"
        dims.update(_assess_details(evidence, profile, policy, ratings, signals))
    if reviewer:
        for comp, spec in reviewer.items():
            if (comp not in ("scope", "career_upside") or not isinstance(spec, dict) or not spec.get("ref")
                    or isinstance(spec.get("rating"), bool) or not isinstance(spec.get("rating"), int)
                    or not 0 <= spec["rating"] <= 100):
                raise SuitabilityError(f"REVIEWER_RATING_INVALID:{comp}")
            ratings[comp] = spec["rating"]
            dims[comp] = {"status": "reviewer_rated", "ref": spec["ref"]}

    facts = ListingFacts(
        listing_id=row["url"] or f"identity:{row['ats_kind']}:{row['tenant']}:{row.get('posting_id')}",
        ratings=ratings, vacancy_open=evidence.vacancy_open if evidence else None,
        explicit_no_sponsorship=signals.get("explicit_no_sponsorship"),
        requires_unlimited_work_right=signals.get("requires_unlimited_work_right"),
        mandatory_language_unmet=signals.get("mandatory_language_unmet"),
        junior_or_technical_only=signals.get("junior_or_technical_only"),
        contradictions=evidence.contradictions if evidence else ())
    decision = decide(facts, criteria)
    result = compute_opportunity(ratings, criteria)

    flags: list[str] = []
    geo = dims["geography"]["status"]
    if geo == "not_configured":
        flags.append("geography_not_in_configured_countries")
    elif geo == "unknown":
        flags.append("geography_unknown")
    if not row.get("posting_id"):
        flags.append("identity_unknown")
    if dims.get("work_authorization", {}).get("status") == "right_to_work_required_scope_unclear" \
            and profile.work_authorization is not True:
        flags.append("work_right_requirement_needs_owner_review")
    if similarity_duplicate:
        flags.append("duplicate_similarity_group")

    label, reason = decision.label, decision.reason_code
    if label == "auto_apply" and any(f in _DOWNGRADE for f in flags):
        label, reason = "needs_user", flags[0]
    blockers = [f"blocker:{c}" for c in criteria.hard_blockers if c != "closed-vacancy"
                and signals.get(BLOCKER_FIELDS[c]) is True]
    final = decision if label == decision.label else replace(decision, label=label, reason_code=reason)
    classification = _classify(final, level, flags, bool(blockers), dims)
    unknown = list(result.unknown_components)
    need = sorted({dims[k]["need"] for k in dims if isinstance(dims[k], dict) and "need" in dims[k]}
                  | ({"vacancy_detail_not_observed"} if level == "listing_metadata" else set())
                  | {f"{c}_evidence_missing" for c in unknown})
    incompat = sorted(
        [f"confirmed:{b}" for b in blockers]
        + (["confirmed:vacancy_closed"] if evidence and evidence.vacancy_open is False else [])
        + (["confirmed:compensation_below_target"] if dims.get("compensation", {}).get("status") ==
           "below_target_confirmed" else [])
        + (["title:seniority_excluded"] if dims["seniority"]["status"] == "exclude_match" else [])
        + (["title:no_role_track_match"] if dims["role_track"]["status"] == "no_title_match" else []))
    return {
        "suitability": {
            "evidence_level": level, "classification": classification,
            "decision_label": label, "reason_code": reason,
            "reason_codes": sorted({*decision.reason_codes, *flags} if label != "auto_apply" else decision.reason_codes),
            "confidence": decision.confidence if label == decision.label else "low",
            "score": result.score, "score_exact": f"{result.score_exact.numerator}/{result.score_exact.denominator}",
            "band": result.band, "band_basis": ("verified_evidence" if level == "vacancy_details" else
                                                "listing_metadata_unknown_credit_applied"),
            "ratings": dict(ratings), "unknown_components": unknown,
            "dimensions": dims, "confirmed_incompatibilities": incompat, "missing_evidence": need,
            "review_flags": flags, "detail_sha256": detail.detail_sha256 if detail else None,
            "detail_quotes": dict(evidence.quotes) if evidence else {},
            "rule_version": criteria.rule_version, "policy_version": policy.version},
        "_decision": decision}


def _validate_input(report: Any) -> list[dict[str, Any]]:
    if not isinstance(report, dict) or report.get("schema") != DISCOVERY_SCHEMA:
        raise SuitabilityError("SUITABILITY_INPUT_SCHEMA")
    rows = report.get("vacancies")
    if not isinstance(rows, list) or report.get("listings_kept") != len(rows):
        raise SuitabilityError("SUITABILITY_INPUT_COUNT")
    for row in rows:
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in ROW_FIELDS)
                or row.get("posting_id") is not None and not isinstance(row["posting_id"], str)):
            raise SuitabilityError("SUITABILITY_INPUT_ROW")
    return rows


def canonical_sha256(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def evaluate_report(report: dict[str, Any], criteria: Criteria, *,
                    details: Mapping[tuple[str, str], VacancyDetail] | None = None,
                    reviewer_ratings: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
                    criteria_approval_ref: str | None = None, criteria_sha256: str | None = None,
                    policy: SuitabilityPolicy | None = None) -> dict[str, Any]:
    """Prioritize a Function 02 projection. Deterministic: same inputs give byte-identical JSON."""
    policy = policy or SuitabilityPolicy()
    rows = _validate_input(report)
    profile = profile_from_criteria(criteria)
    details = details or {}
    reviewer_ratings = reviewer_ratings or {}

    unique: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    duplicate_rows = 0
    for row in rows:
        identity = tuple(row[k] for k in IDENTITY_FIELDS[:3]) + (row.get("posting_id"),)
        if all(identity):
            if identity in seen:
                duplicate_rows += 1
                continue
            seen.add(identity)
        unique.append(row)
    groups = Counter(r.get("similarity_key") for r in unique if r.get("similarity_key"))

    out: list[dict[str, Any]] = []
    rejected: Counter = Counter()
    used: set[tuple[str, str]] = set()
    for row in unique:
        key = (row["tenant"], row.get("posting_id") or "")
        detail = details.get(key)
        if detail is not None and (detail.tenant, detail.posting_id) != key:
            rejected["detail_identity_mismatch"] += 1
            detail = None
        if detail is not None:
            used.add(key)
        assessed = assess_row(row, criteria, profile, policy, detail=detail, reviewer=reviewer_ratings.get(key),
                              similarity_duplicate=bool(row.get("similarity_key")) and groups[row["similarity_key"]] > 1)
        assessed.pop("_decision")
        entry = {k: row[k] for k in ROW_FIELDS} | {"posting_id": row.get("posting_id"),
                                                  "identity_status": "known" if row.get("posting_id") else "unknown",
                                                  "similarity_key": row.get("similarity_key"),
                                                  "discovery_evidence": row.get("discovery_evidence")}
        entry.update(assessed)
        out.append(entry)
    out.sort(key=_order_key)
    for i, entry in enumerate(out, 1):
        entry["priority_rank"] = i
    unused_details = sorted(f"{t}/{p}" for t, p in set(details) - used)

    classes = Counter(r["suitability"]["classification"] for r in out)
    labels = Counter(r["suitability"]["decision_label"] for r in out)
    bands = Counter(r["suitability"]["band"] for r in out)
    reasons = Counter(c for r in out for c in r["suitability"]["reason_codes"])
    missing = Counter(c for r in out for c in r["suitability"]["missing_evidence"])
    approved = bool(criteria_approval_ref)
    return {
        "schema": SCHEMA, "source_schema": DISCOVERY_SCHEMA, "mode": "suitability_no_submit",
        "executable": False, "submit_authorized": False, "authority": dict(AUTHORITY),
        "source_projection": report.get("projection"), "source_report_sha256": canonical_sha256(report),
        "rule_version": criteria.rule_version, "policy_version": policy.version,
        "criteria": {"schema": criteria.schema, "sha256": criteria_sha256, "approved_by_owner": approved,
                     "approval_ref": criteria_approval_ref if approved else None, "profile_gaps": list(profile.gaps)},
        "handoff_ready": approved and not profile.gaps,
        "input_rows": len(rows), "duplicate_rows": duplicate_rows, "listings_kept": len(out),
        "details_supplied": len(details), "details_applied": len(used),
        "details_rejected": dict(rejected), "details_unmatched": unused_details,
        "similarity_groups_with_multiple_postings": sum(1 for n in groups.values() if n > 1),
        "evidence_level_distribution": dict(sorted(Counter(r["suitability"]["evidence_level"] for r in out).items())),
        "classification_distribution": dict(sorted(classes.items())),
        "decision_label_distribution": dict(sorted(labels.items())),
        "band_distribution": dict(sorted(bands.items())),
        "reason_distribution": dict(sorted(reasons.items())),
        "missing_evidence_distribution": dict(sorted(missing.items())),
        "vacancies": out,
        "not_verified": [
            "Vacancy details are read only where supplied; listings without them are preliminary and carry no verified label.",
            "Rating percentages are provisional policy values awaiting owner approval.",
            "scope and career_upside stay Unknown without a reviewer rating that cites a reference.",
            "No employer, form or network access occurred; no execution or submission authority is granted."]}


def project_for_bridge(report: dict[str, Any]) -> dict[str, Any]:
    """Priority-ordered view the unchanged W2 bridge accepts. Confirmed-blocked and below-threshold rows are dropped."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or report.get("executable") is not False:
        raise SuitabilityError("SUITABILITY_PROJECTION_REFUSED")
    drop = {"verified_blocked", "preliminary_blocked", "verified_below_threshold"}
    kept, excluded = [], Counter()
    for r in report["vacancies"]:
        s = r["suitability"]
        if s["classification"] in drop:
            excluded[s["classification"]] += 1
            continue
        row = {k: r[k] for k in (*ROW_FIELDS, "posting_id", "identity_status", "similarity_key", "discovery_evidence")}
        row["criteria_evaluation"] = {"label": s["decision_label"], "reason": s["reason_code"],
                                      "rule_version": s["rule_version"], "score": s["score"],
                                      "unknown_components": s["unknown_components"]}
        kept.append(row)
    return {"schema": DISCOVERY_SCHEMA, "mode": "shadow_no_submit", "source_schema": SCHEMA,
            "projection": "suitability_priority_order", "listings_kept": len(kept),
            "excluded_rows": {"count": sum(excluded.values()), "reasons": dict(sorted(excluded.items()))},
            "vacancies": kept}
