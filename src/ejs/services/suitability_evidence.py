"""FUNCTION-04 evidence extraction from vacancy detail text. Pure, offline, no network.

Only explicit employer statements count. A hedged, conditional or absent statement yields
``None`` (Unknown), never ``False``. Every positive result carries the matched quote so a
reviewer can check the employer's wording. Nothing here reads candidate data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any

MAX_DETAIL_BYTES = 1_000_000
MAX_QUOTE = 240
TEXT_SECTIONS = ("jobDescription", "qualifications", "additionalInformation")
LANGUAGES = ("english", "dutch", "flemish", "german", "french", "spanish", "portuguese", "italian",
             "swedish", "danish", "norwegian", "finnish", "polish", "irish")

_HEDGE = re.compile(r"\b(?:may|might|possibly|possible|case[- ]by[- ]case|depending|depends|subject to|"
                    r"if applicable|for the right|selected|exceptional|considered)\b", re.I)
_SOFT_LANGUAGE = re.compile(r"\b(?:plus|advantage|asset|nice to have|preferred|desirable|beneficial|bonus|"
                            r"an added|is a plus|welcome|not required|not mandatory|optional)\b", re.I)

_NO_SPONSOR = tuple(re.compile(p, re.I) for p in (
    r"\b(?:we|the company|our company|employer)\s+(?:do(?:es)?\s+not|cannot|can't|will\s+not|won't|are\s+unable\s+to|"
    r"is\s+unable\s+to)\s+(?:offer|provide|sponsor|support)\b[^.]{0,40}\b(?:sponsorship|visas?|work\s+permits?)\b",
    r"\b(?:visa|work\s+permit)\s+sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable|not\s+provided|not\s+offered)\b",
    r"\bno\s+(?:visa\s+|work\s+permit\s+)?sponsorship\s+(?:is\s+)?(?:available|offered|provided)\b",
    r"\bunable\s+to\s+(?:offer\s+|provide\s+)?sponsor\w*\b[^.]{0,30}\b(?:visas?|work\s+permits?)\b",
    r"\bwe\s+do\s+not\s+sponsor\b",
))
_YES_SPONSOR = tuple(re.compile(p, re.I) for p in (
    r"\b(?:visa|work\s+permit)\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)\b",
    r"\bwe\s+(?:offer|provide|will\s+provide)\s+(?:visa|work\s+permit)\s+sponsorship\b",
    r"\bwe\s+sponsor\s+(?:work\s+)?(?:visas?|permits?)\b",
))
# Explicit demand for an unrestricted / permanent right to work, or for no need of sponsorship.
_UNLIMITED_RIGHT = tuple(re.compile(p, re.I) for p in (
    r"\b(?:unrestricted|unlimited|permanent|indefinite)\s+right\s+to\s+work\b",
    r"\bright\s+to\s+work\b[^.]{0,40}\b(?:without|not\s+requir\w+|no\s+need\s+for)\b[^.]{0,20}\bsponsorship\b",
    r"\bwithout\s+(?:requiring\s+)?(?:visa\s+)?sponsorship\b",
    r"\bmust\s+not\s+require\s+(?:visa\s+)?sponsorship\b",
))
_GENERIC_RIGHT = re.compile(r"\b(?:must|need\s+to|should|required\s+to)\s+(?:already\s+)?have\s+(?:the\s+)?"
                            r"(?:legal\s+)?right\s+to\s+work\b", re.I)
_LANG = "(" + "|".join(LANGUAGES) + ")"
_MANDATORY_LANG = tuple(re.compile(p, re.I) for p in (
    rf"\b{_LANG}(?:\s+language)?(?:\s+skills)?\s+(?:is|are)\s+(?:required|mandatory|essential|a\s+must|a\s+requirement)\b",
    rf"\b(?:fluent|fluency|proficient|proficiency|native|excellent\s+command)(?:\s+level)?\s+(?:in|of)\s+{_LANG}"
    r"\s+(?:is\s+)?(?:required|mandatory|essential)\b",
    rf"\b(?:must|need\s+to|have\s+to)\s+(?:be\s+)?(?:fluent|proficient)\s+in\s+{_LANG}\b",
    rf"\b(?:requires?|required)\s+(?:fluent\s+|native\s+)?{_LANG}\b(?!\s+(?:or|and/or)\b)[^.]{{0,15}}\b(?:fluency|fluent|native|proficiency)\b",
))
_WORKING_ENGLISH = re.compile(r"\b(?:working\s+language\s+is\s+english|english\s+is\s+(?:our|the)\s+(?:working|corporate|"
                              r"business)\s+language|work\s+in\s+an\s+english[- ]speaking)\b", re.I)
_MONEY = re.compile(
    r"(?:€|\bEUR\b)\s*(?P<lo>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?P<lok>\s?[kK])?"
    r"(?:\s*(?:-|–|—|to)\s*(?:€|\bEUR\b)?\s*(?P<hi>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?P<hik>\s?[kK])?)?"
    r"(?P<tail>[^.\n]{0,60})", re.I)
_PERIOD_MONTH = re.compile(r"(?:per|/|a|each)\s*month|monthly", re.I)
_PERIOD_YEAR = re.compile(r"(?:per|/|a|each)\s*(?:year|annum)|annual|yearly|p\.a\.", re.I)
_NET = re.compile(r"\bnet\b", re.I)
_GROSS = re.compile(r"\bgross\b|\bbruto\b|\bbrutto\b", re.I)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"} and self.hidden is None:
            self.hidden = tag
        if self.hidden is None and tag in {"p", "li", "br", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
        if self.hidden is None and tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden is None:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


@dataclass(frozen=True)
class VacancyDetail:
    """Identity-bound, already-collected vacancy detail. Built offline; this module never fetches."""
    tenant: str
    posting_id: str
    active: bool | None
    text: str
    detail_sha256: str
    structured_compensation: dict[str, Any] | None = None


def detail_from_smartrecruiters(raw: bytes, tenant: str, posting_id: str) -> VacancyDetail:
    """Parse already-collected SmartRecruiters posting bytes. Identity mismatch raises ``ValueError``."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DETAIL_BYTES:
        raise ValueError("detail_size")
    data = json.loads(raw)
    company = data.get("company") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("id") != posting_id or not isinstance(company, dict)
            or company.get("identifier") != tenant):
        raise ValueError("detail_identity")
    active = data.get("active")
    ad = data.get("jobAd")
    sections = ad.get("sections") if isinstance(ad, dict) else None
    chunks: list[str] = []
    if isinstance(sections, dict):
        for name in TEXT_SECTIONS:
            section = sections.get(name)
            html = section.get("text") if isinstance(section, dict) else None
            if isinstance(html, str):
                chunks.append(html_to_text(html))
    return VacancyDetail(tenant=tenant, posting_id=posting_id, active=active if isinstance(active, bool) else None,
                         text="\n".join(chunks), detail_sha256=sha256(raw).hexdigest())


