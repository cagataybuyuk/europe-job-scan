import socket
import sys

import pytest

from ejs.apps import watchlist_coverage

HEADER = "Employer,Primary Market,Reference Vacancy / Careers URL\n"


@pytest.mark.parametrize(
    ("url", "provider", "status", "detail"),
    [
        ("https://JOBS.SMARTRECRUITERS.COM./example/1", "smartrecruiters", "supported", "prepare"),
        ("https://workforcenow.adp.com/jobs/1", "adp", "supported", "live_canary"),
        ("https://job-boards.eu.greenhouse.io/example/jobs/1", "greenhouse", "unsupported", "planned"),
        ("https://jobs.lever.co/example/1", "lever", "unsupported", "planned"),
        ("https://example.wd3.myworkdayjobs.com/1", "workday", "unsupported", "planned"),
        ("https://example.recruitee.com/1", "recruitee", "unsupported", "No registry adapter"),
        ("https://apply.workable.com/example/1", "workable", "unsupported", "No registry adapter"),
        ("https://example.hirehive.com/1", "hirehive", "unsupported", "No registry adapter"),
        ("https://www.linkedin.com/redir/redirect?url=https://jobs.lever.co/1", "", "unknown", "LinkedIn destination unresolved"),
        ("https://nl.linkedin.com/jobs/view/1", "", "unknown", "LinkedIn destination unresolved"),
        ("https://indeed.com/1", "", "unknown", "No verified ATS host"),
        ("https://jobs.smartrecruiters.com.example.org", "", "unknown", "No verified ATS host"),
        ("https://not-hirehive.com/1", "", "unknown", "No verified ATS host"),
        ("https://careers.example.org/?next=https://jobs.lever.co", "", "unknown", "No verified ATS host"),
        ("https://example.org@jobs.lever.co/1", "", "unknown", "No usable HTTPS reference"),
        ("http://jobs.lever.co/1", "", "unknown", "No usable HTTPS reference"),
        ("https://jobs.lever.co:invalid/1", "", "unknown", "No usable HTTPS reference"),
        ("https://[broken", "", "unknown", "No usable HTTPS reference"),
        ("", "", "unknown", "No usable HTTPS reference"),
    ],
)
def test_classify_employer(url, provider, status, detail):
    row = watchlist_coverage.classify_employer("Example", "Ireland", url)
    assert (row.provider, row.status, row.detail) == (provider, status, detail)


def test_cli_reads_only_employer_section_and_reports_coverage(tmp_path, monkeypatch, capsys):
    def deny_network(*args, **kwargs):
        pytest.fail("Coverage report attempted network access")

    monkeypatch.setattr(socket, "socket", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    source = tmp_path / "Employer Watchlist.csv"
    source.write_text(
        "Employer Watchlist v1,,\nWatchlist Employers,999,\n" + HEADER
        + '"A & B | Europe, Ltd",Ireland,https://jobs.smartrecruiters.com/demo/1?opaque=value\n'
        + ",,\n"
        + "Employer Two,Netherlands,https://boards.greenhouse.io/demo/jobs/1\n"
        + "Employer Three,Spain,\n"
        + "Priority Employer Scan Queue,,\n"
        + "Employer,Watchlist Tier,Weighted Priority\nEmployer Two,B,80\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(sys, "argv", ["watchlist_coverage", str(source)])
    watchlist_coverage.main()
    report = capsys.readouterr().out
    assert "- Employers: 3\n- Supported: 1\n- Unsupported: 1\n- Unknown: 1\n- Coverage: 33.3%" in report
    assert "- greenhouse: 1" in report
    assert r"| A &amp; B \| Europe, Ltd | Ireland | jobs.smartrecruiters.com |" in report
    assert "opaque" not in report
    assert "Priority Employer Scan Queue" not in report
    watchlist_coverage.main()
    assert capsys.readouterr().out == report


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("wrong,header\n", "header not found"),
        (HEADER, "section is empty"),
        (HEADER + "Example,,https://jobs.lever.co/1\n", "Invalid employer row at CSV line 2"),
        (HEADER + "Example,Ireland\n", "Invalid employer row at CSV line 2"),
        (HEADER + "Example,Ireland,\nexample,Spain,\n", "Duplicate employer at CSV line 3"),
    ],
)
def test_malformed_export_is_rejected(tmp_path, contents, message):
    source = tmp_path / "watchlist.csv"
    source.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        watchlist_coverage.read_coverage(source)
