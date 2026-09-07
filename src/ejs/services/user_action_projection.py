from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping

from ejs.domain.user_action import UserActionRow


_REVIEW_RE = re.compile(r"review|approve|salary|experience|start date", re.I)
_ORDINARY_RE = re.compile(r"confirm|address|licen|onsite|employment", re.I)
_LEGAL_RE = re.compile(r"privacy|consent|gender|demographic", re.I)
_INTERVIEW_STAGES = {"Screening", "Interview 1", "Interview 2", "Final Interview"}


def project_user_actions(
    *,
    form_fill_rows: Iterable[Mapping[str, object]],
    application_rows: Iterable[Mapping[str, object]],
    follow_up_rows: Iterable[Mapping[str, object]],
    interview_rows: Iterable[Mapping[str, object]],
) -> list[UserActionRow]:
    rows: list[UserActionRow] = []
    rows.extend(_from_form_fill(form_fill_rows))
    rows.extend(_from_applications(application_rows))
    rows.extend(_from_follow_up(follow_up_rows))
    rows.extend(_from_interview_prep(interview_rows))
    rows.sort(key=_sort_key)
    return rows


def _from_form_fill(items: Iterable[Mapping[str, object]]) -> list[UserActionRow]:
    out: list[UserActionRow] = []
    for x in items:
        status = _s(x.get("application_status"))
        readiness = _s(x.get("fill_pack_status"))
        if status != "To Apply" or not readiness.startswith("Ready"):
            continue
        immediate = readiness == "Ready for User Submit"
        action = _s(x.get("remaining_user_actions"))
        out.append(UserActionRow(
            priority="P1" if immediate else "P2",
            action_category="Submit" if immediate else "Input + Submit",
            company=_s(x.get("company")),
            role=_s(x.get("role")),
            pipeline_status=status,
            readiness_decision=readiness,
            required_user_action=action,
            due_date=None,
            ordinary_facts=_i(x.get("user_required_factual")),
            review_approval=1 if _REVIEW_RE.search(action) else 0,
            legal_consent=_i(x.get("user_only_legal")),
            technical_upload=_i(x.get("technical_captcha")) + 1,
            assigned_cv=_none(x.get("assigned_cv")),
            application_url=_none(x.get("application_url")),
            can_submit_now="Yes — user actions only" if immediate else "After input",
            batch_group="Submit Now" if immediate else "Answer Once + Submit",
            last_updated=_none(x.get("last_mapped")) or _none(x.get("queue_date")),
            notes=_none(x.get("notes")),
        ))
    return out


def _from_applications(items: Iterable[Mapping[str, object]]) -> list[UserActionRow]:
    out: list[UserActionRow] = []
    for x in items:
        if _s(x.get("status")) != "To Review":
            continue
        fields = _s(x.get("user_required_application_fields"))
        due = _none(x.get("next_action_date"))
        if not due and _none(x.get("last_update")):
            due = _workday(_s(x.get("last_update")), 2)
        out.append(UserActionRow(
            priority="P3",
            action_category="Decision",
            company=_s(x.get("company")),
            role=_s(x.get("role")),
            pipeline_status="To Review",
            readiness_decision="Manual Review",
            required_user_action=_s(x.get("next_action")),
            due_date=due,
            ordinary_facts=1 if _ORDINARY_RE.search(fields) else 0,
            review_approval=1,
            legal_consent=1 if _LEGAL_RE.search(fields) else 0,
            technical_upload=0,
            assigned_cv=_none(x.get("cv_persona")),
            application_url=_none(x.get("source_url")),
            can_submit_now="After decision",
            batch_group="Manual Review",
            last_updated=_none(x.get("last_update")),
            notes=_none(x.get("auto_review_reason")),
        ))
    return out


