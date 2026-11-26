"""FUNCTION-02 live-feed adapter tests. Every transport is a fake; the network is denied."""
import json
import socket
from urllib.parse import parse_qs, urlparse

import pytest

from ejs.services.application_bridge import prepare_application_report
from ejs.services.criteria_loader import load_criteria
from ejs.services.expansion_replay import (
    COUNTRIES, RESEARCH, initial_checkpoint, live_page, live_page_url, live_replay_batch, replay_batch,
)
from ejs.services.expansion_shadow import build_expansion_report
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


def test_report_carries_live_evidence_and_request_counts(tmp_path):
    criteria = load_criteria(_write(tmp_path / "c.yaml", CRITERIA_YAML))
    pages = {(KPMG, "nl", 0): feed("NL", total=1, ids=("a",))}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL")})
    markets = {tenant: "Synthetic market" for _, tenant, _, _ in RESEARCH}
    report = build_expansion_report(result.checkpoint, list(result.listings), markets, criteria,
                                    live_requests=dict(c.per_employer))
    assert report["transport_mode"] == "live_country_feed" and report["network_requests"] == 1
    assert report["requests_scope"] == "this_invocation" and report["execution_authorized"] is False
    vacancy = report["vacancies"][0]
    assert vacancy["discovery_evidence"] == {"observation": "live_country_feed",
                                             "feed_url": live_page_url(KPMG, "NL", 0), "countries": ["NL"]}
    assert report["coverage"]["counts"]["completed"] == 1 and report["coverage"]["counts"]["incomplete"] == 242


def test_report_is_downstream_compatible_only_through_an_explicit_projection(tmp_path):
    criteria = load_criteria(_write(tmp_path / "c.yaml", CRITERIA_YAML))
    pages = {(KPMG, "nl", 0): feed("NL", total=1, ids=("744000000000001",))}
    result, c = run(initial_checkpoint(), Feed(pages), {(KPMG, "NL")})
    markets = {tenant: "Synthetic market" for _, tenant, _, _ in RESEARCH}
    report = build_expansion_report(result.checkpoint, list(result.listings), markets, criteria,
                                    live_requests=dict(c.per_employer))
    with pytest.raises(ValueError, match="discovery_schema"):
        prepare_application_report(report)           # direct hand-off stays refused
    projected = {**report, "schema": "EJS-SHADOW-REPORT-2"}
    prepared = prepare_application_report(projected)
    candidate = prepared["candidates"][0]
    assert (candidate["tenant"], candidate["posting_id"], candidate["location"]) == (KPMG, "744000000000001",
                                                                                      "Synthetic city, NL")
    assert candidate["readiness"] == "missing_evidence" and prepared["executable"] is False


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


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


def test_cli_refuses_without_an_approved_scope_before_any_request(cli_inputs, monkeypatch):
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


def run_cli(cli_inputs, out, transport, *extra, init=False):
    import scripts.expansion_shadow as es
    parser = cli.argparse.ArgumentParser()
    ns = cli.argparse.Namespace(
        watchlist=str(cli_inputs[0]), criteria=str(cli_inputs[1]), out_dir=str(out), expansion_live=True,
        expansion_init=init, expansion_max_pages=cli.MAX_REQUESTS_PER_RUN, expansion_fixtures=None, sheet_labels=None)
    return es.run_expansion(ns, parser, transport=transport, clock=lambda: 0.0, sleep=lambda s: None)


@pytest.fixture
def approved(monkeypatch):
    import scripts.expansion_shadow as es
    import ejs.services.expansion_replay as er
    monkeypatch.setattr(er, "LIVE_APPROVED_PAIRS", APPROVED)
    monkeypatch.setattr(es, "LIVE_APPROVED_PAIRS", APPROVED)


DAY1 = {(KPMG, "nl", 0): feed("NL", total=2, ids=("a", "b")),
        (DEVO, "nl", 0): feed("NL", total=1, ids=("c",), tenant=DEVO),
        (DEVO, "ie", 0): feed("IE", total=1, ids=("c",), tenant=DEVO)}


