"""EJS-W1 Faz A tests: HTTP carrier + shadow run. Synthetic fixtures only; a real socket is a test failure."""
import ast
import importlib.util
import json
import socket
from collections import Counter
from pathlib import Path

import pytest

from ejs.services import http_carrier as hc
from ejs.services import shadow_run
from ejs.services.discovery import NoCarrierError
from ejs.services.http_carrier import (EmployerStoppedError, HostNotAllowedError, HttpCarrier, HttpStatusError,
                                       PageLimitError, RequestBudgetError)
from tests.unit.phase1a_support import criteria

ROOT = Path(__file__).resolve().parents[2]
GH_URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network access in test")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)


class Clock:
    def __init__(self):
        self.t, self.sleeps = 100.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make(transport, **kw):
    c = Clock()
    car = HttpCarrier(enabled=True, transport=transport, clock=c.now, sleep=c.sleep, **kw)
    car.begin_employer("Acme")
    return car, c


def ok(body=b'{"jobs": []}'):
    return lambda url, h, t: (200, body)


# ------------------------------------------------------------------ carrier
def test_default_off_raises_no_carrier_and_never_calls_transport():
    called = []
    car = HttpCarrier(transport=lambda *a: called.append(a) or (200, b""))
    with pytest.raises(NoCarrierError):
        car(GH_URL)
    assert called == [] and car.total == 0
    enabled_without_transport = HttpCarrier(enabled=True)
    with pytest.raises(NoCarrierError):
        enabled_without_transport(GH_URL)


def test_headers_have_no_identity_or_cookie():
    seen = []
    car, _ = make(lambda url, h, t: seen.append(h) or (200, b'{"jobs": []}'))
    car(GH_URL)
    assert seen[0] == hc.REQUEST_HEADERS
    assert {k.lower() for k in seen[0]} == {"user-agent", "accept"}
    assert "cookie" not in {k.lower() for k in seen[0]} and "authorization" not in {k.lower() for k in seen[0]}


@pytest.mark.parametrize("url", ["https://evil.example.com/x", "http://boards-api.greenhouse.io/v1/boards/a/jobs",
                                 "https://boards-api.greenhouse.io.evil.com/v1/a", "https://www.linkedin.com/jobs/1",
                                 "https://acme.myworkdayjobs.com/x", "https://api.lever.co:444/x",
                                 "https://user:secret@api.lever.co/x", "https://api.lever.co/x#secret"])
def test_disallowed_host_blocked_logged_not_requested(url):
    called = []
    car, _ = make(lambda *a: called.append(a) or (200, b"{}"))
    with pytest.raises(HostNotAllowedError):
        car(url)
    assert called == [] and car.total == 0
    assert car.log[0]["status"] == "blocked"


def test_global_rate_limit_one_per_second():
    car, c = make(ok())
    for _ in range(3):
        car(GH_URL)
    assert c.sleeps == [1.0, 1.0]
    times = [e["t"] for e in car.log]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:]))



def test_request_log_records_start_even_when_responses_take_different_times():
    clock = Clock()
    durations = iter([0.9, 0.1])

    def fetch(*args):
        clock.t += next(durations)
        return 200, b"[]"

    carrier = HttpCarrier(enabled=True, transport=fetch, clock=clock.now, sleep=clock.sleep)
    carrier.begin_employer("Acme")
    carrier(GH_URL)
    carrier(GH_URL)
    assert [entry["t"] for entry in carrier.log] == [100.0, 101.0]

def test_per_employer_page_limit_five():
    car, _ = make(ok())
    for _ in range(5):
        car(GH_URL)
    with pytest.raises(PageLimitError):
        car(GH_URL)
    assert car.total == 5
    car.begin_employer("Other")
    car(GH_URL)                         # another employer has its own budget
    assert car.total == 6


def test_run_request_cap():
    car, _ = make(ok(), max_requests=3)
    for i in range(3):
        car.begin_employer(f"E{i}")
        car(GH_URL)
    car.begin_employer("E9")
    with pytest.raises(RequestBudgetError):
        car(GH_URL)
    assert hc.MAX_REQUESTS_PER_RUN == 60 and hc.MAX_PAGES_PER_EMPLOYER == 5 and hc.MIN_INTERVAL_S == 1.0


@pytest.mark.parametrize("status", [429, 403, 500, 503, 404])
def test_error_status_stops_employer_without_retry(status):
    calls = []
    car, _ = make(lambda u, h, t: calls.append(u) or (status, b"denied"))
    with pytest.raises(HttpStatusError) as e:
        car(GH_URL)
    assert e.value.kind == f"http_{status}"
    with pytest.raises(EmployerStoppedError):
        car(GH_URL)
    assert len(calls) == 1              # no retry, no second attempt
    car.begin_employer("Next")
    with pytest.raises(HttpStatusError):
        car(GH_URL)
    assert len(calls) == 2