def _sentences(text: str) -> list[str]:
    return [" ".join(s.split()) for s in re.split(r"\n+|(?<=[.!?])\s+", text) if s.strip()]


def _quote(sentence: str) -> str:
    return sentence[:MAX_QUOTE]


@dataclass(frozen=True)
class DetailEvidence:
    """Tri-state facts (True / False / None = Unknown) with the employer quote that supports each."""
    vacancy_open: bool | None
    explicit_no_sponsorship: bool | None = None
    explicit_sponsorship_offered: bool | None = None
    requires_unlimited_work_right: bool | None = None
    generic_right_to_work_required: bool | None = None
    mandatory_languages: tuple[str, ...] = ()
    english_working_language: bool | None = None
    compensation: dict[str, Any] | None = None
    quotes: dict[str, str] = field(default_factory=dict)
    contradictions: tuple[str, ...] = ()
    hedged: tuple[str, ...] = ()


def _parse_amount(raw: str, k: str | None) -> float | None:
    s = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return value * 1000 if k else value


def extract_compensation(text: str) -> dict[str, Any] | None:
    """EUR amounts with an explicit period only. Otherwise ``None`` (Unknown). No FX, no tax model."""
    for sentence in _sentences(text):
        m = _MONEY.search(sentence)
        if not m:
            continue
        tail = m.group("tail") or ""
        if _PERIOD_MONTH.search(tail):
            period = "month"
        elif _PERIOD_YEAR.search(tail):
            period = "year"
        else:
            continue
        lo = _parse_amount(m.group("lo"), m.group("lok"))
        hi = _parse_amount(m.group("hi"), m.group("hik")) if m.group("hi") else lo
        if lo is None or hi is None or lo > hi or hi <= 0:
            continue
        basis = "net" if _NET.search(sentence) and not _GROSS.search(sentence) else (
            "gross" if _GROSS.search(sentence) and not _NET.search(sentence) else "unspecified")
        div = 12 if period == "year" else 1
        return {"currency": "EUR", "monthly_min": lo / div, "monthly_max": hi / div, "basis": basis,
                "period_stated": period, "quote": _quote(sentence)}
    return None


