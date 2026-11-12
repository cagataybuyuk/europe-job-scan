from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from ejs.services.oneclick_target import OneClickNetworkBoundary, OneClickTarget


@pytest.fixture
def target():
    return OneClickTarget(
        company="Fixture", company_id="a" * 24, posting_id="123456789012345",
        publication_id="01234567-89ab-4cde-8f01-234567890abc", requisition_id="REF1234X",
        posting_url="https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst",
    )


@pytest.fixture
def boundary(target):
    return OneClickNetworkBoundary(target, read_urls=(
        "https://js-www.smartrecruiters.com/oneclick-ui/static/main.abc123.js",
        "https://api.smartrecruiters.com/v1/companies/Fixture/postings/123456789012345",
    ), resume_filename="CV.pdf")


def test_builds_only_target_bound_routes(target):
    base = "https://jobs.smartrecruiters.com/oneclick-ui"
    publication = "01234567-89ab-4cde-8f01-234567890abc"
    assert target.entry_url == f"{base}/company/Fixture/publication/{publication}?dcr_ci=Fixture"
    assert target.screening_url == f"{base}/company/Fixture/publication/{publication}/screening?dcr_ci=Fixture"
    assert target.application_url == f"{base}/api/company/Fixture/publication/{publication}/application"
    assert target.resume_upload_url("CV.pdf") == f"{base}/api/company/{'a' * 24}/attachment/resume?filename=CV.pdf"


@pytest.mark.parametrize("changes", [
    {"company": "../Fixture"}, {"company": "Fixture%2fOther"}, {"company": "Fixture?x"},
    {"company": "Fıxture"}, {"company_id": "A" * 24}, {"company_id": "x" * 24},
    {"posting_id": "123"}, {"posting_id": True}, {"posting_id": "12345678?x"},
    {"publication_id": "01234567-89AB-4cde-8f01-234567890abc"},
    {"publication_id": "0123456789ab4cde8f01234567890abc"},
    {"publication_id": "not-a-uuid"}, {"publication_id": "../application"},
    {"requisition_id": "candidate@example.test"}, {"requisition_id": ""},
    {"posting_url": "https://jobs.smartrecruiters.com/Other/123456789012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture/987654321012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst?job=1"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst#top"},
    {"posting_url": "https://jobs.smartrecruiters.com:444/Fixture/123456789012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com:443/Fixture/123456789012345--analyst"},
    {"posting_url": "https://user:PRIVATE@jobs.smartrecruiters.com/Fixture/123456789012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture%2f123456789012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com/Fixture/123456789012345--analyst/"},
    {"posting_url": "http://jobs.smartrecruiters.com/Fixture/123456789012345--analyst"},
    {"posting_url": "https://jobs.smartrecruiters.com.evil.test/Fixture/123456789012345--analyst"},
])
def test_rejects_ambiguous_or_foreign_target(target, changes):
    with pytest.raises(ValueError, match="^ONECLICK_TARGET_INVALID$") as exc:
        replace(target, **changes)
    assert "PRIVATE" not in str(exc.value)


@pytest.mark.parametrize("name", ["../CV.pdf", "CV.pdf?x=1", "CV.pdf#x", "CV%2epdf", "CV.docx", "CV.pdf.exe", "a/b.pdf", "a\\b.pdf", "CV file.pdf", "a\n.pdf", None, True, ""])
def test_rejects_unsafe_or_non_pdf_upload_name(target, name):
    with pytest.raises(ValueError, match="^ONECLICK_RESUME_FILENAME_INVALID$"):
        target.resume_upload_url(name)


def test_only_exact_reviewed_reads_and_target_navigation(boundary, target):
    for url in (target.posting_url, target.entry_url, target.screening_url):
        assert boundary.allows("GET", url, "document", True)
        assert boundary.allows("HEAD", url)
    asset = "https://js-www.smartrecruiters.com/oneclick-ui/static/main.abc123.js"
    assert boundary.allows("GET", asset, "script")
    assert not boundary.allows("GET", asset, "document", True)
    assert not boundary.allows("GET", target.entry_url, "script", True)
    for extra in ("#x", "&extra=1", "&email=PRIVATE"):
        assert not boundary.allows("GET", target.entry_url + extra, "document", True)
    assert not boundary.allows("GET", asset + "?email=PRIVATE", "script")
    assert not boundary.allows("GET", asset.replace("main.abc123", "other"), "script")
    assert not boundary.allows("GET", target.application_url)
    assert not boundary.allows("GET", target.resume_upload_url("CV.pdf"))
    assert not boundary.allows("GET", target.entry_url, "document", "true")


@pytest.mark.parametrize("read_urls", [
    ["https://example.test/script.js"],
    ("https://example.test/script.js", "https://example.test/script.js"),
    ("https://example.test/",), ("http://example.test/script.js",),
    ("https://user:PRIVATE@example.test/script.js",),
    ("https://example.test/script.js#private",), ("https://example.test/script.js#",),
    ("https://example.test/script\x7f.js",),
    ("https://example.test/*.js",), ("https://example.test/script\n.js",),
    ("https://EXAMPLE.test/script.js",), ("https://example.test:443/script.js",),
    ("https://jobs.smartrecruiters.com/oneclick-ui/api/company/Fixture/publication/01234567-89ab-4cde-8f01-234567890abc/application",),
    ("https://jobs.smartrecruiters.com/oneclick-ui/api/company/Fixture/attachment/resume?filename=CV.pdf",),
])
def test_read_approval_is_exact_bounded_and_not_mutation_endpoint(target, read_urls):
    with pytest.raises(ValueError, match="^ONECLICK_READ_ALLOWLIST_INVALID$") as exc:
        OneClickNetworkBoundary(target, read_urls=read_urls, resume_filename="CV.pdf")
    assert "PRIVATE" not in str(exc.value)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "post"])
