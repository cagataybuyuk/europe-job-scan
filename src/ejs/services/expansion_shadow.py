"""Project the durable synthetic country replay into a shadow discovery report."""
from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlsplit

from ejs.services.criteria_loader import Criteria
from ejs.services.discovery import AtsClass, classify_url
from ejs.services.expansion_replay import (
    COUNTRIES, LIVE_BUNDLE_MAX_REQUESTS, RESEARCH, Checkpoint, effective_rechecks, live_page_url,
    remaining_requests, spent_requests,
)
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


def _request_accounting(evidence: dict) -> tuple[list[dict], dict, Counter]:
    log: list[dict] = []
    per_tenant: Counter = Counter()
    for call in evidence["invocations"]:
        for request in call["requests"]:
            log.append({"invocation": call["seq"], **request})
            parts = urlsplit(request["url"]).path.split("/")
            if len(parts) > 3:
                per_tenant[parts[3]] += 1
    summary = {
        "scope": "bundle_cumulative", "budget": min(evidence["budget"], LIVE_BUNDLE_MAX_REQUESTS),
        "spent": spent_requests(evidence), "remaining": remaining_requests(evidence),
        "uncertain_reserved": sum(c["reserved"] for c in evidence["invocations"]
                                  if c["status"] == "interrupted_unverified")
                              + (evidence["in_flight"]["reserved"] if evidence["in_flight"] else 0),
        "invocations": [{"seq": c["seq"], "status": c["status"], "reserved": c["reserved"],
                         "requests": len(c["requests"])} for c in evidence["invocations"]],
    }
    return log, summary, per_tenant


def build_expansion_report(checkpoint: Checkpoint, listings: list[dict[str, str]],
                           markets: dict[str, str], criteria: Criteria, *,
                           evidence: dict | None = None,
                           approved: frozenset[tuple[str, str]] | None = None) -> dict:
    """Report cumulative coverage and evidence from a validated bundle snapshot.

    ``evidence`` (the bundle's durable live evidence) marks a report built from the live country feed; omit it for
    a synthetic replay. Request accounting and rechecks come only from that durable evidence, so a rebuilt report
    never loses them. ``approved`` is the owner-approved subset of the research pairs: coverage status is measured
    over it, and every other pair is reported as ``not_approved`` instead of incomplete. A live report never claims
    an atomic snapshot.
    """
    live = evidence is not None
    in_scope = (lambda pair: True) if approved is None else (lambda pair: pair in approved)
    failed_tenants = {cursor.tenant: cursor.error for cursor in checkpoint.cursors
                      if cursor.status == "error" and in_scope((cursor.tenant, cursor.country))}
    pair_counts = dict.fromkeys(("completed", "incomplete", "blocked", "failed"), 0)
    if approved is not None:
        pair_counts["not_approved"] = 0
    pairs = []
    pending_tenants, scoped_tenants = set(), set()
    countries: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cursor in checkpoint.cursors:
        pair = (cursor.tenant, cursor.country)
        if not in_scope(pair):
            status, error = "not_approved", None
        else:
            scoped_tenants.add(cursor.tenant)
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

    def observed(item: dict[str, str]) -> dict:
        seen_in = countries[item["tenant"], item["posting_id"]]
        return {"observation": "live_country_feed" if live else "synthetic_country_replay",
                "feed_url": live_page_url(item["tenant"], seen_in[0], 0) if live else None,
                "countries": seen_in}

    log, accounting, requests_by_tenant = _request_accounting(evidence) if live else ([], None, Counter())
    per_employer = Counter(item["tenant"] for item in listings)
    employers = [
        {"employer": name, "market": markets[tenant], "ats": "smartrecruiters",
         "tenant": tenant, "region": "global", "listings": per_employer[tenant],
         "requests": requests_by_tenant[tenant],
         "status": ("not_approved" if tenant not in scoped_tenants else
                    "failed" if tenant in failed_tenants else
                    "incomplete" if tenant in pending_tenants else "completed"),
         "error_kind": failed_tenants.get(tenant)}
        for name, tenant, _, _ in RESEARCH
    ]
    # Cursor order is stable even when the replay is partitioned into different batches.
    by_identity = {(item["tenant"], item["posting_id"]): item for item in listings}
    report = build_shadow_report(employers, [], [by_identity[key] for key in countries], criteria,
                                 evidence_for=observed, vacancy_open=None)
    parsed = sum(cursor.offset for cursor in checkpoint.cursors)
    coverage = {"scope_version": checkpoint.scope_version, "countries": list(COUNTRIES),
                "status": ("failed" if failed_tenants else
                           "incomplete" if pending_tenants else "completed"),
                "counts": pair_counts, "pairs": pairs}
    if approved is not None:
        completed = pair_counts["completed"]
        coverage["measured_over"] = "approved_scope"
        coverage["research_scope"] = {
            "total_pairs": len(checkpoint.cursors), "approved_pairs": len(checkpoint.cursors) - pair_counts["not_approved"],
            "completed_pairs": completed, "not_approved_pairs": pair_counts["not_approved"],
            "research_coverage_complete": completed == len(checkpoint.cursors)}
    if live:
        recheck_state = effective_rechecks(evidence)
        # Collection finished is not acceptance: every completed non-empty pair also needs a stable recheck.
        unverified = [{"tenant": p["tenant"], "country": p["country"],
                       "recheck": recheck_state.get((p["tenant"], p["country"]), {"status": "missing"})["status"]}
                      for p in pairs if p["status"] == "completed" and p["accepted_listings"]
                      and recheck_state.get((p["tenant"], p["country"]), {}).get("status") != "stable"]
        report["acceptance"] = {
            "measured_over": "approved_scope", "collection_complete": coverage["status"] == "completed",
            "verification_complete": not unverified, "unverified_pairs": unverified,
            "ready": coverage["status"] == "completed" and not unverified}
        report["request_log"] = log
        report["request_accounting"] = accounting
        report["snapshot_consistency"] = {
            "atomic_snapshot": False,
            "statement": "Pages were read sequentially; completeness is per observed page and total, not a "
                         "point-in-time snapshot. Unobserved changes cannot be excluded.",
            "first_page_rechecks": [{"tenant": t, "country": c, **state}
                                    for (t, c), state in sorted(recheck_state.items())],
            "recheck_history": list(evidence["rechecks"]),
        }
    report.update(
        schema=SCHEMA, transport_mode="live_country_feed" if live else "offline_expansion",
        network_requests=accounting["spent"] if live else 0,
        requests_scope="bundle_cumulative" if live else "none",
        listings_parsed=parsed, listings_duplicate=parsed - len(listings),
        coverage=coverage,
        sponsorship_eligibility="unknown", application_readiness="unverified",
        execution_authorized=False,
        not_verified=["Kapsam, onaylı işveren ve ülke sayfalarının okunmasını gösterir." if live else
                      "Kapsam, sentetik işveren ve ülke sayfalarının okunmasını gösterir.",
                      "İlanların güncelliği, sponsorluk uygunluğu ve başvuru erişilebilirliği doğrulanmadı.",
                      "Bu rapor yürütme yetkisi vermez."],
    )
    return report


