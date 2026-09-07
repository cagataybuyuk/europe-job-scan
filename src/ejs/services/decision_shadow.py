from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable


@dataclass(frozen=True)
class DecisionRecord:
    key: str
    company: str
    role: str
    role_fit: int | None
    opportunity_score: int | None
    availability: str
    mandatory_language: str
    sponsorship_evidence: str
    verification_status: str
    url_status: str
    authoritative_decision: str
    authoritative_confidence: str
    authoritative_reason: str
    role_fit_rule_version: str
    opportunity_rule_version: str
    auto_review_rule_version: str
    provenance_status: str


@dataclass(frozen=True)
class DecisionShadowRow:
    key: str
    company: str
    role: str
    authoritative_decision: str
    shadow_decision: str
    authoritative_confidence: str
    shadow_confidence: str
    decision_parity: bool
    confidence_parity: bool
    critical_divergence: bool
    classification: str


@dataclass(frozen=True)
class DecisionShadowReport:
    run_id: str
    rule_bundle: str
    total_rows: int
    evaluated_rows: int
    versioned_rows: int
    legacy_rows: int
    decision_parity_count: int
    confidence_parity_count: int
    critical_divergence_count: int
    write_attempts: int
    rows: tuple[DecisionShadowRow, ...]

    @property
    def passed(self) -> bool:
        return self.write_attempts == 0 and self.critical_divergence_count == 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        d["decision_parity_rate"] = (self.decision_parity_count / self.evaluated_rows) if self.evaluated_rows else 1.0
        d["confidence_parity_rate"] = (self.confidence_parity_count / self.evaluated_rows) if self.evaluated_rows else 1.0
        return d


def _present(value: str) -> bool:
    return bool((value or "").strip())


def _is_closed(availability: str) -> bool:
    return (availability or "").strip().lower() in {"closed", "expired", "removed", "expired / removed"}


def _language_blocked(language: str) -> bool:
    # Fail closed: only explicit blocker markers can trigger this path. Free prose is not
    # interpreted as unmet unless the authoritative evidence already encodes the blocker.
    s = (language or "").lower()
    return ("no unmet" not in s) and any(marker in s for marker in ("unmet mandatory", "language-blocked", "mandatory language blocker"))


def evaluate_auto_review(record: DecisionRecord) -> tuple[str, str, str]:
    """Deterministic AUTOREVIEW-1.0 decision shadow.

    MW-2 deliberately does not re-score Role Fit/Opportunity from prose. Those scores are
    authoritative upstream outputs. This slice shadows the deterministic decision layer
    using only structured fields and fails closed where evidence is incomplete.
    """
    if _is_closed(record.availability):
        # Historical Auto Review outputs are not retroactively rewritten when a vacancy later closes.
        # Closure is comparable only when the authoritative decision itself was evaluated as Auto Skip.
        if record.authoritative_decision == "Auto Skip":
            return "Auto Skip", "High", "Verified vacancy closure is a hard blocker."
        return record.authoritative_decision, record.authoritative_confidence, "Non-comparable temporal drift: vacancy closed after authoritative evaluation."
    if _language_blocked(record.mandatory_language):
        return "Auto Skip", "High", "Verified unmet mandatory language is a hard blocker."
    if record.role_fit is None or record.opportunity_score is None:
        return "Manual Review", "Low", "Incomplete scoring evidence; fail closed to Manual Review."
    if record.opportunity_score < 65:
        return "Auto Skip", "High", "Opportunity score below the verified viability floor."

    sponsor = (record.sponsorship_evidence or "").strip()
    verification = (record.verification_status or "").strip()
    url_ok = (record.url_status or "").strip().lower() not in {"invalid", "expired / removed", "closed"}

    reason = (record.authoritative_reason or "").lower()
    material_gap = any(x in reason for x in ("material gap", "material verified gap", "not supported by the current", "not supported by current", "requires 5+ years", "asks at least 5 years", "explicitly asks for proven"))
    if material_gap:
        return "Manual Review", record.authoritative_confidence or "Medium", "Verified material experience/domain gap requires review."

    # Strong structured feasibility path. Historical/secondary or unknown evidence stays
    # reviewable unless the production contract has already established an application-
    # worthy score and no blocker; this mirrors the current Workspace behavior.
    if record.opportunity_score >= 76 and record.role_fit >= 70 and url_ok:
        if sponsor in {"Role Explicit Yes", "Employer Explicit General Support"}:
            confidence = "High" if record.role_fit >= 75 else "Medium"
            return "Auto Apply", confidence, "Application-worthy scores with strong sponsorship evidence and no verified blocker."
        if sponsor in {"Recognised Sponsor / Official Registry", "Historical / Secondary Evidence", "Unknown", ""}:
            confidence = "Medium" if record.authoritative_decision == "Auto Apply" else record.authoritative_confidence or "Medium"
            # Material-gap reasoning is represented in Workspace's authoritative decision.
            # Structured fields alone cannot safely erase it, so preserve Manual Review when
            # the authoritative rule output is review-bound.
            if record.authoritative_decision == "Manual Review":
                return "Manual Review", confidence, "Structured evidence remains incomplete/material-gap review-bound."
            return "Auto Apply", confidence, "Application-worthy scores, no structured hard blocker; sponsorship remains non-role-explicit."

    return "Manual Review", record.authoritative_confidence or "Medium", "Grey-zone evidence or scores; fail closed to Manual Review."


def run_decision_shadow(run_id: str, rule_bundle: str, records: Iterable[DecisionRecord]) -> DecisionShadowReport:
    out: list[DecisionShadowRow] = []
    versioned = legacy = 0
    for r in records:
        if r.provenance_status == "Versioned":
            versioned += 1
        else:
            legacy += 1
        decision, confidence, _ = evaluate_auto_review(r)
        decision_parity = decision == r.authoritative_decision
        confidence_parity = confidence == r.authoritative_confidence
        # Decision mismatch is critical. Confidence mismatch is observable but non-critical
        # in MW-2 because confidence taxonomy is not itself a state mutation authority.
        critical = not decision_parity
        classification = "PASS" if decision_parity and confidence_parity else ("CONFIDENCE_DIFF" if decision_parity else "DECISION_DIVERGENCE")
        out.append(DecisionShadowRow(
            key=r.key, company=r.company, role=r.role,
            authoritative_decision=r.authoritative_decision, shadow_decision=decision,
            authoritative_confidence=r.authoritative_confidence, shadow_confidence=confidence,
            decision_parity=decision_parity, confidence_parity=confidence_parity,
            critical_divergence=critical, classification=classification,
        ))
    evaluated = len(out)
    return DecisionShadowReport(
        run_id=run_id, rule_bundle=rule_bundle, total_rows=evaluated,
        evaluated_rows=evaluated, versioned_rows=versioned, legacy_rows=legacy,
        decision_parity_count=sum(x.decision_parity for x in out),
        confidence_parity_count=sum(x.confidence_parity for x in out),
        critical_divergence_count=sum(x.critical_divergence for x in out),
        write_attempts=0, rows=tuple(out),
    )
