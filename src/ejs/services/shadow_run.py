"""Shadow discovery run (EJS-W1 Faz A): read -> normalise -> score -> compare -> report. Nothing is submitted.

Pure orchestration over Faz 1a/1b modules plus an injected ``fetch`` (normally ``HttpCarrier``). No submission,
ledger, runtime-config or Sheet-write code is imported. The report carries NO timestamps so that two runs over the
same fixtures give byte-identical JSON. Listing ratings are all Unknown: these adapters extract no
evidence for sponsorship/compensation/etc., and nothing is inferred - so labels are honest ``needs_user``.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from ejs.services.autoreview import ListingFacts, decide
from ejs.services.criteria_loader import SCORE_COMPONENTS, Criteria
from ejs.services.dedupe import dedupe_key
from ejs.services.discovery import (WatchlistRow, classify_url, discover_pages)
from ejs.services.shadow_compare import compare_decisions

SCHEMA = "EJS-SHADOW-REPORT-2"
LISTING_FIELDS = ("company", "role", "location", "url", "ats_kind", "source")
VACANCY_FIELDS = (*LISTING_FIELDS, "tenant", "region", "posting_id")
_SUPPORTED = ("greenhouse", "lever", "smartrecruiters")
_NATIONAL_HOSTS = ("nationalevacaturebank.nl", "irishjobs.ie", "jobs.ie", "werkzoeken.nl", "stepstone.de", "monster.")
_BLOCKERS = {
    "workday": "Workday adaptörü yok",
    "recruitee": "Recruitee adaptörü yok",
    "workable": "Workable adaptörü yok",
    "hirehive": "HireHive adaptörü yok",
    "successfactors_like": "SuccessFactors benzeri (URL tahmini, doğrulanmadı); adaptör yok",
    "linkedin_indeed": "Toplu okuma yasak (K4: LinkedIn/Indeed); işveren sitesi adresi gerekir",
    "national_board": "Ulusal ilan sitesi; işveren ATS adresi gerekir",
    "custom": "Özel kariyer sitesi; genel ATS API'si yok",
    "greenhouse": "Greenhouse slug yok", "lever": "Lever slug yok", "smartrecruiters": "SmartRecruiters slug yok",
    "invalid": "Geçersiz/https olmayan URL",
}


def read_watchlist(path: str | Path) -> list[dict[str, str]]:
    """Reads ONLY Employer / Primary Market / Reference URL from the table that starts at the header row."""
    rows: list[dict[str, str]] = []
    in_table = False
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, strict=True)
        for r in reader:
            cells = [c.strip() for c in r] + ["", "", ""]
            if not in_table:
                if cells[0] == "Employer" and cells[1] == "Primary Market" and cells[2].startswith("Reference"):
                    in_table = True
                continue
            if not any(cells):
                break
            if not all(cells[:3]):
                raise ValueError(f"watchlist row {reader.line_num}: employer, market and reference URL are required")
            if classify_url(cells[2]).kind == "invalid":
                raise ValueError(f"watchlist row {reader.line_num}: a valid HTTPS reference URL is required")
            rows.append({"employer": cells[0], "market": cells[1], "url": cells[2]})
    if not in_table:
        raise ValueError("watchlist header is missing")
    return rows


def pending_class(url: str) -> str:
    """Report-only refinement of ``classify_url`` for employers with no adapter. Guesses are labelled as such."""
    base = classify_url(url).kind
    if base in _SUPPORTED or base in ("invalid", "workday", "workable"):
        return base
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path
    if base == "blocked_aggregator":
        return "linkedin_indeed"
    if host.endswith("recruitee.com"):
        return "recruitee"
    if host.endswith("hirehive.com"):
        return "hirehive"
    if "successfactors" in host or "sapsf" in host:
        return "successfactors_like"
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "job" and parts[-1].isdigit():
        return "successfactors_like"
    if any(host == h or host.endswith("." + h) or h.endswith(".") and h in host for h in _NATIONAL_HOSTS):
        return "national_board"
    return "custom"


def _completeness(listings: list[Mapping[str, str]]) -> dict[str, Any]:
    n = len(listings)
    per = {f: (sum(1 for x in listings if (x.get(f) or "").strip()) / n if n else None) for f in LISTING_FIELDS}
    full = sum(1 for x in listings if all((x.get(f) or "").strip() for f in LISTING_FIELDS))
    return {"per_field": per, "all_six_fields_ratio": (full / n if n else None)}


def run_shadow(rows: list[dict[str, str]], criteria: Criteria, *, fetch: Callable[[str], Any] | None,
               begin_employer: Callable[[str], None] | None = None,
               request_counts: Callable[[], Mapping[str, int]] | None = None,
               sheet_labels: Mapping[str, str] | None = None) -> dict[str, Any]:
    employers: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    listings: list[dict[str, str]] = []
    for r in rows:
        cls = classify_url(r["url"])
        entry: dict[str, Any] = {"employer": r["employer"], "market": r["market"], "ats": cls.kind,
                                 "tenant": cls.slug, "region": cls.region}
        if cls.kind not in _SUPPORTED or not cls.slug:
            kind = pending_class(r["url"])
            entry.update(status="pending_adapter", error_kind=None, listings=0)
            employers.append(entry)
            pending.append({"employer": r["employer"], "ats_class": kind, "blocker": _BLOCKERS.get(kind, "adaptör yok")})
            continue
        if begin_employer:
            begin_employer(r["employer"])
        result = discover_pages(WatchlistRow(r["employer"], r["url"]), fetch)
        entry.update(status=result.status, error_kind=result.error_kind, listings=len(result.listings))
        listings.extend(result.listings)
        employers.append(entry)
    counts = request_counts() if request_counts else {}
    for e in employers:
        e["requests"] = int(counts.get(e["employer"], 0))

    vacancies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    duplicates = unkeyed = 0
    decisions: dict[str, str] = {}
    labels: Counter = Counter()
    reasons: Counter = Counter()
    for item in listings:
        posting_id = item["posting_id"]
        identity = (item["ats_kind"], item["region"], item["tenant"], posting_id)
        if posting_id and identity in seen:
            duplicates += 1
            continue
        if posting_id:
            seen.add(identity)
        else:
            unkeyed += 1
        vacancy = {field: item[field] for field in VACANCY_FIELDS}
        vacancy["posting_id"] = posting_id or None
        vacancy["url"] = item["url"] or None
        vacancy["identity_status"] = "known" if posting_id else "unknown"
        vacancy["similarity_key"] = dedupe_key(item["company"], item["role"], item["location"])
        vacancy["discovery_evidence"] = {"feed_url": item["discovered_via"], "observation": "public_listing_feed"}
        decision = decide(ListingFacts(item["url"] or f"unknown-{len(vacancies) + 1}",
                                      {c: None for c in SCORE_COMPONENTS}, vacancy_open=True), criteria)
        vacancy["criteria_evaluation"] = {"label": decision.label, "reason": decision.reason_code,
                                          "rule_version": decision.rule_version, "score": decision.score,
                                          "unknown_components": list(decision.unknown_components)}
        vacancies.append(vacancy)
        if item["url"]:
            decisions[item["url"]] = decision.label
        labels[decision.label] += 1
        reasons[decision.reason_code] += 1

    cmp_report: dict[str, Any] | None = None
    if sheet_labels is not None:
        diff = compare_decisions(decisions, dict(sheet_labels))
        cmp_report = {"agree": len(diff.agree), "disagree": [list(x) for x in diff.disagree],
                      "only_ejs": len(diff.only_ejs), "only_sheet": list(diff.only_sheet),
                      "agreement_rate": diff.agreement_rate}

    supported = [e for e in employers if e["status"] != "pending_adapter"]
    return {
        "schema": SCHEMA,
        "rule_version": criteria.rule_version,
        "mode": "shadow_no_submit",
        "employers_total": len(employers),
        "employers_supported": len(supported),
        "employers_pending_adapter": len(pending),
        "employers": employers,
        "listings_parsed": len(listings),
        "listings_kept": len(vacancies),
        "listings_duplicate": duplicates,
        "listings_unkeyed": unkeyed,
        "vacancies": vacancies,
        "completeness": _completeness(listings),
        "label_distribution": dict(sorted(labels.items())),
        "reason_distribution": dict(sorted(reasons.items())),
        "sheet_comparison": cmp_report,
        "pending_employers": pending,
        "not_verified": [
            "İlan detay sayfası ve başvuru erişilebilirliği ayrıca kontrol edilmedi.",
            "Tam watchlist kapsamı SAYILMAZ: yalnız desteklenen ATS adaptörlü işverenler okunur.",
            "Tüm puan bileşenleri Unknown: liste uçları sponsorluk/maaş kanıtı vermez; çıkarım yapılmaz.",
            "SuccessFactors benzeri sınıf URL biçimi tahminidir.",
        ],
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    L = [f"# EJS-W1 gölge keşif raporu ({rep['schema']})", "",
         f"Mod: `{rep['mode']}` · kural: `{rep['rule_version']}` · gönderim YOK",
         f"İşveren: {rep['employers_total']} toplam, {rep['employers_supported']} desteklenen ATS, "
         f"{rep['employers_pending_adapter']} adaptör bekliyor.", "",
         "## İşveren okuma durumu", "", "| İşveren | ATS | Durum | Hata sınıfı | İstek | İlan |", "|---|---|---|---|---|---|"]
    for e in rep["employers"]:
        if e["status"] != "pending_adapter":
            L.append(f"| {e['employer']} | {e['ats']} | {e['status']} | {e['error_kind'] or '-'} | {e['requests']} | {e['listings']} |")
    c = rep["completeness"]
    L += ["", f"## İlanlar", "", f"Ayrıştırılan {rep['listings_parsed']}, tekil {rep['listings_kept']}, "
          f"yinelenen {rep['listings_duplicate']}, anahtarsız {rep['listings_unkeyed']}.",
          f"6 alan tamlığı (hepsi dolu): {c['all_six_fields_ratio']}", "",
          "Alan başına: " + ", ".join(f"{k}={v}" for k, v in c["per_field"].items()), "",
          "## Etiket dağılımı", "", json.dumps(rep["label_distribution"], sort_keys=True),
          "", json.dumps(rep["reason_distribution"], sort_keys=True), "", "## Sheet karşılaştırması", "",
          json.dumps(rep["sheet_comparison"], sort_keys=True) if rep["sheet_comparison"] else "Sheet etiketi verilmedi.",
          "", f"## Adaptör bekleyen işverenler ({rep['employers_pending_adapter']})", "",
          "| İşveren | ATS sınıfı | Engelleyici |", "|---|---|---|"]
    L += [f"| {p['employer']} | {p['ats_class']} | {p['blocker']} |" for p in rep["pending_employers"]]
    L += ["", "## Doğrulanmadı", ""] + [f"- {x}" for x in rep["not_verified"]]
    return "\n".join(L) + "\n"