def test_transport_exception_stops_employer():
    def bad(*a):
        raise TimeoutError("x")
    car, _ = make(bad)
    with pytest.raises(Exception) as e:
        car(GH_URL)
    assert e.value.kind == "TimeoutError" or "TimeoutError" in str(e.value)
    with pytest.raises(EmployerStoppedError):
        car(GH_URL)


def test_log_has_no_body_and_carrier_has_no_recording(tmp_path):
    car, _ = make(ok(b'{"jobs": [], "secret_marker": 1}'))
    car(GH_URL)
    assert set(car.log[0]) == {"employer", "url", "t", "status", "bytes"}
    assert "secret_marker" not in json.dumps(car.log)
    with pytest.raises(TypeError, match="record_dir"):
        make(ok(), record_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kw", [
    {"min_interval_s": 0}, {"min_interval_s": 0.5}, {"max_requests": 100}, {"max_pages": 100},
    {"max_requests": 61}, {"max_pages": 6}, {"max_requests": 0},
    {"max_requests": 3.5}, {"max_pages": True}, {"min_interval_s": float("nan")},
    {"allowed_hosts": frozenset({"example.com"})},
])
def test_mandatory_limits_cannot_be_relaxed(kw):
    with pytest.raises(ValueError):
        make(ok(), **kw)


def test_limits_can_be_tightened():
    car, _ = make(ok(), min_interval_s=2.0, max_requests=3, max_pages=2)
    car(GH_URL)
    car(GH_URL)
    with pytest.raises(hc.PageLimitError):
        car(GH_URL)


def test_replay_carrier_is_offline_only_and_still_capped():
    c = Clock()
    with pytest.raises(ValueError):
        hc.ReplayCarrier(transport=hc.urllib_transport, clock=c.now)
    with pytest.raises(ValueError):
        hc.ReplayCarrier(transport=ok(), clock=c.now, max_requests=100)
    car = hc.ReplayCarrier(transport=ok(), clock=c.now)
    car.begin_employer("Acme")
    for _ in range(5):
        car(GH_URL)
    with pytest.raises(hc.PageLimitError):
        car(GH_URL)
    assert c.sleeps == []


def test_oversize_body_rejected():
    car, _ = make(lambda *a: (200, b"x" * (hc.MAX_BODY_BYTES + 1)))
    with pytest.raises(hc.BodyTooLargeError):
        car(GH_URL)


def test_real_transport_not_used_offline():
    with pytest.raises(AssertionError, match="real network"):
        hc.urllib_transport(GH_URL, hc.REQUEST_HEADERS, 1.0)


# ------------------------------------------------------------------ shadow run
PILOT = [("HSO International", "Netherlands", "https://job-boards.eu.greenhouse.io/hsogroupbv/jobs/1"),
         ("CarTrawler", "Ireland", "https://jobs.lever.co/cartrawler/aaa"),
         ("Veepee", "Spain", "https://jobs.lever.co/veepee/bbb"),
         ("Rituals", "Netherlands", "https://jobs.smartrecruiters.com/Rituals1/744-x")]
PENDING_SPEC = ([("https://acme{}.wd3.myworkdayjobs.com/en/x", 3), ("https://x{}.recruitee.com/o/a", 1),
                 ("https://apply.workable.com/w{}/j/1", 2), ("https://x{}.hirehive.com/job/1", 1),
                 ("https://jobs{}.example.org/job/Town-Role/12345/", 5), ("https://careers{}.example.com/p", 14),
                 ("https://nl.linkedin.com/jobs/view/{}", 3), ("https://nl.indeed.com/viewjob?jk={}", 2),
                 ("https://www.irishjobs.ie/{}", 2)])
BODIES = {
    "hsogroupbv": {"meta": {"total": 1}, "jobs": [{"id": 1, "title": "Analyst (m/f/d)", "absolute_url": "https://job-boards.eu.greenhouse.io/hsogroupbv/jobs/1",
                             "location": {"name": "Utrecht"}}]},
    "cartrawler": [{"id": "1", "text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/cartrawler/1", "categories": {"location": "Dublin"}},
                   {"id": "2", "text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/cartrawler/2", "categories": {"location": "Dublin"}}],
    "veepee": [{"text": "PM", "hostedUrl": "https://jobs.lever.co/veepee/1", "categories": {}}],
    "Rituals1": {"totalFound": 1, "limit": 100, "offset": 0, "content": [{"id": "9", "name": "Buyer", "location": {"city": "Amsterdam", "country": "nl"}}]},
}


