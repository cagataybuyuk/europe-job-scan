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
# Conditional, scoped or time-limited wording: the employer statement is ambiguous, so it is never a confirmed fact.
_AMBIGUOUS = re.compile(r"\b(?:unless|except|excluding|other than|outside|currently|at this time|generally|usually|"
                        r"typically|normally|in most cases|in some cases|for some|certain|preferably|ideally|"
                        r"if\s+(?:you|applicable|needed|required|possible)|where\s+(?:possible|applicable)|"
                        r"or equivalent|or similar)\b", re.I)
# "... is not required", "no need for ...": the requirement is explicitly absent.
_NOT_REQUIRED = re.compile(r"\b(?:(?:is|are|be|was|were)\s+(?:not|no longer|never)\s+(?:be\s+)?(?:required|necessary|needed|"
                           r"mandatory|essential|a\s+requirement)|(?:not|n't)\s+(?:be\s+)?(?:required|necessary|needed|mandatory|"
                           r"essential)|no\s+(?:need|requirement)|need\s+not|(?:do|does|don't|doesn't)\s+not\s+(?:need|require)|"
                           r"don't\s+(?:need|require)|doesn't\s+require|not\s+a\s+requirement)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|neither|nor)\b|n't", re.I)
_CUE = re.compile(r"\b(?:must|need|needs|required|requires?|require|have\s+to|has\s+to|should|mandatory|essential)\b", re.I)
_SOFT_LANGUAGE = re.compile(r"\b(?:plus|advantage|asset|nice to have|preferred|desirable|beneficial|bonus|"
                            r"an added|is a plus|welcome|not required|not mandatory|optional)\b", re.I)

_NO_SPONSOR = tuple(re.compile(p, re.I) for p in (
    r"\b(?:we|the company|our company|employer)\s+(?:do(?:es)?\s+not|cannot|can't|will\s+not|won't|are\s+unable\s+to|"
    r"is\s+unable\s+to)\s+(?:offer|provide|sponsor|support)\b[^.]{0,40}\b(?:sponsorship|visas?|work\s+permits?)\b",
    r"\b(?:visa|work\s+permit)\s+sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable|not\s+provided|not\s+offered)\b",
    r"\bno\s+(?:visa\s+|work\s+permit\s+)?sponsorship\s+(?:is\s+)?(?:available|offered|provided)\b",
    r"\bunable\s+to\s+(?:offer\s+|provide\s+)?sponsor\w*\b[^.]{0,30}\b(?:visas?|work\s+permits?)\b",
    r"\bwe\s+do\s+not\s+sponsor\b",
    # Dutch (explicit refusal only)
    r"\bgeen\s+(?:visum|visa|werkvergunning|verblijfsvergunning)[- ]?sponsor(?:ing|schap)\b",
    r"\b(?:wij|we)\s+(?:bieden|verlenen|verzorgen)\s+geen\s+(?:visum|visa|werkvergunning)[- ]?sponsor\w*",
    r"\b(?:wij|we)\s+sponsoren\s+geen\s+(?:visum|visa|visums|werkvergunning\w*)\b",
    r"\b(?:visum|visa|werkvergunning)[- ]?sponsoring\s+is\s+niet\s+(?:mogelijk|beschikbaar)\b",
))
_YES_SPONSOR = tuple(re.compile(p, re.I) for p in (
    r"\b(?:visa|work\s+permit)\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)\b",
    r"\bwe\s+(?:offer|provide|will\s+provide)\s+(?:visa|work\s+permit)\s+sponsorship\b",
    r"\bwe\s+sponsor\s+(?:work\s+)?(?:visas?|permits?)\b",
    # Dutch (explicit offer only)
    r"\b(?:wij|we)\s+(?:bieden|verzorgen|regelen)\s+(?:je\s+|jou\s+)?(?:een\s+)?(?:visum|visa|werkvergunning)[- ]?sponsor(?:ing|schap)\b",
    r"\b(?:wij|we)\s+sponsoren\s+(?:een\s+|het\s+)?(?:visum|visa|werkvergunning)\b",
    r"\b(?:visum|visa|werkvergunning)[- ]?sponsoring\s+is\s+(?:mogelijk|beschikbaar)\b",
))
# Explicit demand for an unrestricted / permanent right to work, or for no need of sponsorship.
_UNLIMITED_RIGHT = tuple(re.compile(p, re.I) for p in (
    r"\b(?:unrestricted|unlimited|permanent|indefinite)\s+right\s+to\s+work\b",
    r"\bright\s+to\s+work\b[^.]{0,40}\bwithout\b[^.]{0,20}\bsponsorship\b",
    r"\bwithout\s+(?:requiring\s+)?(?:visa\s+)?sponsorship\b",
))
_UNLIMITED_EXPLICIT = re.compile(r"\b(?:must|need\s+to|have\s+to)\s+not\s+require\s+(?:visa\s+)?sponsorship\b", re.I)
_JUNIOR_STATEMENT = re.compile(
    r"\b(?:entry[- ]level|junior\s+(?:level|position|role|profile|engineer|analyst)|graduate\s+(?:programme|program|scheme)|"
    r"internship|(?:0|zero)\s*(?:-|–|to)\s*[12]\s+years?|no\s+(?:prior\s+|previous\s+)?experience\s+(?:is\s+)?required)\b", re.I)
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
_LANG_ALTERNATIVES = re.compile(rf"\b{_LANG}\b\s*(?:/|\bor\b|and/or)\s*{_LANG}\b", re.I)
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