def test_writes_default_denied(boundary, target, method):
    assert not boundary.allows(method, target.application_url)
    assert not boundary.allows(method, target.resume_upload_url("CV.pdf"))
    assert boundary.blocked_write_count == 2
    assert boundary.application_requests_allowed == boundary.upload_requests_allowed == 0


def test_upload_one_use_and_no_parser_autosave_or_other_asset(boundary, target):
    boundary.begin_upload()
    url = target.resume_upload_url("CV.pdf")
    for wrong in (url + "&x=1", url.replace("CV.pdf", "Other.pdf"),
                  url.replace("/resume?", "/resume/parse?"),
                  url.replace("a" * 24, "b" * 24)):
        assert not boundary.allows("POST", wrong)
    assert boundary.allows("POST", url)
    assert not boundary.allows("POST", url)
    assert boundary.upload_requests_allowed == 1
    boundary.end_upload()
    assert boundary.phase == "read"
    with pytest.raises(PermissionError, match="^ONECLICK_UPLOAD_PHASE_INVALID$"):
        boundary.begin_upload()
    assert not boundary.allows("POST", url)


def test_submit_is_one_use_even_after_vendor_challenge_retry(boundary, target):
    boundary.begin_submit()
    assert boundary.allows("POST", target.application_url)
    assert boundary.phase == "closed"
    assert boundary.application_requests_allowed == 1
    # DataDome/captcha-passed may invoke submitForm again without a user click.
    assert not boundary.allows("POST", target.application_url)
    assert boundary.application_attempts == 2
    assert boundary.blocked_write_count == 1
    assert boundary.application_requests_allowed == 1
    with pytest.raises(PermissionError, match="^ONECLICK_SUBMIT_PHASE_INVALID$"):
        boundary.begin_submit()
    with pytest.raises(PermissionError, match="^ONECLICK_UPLOAD_PHASE_INVALID$"):
        boundary.begin_upload()


def test_foreign_application_cannot_use_armed_submit(boundary, target):
    boundary.begin_submit()
    for wrong in (target.application_url.replace("Fixture", "Other"),
                  target.application_url.replace(target.publication_id, "11234567-89ab-4cde-8f01-234567890abc"),
                  target.application_url + "?jobId=123456789012345",
                  target.application_url.replace("/publication/", "/public%61tion/"),
                  target.application_url + "/", target.application_url + "#x"):
        assert not boundary.allows("POST", wrong)
    assert boundary.application_requests_allowed == 0
    assert boundary.allows("POST", target.application_url)