def _from_follow_up(items: Iterable[Mapping[str, object]]) -> list[UserActionRow]:
    out: list[UserActionRow] = []
    for x in items:
        state = _s(x.get("queue_state"))
        if state not in {"Due", "Data Quality - Missing Applied Date"}:
            continue
        due = state == "Due"
        out.append(UserActionRow(
            priority="P2",
            action_category="Follow-up" if due else "Data Quality",
            company=_s(x.get("company")),
            role=_s(x.get("role")),
            pipeline_status=_s(x.get("pipeline_status")),
            readiness_decision="Follow-up Due" if due else "User Data Required",
            required_user_action=_s(x.get("recommended_action")),
            due_date=_none(x.get("follow_up_due")),
            ordinary_facts=0 if due else 1,
            review_approval=1 if due else 0,
            legal_consent=0,
            technical_upload=0,
            assigned_cv=None,
            application_url=_none(x.get("recruiter_linkedin")),
            can_submit_now="No",
            batch_group="Follow-up" if due else "Data Quality",
            last_updated=_none(x.get("last_update")),
            notes=_none(x.get("notes")),
        ))
    return out


def _from_interview_prep(items: Iterable[Mapping[str, object]]) -> list[UserActionRow]:
    out: list[UserActionRow] = []
    for x in items:
        if not _s(x.get("prep_id")):
            continue
        stage = _s(x.get("pipeline_stage"))
        readiness = _s(x.get("readiness_tier"))
        if stage not in _INTERVIEW_STAGES or readiness == "Ready":
            continue
        out.append(UserActionRow(
            priority=_s(x.get("priority")),
            action_category="Interview Prep",
            company=_s(x.get("company")),
            role=_s(x.get("role")),
            pipeline_status=stage,
            readiness_decision=readiness,
            required_user_action=_s(x.get("required_user_action")),
            due_date=_none(x.get("interview_date")),
            ordinary_facts=0,
            review_approval=1,
            legal_consent=0,
            technical_upload=0,
            assigned_cv=None,
            application_url=None,
            can_submit_now="No",
            batch_group="Interview Prep",
            last_updated=_none(x.get("last_updated")),
            notes=_none(x.get("notes")),
        ))
    return out


def compare_user_action_projection(projected: Iterable[UserActionRow], actual: Iterable[UserActionRow]) -> dict[str, object]:
    p = list(projected)
    a = list(actual)
    pmap = {x.key: x for x in p}
    amap = {x.key: x for x in a}
    keys = sorted(set(pmap) | set(amap))
    diffs: list[dict[str, object]] = []
    for key in keys:
        pv = pmap.get(key)
        av = amap.get(key)
        if pv != av:
            diffs.append({
                "company": key[0],
                "role": key[1],
                "projected": None if pv is None else pv.__dict__,
                "actual": None if av is None else av.__dict__,
            })
    return {
        "projected_count": len(p),
        "actual_count": len(a),
        "key_parity": set(pmap) == set(amap),
        "exact_row_parity": not diffs,
        "critical_divergences": len(diffs),
        "differences": diffs,
    }


def _workday(value: str, days: int) -> str:
    d = _parse_date(value)
    remaining = days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d.isoformat()


def normalize_dateish(value: object) -> str | None:
    if value is None or value == "":
        return None
    s = str(value)
    if s.isdigit():
        serial = int(s)
        # Google Sheets serial epoch compatibility for modern dates.
        return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
    try:
        return _parse_date(s).isoformat()
    except ValueError:
        return s


def normalized(row: UserActionRow) -> UserActionRow:
    return UserActionRow(
        priority=row.priority,
        action_category=row.action_category,
        company=row.company,
        role=row.role,
        pipeline_status=row.pipeline_status,
        readiness_decision=row.readiness_decision,
        required_user_action=row.required_user_action,
        due_date=normalize_dateish(row.due_date),
        ordinary_facts=row.ordinary_facts,
        review_approval=row.review_approval,
        legal_consent=row.legal_consent,
        technical_upload=row.technical_upload,
        assigned_cv=row.assigned_cv,
        application_url=row.application_url,
        can_submit_now=row.can_submit_now,
        batch_group=row.batch_group,
        last_updated=normalize_dateish(row.last_updated),
        notes=row.notes,
    )


def _sort_key(x: UserActionRow) -> tuple[object, ...]:
    due = normalize_dateish(x.due_date)
    return (x.priority, 1 if due is None else 0, due or "9999-12-31")


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(value)


def _s(value: object) -> str:
    return "" if value is None else str(value)


def _none(value: object) -> str | None:
    s = _s(value)
    return s if s else None


def _i(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(value)