# ---- Dutch support (additive). Same safeguards as English: preference, negation, condition and alternatives stay Unknown.
NL_LANGUAGES = {"nederlands": "dutch", "engels": "english", "duits": "german", "frans": "french", "spaans": "spanish",
                "portugees": "portuguese", "italiaans": "italian", "zweeds": "swedish", "deens": "danish",
                "noors": "norwegian", "fins": "finnish", "pools": "polish", "iers": "irish"}
_NL_LANG = "(" + "|".join(NL_LANGUAGES) + ")"
_NL_PREF = re.compile(r"\b(?:bij voorkeur|voorkeur|pr[eé]|pluspunt|liefst|wenselijk|idealiter|gewenst|een plus|plus|"
                      r"voordeel|optioneel|niet vereist|geen vereiste|niet noodzakelijk|nice to have|preferably|"
                      r"preferred|ideally|advantage)\b", re.I)
_NL_HEDGE = re.compile(r"\b(?:tenzij|behalve|momenteel|meestal|doorgaans|normaal gesproken|indien|eventueel|mogelijk|"
                       r"afhankelijk van|in sommige gevallen|bepaalde)\b", re.I)
_NL_NEG = re.compile(r"\b(?:geen|niet|nooit|zonder)\b", re.I)
_NL_LANG_VERB = re.compile(r"\b(?:spreekt|spreek|schrijft|beheerst|beheers|vloeiend|uitstekende?\s+beheersing|"
                           r"uitstekende?\s+kennis|vereist|verplicht|noodzakelijk|een must|minimaal|minimum|ten minste)\b", re.I)
_NL_LANG_ALT = re.compile(rf"\b{_NL_LANG}\b\s*(?:/|\bof\b|en/of)\s*{_NL_LANG}\b", re.I)
_CEFR_BEFORE = re.compile(rf"\b(?:minimaal|minimum|ten minste)?\s*([ABC][12])(?:[- ]niveau)?\s+{_NL_LANG}\b", re.I)
_CEFR_AFTER = re.compile(rf"\b{_NL_LANG}\b\s*\(?\s*(?:minimaal\s+|minimum\s+|op\s+)?([ABC][12])\b", re.I)
_NL_AMOUNT = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2}|,-)?|\d+(?:,\d{1,2}|,-)?"
_NL_MONEY = re.compile(rf"(?:€|\bEUR\b)\s*(?P<lo>{_NL_AMOUNT})(?:\s*(?:-|–|—|tot(?:\s+en\s+met)?|en)\s*(?:€|\bEUR\b)?\s*"
                       rf"(?P<hi>{_NL_AMOUNT}))?", re.I)
# Compound words (Brutomaandsalaris, Nettojaarsalaris, onkostenvergoeding) are matched as substrings on purpose.
_NL_SALARY_WORD = re.compile(r"salaris|\bloon\b|\bverdien\w*|\binkomen\b|\bsalary\b", re.I)
_NL_BENEFIT = re.compile(r"vergoeding|toelage|bonus|vakantiegeld|reiskosten|\blease|budget|bijdrage|pensioen|kilometer|"
                         r"\bbeloning|13e\s+maand|dertiende", re.I)