def test_target_form_navigation_is_denied_and_session_closed(boundary, target):
    boundary.begin_submit()
    assert not boundary.allows("POST", target.application_url, "document", True)
    assert boundary.phase == "closed"
    assert boundary.application_requests_allowed == 0
    assert not boundary.allows("POST", target.application_url)


def test_premature_application_post_cannot_later_be_armed(boundary, target):
    assert not boundary.allows("POST", target.application_url)
    assert boundary.phase == "closed"
    with pytest.raises(PermissionError, match="^ONECLICK_SUBMIT_PHASE_INVALID$"):
        boundary.begin_submit()
    assert not boundary.allows("POST", target.application_url)
    assert boundary.application_requests_allowed == 0
    assert boundary.application_attempts == 2


def test_target_cannot_be_rebound_after_boundary_construction(boundary, target):
    with pytest.raises(AttributeError):
        boundary.target = replace(target, company_id="b" * 24)


def test_invalid_phase_transitions_remain_closed(boundary):
    with pytest.raises(PermissionError, match="^ONECLICK_UPLOAD_PHASE_INVALID$"):
        boundary.end_upload()
    boundary.begin_upload()
    with pytest.raises(PermissionError, match="^ONECLICK_UPLOAD_PHASE_INVALID$"):
        boundary.begin_upload()
    with pytest.raises(PermissionError, match="^ONECLICK_SUBMIT_PHASE_INVALID$"):
        boundary.begin_submit()
    boundary.end_upload()
    with pytest.raises(PermissionError, match="^ONECLICK_UPLOAD_PHASE_INVALID$"):
        boundary.end_upload()
    boundary.begin_submit()
    with pytest.raises(PermissionError, match="^ONECLICK_SUBMIT_PHASE_INVALID$"):
        boundary.begin_submit()


def test_concurrent_automatic_submissions_allow_at_most_one(boundary, target):
    boundary.begin_submit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        allowed = list(pool.map(lambda _: boundary.allows("POST", target.application_url), range(32)))
    assert sum(allowed) == boundary.application_requests_allowed == 1
    assert boundary.application_attempts == 32
    assert boundary.blocked_write_count == 31


def test_concurrent_uploads_allow_at_most_one(boundary, target):
    boundary.begin_upload()
    with ThreadPoolExecutor(max_workers=8) as pool:
        allowed = list(pool.map(lambda _: boundary.allows("POST", target.resume_upload_url("CV.pdf")), range(32)))
    assert sum(allowed) == boundary.upload_requests_allowed == 1
    assert boundary.blocked_write_count == 31


def test_reviewed_get_query_is_exact_not_a_general_query_channel(target):
    url = "https://api.smartrecruiters.com/metadata?company=Fixture&locale=en"
    fence = OneClickNetworkBoundary(target, read_urls=(url,), resume_filename="CV.pdf")
    assert fence.allows("GET", url)
    assert not fence.allows("GET", url + "&email=PRIVATE")
    assert not fence.allows("GET", url.replace("Fixture", "Other"))
    assert not fence.allows("POST", url)


def test_bad_input_never_echoed_or_used_as_authority(boundary):
    assert not boundary.allows(None, "PRIVATE")
    assert not boundary.allows("POST", None)
    assert not boundary.allows("POST", "PRIVATE", is_navigation="false")
    assert "PRIVATE" not in repr(boundary.__dict__)
    assert boundary.blocked_write_count == 3


@pytest.mark.parametrize("enabled,reviewed", [(False, False), (False, True), (True, False), (True, True)])
def test_native_widget_navigation_needs_both_explicit_preference_and_exact_public_read(target, enabled, reviewed):
    url = "https://www.linkedin.com/talentwidgets/apply-with-linkedin"
    fence = OneClickNetworkBoundary(target, read_urls=(url,) if reviewed else (), resume_filename="CV.pdf",
                                   reviewed_native_entry=enabled)
    assert fence.allows("GET", url, "document", True) is (enabled and reviewed)
    assert not fence.allows("HEAD", url, "document", True)
    for wrong in (url + "?scope=profile", url.replace("/talentwidgets/", "/oauth/"), url.replace(".com/", ".com:443/")):
        assert not fence.allows("GET", wrong, "document", True)
    assert not fence.allows("POST", url)
    assert fence.application_requests_allowed == 0