def write_fixture(root, host, tenant, offset, body):
    fixture = root / host / tenant / f"{offset}.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(json.dumps(body), encoding="utf-8")


def write_pilot_fixtures(root):
    write_fixture(root, "boards.eu.greenhouse.io", "hsogroupbv", 0, BODIES["hsogroupbv"])
    write_fixture(root, "api.lever.co", "cartrawler", 0, BODIES["cartrawler"])
    write_fixture(root, "api.lever.co", "veepee", 0, BODIES["veepee"])
    write_fixture(root, "api.smartrecruiters.com", "Rituals1", 0, BODIES["Rituals1"])


def write_watchlist(path):
    lines = ["Employer Watchlist v1,,", "Operating Rule,\"x\",", "Watchlist Employers,37,", "",
             "Employer,Primary Market,Reference Vacancy / Careers URL"]
    lines += [f"{n},{m},{u}" for n, m, u in PILOT]
    i = 0
    for tmpl, count in PENDING_SPEC:
        for _ in range(count):
            i += 1
            lines.append(f"Pending{i:02d},Germany,{tmpl.format(i)}")
    lines += ["", "Priority Employer Scan Queue,,", "Employer,Watchlist Tier,Weighted Priority", "Accenture,B,71"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def transport_for(bodies=BODIES, status=200):
    from urllib.parse import urlparse
    import re
    def t(url, h, to):
        slug = re.search(r"/(?:boards|postings|companies)/([^/?]+)", urlparse(url).path).group(1)
        if slug not in bodies:
            return 404, b""
        return status, json.dumps(bodies[slug]).encode()
    return t


def run(tmp_path, transport=None, sheet=None, enabled=True):
    wl = tmp_path / "wl.csv"
    write_watchlist(wl)
    rows = shadow_run.read_watchlist(wl)
    c = Clock()
    car = HttpCarrier(enabled=enabled, transport=transport or transport_for(), clock=c.now, sleep=c.sleep)
    rep = shadow_run.run_shadow(rows, criteria(), fetch=car, begin_employer=car.begin_employer,
                                request_counts=lambda: car.per_employer, sheet_labels=sheet)
    return rep, car


def test_watchlist_reads_only_the_table(tmp_path):
    wl = tmp_path / "wl.csv"
    write_watchlist(wl)
    rows = shadow_run.read_watchlist(wl)
    assert len(rows) == 37 and set(rows[0]) == {"employer", "market", "url"}
    assert all(r["employer"] != "Accenture" for r in rows)


def test_pilot_and_33_pending_table(tmp_path):
    rep, car = run(tmp_path)
    assert (rep["employers_total"], rep["employers_supported"], rep["employers_pending_adapter"]) == (37, 4, 33)
    assert len(rep["pending_employers"]) == 33
    cls = Counter(p["ats_class"] for p in rep["pending_employers"])
    assert cls == {"workday": 3, "recruitee": 1, "workable": 2, "hirehive": 1, "successfactors_like": 5,
                   "custom": 14, "linkedin_indeed": 5, "national_board": 2}
    assert all(p["blocker"] for p in rep["pending_employers"])
    assert car.total == 4 and all(e["requests"] == 1 for e in rep["employers"] if e["status"] == "complete")
    assert rep["listings_parsed"] == 5 and rep["listings_duplicate"] == 0 and rep["listings_kept"] == 5 and rep["listings_unkeyed"] == 1
    assert rep["completeness"]["per_field"]["location"] == pytest.approx(4 / 5)
    assert rep["label_distribution"] == {"needs_user": 5}
    assert rep["reason_distribution"] == {"evidence_missing": 5}
    assert any("Tam watchlist kapsamı SAYILMAZ" in x for x in rep["not_verified"])


def test_two_runs_are_identical(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, _ = run(tmp_path / "a")
    b, _ = run(tmp_path / "b")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert shadow_run.render_markdown(a) == shadow_run.render_markdown(b)


def test_report_schema(tmp_path):
    rep, _ = run(tmp_path)
    assert set(rep) == {"schema", "rule_version", "mode", "employers_total", "employers_supported",
                        "employers_pending_adapter", "employers", "listings_parsed", "listings_kept",
                        "listings_duplicate", "listings_unkeyed", "completeness", "label_distribution",
                        "reason_distribution", "sheet_comparison", "pending_employers", "not_verified", "vacancies"}
    assert rep["schema"] == "EJS-SHADOW-REPORT-2" and rep["mode"] == "shadow_no_submit"
    for e in rep["employers"]:
        assert set(e) == {"employer", "market", "ats", "status", "error_kind", "listings", "requests", "tenant", "region"}
    assert set(rep["completeness"]["per_field"]) == set(shadow_run.LISTING_FIELDS)
    json.dumps(rep)
    md = shadow_run.render_markdown(rep)
    assert "Adaptör bekleyen işverenler (33)" in md and "Doğrulanmadı" in md


def test_carrier_off_reports_no_carrier(tmp_path):
    rep, car = run(tmp_path, enabled=False)
    errs = [e for e in rep["employers"] if e["status"] == "error"]
    assert len(errs) == 4 and {e["error_kind"] for e in errs} == {"no_carrier"}
    assert car.total == 0 and rep["listings_parsed"] == 0


@pytest.mark.parametrize("status,kind", [(429, "http_429"), (403, "http_403"), (503, "http_503")])
def test_status_error_class_per_employer(tmp_path, status, kind):
    rep, car = run(tmp_path, transport=transport_for(status=status))
    errs = [e for e in rep["employers"] if e["status"] == "error"]
    assert {e["error_kind"] for e in errs} == {kind} and len(errs) == 4
    assert car.total == 4               # one attempt per employer, no retry


def test_bad_json_and_empty_list(tmp_path):
    def t(url, h, to):
        if "hsogroupbv" in url:
            return 200, b"{not json"
        if "cartrawler" in url:
            return 200, b"[]"
        if "veepee" in url:
            return 200, b""
        return 200, b'{"content": [], "totalFound": 0, "offset": 0, "limit": 100}'
    rep, _ = run(tmp_path, transport=t)
    by = {e["employer"]: e for e in rep["employers"] if e["status"] != "pending_adapter"}
    assert by["HSO International"]["error_kind"] == "bad_payload"
    assert by["CarTrawler"]["status"] == "empty" and by["CarTrawler"]["listings"] == 0
    assert by["Veepee"]["error_kind"] == "bad_payload"
    assert by["Rituals"]["status"] == "empty" and rep["listings_parsed"] == 0
    assert rep["completeness"]["all_six_fields_ratio"] is None


def test_disallowed_host_in_run_is_reported(tmp_path):
    wl = tmp_path / "wl.csv"
    write_watchlist(wl)
    c = Clock()
    car = HttpCarrier(enabled=True, transport=transport_for(), clock=c.now, sleep=c.sleep,
                      allowed_hosts=frozenset({"api.lever.co"}))
    rep = shadow_run.run_shadow(shadow_run.read_watchlist(wl), criteria(), fetch=car,
                                begin_employer=car.begin_employer, request_counts=lambda: car.per_employer)
    kinds = {e["employer"]: e["error_kind"] for e in rep["employers"] if e["status"] != "pending_adapter"}
    assert kinds["HSO International"] == "host_not_allowed" and kinds["Rituals"] == "host_not_allowed"
    assert kinds["CarTrawler"] is None and car.total == 2


def test_sheet_comparison(tmp_path):
    rep, _ = run(tmp_path, sheet={"https://jobs.lever.co/cartrawler/1": "Apply", "https://jobs.smartrecruiters.com/Rituals1/9": "needs_user",
                                  "https://x/none": "skip"})
    cmp = rep["sheet_comparison"]
    assert cmp["agree"] == 1 and cmp["disagree"] == [["https://jobs.lever.co/cartrawler/1", "needs_user", "auto_apply"]]
    assert cmp["only_sheet"] == ["https://x/none"] and cmp["only_ejs"] == 2


# ------------------------------------------------------------------ CLI + static negatives
def load_script():
    spec = importlib.util.spec_from_file_location("shadow_discovery_cli", ROOT / "scripts" / "shadow_discovery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_offline_default_and_fixture_replay(tmp_path, capsys):
    from tests.unit.phase1a_support import CRITERIA_YAML
    wl, crit, fx = tmp_path / "wl.csv", tmp_path / "c.yaml", tmp_path / "fx"
    write_watchlist(wl)
    crit.write_text(CRITERIA_YAML, encoding="utf-8")
    write_pilot_fixtures(fx)
    cli = load_script()
    outs = []
    for name, extra in (("off", []), ("r1", ["--fixture-dir", str(fx)]), ("r2", ["--fixture-dir", str(fx)])):
        out = tmp_path / name
        assert cli.main(["--watchlist", str(wl), "--criteria", str(crit), "--out-dir", str(out), *extra]) == 0
        outs.append(out)
    off = json.loads((outs[0] / "report.json").read_text())
    assert {e["error_kind"] for e in off["employers"] if e["status"] == "error"} == {"no_carrier"}
    for name in ("report.json", "report.md", "request_log.json"):
        assert (outs[1] / name).read_bytes() == (outs[2] / name).read_bytes()
    assert json.loads((outs[1] / "report.json").read_text())["listings_parsed"] == 5
    assert "requests=4" in capsys.readouterr().out


FORBIDDEN = ("submit", "ledger", "runtime", "durable_submission", "policy_submission", "browser", "playwright",
             "single_application", "orchestrator", "github_executor")


def _imports(path: Path):
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            yield n.module
            yield from (f"{n.module}.{a.name}" for a in n.names)


def _closure(start: list[Path]):
    seen, todo, names = set(), list(start), set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        for m in _imports(p):
            names.add(m)
            if m.startswith("ejs"):
                f = ROOT / "src" / Path(*m.split("."))
                for cand in (f.with_suffix(".py"), f / "__init__.py"):
                    if cand.exists():
                        todo.append(cand)
    return names


def test_no_submission_ledger_runtime_in_import_graph():
    names = _closure([ROOT / "src/ejs/services/shadow_run.py", ROOT / "src/ejs/services/http_carrier.py",
                      ROOT / "scripts/shadow_discovery.py"])
    bad = sorted(n for n in names if any(f in n.lower() for f in FORBIDDEN))
    assert bad == [], bad
    src = "".join((ROOT / p).read_text() for p in ("src/ejs/services/shadow_run.py", "src/ejs/services/http_carrier.py",
                                                    "scripts/shadow_discovery.py"))
    assert "runtime.json" not in src and "requests" not in {n.split(".")[0] for n in names}
    assert not {"urllib.request", "http.client", "socket"} & {n for n in _closure(
        [ROOT / "src/ejs/services/shadow_run.py"])}


def smart_page(offset, total, ids):
    return {"limit": 2, "offset": offset, "totalFound": total,
            "content": [{"id": str(posting_id), "name": "Analyst",
                         "company": {"identifier": "rituals1"},
                         "location": {"city": "Amsterdam", "country": "NL"}} for posting_id in ids]}


def run_one(url, fetch):
    return shadow_run.run_shadow(
        [{"employer": "Employer", "market": "Netherlands", "url": url}], criteria(), fetch=fetch
    )


def test_rituals_short_first_page_uses_total_and_preserves_distinct_same_title_jobs():
    requested = []
    pages = iter([smart_page(0, 3, [101, 102]), smart_page(2, 3, [103])])

    def fetch(url):
        requested.append(url)
        return next(pages)

    report = run_one(PILOT[3][2], fetch)
    assert [url.split("?")[1] for url in requested] == ["offset=0&limit=100", "offset=2&limit=100"]
    assert report["employers"][0]["status"] == "complete"
    records = report["vacancies"]
    assert [record["posting_id"] for record in records] == ["101", "102", "103"]
    assert len({record["similarity_key"] for record in records}) == 1
    assert records[2]["discovery_evidence"]["feed_url"] == requested[1]
    assert records[0]["url"] == "https://jobs.smartrecruiters.com/Rituals1/101"
    assert records[0]["criteria_evaluation"]["label"] == "needs_user"


@pytest.mark.parametrize(("second", "error"), [
    (smart_page(2, 3, []), "pagination_no_progress"),
    (smart_page(0, 3, [103]), "invalid_pagination"),
    (smart_page(2, 4, [103]), "invalid_pagination"),
])
def test_rituals_incomplete_pagination_keeps_evidence(second, error):
    pages = iter([smart_page(0, 3, [101, 102]), second])
    report = run_one(PILOT[3][2], lambda _: next(pages))
    entry = report["employers"][0]
    assert (entry["status"], entry["error_kind"]) == ("partial", error)
    assert len(report["vacancies"]) >= 2


def test_smartrecruiters_repeated_page_is_partial_even_with_valid_pagination():
    pages = iter([smart_page(0, 4, [101, 102]), smart_page(2, 4, [101, 102])])
    report = run_one(PILOT[3][2], lambda _: next(pages))
    assert report["employers"][0]["error_kind"] == "overlapping_page"
    assert report["employers"][0]["status"] == "partial"
    assert len(report["vacancies"]) == 2


def test_smartrecruiters_conflicting_publisher_is_rejected():
    page = smart_page(0, 1, [101])
    page["content"][0]["company"]["identifier"] = "another-tenant"
    report = run_one(PILOT[3][2], lambda _: page)
    assert report["employers"][0]["status"] == "error"
    assert report["vacancies"] == []


@pytest.mark.parametrize(("total", "status"), [(0, "empty"), (1, "error"), (False, "error")])
def test_greenhouse_empty_requires_matching_provider_total(total, status):
    report = run_one(PILOT[0][2], lambda _: {"jobs": [], "meta": {"total": total}})
    assert report["employers"][0]["status"] == status


def test_greenhouse_unpaginated_total_mismatch_is_partial():
    report = run_one(PILOT[0][2], lambda _: {"jobs": [{"id": 1, "title": "Analyst"}], "meta": {"total": 2}})
    assert report["employers"][0]["status"] == "partial"
    assert report["vacancies"][0]["url"] == "https://job-boards.eu.greenhouse.io/hsogroupbv/jobs/1"


@pytest.mark.parametrize(("last_page", "status", "offsets"), [
    (1, "complete", [0, 100, 200, 300, 400]),
    (100, "partial", [0, 100, 200, 300, 400]),
])
def test_lever_fifth_page_boundary(last_page, status, offsets):
    from urllib.parse import parse_qs, urlparse

    requested = []

    def fetch(url):
        params = parse_qs(urlparse(url).query)
        skip = int(params["skip"][0])
        requested.append(skip)
        size = last_page if skip == 400 else int(params["limit"][0])
        return [{"id": f"p-{i}", "text": "Analyst"} for i in range(skip, skip + size)]

    report = run_one(PILOT[1][2], fetch)
    assert requested == offsets
    assert report["employers"][0]["status"] == status
    assert report["employers"][0]["error_kind"] == ("page_limit_exceeded" if status == "partial" else None)


def test_lever_exact_full_page_requires_empty_next_page():
    page = [{"id": f"p-{i}", "text": "Analyst"} for i in range(100)]
    pages = iter([page, []])
    report = run_one(PILOT[1][2], lambda _: next(pages))
    assert report["employers"][0]["status"] == "complete"
    assert len(report["vacancies"]) == 100
    assert next(pages, None) is None


def test_posting_identity_dedupes_without_location_and_retains_unknowns():
    jobs = [{"id": "same", "text": "Analyst"}, {"id": "same", "text": "Analyst"},
            {"text": "Analyst"}, {"text": "Analyst"}]
    report = run_one(PILOT[1][2], lambda _: jobs)
    assert (report["listings_duplicate"], report["listings_unkeyed"]) == (1, 2)
    assert [(r["posting_id"], r["identity_status"], r["similarity_key"]) for r in report["vacancies"]] == [
        ("same", "known", None), (None, "unknown", None), (None, "unknown", None)
    ]


def test_posting_identity_is_scoped_to_region_and_tenant():
    rows = [{"employer": employer, "market": "Netherlands", "url": url} for employer, url in [
        ("Acme", "https://jobs.lever.co/acme"), ("Acme EU", "https://jobs.eu.lever.co/acme"),
        ("Other", "https://jobs.lever.co/other"),
    ]]
    report = shadow_run.run_shadow(rows, criteria(), fetch=lambda _: [{"id": "same", "text": "Analyst"}])
    assert report["listings_duplicate"] == 0
    assert [record["region"] for record in report["vacancies"]] == ["global", "eu", "global"]


@pytest.mark.parametrize(("board", "api", "region"), [
    ("https://job-boards.eu.greenhouse.io/hsogroupbv/jobs/1",
     "https://boards.eu.greenhouse.io/v1/boards/hsogroupbv/jobs", "eu"),
    ("https://boards.eu.greenhouse.io/embed/job_board?for=hsogroupbv",
     "https://boards.eu.greenhouse.io/v1/boards/hsogroupbv/jobs", "eu"),
    ("https://job-boards.greenhouse.io/acme",
     "https://boards-api.greenhouse.io/v1/boards/acme/jobs", "global"),
    ("https://jobs.eu.lever.co/acme",
     "https://api.eu.lever.co/v0/postings/acme?mode=json", "eu"),
    ("https://jobs.lever.co/acme",
     "https://api.lever.co/v0/postings/acme?mode=json", "global"),
])
def test_regional_endpoints_and_explicit_host_boundary(board, api, region):
    from ejs.services.discovery import classify_url, list_api_url

    ats = classify_url(board)
    assert ats.region == region
    assert list_api_url(ats) == api
    carrier, _ = make(ok())
    carrier(api)
    carrier.begin_employer("Unexpected")
    with pytest.raises(HostNotAllowedError):
        carrier("https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs")
    assert carrier.total == 1


def pilot_cli_args(tmp_path):
    from tests.unit.phase1a_support import CRITERIA_YAML

    watchlist, rule_file, out = (tmp_path / name for name in ("pilot.csv", "criteria.yaml", "out"))
    watchlist.write_text("Employer,Primary Market,Reference URL\n" +
                         "".join(f"{name},{market},{url}\n" for name, market, url in PILOT), encoding="utf-8")
    rule_file.write_text(CRITERIA_YAML, encoding="utf-8")
    return watchlist, out, ["--watchlist", str(watchlist), "--criteria", str(rule_file), "--out-dir", str(out)]


def test_live_cli_persists_only_allowlisted_vacancy_fields_and_fails_partial(tmp_path, monkeypatch):
    _, out, args = pilot_cli_args(tmp_path)
    cli = load_script()
    clock = Clock()
    bodies = {
        **BODIES,
        "hsogroupbv": {"meta": {"total": 1}, "jobs": [
            {"id": 7, "title": "Analyst", "location": {"name": "Utrecht", "street": "Synthetic Street"},
             "absolute_url": "https://untrusted.invalid/contact?token=secret-query",
             "contact": {"name": "Synthetic Person", "number": 31612345678},
             "content": "raw-description", "department": {"name": "raw-department"}},
        ]},
        "veepee": [],
        "Rituals1": smart_page(0, 3, [101, 102]),
    }
    transport = transport_for(bodies)
    calls = []

    def fetch(url, headers, timeout):
        calls.append(url)
        if "Rituals1" in url and "offset=2" in url:
            return 503, b"raw-error-content"
        return transport(url, headers, timeout)

    monkeypatch.setattr(cli, "urllib_transport", fetch)
    monkeypatch.setattr(cli.time, "monotonic", clock.now)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    assert cli.main([*args, "--live"]) == 1
    assert len(calls) == 5
    assert set(p.name for p in out.iterdir()) == {"report.json", "report.md", "request_log.json"}
    for path in out.iterdir():
        data = path.read_text(encoding="utf-8")
        assert not any(value in data for value in (
            "Synthetic Person", "31612345678", "raw-description", "raw-department",
            "raw-error-content", "secret-query", "Synthetic Street", "untrusted.invalid",
        ))
    report = json.loads((out / "report.json").read_text())
    assert report["transport_mode"] == "live"
    assert [e["status"] for e in report["employers"]] == ["complete", "complete", "empty", "partial"]
    entry = report["vacancies"][0]
    assert set(entry) == {"company", "ats_kind", "tenant", "region", "posting_id", "role", "location",
                          "url", "source", "identity_status", "similarity_key", "discovery_evidence",
                          "criteria_evaluation"}
    assert (entry["company"], entry["posting_id"], entry["url"]) == (
        "HSO International", "7", "https://job-boards.eu.greenhouse.io/hsogroupbv/jobs/7"
    )
    evidence = json.loads((out / "request_log.json").read_text())
    assert [item["status"] for item in evidence] == [200, 200, 200, 200, 503]
    assert all(b["t"] - a["t"] >= 1 for a, b in zip(evidence, evidence[1:]))


def test_live_cli_refuses_raw_recording_and_out_of_scope_tenant(tmp_path):
    wl, out, args = pilot_cli_args(tmp_path)
    cli = load_script()
    with pytest.raises(SystemExit) as raw:
        cli.main([*args, "--live", "--record-dir", str(tmp_path / "raw")])
    assert raw.value.code == 2
    wl.write_text(wl.read_text().replace("hsogroupbv", "some-other-tenant"))
    with pytest.raises(SystemExit) as scope:
        cli.main([*args, "--live"])
    assert scope.value.code == 2
    assert not out.exists()


def test_live_transport_get_no_proxy_no_redirect_and_no_sensitive_headers(monkeypatch):
    import urllib.error
    import urllib.request

    def opener(proxy, redirect):
        assert proxy.proxies == {}
        assert redirect.redirect_request(None, None, None, None, "https://evil.invalid/") is None

        class Opener:
            def open(self, request, timeout):
                assert request.get_method() == "GET" and request.full_url == GH_URL
                assert set(name.lower() for name, _ in request.header_items()) == {"user-agent", "accept"}
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {}, None)

        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    assert hc.urllib_transport(GH_URL, {**hc.REQUEST_HEADERS, "Cookie": "secret", "Authorization": "secret"}, 1) == (302, b"")


@pytest.mark.parametrize(("jobs", "expected_exit"), [([], 0), ([{"text": "Unknown ID"}], 1)])
def test_pilot_exit_distinguishes_verified_empty_and_unknown_identity(tmp_path, monkeypatch, jobs, expected_exit):
    _, out, args = pilot_cli_args(tmp_path)
    cli = load_script()
    clock = Clock()
    bodies = {**BODIES, "veepee": jobs}
    monkeypatch.setattr(cli, "urllib_transport", transport_for(bodies))
    monkeypatch.setattr(cli.time, "monotonic", clock.now)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    assert cli.main([*args, "--live"]) == expected_exit
    report = json.loads((out / "report.json").read_text())
    assert report["listings_unkeyed"] == len(jobs)
    assert report["employers"][2]["status"] == ("complete" if jobs else "empty")


def test_fixture_replays_smartrecruiters_second_page(tmp_path):
    _, out, args = pilot_cli_args(tmp_path)
    fx = tmp_path / "fixture"
    write_pilot_fixtures(fx)
    write_fixture(fx, "api.smartrecruiters.com", "Rituals1", 0, smart_page(0, 3, [101, 102]))
    write_fixture(fx, "api.smartrecruiters.com", "Rituals1", 2, smart_page(2, 3, [103]))
    assert load_script().main([*args, "--fixture-dir", str(fx)]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["transport_mode"] == "fixture"
    assert [v["posting_id"] for v in report["vacancies"] if v["tenant"] == "Rituals1"] == ["101", "102", "103"]


def test_live_pilot_fails_smartrecruiters_overlap_and_keeps_three_unique_vacancies(tmp_path, monkeypatch):
    _, out, args = pilot_cli_args(tmp_path)
    cli = load_script()
    clock = Clock()
    base_transport = transport_for({**BODIES, "Rituals1": smart_page(0, 4, [101, 102])})

    def fetch(url, headers, timeout):
        if "Rituals1" in url and "offset=2" in url:
            return 200, json.dumps(smart_page(2, 4, [102, 103])).encode()
        return base_transport(url, headers, timeout)

    monkeypatch.setattr(cli, "urllib_transport", fetch)
    monkeypatch.setattr(cli.time, "monotonic", clock.now)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    assert cli.main([*args, "--live"]) == 1
    report = json.loads((out / "report.json").read_text())
    rituals = next(entry for entry in report["employers"] if entry["tenant"] == "Rituals1")
    assert (rituals["status"], rituals["error_kind"], rituals["requests"]) == ("partial", "overlapping_page", 2)
    assert [item["posting_id"] for item in report["vacancies"] if item["tenant"] == "Rituals1"] == [
        "101", "102", "103",
    ]


@pytest.mark.parametrize("host", ["jobs.lever.co", "jobs.eu.lever.co"])
def test_lever_short_final_page_with_overlap_is_partial(host):
    first = [{"id": f"p-{i}", "text": "Analyst"} for i in range(100)]
    second = [{"id": "p-99", "text": "Analyst"}, {"id": "p-100", "text": "Analyst"}]
    pages = iter([first, second])
    report = run_one(f"https://{host}/employer", lambda _: next(pages))
    entry = report["employers"][0]
    assert (entry["status"], entry["error_kind"]) == ("partial", "overlapping_page")
    assert report["listings_duplicate"] == 1
    assert len(report["vacancies"]) == 101
    assert report["vacancies"][-1]["posting_id"] == "p-100"


@pytest.mark.parametrize("write_eu_fixture", [True, False])
def test_fixture_replay_isolates_regional_lever_tenants(tmp_path, write_eu_fixture):
    watchlist, out, args = pilot_cli_args(tmp_path)
    watchlist.write_text(
        "Employer,Primary Market,Reference URL\n"
        "Global,Netherlands,https://jobs.lever.co/acme\n"
        "EU,Netherlands,https://jobs.eu.lever.co/acme\n",
        encoding="utf-8",
    )
    fx = tmp_path / "fixture"
    write_fixture(fx, "api.lever.co", "acme", 0, [{"id": "global", "text": "Global Analyst"}])
    if write_eu_fixture:
        write_fixture(fx, "api.eu.lever.co", "acme", 0, [{"id": "eu", "text": "EU Analyst"}])
    else:
        (fx / "acme.json").write_text('[{"id":"legacy","text":"Legacy Analyst"}]', encoding="utf-8")

    assert load_script().main([*args, "--fixture-dir", str(fx)]) == (0 if write_eu_fixture else 1)
    report = json.loads((out / "report.json").read_text())
    assert [item["posting_id"] for item in report["vacancies"]] == (["global", "eu"] if write_eu_fixture else ["global"])
    eu = report["employers"][1]
    assert (eu["status"], eu["error_kind"]) == (("complete", None) if write_eu_fixture else ("error", "missing_fixture"))


def test_missing_later_fixture_is_partial_instead_of_reusing_first_page(tmp_path):
    _, out, args = pilot_cli_args(tmp_path)
    fx = tmp_path / "fixture"
    write_pilot_fixtures(fx)
    write_fixture(fx, "api.smartrecruiters.com", "Rituals1", 0, smart_page(0, 3, [101, 102]))
    assert load_script().main([*args, "--fixture-dir", str(fx)]) == 1
    report = json.loads((out / "report.json").read_text())
    rituals = next(entry for entry in report["employers"] if entry["tenant"] == "Rituals1")
    assert (rituals["status"], rituals["error_kind"], rituals["requests"]) == ("partial", "missing_fixture", 2)
    log = json.loads((out / "request_log.json").read_text())
    assert log[-1]["status"] == "missing_fixture"
    assert "offset=2" in log[-1]["url"]