_NL_MONTH = re.compile(r"\bper\s+maand\b|/\s*maand\b|maandsalaris|\bmaandelijks\b|\bp\.?m\.?(?=\W|$)|\bper\s+month\b", re.I)
_NL_YEAR = re.compile(r"\bper\s+jaar\b|/\s*jaar\b|jaarsalaris|\bjaarlijks\b|\bper\s+annum\b|\bp\.a\.|\bper\s+year\b", re.I)
_NL_NET = re.compile(r"\bnetto\w*|\bnet\b", re.I)
_NL_GROSS = re.compile(r"bruto|brutto|\bgross\b", re.I)
_EXP_MIN = r"minimaal|minimum|ten\s+minste|tenminste|at\s+least|minimum\s+of|min\."
_EXP = re.compile(rf"(?:\b(?P<min>{_EXP_MIN})\s+)?\b(?P<lo>\d{{1,2}})(?:\s*(?:-|–|tot|to)\s*(?P<hi>\d{{1,2}}))?(?P<plus>\s*\+)?\s*"
                  r"(?:jaar|jaren|years?|yrs?)\b", re.I)
_EXP_WORD = re.compile(r"\b(?:ervaring|werkervaring|experience)\b", re.I)
_EDU = (
    ("phd", re.compile(r"\b(?:phd|ph\.d|promotie|doctoraat|doctorate)\b", re.I)),
    ("wo_master", re.compile(r"\b(?:wo[- ]?master\w*|master\s+(?:op\s+)?wo|wetenschappelijk\s+onderwijs|universitaire\s+master|"
                             r"msc|master\s+of\s+science|afgeronde?\s+(?:wo|universitaire)\b[^.;]{0,20}master)\b", re.I)),
    ("wo_bachelor", re.compile(r"\b(?:wo[- ]?bachelor\w*|universitaire\s+bachelor)\b", re.I)),
    ("hbo", re.compile(r"\bhbo\b", re.I)),
    ("bachelor", re.compile(r"\bbachelor'?s?(?:\s+degree)?\b", re.I)),
    ("master", re.compile(r"\bmaster\b", re.I)),
)
_EDU_CUE = re.compile(r"\b(?:je\s+hebt|u\s+heeft|you\s+(?:have|hold|bring)|afgeronde?|completed|degree|diploma|opleiding|"
                      r"studie|vereist|required|minimaal|minimum|ten\s+minste|beschik\w*)\b", re.I)
_EDU_COMPLETED = re.compile(r"\b(?:afgeronde?|completed|behaald\w*|graduated)\b", re.I)
_EDU_MANDATORY = re.compile(r"\b(?:afgeronde?|completed|vereist|required|must|minimaal|minimum|ten\s+minste)\b", re.I)


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
    junior_statement: bool | None = None
    mandatory_languages: tuple[str, ...] = ()
    english_working_language: bool | None = None
    compensation: dict[str, Any] | None = None
    quotes: dict[str, str] = field(default_factory=dict)
    contradictions: tuple[str, ...] = ()
    hedged: tuple[str, ...] = ()
    language_levels: dict[str, str] = field(default_factory=dict)
    experience_requirements: tuple[dict[str, Any], ...] = ()
    education_requirements: tuple[dict[str, Any], ...] = ()


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


def _parse_nl_amount(raw: str) -> float | None:
    s = raw.strip().removesuffix(",-")
    s = s.replace(".", "").replace(",", ".") if "," in s or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s) else s
    try:
        return float(s)
    except ValueError:
        return None