def normalize_structured_compensation(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Caller-supplied figures: ``currency``, ``min``, ``max``, ``period`` (month|year), ``basis`` (net|gross|unspecified)."""
    try:
        lo, hi = raw["min"], raw.get("max", raw["min"])
        if raw.get("currency") != "EUR" or raw.get("period") not in ("month", "year"):
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lo, hi)) or not 0 < lo <= hi:
            return None
        basis = raw.get("basis", "unspecified")
        if basis not in ("net", "gross", "unspecified"):
            return None
    except (KeyError, TypeError):
        return None
    div = 12 if raw["period"] == "year" else 1
    return {"currency": "EUR", "monthly_min": lo / div, "monthly_max": hi / div, "basis": basis,
            "period_stated": raw["period"], "quote": "structured_input"}


def extract_detail_evidence(detail: VacancyDetail) -> DetailEvidence:
    quotes: dict[str, str] = {}
    hedged: list[str] = []
    no_s = yes_s = unlimited = generic = False
    languages: list[str] = []
    working_english = False
    for sentence in _sentences(detail.text):
        if any(p.search(sentence) for p in _NO_SPONSOR):
            if _HEDGE.search(sentence):
                hedged.append("sponsorship")
            else:
                no_s = True
                quotes.setdefault("sponsorship_no", _quote(sentence))
        if any(p.search(sentence) for p in _YES_SPONSOR) and not any(p.search(sentence) for p in _NO_SPONSOR):
            if _HEDGE.search(sentence):
                hedged.append("sponsorship")
            else:
                yes_s = True
                quotes.setdefault("sponsorship_yes", _quote(sentence))
        if any(p.search(sentence) for p in _UNLIMITED_RIGHT) and not any(p.search(sentence) for p in _NO_SPONSOR):
            unlimited = True
            quotes.setdefault("unlimited_work_right", _quote(sentence))
        if _GENERIC_RIGHT.search(sentence):
            generic = True
            quotes.setdefault("generic_right_to_work", _quote(sentence))
        if not _SOFT_LANGUAGE.search(sentence):
            for pattern in _MANDATORY_LANG:
                for m in pattern.finditer(sentence):
                    lang = m.group(1).lower()
                    if lang not in languages:
                        languages.append(lang)
                        quotes.setdefault(f"mandatory_language:{lang}", _quote(sentence))
        if _WORKING_ENGLISH.search(sentence):
            working_english = True
            quotes.setdefault("english_working_language", _quote(sentence))
    comp = (normalize_structured_compensation(detail.structured_compensation)
            if detail.structured_compensation else extract_compensation(detail.text))
    contradictions = ("sponsorship_statements_conflict",) if no_s and yes_s else ()
    return DetailEvidence(
        vacancy_open=detail.active,
        explicit_no_sponsorship=True if no_s else None,
        explicit_sponsorship_offered=True if yes_s else None,
        requires_unlimited_work_right=True if unlimited else None,
        generic_right_to_work_required=True if generic else None,
        mandatory_languages=tuple(sorted(languages)),
        english_working_language=True if working_english else None,
        compensation=comp, quotes=dict(sorted(quotes.items())), contradictions=contradictions,
        hedged=tuple(sorted(set(hedged))))