def project_for_application_bridge(report: dict) -> dict:
    """Minimal W2-compatible view of a live expansion report; the bridge itself is unchanged.

    Only rows whose every observed country pair is verified are projected: the pair is completed and its
    first-page recheck is stable. Rows from pending, failed, blocked, unapproved or drifting pairs, including a
    row that was also seen in such a pair, are excluded and counted. Synthetic reports, executable reports,
    atomic-snapshot claims and inconsistent counts are refused. The projection never claims an atomic snapshot.
    """
    consistency = report.get("snapshot_consistency") if isinstance(report, dict) else None
    coverage = report.get("coverage") if isinstance(report, dict) else None
    vacancies = report.get("vacancies") if isinstance(report, dict) else None
    if (not isinstance(consistency, dict) or not isinstance(coverage, dict) or not isinstance(vacancies, list)
            or report.get("schema") != SCHEMA or report.get("transport_mode") != "live_country_feed"
            or report.get("execution_authorized") is not False
            or consistency.get("atomic_snapshot") is not False
            or report.get("listings_kept") != len(vacancies)
            or any(not isinstance(v.get("discovery_evidence"), dict)
                   or v["discovery_evidence"].get("observation") != "live_country_feed" for v in vacancies)):
        raise ValueError("EXPANSION_PROJECTION_REFUSED")
    status = {(p["tenant"], p["country"]): p["status"] for p in coverage.get("pairs", ())}
    stable = {(c["tenant"], c["country"]) for c in consistency.get("first_page_rechecks", ())
              if c.get("status") == "stable"}
    kept: list[dict] = []
    excluded: Counter = Counter()
    for vacancy in vacancies:
        pairs = [(vacancy["tenant"], country) for country in vacancy["discovery_evidence"].get("countries", ())]
        if not pairs or any(status.get(pair) != "completed" for pair in pairs):
            excluded["provenance_pair_not_completed"] += 1
        elif any(pair not in stable for pair in pairs):
            excluded["provenance_pair_recheck_not_stable"] += 1
        else:
            kept.append(dict(vacancy))
    return {"schema": DISCOVERY_SCHEMA, "mode": "shadow_no_submit", "source_schema": SCHEMA,
            "projection": "verified_live_country_feed", "atomic_snapshot": False,
            "coverage_status": coverage.get("status"), "listings_kept": len(kept),
            "excluded_rows": {"count": sum(excluded.values()), "reasons": dict(sorted(excluded.items()))},
            "vacancies": kept}