def extract_compensation_nl(text: str) -> dict[str, Any] | None:
    """Dutch salary sentences: EUR amounts (``4.500``, ``4.500,-``, ranges with ``tot``/``en``/dash) with an explicit
    month or year period and a salary word. Gross stays gross; net is never derived. Otherwise ``None`` (Unknown)."""
    for sentence in _sentences(text):
        m = _NL_MONEY.search(sentence)
        if not m or not _NL_SALARY_WORD.search(sentence):
            continue
        # Benefits (bonus, allowance, ...) may share the sentence with a base salary range; only a benefit word
        # that directly labels the amount (just before it, or right after it up to the next comma) disqualifies it.
        before, after = sentence[max(0, m.start() - 40):m.start()], re.split(r"[,;]", sentence[m.end():], maxsplit=1)[0]
        if _NL_BENEFIT.search(before.split(",")[-1]) or _NL_BENEFIT.search(after[:40]):
            continue
        month, year = bool(_NL_MONTH.search(sentence)), bool(_NL_YEAR.search(sentence))
        if month == year:
            continue                                   # no period, or both: ambiguous
        lo = _parse_nl_amount(m.group("lo"))
        hi = _parse_nl_amount(m.group("hi")) if m.group("hi") else lo
        if lo is None or hi is None or lo > hi or hi <= 0:
            continue
        basis = "net" if _NL_NET.search(sentence) and not _NL_GROSS.search(sentence) else (
            "gross" if _NL_GROSS.search(sentence) and not _NL_NET.search(sentence) else "unspecified")
        div = 12 if year else 1
        return {"currency": "EUR", "monthly_min": lo / div, "monthly_max": hi / div, "basis": basis,
                "period_stated": "year" if year else "month", "quote": _quote(sentence)}
    return None


_NL_PREF_INTRO = re.compile(r"\b(?:bij\s+voorkeur|liefst|idealiter|preferably|ideally)\b", re.I)


def _kind(clause: str, head: str, explicit_minimum: bool) -> str:
    """Negation and conditions anywhere in the clause make a requirement conditional; a preference marker makes it
    preferred only for what it covers (see ``_scoped``)."""
    if _NL_NEG.search(clause) and not explicit_minimum or _NL_HEDGE.search(clause):
        return "conditional"
    if _NL_PREF.search(head):
        return "preferred"
    return "mandatory" if explicit_minimum else "stated_unclassified"


def _scoped(clause: str) -> list[tuple[str, str, bool]]:
    """Split ``<requirement>, bij voorkeur <specialisation>`` so the preference covers only what follows it.
    Returns (text, head-for-kind, is_preferred_scope) parts; a clause without an introducer is one part."""
    intro = _NL_PREF_INTRO.search(clause)
    if not intro:
        return [(clause, clause, False)]
    head, tail = clause[:intro.start()], clause[intro.start():]
    parts = [(head, head, False)] if head.strip(" ,:-") else []
    parts.append((tail, tail, True))
    return parts


