"""FUNCTION-05 acceptance tests. Synthetic criteria, listings and posting bytes only. No real network, ever."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import socket
import threading
import stat
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from ejs.services import detail_verification as dv
from ejs.services.detail_verification import (AUTHORITY, DETAIL_HOST, DetailVerificationError, build_allowlist,
                                               build_handoff, build_report, check_lineage, detail_url,
                                               request_details, reserve_budget, verify_allowlist, verify_details)
from ejs.services.discovery import DiscoveryError
from ejs.services.http_carrier import (REQUEST_HEADERS, HostNotAllowedError, HttpCarrier, ReplayCarrier,
                                       urllib_transport)
from ejs.services.standing_pilot_pool import ERROR as PILOT_POOL_ERROR, RankedEligibleTarget, select_pilot_pool
from ejs.services.suitability import canonical_sha256, evaluate_report
from ejs.services.suitability_evidence import detail_from_smartrecruiters
from tests.test_function04_suitability import criteria, criteria_text, report

TENANT = "Acme"
SHA = sha256(criteria_text().encode()).hexdigest()
RULE = criteria().rule_version
F04_SHA = "b" * 64
F02_FILE_SHA = "a" * 64
NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
EN_DESCRIPTION = ("<p>In this role you will work with our clients and the delivery team to define the scope of the project, "
                  "and you will be responsible for the quality of the results that we deliver to them.</p>"
                  "<ul><li>Run workshops.</li><li>Write specifications.</li></ul>")
APPLY_TEXT = ("<p>Visa sponsorship is available.</p><p>Working language is English.</p>"
              "<p>Salary: €4,500 per month net.</p>")


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network attempted during offline Function 05 regression")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


# ------------------------------------------------------------------------------------------ fixtures
def pid(i: int) -> str:
    return str(100000000000000 + i)


def pilot_candidate(rank: int, country: str, posting: int | None = None) -> RankedEligibleTarget:
    number = rank if posting is None else posting
    posting_id = pid(number)
    return RankedEligibleTarget(rank, country, dict(
        ats_family="smartrecruiters", company=TENANT, posting_id=posting_id,
        requisition_id=f"REF{number}", opportunity_key=f"opp:synthetic-{number}",
        apply_url=f"https://jobs.smartrecruiters.com/{TENANT}/{posting_id}",
    ))


def test_pilot_pool_pins_country_from_the_highest_ranked_eligible_vacancy():
    first = pilot_candidate(1, "NL")
    later = pilot_candidate(4, "NL")
    pool = select_pilot_pool([later, pilot_candidate(2, "DE"), first, pilot_candidate(3, "DE")])
    assert pool.country == "NL"
    assert pool.targets == (first.target, later.target)


def test_pilot_pool_limits_the_selected_country_to_five_targets_in_rank_order():
    eligible = [pilot_candidate(rank, "NL") for rank in range(1, 8)]
    pool = select_pilot_pool(tuple(reversed(eligible)))
    assert pool.targets == tuple(candidate.target for candidate in eligible[:5])
    assert select_pilot_pool([]) is None


def test_pilot_pool_rejects_ambiguous_ranks_duplicate_vacancies_and_invalid_country_or_target():
    first = pilot_candidate(1, "NL")
    ambiguous = [pilot_candidate(1, "DE", posting=2), pilot_candidate(2, "DE", posting=1),
                 RankedEligibleTarget(2, "NL", {**first.target, "company": first.target["company"].lower()}),
                 RankedEligibleTarget(2, "NL", {**first.target, "apply_url": first.target["apply_url"] + "-role"}),
                 pilot_candidate(2, "Unknown"), RankedEligibleTarget(2, "NL", {**first.target, "apply_url": "*"})]
    for candidate in ambiguous:
        with pytest.raises(PermissionError, match=f"^{PILOT_POOL_ERROR}$"):
            select_pilot_pool([first, candidate])


def row(i: int, role: str = "Senior Business Analyst", *, country: str | list[str] | None = "NL", url: str | None = None,
        ats: str = "smartrecruiters", tenant: str = TENANT, region: str = "global") -> dict:
    return {"company": "Acme", "role": role, "location": f"City{i}", "url": url if url is not None else
            f"https://jobs.smartrecruiters.com/{tenant}/{pid(i)}", "ats_kind": ats, "source": "employer_watchlist",
            "tenant": tenant, "region": region, "posting_id": pid(i), "identity_status": "known",
            "similarity_key": f"acme|{role.casefold()}|city{i}",
            "discovery_evidence": {"observation": "live_country_feed",
                                   "feed_url": (f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings"
                                                f"?country={(country if isinstance(country, str) else (country or [''])[0]).lower()}"
                                                "&offset=0&limit=100"),
                                   "countries": [country] if isinstance(country, str) else (country or [])}}


class F04(dict):
    """A Function 04 report that remembers the (verified original) Function 02 projection it came from."""
    projection: dict


def f04_report(rows: list[dict]) -> F04:
    projection = report(*rows)
    out = F04(evaluate_report(projection, criteria(), criteria_sha256=SHA, expected_criteria_sha256=SHA,
                              expected_rule_version=RULE))
    out.projection = projection
    return out


def lineage_of(f04: dict) -> dict:
    return {"f02_source_file_sha256": F02_FILE_SHA, "f02_projection_sha256": f04["source_report_sha256"]}


def world(highs: int = 5, *, extra: list[dict] | None = None) -> tuple[dict, dict]:
    rows = [row(i) for i in range(1, highs + 1)] + [row(50, "Data Analyst"), row(60, "Accountant")] + (extra or [])
    f04 = f04_report(rows)
    return f04, build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04))


def sr(i: int, *, active=True, ref="REQ-1", text=APPLY_TEXT, tenant=TENANT, posting=None, posting_url="default",
       description=EN_DESCRIPTION, qualifications="<ul><li>You must have 3 years of ERP experience.</li><li>Dutch is a plus.</li></ul>") -> bytes:
    p = posting or pid(i)
    data = {"id": p, "company": {"identifier": tenant}, "active": active,
            "jobAd": {"sections": {"jobDescription": {"text": description},
                                   "qualifications": {"text": qualifications},
                                   "additionalInformation": {"text": text}}}}
    if ref is not None:
        data["refNumber"] = ref
    if posting_url == "default":
        data["postingUrl"] = f"https://jobs.smartrecruiters.com/{tenant}/{p}-senior-business-analyst"
    elif posting_url is not None:
        data["postingUrl"] = posting_url
    return json.dumps(data).encode()


class Net:
    """Recording fake transport; the only thing that 'answers' a request."""

    def __init__(self, responses: dict):
        self.responses, self.calls = responses, []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        answer = self.responses[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.001
        return self.t


def ok(raw: bytes):
    return 200, raw


def run(f04: dict, allow: dict, responses: dict, *, max_requests: int | None = None, real: bool = False):
    net = Net(responses)
    sleeps: list[float] = []
    clock = Clock()
    hosts = frozenset({DETAIL_HOST})
    n = len(allow["targets"])
    if real:
        carrier = HttpCarrier(enabled=True, transport=net, clock=clock, sleep=sleeps.append, max_requests=max_requests or n,
                              max_pages=5, allowed_hosts=hosts)
    else:
        carrier = ReplayCarrier(transport=net, clock=clock, max_requests=max_requests or n, max_pages=5, allowed_hosts=hosts)
    observations = request_details(allow, carrier, now=lambda: NOW)
    verification = verify_details(f04.projection, observations, criteria(), criteria_sha256=SHA,
                                  expected_criteria_sha256=SHA, expected_rule_version=RULE)
    return net, carrier, observations, verification, sleeps


def url_of(i: int) -> str:
    return detail_url(TENANT, pid(i))


def by_posting(verification: dict) -> dict:
    return {c["posting_id"]: c for c in verification["candidates"]}


# ------------------------------------------------------------------ 1. selection keeps Function 04's priority
def test_selects_five_preliminary_high_in_f04_priority_order():
    f04, allow = world(highs=6)
    assert [t["posting_id"] for t in allow["targets"]] == [pid(i) for i in range(1, 6)]
    assert [t["f04_priority_rank"] for t in allow["targets"]] == [1, 2, 3, 4, 5]
    assert allow["selection"] == {"rule": "preliminary_high_in_f04_priority_rank_order", "limit": 5,
                                  "high_rows_in_report": 6, "selected": 5, "unbound": []}
    assert allow["request_policy"]["max_requests"] == 5
    classes = {v["posting_id"]: v["suitability"]["classification"] for v in f04["vacancies"]}
    assert all(classes[t["posting_id"]] == "preliminary_high" for t in allow["targets"])
    assert pid(50) not in classes or classes[pid(50)] != "preliminary_high"


def test_priority_order_wins_over_the_w2_score_sort():
    # Equal scores: W2 would order by posting id (smallest first). Function 04 ranks the Ireland row last (geography
    # tie-break), so the explicit Function 04 priority drops it from a five-row shortlist.
    f04 = f04_report([row(1, country="IE")] + [row(i) for i in range(2, 8)])
    allow = build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04))
    chosen = [t["posting_id"] for t in allow["targets"]]
    assert pid(1) not in chosen and chosen == [pid(i) for i in range(2, 7)]
    ranks = {v["posting_id"]: v["priority_rank"] for v in f04["vacancies"]}
    assert [ranks[p] for p in chosen] == sorted(ranks[p] for p in chosen)


def test_fewer_than_five_high_rows_yield_fewer_targets_and_never_pad_with_medium():
    f04, allow = world(highs=3)
    assert len(allow["targets"]) == 3 and allow["request_policy"]["max_requests"] == 3


def test_every_target_is_bound_to_ats_tenant_posting_and_canonical_url():
    _, allow = world()
    for t in allow["targets"]:
        assert (t["ats"], t["region"], t["tenant"]) == ("smartrecruiters", "global", TENANT)
        assert t["canonical_url"] == f"https://jobs.smartrecruiters.com/{TENANT}/{t['posting_id']}"
        assert t["detail_url"] == detail_url(TENANT, t["posting_id"]) == \
            f"https://api.smartrecruiters.com/v1/companies/{TENANT}/postings/{t['posting_id']}"
    assert len({t["detail_url"] for t in allow["targets"]}) == len(allow["targets"])


def test_allowlist_is_non_executable_reviewable_and_digest_bound():
    _, allow = world()
    assert allow["executable"] is False and allow["submit_authorized"] is False
    assert all(v is False for k, v in allow["authority"].items() if k != "label_is_not_authority")
    policy = allow["request_policy"]
    assert (policy["method"], policy["scheme"], policy["host"], policy["retries"], policy["redirects"],
            policy["authentication"], policy["min_interval_s"], policy["each_url_at_most_once"]) == \
        ("GET", "https", DETAIL_HOST, 0, "refused", "none", 1.0, True)
    assert allow["approval"]["status"] == "awaiting_owner_approval"
    assert allow["approval"]["allowlist_sha256"] == dv.allowlist_digest(allow)
    f04b = world()[0]
    again = build_allowlist(f04b, f04_report_sha256=F04_SHA, lineage=lineage_of(f04b))
    assert json.dumps(again, sort_keys=True) == json.dumps(allow, sort_keys=True)


@pytest.mark.parametrize("kwargs,reason", [
    (dict(ats="greenhouse"), "unsupported_ats"),
    (dict(region="eu"), "unsupported_region"),
    (dict(url="https://evil.example/Acme/100000000000001"), "canonical_url_invalid"),
    (dict(url="https://jobs.smartrecruiters.com/Other/100000000000001"), "canonical_url_tenant_mismatch"),
    (dict(url="https://jobs.smartrecruiters.com/Acme/100000000000001?utm=1"), "canonical_url_invalid"),
])
def test_unbindable_high_row_is_reported_and_not_replaced_by_a_lower_priority_row(kwargs, reason):
    rows = [row(1, **kwargs)] + [row(i) for i in range(2, 4)] + [row(50, "Data Analyst")]
    f04b = f04_report(rows)
    allow = build_allowlist(f04b, f04_report_sha256=F04_SHA, lineage=lineage_of(f04b))
    assert [u["reason"] for u in allow["selection"]["unbound"]] == [reason]
    assert [t["posting_id"] for t in allow["targets"]] == [pid(2), pid(3)]
    assert pid(50) not in {t["posting_id"] for t in allow["targets"]}


def test_f04_report_must_be_approved_ordered_and_authority_free():
    f04 = f04_report([row(i) for i in range(1, 4)])
    unapproved = evaluate_report(report(*[row(i) for i in range(1, 4)]), criteria())          # no expected digest
    broken = copy.deepcopy(f04)
    broken["vacancies"][0]["priority_rank"] = 2
    powered = copy.deepcopy(f04)
    powered["authority"]["submit"] = True
    wrong = {**f04, "schema": "EJS-OTHER"}
    for bad, code in ((unapproved, "F04_CRITERIA_NOT_APPROVED"), (broken, "F04_PRIORITY_ORDER_BROKEN"),
                      (powered, "F04_REPORT_AUTHORITY"), (wrong, "F04_REPORT_SCHEMA")):
        with pytest.raises(DetailVerificationError, match=code):
            build_allowlist(bad, f04_report_sha256=F04_SHA, lineage=lineage_of(f04))


@pytest.mark.parametrize("limit", [0, 6, -1, True, "5"])
def test_limit_is_bounded_to_five(limit):
    with pytest.raises(DetailVerificationError, match="LIMIT_OUT_OF_RANGE"):
        f04b = world()[0]
        build_allowlist(f04b, f04_report_sha256=F04_SHA, lineage=lineage_of(f04b), limit=limit)


# ---------------------------------------------------------------- 2. no unapproved targets or duplicate requests
def _tampered(mutate):
    f04, allow = world()
    doc = copy.deepcopy(allow)
    mutate(doc)
    return f04, doc


@pytest.mark.parametrize("mutate", [
    lambda d: d["targets"].append({**d["targets"][0], "sequence": 6}),
    lambda d: d["targets"][0].update(detail_url=d["targets"][0]["detail_url"] + "?x=1"),
    lambda d: d["targets"][0].update(detail_url=d["targets"][0]["detail_url"].replace(DETAIL_HOST, "evil.example")),
    lambda d: d["targets"][0].update(posting_id=pid(99)),
    lambda d: d["targets"].reverse(),
    lambda d: d["targets"].pop(),
    lambda d: d["selection"].update(limit=3),
    lambda d: d["request_policy"].update(max_requests=60, retries=3),
    lambda d: d.update(extra="x"),
    lambda d: d["source"].update(f04_report_sha256="c" * 64),
])
def test_tampered_allowlist_is_refused_before_any_request(mutate):
    f04, doc = _tampered(mutate)
    with pytest.raises(DetailVerificationError):
        verify_allowlist(doc, f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04))


def test_verify_allowlist_accepts_the_exact_allowlist():
    f04, allow = world()
    assert verify_allowlist(allow, f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04)) == allow


def test_request_details_refuses_duplicates_and_oversize_allowlists_without_any_request():
    f04, allow = world()
    net = Net({})
    carrier = ReplayCarrier(transport=net, clock=Clock(), max_requests=5, max_pages=5, allowed_hosts=frozenset({DETAIL_HOST}))
    dup = copy.deepcopy(allow)
    dup["targets"][1] = copy.deepcopy(dup["targets"][0])
    big = copy.deepcopy(allow)
    big["targets"] += [dict(big["targets"][0], detail_url=url_of(70 + n)) for n in range(2)]
    for bad in (dup, big):
        with pytest.raises(DetailVerificationError):
            request_details(bad, carrier, now=lambda: NOW)
    assert net.calls == []


def test_guard_blocks_any_url_outside_the_allowlist(monkeypatch):
    f04, allow = world(highs=2)
    net = Net({url_of(1): ok(sr(1)), url_of(99): ok(sr(99))})

    def rogue(candidate, transport):                    # a bridge change that asks for another posting
        transport(url_of(99))

    monkeypatch.setattr(dv, "observe_posting_detail", rogue)
    carrier = ReplayCarrier(transport=net, clock=Clock(), max_requests=2, max_pages=5, allowed_hosts=frozenset({DETAIL_HOST}))
    with pytest.raises(DetailVerificationError, match="REQUEST_NOT_ALLOWLISTED_OR_DUPLICATE"):
        request_details(allow, carrier, now=lambda: NOW)
    assert net.calls == []


def test_each_allowlisted_url_is_requested_exactly_once_with_get_semantics():
    f04, allow = world()
    net, carrier, _, _, _ = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 6)})
    urls = [u for u, _ in net.calls]
    assert urls == [t["detail_url"] for t in allow["targets"]] and len(set(urls)) == 5
    assert carrier.total == 5 and [e["url"] for e in carrier.log] == urls
    assert all(headers == REQUEST_HEADERS for _, headers in net.calls)       # no Authorization, Cookie or identity


# ------------------------------------------------------------------------ 3. detail verification outcomes
def test_active_identity_bound_posting_with_requisition_is_candidate_for_review():
    f04, allow = world(highs=1)
    net, _, observations, ver, _ = run(f04, allow, {url_of(1): ok(sr(1))})
    c = ver["candidates"][0]
    raw = observations[0]["raw"]
    assert c["identity"] == {"status": "bound", "reasons": []}
    assert (c["availability"], c["requisition_id"]) == ("active", "REQ-1")
    assert c["verdict"]["status"] == "candidate_for_review"
    assert c["reevaluation"]["classification"] == "verified_apply_candidate"
    assert c["evidence"] == {"raw_sha256": sha256(raw).hexdigest(), "raw_bytes": len(raw)}
    assert c["request"] == {"outcome": "ok", "http_status": 200, "attempted": 1, "requested_at": "2026-11-26T12:00:00Z",
                            "observed_at": "2026-11-26T12:00:00Z", "duration_ms": 0, "response_bytes": len(raw),
                            "response_sha256": sha256(raw).hexdigest()}
    assert c["canonical_url"] == f"https://jobs.smartrecruiters.com/{TENANT}/{pid(1)}"
    assert ver["verdict_counts"] == {"candidate_for_review": 1, "incomplete_evidence": 0, "confirmed_incompatible": 0}


def test_responsibilities_and_mandatory_qualifications_are_employer_wording_only():
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1))})
    s = ver["candidates"][0]["sections"]
    assert s["job_description_lines"][-2:] == ["Run workshops.", "Write specifications."]
    assert s["qualification_lines"] == ["You must have 3 years of ERP experience.", "Dutch is a plus."]
    assert s["mandatory_qualification_lines"] == ["You must have 3 years of ERP experience."]    # "plus" stays optional
    assert s["truncated"] is False


def test_inactive_posting_is_a_confirmed_incompatibility():
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, active=False))})
    c = ver["candidates"][0]
    assert c["availability"] == "inactive"
    assert c["verdict"]["status"] == "confirmed_incompatible" and "confirmed:vacancy_closed" in c["verdict"]["reasons"]


@pytest.mark.parametrize("raw,reason", [
    (sr(1, posting=pid(2)), "detail_identity"),                                        # another posting's body
    (sr(1, tenant="Other"), "detail_identity"),                                        # another employer's body
    (sr(1, posting_url=f"https://jobs.smartrecruiters.com/Other/{pid(1)}"), "posting_url_mismatch"),
    (sr(1, posting_url=f"https://jobs.smartrecruiters.com/{TENANT}/{pid(2)}-x"), "posting_url_mismatch"),
    (sr(1, posting_url="http://jobs.smartrecruiters.com/Acme/" + pid(1)), "posting_url_mismatch"),
    (sr(1, posting_url="https://evil.example/Acme/" + pid(1)), "posting_url_mismatch"),
    (b"[]", "detail_identity"),
    (b"not json", "detail_unreadable"),
    (b"", "detail_size"),
])
def test_identity_mismatch_is_never_evaluated_and_never_candidate_for_review(raw, reason):
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(raw)})
    c = ver["candidates"][0]
    assert c["identity"]["reasons"] == [reason] and c["identity"]["status"] != "bound"
    assert c["reevaluation"] is None and c["sections"] is None and c["requisition_id"] is None
    assert c["verdict"]["status"] == "incomplete_evidence"
    assert ver["reevaluation_summary"]["details_applied"] == 0


@pytest.mark.parametrize("ref", [None, "", "bad ref!", 12345])
def test_missing_or_invalid_requisition_id_is_never_candidate_for_review(ref):
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, ref=ref))})
    c = ver["candidates"][0]
    assert c["requisition_id"] is None
    assert c["reevaluation"]["classification"] == "verified_apply_candidate"           # F04 alone would pass it
    assert c["verdict"] == {"status": "incomplete_evidence", "reasons": ["requisition_id_unverified"]}


def test_missing_active_flag_is_incomplete_not_active():
    f04, allow = world(highs=1)
    data = json.loads(sr(1))
    del data["active"]
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(json.dumps(data).encode())})
    c = ver["candidates"][0]
    assert c["availability"] == "unknown" and c["verdict"]["status"] == "incomplete_evidence"
    assert "availability_unverified" in c["verdict"]["reasons"]


@pytest.mark.parametrize("text,blocker", [
    ("<p>We do not offer visa sponsorship.</p>", "confirmed:blocker:explicit-no-sponsorship"),
    ("<p>Applicants must have the unrestricted right to work in the Netherlands.</p>",
     "confirmed:blocker:unlimited-work-right-required"),
])
def test_explicit_mandatory_eligibility_restriction_is_confirmed_incompatible(text, blocker):
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text))})
    c = ver["candidates"][0]
    assert c["verdict"]["status"] == "confirmed_incompatible" and blocker in c["verdict"]["reasons"]
    assert c["reevaluation"]["detail_quotes"]                                           # the employer's wording is kept


@pytest.mark.parametrize("text", [
    "<p>The team is friendly.</p>",                                                      # not stated
    "<p>Visa sponsorship may be available depending on the candidate.</p>",             # hedged
    "<p>We currently do not offer visa sponsorship.</p>",                               # conditional / time-scoped
    "<p>Unrestricted right to work is not required.</p>",                               # negated requirement
    "<p>We do not offer visa sponsorship unless the role is critical.</p>",
])
def test_unknown_or_ambiguous_sponsorship_stays_incomplete_never_incompatible(text):
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text))})
    c = ver["candidates"][0]
    assert c["verdict"]["status"] == "incomplete_evidence", c["verdict"]
    assert c["reevaluation"]["confirmed_incompatibilities"] == []
    assert "sponsorship" in c["reevaluation"]["unknown_components"]
    assert c["reevaluation"]["ratings"]["sponsorship"] is None


def test_w2_eligibility_condition_keeps_a_candidate_out_of_candidate_for_review():
    f04, allow = world(highs=1)
    text = APPLY_TEXT + "<p>Candidates must have the right to work in the Netherlands</p>"
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text))})
    c = ver["candidates"][0]
    assert c["w2_eligibility_conditions"] and c["verdict"]["status"] == "incomplete_evidence"
    assert "w2_eligibility_condition_requires_review" in c["verdict"]["reasons"]


# ------------------------------------------------------------------- 4. HTTP failures and request budget
@pytest.mark.parametrize("answer,kind", [((500, b""), "http_500"), ((404, b""), "http_404"), ((429, b""), "http_429"),
                                         ((403, b""), "http_403"), ((200, b"x" * 5_000_001), "body_too_large")])
def test_http_failure_stops_the_employer_with_no_retry_and_no_further_requests(answer, kind):
    f04, allow = world()
    responses = {url_of(i): ok(sr(i)) for i in range(1, 6)} | {url_of(1): answer}
    net, carrier, _, ver, _ = run(f04, allow, responses)
    assert len(net.calls) == 1 and carrier.total == 1                                    # one attempt, no retry
    first, *rest = ver["candidates"]
    assert first["verdict"]["status"] == "incomplete_evidence"
    assert first["verdict"]["reasons"] == [f"detail_not_obtained:{kind}"] and first["request"]["attempted"] == 1
    assert all(c["request"] == {"outcome": "employer_stopped", "http_status": None, "attempted": 0,
                                "requested_at": "2026-11-26T12:00:00Z", "observed_at": "2026-11-26T12:00:00Z",
                                "duration_ms": 0, "response_bytes": None, "response_sha256": None} for c in rest)
    assert all(c["verdict"]["status"] == "incomplete_evidence" and c["reevaluation"] is None for c in ver["candidates"])


def test_transport_exception_is_recorded_not_retried():
    f04, allow = world(highs=2)
    net, _, _, ver, _ = run(f04, allow, {url_of(1): TimeoutError("boom"), url_of(2): ok(sr(2))})
    assert len(net.calls) == 1
    assert [c["verdict"]["status"] for c in ver["candidates"]] == ["incomplete_evidence"] * 2


def test_request_budget_exhaustion_makes_no_extra_request():
    f04, allow = world(highs=3)
    net, carrier, _, ver, _ = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 4)}, max_requests=2)
    assert len(net.calls) == 2 and carrier.total == 2
    third = ver["candidates"][2]
    assert third["request"]["outcome"] == "request_budget_exceeded" and third["request"]["attempted"] == 0
    assert third["verdict"]["status"] == "incomplete_evidence"
    assert [c["verdict"]["status"] for c in ver["candidates"][:2]] == ["candidate_for_review", "candidate_for_review"]


# --------------------------------------------------------------------------- carrier restrictions reused
def test_real_carrier_enforces_one_second_interval_https_host_and_no_retry():
    f04, allow = world(highs=3)
    net, carrier, _, _, sleeps = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 4)}, real=True)
    assert len(sleeps) == 2 and all(s > 0.99 for s in sleeps)                           # >= 1 s between requests
    with pytest.raises(HostNotAllowedError):
        carrier("https://example.com/v1/companies/Acme/postings/1")
    with pytest.raises(HostNotAllowedError):
        carrier(url_of(1).replace("https://", "http://"))
    assert carrier.total == 3
    with pytest.raises(ValueError):
        HttpCarrier(enabled=True, transport=net, clock=Clock(), sleep=lambda s: None, min_interval_s=0.5)
    with pytest.raises(ValueError):
        HttpCarrier(enabled=True, transport=net, clock=Clock(), sleep=lambda s: None, max_requests=61)
    with pytest.raises(ValueError):
        HttpCarrier(enabled=True, transport=net, clock=Clock(), sleep=lambda s: None, allowed_hosts=frozenset({"example.com"}))


def test_live_transport_is_get_only_and_refuses_other_hosts_without_a_connection():
    with pytest.raises(DiscoveryError):
        urllib_transport("https://example.com/x", dict(REQUEST_HEADERS), 1.0)


# -------------------------------------------------------------------------------- Function 04 re-evaluation
def test_reevaluation_matches_function_04_with_the_same_criteria_and_leaves_inputs_untouched():
    f04, allow = world(highs=2)
    before = json.dumps(f04, sort_keys=True)
    texts = {1: APPLY_TEXT, 2: "<p>Nothing relevant.</p>"}
    _, _, observations, ver, _ = run(f04, allow, {url_of(i): ok(sr(i, text=texts[i])) for i in (1, 2)})
    assert json.dumps(f04, sort_keys=True) == before
    direct = evaluate_report(f04.projection, criteria(),
                             details={(TENANT, pid(i)): detail_from_smartrecruiters(observations[i - 1]["raw"], TENANT, pid(i))
                                      for i in (1, 2)},
                             criteria_sha256=SHA, expected_criteria_sha256=SHA, expected_rule_version=RULE)
    expected = {v["posting_id"]: dv._compact(v["suitability"]) for v in direct["vacancies"]}
    for c in ver["candidates"]:
        assert c["reevaluation"] == expected[c["posting_id"]]
        assert c["reevaluation"]["evidence_level"] == "vacancy_details"
        assert c["reevaluation"]["rule_version"] == RULE
    assert ver["reevaluation_summary"]["details_applied"] == 2


def lineage_call(f04, projection, *, expected_sha=SHA):
    return check_lineage(f04, projection, criteria(), criteria_sha256=SHA, expected_criteria_sha256=expected_sha,
                         expected_rule_version=RULE, f02_source_sha256=F02_FILE_SHA)


def test_lineage_check_binds_the_f04_report_to_the_pinned_original_f02_data():
    f04 = f04_report([row(i) for i in range(1, 4)])
    assert lineage_call(f04, f04.projection) == lineage_of(f04)
    edited = copy.deepcopy(f04)
    edited["vacancies"][0]["suitability"]["classification"] = "preliminary_medium"
    with pytest.raises(DetailVerificationError, match="F04_REPORT_NOT_REPRODUCIBLE"):
        lineage_call(edited, f04.projection)
    with pytest.raises(DetailVerificationError, match="CRITERIA_NOT_APPROVED"):
        lineage_call(f04, f04.projection, expected_sha="d" * 64)


def test_a_self_consistent_forged_f04_report_from_other_data_is_refused():
    original = f04_report([row(i) for i in range(1, 4)])
    forged = f04_report([row(i) for i in range(1, 4)] + [row(9, "Business Analyst")])      # extra row, own digest
    assert forged["source_report_sha256"] != original["source_report_sha256"]
    lineage_call(forged, forged.projection)                                                 # consistent with itself
    with pytest.raises(DetailVerificationError, match="F04_REPORT_NOT_FROM_PINNED_F02_DATA"):
        lineage_call(forged, original.projection)                                           # but not with the pinned data
    with pytest.raises(DetailVerificationError, match="LINEAGE_NOT_BOUND_TO_F04_REPORT"):
        build_allowlist(forged, f04_report_sha256=F04_SHA, lineage=lineage_of(original))


# ---------------------------------------------------------------------------------------- Function 06 hand-off
def test_handoff_orders_candidates_for_review_before_incomplete_and_lists_confirmed_incompatible_apart():
    f04, allow = world()
    responses = {url_of(1): ok(sr(1, active=False)), url_of(2): ok(sr(2, ref=None)), url_of(3): ok(sr(3)),
                 url_of(4): ok(sr(4, text="<p>We do not offer visa sponsorship.</p>")),
                 url_of(5): ok(sr(5, text="<p>Nothing relevant.</p>"))}
    _, carrier, _, ver, _ = run(f04, allow, responses)
    rep = build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256=dv.allowlist_digest(allow),
                       mode="offline_replay", started_at="2026-11-26T12:00:00Z")
    hand = build_handoff(rep)
    assert [c["posting_id"] for c in hand["candidates"]][0] == pid(3)
    assert [c["status"] for c in hand["candidates"]] == ["candidate_for_review", "incomplete_evidence", "incomplete_evidence"]
    assert {c["posting_id"] for c in hand["candidates"]} == {pid(2), pid(3), pid(5)}
    assert {c["posting_id"] for c in hand["confirmed_incompatible"]} == {pid(1), pid(4)}
    assert hand["first_candidate_for_review"]["posting_id"] == pid(3)
    assert hand["first_candidate_for_review"]["requisition_id"] == "REQ-1"
    assert [c["handoff_rank"] for c in hand["candidates"]] == [1, 2, 3]
    assert rep["verdict_counts"] == {"candidate_for_review": 1, "incomplete_evidence": 2, "confirmed_incompatible": 2}


def test_handoff_is_non_executable_and_carries_no_authority_or_candidate_inference():
    f04, allow = world(highs=2)
    _, carrier, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1)), url_of(2): ok(sr(2, text="<p>x</p>"))})
    rep = build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256="a" * 64, mode="offline_replay",
                       started_at="s")
    for doc in (rep, build_handoff(rep)):
        assert doc["executable"] is False and doc["submit_authorized"] is False
        assert doc["authority"] == AUTHORITY and not any(v for k, v in doc["authority"].items() if k != "label_is_not_authority")
    hand = build_handoff(rep)
    assert all(c["candidate_qualification_match"] == "not_performed_requires_verified_candidate_facts" for c in hand["candidates"])
    assert "separate_function_06_authorization" in hand["prerequisites_before_function_06"]
    assert rep["requests"]["methods"] == ["GET"] and rep["requests"]["redirects_followed"] == 0


def test_handoff_without_a_candidate_for_review_names_the_next_step_and_invents_none():
    f04, allow = world(highs=2)
    _, carrier, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, ref=None)), url_of(2): ok(sr(2, active=False))})
    hand = build_handoff(build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256="a" * 64,
                                      mode="live_allowlisted_get", started_at="s"))
    assert hand["first_candidate_for_review"] is None and hand["next_step"].startswith("no_candidate_for_review_yet")
    assert [c["status"] for c in hand["candidates"]] == ["incomplete_evidence"]


def test_modules_have_no_browser_form_or_post_capability():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "src/ejs/services/detail_verification.py", root / "scripts/function05_detail_verification.py"):
        text = path.read_text()
        for forbidden in ("playwright", "selenium", "method=\"POST\"", "urlopen", "subprocess", "import requests"):
            assert forbidden not in text, (path.name, forbidden)


# ------------------------------------------------------------------------------------------------- the CLI
def load_cli():
    spec = importlib.util.spec_from_file_location(
        "function05_cli", Path(__file__).resolve().parents[1] / "scripts" / "function05_detail_verification.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def files(tmp_path):
    rows = [row(i) for i in range(1, 6)] + [row(50, "Data Analyst"), row(60, "Accountant")]
    crit = tmp_path / "criteria.yaml"
    crit.write_text(criteria_text())
    # Synthetic transport test fixture in the original F02 artifact shape.
    from ejs.services.expansion_shadow import SCHEMA as EXPANSION_SCHEMA, project_for_application_bridge
    f02 = {"schema": EXPANSION_SCHEMA, "transport_mode": "live_country_feed",
           "execution_authorized": False, "listings_kept": len(rows), "vacancies": rows,
           "coverage": {"status": "completed", "pairs": [
               {"tenant": TENANT, "country": "NL", "status": "completed"}]},
           "snapshot_consistency": {"atomic_snapshot": False, "first_page_rechecks": [
               {"tenant": TENANT, "country": "NL", "status": "stable"}]}}
    projection = project_for_application_bridge(f02)
    f04 = F04(evaluate_report(projection, criteria(), criteria_sha256=SHA,
                             expected_criteria_sha256=SHA, expected_rule_version=RULE))
    f04.projection = projection
    f04_path = tmp_path / "suitability-report.json"
    f04_path.write_text(json.dumps(f04, sort_keys=True, indent=1))
    f02_path = tmp_path / "f02-report.json"
    f02_path.write_text(json.dumps(f02, sort_keys=True))
    common = ["--f04-report", str(f04_path), "--criteria", str(crit), "--expected-criteria-sha256", SHA,
              "--expected-rule-version", RULE, "--f02-report", str(f02_path), "--expected-f02-sha256",
              sha256(f02_path.read_bytes()).hexdigest()]
    return tmp_path, common, f04


def prepare(cli, tmp_path, common, name="allow"):
    out = tmp_path / name
    assert cli.main(["prepare", *common, "--pilot-country", "NL", "--out-dir", str(out)]) == 0
    return out / "detail-allowlist.json"


def fake_now():
    return NOW


def test_cli_prepare_then_offline_verify_end_to_end(tmp_path, files, capsys):
    base, common, f04 = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    prepared = json.loads(capsys.readouterr().out)
    allow = json.loads(allow_path.read_text())
    assert prepared["network"] is False and prepared["expected_request_count"] == 5
    assert prepared["allowlist_sha256"] == dv.allowlist_digest(allow)
    assert [t["posting_id"] for t in prepared["targets"]] == [pid(i) for i in range(1, 6)]

    replay = base / "replay"
    replay.mkdir()
    for i in range(1, 6):
        (replay / f"{TENANT}__{pid(i)}.json").write_bytes(sr(i, ref=None if i == 2 else "REQ-1"))
    out = base / "result"
    argv = ["verify", *common, "--allowlist", str(allow_path), "--replay-dir", str(replay), "--out-dir", str(out)]
    assert cli.main(argv, now=fake_now, clock=Clock(), sleep=lambda s: None) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "offline_replay" and summary["requests_attempted"] == 5
    assert summary["verdicts"] == {"confirmed_incompatible": 0, "incomplete_evidence": 1, "candidate_for_review": 4}
    assert summary["first_candidate_for_review"]["posting_id"] == pid(1)

    assert stat.S_IMODE(os.stat(out).st_mode) == 0o700
    for name in ("detail-verification-report.json", "f06-handoff.json", "evidence-manifest.json"):
        assert stat.S_IMODE(os.stat(out / name).st_mode) == 0o600
    manifest = json.loads((out / "evidence-manifest.json").read_text())
    assert len(manifest["entries"]) == 5
    for entry in manifest["entries"]:
        stored = (out / entry["stored_as"]).read_bytes()
        assert sha256(stored).hexdigest() == entry["sha256"] and entry["bytes"] == len(stored)
        assert entry["observed_at"] == "2026-11-26T12:00:00Z"
        assert stat.S_IMODE(os.stat(out / entry["stored_as"]).st_mode) == 0o600
        # The raw store is directly usable as Function 04's --details-dir input.
        assert detail_from_smartrecruiters(stored, entry["tenant"], entry["posting_id"]).detail_sha256 == entry["sha256"]
    handoff = json.loads((out / "f06-handoff.json").read_text())
    assert handoff["executable"] is False and handoff["first_candidate_for_review"]["posting_id"] == pid(1)

    assert cli.main(argv, now=fake_now, clock=Clock(), sleep=lambda s: None) == 2          # no overwrite
    out2 = base / "result2"
    assert cli.main([*argv[:-1], str(out2)], now=fake_now, clock=Clock(), sleep=lambda s: None) == 0
    assert (out2 / "detail-verification-report.json").read_bytes() == (out / "detail-verification-report.json").read_bytes()
    assert (out2 / "f06-handoff.json").read_bytes() == (out / "f06-handoff.json").read_bytes()


def test_cli_replay_without_a_saved_body_is_incomplete_and_requests_nothing_live(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    replay = base / "replay"
    replay.mkdir()
    out = base / "result"
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--replay-dir", str(replay),
                     "--out-dir", str(out)], now=fake_now, clock=Clock(), sleep=lambda s: None) == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["verdicts"]["candidate_for_review"] == 0 and summary["first_candidate_for_review"] is None
    assert summary["requests_attempted"] == 1                                              # employer stopped after the 404


def test_cli_live_needs_the_owner_quoted_digest_and_asks_nothing_before_that(tmp_path, files):
    base, common, _ = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    net = Net({url_of(i): ok(sr(i)) for i in range(1, 6)})
    for approval in ([], ["--approve-allowlist-sha256", "0" * 64], ["--approve-allowlist-sha256", ""]):
        out = base / f"live{len(approval)}{len(approval[-1:] and approval[-1])}"
        assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--live", *approval,
                         "--out-dir", str(out)], live_transport=net, now=fake_now, clock=Clock(),
                        sleep=lambda s: None) == 2
        assert not out.exists()
    assert net.calls == []


def test_cli_live_with_the_approved_digest_requests_only_the_allowlisted_urls_once(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    capsys.readouterr()
    digest = dv.allowlist_digest(json.loads(allow_path.read_text()))
    net = Net({url_of(i): ok(sr(i)) for i in range(1, 6)})
    sleeps: list[float] = []
    out = base / "live"
    budget = base / "budget"
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--live", "--approve-allowlist-sha256", digest,
                     "--budget-dir", str(budget), "--out-dir", str(out)], live_transport=net, now=fake_now,
                    clock=Clock(), sleep=sleeps.append) == 0
    assert [u for u, _ in net.calls] == [url_of(i) for i in range(1, 6)]
    assert all(h == REQUEST_HEADERS for _, h in net.calls) and len(sleeps) == 4 and min(sleeps) > 0.99
    report = json.loads((out / "detail-verification-report.json").read_text())
    assert report["transport_mode"] == "live_allowlisted_get" and report["requests"]["attempted"] == 5
    assert report["requests"]["approved"] == 5 and report["requests"]["retries"] == 0
    assert report["evidence_origin"] == "live_employer_response" and report["real_evidence"] is True
    assert sorted(p.name for p in budget.iterdir()).count(f"allowlist-{digest}.json") == 1 and len(list(budget.iterdir())) == 6
    events = [json.loads(line) for line in (out / "attempt-journal.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["run_started"] + ["attempt_started", "attempt_finished"] * 5 + ["run_finished"]
    assert all(e["http_status"] == 200 and e["response_sha256"] and e["response_bytes"] for e in events
               if e["event"] == "attempt_finished")
    # second run of the same approval, new output directory: budget consumed, nothing requested
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--live", "--approve-allowlist-sha256", digest,
                     "--budget-dir", str(budget), "--out-dir", str(base / "live-again")], live_transport=net,
                    now=fake_now, clock=Clock(), sleep=sleeps.append) == 2
    assert len(net.calls) == 5 and not (base / "live-again" / "raw").exists()


def test_cli_refuses_tampered_allowlist_wrong_criteria_and_foreign_reports_before_any_request(tmp_path, files):
    base, common, f04 = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    net = Net({})
    kwargs = dict(live_transport=net, now=fake_now, clock=Clock(), sleep=lambda s: None)
    doc = json.loads(allow_path.read_text())
    doc["targets"].append({**doc["targets"][0], "sequence": 6, "posting_id": pid(99), "detail_url": url_of(99)})
    forged = base / "forged.json"
    forged.write_text(json.dumps(doc))
    digest = dv.allowlist_digest(doc)
    assert cli.main(["verify", *common, "--allowlist", str(forged), "--live", "--approve-allowlist-sha256", digest,
                     "--out-dir", str(base / "o1")], **kwargs) == 2

    bad_sha = [*common]
    bad_sha[bad_sha.index("--expected-criteria-sha256") + 1] = "e" * 64
    assert cli.main(["prepare", *bad_sha, "--out-dir", str(base / "o2")]) == 2
    bad_rule = [*common]
    bad_rule[bad_rule.index("--expected-rule-version") + 1] = "OTHER"
    assert cli.main(["prepare", *bad_rule, "--out-dir", str(base / "o3")]) == 2
    other = json.loads(Path(common[1]).read_text())
    other["criteria"]["sha256"] = "f" * 64
    foreign = base / "foreign.json"
    foreign.write_text(json.dumps(other))
    swapped = [*common]
    swapped[1] = str(foreign)
    assert cli.main(["prepare", *swapped, "--out-dir", str(base / "o4")]) == 2
    assert cli.main(["prepare", *common, "--out-dir", str(Path(__file__).resolve().parents[1] / "f05-must-not-exist")]) == 2
    assert not (Path(__file__).resolve().parents[1] / "f05-must-not-exist").exists()
    assert net.calls == [] and not any((base / n).exists() for n in ("o1", "o2", "o3", "o4"))


def test_cli_prepare_refuses_overwrite_and_missing_digest_arguments(tmp_path, files):
    base, common, _ = files
    cli = load_cli()
    prepare(cli, base, common)
    assert cli.main(["prepare", *common, "--out-dir", str(base / "allow")]) == 2
    with pytest.raises(SystemExit):                                  # digest and rule version are mandatory arguments
        cli.main(["prepare", "--f04-report", common[1], "--criteria", common[3], "--out-dir", str(base / "x")])


# ============================================================================== review fixes (pre-live QA)
def approved_live(cli, base, common, capsys=None):
    allow_path = prepare(cli, base, common)
    if capsys:
        capsys.readouterr()
    return allow_path, dv.allowlist_digest(json.loads(allow_path.read_text()))


def live_argv(common, allow_path, digest, budget, out):
    return ["verify", *common, "--allowlist", str(allow_path), "--live", "--approve-allowlist-sha256", digest,
            "--budget-dir", str(budget), "--out-dir", str(out)]


def kwargs(net):
    return dict(live_transport=net, now=fake_now, clock=Clock(), sleep=lambda s: None)


# ---- 1. durable single-use budget ---------------------------------------------------------------------
def test_budget_is_single_use_across_output_dirs_and_overlapping_allowlists(tmp_path):
    f04, allow = world(highs=3)
    other = copy.deepcopy(allow)
    other["targets"] = other["targets"][1:]                                   # different digest, overlapping URLs
    budget = tmp_path / "budget"
    budget.mkdir(mode=0o700)
    path = reserve_budget(budget, allow, now=lambda: NOW, out_dir=tmp_path / "o1")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and len(list(budget.iterdir())) == 4
    for again in (allow, other):
        with pytest.raises(DetailVerificationError, match="BUDGET_ALREADY_CONSUMED"):
            reserve_budget(budget, again, now=lambda: NOW, out_dir=tmp_path / "o2")
    assert len(list(budget.iterdir())) == 4                                   # refusal left nothing of its own behind
    fresh = copy.deepcopy(allow)
    fresh["targets"] = [dict(allow["targets"][0], detail_url=url_of(77), posting_id=pid(77))]
    reserve_budget(budget, fresh, now=lambda: NOW, out_dir=tmp_path / "o3")   # a different URL is a different budget


def test_concurrent_reservations_admit_exactly_one_process(tmp_path):
    f04, allow = world(highs=5)
    budget = tmp_path / "budget"
    budget.mkdir(mode=0o700)
    barrier, results = threading.Barrier(8), []

    def worker(n):
        barrier.wait()
        try:
            reserve_budget(budget, allow, now=lambda: NOW, out_dir=tmp_path / f"o{n}")
            results.append("won")
        except DetailVerificationError:
            results.append("refused")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("won") == 1 and results.count("refused") == 7


def test_interrupted_live_attempt_consumes_the_budget_keeps_evidence_and_is_never_retried(tmp_path, files):
    base, common, _ = files
    cli = load_cli()
    allow_path, digest = approved_live(cli, base, common)

    class Interrupted(Net):
        def __call__(self, url, headers, timeout):
            if len(self.calls) == 2:
                self.calls.append((url, dict(headers)))
                raise KeyboardInterrupt                                    # process dies mid-request
            return super().__call__(url, headers, timeout)

    net = Interrupted({url_of(i): ok(sr(i)) for i in range(1, 6)})
    budget, out = base / "budget", base / "out1"
    with pytest.raises(KeyboardInterrupt):
        cli.main(live_argv(common, allow_path, digest, budget, out), **kwargs(net))
    assert len(net.calls) == 3                                             # two answered, third interrupted, no retry
    events = [json.loads(line) for line in (out / "attempt-journal.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["run_started"] + ["attempt_started", "attempt_finished"] * 2 + ["attempt_started"]
    for entry in events:                                                   # answered bodies were stored immediately
        if entry["event"] == "attempt_finished":
            assert sha256((out / entry["stored_as"]).read_bytes()).hexdigest() == entry["response_sha256"]
    assert not (out / "detail-verification-report.json").exists()
    # the reservation stays consumed: neither the same nor a fresh output directory may run again
    for name in ("out1", "out2"):
        assert cli.main(live_argv(common, allow_path, digest, budget, base / name), **kwargs(net)) == 2
    assert len(net.calls) == 3


def test_live_requires_a_budget_dir_separate_from_the_output_dir_and_asks_nothing_otherwise(tmp_path, files):
    base, common, _ = files
    cli = load_cli()
    allow_path, digest = approved_live(cli, base, common)
    net = Net({url_of(i): ok(sr(i)) for i in range(1, 6)})
    no_budget = ["verify", *common, "--allowlist", str(allow_path), "--live", "--approve-allowlist-sha256", digest,
                 "--out-dir", str(base / "o1")]
    assert cli.main(no_budget, **kwargs(net)) == 2
    inside = base / "o2"
    assert cli.main(live_argv(common, allow_path, digest, inside / "budget", inside), **kwargs(net)) == 2
    assert net.calls == [] and not (base / "o1").exists()


# ---- 2. stop the employer on an invalid response ----------------------------------------------------------
@pytest.mark.parametrize("bad", [sr(1, posting=pid(2)), sr(1, tenant="Other"), b"not json", b"", b"[]",
                                 sr(1, posting_url=f"https://jobs.smartrecruiters.com/Other/{pid(1)}")])
def test_invalid_or_unbound_response_stops_the_employer_immediately(bad):
    f04, allow = world()
    net, carrier, _, ver, _ = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 6)} | {url_of(1): ok(bad)})
    assert len(net.calls) == 1 and carrier.total == 1
    first, *rest = ver["candidates"]
    assert first["identity"]["status"] != "bound" and first["request"]["attempted"] == 1
    assert all(c["request"]["outcome"] == "employer_stopped_invalid_response" and c["request"]["attempted"] == 0
               and c["verdict"]["reasons"] == ["detail_not_obtained:employer_stopped_invalid_response"] for c in rest)


def test_a_later_invalid_response_stops_the_requests_after_it_not_before_it():
    f04, allow = world()
    net, _, _, ver, _ = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 6)} | {url_of(3): ok(sr(3, posting=pid(9)))})
    assert len(net.calls) == 3
    assert [c["request"]["attempted"] for c in ver["candidates"]] == [1, 1, 1, 0, 0]
    assert [c["verdict"]["status"] for c in ver["candidates"]][:2] == ["candidate_for_review"] * 2


# ---- 3. actual status, timing, bytes, digest; evidence on failure -------------------------------------------
def test_request_metadata_is_the_actual_response_not_a_default():
    f04, allow = world(highs=2)
    ticks = iter(range(100))
    net = Net({url_of(1): ok(sr(1)), url_of(2): (503, b"")})
    carrier = ReplayCarrier(transport=net, clock=Clock(), max_requests=2, max_pages=5, allowed_hosts=frozenset({DETAIL_HOST}))
    obs = request_details(allow, carrier, now=lambda: datetime(2026, 11, 26, 12, 0, next(ticks), tzinfo=timezone.utc))
    good, bad = obs
    raw = sr(1)
    assert (good["http_status"], good["response_bytes"], good["response_sha256"]) == (200, len(raw), sha256(raw).hexdigest())
    assert good["requested_at"] == "2026-11-26T12:00:00Z" and good["observed_at"] == "2026-11-26T12:00:01Z"
    assert good["duration_ms"] == 1000 and good["attempted"] == 1
    assert (bad["http_status"], bad["response_bytes"], bad["response_sha256"], bad["raw"]) == (503, 0, None, None)
    assert bad["outcome"] == "http_503" and bad["attempted"] == 1


def test_sinks_see_every_attempt_before_and_after_the_response():
    f04, allow = world(highs=2)
    events = []
    net = Net({url_of(1): ok(sr(1)), url_of(2): (500, b"")})
    carrier = ReplayCarrier(transport=net, clock=Clock(), max_requests=2, max_pages=5, allowed_hosts=frozenset({DETAIL_HOST}))
    request_details(allow, carrier, now=lambda: NOW, on_start=lambda r: events.append(("start", r["sequence"], len(net.calls))),
                    on_finish=lambda r, raw: events.append(("finish", r["sequence"], r["http_status"], raw is not None)))
    assert events == [("start", 1, 0), ("finish", 1, 200, True), ("start", 2, 1), ("finish", 2, 500, False)]


# ---- 4. nothing is definitively suitable; contradictions and compensation are not exclusions ---------------
def test_no_candidate_is_ever_classified_suitable_and_the_missing_check_is_stated():
    f04, allow = world()
    _, carrier, _, ver, _ = run(f04, allow, {url_of(i): ok(sr(i)) for i in range(1, 6)})
    assert set(ver["verdict_counts"]) == {"candidate_for_review", "incomplete_evidence", "confirmed_incompatible"}
    c = ver["candidates"][0]
    assert c["verdict"]["status"] == "candidate_for_review"
    assert "mandatory_qualifications_not_checked_against_verified_candidate_facts" in c["verdict"]["reasons"]
    rep = build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256="a" * 64, mode="live_allowlisted_get",
                       started_at="s")
    hand = build_handoff(rep)
    assert "suitable" not in json.dumps(hand) .replace("not_suitable", "")
    assert all(x["candidate_qualification_match"] == "not_performed_requires_verified_candidate_facts"
               for x in hand["candidates"])
    assert "mandatory_qualifications_checked_against_verified_candidate_facts" in hand["prerequisites_before_function_06"]
    assert hand["candidates"][0]["mandatory_qualification_lines"] == ["You must have 3 years of ERP experience."]


def test_contradictory_employer_statements_never_cause_a_confirmed_exclusion():
    f04, allow = world(highs=1)
    text = "<p>We do not offer visa sponsorship.</p><p>Visa sponsorship is available.</p>"
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text))})
    c = ver["candidates"][0]
    assert c["reevaluation"]["reason_code"] == "contradiction"
    assert c["verdict"]["status"] == "incomplete_evidence"
    assert "contradictory_evidence_requires_review" in c["verdict"]["reasons"]


def test_closed_posting_is_still_confirmed_even_when_the_text_contradicts_itself():
    f04, allow = world(highs=1)
    text = "<p>We do not offer visa sponsorship.</p><p>Visa sponsorship is available.</p>"
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text, active=False))})
    assert ver["candidates"][0]["verdict"] == {"status": "confirmed_incompatible", "reasons": ["confirmed:vacancy_closed"]}


def test_below_target_compensation_is_scored_not_turned_into_a_hard_blocker():
    f04, allow = world(highs=1)
    text = "<p>Visa sponsorship is available.</p><p>Working language is English.</p><p>Salary: €2,000 per month net.</p>"
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text=text))})
    c = ver["candidates"][0]
    assert "confirmed:compensation_below_target" in c["reevaluation"]["confirmed_incompatibilities"]
    assert c["reevaluation"]["ratings"]["compensation_living"] == 0                    # the engine scores it
    assert c["verdict"]["status"] == "candidate_for_review"
    assert c["verdict"]["notes"] == ["compensation_below_target_scored_not_a_blocker"]


def test_only_approved_k2_blockers_exclude():
    criteria_blockers = set(criteria().hard_blockers)
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, text="<p>We do not offer visa sponsorship.</p>"))})
    reasons = ver["candidates"][0]["verdict"]["reasons"]
    assert reasons == ["confirmed:blocker:explicit-no-sponsorship"] and "explicit-no-sponsorship" in criteria_blockers


# ---- 6. lineage and synthetic-versus-real labelling ------------------------------------------------------------
def test_cli_refuses_a_wrong_f02_pin_or_a_f04_report_from_other_f02_data_before_any_request(tmp_path, files):
    base, common, f04 = files
    cli = load_cli()
    bad_pin = [*common]
    bad_pin[bad_pin.index("--expected-f02-sha256") + 1] = "0" * 64
    assert cli.main(["prepare", *bad_pin, "--out-dir", str(base / "o1")]) == 2
    other = f04_report([row(i) for i in range(1, 4)])                         # genuine-looking but other F02 data
    forged = base / "forged-f04.json"
    forged.write_text(json.dumps(other, sort_keys=True))
    swapped = [*common]
    swapped[1] = str(forged)
    assert cli.main(["prepare", *swapped, "--out-dir", str(base / "o2")]) == 2
    assert not (base / "o1").exists() and not (base / "o2").exists()


def test_allowlist_digest_covers_the_f02_lineage(tmp_path, files):
    base, common, f04 = files
    cli = load_cli()
    doc = json.loads(prepare(cli, base, common).read_text())
    assert doc["source"]["lineage"]["f02_projection_sha256"] == f04["source_report_sha256"]
    assert doc["source"]["lineage"]["f02_source_file_sha256"] == common[common.index("--expected-f02-sha256") + 1]
    tampered = copy.deepcopy(doc)
    tampered["source"]["lineage"]["f02_source_file_sha256"] = "9" * 64
    assert dv.allowlist_digest(tampered) != dv.allowlist_digest(doc)
    with pytest.raises(DetailVerificationError):
        verify_allowlist(tampered, f04, f04_report_sha256=sha256((base / "suitability-report.json").read_bytes()).hexdigest(),
                         lineage=lineage_of(f04))


def test_replay_output_is_labelled_synthetic_and_never_function_06_eligible(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    allow_path = prepare(cli, base, common)
    replay = base / "replay"
    replay.mkdir()
    for i in range(1, 6):
        (replay / f"{TENANT}__{pid(i)}.json").write_bytes(sr(i))
    out = base / "result"
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--replay-dir", str(replay), "--out-dir", str(out)],
                    now=fake_now, clock=Clock(), sleep=lambda s: None) == 0
    report = json.loads((out / "detail-verification-report.json").read_text())
    hand = json.loads((out / "f06-handoff.json").read_text())
    manifest = json.loads((out / "evidence-manifest.json").read_text())
    for doc in (report, hand):
        assert (doc["evidence_origin"], doc["real_evidence"], doc["function06_eligible"]) == (
            "offline_replay_synthetic", False, False)
    assert hand["next_step"].startswith("synthetic_replay_output_is_not_real_evidence")
    assert manifest["evidence_origin"] == "offline_replay_synthetic"
    assert all(c["evidence_origin"] == "offline_replay_synthetic" for c in hand["candidates"])
    assert not (base / "budget").exists()                                            # replay never touches a budget


# ======================================================================================================
# Real-evidence extraction and language/sponsorship policy (owner-approved 2026-11-26)
# ======================================================================================================
from ejs.services import language_policy as lp                                   # noqa: E402
from ejs.services.suitability_evidence import VacancyDetail, extract_detail_evidence  # noqa: E402

NL_DESCRIPTION = ("<p>Als consultant ga je aan de slag met onze klanten en het team om de scope van het project te "
                  "bepalen. Je bent verantwoordelijk voor de kwaliteit van de resultaten die wij aan hen leveren en "
                  "je werkt samen met het hele team aan een mooie oplossing voor de klant.</p>")
NL_QUALIFICATIONS = ("<ul><li>Je hebt een afgeronde WO master (MSc) in bedrijfskunde of informatica.</li>"
                     "<li>Je hebt minimaal 3 jaar relevante werkervaring.</li>"
                     "<li>Je spreekt vloeiend Nederlands en minimaal C1 Engels.</li>"
                     "<li>Ervaring met Dynamics 365 is een pré.</li></ul>")
NL_INFO = "<p>Bruto salaris: € 4.500 - € 6.000 per maand op basis van 40 uur.</p>"
NL_SPONSOR_YES = "<p>Wij bieden visumsponsoring voor kennismigranten.</p>"
NL_SPONSOR_NO = "<p>Wij bieden geen visumsponsoring.</p>"
EN_INFO = "<p>Working language is English.</p>"


def nl(i: int, info: str = NL_INFO, *, qualifications: str = NL_QUALIFICATIONS) -> bytes:
    return sr(i, text=info, description=NL_DESCRIPTION, qualifications=qualifications)


def en(i: int, info: str = EN_INFO) -> bytes:
    return sr(i, text=info, qualifications="<ul><li>You must have 3 years of experience.</li></ul>")


def decide_for(raw: bytes, *, highs: int = 1):
    f04, allow = world(highs=highs)
    responses = {url_of(i): ok(raw if i == 1 else en(i)) for i in range(1, highs + 1)}
    _, _, _, verification, _ = run(f04, allow, responses)
    return by_posting(verification)[pid(1)], verification


def ev(text: str):
    return extract_detail_evidence(VacancyDetail("Acme", "1", True, text, "d" * 64))


# ---- A. Dutch evidence extraction ----------------------------------------------------------------------
def test_dutch_gross_monthly_salary_range_and_number_formatting_are_extracted_with_quote():
    c = ev("Bruto salaris: € 4.500 - € 6.000 per maand op basis van 40 uur.").compensation
    assert (c["currency"], c["monthly_min"], c["monthly_max"], c["basis"]) == ("EUR", 4500.0, 6000.0, "gross")
    assert "4.500" in c["quote"]
    c = ev("Salaris tussen €4.200,- en €5.600,- bruto per maand.").compensation
    assert (c["monthly_min"], c["monthly_max"], c["basis"]) == (4200.0, 5600.0, "gross")
    c = ev("Jaarsalaris € 60.000 bruto per jaar.").compensation
    assert (c["monthly_min"], c["monthly_max"], c["period_stated"]) == (5000.0, 5000.0, "year")


def test_dutch_salary_never_infers_net_and_ignores_allowances_or_missing_period():
    assert ev("Salaris € 4.500 per maand.").compensation["basis"] == "unspecified"      # gross is not assumed either
    assert ev("Netto salaris € 3.200 per maand.").compensation["basis"] == "net"
    assert ev("Reiskostenvergoeding € 150 per maand.").compensation is None
    assert ev("Salaris € 4.500.").compensation is None                                  # no period: Unknown


def test_dutch_language_requirements_with_cefr_level_and_original_quote():
    e = ev("Je spreekt vloeiend Nederlands en minimaal C1 Engels.")
    assert e.mandatory_languages == ("dutch", "english") and e.language_levels == {"english": "C1"}
    assert "vloeiend Nederlands" in e.quotes["mandatory_language:dutch"]
    assert ev("Engels op C1-niveau is vereist.").language_levels == {"english": "C1"}


def test_dutch_language_preference_negation_and_alternatives_stay_unknown():
    for text in ("Nederlands is een pré.", "Nederlands is niet vereist.", "Nederlands of Engels is vereist.",
                 "Bij voorkeur spreek je vloeiend Nederlands."):
        e = ev(text)
        assert e.mandatory_languages == (), text                       # never promoted to a mandatory language
    assert ev("Je spreekt vloeiend Nederlands; Engels is een pré.").mandatory_languages == ("dutch",)


def test_experience_and_education_requirements_distinguish_mandatory_from_preferred():
    e = ev("Je hebt een afgeronde WO master (MSc) in bedrijfskunde.\nJe hebt minimaal 3 jaar relevante werkervaring.")
    assert [(x["min_years"], x["kind"]) for x in e.experience_requirements] == [(3, "mandatory")]
    assert [(x["level"], x["kind"], x["completed_stated"]) for x in e.education_requirements] == \
        [("wo_master", "mandatory", True)]
    assert "minimaal 3 jaar" in e.experience_requirements[0]["quote"]
    e = ev("Bij voorkeur 5 jaar ervaring met Dynamics. Een afgeronde HBO opleiding is een pré.")
    assert [(x["min_years"], x["kind"]) for x in e.experience_requirements] == [(5, "preferred")]
    assert e.education_requirements[0]["kind"] == "preferred"
    e = ev("A completed master's degree is required. At least 5 years of relevant experience.")
    assert e.education_requirements[0]["level"] == "master"        # WO / MSc is claimed only when the posting says so
    assert e.experience_requirements[0]["min_years"] == 5
    assert ev("We are 5 years old and our office has 3 floors.").experience_requirements == ()


def test_dutch_sponsorship_is_explicit_only_hedged_or_contradictory_is_unknown_or_contradiction():
    assert ev("Wij bieden visumsponsoring voor kennismigranten.").explicit_sponsorship_offered
    assert ev("Wij bieden geen visumsponsoring.").explicit_no_sponsorship
    hedged = ev("Visumsponsoring is mogelijk, tenzij de functie kritisch is.")
    assert not hedged.explicit_sponsorship_offered and "sponsorship" in hedged.hedged
    both = ev("Wij bieden visumsponsoring. Wij bieden geen visumsponsoring.")
    assert lp.sponsorship_status(both) == "contradictory"
    assert lp.sponsorship_status(ev("Wij zoeken een collega.")) == "unknown"


# ---- B. advertisement language is read from the body, never the title ----------------------------------
def test_advertisement_language_detection_uses_the_body_and_never_calls_mixed_text_non_english():
    en_text = ("In this role you will work with the team and our clients to define the scope of the project and you "
               "will be responsible for the quality of the results that we deliver to them.")
    nl_text = ("Als consultant ga je aan de slag met onze klanten en het team om de scope van het project te bepalen "
               "en je bent verantwoordelijk voor de kwaliteit van de resultaten die wij aan hen leveren.")
    assert lp.detect_ad_language(en_text)["status"] == "english"
    assert lp.detect_ad_language(nl_text)["status"] == "non_english"
    assert lp.detect_ad_language(nl_text)["dominant_non_english"] == "dutch"
    assert lp.detect_ad_language(en_text + " " + nl_text)["status"] == "multilingual_including_english"
    assert lp.detect_ad_language("Consultant")["status"] == "undetermined"          # thin evidence, e.g. a title
    assert lp.detect_ad_language(nl_text[:60] + " the team")["status"] == "undetermined"   # mixed but thin


# ---- C. the owner-approved matrix ---------------------------------------------------------------------
def test_matrix_covers_every_cell_exactly_as_approved():
    expected = {("english", "offered"): "evaluate", ("english", "unknown"): "evaluate",
                ("english", "unavailable"): "exclude_existing_k2",
                ("non_english", "offered"): "watchlist", ("non_english", "unknown"): "policy_exclusion",
                ("non_english", "unavailable"): "exclude_existing_k2",
                ("multilingual_including_english", "offered"): "evaluate",
                ("multilingual_including_english", "unknown"): "evaluate",
                ("multilingual_including_english", "unavailable"): "exclude_existing_k2",
                ("undetermined", "offered"): "review", ("undetermined", "unknown"): "review",
                ("undetermined", "unavailable"): "exclude_existing_k2"}
    assert {k: v[0] for k, v in lp.MATRIX.items()} == expected
    assert lp.decide("english", "unknown")["policy_version"] == lp.POLICY_VERSION == "EJS-LANG-SPONSOR-POLICY-1.0"


def test_english_advertisement_with_unknown_sponsorship_is_evaluated_not_policy_excluded():
    c, _ = decide_for(en(1))
    assert c["language_policy"]["action"] == "evaluate" and c["verdict"]["status"] != "policy_excluded"
    assert c["posting_evidence"]["advertisement_language"]["status"] == "english"


def test_non_english_with_unknown_sponsorship_is_a_policy_exclusion_distinct_from_incompatibility():
    c, v = decide_for(sr(1, text="<p>Salaris wordt besproken.</p>", description=NL_DESCRIPTION,
                         qualifications="<ul><li>Je hebt minimaal 3 jaar werkervaring.</li></ul>"))
    assert c["language_policy"]["action"] == "policy_exclusion"
    assert c["verdict"]["status"] == "policy_excluded"
    assert c["verdict"]["reasons"] == ["language_policy:non_english_advertisement_sponsorship_unknown"]
    assert v["verdict_counts"]["confirmed_incompatible"] == 0 and v["verdict_counts"]["policy_excluded"] == 1
    kinds = [h["kind"] for h in v["history_records"]]
    assert kinds == ["policy_exclusion"]                                   # retained, never a candidate incompatibility


def test_non_english_with_explicit_sponsorship_goes_to_the_watchlist_with_full_record():
    c, v = decide_for(nl(1, NL_SPONSOR_YES + NL_INFO))
    assert c["verdict"]["status"] == lp.WATCHLIST == "foreign_language_sponsorship_watchlist"
    assert v["verdict_counts"][lp.WATCHLIST] == 1
    (rec,) = v["watchlist_records"]
    assert rec["identity"] == f"smartrecruiters:global:{TENANT}:{pid(1)}"
    assert rec["employer"] == "Acme" and rec["position"] == "Senior Business Analyst" and rec["country"] == ["NL"]
    assert rec["advertisement_language"]["status"] == "non_english"
    assert rec["mandatory_working_languages"] == ["dutch", "english"]            # separate from the advertisement language
    assert rec["sponsorship_evidence"]["status"] == "offered" and "visumsponsoring" in rec["sponsorship_evidence"]["quote"]
    assert rec["source_url"] == f"https://jobs.smartrecruiters.com/{TENANT}/{pid(1)}"
    assert rec["observed_at"] and rec["policy_version"] == lp.POLICY_VERSION


def test_non_english_with_explicit_no_sponsorship_is_excluded_under_the_existing_policy():
    c, v = decide_for(nl(1, NL_SPONSOR_NO + NL_INFO))
    assert c["language_policy"]["action"] == "exclude_existing_k2"
    assert c["verdict"]["status"] == "confirmed_incompatible"
    assert not v["watchlist_records"]


def test_multilingual_advertisement_including_english_is_evaluated():
    both = sr(1, text=EN_INFO, description=EN_DESCRIPTION + NL_DESCRIPTION)
    c, _ = decide_for(both)
    assert c["posting_evidence"]["advertisement_language"]["status"] == "multilingual_including_english"
    assert c["language_policy"]["action"] == "evaluate" and c["verdict"]["status"] != "policy_excluded"
    assert c["verdict"]["status"] != lp.WATCHLIST


def test_undetermined_language_is_reviewed_never_excluded_or_confirmed_non_english():
    thin = sr(1, text="<p>Consultant.</p>", description="<p>Consultant Amsterdam.</p>", qualifications="<p>Ja.</p>")
    c, _ = decide_for(thin)
    assert c["posting_evidence"]["advertisement_language"]["status"] == "undetermined"
    assert c["language_policy"]["action"] == "review"
    assert c["verdict"]["status"] == "incomplete_evidence"
    assert "language_policy:advertisement_language_undetermined" in c["verdict"]["reasons"]


def test_contradictory_sponsorship_is_never_an_explicit_refusal():
    both = "<p>Wij bieden visumsponsoring.</p><p>Wij bieden geen visumsponsoring.</p>"
    c, v = decide_for(nl(1, both))
    assert c["posting_evidence"]["sponsorship"]["status"] == "contradictory"
    assert c["language_policy"]["action"] == "review"
    assert c["verdict"]["status"] != "confirmed_incompatible" or not any("sponsorship" in r for r in c["verdict"]["reasons"])
    assert not v["watchlist_records"]
    assert lp.decide("english", "contradictory")["action"] == "evaluate"             # F04 contradiction rule asks for review


def test_the_title_alone_never_decides_the_advertisement_language():
    f04 = f04_report([row(1, "Senior Business Analyst Bedrijfsprocessen en Digitalisering")])
    allow = build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04))
    _, _, _, v, _ = run(f04, allow, {url_of(1): ok(en(1))})
    assert by_posting(v)[pid(1)]["posting_evidence"]["advertisement_language"]["status"] == "english"


# ---- D. watchlist: dedup, first/last seen, reporting suppression, no authority --------------------------
def _watch_record(day: str, **extra):
    return {"identity": f"smartrecruiters:global:{TENANT}:{pid(1)}", "employer": "Acme", "position": "BA",
            "source_url": "https://jobs.smartrecruiters.com/Acme/1", "observed_at": day, "evidence_sha256": "e" * 64,
            "country": ["NL"], "advertisement_language": {"status": "non_english"}, "policy_version": lp.POLICY_VERSION,
            "mandatory_working_languages": ["dutch"], "sponsorship_evidence": {"status": "offered"}, **extra}


def test_watchlist_deduplicates_by_canonical_identity_and_tracks_first_and_last_seen():
    first = lp.merge_watchlist(None, [_watch_record("2026-11-26T10:00:00Z")])
    again = lp.merge_watchlist(first, [_watch_record("2026-11-28T10:00:00Z"), _watch_record("2026-11-27T10:00:00Z")])
    (entry,) = again["entries"].values()
    assert len(again["entries"]) == 1
    assert (entry["first_seen"], entry["last_seen"]) == ("2026-11-26T10:00:00Z", "2026-11-28T10:00:00Z")
    other = _watch_record("2026-11-28T10:00:00Z") | {"identity": f"smartrecruiters:global:{TENANT}:{pid(2)}"}
    assert len(lp.merge_watchlist(again, [other])["entries"]) == 2
    with pytest.raises(ValueError):
        lp.merge_watchlist({"schema": "x", "entries": {}}, [])


def test_empty_watchlist_suppresses_the_reporting_section_and_count():
    assert lp.reporting_section(lp.merge_watchlist(None, [])) == {}
    f04, allow = world(highs=1)
    _, carrier, _, v, _ = run(f04, allow, {url_of(1): ok(en(1))})
    report_doc = build_report(allow, v, carrier, criteria_sha256=SHA, allowlist_sha256=dv.allowlist_digest(allow),
                              mode="offline_replay", started_at="2026-11-26T12:00:00Z")
    assert lp.WATCHLIST not in report_doc and "foreign_language_sponsorship_watchlist" not in json.dumps(
        build_handoff(report_doc))


def test_nonempty_watchlist_section_has_count_and_no_application_authority():
    f04, allow = world(highs=1)
    _, carrier, _, v, _ = run(f04, allow, {url_of(1): ok(nl(1, NL_SPONSOR_YES + NL_INFO))})
    doc = build_report(allow, v, carrier, criteria_sha256=SHA, allowlist_sha256=dv.allowlist_digest(allow),
                       mode="offline_replay", started_at="2026-11-26T12:00:00Z")
    section = doc[lp.WATCHLIST]
    assert section["count"] == 1 and section["application_queue_authority"] is False
    handoff = build_handoff(doc)
    assert handoff[lp.WATCHLIST]["count"] == 1
    assert handoff["candidates"] == [] and handoff["first_candidate_for_review"] is None      # never in the queue
    assert handoff["executable"] is False and all(v is False for k, v in handoff["authority"].items()
                                                  if k != "label_is_not_authority")


def test_policy_exclusion_history_keeps_every_reason_and_never_shrinks():
    rec = {"identity": "i", "employer": "Acme", "position": "P", "source_url": "u", "observed_at": "2026-11-26T10:00:00Z",
           "evidence_sha256": "e" * 64, "kind": "policy_exclusion", "reasons": ["a"], "country": [],
           "policy_version": lp.POLICY_VERSION, "advertisement_language_status": "non_english"}
    one = lp.merge_history(None, [rec])
    two = lp.merge_history(one, [rec | {"observed_at": "2026-11-27T10:00:00Z", "evidence_sha256": "f" * 64, "reasons": ["a", "b"]}])
    again = lp.merge_history(two, [rec])
    (entry,) = again["entries"].values()
    assert len(entry["history"]) == 2 and entry["first_seen"] < entry["last_seen"]
    assert again == lp.merge_history(again, [rec])                                    # idempotent


# ---- E. Dutch evidence reaches the report; scoring inputs, criteria and weights are untouched -----------
def test_dutch_posting_evidence_is_reported_with_quotes_and_scoring_is_unchanged():
    c, _ = decide_for(nl(1, NL_SPONSOR_YES + NL_INFO))
    pe = c["posting_evidence"]
    assert pe["compensation"]["monthly_min"] == 4500.0 and pe["compensation"]["basis"] == "gross"
    assert pe["mandatory_working_languages"] == ["dutch", "english"] and pe["language_levels"] == {"english": "C1"}
    assert [(e["min_years"], e["kind"]) for e in pe["experience_requirements"]] == [(3, "mandatory")]
    assert pe["education_requirements"][0]["level"] == "wo_master"
    assert "mandatory_language:dutch" in pe["language_quotes"]
    approved = criteria()
    assert approved.rule_version == RULE and sha256(criteria_text().encode()).hexdigest() == SHA   # artifact unchanged


def test_policy_module_is_not_a_scoring_or_application_module():
    imports = [line for line in Path(lp.__file__).read_text().splitlines() if line.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "import re", "from collections.abc import Mapping",
                       "from typing import Any"]                       # pure: no scoring, carrier, network or file access


# ---- F. offline reassessment of previously collected live evidence --------------------------------------
def _live_run(cli, base, common, capsys, bodies: dict):
    allow_path, digest = approved_live(cli, base, common, capsys)
    net = Net({url_of(i): ok(raw) for i, raw in bodies.items()})
    out = base / "live-out"
    assert cli.main(live_argv(common, allow_path, digest, base / "budget", out), **kwargs(net)) == 0
    capsys.readouterr()
    return allow_path, out, net


def _tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_offline_reassessment_rereads_saved_bodies_without_any_request_or_budget_change(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    bodies = {1: nl(1, NL_SPONSOR_YES + NL_INFO), 2: nl(2, "<p>Salaris wordt besproken.</p>", qualifications="<ul><li>Je hebt minimaal 3 jaar werkervaring.</li></ul>"), 3: nl(3, NL_SPONSOR_NO),
              4: en(4), 5: sr(5, text="<p>Consultant.</p>", description="<p>Consultant.</p>", qualifications="<p>Ja.</p>")}
    allow_path, out, net = _live_run(cli, base, common, capsys, bodies)
    before_out, before_budget = _tree_digest(out), _tree_digest(base / "budget")
    calls_before = len(net.calls)
    history, redo = base / "history", base / "reassessed"
    argv = ["reassess", *common, "--allowlist", str(allow_path), "--evidence-dir", str(out),
            "--history-dir", str(history), "--out-dir", str(redo)]
    assert cli.main(argv, **kwargs(Net({}))) == 0                          # empty transport: any GET would KeyError/fail
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "derived_offline_reassessment" and summary["new_requests"] == 0
    assert summary["evidence_origin"] == "derived_offline_analysis_of_live_evidence"
    assert len(net.calls) == calls_before
    assert _tree_digest(out) == before_out and _tree_digest(base / "budget") == before_budget   # originals preserved
    by = {c["posting_id"]: c for c in summary["candidates"]}
    assert by[pid(1)]["advertisement_language"] == "non_english" and by[pid(1)]["status"] == lp.WATCHLIST
    assert by[pid(1)]["compensation"] and by[pid(1)]["experience"] == [[3, "mandatory"]]
    assert by[pid(1)]["education"] == [["wo_master", "mandatory"]]
    assert by[pid(2)]["status"] == "policy_excluded" and by[pid(3)]["sponsorship"] == "unavailable"
    assert by[pid(4)]["advertisement_language"] == "english"
    assert by[pid(5)]["advertisement_language"] == "undetermined"
    assert summary["foreign_language_sponsorship_watchlist_count"] == 1 and summary["policy_exclusions"] >= 1
    report_doc = json.loads((redo / "reassessed-verification-report.json").read_text())
    assert report_doc["evidence_origin"] == "derived_offline_analysis_of_live_evidence"
    assert report_doc["derived_from"]["new_requests"] == 0 and report_doc["derived_from"]["budget_touched"] is False
    assert "derived offline analysis of previously collected live evidence" in report_doc["derived_from"]["note"]
    assert report_doc["requests"]["attempted"] == 0 and report_doc["requests"]["log"] == []
    assert json.loads((history / "foreign-language-sponsorship-watchlist.json").read_text())["entries"]
    assert (history / "policy-exclusion-history.json").is_file()
    # running it again neither duplicates the watchlist nor refuses to merge history
    assert cli.main([*argv[:-1], str(base / "reassessed2")], **kwargs(Net({}))) == 0
    capsys.readouterr()
    assert len(json.loads((history / "foreign-language-sponsorship-watchlist.json").read_text())["entries"]) == 1
    assert stat.S_IMODE(os.stat(redo / "reassessed-verification-report.json").st_mode) == 0o600


def test_reassessment_refuses_tampered_bodies_synthetic_sources_and_unsafe_output(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    allow_path, out, _ = _live_run(cli, base, common, capsys, {i: en(i) for i in range(1, 6)})
    args = lambda evidence, dest: ["reassess", *common, "--allowlist", str(allow_path), "--evidence-dir", str(evidence),
                                   "--history-dir", str(base / "h"), "--out-dir", str(dest)]
    assert cli.main(args(out, out / "inside"), **kwargs(Net({}))) == 2                  # output must be separate
    victim = next((out / "raw").iterdir())
    victim.write_bytes(victim.read_bytes() + b" ")
    assert cli.main(args(out, base / "r1"), **kwargs(Net({}))) == 2                    # SHA-256 no longer matches
    assert not (base / "h").exists() or not any((base / "h").iterdir())
    synthetic = base / "synthetic"
    replay = base / "replay"
    replay.mkdir()
    for i in range(1, 6):
        (replay / f"{TENANT}__{pid(i)}.json").write_bytes(en(i))
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--replay-dir", str(replay),
                     "--out-dir", str(synthetic)], now=fake_now, clock=Clock(), sleep=lambda s: None) == 0
    assert cli.main(args(synthetic, base / "r2"), **kwargs(Net({}))) == 2              # synthetic is not live evidence
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--replay-dir", str(replay),
                     "--history-dir", str(base / "h2"), "--out-dir", str(base / "r3")], now=fake_now,
                    clock=Clock(), sleep=lambda s: None) == 2                          # synthetic never feeds history


def test_nothing_in_the_language_policy_path_grants_application_authority(tmp_path, files, capsys):
    base, common, _ = files
    cli = load_cli()
    allow_path, out, _ = _live_run(cli, base, common, capsys, {i: nl(i, NL_SPONSOR_YES + NL_INFO) for i in range(1, 6)})
    assert cli.main(["reassess", *common, "--allowlist", str(allow_path), "--evidence-dir", str(out),
                     "--history-dir", str(base / "h"), "--out-dir", str(base / "r")], **kwargs(Net({}))) == 0
    capsys.readouterr()
    handoff = json.loads((base / "r" / "reassessed-f06-handoff.json").read_text())
    assert handoff["candidates"] == [] and handoff["first_candidate_for_review"] is None
    assert handoff[lp.WATCHLIST]["count"] == 5 and handoff[lp.WATCHLIST]["application_queue_authority"] is False
    assert handoff["executable"] is False and handoff["submit_authorized"] is False


# ---- G. regressions from the real offline reassessment (exact employer sentence patterns) -----------------
REAL_SALARY = [
    ("Brutomaandsalaris tussen € 6.120,- en € 8.850,- afhankelijk van je werkervaring, variabele prestatie beloning, "
     "een vaste onkostenvergoeding.", 6120.0, 8850.0),
    ("Brutomaandsalaris tussen € 5.085,- en € 6.230,-", 5085.0, 6230.0),
    ("Brutomaandsalaris tussen €3.965,- en €5.050,-", 3965.0, 5050.0),
]


@pytest.mark.parametrize("text,low,high", REAL_SALARY)
def test_real_compound_gross_monthly_salary_ranges_are_exact_and_never_net(text, low, high):
    c = ev(text).compensation
    assert (c["monthly_min"], c["monthly_max"], c["basis"], c["currency"], c["period_stated"]) == \
        (low, high, "gross", "EUR", "month")
    assert "Brutomaandsalaris" in c["quote"]


def test_a_base_salary_range_coexists_with_bonus_and_allowance_but_benefits_are_never_salary():
    assert ev(REAL_SALARY[0][0]).compensation["monthly_max"] == 8850.0          # bonus / allowance words did not veto it
    for text in ("Reiskostenvergoeding € 150 per maand.", "Bonus € 500 per maand en salaris op aanvraag.",
                 "Vaste onkostenvergoeding € 200 per maand.", "Salaris naar rato van ervaring."):
        assert ev(text).compensation is None, text
    assert ev("Nettomaandsalaris € 3.000 per maand.").compensation["basis"] == "net"     # net is stated, never derived
    assert ev("Salaris € 3.000.").compensation is None                              # amount without a period


def test_real_mandatory_education_keeps_the_preferred_specialisation_apart():
    e = ev("Een afgeronde WO-masteropleiding, bij voorkeur op het snijvlak van bedrijfskunde/economie en technologie.")
    (edu,) = e.education_requirements
    assert (edu["level"], edu["kind"], edu["completed_stated"]) == ("wo_master", "mandatory", True)


def test_real_minimum_years_stay_mandatory_when_only_the_work_environment_is_preferred():
    e = ev("Met minimaal 6-7+ jaar relevante werkervaring, bij voorkeur binnen consultancy of een grote organisatie.")
    (x,) = e.experience_requirements
    assert (x["min_years"], x["max_years"], x["kind"]) == (6, None, "mandatory")          # "6-7+" is open ended
    e = ev("Je hebt minimaal 3 jaar relevante werkervaring met organisatieverandering en digitale transformaties, "
           "bij voorkeur binnen consultancy.")
    (x,) = e.experience_requirements
    assert (x["min_years"], x["kind"]) == (3, "mandatory") and "bij voorkeur" in x["quote"]


def test_preference_negation_and_condition_safeguards_still_hold_after_the_scope_fix():
    def kinds(text):
        e = ev(text)
        return [x["kind"] for x in e.experience_requirements] + [x["kind"] for x in e.education_requirements]

    assert kinds("Bij voorkeur 5 jaar ervaring met Dynamics.") == ["preferred"]
    assert kinds("Een afgeronde HBO opleiding is een pré.") == ["preferred"]
    assert kinds("Minimaal 3 jaar werkervaring is een pluspunt.") == ["preferred"]
    assert kinds("Je hebt geen 3 jaar werkervaring nodig.") == ["conditional"]
    assert kinds("Minimaal 3 jaar werkervaring, tenzij je een master hebt.") == ["conditional", "conditional"]
    assert kinds("Een afgeronde WO master, bij voorkeur een PhD.") == ["mandatory", "preferred"]
    assert kinds("Een afgeronde WO master (MSc) is vereist.") == ["mandatory"]


def test_real_sentences_flow_into_the_report_and_leave_policy_and_scoring_untouched():
    qualifications = ("<ul><li>Een afgeronde WO-masteropleiding, bij voorkeur op het snijvlak van bedrijfskunde/economie en "
                      "technologie.</li><li>Met minimaal 6-7+ jaar relevante werkervaring, bij voorkeur binnen consultancy "
                      "of een grote organisatie.</li></ul>")
    c, _ = decide_for(nl(1, "<p>" + REAL_SALARY[1][0] + "</p>" + NL_SPONSOR_YES, qualifications=qualifications))
    pe = c["posting_evidence"]
    assert (pe["compensation"]["monthly_min"], pe["compensation"]["monthly_max"]) == (5085.0, 6230.0)
    assert [(e["min_years"], e["kind"]) for e in pe["experience_requirements"]] == [(6, "mandatory")]
    assert [(e["level"], e["kind"]) for e in pe["education_requirements"]] == [("wo_master", "mandatory")]
    assert c["language_policy"]["action"] == "watchlist" and c["verdict"]["status"] == lp.WATCHLIST   # policy unchanged
    assert lp.POLICY_VERSION == "EJS-LANG-SPONSOR-POLICY-1.0" and sha256(criteria_text().encode()).hexdigest() == SHA


# ---- H. explicit medium windows; previous high allowlists remain reproducible ------------------------
def test_medium_windows_are_bounded_reproducible_and_do_not_relabel_rows():
    f04 = f04_report([row(1)] + [row(i, "Data Analyst") for i in range(10, 17)] + [row(60, "Accountant")])
    kwargs = dict(f04_report_sha256=F04_SHA, lineage=lineage_of(f04))
    high = build_allowlist(f04, **kwargs)
    assert [t["posting_id"] for t in high["targets"]] == [pid(1)]
    assert verify_allowlist(high, f04, **kwargs) == high
    first = build_allowlist(f04, medium_offset=0, **kwargs)
    second = build_allowlist(f04, medium_offset=5, **kwargs)
    medium = [r["posting_id"] for r in f04["vacancies"] if r["suitability"]["classification"] == "preliminary_medium"]
    assert len(medium) == 7
    assert [t["posting_id"] for t in first["targets"]] == medium[:5]
    assert [t["posting_id"] for t in second["targets"]] == medium[5:]
    assert first["selection"]["medium_offset"] == 0 and second["selection"]["medium_offset"] == 5
    assert first["request_policy"]["max_requests"] == 5 and second["request_policy"]["max_requests"] == 2
    assert verify_allowlist(first, f04, **kwargs) == first
    assert verify_allowlist(second, f04, **kwargs) == second
    assert dv.allowlist_digest(first) != dv.allowlist_digest(second)
    for offset in (-1, 7, True, "0"):
        with pytest.raises(DetailVerificationError, match="MEDIUM_OFFSET_OUT_OF_RANGE"):
            build_allowlist(f04, medium_offset=offset, **kwargs)
    for changed in (dict(rule="preliminary_low"), dict(medium_offset=1), dict(medium_offset=None)):
        tampered = copy.deepcopy(first)
        tampered["selection"].update(changed)
        with pytest.raises(DetailVerificationError):
            verify_allowlist(tampered, f04, **kwargs)


def test_medium_cli_uses_same_approval_and_budget_guards(files, capsys):
    base, common, f04 = files
    cli = load_cli()
    out = base / "medium-allow"
    assert cli.main(["prepare", *common, "--medium-offset", "0", "--pilot-country", "NL", "--out-dir", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["network"] is False and summary["medium_rows_in_report"] == 1
    allow_path = out / "detail-allowlist.json"
    allow = json.loads(allow_path.read_text())
    assert [t["posting_id"] for t in allow["targets"]] == [pid(50)]
    digest = dv.allowlist_digest(allow)
    net = Net({url_of(50): ok(sr(50))})
    argv = ["verify", *common, "--allowlist", str(allow_path), "--live",
            "--budget-dir", str(base / "budget"), "--out-dir", str(base / "medium-result")]
    kwargs = dict(live_transport=net, now=fake_now, clock=Clock(), sleep=lambda s: None)
    assert cli.main(argv, **kwargs) == 2 and net.calls == []
    assert cli.main([*argv, "--approve-allowlist-sha256", digest], **kwargs) == 0
    assert [u for u, _ in net.calls] == [url_of(50)]
    assert cli.main([*argv[:-1], str(base / "medium-again"), "--approve-allowlist-sha256", digest], **kwargs) == 2
    assert len(net.calls) == 1


# Geographic pilot scope: selection only, never a change in F04 eligibility.
def test_scoped_shortlist_uses_verified_configured_countries_only():
    f04 = f04_report([row(1, country="US"), row(2, country=None), row(3, country="IE"),
                      row(4, country="NL"), row(5, country=["NL", "US"]), row(6, country="NL")])
    kwargs = dict(f04_report_sha256=F04_SHA, lineage=lineage_of(f04))
    allow = build_allowlist(f04, pilot_countries=["NL"], **kwargs)
    assert [x["posting_id"] for x in allow["targets"]] == [pid(4), pid(6)]
    assert allow["selection"]["pilot_countries"] == ["NL"]
    assert allow["selection"]["scope_excluded"] == 4
    assert allow["selection"]["high_rows_in_report"] == 2
    assert verify_allowlist(allow, f04, **kwargs) == allow
    assert f04["listings_kept"] == 6
    forged = copy.deepcopy(allow)
    forged["selection"]["pilot_countries"] = ["IE"]
    with pytest.raises(DetailVerificationError, match="ALLOWLIST_NOT_REPRODUCED"):
        verify_allowlist(forged, f04, **kwargs)


def test_scoped_medium_window_and_canonical_scope_order():
    f04 = f04_report([row(1, "Data Analyst", country="US"), row(2, "Data Analyst", country="NL"),
                      row(3, "Data Analyst", country="IE"), row(4, "Data Analyst", country="NL")])
    kwargs = dict(f04_report_sha256=F04_SHA, lineage=lineage_of(f04), medium_offset=1, limit=1)
    allow = build_allowlist(f04, pilot_countries=["NL", "IE"], **kwargs)
    assert [x["posting_id"] for x in allow["targets"]] == [pid(4)]
    assert allow["selection"]["medium_rows_in_report"] == 3
    assert allow["selection"]["scope_excluded"] == 1
    assert allow == build_allowlist(f04, pilot_countries=["IE", "NL"], **kwargs)


@pytest.mark.parametrize("scope", [[], ["nl"], ["NL", "NL"], ["Netherlands"], "NL", [None], ["ZZ"], ["NL", "ZZ"]])
def test_invalid_pilot_scope_fails_closed(scope):
    f04, _ = world()
    with pytest.raises(DetailVerificationError, match="PILOT_COUNTRIES_INVALID"):
        build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04), pilot_countries=scope)


def test_lineage_rejects_forged_binding_even_if_suitability_unchanged():
    f04 = f04_report([row(1)])
    forged = copy.deepcopy(f04)
    forged["vacancies"][0]["url"] = f"https://jobs.smartrecruiters.com/Other/{pid(1)}"
    with pytest.raises(DetailVerificationError, match="F04_REPORT_NOT_REPRODUCIBLE"):
        lineage_call(forged, f04.projection)


def test_cli_unscoped_allowlist_is_offline_only_even_with_quoted_digest(files):
    base, common, _ = files
    cli = load_cli()
    out = base / "unscoped"
    assert cli.main(["prepare", *common, "--out-dir", str(out)]) == 0
    path = out / "detail-allowlist.json"
    digest = dv.allowlist_digest(json.loads(path.read_text()))
    net = Net({url_of(i): ok(sr(i)) for i in range(1, 6)})
    assert cli.main(["verify", *common, "--allowlist", str(path), "--live",
                     "--approve-allowlist-sha256", digest, "--budget-dir", str(base / "budget"),
                     "--out-dir", str(base / "live")], live_transport=net, now=fake_now,
                    clock=Clock(), sleep=lambda _: None) == 2
    assert net.calls == [] and not (base / "budget").exists() and not (base / "live").exists()


@pytest.mark.parametrize("change", [
    {"observation": "synthetic_country_replay"}, {"observation": "public_listing_feed"},
    {"feed_url": None}, {"feed_url": "https://x"}, {"countries": ["NL", None]},
    {"countries": ["NL", ""]}, {"countries": ["NL", "NL"]}, {"countries": "NL"},
    {"countries": ["nl"]}, {"countries": ["NL", "ZZ"]},
])
def test_scoped_selection_refuses_partial_or_synthetic_country_evidence(change):
    listing = row(1)
    listing["discovery_evidence"].update(change)
    f04 = f04_report([listing])
    allow = build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04), pilot_countries=["NL"])
    assert allow["targets"] == [] and allow["selection"]["scope_excluded"] == 1


def test_scoped_selection_requires_verified_projection_label():
    f04 = f04_report([row(1)])
    f04["source_projection"] = "offline_expansion"
    allow = build_allowlist(f04, f04_report_sha256=F04_SHA, lineage=lineage_of(f04), pilot_countries=["NL"])
    assert allow["targets"] == []


def test_cli_live_refuses_direct_projection_even_with_matching_pins(files):
    base, common, f04 = files
    cli = load_cli()
    path = base / "direct-projection.json"
    path.write_text(json.dumps(f04.projection, sort_keys=True))
    args = list(common)
    args[args.index("--f02-report") + 1] = str(path)
    args[args.index("--expected-f02-sha256") + 1] = sha256(path.read_bytes()).hexdigest()
    allow_path = prepare(cli, base, args)
    digest = dv.allowlist_digest(json.loads(allow_path.read_text()))
    net = Net({url_of(i): ok(sr(i)) for i in range(1, 6)})
    assert cli.main(["verify", *args, "--allowlist", str(allow_path), "--live",
                     "--approve-allowlist-sha256", digest, "--budget-dir", str(base / "budget"),
                     "--out-dir", str(base / "live")], live_transport=net, now=fake_now,
                    clock=Clock(), sleep=lambda _: None) == 2
    assert net.calls == [] and not (base / "budget").exists()
