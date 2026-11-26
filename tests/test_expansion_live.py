"""FUNCTION-02 live-feed adapter tests. Every transport is a fake; the network is denied."""
import json
import socket
from urllib.parse import parse_qs, urlparse

import pytest

from ejs.services.application_bridge import prepare_application_report
from ejs.services.criteria_loader import load_criteria
from ejs.services.expansion_replay import (
    COUNTRIES, LIVE_BUNDLE_MAX_REQUESTS, LIVE_MAX_REQUESTS, RESEARCH, LiveObservation, begin_invocation,
    effective_rechecks, empty_evidence, initial_checkpoint, live_page, live_page_url, live_replay_batch,
    recheck_first_page, record_invocation, remaining_requests, replay_batch, settle_in_flight,
    spent_requests, validate_evidence,
)
from ejs.services.expansion_shadow import build_expansion_report, project_for_application_bridge
from ejs.services.http_carrier import HttpCarrier, HttpStatusError
from scripts import expansion_replay_store as store, shadow_discovery as cli
from tests.unit.phase1a_support import CRITERIA_YAML

KPMG, DEVO = "KPMGNederland", "Devoteam"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("tests must inject a fake transport")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def feed(country, offset=0, total=0, ids=(), *, tenant=KPMG):
    """Provider-shaped body: lowercase country, no tenant/country metadata, extra provider fields."""
    return json.dumps({
        "offset": offset, "limit": 100, "totalFound": total,
        "content": [{"id": i, "name": f"Analyst {i}", "refNumber": f"R-{i}", "releasedDate": "2026-11-26T00:00:00Z",
                     "location": {"city": "Synthetic city", "country": country.lower(), "remote": False},
                     "company": {"identifier": tenant.upper(), "name": "Synthetic"}} for i in ids],
    }).encode()