def extract_requirements(text: str) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Minimum relevant experience and required education, with the employer quote and a mandatory / preferred /
    conditional / unclassified kind. Statements only; nothing is compared to the candidate."""
    experience: list[dict[str, Any]] = []
    education: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        for clause in sentence.split(";"):
            for part, head, preferred_scope in _scoped(clause):
                if _EXP_WORD.search(clause):
                    for m in _EXP.finditer(part):
                        lo, hi = int(m.group("lo")), int(m.group("hi")) if m.group("hi") and not m.group("plus") else None
                        if not 0 < lo <= 40 or (hi is not None and hi < lo):
                            continue
                        explicit = bool(m.group("min") or m.group("plus")) and not preferred_scope
                        kind = "preferred" if preferred_scope and not _NL_HEDGE.search(clause) else _kind(clause, head, explicit)
                        row = {"min_years": lo, "max_years": hi, "kind": kind, "quote": _quote(clause)}
                        if not any(r["min_years"] == lo and r["max_years"] == hi for r in experience):
                            experience.append(row)
                if _EDU_CUE.search(clause):
                    for level, pattern in _EDU:
                        if pattern.search(part):
                            explicit = bool(_EDU_MANDATORY.search(head)) and not preferred_scope
                            kind = "preferred" if preferred_scope and not _NL_HEDGE.search(clause) else _kind(clause, head, explicit)
                            row = {"level": level, "kind": kind, "completed_stated": bool(_EDU_COMPLETED.search(part)),
                                   "quote": _quote(clause)}
                            if not any(r["level"] == level for r in education):
                                education.append(row)
                            break
    return tuple(experience), tuple(education)


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
    junior = False
    levels: dict[str, str] = {}
    for sentence in _sentences(detail.text):
        refuses = any(p.search(sentence) for p in _NO_SPONSOR)
        if refuses:
            if _HEDGE.search(sentence) or _AMBIGUOUS.search(sentence) or _NL_HEDGE.search(sentence):
                hedged.append("sponsorship")
            else:
                no_s = True
                quotes.setdefault("sponsorship_no", _quote(sentence))
        elif any(p.search(sentence) for p in _YES_SPONSOR):
            if _HEDGE.search(sentence) or _AMBIGUOUS.search(sentence) or _NL_HEDGE.search(sentence):
                hedged.append("sponsorship")
            else:
                yes_s = True
                quotes.setdefault("sponsorship_yes", _quote(sentence))
        ambiguous = bool(_HEDGE.search(sentence) or _AMBIGUOUS.search(sentence) or _SOFT_LANGUAGE.search(sentence)
                         or _NL_HEDGE.search(sentence) or _NL_PREF.search(sentence))
        for clause in sentence.split(";"):
            nl_langs = [NL_LANGUAGES[m.group(1).lower()] for m in re.finditer(rf"\b{_NL_LANG}\b", clause, re.I)]
            if nl_langs and _NL_LANG_VERB.search(clause):
                if (_NL_PREF.search(clause) or _NL_HEDGE.search(clause) or _NL_NEG.search(clause)
                        or _NL_LANG_ALT.search(clause) or _HEDGE.search(clause)):
                    hedged.append("language")
                else:
                    for lang in nl_langs:
                        if lang not in languages:
                            languages.append(lang)
                            quotes.setdefault(f"mandatory_language:{lang}", _quote(clause.strip()))
                    for m in _CEFR_BEFORE.finditer(clause):
                        levels.setdefault(NL_LANGUAGES[m.group(2).lower()], m.group(1).upper())
                    for m in _CEFR_AFTER.finditer(clause):
                        levels.setdefault(NL_LANGUAGES[m.group(1).lower()], m.group(2).upper())
        if any(p.search(sentence) for p in _UNLIMITED_RIGHT) and not refuses or _UNLIMITED_EXPLICIT.search(sentence):
            if _NOT_REQUIRED.search(sentence) and not _UNLIMITED_EXPLICIT.search(sentence) or ambiguous or \
                    not _CUE.search(sentence):
                hedged.append("work_right")
            else:
                unlimited = True
                quotes.setdefault("unlimited_work_right", _quote(sentence))
        if _GENERIC_RIGHT.search(sentence) and not _NOT_REQUIRED.search(sentence) and not _NEGATION.search(sentence):
            generic = True
            quotes.setdefault("generic_right_to_work", _quote(sentence))
        found = [m.group(1).lower() for pattern in _MANDATORY_LANG for m in pattern.finditer(sentence)]
        if found:
            if ambiguous or _NEGATION.search(sentence) or _NOT_REQUIRED.search(sentence) or _LANG_ALTERNATIVES.search(sentence):
                hedged.append("language")
            else:
                for lang in found:
                    if lang not in languages:
                        languages.append(lang)
                        quotes.setdefault(f"mandatory_language:{lang}", _quote(sentence))
        if _WORKING_ENGLISH.search(sentence) and not _NEGATION.search(sentence):
            working_english = True
            quotes.setdefault("english_working_language", _quote(sentence))
        if _JUNIOR_STATEMENT.search(sentence) and not ambiguous:
            junior = True
            quotes.setdefault("junior_statement", _quote(sentence))
    comp = (normalize_structured_compensation(detail.structured_compensation)
            if detail.structured_compensation else
            extract_compensation(detail.text) or extract_compensation_nl(detail.text))
    experience, education = extract_requirements(detail.text)
    contradictions = ("sponsorship_statements_conflict",) if no_s and yes_s else ()
    return DetailEvidence(
        vacancy_open=detail.active,
        explicit_no_sponsorship=True if no_s else None,
        explicit_sponsorship_offered=True if yes_s else None,
        requires_unlimited_work_right=True if unlimited else None,
        generic_right_to_work_required=True if generic else None,
        junior_statement=True if junior else None,
        mandatory_languages=tuple(sorted(languages)),
        english_working_language=True if working_english else None,
        compensation=comp, quotes=dict(sorted(quotes.items())), contradictions=contradictions,
        hedged=tuple(sorted(set(hedged))), language_levels={k: levels[k] for k in sorted(levels) if k in languages},
        experience_requirements=experience, education_requirements=education)
