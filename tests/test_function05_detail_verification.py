"""FUNCTION-05 acceptance tests. Synthetic criteria, listings and posting bytes only. No real network, ever."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import socket
import stat
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from ejs.services import detail_verification as dv
from ejs.services.detail_verification import (AUTHORITY, DETAIL_HOST, DetailVerificationError, build_allowlist,
                                               build_handoff, build_report, check_f04_reproducible, detail_url,
                                               request_details, verify_allowlist, verify_details)
from ejs.services.discovery import DiscoveryError
from ejs.services.http_carrier import (REQUEST_HEADERS, HostNotAllowedError, HttpCarrier, ReplayCarrier,
                                       urllib_transport)
from ejs.services.suitability import evaluate_report
from ejs.services.suitability_evidence import detail_from_smartrecruiters
from tests.test_function04_suitability import criteria, criteria_text, report

TENANT = "Acme"
SHA = sha256(criteria_text().encode()).hexdigest()
RULE = criteria().rule_version
F04_SHA = "b" * 64
NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
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


def row(i: int, role: str = "Senior Business Analyst", *, country: str | None = "NL", url: str | None = None,
        ats: str = "smartrecruiters", tenant: str = TENANT, region: str = "global") -> dict:
    return {"company": "Acme", "role": role, "location": f"City{i}", "url": url if url is not None else
            f"https://jobs.smartrecruiters.com/{tenant}/{pid(i)}", "ats_kind": ats, "source": "employer_watchlist",
            "tenant": tenant, "region": region, "posting_id": pid(i), "identity_status": "known",
            "similarity_key": f"acme|{role.casefold()}|city{i}",
            "discovery_evidence": {"observation": "live_country_feed", "feed_url": "https://x",
                                   "countries": [country] if country else []}}


def f04_report(rows: list[dict]) -> dict:
    return evaluate_report(report(*rows), criteria(), criteria_sha256=SHA, expected_criteria_sha256=SHA,
                           expected_rule_version=RULE)


def world(highs: int = 5, *, extra: list[dict] | None = None) -> tuple[dict, dict]:
    rows = [row(i) for i in range(1, highs + 1)] + [row(50, "Data Analyst"), row(60, "Accountant")] + (extra or [])
    f04 = f04_report(rows)
    return f04, build_allowlist(f04, f04_report_sha256=F04_SHA)


def sr(i: int, *, active=True, ref="REQ-1", text=APPLY_TEXT, tenant=TENANT, posting=None, posting_url="default",
       qualifications="<ul><li>You must have 3 years of ERP experience.</li><li>Dutch is a plus.</li></ul>") -> bytes:
    p = posting or pid(i)
    data = {"id": p, "company": {"identifier": tenant}, "active": active,
            "jobAd": {"sections": {"jobDescription": {"text": "<ul><li>Run workshops.</li><li>Write specifications.</li></ul>"},
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
    verification = verify_details(f04, allow, observations, criteria(), criteria_sha256=SHA,
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
    allow = build_allowlist(f04, f04_report_sha256=F04_SHA)
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
    again = build_allowlist(world()[0], f04_report_sha256=F04_SHA)
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
    allow = build_allowlist(f04_report(rows), f04_report_sha256=F04_SHA)
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
            build_allowlist(bad, f04_report_sha256=F04_SHA)


@pytest.mark.parametrize("limit", [0, 6, -1, True, "5"])
def test_limit_is_bounded_to_five(limit):
    with pytest.raises(DetailVerificationError, match="LIMIT_OUT_OF_RANGE"):
        build_allowlist(world()[0], f04_report_sha256=F04_SHA, limit=limit)


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
        verify_allowlist(doc, f04, f04_report_sha256=F04_SHA)


def test_verify_allowlist_accepts_the_exact_allowlist():
    f04, allow = world()
    assert verify_allowlist(allow, f04, f04_report_sha256=F04_SHA) == allow


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
def test_active_identity_bound_posting_with_requisition_is_suitable():
    f04, allow = world(highs=1)
    net, _, observations, ver, _ = run(f04, allow, {url_of(1): ok(sr(1))})
    c = ver["candidates"][0]
    raw = observations[0]["raw"]
    assert c["identity"] == {"status": "bound", "reasons": []}
    assert (c["availability"], c["requisition_id"]) == ("active", "REQ-1")
    assert c["verdict"]["status"] == "suitable"
    assert c["reevaluation"]["classification"] == "verified_apply_candidate"
    assert c["evidence"] == {"raw_sha256": sha256(raw).hexdigest(), "raw_bytes": len(raw)}
    assert c["request"] == {"outcome": "ok", "http_status": None, "attempted": 1, "observed_at": "2026-11-26T12:00:00Z"}
    assert c["canonical_url"] == f"https://jobs.smartrecruiters.com/{TENANT}/{pid(1)}"
    assert ver["verdict_counts"] == {"suitable": 1, "incomplete_evidence": 0, "confirmed_incompatible": 0}


def test_responsibilities_and_mandatory_qualifications_are_employer_wording_only():
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1))})
    s = ver["candidates"][0]["sections"]
    assert s["job_description_lines"] == ["Run workshops.", "Write specifications."]
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
def test_identity_mismatch_is_never_evaluated_and_never_suitable(raw, reason):
    f04, allow = world(highs=1)
    _, _, _, ver, _ = run(f04, allow, {url_of(1): ok(raw)})
    c = ver["candidates"][0]
    assert c["identity"]["reasons"] == [reason] and c["identity"]["status"] != "bound"
    assert c["reevaluation"] is None and c["sections"] is None and c["requisition_id"] is None
    assert c["verdict"]["status"] == "incomplete_evidence"
    assert ver["reevaluation_summary"]["details_applied"] == 0


@pytest.mark.parametrize("ref", [None, "", "bad ref!", 12345])
def test_missing_or_invalid_requisition_id_is_never_suitable(ref):
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


def test_w2_eligibility_condition_keeps_a_candidate_out_of_suitable():
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
                                "observed_at": "2026-11-26T12:00:00Z"} for c in rest)
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
    assert [c["verdict"]["status"] for c in ver["candidates"][:2]] == ["suitable", "suitable"]


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
    direct = evaluate_report(dv.report_to_discovery(f04), criteria(),
                             details={(TENANT, pid(i)): detail_from_smartrecruiters(observations[i - 1]["raw"], TENANT, pid(i))
                                      for i in (1, 2)},
                             criteria_sha256=SHA, expected_criteria_sha256=SHA, expected_rule_version=RULE)
    expected = {v["posting_id"]: dv._compact(v["suitability"]) for v in direct["vacancies"]}
    for c in ver["candidates"]:
        assert c["reevaluation"] == expected[c["posting_id"]]
        assert c["reevaluation"]["evidence_level"] == "vacancy_details"
        assert c["reevaluation"]["rule_version"] == RULE
    assert ver["reevaluation_summary"]["details_applied"] == 2


def test_f04_report_reproducibility_check_rejects_a_hand_edited_report():
    f04 = f04_report([row(i) for i in range(1, 4)])
    check_f04_reproducible(f04, criteria(), criteria_sha256=SHA, expected_criteria_sha256=SHA, expected_rule_version=RULE)
    edited = copy.deepcopy(f04)
    edited["vacancies"][0]["suitability"]["classification"] = "preliminary_medium"
    with pytest.raises(DetailVerificationError, match="F04_REPORT_NOT_REPRODUCIBLE"):
        check_f04_reproducible(edited, criteria(), criteria_sha256=SHA, expected_criteria_sha256=SHA,
                               expected_rule_version=RULE)
    with pytest.raises(DetailVerificationError, match="CRITERIA_NOT_APPROVED"):
        check_f04_reproducible(f04, criteria(), criteria_sha256=SHA, expected_criteria_sha256="d" * 64,
                               expected_rule_version=RULE)


# ---------------------------------------------------------------------------------------- Function 06 hand-off
def test_handoff_orders_suitable_before_incomplete_and_lists_confirmed_incompatible_apart():
    f04, allow = world()
    responses = {url_of(1): ok(sr(1, active=False)), url_of(2): ok(sr(2, ref=None)), url_of(3): ok(sr(3)),
                 url_of(4): ok(sr(4, text="<p>We do not offer visa sponsorship.</p>")),
                 url_of(5): ok(sr(5, text="<p>Nothing relevant.</p>"))}
    _, carrier, _, ver, _ = run(f04, allow, responses)
    rep = build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256=dv.allowlist_digest(allow),
                       mode="offline_replay", started_at="2026-11-26T12:00:00Z")
    hand = build_handoff(rep)
    assert [c["posting_id"] for c in hand["candidates"]][0] == pid(3)
    assert [c["status"] for c in hand["candidates"]] == ["suitable", "incomplete_evidence", "incomplete_evidence"]
    assert {c["posting_id"] for c in hand["candidates"]} == {pid(2), pid(3), pid(5)}
    assert {c["posting_id"] for c in hand["confirmed_incompatible"]} == {pid(1), pid(4)}
    assert hand["first_suitable_candidate"]["posting_id"] == pid(3)
    assert hand["first_suitable_candidate"]["requisition_id"] == "REQ-1"
    assert [c["handoff_rank"] for c in hand["candidates"]] == [1, 2, 3]
    assert rep["verdict_counts"] == {"suitable": 1, "incomplete_evidence": 2, "confirmed_incompatible": 2}


def test_handoff_is_non_executable_and_carries_no_authority_or_candidate_inference():
    f04, allow = world(highs=2)
    _, carrier, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1)), url_of(2): ok(sr(2, text="<p>x</p>"))})
    rep = build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256="a" * 64, mode="offline_replay",
                       started_at="s")
    for doc in (rep, build_handoff(rep)):
        assert doc["executable"] is False and doc["submit_authorized"] is False
        assert doc["authority"] == AUTHORITY and not any(v for k, v in doc["authority"].items() if k != "label_is_not_authority")
    hand = build_handoff(rep)
    assert all(c["candidate_qualification_match"] == "not_performed" for c in hand["candidates"])
    assert "separate_function_06_authorization" in hand["prerequisites_before_function_06"]
    assert rep["requests"]["methods"] == ["GET"] and rep["requests"]["redirects_followed"] == 0


def test_handoff_without_a_suitable_candidate_names_the_next_step_and_invents_none():
    f04, allow = world(highs=2)
    _, carrier, _, ver, _ = run(f04, allow, {url_of(1): ok(sr(1, ref=None)), url_of(2): ok(sr(2, active=False))})
    hand = build_handoff(build_report(allow, ver, carrier, criteria_sha256=SHA, allowlist_sha256="a" * 64,
                                      mode="offline_replay", started_at="s"))
    assert hand["first_suitable_candidate"] is None and hand["next_step"].startswith("no_suitable_candidate_yet")
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
    f04 = f04_report(rows)
    f04_path = tmp_path / "suitability-report.json"
    f04_path.write_text(json.dumps(f04, sort_keys=True, indent=1))
    common = ["--f04-report", str(f04_path), "--criteria", str(crit), "--expected-criteria-sha256", SHA,
              "--expected-rule-version", RULE]
    return tmp_path, common, f04


def prepare(cli, tmp_path, common, name="allow"):
    out = tmp_path / name
    assert cli.main(["prepare", *common, "--out-dir", str(out)]) == 0
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
    assert summary["verdicts"] == {"confirmed_incompatible": 0, "incomplete_evidence": 1, "suitable": 4}
    assert summary["first_suitable"]["posting_id"] == pid(1)

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
    assert handoff["executable"] is False and handoff["first_suitable_candidate"]["posting_id"] == pid(1)

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
    assert summary["verdicts"]["suitable"] == 0 and summary["first_suitable"] is None
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
    assert cli.main(["verify", *common, "--allowlist", str(allow_path), "--live", "--approve-allowlist-sha256", digest,
                     "--out-dir", str(out)], live_transport=net, now=fake_now, clock=Clock(), sleep=sleeps.append) == 0
    assert [u for u, _ in net.calls] == [url_of(i) for i in range(1, 6)]
    assert all(h == REQUEST_HEADERS for _, h in net.calls) and len(sleeps) == 4 and min(sleeps) > 0.99
    report = json.loads((out / "detail-verification-report.json").read_text())
    assert report["transport_mode"] == "live_allowlisted_get" and report["requests"]["attempted"] == 5
    assert report["requests"]["approved"] == 5 and report["requests"]["retries"] == 0


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
