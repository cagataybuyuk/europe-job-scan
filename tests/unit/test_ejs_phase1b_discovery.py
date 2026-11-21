"""EJS Faz 1b tests: synthetic fixtures only; no network, no personal data."""
import ast
import json
import socket
from pathlib import Path

import pytest

from ejs.services import discovery, linkedin_url_resolve, shadow_compare
from ejs.services.dedupe import dedupe_listings
from ejs.services.discovery import (BadPayloadError, FetchFailedError, NoCarrierError, RateLimiter, UnsupportedAtsError,
                                    WatchlistError, WatchlistRow, classify_url, discover_all, discover_company,
                                    list_api_url, parse_greenhouse, parse_lever, parse_smartrecruiters, parse_watchlist)
from ejs.services.linkedin_url_resolve import resolve_from_html, resolve_linkedin_url

GH = {"jobs": [{"id": 1, "title": "Widget Engineer (m/f/d)", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Berlin, Atlantis"}},
               {"id": 2, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "location": {"name": "Remote"}},
               {"id": 3, "title": "", "absolute_url": "https://x/3"}]}
LV = [{"id": "a", "text": "Platform Engineer", "hostedUrl": "https://jobs.lever.co/borealis/a", "categories": {"location": "Oslo"}},
      {"id": "b", "text": "SRE", "hostedUrl": "https://jobs.lever.co/borealis/b", "categories": {}}]
SR = {"content": [{"id": "743", "name": "Backend Developer", "location": {"city": "Utrecht", "country": "nl"}},
                  {"id": "744", "name": "QA Engineer", "location": {"city": "Utrecht", "country": "nl"}}]}

URL_TABLE = [
    ("https://boards.greenhouse.io/acme", "greenhouse", "acme"),
    ("https://job-boards.greenhouse.io/acme/jobs/9", "greenhouse", "acme"),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme"),
    ("https://jobs.lever.co/borealis", "lever", "borealis"),
    ("https://jobs.smartrecruiters.com/CobaltAG/123-x", "smartrecruiters", "CobaltAG"),
    ("https://delta.wd3.myworkdayjobs.com/en-US/ext", "workday", "delta"),
    ("https://apply.workable.com/evergreen/", "workable", "evergreen"),
    ("https://careers.example.org/jobs", "custom", None),
    ("https://evil-greenhouse.io/acme", "custom", None),
    ("https://boards.greenhouse.io.evil.com/acme", "custom", None),
    ("https://www.linkedin.com/jobs/search/?keywords=x", "blocked_aggregator", None),
    ("https://de.indeed.com/jobs?q=x", "blocked_aggregator", None),
    ("https://www.glassdoor.com/Job/x", "blocked_aggregator", None),
    ("http://boards.greenhouse.io/acme", "invalid", None),
    ("", "invalid", None),
    ("not a url", "invalid", None),
]


@pytest.mark.parametrize("url,kind,slug", URL_TABLE)
def test_classify_table(url, kind, slug):
    c = classify_url(url)
    assert (c.kind, c.slug) == (kind, slug)


def test_api_urls_and_unsupported():
    assert list_api_url(classify_url("https://jobs.lever.co/borealis")) == "https://api.lever.co/v0/postings/borealis?mode=json"
    assert list_api_url(classify_url("https://boards.greenhouse.io/acme")).endswith("/boards/acme/jobs")
    for u in ("https://careers.example.org/", "https://delta.wd3.myworkdayjobs.com/x", "https://www.linkedin.com/jobs/search/", "https://boards.greenhouse.io/"):
        with pytest.raises(UnsupportedAtsError):
            list_api_url(classify_url(u))


def test_parse_each_ats():
    g = parse_greenhouse(GH, "Acme Widgets GmbH")
    assert [r["role"] for r in g] == ["Widget Engineer (m/f/d)", "Data Engineer"] and g[0]["location"] == "Berlin, Atlantis"
    assert set(g[0]) == {"company", "role", "location", "url", "ats_kind", "source"} and g[0]["ats_kind"] == "greenhouse"
    lv = parse_lever(json.dumps(LV).encode(), "Borealis Labs")
    assert [(r["role"], r["location"]) for r in lv] == [("Platform Engineer", "Oslo"), ("SRE", "")]
    sr = parse_smartrecruiters(SR, "Delta Works", "DeltaWorks")
    assert sr[0]["url"] == "https://jobs.smartrecruiters.com/DeltaWorks/743" and sr[0]["location"] == "Utrecht, NL"
    assert parse_greenhouse({"jobs": []}, "X") == []


@pytest.mark.parametrize("fn,args", [(parse_greenhouse, ("X",)), (parse_lever, ("X",)), (parse_smartrecruiters, ("X", "s"))])
@pytest.mark.parametrize("bad", [b"", b"   ", b"{not json", b"\xff\xfe", "str", 5, None, {}, {"jobs": "x"}, {"content": 3}, [1, 2], {"jobs": [1, {"title": "t"}]}])
def test_bad_payloads_raise(fn, args, bad):
    with pytest.raises(BadPayloadError):
        fn(bad, *args)


def test_no_carrier_is_error_and_fetch_errors_classified():
    row = WatchlistRow("Acme", "https://boards.greenhouse.io/acme")
    with pytest.raises(NoCarrierError):
        discover_company(row, None)
    def boom(url):
        raise OSError("down")
    with pytest.raises(FetchFailedError):
        discover_company(row, boom)
    rep = discover_all([row, WatchlistRow("Custom Co", "https://careers.example.org/")], None)
    assert rep.listings == () and rep.errors == (("Acme", "no_carrier"), ("Custom Co", "unsupported_ats"))


def test_discover_all_end_to_end_with_dedupe_and_determinism():
    rows = parse_watchlist([{"company": "Acme Widgets GmbH", "careers_url": "https://boards.greenhouse.io/acme", "note": "ignored"},
                            {"company": "Borealis Labs", "careers_url": "https://jobs.lever.co/borealis"},
                            {"company": "Acme Widgets", "careers_url": "https://boards.greenhouse.io/acme"},   # same board twice
                            {"company": "Delta Works", "careers_url": "https://jobs.smartrecruiters.com/DeltaWorks"},
                            {"company": "Broken", "careers_url": "https://jobs.lever.co/broken"}])
    api = {"https://boards-api.greenhouse.io/v1/boards/acme/jobs": json.dumps(GH).encode(),
           "https://api.lever.co/v0/postings/borealis?mode=json": LV,
           "https://api.smartrecruiters.com/v1/companies/DeltaWorks/postings": SR,
           "https://api.lever.co/v0/postings/broken?mode=json": b"<html>"}
    calls = []
    def fetch(u):
        calls.append(u)
        return api[u]
    r1 = discover_all(rows, fetch)
    assert r1.errors == (("Broken", "bad_payload"),) and len(r1.listings) == 2 + 2 + 2 + 2
    assert discover_all(rows, fetch) == r1
    res = dedupe_listings(r1.listings)
    assert (len(res.kept), len(res.duplicates), len(res.unkeyed)) == (5, 2, 1)   # Acme board listed twice -> 2 dups; Lever "SRE" has no location -> unkeyed
    assert all(u.startswith("https://") for u in calls)


def test_watchlist_validation():
    for bad in ([{"company": "", "careers_url": "https://a.b"}], [{"company": "A"}], [{"company": "A", "careers_url": "http://a.b"}], [None], [{"company": "A", "careers_url": "https://[bad"}], [{"company": "A", "careers_url": "https:///careers"}]):
        with pytest.raises(WatchlistError):
            parse_watchlist(bad)
    assert parse_watchlist([{"company": " A ", "careers_url": " https://a.b/c ", "email": "x"}]) == (WatchlistRow("A", "https://a.b/c"),)


def test_rate_limiter_injected_clock():
    t = [0.0]
    sleeps = []
    def sleep(s):
        sleeps.append(s)
        t[0] += s
    lim = RateLimiter(2.0, lambda: t[0], sleep)
    lim.wait()                  # first call: no wait
    t[0] += 0.5
    lim.wait()                  # waits 1.5
    lim.wait()                  # waits 2.0
    assert sleeps == [1.5, 2.0]
    with pytest.raises(ValueError):
        RateLimiter(-1, lambda: 0.0, sleep)
    rows = [WatchlistRow("A", "https://jobs.lever.co/a"), WatchlistRow("B", "https://jobs.lever.co/b")]
    sleeps.clear()
    discover_all(rows, lambda u: [], RateLimiter(3.0, lambda: t[0], sleep))
    assert sleeps == [3.0]


# ---- LinkedIn single URL resolution -------------------------------------------------------------
LI = "https://www.linkedin.com/jobs/view/123456789/"
PAGE_OK = '<a class="x" href="https://www.linkedin.com/jobs/view/externalApply/123?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1&amp;a=b">Apply</a><a href="/in/x">p</a>'


def test_linkedin_resolve_ok_and_needs_user():
    r = resolve_linkedin_url(LI, lambda u: PAGE_OK.encode())
    assert (r.status, r.target_url) == ("resolved", "https://boards.greenhouse.io/acme/jobs/1")
    assert classify_url(r.target_url).kind == "greenhouse"
    assert resolve_linkedin_url(LI, lambda u: "<html>nothing</html>").status == "needs_user"
    assert resolve_linkedin_url(LI, lambda u: "").reason == "empty_page"
    assert resolve_linkedin_url(LI, lambda u: b"\xff").reason == "undecodable_page"
    assert resolve_linkedin_url(LI, None).reason == "no_carrier"
    def boom(u):
        raise OSError
    assert resolve_linkedin_url(LI, boom).reason == "fetch_failed:OSError"
    two = '<a href="https://a.example/1">Apply now</a><a href="https://b.example/2">Apply on company site</a>'
    assert resolve_from_html(two).reason == "ambiguous_apply_targets"
    assert resolve_from_html('<a href="http://a.example/1">Apply</a>').status == "needs_user"
    assert resolve_from_html('<a href="https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Fwww.linkedin.com%2Fx">x</a>').status == "needs_user"


def test_linkedin_slug_url_accepted():
    assert linkedin_url_resolve.is_single_job_url("https://www.linkedin.com/jobs/view/widget-engineer-at-acme-3812345678/")


def test_linkedin_non_apply_links_ignored_and_bad_href_controlled():
    priv = '<a href="https://example.com/privacy">Privacy Policy</a><a href="https://twitter.com/x">Follow</a>'
    assert resolve_from_html(priv).status == "needs_user" and resolve_from_html(priv).reason == "no_external_apply"
    both = priv + '<a class="b" href="https://boards.greenhouse.io/acme/jobs/1"><span>Apply</span></a>'
    r = resolve_from_html(both)
    assert (r.status, r.target_url) == ("resolved", "https://boards.greenhouse.io/acme/jobs/1")
    for bad in ('<a href="https://[bad">Apply</a>',
                '<a href="https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2F%5Bbad">x</a>'):
        assert resolve_from_html(bad).status == "needs_user"
        assert resolve_linkedin_url(LI, lambda u, b=bad: b).status == "needs_user"
    sneaky = '<a href="https://example.com/privacy?x=/jobs/view/externalApply/">Privacy Policy</a>'
    assert resolve_from_html(sneaky).status == "needs_user"
    assert resolve_from_html('<a href="https://example.com/jobs/view/externalApply/1">Privacy</a>').status == "needs_user"
    assert linkedin_url_resolve.extract_target("https://[bad") is None
    assert resolve_linkedin_url("https://[bad", None).status == "rejected"


@pytest.mark.parametrize("url", ["https://www.linkedin.com/jobs/search/?keywords=x", "https://www.linkedin.com/jobs/collections/recommended/",
                                 "https://www.linkedin.com/jobs/", "https://www.linkedin.com/jobs/view/", "https://www.linkedin.com/jobs/view/search/", "http://www.linkedin.com/jobs/view/1/",
                                 "https://evil-linkedin.com/jobs/view/1/", "https://www.linkedin.com/jobs/view/externalApply/1", "https://example.com/jobs/view/1/", ""])
def test_linkedin_list_and_other_urls_rejected_without_fetch(url):
    def fetch(u):
        raise AssertionError("must not fetch")
    r = resolve_linkedin_url(url, fetch)
    assert r.status == "rejected" and r.target_url is None


# ---- shadow compare (synthetic only) ------------------------------------------------------------
def test_shadow_compare_synthetic():
    d = shadow_compare.compare_decisions({"a": "auto_apply", "b": "auto_skip", "c": "needs_user", "e": "weird"},
                                         {"a": "Apply", "b": "apply", "c": "Needs User", "d": "skip", "e": "weird"})
    assert d.agree == ("a", "c") and d.disagree == (("b", "auto_skip", "auto_apply"), ("e", "unknown", "unknown"))
    assert d.only_sheet == ("d",) and d.only_ejs == () and d.agreement_rate == 0.5
    assert shadow_compare.compare_decisions({}, {}).agreement_rate is None


# ---- offline guarantees -------------------------------------------------------------------------
def test_modules_have_no_network_imports_and_socket_blocked(monkeypatch):
    banned = {"socket", "http", "requests", "httpx", "aiohttp", "ssl", "smtplib", "playwright", "urllib3"}
    for mod in (discovery, linkedin_url_resolve, shadow_compare):
        for node in ast.walk(ast.parse(Path(mod.__file__).read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                assert n.split(".")[0] not in banned and n not in ("urllib.request", "urllib.error"), (mod.__name__, n)
    def boom(*a, **k):
        raise AssertionError("network call")
    for name in ("connect", "connect_ex", "send", "sendto"):
        monkeypatch.setattr(socket.socket, name, boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert discover_all([WatchlistRow("A", "https://jobs.lever.co/a")], None).errors == (("A", "no_carrier"),)
    assert resolve_linkedin_url(LI, None).status == "needs_user"
