"""Exercise the shadow CLI with a network-denied synthetic expansion snapshot."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from ejs.services.application_bridge import prepare_application_report
from ejs.services.criteria_loader import SCORE_COMPONENTS
from ejs.services.expansion_replay import COUNTRIES, RESEARCH, initial_checkpoint
from scripts import expansion_replay_store as store, shadow_discovery as cli
from tests.test_expansion_replay import TENANT, page
from tests.unit.phase1a_support import CRITERIA_YAML


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("synthetic CLI must never construct a carrier or access the network")

    for name in ("HttpCarrier", "ReplayCarrier", "urllib_transport", "fixture_transport"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def entry(payload, *, tenant=None, country=None, offset=None):
    return {"tenant": tenant or payload["tenant"], "country": country or payload["country"],
            "offset": payload["offset"] if offset is None else offset, "page": payload}


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path.resolve()
    watchlist = root / "scope.csv"
    watchlist.write_text("Employer,Primary Market,Reference URL\n" + "".join(
        f"{name},Synthetic market,https://jobs.smartrecruiters.com/{tenant}\n"
        for name, tenant, _, _ in reversed(RESEARCH)), encoding="utf-8")
    criteria = root / "criteria.yaml"
    criteria.write_text(CRITERIA_YAML, encoding="utf-8")
    fixtures = root / "pages.json"
    fixtures.write_text(json.dumps([
        entry(page(c.country, total=1, ids=(c.country,), tenant=c.tenant))
        for c in initial_checkpoint().cursors
    ]), encoding="utf-8")
    return watchlist, criteria, fixtures


def args(inputs, out):
    watchlist, criteria, fixtures = inputs
    return ["--watchlist", str(watchlist), "--criteria", str(criteria), "--out-dir", str(out),
            "--expansion-fixtures", str(fixtures)]


def report(out):
    return json.loads((out / store.REPORT_NAMES[0]).read_bytes())


def test_all_243_pairs_and_identical_final_reports_under_different_batch_sizes(inputs):
    pages = json.loads(inputs[2].read_bytes())
    pages[0] = entry(page("NL", total=2, ids=("NL",)))
    pages[1] = entry(page("IE", total=1, ids=("NL",)))
    pages[2] = entry(page("DE"))
    pages.append(entry(page("NL", offset=1, total=2, ids=("other",))))
    inputs[2].write_text(json.dumps(pages), encoding="utf-8")
    outputs = []
    for budget in (25, 60):
        out = inputs[0].parent / str(budget)
        run = [*args(inputs, out), "--expansion-max-pages", str(budget)]
        assert cli.main([*run, "--expansion-init"]) == 3
        first = report(out)
        assert first["coverage"]["status"] == "incomplete"
        assert first["coverage"]["counts"]["completed"] < 243
        for _ in range(15):
            status = cli.main(run)
            if status == 0:
                break
            assert status == 3
        else:
            pytest.fail("replay did not converge")
        final = report(out)
        assert final["coverage"]["counts"] == {"completed": 243, "incomplete": 0, "blocked": 0, "failed": 0}
        assert final["coverage"]["countries"] == list(COUNTRIES)
        assert final["employers_total"] == final["employers_supported"] == 27
        assert all(e["requests"] == 0 and e["status"] == "completed" for e in final["employers"])
        assert final["network_requests"] == 0
        assert final["listings_parsed"] == 243
        assert final["listings_kept"] == 242 and final["listings_duplicate"] == 1
        assert final["listings_unkeyed"] == 0
        identities = {(v["tenant"], v["posting_id"]) for v in final["vacancies"]}
        assert len(identities) == 242
        vacancy = final["vacancies"][0]
        assert vacancy["url"] == f"https://jobs.smartrecruiters.com/{TENANT}/NL"
        assert vacancy["location"] == "Synthetic city, NL"
        assert vacancy["discovery_evidence"] == {"observation": "synthetic_country_replay",
                                                 "countries": ["NL", "IE"], "feed_url": None}
        evaluation = vacancy["criteria_evaluation"]
        assert evaluation["label"] == "needs_user" and evaluation["reason"] == "vacancy_evidence_missing"
        assert set(evaluation["unknown_components"]) == set(SCORE_COMPONENTS)
        assert final["label_distribution"] == {"needs_user": 242}
        assert final["sponsorship_eligibility"] == "unknown"
        assert final["application_readiness"] == "unverified"
        assert final["execution_authorized"] is False
        with pytest.raises(ValueError, match="^discovery_schema$"):
            prepare_application_report(final, detail_budget=1,
                                       transport=lambda _: pytest.fail("unexpected detail transport"))
        assert final["coverage"]["pairs"][2]["declared_total"] == 0
        outputs.append(tuple((out / name).read_bytes() for name in store.REPORT_NAMES))
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("failure", ["wrong_page_country", "missing_page", "total_drift"])
def test_failure_and_blocked_coverage_keep_accepted_rows_and_allow_other_tenants(inputs, failure):
    pages = json.loads(inputs[2].read_bytes())
    pages[0] = entry(page("NL", total=2, ids=("saved",)))
    if failure == "wrong_page_country":
        pages.append(entry(page("DE", offset=1, total=2, ids=("untrusted",)), country="NL"))
    elif failure == "total_drift":
        pages.append(entry(page("NL", offset=1, total=3, ids=("untrusted",))))
    inputs[2].write_text(json.dumps(pages), encoding="utf-8")
    out = inputs[0].parent / "failure"
    run = args(inputs, out)
    assert cli.main([*run, "--expansion-init", "--expansion-max-pages", "1"]) == 3
    assert cli.main([*run, "--expansion-max-pages", "2"]) == 1
    result = report(out)
    coverage = result["coverage"]
    assert coverage["status"] == "failed"
    assert coverage["counts"] == {"completed": 1, "incomplete": 233, "blocked": 8, "failed": 1}
    assert coverage["pairs"][0] == {"tenant": TENANT, "country": "NL", "status": "failed",
                                    "error_kind": "page_review_required", "accepted_listings": 1,
                                    "declared_total": 2}
    assert coverage["pairs"][1]["error_kind"] == "prior_country_failed"
    assert coverage["pairs"][9]["status"] == "completed"
    assert [(v["tenant"], v["posting_id"]) for v in result["vacancies"]] == [(TENANT, "saved"), ("Devoteam", "NL")]


def test_report_publish_failure_after_commit_recovers_in_new_process(inputs, monkeypatch):
    pages = json.loads(inputs[2].read_bytes())
    pages[0]["page"]["content"][0].update(applyUrl="https://example.invalid/?secret=sentinel",
                                         sponsorship="guaranteed", execution_authorized=True)
    pages[1] = entry(page("IE", total=1, ids=("NL",)))
    inputs[2].write_text(json.dumps(pages), encoding="utf-8")
    out = inputs[0].parent / "recovery"
    run = [*args(inputs, out), "--expansion-max-pages", "1"]
    publish = store._publish_file

    def fail(directory_fd, name, raw):
        if name == store.REPORT_NAMES[0]:
            raise OSError("simulated failure after bundle replacement")
        publish(directory_fd, name, raw)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_publish_file", fail)
        with pytest.raises(SystemExit, match="^2$"):
            cli.main([*run, "--expansion-init"])
    bundle = out / store.BUNDLE_NAME
    assert bundle.exists() and not (out / store.REPORT_NAMES[0]).exists()
    # Run the documented script entry point with an independent process and a socket tripwire.
    root = Path(__file__).resolve().parents[1]
    guard = inputs[0].parent / "sitecustomize.py"
    guard.write_text("import socket\ndef denied(*a, **k):\n raise AssertionError('network denied')\n"
                     "socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = denied\n",
                     encoding="utf-8")
    completed = subprocess.run([sys.executable, "scripts/shadow_discovery.py", *run], cwd=root,
                               env={**os.environ, "PYTHONPATH": os.pathsep.join(("src", str(guard.parent)))},
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 3, completed.stderr
    assert "fixture_pages=1 new_listings=0 requests=0" in completed.stdout
    recovered = report(out)
    assert [row["posting_id"] for row in recovered["vacancies"]] == ["NL"]
    assert recovered["listings_duplicate"] == 1
    assert recovered["vacancies"][0]["discovery_evidence"]["countries"] == ["NL", "IE"]
    assert cli.main(run) == 3
    assert [row["posting_id"] for row in report(out)["vacancies"]] == ["NL", "DE"]
    for name in (store.BUNDLE_NAME, *store.REPORT_NAMES):
        data = (out / name).read_bytes()
        assert b"sentinel" not in data and b"guaranteed" not in data
        assert (out / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("changed", ["fixture", "watchlist", "criteria"])
def test_resume_rejects_input_drift_without_touching_bundle_or_reports(inputs, changed):
    out = inputs[0].parent / "pinned"
    run = [*args(inputs, out), "--expansion-max-pages", "1"]
    assert cli.main([*run, "--expansion-init"]) == 3
    files = [out / name for name in (store.BUNDLE_NAME, *store.REPORT_NAMES)]
    before = [path.read_bytes() for path in files]
    if changed == "fixture":
        inputs[2].write_bytes(inputs[2].read_bytes() + b"\n")
    elif changed == "watchlist":
        inputs[0].write_bytes(inputs[0].read_bytes().replace(b"Synthetic market", b"Other market"))
    else:
        inputs[1].write_text(CRITERIA_YAML.replace("unknown_credit_percent: 50", "unknown_credit_percent: 51"),
                             encoding="utf-8")
    with pytest.raises(SystemExit, match="^2$"):
        cli.main(run)
    assert [path.read_bytes() for path in files] == before


@pytest.mark.parametrize("invalid", ["live", "legacy_fixture", "sheet_labels", "budget", "wrong_tenant",
                                     "duplicate_fixture", "duplicate_json_key", "report_symlink"])
def test_rejects_ambiguous_modes_and_untrusted_inputs(inputs, invalid):
    out = inputs[0].parent / "invalid"
    extra = []
    if invalid == "live":
        extra = ["--live"]
    elif invalid == "legacy_fixture":
        extra = ["--fixture-dir", "/missing"]
    elif invalid == "sheet_labels":
        extra = ["--sheet-labels", "/missing"]
    elif invalid == "budget":
        extra = ["--expansion-max-pages", "61"]
    elif invalid == "wrong_tenant":
        inputs[0].write_bytes(inputs[0].read_bytes().replace(TENANT.encode(), TENANT.lower().encode()))
    elif invalid == "duplicate_fixture":
        pages = json.loads(inputs[2].read_bytes())
        inputs[2].write_text(json.dumps([pages[0], pages[0]]), encoding="utf-8")
    elif invalid == "duplicate_json_key":
        inputs[2].write_bytes(b'[{"tenant":"duplicate",' + inputs[2].read_bytes()[2:])
    else:
        out.mkdir(mode=0o700)
        (out / store.REPORT_NAMES[0]).symlink_to(inputs[2])
    with pytest.raises(SystemExit, match="^2$"):
        cli.main([*args(inputs, out), "--expansion-init", *extra])
    assert not (out / store.BUNDLE_NAME).exists()


def test_resume_completed_scope_rebuilds_missing_view_without_rewriting_bundle(inputs, capsys):
    out = inputs[0].parent / "restore-view"
    run = args(inputs, out)
    assert cli.main([*run, "--expansion-init"]) == 3
    for _ in range(10):
        if cli.main(run) == 0:
            break
    else:
        pytest.fail("replay did not converge")
    bundle = out / store.BUNDLE_NAME
    report_path = out / store.REPORT_NAMES[0]
    original_stat, original_report = bundle.stat(), report_path.read_bytes()
    report_path.unlink()
    capsys.readouterr()
    assert cli.main(run) == 0
    assert "fixture_pages=0 new_listings=0 requests=0" in capsys.readouterr().out
    assert report_path.read_bytes() == original_report
    assert bundle.stat().st_ino == original_stat.st_ino


def test_cli_cannot_resume_an_unbound_replay_bundle(inputs):
    out = inputs[0].parent / "unbound"
    store.replay_private_batch(out, {(TENANT, "NL", 0): page("NL")},
                               initialize=True, max_pages=1)
    bundle = out / store.BUNDLE_NAME
    original = bundle.read_bytes()
    with pytest.raises(SystemExit, match="^2$"):
        cli.main(args(inputs, out))
    assert bundle.read_bytes() == original
    assert not (out / store.REPORT_NAMES[0]).exists()
