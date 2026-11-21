"""Versioned criteria file schema + loader (ROADMAP Faz 1.1).

Pure logic: no network, no writes. Fail-closed: any problem raises ``CriteriaError``.

File formats: JSON, or the small YAML *subset* implemented here (PyYAML is not a
dependency). The subset: ``key: value`` block mappings (2+ space indentation),
``- item`` block sequences of scalars / flow collections, flow ``[a, b]`` / ``{k: v}``
collections, ``#`` comments, quoted/plain scalars, ``null``/``~``/``true``/``false``,
ints and floats. Anchors, tags, multi-line scalars, multi-document files and block
mappings inside sequence items are NOT supported and raise ``CriteriaError``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SUPPORTED_SCHEMAS = frozenset({"EJS-CRITERIA-0.1-DRAFT", "EJS-CRITERIA-1.0"})
SCORE_COMPONENTS = ("sponsorship", "compensation_living", "role_fit", "scope",
                    "career_upside", "english_intl", "seniority_title")
THRESHOLD_KEYS = ("priority_apply", "apply", "consider")
HARD_BLOCKER_CODES = frozenset({
    "explicit-no-sponsorship", "unlimited-work-right-required", "mandatory-language-unmet",
    "closed-vacancy", "junior-or-technical-only"})

# Every top-level key; nested dict sections list their allowed keys (unknown key = error).
_NESTED_KEYS: dict[str, frozenset[str]] = {
    "countries": frozenset({"tier_1", "others"}),
    "role_tracks": frozenset({"primary", "secondary"}),
    "seniority": frozenset({"include", "exclude"}),
    "work_rights": frozenset({"eu_work_authorization", "unknown_sponsorship_is_hard_reject"}),
    "compensation": frozenset({"target_net_monthly_eur_min", "unknown_salary_is_hard_reject"}),
    "score_weights": frozenset(SCORE_COMPONENTS),
    "thresholds": frozenset(THRESHOLD_KEYS),
    "scoring": frozenset({"unknown_credit_percent"}),
    "auto_apply": frozenset({"opportunity_min", "hard_blockers",
                             "calibration_review_after_applications", "previous_sheet_rule"}),
    "rollout": frozenset({"daily_submit_cap", "pilot_applications"}),
    "sources": frozenset({"default", "linkedin", "indeed", "eures"}),
    "employer_watchlist": frozenset({"location"}),
    "ledger": frozenset({"path", "single_writer"}),
    "chain": frozenset({"steps", "schedule_local_time", "catch_up"}),
    "health": frozenset({"feeder_dead_after_hours", "feeder_dead_after_zero_runs",
                         "notify_feeder_dead_immediately", "daily_summary"}),
    # No Sheet-write key exists on purpose (ARCHITECTURE §5).
    "export": frozenset({"csv_dir", "enabled"}),
    "outcome_source": frozenset({"adapter", "cutover_date", "overlap_days", "dedupe_key"}),
    "profile_answer_bank": frozenset({"location"}),
    "ranking": frozenset({"mode"}),
}
_FLAT_KEYS = frozenset({"schema", "source", "languages_required_met", "exclusions", "submit_mode",
                        "cv_selection", "dedupe_key", "follow_up_default_business_days",
                        "rule_version"})
TOP_LEVEL_KEYS = _FLAT_KEYS | frozenset(_NESTED_KEYS)


class CriteriaError(ValueError):
    """Criteria file is invalid; the caller must not start the module (fail-closed)."""


# ---------------------------------------------------------------- mini YAML subset
def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _split_top(text: str, sep: str) -> list[str]:
    parts, depth, quote, cur = [], 0, None, []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quote or depth != 0:
        raise CriteriaError("YAML_UNBALANCED_FLOW_OR_QUOTE")
    parts.append("".join(cur))
    return parts


_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?\d+\.\d+$")


def _scalar(text: str) -> Any:
    t = text.strip()
    if t == "":
        return None
    if t[0] in "\"'":
        if len(t) < 2 or t[-1] != t[0]:
            raise CriteriaError("YAML_BAD_QUOTED_SCALAR")
        return t[1:-1]
    if t[0] in "[{":
        return _flow(t)
    if t[0] in "&*!|>%@`":
        raise CriteriaError("YAML_UNSUPPORTED_SYNTAX")
    low = t.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT.match(t):
        return int(t)
    if _FLOAT.match(t):
        return float(t)
    return t


def _key_split(text: str) -> tuple[str, str] | None:
    """Split ``key: value`` at the first top-level ': ' (or trailing ':')."""
    quote, depth = None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and (i + 1 == len(text) or text[i + 1] == " "):
            return text[:i].strip(), text[i + 1:].strip()
    return None


def _flow(text: str) -> Any:
    t = text.strip()
    if t.startswith("["):
        if not t.endswith("]"):
            raise CriteriaError("YAML_UNBALANCED_FLOW_OR_QUOTE")
        inner = t[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in _split_top(inner, ",")]
    if not t.endswith("}"):
        raise CriteriaError("YAML_UNBALANCED_FLOW_OR_QUOTE")
    inner = t[1:-1].strip()
    out: dict[str, Any] = {}
    if inner:
        for part in _split_top(inner, ","):
            kv = _key_split(part.strip())
            if kv is None:
                raise CriteriaError("YAML_BAD_FLOW_MAPPING")
            k = _scalar(kv[0])
            if not isinstance(k, str) or k in out:
                raise CriteriaError("YAML_BAD_OR_DUPLICATE_KEY")
            out[k] = _scalar(kv[1])
    return out


def parse_yaml_subset(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise CriteriaError("YAML_TAB_INDENT")
        body = _strip_comment(raw).rstrip()
        if body.strip() == "":
            continue
        if body.strip() in ("---", "..."):
            raise CriteriaError("YAML_UNSUPPORTED_SYNTAX")
        lines.append((len(body) - len(body.lstrip()), body.strip()))
    if not lines:
        raise CriteriaError("CRITERIA_EMPTY")
    value, pos = _block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise CriteriaError("YAML_BAD_INDENTATION")
    return value


def _block(lines: list[tuple[int, str]], pos: int, indent: int) -> tuple[Any, int]:
    if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
        items: list[Any] = []
        while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("-"):
            rest = lines[pos][1][1:].strip()
            if rest == "" or _key_split(rest) is not None and rest[0] not in "{[\"'":
                raise CriteriaError("YAML_UNSUPPORTED_SEQUENCE_ITEM")
            items.append(_scalar(rest))
            pos += 1
        if pos < len(lines) and lines[pos][0] >= indent:
            raise CriteriaError("YAML_BAD_INDENTATION")
        return items, pos
    out: dict[str, Any] = {}
    while pos < len(lines) and lines[pos][0] == indent:
        kv = _key_split(lines[pos][1])
        if kv is None:
            raise CriteriaError("YAML_EXPECTED_MAPPING_KEY")
        key = _scalar(kv[0])
        if not isinstance(key, str) or key in out:
            raise CriteriaError("YAML_BAD_OR_DUPLICATE_KEY")
        pos += 1
        if kv[1] != "":
            out[key] = _scalar(kv[1])
        elif pos < len(lines) and lines[pos][0] > indent:
            out[key], pos = _block(lines, pos, lines[pos][0])
        elif pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
            out[key], pos = _block(lines, pos, indent)  # same-indent sequence
        else:
            out[key] = None
    if pos < len(lines) and lines[pos][0] > indent:
        raise CriteriaError("YAML_BAD_INDENTATION")
    return out, pos


# ---------------------------------------------------------------- validated model
@dataclass(frozen=True)
class Criteria:
    rule_version: str
    schema: str
    weights: Mapping[str, int]
    thresholds: Mapping[str, int]
    unknown_credit_percent: int
    auto_apply_min: int
    hard_blockers: tuple[str, ...]
    previous_sheet_rule: Any          # stored for the record only; never used in decisions
    feeder_dead_after_hours: float | None
    feeder_dead_after_zero_runs: int | None
    raw: Mapping[str, Any]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_criteria(doc: Any) -> Criteria:
    if not isinstance(doc, dict):
        raise CriteriaError("CRITERIA_NOT_A_MAPPING")
    unknown = sorted(set(doc) - TOP_LEVEL_KEYS)
    if unknown:
        raise CriteriaError(f"CRITERIA_UNKNOWN_KEY:{','.join(map(str, unknown))}")
    if doc.get("schema") not in SUPPORTED_SCHEMAS:
        raise CriteriaError("CRITERIA_SCHEMA_UNSUPPORTED")
    rv = doc.get("rule_version")
    if not isinstance(rv, str) or not rv.strip():
        raise CriteriaError("CRITERIA_RULE_VERSION_MISSING")
    for section, allowed in _NESTED_KEYS.items():
        val = doc.get(section)
        if val is None:
            continue
        if not isinstance(val, dict):
            raise CriteriaError(f"CRITERIA_SECTION_NOT_MAPPING:{section}")
        bad = sorted(set(val) - allowed)
        if bad:
            raise CriteriaError(f"CRITERIA_UNKNOWN_KEY:{section}.{','.join(map(str, bad))}")

    weights = doc.get("score_weights")
    if not isinstance(weights, dict) or set(weights) != set(SCORE_COMPONENTS):
        raise CriteriaError("CRITERIA_SCORE_WEIGHTS_INCOMPLETE")
    if not all(_is_int(w) and w >= 0 for w in weights.values()):
        raise CriteriaError("CRITERIA_SCORE_WEIGHT_INVALID")
    if sum(weights.values()) != 100:
        raise CriteriaError("CRITERIA_SCORE_WEIGHTS_SUM_NOT_100")

    th = doc.get("thresholds")
    if not isinstance(th, dict) or set(th) != set(THRESHOLD_KEYS) or not all(_is_int(v) for v in th.values()):
        raise CriteriaError("CRITERIA_THRESHOLDS_INVALID")
    if not 0 < th["consider"] < th["apply"] < th["priority_apply"] <= 100:
        raise CriteriaError("CRITERIA_THRESHOLD_ORDER")

    scoring = doc.get("scoring")
    credit = scoring.get("unknown_credit_percent") if isinstance(scoring, dict) else None
    if not _is_int(credit) or not 0 <= credit <= 100:
        raise CriteriaError("CRITERIA_UNKNOWN_CREDIT_INVALID")

    aa = doc.get("auto_apply")
    if not isinstance(aa, dict):
        raise CriteriaError("CRITERIA_AUTO_APPLY_MISSING")
    amin = aa.get("opportunity_min")
    if not _is_int(amin) or not 0 < amin <= 100:
        raise CriteriaError("CRITERIA_AUTO_APPLY_MIN_INVALID")
    blockers = aa.get("hard_blockers")
    if not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers):
        raise CriteriaError("CRITERIA_HARD_BLOCKERS_INVALID")
    if len(set(blockers)) != len(blockers) or not set(blockers) <= HARD_BLOCKER_CODES:
        raise CriteriaError("CRITERIA_HARD_BLOCKER_UNKNOWN_OR_DUPLICATE")
    if set(blockers) != HARD_BLOCKER_CODES:  # mandatory blockers cannot be configured away
        raise CriteriaError("CRITERIA_HARD_BLOCKER_MANDATORY_MISSING")

    health = doc.get("health") or {}
    hours = health.get("feeder_dead_after_hours")
    runs = health.get("feeder_dead_after_zero_runs")
    if hours is not None and not (_num(hours) and hours > 0):
        raise CriteriaError("CRITERIA_FEEDER_HOURS_INVALID")
    if runs is not None and not (_is_int(runs) and runs > 0):
        raise CriteriaError("CRITERIA_FEEDER_ZERO_RUNS_INVALID")

    return Criteria(
        rule_version=rv.strip(), schema=doc["schema"], weights=dict(weights), thresholds=dict(th),
        unknown_credit_percent=credit, auto_apply_min=amin, hard_blockers=tuple(blockers),
        previous_sheet_rule=aa.get("previous_sheet_rule"),
        feeder_dead_after_hours=hours, feeder_dead_after_zero_runs=runs, raw=doc)


def parse_criteria_text(text: str) -> Criteria:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            doc = json.loads(text, object_pairs_hook=_no_dup_pairs)
        except json.JSONDecodeError as exc:
            raise CriteriaError("CRITERIA_JSON_INVALID") from exc
    else:
        doc = parse_yaml_subset(text)
    return validate_criteria(doc)


def _no_dup_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise CriteriaError("CRITERIA_DUPLICATE_KEY")
        out[k] = v
    return out


def load_criteria(path: str | Path) -> Criteria:
    """Read a criteria file (read-only). Callers pass a path explicitly; no default
    location and no live Sheet access exist."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CriteriaError("CRITERIA_UNREADABLE") from exc
    return parse_criteria_text(text)