def test_restart_after_interruption_equals_uninterrupted_run(cli_inputs, approved):
    full = cli_inputs[2] / "full"
    assert run_cli(cli_inputs, full, Feed(DAY1), init=True) == 3        # unapproved pairs stay incomplete
    interrupted = cli_inputs[2] / "interrupted"
    boom = Feed({**DAY1, (DEVO, "nl", 0): 500})
    assert run_cli(cli_inputs, interrupted, boom, init=True) == 1       # Devoteam failed, KPMG kept
    report = json.loads((interrupted / store.REPORT_NAMES[0]).read_bytes())
    assert report["coverage"]["counts"]["failed"] == 1
    assert {e["tenant"]: e["status"] for e in report["employers"]}[KPMG] == "incomplete"
    assert report["listings_kept"] == 2                                  # KPMG progress survived the failure
    # a rerun neither re-requests completed pairs nor retries the failed tenant
    retry = Feed(DAY1)
    assert run_cli(cli_inputs, interrupted, retry) == 1
    assert retry.urls == []
    final = json.loads((full / store.REPORT_NAMES[0]).read_bytes())
    assert final["listings_parsed"] == 4 and final["listings_kept"] == 3 and final["listings_duplicate"] == 1
    assert {v["posting_id"] for v in final["vacancies"]} == {"a", "b", "c"}
    assert final["network_requests"] == 3 and final["transport_mode"] == "live_country_feed"
    # every owner-only file, no candidate data
    for path in full.iterdir():
        assert path.stat().st_mode & 0o077 == 0


def test_failed_second_page_commits_first_page_progress_only_once(cli_inputs, approved):
    out = cli_inputs[2] / "out"
    pages = {**DAY1, (KPMG, "nl", 0): feed("NL", total=150, ids=[f"k{n:03d}" for n in range(100)]),
             (KPMG, "nl", 100): feed("NL", offset=100, total=150, ids=[f"k{n:03d}" for n in range(100, 150)])}
    cli_args_first = Feed({**pages, (KPMG, "nl", 100): 500})
    run_cli(cli_inputs, out, cli_args_first, init=True)                  # page 2 fails mid-pair
    bundle = json.loads((out / store.BUNDLE_NAME).read_bytes())
    assert len(bundle["listings"]) == 101                                # 100 KPMG + 1 Devoteam committed
    assert cli_args_first.urls.count(live_page_url(KPMG, "NL", 100)) == 1


def test_daily_runs_use_separate_snapshots_and_never_touch_earlier_days(cli_inputs, approved):
    day1, day2 = cli_inputs[2] / "2026-11-27", cli_inputs[2] / "2026-11-28"
    run_cli(cli_inputs, day1, Feed(DAY1), init=True)
    before = {p.name: p.read_bytes() for p in day1.iterdir() if p.name != store._LOCK_NAME}
    changed = {**DAY1, (KPMG, "nl", 0): feed("NL", total=3, ids=("a", "b", "z"))}
    run_cli(cli_inputs, day2, Feed(changed), init=True)
    assert {p.name: p.read_bytes() for p in day1.iterdir() if p.name != store._LOCK_NAME} == before
    ids = lambda d: {v["posting_id"] for v in json.loads((d / store.REPORT_NAMES[0]).read_bytes())["vacancies"]}
    assert ids(day2) - ids(day1) == {"z"}
    with pytest.raises(SystemExit):             # re-initializing an existing day is refused
        run_cli(cli_inputs, day1, Feed(DAY1), init=True)


def test_process_killed_mid_batch_commits_nothing_and_resume_matches_clean_run(cli_inputs, approved):
    class Killed(Feed):
        def __call__(self, url, headers, timeout):
            if len(self.urls) == 1:
                raise KeyboardInterrupt
            return super().__call__(url, headers, timeout)

    killed_dir, clean_dir = cli_inputs[2] / "killed", cli_inputs[2] / "clean"
    with pytest.raises(KeyboardInterrupt):
        run_cli(cli_inputs, killed_dir, Killed(DAY1), init=True)
    assert not (killed_dir / store.BUNDLE_NAME).exists()                  # nothing half-committed
    run_cli(cli_inputs, killed_dir, Feed(DAY1), init=True)                # fresh start is allowed again
    run_cli(cli_inputs, clean_dir, Feed(DAY1), init=True)
    read = lambda d: (d / store.BUNDLE_NAME).read_bytes()
    assert read(killed_dir) == read(clean_dir)
