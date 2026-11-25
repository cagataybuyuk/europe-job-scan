from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from ejs.services.application_bridge import MAX_DETAIL_BYTES, prepare_application_report, validate_candidate_manifest
from tests.test_execution_manifest import NOW, URL, base_document, encode


FIXTURE = json.loads((Path(__file__).parent / "fixtures/application_bridge/detail.json").read_bytes())
DETAIL_URL, DETAIL = next(iter(FIXTURE.items()))
POSTING = DETAIL["id"]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network attempted during offline bridge regression")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def vacancy(posting=POSTING, *, score=0, **overrides):
    return dict(dict(ats_kind="smartrecruiters", region="global", tenant="ExampleCo",
                     posting_id=posting, url=URL.replace(POSTING, posting or ""),
                     company="Example Company", role="Synthetic Analyst", location="Example",
                     criteria_evaluation={"score": score, "label": "needs_user"}), **overrides)


def discovery(*rows):
    return dict(schema="EJS-SHADOW-REPORT-2", vacancies=list(rows), listings_kept=len(rows))


def enriched(payload=DETAIL):
    def transport(url):
        assert url == DETAIL_URL
        return encode(payload)
    return prepare_application_report(discovery(vacancy()), transport=transport, detail_budget=1)["candidates"][0]


def test_scores_only_order_and_duplicate_consumes_one_attempt():
    other = vacancy("744000000000002", score=-5)
    row = vacancy(score=-1)
    report = discovery(other, row, deepcopy(row), vacancy(ats_kind="lever", score=999))
    original = deepcopy(report)
    calls = []

    def transport(url):
        calls.append(url)
        return encode(FIXTURE[url])

    result = prepare_application_report(report, transport=transport, detail_budget=1)
    assert report == original
    assert calls == [DETAIL_URL]
    assert (result["input_rows"], result["candidate_count"], result["duplicate_rows"]) == (4, 3, 1)
    assert [r["readiness"] for r in result["candidates"]] == [
        "ready_for_form_observation", "missing_evidence", "unsupported_ats",
    ]
    assert result["candidates"][0]["source_rows"] == 2
    assert result["candidates"][1]["reason"] == "detail_budget_exhausted"
    assert prepare_application_report(discovery(*reversed(report["vacancies"])),
                                      transport=transport, detail_budget=1) == result
    assert not result["executable"] and not result["submit_authorized"]


@pytest.mark.parametrize(("change", "status", "reason"), [
    ({"active": False}, "hard_blocker", "employer_posting_inactive"),
    ({"active": None}, "missing_evidence", "application_availability_unverified"),
    ({"refNumber": None}, "missing_evidence", "requisition_id_unverified"),
    ({"refNumber": "bad:ref"}, "missing_evidence", "requisition_id_unverified"),
    ({"id": "744000000000009"}, "missing_evidence", "detail_invalid_or_unbound"),
    ({"company": {"identifier": "Other"}}, "missing_evidence", "detail_invalid_or_unbound"),
])
def test_target_binding_and_missing_signals(change, status, reason):
    candidate = enriched(dict(DETAIL, **change))
    assert (candidate["readiness"], candidate["reason"]) == (status, reason)
    if reason in {"requisition_id_unverified", "detail_invalid_or_unbound"}:
        assert candidate["requisition_id"] is None


def test_extract_only_explicit_conditions_with_source():
    payload = deepcopy(DETAIL)
    payload["jobAd"]["sections"]["qualifications"]["text"] = (
        "<p>Applicants must have the legal right to work in Ireland.</p>"
        "<p>We do not provide <strong>visa sponsorship</strong>.</p>"
        "<p>We do not provide visa sponsorship for internships only.</p>"
        "<script>Visa sponsorship is not available.</script>"
    )
    candidate = enriched(payload)
    assert candidate["readiness"] == "missing_evidence"
    assert candidate["reason"] == "eligibility_condition_requires_candidate_review"
    assert [e["condition"] for e in candidate["eligibility_conditions"]] == [
        "existing_work_rights_required", "explicit_no_sponsorship",
    ]
    assert candidate["eligibility_conditions"][1]["quote"] == "We do not provide visa sponsorship"
    assert candidate["detail_source_url"] == DETAIL_URL
    assert len(candidate["detail_sha256"]) == 64


def test_conflicting_or_missing_identity_and_untrusted_targets_do_not_fetch():
    result = prepare_application_report(discovery(
        vacancy(), vacancy(url=URL + "-conflict"), vacancy(None), vacancy(None),
        vacancy("744000000000002", url="https://jobs.smartrecruiters.com/Other/744000000000002"),
    ), transport=lambda url: pytest.fail("unexpected detail attempt"), detail_budget=3)
    assert result["detail_attempts"] == 0
    assert [c["reason"] for c in result["candidates"]] == [
        "missing_discovery_identity", "missing_discovery_identity",
        "conflicting_discovery_urls", "incompatible_posting_identity_or_url",
    ]
    assert result["duplicate_rows"] == 1


def test_size_bound_and_transport_error():
    for transport, reason in [
        (lambda url: b" " * (MAX_DETAIL_BYTES + 1), "detail_invalid_or_unbound"),
        ({}.__getitem__, "detail_unavailable"),
    ]:
        result = prepare_application_report(discovery(vacancy()), transport=transport, detail_budget=1)
        assert result["candidates"][0]["reason"] == reason
        assert result["detail_attempts"] == 1


def test_manifest_uses_real_contract_and_matches_evidenced_target():
    candidate = enriched()
    assert candidate["requisition_id"] == "REF001"
    assert candidate["posting_id"] == POSTING
    assert candidate["readiness"] == "ready_for_form_observation"
    manifest = validate_candidate_manifest(candidate, encode(base_document()), now=NOW)
    assert manifest.requisition_id == candidate["requisition_id"]
    with pytest.raises(PermissionError, match="PREPARATION_MANIFEST_TARGET_MISMATCH"):
        validate_candidate_manifest(dict(candidate, requisition_id="REF-OTHER"),
                                    encode(base_document()), now=NOW)
    invalid = base_document()
    invalid["form"]["fingerprint"] = "0" * 64
    with pytest.raises(PermissionError, match="EXECUTION_MANIFEST_REVIEW_REQUIRED"):
        validate_candidate_manifest(candidate, encode(invalid), now=NOW)


def test_cli_publishes_private_non_executable_fixture_report(tmp_path, monkeypatch):
    import runpy
    import stat
    import sys

    root = Path(__file__).resolve().parents[1]
    tmp_path = tmp_path.resolve()
    tmp_path.chmod(0o700)
    source = tmp_path / "discovery.json"
    source.write_bytes(encode(discovery(vacancy())))
    output = tmp_path / "output"
    script = root / "scripts/application_bridge.py"
    monkeypatch.syspath_prepend(str(script.parent))
    monkeypatch.setattr(sys, "argv", [str(script), str(source), str(output), "--fixtures",
                        str(root / "tests/fixtures/application_bridge/detail.json"), "--detail-budget", "1"])
    runpy.run_path(str(script), run_name="__main__")
    report_path = output / "preparation-report.json"
    original = report_path.read_bytes()
    report = json.loads(original)
    assert report["detail_mode"] == "synthetic_fixtures"
    assert report["readiness_counts"] == {"ready_for_form_observation": 1}
    assert report["submit_authorized"] is False
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="report files already exist"):
        runpy.run_path(str(script), run_name="__main__")
    assert report_path.read_bytes() == original