class Feed:
    """Fake provider keyed by (tenant, country, offset); records every URL it serves."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        parsed = urlparse(url)
        tenant = parsed.path.split("/")[3]
        q = parse_qs(parsed.query)
        page = self.pages.get((tenant, q["country"][0], int(q["offset"][0])))
        if isinstance(page, int):
            return page, b""
        return (200, page) if page is not None else (404, b"")


def carrier(transport, **kw):
    return HttpCarrier(enabled=True, transport=transport, clock=lambda: 0.0, sleep=lambda s: None, **kw)


def run(checkpoint, transport, approved, **kw):
    c = carrier(transport, **kw.pop("carrier_kw", {}))
    return live_replay_batch(checkpoint, c, c.begin_employer, approved=frozenset(approved), **kw), c


def test_url_uses_documented_lowercase_country_filter_and_page_size():
    url = live_page_url(KPMG, "NL", 100)
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.hostname, parsed.path) == ("https", "api.smartrecruiters.com",
                                                             "/v1/companies/KPMGNederland/postings")
    assert parse_qs(parsed.query) == {"country": ["nl"], "offset": ["100"], "limit": ["100"]}
    for bad in (("Rituals1", "NL", 0), (KPMG, "FR", 0), (KPMG, "nl", 0)):
        with pytest.raises(ValueError):
            live_page_url(*bad, 0) if len(bad) == 2 else live_page_url(*bad)


def test_provider_shaped_page_is_accepted_and_input_is_not_mutated():
    body = feed("NL", total=1, ids=("744000000000001",))
    snapshot = json.loads(body)
    page = live_page(KPMG, "NL", body)
    assert json.loads(body) == snapshot
    assert (page["tenant"], page["country"], page["offset"]) == (KPMG, "NL", 0)
    result = replay_batch(initial_checkpoint(), {(KPMG, "NL", 0): page}, max_pages=1)
    assert [x["posting_id"] for x in result.listings] == ["744000000000001"]
    assert result.listings[0]["location"] == "Synthetic city, NL"


@pytest.mark.parametrize("mutate", [
    lambda d: d["content"][0]["location"].update(country="de"),          # filter ignored by provider
    lambda d: d["content"][0].pop("location"),
    lambda d: d["content"][0]["company"].update(identifier="OtherTenant"),
    lambda d: d.update(totalFound=0),                                  # total below what was returned
    lambda d: d.update(offset=100),                                       # wrong offset echo
    lambda d: d["content"].append(dict(d["content"][0])),                 # repeated id within a page
])
def test_drifted_pages_fail_the_pair_and_leave_the_cursor_unchanged(mutate):
    data = json.loads(feed("NL", total=1, ids=("a",)))
    mutate(data)
    result, _ = run(initial_checkpoint(), Feed({(KPMG, "nl", 0): json.dumps(data).encode()}), {(KPMG, "NL")})
    first = result.checkpoint.cursors[0]
    assert (first.status, first.offset, first.total, result.listings) == ("error", 0, None, ())


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"content": 1}'])
def test_unparseable_pages_fail_the_pair(body):
    result, _ = run(initial_checkpoint(), Feed({(KPMG, "nl", 0): body}), {(KPMG, "NL")})
    assert result.checkpoint.cursors[0].status == "error"


def test_only_approved_pairs_are_requested():
    pages = {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",)),
             (DEVO, "nl", 0): feed("NL", total=1, ids=("b",), tenant=DEVO),
             (DEVO, "de", 0): feed("DE", total=1, ids=("x",), tenant=DEVO)}
    transport = Feed(pages)
    result, c = run(initial_checkpoint(), transport, {(KPMG, "NL"), (DEVO, "NL")})
    assert sorted(urlparse(u).path.split("/")[3] + parse_qs(urlparse(u).query)["country"][0] for u in transport.urls) \
        == ["Devoteamnl", "KPMGNederlandnl"]
    assert result.pages == c.total == 2
    done = {(x.tenant, x.country) for x in result.checkpoint.cursors if x.status == "complete"}
    assert done == {(KPMG, "NL"), (DEVO, "NL")}
    # nothing else moved: all other 241 pairs are still pristine
    assert sum(1 for x in result.checkpoint.cursors if x.status == "pending" and x.offset == 0) == 241


def test_empty_approved_scope_makes_no_request():
    transport = Feed({})
    result, c = run(initial_checkpoint(), transport, set())
    assert (transport.urls, result.pages, c.total) == ([], 0, 0)


@pytest.mark.parametrize("scope", [
    {("Rituals1", "NL")},                      # tenant outside the research scope
    {(DEVO, "DE")},                            # skips NL and IE: would break the checkpoint ordering invariant
    {(DEVO, "NL"), (DEVO, "DE")},
    {(KPMG, "FR")},
])
def test_unapproved_or_out_of_order_scope_is_rejected(scope):
    c = carrier(Feed({}))
    with pytest.raises(ValueError, match="EXPANSION_LIVE_SCOPE_INVALID"):
        live_replay_batch(initial_checkpoint(), c, c.begin_employer, approved=frozenset(scope))


def test_pagination_across_pages_and_cross_country_dedup():
    ids = [f"id{n:03d}" for n in range(150)]
    pages = {(KPMG, "nl", 0): feed("NL", total=150, ids=ids[:100]),
             (KPMG, "nl", 100): feed("NL", offset=100, total=150, ids=ids[100:]),
             (KPMG, "ie", 0): feed("IE", total=2, ids=("id000", "extra"))}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL"), (KPMG, "IE")})
    assert result.pages == 3 and result.duplicates == 1
    assert len(result.listings) == 151
    assert [x.status for x in result.checkpoint.cursors[:2]] == ["complete", "complete"]


def test_http_failure_blocks_only_that_tenant():
    pages = {(KPMG, "nl", 0): 503,
             (DEVO, "nl", 0): feed("NL", total=1, ids=("b",), tenant=DEVO)}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL"), (KPMG, "IE"), (DEVO, "NL")})
    status = {(x.tenant, x.country): x.status for x in result.checkpoint.cursors}
    assert status[(KPMG, "NL")] == "error" and status[(KPMG, "IE")] == "pending"
    assert status[(DEVO, "NL")] == "complete"
    assert [x["posting_id"] for x in result.listings] == ["b"]
    assert c.log[0]["status"] == 503


def test_tighter_carrier_stops_the_batch_without_failing_pairs():
    pages = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a",)),
             (KPMG, "nl", 1): feed("NL", offset=1, total=2, ids=("b",))}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL")}, carrier_kw={"max_requests": 1})
    cursor = result.checkpoint.cursors[0]
    assert (result.pages, c.total, cursor.status, cursor.offset) == (1, 1, "pending", 1)
    resumed, _ = run(result.checkpoint, Feed(pages), {(KPMG, "NL")})
    assert resumed.checkpoint.cursors[0].status == "complete"


def test_batch_never_exceeds_five_pages_per_employer_or_60_total():
    ids = [f"i{n:04d}" for n in range(800)]
    pages = {(KPMG, "nl", o): feed("NL", offset=o, total=800, ids=ids[o:o + 100]) for o in range(0, 800, 100)}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL")})
    assert result.pages == 5 and result.checkpoint.cursors[0].offset == 500



MARKETS = {tenant: "Synthetic market" for _, tenant, _, _ in RESEARCH}
STABLE_KPMG_NL = {"tenant": KPMG, "country": "NL", "status": "stable", "reason": "synthetic"}


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def live_report(tmp_path, pages, approved, *, max_pages=60, rechecks=()):
    """Report built directly from a live batch and durable-shaped evidence."""
    criteria = load_criteria(_write(tmp_path / "c.yaml", CRITERIA_YAML))
    result, c = run(initial_checkpoint(), Feed(pages), approved, max_pages=max_pages)
    evidence = record_invocation(begin_invocation(empty_evidence(), 5),
                                 LiveObservation(requests=tuple(c.log), rechecks=tuple(rechecks)))
    return build_expansion_report(result.checkpoint, list(result.listings), MARKETS, criteria,
                                  evidence=evidence, approved=frozenset(approved))


def test_report_carries_live_evidence_and_cumulative_request_accounting(tmp_path):
    report = live_report(tmp_path, {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}, {(KPMG, "NL")},
                         rechecks=[STABLE_KPMG_NL])
    assert report["transport_mode"] == "live_country_feed" and report["network_requests"] == 1
    assert report["requests_scope"] == "bundle_cumulative" and report["execution_authorized"] is False
    assert report["request_accounting"] == {
        "scope": "bundle_cumulative", "budget": 5, "spent": 1, "remaining": 4, "uncertain_reserved": 0,
        "invocations": [{"seq": 1, "status": "completed", "reserved": 5, "requests": 1}]}
    assert report["request_log"][0]["invocation"] == 1
    assert report["employers"][0]["requests"] == 1
    assert report["vacancies"][0]["discovery_evidence"] == {
        "observation": "live_country_feed", "feed_url": live_page_url(KPMG, "NL", 0), "countries": ["NL"]}


def test_approved_scope_coverage_is_separate_from_research_coverage(tmp_path):
    report = live_report(tmp_path, {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}, {(KPMG, "NL")})
    coverage = report["coverage"]
    assert coverage["status"] == "completed" and coverage["measured_over"] == "approved_scope"
    assert coverage["counts"] == {"completed": 1, "incomplete": 0, "blocked": 0, "failed": 0, "not_approved": 242}
    assert coverage["research_scope"] == {"total_pairs": 243, "approved_pairs": 1, "completed_pairs": 1,
                                          "not_approved_pairs": 242, "research_coverage_complete": False}
    assert sum(p["status"] == "not_approved" for p in coverage["pairs"]) == 242
    employers = {e["tenant"]: e["status"] for e in report["employers"]}
    assert employers[KPMG] == "completed" and employers[DEVO] == "not_approved"
    assert "incomplete" not in employers.values()


def test_pending_failed_and_unapproved_pairs_still_report_honestly(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=150, ids=[f"k{n}" for n in range(100)]),
             (DEVO, "nl", 0): 503}
    report = live_report(tmp_path, pages, {(KPMG, "NL"), (DEVO, "NL"), (DEVO, "IE")})
    assert report["coverage"]["status"] == "failed"
    assert report["coverage"]["counts"] == {"completed": 0, "incomplete": 0, "blocked": 1, "failed": 2,
                                            "not_approved": 240}     # KPMG page 2 is missing (404): failed
    assert report["coverage"]["research_scope"]["research_coverage_complete"] is False


def test_synthetic_report_keeps_its_original_coverage_shape():
    criteria = load_criteria(_write_tmp(CRITERIA_YAML))
    report = build_expansion_report(initial_checkpoint(), [], MARKETS, criteria)
    assert report["coverage"]["counts"] == {"completed": 0, "incomplete": 243, "blocked": 0, "failed": 0}
    assert "research_scope" not in report["coverage"] and report["network_requests"] == 0


def _write_tmp(text):
    import tempfile, pathlib
    path = pathlib.Path(tempfile.mkdtemp()) / "c.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- durable evidence (pure) --------------------------------------------------------------------

def request(status=200):
    return {"url": live_page_url(KPMG, "NL", 0), "status": status, "bytes": 10}


def test_evidence_accounting_charges_interrupted_invocations_their_whole_reservation():
    e = begin_invocation(empty_evidence(), 4)
    assert spent_requests(e) == 4 and remaining_requests(e) == 1          # in flight counts before any reply
    settled = settle_in_flight(e)
    assert settled["in_flight"] is None and settled["invocations"][0]["status"] == "interrupted_unverified"
    assert spent_requests(settled) == 4 and remaining_requests(settled) == 1
    validate_evidence(settled)
    done = record_invocation(begin_invocation(empty_evidence(), 4), LiveObservation(requests=(request(),)))
    assert spent_requests(done) == 1 and remaining_requests(done) == 4
    with pytest.raises(ValueError):
        begin_invocation(settled, 2)                                       # more than what is left


def test_drift_and_unreadable_are_sticky_in_effective_rechecks():
    def check(status):
        return {"tenant": KPMG, "country": "NL", "status": status, "reason": status}

    e = empty_evidence()
    for status in ("drift", "stable"):
        e = record_invocation(begin_invocation(e, 1), LiveObservation(rechecks=(check(status),)))
    assert effective_rechecks(e)[(KPMG, "NL")]["status"] == "drift"
    e = empty_evidence()
    for status in ("not_checked", "stable"):
        e = record_invocation(begin_invocation(e, 1), LiveObservation(rechecks=(check(status),)))
    assert effective_rechecks(e)[(KPMG, "NL")]["status"] == "stable"
    e = record_invocation(begin_invocation(e, 1), LiveObservation(rechecks=(check("unreadable"),)))
    assert effective_rechecks(e)[(KPMG, "NL")]["status"] == "unreadable"


@pytest.mark.parametrize("tamper", [
    lambda e: e["invocations"][0].update(seq=2),
    lambda e: e["invocations"][0].update(requests=[request()] * 3, reserved=2),
    lambda e: e["invocations"][0].update(status="interrupted_unverified"),     # interrupted cannot list requests
    lambda e: e.update(budget=6),
    lambda e: e.update(in_flight={"seq": 5, "reserved": 1}),
    lambda e: e["rechecks"].append({"seq": 9, "tenant": KPMG, "country": "NL", "status": "stable", "reason": "x"}),
    lambda e: e["rechecks"].append({"seq": 1, "tenant": "Rituals1", "country": "NL", "status": "stable",
                                    "reason": "x"}),
    lambda e: e["invocations"][0]["requests"][0].update(status=None),
    lambda e: e.update(extra=1),
])
def test_tampered_evidence_is_rejected(tamper):
    e = record_invocation(begin_invocation(empty_evidence(), 4), LiveObservation(requests=(request(),)))
    validate_evidence(e)
    import copy
    e = copy.deepcopy(e)
    tamper(e)
    with pytest.raises(ValueError):
        validate_evidence(e)


# --- projection ---------------------------------------------------------------------------------

def test_verified_rows_pass_the_unchanged_bridge_and_exclusions_are_counted(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=1, ids=("744000000000001",))}
    report = live_report(tmp_path, pages, {(KPMG, "NL")}, rechecks=[STABLE_KPMG_NL])
    with pytest.raises(ValueError, match="discovery_schema"):
        prepare_application_report(report)           # direct hand-off stays refused
    projected = project_for_application_bridge(report)
    assert projected["excluded_rows"] == {"count": 0, "reasons": {}} and projected["atomic_snapshot"] is False
    prepared = prepare_application_report(projected)
    candidate = prepared["candidates"][0]
    assert (candidate["tenant"], candidate["posting_id"], candidate["location"]) == (KPMG, "744000000000001",
                                                                                      "Synthetic city, NL")
    assert candidate["readiness"] == "missing_evidence"
    assert prepared["executable"] is False and prepared["submit_authorized"] is False


def test_rows_from_a_pending_pair_never_reach_the_projection(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=150, ids=[f"k{n:03d}" for n in range(100)])}
    report = live_report(tmp_path, pages, {(KPMG, "NL")}, max_pages=1,
                         rechecks=[STABLE_KPMG_NL])                                  # even with a stable check
    assert report["listings_kept"] == 100 and report["coverage"]["counts"]["incomplete"] == 1
    projected = project_for_application_bridge(report)
    assert projected["listings_kept"] == 0 and projected["vacancies"] == []
    assert projected["excluded_rows"] == {"count": 100, "reasons": {"provenance_pair_not_completed": 100}}
    assert prepare_application_report(projected)["candidate_count"] == 0


def test_cross_country_duplicate_with_an_unverified_pair_is_excluded(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "n")),
             (KPMG, "ie", 0): feed("IE", total=3, ids=("a", "b"))}                  # IE pending; "a" seen in both
    report = live_report(tmp_path, pages, {(KPMG, "NL"), (KPMG, "IE")}, rechecks=[STABLE_KPMG_NL])
    by_id = {v["posting_id"]: v["discovery_evidence"]["countries"] for v in report["vacancies"]}
    assert by_id == {"a": ["NL", "IE"], "n": ["NL"], "b": ["IE"]}
    projected = project_for_application_bridge(report)
    assert [v["posting_id"] for v in projected["vacancies"]] == ["n"]
    assert projected["excluded_rows"] == {"count": 2, "reasons": {"provenance_pair_not_completed": 2}}


def test_rows_without_a_stable_recheck_or_with_drift_are_excluded(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "b"))}
    for rechecks, reason in (([], "provenance_pair_recheck_not_stable"),
                             ([{**STABLE_KPMG_NL, "status": "not_checked"}], "provenance_pair_recheck_not_stable"),
                             ([{**STABLE_KPMG_NL, "status": "drift"}], "provenance_pair_recheck_not_stable")):
        report = live_report(tmp_path, pages, {(KPMG, "NL")}, rechecks=rechecks)
        projected = project_for_application_bridge(report)
        assert projected["listings_kept"] == 0 and projected["excluded_rows"]["reasons"] == {reason: 2}


def test_rows_from_a_failed_pair_are_excluded(tmp_path):
    pages = {(KPMG, "nl", 0): feed("NL", total=150, ids=[f"k{n:03d}" for n in range(100)]),
             (KPMG, "nl", 100): 503}
    report = live_report(tmp_path, pages, {(KPMG, "NL")}, rechecks=[STABLE_KPMG_NL])
    assert report["coverage"]["status"] == "failed"
    assert project_for_application_bridge(report)["excluded_rows"]["count"] == 100


@pytest.mark.parametrize("tamper", [
    lambda r: r.update(transport_mode="offline_expansion"),
    lambda r: r.update(execution_authorized=True),
    lambda r: r["snapshot_consistency"].update(atomic_snapshot=True),
    lambda r: r.update(listings_kept=r["listings_kept"] + 1),
    lambda r: r["vacancies"][0]["discovery_evidence"].update(observation="synthetic_country_replay"),
    lambda r: r.pop("snapshot_consistency"),
    lambda r: r.update(schema="EJS-SHADOW-REPORT-2"),
])
def test_projection_refuses_unverified_reports(tmp_path, tamper):
    report = live_report(tmp_path, {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}, {(KPMG, "NL")},
                         rechecks=[STABLE_KPMG_NL])
    tamper(report)
    with pytest.raises(ValueError, match="EXPANSION_PROJECTION_REFUSED"):
        project_for_application_bridge(report)


# --- durable workflow (store + CLI) -------------------------------------------------------------

@pytest.fixture
def cli_inputs(tmp_path):
    root = tmp_path.resolve()
    watchlist = _write(root / "scope.csv", "Employer,Primary Market,Reference URL\n" + "".join(
        f"{name},Synthetic market,https://jobs.smartrecruiters.com/{tenant}\n" for name, tenant, _, _ in RESEARCH))
    return watchlist, _write(root / "criteria.yaml", CRITERIA_YAML), root


def cli_args(cli_inputs, out, *extra):
    watchlist, criteria, _ = cli_inputs
    return ["--watchlist", str(watchlist), "--criteria", str(criteria), "--out-dir", str(out),
            "--expansion-live", *extra]


def test_default_live_scope_is_exactly_the_owner_approved_pilot():
    from ejs.services.expansion_replay import LIVE_APPROVED_PAIRS
    assert LIVE_APPROVED_PAIRS == {("KPMGNederland", "NL")}
    assert LIVE_BUNDLE_MAX_REQUESTS == LIVE_MAX_REQUESTS == 5


def test_cli_refuses_without_an_approved_scope_before_any_request(cli_inputs, monkeypatch):
    import scripts.expansion_shadow as es0
    monkeypatch.setattr(es0, "LIVE_APPROVED_PAIRS", frozenset())

    def transport(*a):
        pytest.fail("no request may be made without an approved scope")

    import scripts.expansion_shadow as es
    monkeypatch.setattr(es, "urllib_transport", transport)
    with pytest.raises(SystemExit) as exc:
        cli.main(cli_args(cli_inputs, cli_inputs[2] / "out", "--expansion-init"))
    assert exc.value.code == 2
    assert not (cli_inputs[2] / "out").exists()


def test_live_options_are_mutually_exclusive_with_other_modes(cli_inputs):
    with pytest.raises(SystemExit):
        cli.main([*cli_args(cli_inputs, cli_inputs[2] / "out"), "--live"])
    with pytest.raises(SystemExit):
        cli.main([*cli_args(cli_inputs, cli_inputs[2] / "out"), "--expansion-fixtures", "x.json"])


APPROVED = frozenset({(KPMG, "NL"), (DEVO, "NL"), (DEVO, "IE")})
PILOT = frozenset({(KPMG, "NL")})


def run_cli(cli_inputs, out, transport, *extra, init=False, max_pages=None):
    import scripts.expansion_shadow as es
    parser = cli.argparse.ArgumentParser()
    ns = cli.argparse.Namespace(
        watchlist=str(cli_inputs[0]), criteria=str(cli_inputs[1]), out_dir=str(out), expansion_live=True,
        expansion_init=init, expansion_max_pages=max_pages or cli.MAX_REQUESTS_PER_RUN, expansion_fixtures=None, sheet_labels=None)
    return es.run_expansion(ns, parser, transport=transport, clock=lambda: 0.0, sleep=lambda s: None)


def scope(monkeypatch, pairs):
    import scripts.expansion_shadow as es
    import ejs.services.expansion_replay as er
    monkeypatch.setattr(er, "LIVE_APPROVED_PAIRS", pairs)
    monkeypatch.setattr(es, "LIVE_APPROVED_PAIRS", pairs)


@pytest.fixture
def approved(monkeypatch):
    scope(monkeypatch, APPROVED)


@pytest.fixture
def pilot(monkeypatch):
    scope(monkeypatch, PILOT)


def report_of(out):
    return json.loads((out / store.REPORT_NAMES[0]).read_bytes())


def bundle_of(out):
    return json.loads((out / store.BUNDLE_NAME).read_bytes())


DAY1 = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "b")),
        (DEVO, "nl", 0): feed("NL", total=1, ids=("c",), tenant=DEVO),
        (DEVO, "ie", 0): feed("IE", total=1, ids=("c",), tenant=DEVO)}


def two_pages(second_total=150):
    ids = [f"k{n:03d}" for n in range(150)]
    return {(KPMG, "nl", 0): feed("NL", total=150, ids=ids[:100]),
            (KPMG, "nl", 100): feed("NL", offset=100, total=second_total, ids=ids[100:])}


def test_pilot_scope_reads_two_pages_and_one_recheck_and_is_complete_for_its_scope(cli_inputs, pilot):
    out, feed_ = cli_inputs[2] / "pilot", Feed(two_pages())
    assert run_cli(cli_inputs, out, feed_, init=True) == 0           # approved scope done: not "incomplete"
    report = report_of(out)
    assert [urlparse(u).query for u in feed_.urls] == [
        "country=nl&offset=0&limit=100", "country=nl&offset=100&limit=100", "country=nl&offset=0&limit=100"]
    assert [r["status"] for r in report["request_log"]] == [200, 200, 200] and report["network_requests"] == 3
    assert report["request_accounting"]["remaining"] == 2
    assert [c["status"] for c in report["snapshot_consistency"]["first_page_rechecks"]] == ["stable"]
    assert report["snapshot_consistency"]["atomic_snapshot"] is False
    assert report["listings_kept"] == 150 and report["coverage"]["status"] == "completed"
    assert report["acceptance"] == {"measured_over": "approved_scope", "collection_complete": True,
                                    "verification_complete": True, "unverified_pairs": [], "ready": True}
    assert report["coverage"]["research_scope"]["not_approved_pairs"] == 242
    assert report["coverage"]["research_scope"]["research_coverage_complete"] is False
    assert project_for_application_bridge(report)["listings_kept"] == 150


def test_resume_without_requests_keeps_request_log_rechecks_and_accounting(cli_inputs, pilot):
    out = cli_inputs[2] / "pilot"
    run_cli(cli_inputs, out, Feed(two_pages()), init=True)
    first, bundle_before = report_of(out), bundle_of(out)
    silent = Feed({})
    assert run_cli(cli_inputs, out, silent) == 0
    assert silent.urls == []
    again = report_of(out)
    for key in ("request_log", "request_accounting", "network_requests", "snapshot_consistency", "vacancies",
                "coverage"):
        assert again[key] == first[key], key
    assert bundle_of(out) == bundle_before                          # nothing rewritten, nothing lost
    assert project_for_application_bridge(again)["listings_kept"] == 150   # projection survives a resume


def test_drift_stays_visible_after_resume_and_never_becomes_success(cli_inputs, pilot):
    calls = {"n": 0}

    class Moving(Feed):
        def __call__(self, url, headers, timeout):
            calls["n"] += 1
            if calls["n"] == 3:                      # the recheck sees a shifted first page
                return 200, feed("NL", total=151, ids=[f"k{n:03d}" for n in range(100)])
            return super().__call__(url, headers, timeout)

    out = cli_inputs[2] / "moving"
    assert run_cli(cli_inputs, out, Moving(two_pages()), init=True) == 1
    first = report_of(out)
    assert first["snapshot_consistency"]["first_page_rechecks"][0]["reason"] == "total_changed"
    silent = Feed({})
    assert run_cli(cli_inputs, out, silent) == 1                   # resume: still failing, no new request
    assert silent.urls == []
    again = report_of(out)
    assert again["snapshot_consistency"]["first_page_rechecks"] == first["snapshot_consistency"]["first_page_rechecks"]
    assert again["snapshot_consistency"]["recheck_history"][0]["status"] == "drift"
    projected = project_for_application_bridge(again)
    assert projected["listings_kept"] == 0 and projected["excluded_rows"]["count"] == 150


def test_later_stable_recheck_cannot_erase_earlier_drift(cli_inputs, pilot):
    out = cli_inputs[2] / "sticky"
    run_cli(cli_inputs, out, Feed({(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}), init=True)
    path = out / store.BUNDLE_NAME
    data = json.loads(path.read_bytes())
    data["evidence"]["rechecks"] = [{"seq": 1, "tenant": KPMG, "country": "NL", "status": "drift", "reason": "total_changed"},
                                    {"seq": 1, "tenant": KPMG, "country": "NL", "status": "stable", "reason": "later"}]
    path.write_bytes((json.dumps(data, separators=(",", ":")) + "\n").encode())
    assert run_cli(cli_inputs, out, Feed({})) == 1
    assert report_of(out)["snapshot_consistency"]["first_page_rechecks"][0]["status"] == "drift"


def test_bundle_budget_is_cumulative_across_invocations_and_fails_closed(cli_inputs, pilot):
    ids = [f"i{n:04d}" for n in range(450)]
    pages = {(KPMG, "nl", o): feed("NL", offset=o, total=450, ids=ids[o:o + 100]) for o in range(0, 450, 100)}
    out = cli_inputs[2] / "budget"
    first, second, third = Feed(pages), Feed(pages), Feed(pages)
    assert run_cli(cli_inputs, out, first, init=True) == 3        # 4 pages (reserve 1 for a recheck), pair pending
    assert len(first.urls) == 4 and report_of(out)["request_accounting"]["remaining"] == 1
    assert run_cli(cli_inputs, out, second) == 4                  # collection complete, recheck unaffordable: not accepted
    assert len(second.urls) == 1
    report = report_of(out)
    assert report["network_requests"] == 5 and report["request_accounting"]["remaining"] == 0
    assert [c["status"] for c in report["snapshot_consistency"]["first_page_rechecks"]] == ["not_checked"]
    assert run_cli(cli_inputs, out, third) == 4                   # still not accepted, and still no error
    assert third.urls == []                                       # budget spent: nothing more
    assert project_for_application_bridge(report_of(out))["excluded_rows"]["count"] == 450   # unverified, excluded


def test_unchecked_pair_is_rechecked_by_a_later_invocation_without_losing_history(cli_inputs, pilot):
    out = cli_inputs[2] / "later"
    pages = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "b"))}
    assert run_cli(cli_inputs, out, Feed(pages), init=True, max_pages=1) == 4
    first = report_of(out)
    assert [c["status"] for c in first["snapshot_consistency"]["first_page_rechecks"]] == ["not_checked"]
    assert project_for_application_bridge(first)["listings_kept"] == 0
    second = Feed(pages)
    assert run_cli(cli_inputs, out, second) == 0                    # the recheck makes it acceptance-ready
    assert [urlparse(u).query for u in second.urls] == ["country=nl&offset=0&limit=100"]   # only the recheck
    report = report_of(out)
    assert [c["status"] for c in report["snapshot_consistency"]["first_page_rechecks"]] == ["stable"]
    assert [c["status"] for c in report["snapshot_consistency"]["recheck_history"]] == ["not_checked", "stable"]
    assert report["network_requests"] == 2 and report["request_accounting"]["remaining"] == 3
    assert project_for_application_bridge(report)["listings_kept"] == 2
    third = Feed(pages)
    run_cli(cli_inputs, out, third)
    assert third.urls == []                                         # verified pairs are never re-requested


def test_process_killed_mid_invocation_is_charged_its_whole_reservation(cli_inputs, pilot):
    class Killed(Feed):
        def __call__(self, url, headers, timeout):
            if len(self.urls) == 1:
                raise KeyboardInterrupt
            return super().__call__(url, headers, timeout)

    out = cli_inputs[2] / "killed"
    with pytest.raises(KeyboardInterrupt):
        run_cli(cli_inputs, out, Killed(two_pages()), init=True)
    assert bundle_of(out)["evidence"]["in_flight"] == {"seq": 1, "reserved": 5}   # durable before the first GET
    assert bundle_of(out)["listings"] == []
    after = Feed(two_pages())
    assert run_cli(cli_inputs, out, after) == 3                    # pair still pending, budget gone
    assert after.urls == []                                        # uncertain attempts were charged in full
    report = report_of(out)
    assert report["request_accounting"]["uncertain_reserved"] == 5 and report["request_accounting"]["remaining"] == 0
    assert report["request_accounting"]["invocations"] == [
        {"seq": 1, "status": "interrupted_unverified", "reserved": 5, "requests": 0}]
    assert bundle_of(out)["evidence"]["in_flight"] is None


def test_a_live_bundle_without_evidence_or_with_tampered_evidence_is_refused(cli_inputs, pilot):
    out = cli_inputs[2] / "x"
    run_cli(cli_inputs, out, Feed(two_pages()), init=True)
    path = out / store.BUNDLE_NAME
    original = path.read_bytes()
    data = json.loads(original)
    for tamper in (lambda d: d.pop("evidence"),
                   lambda d: d["evidence"]["invocations"][0]["requests"].extend([request()] * 3)):
        broken = json.loads(original)
        tamper(broken)
        path.write_bytes((json.dumps(broken, separators=(",", ":")) + "\n").encode())
        silent = Feed({})
        with pytest.raises(SystemExit):
            run_cli(cli_inputs, out, silent)
        assert silent.urls == []                                    # refused before any request
    path.write_bytes(original)
    assert run_cli(cli_inputs, out, Feed({})) == 0


def test_requests_never_exceed_five_even_when_every_call_fails(cli_inputs, pilot):
    feed_ = Feed({(KPMG, "nl", 0): 429})
    assert run_cli(cli_inputs, cli_inputs[2] / "p", feed_, init=True) == 1
    assert len(feed_.urls) == 1              # no retry after a failure


def test_recheck_detects_total_and_identity_drift():
    def go(first, again):
        calls = iter([first, again])
        c = carrier(lambda url, h, t: (200, next(calls)))
        result = live_replay_batch(initial_checkpoint(), c, c.begin_employer, approved=PILOT, max_pages=1)
        return recheck_first_page(result.checkpoint.cursors[0], c, c.begin_employer)

    base = feed("NL", total=2, ids=("a", "b"))
    assert go(base, base)["status"] == "stable"
    assert go(base, feed("NL", total=3, ids=("a", "b", "c")))["reason"] == "total_changed"
    assert go(base, feed("NL", total=2, ids=("z", "a")))["reason"] == "first_page_identities_changed"
    assert go(base, b"garbage")["status"] == "unreadable"


def test_recheck_over_budget_is_not_a_failure():
    calls = iter([feed("NL", total=1, ids=("a",))])
    c = carrier(lambda url, h, t: (200, next(calls)), max_requests=1)
    result = live_replay_batch(initial_checkpoint(), c, c.begin_employer, approved=PILOT, max_pages=1)
    assert recheck_first_page(result.checkpoint.cursors[0], c, c.begin_employer)["status"] == "not_checked"


def test_drift_between_pages_stops_the_pair_and_the_run_exits_nonzero(cli_inputs, pilot):
    out = cli_inputs[2] / "drift"
    assert run_cli(cli_inputs, out, Feed(two_pages(second_total=151)), init=True) == 1
    report = report_of(out)
    assert report["coverage"]["counts"]["failed"] == 1 and report["listings_kept"] == 100
    assert project_for_application_bridge(report)["listings_kept"] == 0


def test_restart_after_interruption_equals_uninterrupted_run(cli_inputs, approved):
    full = cli_inputs[2] / "full"
    assert run_cli(cli_inputs, full, Feed(DAY1), init=True) == 4         # collected, but DEVO IE never rechecked
    interrupted = cli_inputs[2] / "interrupted"
    assert run_cli(cli_inputs, interrupted, Feed({**DAY1, (DEVO, "nl", 0): 500}), init=True) == 1
    report = report_of(interrupted)
    assert report["coverage"]["counts"]["failed"] == 1 and report["listings_kept"] == 2
    assert {e["tenant"]: e["status"] for e in report["employers"]}[KPMG] == "completed"
    retry = Feed(DAY1)
    assert run_cli(cli_inputs, interrupted, retry) == 1                  # failed tenant stays blocked
    assert retry.urls == []
    assert report_of(interrupted)["request_log"] == report["request_log"]
    final = report_of(full)
    assert final["listings_parsed"] == 4 and final["listings_kept"] == 3 and final["listings_duplicate"] == 1
    assert {v["posting_id"] for v in final["vacancies"]} == {"a", "b", "c"}
    # 3 pages + 3 first-page rechecks would be 6, so the 5-request ceiling leaves the last recheck unchecked
    state = {(c["tenant"], c["country"]): c["status"] for c in final["snapshot_consistency"]["first_page_rechecks"]}
    assert state == {(KPMG, "NL"): "stable", (DEVO, "NL"): "stable", (DEVO, "IE"): "not_checked"}
    assert final["network_requests"] == 5 and final["transport_mode"] == "live_country_feed"
    for path in full.iterdir():
        assert path.stat().st_mode & 0o077 == 0


def test_failed_second_page_commits_first_page_progress_only_once(cli_inputs, approved):
    out = cli_inputs[2] / "out"
    ids = [f"k{n:03d}" for n in range(150)]
    pages = {**DAY1, (KPMG, "nl", 0): feed("NL", total=150, ids=ids[:100]),
             (KPMG, "nl", 100): feed("NL", offset=100, total=150, ids=ids[100:])}
    first = Feed({**pages, (KPMG, "nl", 100): 500})
    run_cli(cli_inputs, out, first, init=True)                           # page 2 fails mid-pair
    assert len(bundle_of(out)["listings"]) == 101                        # 100 KPMG + 1 Devoteam committed
    assert first.urls.count(live_page_url(KPMG, "NL", 100)) == 1


def test_daily_runs_use_separate_snapshots_and_never_touch_earlier_days(cli_inputs, approved):
    day1, day2 = cli_inputs[2] / "2026-11-27", cli_inputs[2] / "2026-11-28"
    run_cli(cli_inputs, day1, Feed(DAY1), init=True)
    before = {p.name: p.read_bytes() for p in day1.iterdir() if p.name != store._LOCK_NAME}
    changed = {**DAY1, (KPMG, "nl", 0): feed("NL", total=3, ids=("a", "b", "z"))}
    run_cli(cli_inputs, day2, Feed(changed), init=True)               # a new day starts with its own budget
    assert {p.name: p.read_bytes() for p in day1.iterdir() if p.name != store._LOCK_NAME} == before
    ids = lambda d: {v["posting_id"] for v in report_of(d)["vacancies"]}
    assert ids(day2) - ids(day1) == {"z"}
    with pytest.raises(SystemExit):             # re-initializing an existing day is refused
        run_cli(cli_inputs, day1, Feed(DAY1), init=True)


# --- acceptance status: collection complete is not acceptance ready -----------------------------

def test_completed_pagination_without_a_stable_recheck_is_not_accepted(cli_inputs, pilot):
    out = cli_inputs[2] / "unverified"
    pages = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "b"))}
    assert run_cli(cli_inputs, out, Feed(pages), init=True, max_pages=1) == 4   # not 0, not "incomplete" (3)
    report = report_of(out)
    assert report["coverage"]["status"] == "completed"                            # collection finished ...
    assert report["acceptance"] == {                                              # ... acceptance did not
        "measured_over": "approved_scope", "collection_complete": True, "verification_complete": False,
        "unverified_pairs": [{"tenant": KPMG, "country": "NL", "recheck": "not_checked"}], "ready": False}
    assert project_for_application_bridge(report)["listings_kept"] == 0


@pytest.mark.parametrize("rechecks, state", [
    ([], "missing"),
    ([{**STABLE_KPMG_NL, "status": "not_checked"}], "not_checked"),
    ([{**STABLE_KPMG_NL, "status": "drift"}], "drift"),
    ([{**STABLE_KPMG_NL, "status": "unreadable"}], "unreadable"),
])
def test_acceptance_requires_a_stable_recheck_for_every_completed_nonempty_pair(tmp_path, rechecks, state):
    pages = {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}
    acceptance = live_report(tmp_path, pages, {(KPMG, "NL")}, rechecks=rechecks)["acceptance"]
    assert acceptance["ready"] is False and acceptance["collection_complete"] is True
    assert acceptance["unverified_pairs"] == [{"tenant": KPMG, "country": "NL", "recheck": state}]


def test_empty_completed_pairs_need_no_recheck_and_pending_pairs_are_not_ready(tmp_path):
    empty = live_report(tmp_path, {(KPMG, "nl", 0): feed("NL", total=0)}, {(KPMG, "NL")})["acceptance"]
    assert empty["ready"] is True and empty["unverified_pairs"] == []
    pending = live_report(tmp_path, {(KPMG, "nl", 0): feed("NL", total=150, ids=[f"k{n}" for n in range(100)])},
                          {(KPMG, "NL")}, max_pages=1, rechecks=[STABLE_KPMG_NL])["acceptance"]
    assert pending["collection_complete"] is False and pending["ready"] is False


def test_synthetic_reports_carry_no_acceptance_section():
    criteria = load_criteria(_write_tmp(CRITERIA_YAML))
    assert "acceptance" not in build_expansion_report(initial_checkpoint(), [], MARKETS, criteria)


# --- acceptance check script (offline, read-only) -----------------------------------------------

import scripts.function02_acceptance_check as acceptance_check


def accepted_bundle(cli_inputs, pilot_scope=None):
    """The exact bounded sequence: cap 2, resume with cap 3, then a zero-request resume."""
    out = cli_inputs[2] / "acceptance"
    first, second, third = Feed(two_pages()), Feed(two_pages()), Feed({})
    assert run_cli(cli_inputs, out, first, init=True, max_pages=2) == 3          # page 1 only, pair pending
    assert run_cli(cli_inputs, out, second, max_pages=3) == 0                    # page 2 + recheck: accepted
    assert run_cli(cli_inputs, out, third, max_pages=3) == 0                     # zero-request resume
    assert (len(first.urls), len(second.urls), len(third.urls)) == (1, 2, 0)
    return out


def test_bounded_acceptance_sequence_passes_the_offline_check(cli_inputs, pilot, capsys):
    out = accepted_bundle(cli_inputs)
    report = report_of(out)
    assert report["network_requests"] == 3 <= 5 and report["request_accounting"]["remaining"] == 2
    assert [i["requests"] for i in report["request_accounting"]["invocations"]] == [1, 2]
    capsys.readouterr()
    assert acceptance_check.main(["--bundle-dir", str(out), "--expect-invocations", "2"]) == 0
    printed = capsys.readouterr().out
    assert "FAIL" not in printed and '"requests": 3' in printed and '"listings": 150' in printed
    assert "k000" not in printed                                                  # counts only, no listing content


@pytest.mark.parametrize("tamper", [
    lambda r, b: r["request_log"][0].update(url=r["request_log"][0]["url"].replace("country=nl", "country=de")),
    lambda r, b: r["request_log"][0].update(status=429),
    lambda r, b: r["acceptance"].update(ready=False),
    lambda r, b: r["snapshot_consistency"].update(first_page_rechecks=[]),
    lambda r, b: r.update(execution_authorized=True),
    lambda r, b: r["coverage"]["pairs"][9].update(status="incomplete"),             # a second pair in scope
    lambda r, b: b["evidence"]["invocations"][0].update(status="interrupted_unverified", requests=[]),
])
def test_acceptance_check_fails_closed_on_inconsistent_evidence(cli_inputs, pilot, capsys, tamper):
    out = accepted_bundle(cli_inputs)
    report, bundle = report_of(out), bundle_of(out)
    tamper(report, bundle)
    (out / store.REPORT_NAMES[0]).write_bytes(json.dumps(report).encode())
    (out / store.BUNDLE_NAME).write_bytes(json.dumps(bundle).encode())
    capsys.readouterr()
    assert acceptance_check.main(["--bundle-dir", str(out), "--expect-invocations", "2"]) in (1, 2)


def test_acceptance_check_rejects_a_wrong_invocation_count_and_missing_input(cli_inputs, pilot, tmp_path, capsys):
    out = accepted_bundle(cli_inputs)
    assert acceptance_check.main(["--bundle-dir", str(out), "--expect-invocations", "1"]) == 1
    assert acceptance_check.main(["--bundle-dir", str(tmp_path / "missing")]) == 2
