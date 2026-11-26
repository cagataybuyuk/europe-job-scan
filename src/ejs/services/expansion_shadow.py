"""Project the durable synthetic country replay into a shadow discovery report."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping

from ejs.services.criteria_loader import Criteria
from ejs.services.discovery import AtsClass, classify_url
from ejs.services.expansion_replay import COUNTRIES, RESEARCH, Checkpoint, live_page_url
from ejs.services.shadow_run import SCHEMA as DISCOVERY_SCHEMA, build_shadow_report

SCHEMA = "EJS-EXPANSION-SHADOW-REPORT-1"
_SCOPE = {name: tenant for name, tenant, _, _ in RESEARCH}


def validate_watchlist(rows: list[dict[str, str]]) -> dict[str, str]:
    """Bind the existing CSV format to the exact synthetic research scope."""
    markets: dict[str, str] = {}
    for row in rows:
        tenant = _SCOPE.get(row["employer"])
        if (tenant is None or tenant in markets or not row["market"].isprintable()
                or classify_url(row["url"]) != AtsClass("smartrecruiters", tenant)):
            raise ValueError("EXPANSION_WATCHLIST_SCOPE_MISMATCH")
        markets[tenant] = row["market"]
    if len(markets) != len(_SCOPE):
        raise ValueError("EXPANSION_WATCHLIST_SCOPE_MISMATCH")
    return markets


def build_expansion_report(checkpoint: Checkpoint, listings: list[dict[str, str]],
                           markets: dict[str, str], criteria: Criteria, *,
                           live_requests: Mapping[str, int] | None = None,
                           request_log: list[dict] | None = None,
                           rechecks: list[dict] | None = None) -> dict:
    """Report cumulative coverage and evidence from a validated bundle snapshot.

    ``live_requests`` (employer tenant -> requests made by this invocation) marks a report built from the
    live country feed; omit it for a synthetic replay. ``request_log`` (url, status, bytes) and ``rechecks``
    (first-page drift checks) are recorded as observed. A live report never claims an atomic snapshot.
    """
    live = live_requests is not None
    failed_tenants = {cursor.tenant: cursor.error for cursor in checkpoint.cursors
                      if cursor.status == "error"}
    pair_counts = dict.fromkeys(("completed", "incomplete", "blocked", "failed"), 0)
    pairs = []
    pending_tenants = set()
    countries: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cursor in checkpoint.cursors:
        if cursor.status == "complete":
            status, error = "completed", None
        elif cursor.status == "error":
            status, error = "failed", cursor.error
        elif cursor.tenant in failed_tenants:
            status, error = "blocked", "prior_country_failed"
        else:
            status, error = "incomplete", None
            pending_tenants.add(cursor.tenant)
        pair_counts[status] += 1
        pairs.append({"tenant": cursor.tenant, "country": cursor.country, "status": status,
                      "error_kind": error, "accepted_listings": cursor.offset,
                      "declared_total": cursor.total})
        for posting_id in cursor.seen:
            countries[cursor.tenant, posting_id].append(cursor.country)

    def evidence(item: dict[str, str]) -> dict:
        seen_in = countries[item["tenant"], item["posting_id"]]
        return {"observation": "live_country_feed" if live else "synthetic_country_replay",
                "feed_url": live_page_url(item["tenant"], seen_in[0], 0) if live else None,
                "countries": seen_in}

    per_employer = Counter(item["tenant"] for item in listings)
    employers = [
        {"employer": name, "market": markets[tenant], "ats": "smartrecruiters",
         "tenant": tenant, "region": "global", "listings": per_employer[tenant],
         "requests": live_requests.get(tenant, 0) if live else 0,
         "status": ("failed" if tenant in failed_tenants else
                    "incomplete" if tenant in pending_tenants else "completed"),
         "error_kind": failed_tenants.get(tenant)}
        for name, tenant, _, _ in RESEARCH
    ]
    # Cursor order is stable even when the replay is partitioned into different batches.
    by_identity = {(item["tenant"], item["posting_id"]): item for item in listings}
    report = build_shadow_report(employers, [], [by_identity[key] for key in countries], criteria,
                                 evidence_for=evidence, vacancy_open=None)
    parsed = sum(cursor.offset for cursor in checkpoint.cursors)
    if live:
        report["request_log"] = [{k: entry[k] for k in ("url", "status", "bytes")} for entry in request_log or []]
        report["snapshot_consistency"] = {
            "atomic_snapshot": False,
            "statement": "Pages were read sequentially; completeness is per observed page and total, not a "
                         "point-in-time snapshot. Unobserved changes cannot be excluded.",
            "first_page_rechecks": list(rechecks or []),
        }
    report.update(
        schema=SCHEMA, transport_mode="live_country_feed" if live else "offline_expansion",
        network_requests=sum(live_requests.values()) if live else 0,
        requests_scope="this_invocation" if live else "none",
        listings_parsed=parsed, listings_duplicate=parsed - len(listings),
        coverage={"scope_version": checkpoint.scope_version, "countries": list(COUNTRIES),
                  "status": ("failed" if failed_tenants else
                             "incomplete" if pending_tenants else "completed"),
                  "counts": pair_counts, "pairs": pairs},
        sponsorship_eligibility="unknown", application_readiness="unverified",
        execution_authorized=False,
        not_verified=["Kapsam, onaylı işveren ve ülke sayfalarının okunmasını gösterir." if live else
                      "Kapsam, sentetik işveren ve ülke sayfalarının okunmasını gösterir.",
                      "İlanların güncelliği, sponsorluk uygunluğu ve başvuru erişilebilirliği doğrulanmadı.",
                      "Bu rapor yürütme yetkisi vermez."],
    )
    return report


def project_for_application_bridge(report: dict) -> dict:
    """Minimal W2-compatible view of a verified live expansion report; the bridge itself is unchanged.

    Refuses synthetic reports, any failed pair, any non-stable drift check and anything that is not a
    non-executable live report. The projection says explicitly that it is not an atomic snapshot.
    """
    consistency = report.get("snapshot_consistency") if isinstance(report, dict) else None
    coverage = report.get("coverage") if isinstance(report, dict) else None
    vacancies = report.get("vacancies") if isinstance(report, dict) else None
    if (not isinstance(consistency, dict) or not isinstance(coverage, dict) or not isinstance(vacancies, list)
            or report.get("schema") != SCHEMA or report.get("transport_mode") != "live_country_feed"
            or report.get("execution_authorized") is not False
            or consistency.get("atomic_snapshot") is not False
            or coverage.get("counts", {}).get("failed") != 0 or coverage.get("status") == "failed"
            or report.get("listings_kept") != len(vacancies)
            or any(c.get("status") not in ("stable", "not_applicable")
                   for c in consistency.get("first_page_rechecks", ()))
            or not {(p["tenant"], p["country"]) for p in coverage.get("pairs", ())
                    if p["status"] == "completed" and p["accepted_listings"]}
            <= {(c.get("tenant"), c.get("country")) for c in consistency.get("first_page_rechecks", ())
                if c.get("status") == "stable"}
            or any(not isinstance(v.get("discovery_evidence"), dict)
                   or v["discovery_evidence"].get("observation") != "live_country_feed" for v in vacancies)):
        raise ValueError("EXPANSION_PROJECTION_REFUSED")
    return {"schema": DISCOVERY_SCHEMA, "mode": "shadow_no_submit", "source_schema": SCHEMA,
            "projection": "verified_live_country_feed", "atomic_snapshot": False,
            "coverage_status": coverage["status"], "listings_kept": len(vacancies),
            "vacancies": [dict(v) for v in vacancies]}
