"""Synthetic fixtures for EJS Faz 1a tests. No personal data; all values are made up."""
from __future__ import annotations

import copy

from ejs.services.criteria_loader import Criteria, parse_criteria_text

CRITERIA_YAML = """\
# synthetic test criteria (not the user's values)
schema: EJS-CRITERIA-1.0
source: "synthetic fixture"
countries:
  tier_1: [Atlantis]
  others: []
role_tracks:
  primary: widget-engineering
  secondary: []
exclusions: []
score_weights:
  sponsorship: 30
  compensation_living: 25
  role_fit: 20
  scope: 10
  career_upside: 7
  english_intl: 5
  seniority_title: 3
thresholds: {priority_apply: 85, apply: 75, consider: 65}
scoring:
  unknown_credit_percent: 50
auto_apply:
  opportunity_min: 65
  hard_blockers:
    - explicit-no-sponsorship
    - unlimited-work-right-required
    - mandatory-language-unmet
    - closed-vacancy
    - junior-or-technical-only
  calibration_review_after_applications: 30
  previous_sheet_rule: {opportunity_min: 75, role_fit_min: 70, if_sponsorship_unknown: {opportunity_min: 80, role_fit_min: 78}}
health:
  feeder_dead_after_hours: 36
  feeder_dead_after_zero_runs: 3
  notify_feeder_dead_immediately: true
  daily_summary: true
export:
  csv_dir: "owner-only, outside repo"
  enabled: true
dedupe_key: "company + normalized role + location"
rule_version: "OPPORTUNITY-1.0+K2-test"
"""


def criteria() -> Criteria:
    return parse_criteria_text(CRITERIA_YAML)


def uniform(r: int | None) -> dict[str, int | None]:
    return {k: r for k in ("sponsorship", "compensation_living", "role_fit", "scope",
                           "career_upside", "english_intl", "seniority_title")}


def listing_rows() -> list[dict[str, str]]:
    """12 synthetic listings; indexes 3,6,9 duplicate earlier ones (case/accents/legal suffix/gender tag)."""
    base = [
        ("Acme Widgets GmbH", "Senior Widget Engineer (m/f/d)", "Berlin, Atlantis"),
        ("Borealis Labs", "Platform Engineer", "Oslo"),
        ("Cobalt AG", "Data Engineer", "Zürich"),
        ("ACME Widgets", "senior widget engineer (w/m/d)", "Berlin"),          # dup of 0
        ("Delta Works BV", "Backend Developer", "Utrecht"),
        ("Evergreen Ltd", "Site Reliability Engineer", "Remote"),
        ("Borealis Labs", "Platform  Engineer", "OSLO, Norland"),              # dup of 1
        ("Fjord Systems", "Frontend Developer", "Bergen"),
        ("Granite SA", "ML Engineer", "Lyon"),
        ("Cobalt", "Data Engineer", "Zurich"),                                  # dup of 2
        ("Helios Oy", "QA Engineer", "Tampere"),
        ("Ionic Tech", "Security Engineer", ""),                                # unkeyed
    ]
    return [{"id": f"L{i:02d}", "company": c, "role": r, "location": loc} for i, (c, r, loc) in enumerate(base)]


def mutated(doc_yaml: str, old: str, new: str) -> str:
    assert old in doc_yaml
    return doc_yaml.replace(old, new)
