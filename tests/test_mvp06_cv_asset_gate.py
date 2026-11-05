from ejs.services.cv_asset_gate import evaluate_cv_asset


def fixture():
    value = {"sha256": "a" * 64, "size_bytes": 1024,
             "mime_type": "application/pdf", "approved_filename": "cv.pdf"}
    return value, {**value, "user_approved": True, "scope": "one_cv_asset"}


def test_matching_metadata_never_grants_upload():
    a, p = fixture()
    result = evaluate_cv_asset(a, p)
    assert result["status"] == "METADATA_MATCH"
    assert result["upload_authorized"] is False
    assert result["submit_authorized"] is False


def test_changed_sha_denied():
    a, p = fixture()
    a["sha256"] = "b" * 64
    assert "ASSET_CHANGED" in evaluate_cv_asset(a, p)["reasons"]


def test_missing_approval_denied():
    a, p = fixture()
    p["user_approved"] = False
    assert "APPROVAL_REQUIRED" in evaluate_cv_asset(a, p)["reasons"]


def test_changed_size_denied():
    a, p = fixture()
    a["size_bytes"] = 2048
    assert "INVALID_OR_CHANGED_SIZE" in evaluate_cv_asset(a, p)["reasons"]


def test_unsupported_type_denied():
    a, p = fixture()
    a["mime_type"] = "application/msword"
    assert "UNSUPPORTED_TYPE" in evaluate_cv_asset(a, p)["reasons"]


def test_invalid_filename_denied():
    a, p = fixture()
    a["approved_filename"] = "../cv.pdf"
    assert "INVALID_OR_CHANGED_FILENAME" in evaluate_cv_asset(a, p)["reasons"]


def test_missing_and_boolean_size_denied():
    assert evaluate_cv_asset(None, None)["status"] == "BLOCKED"
    a, p = fixture()
    a["size_bytes"] = True
    assert evaluate_cv_asset(a, p)["status"] == "BLOCKED"


def test_same_size_different_digest_is_rejected():
    asset, approval = fixture()
    asset["sha256"] = "f" * 64
    result = evaluate_cv_asset(asset, approval)
    assert result["status"] == "BLOCKED"
    assert "ASSET_CHANGED" in result["reasons"]
    assert result["upload_authorized"] is False


def test_filename_case_and_extension_are_pinned_to_approval():
    asset, approval = fixture()
    asset["approved_filename"] = "CV.PDF"
    assert "INVALID_OR_CHANGED_FILENAME" in evaluate_cv_asset(asset, approval)["reasons"]
    asset["approved_filename"] = "cv.pdf.exe"
    approval["approved_filename"] = "cv.pdf.exe"
    assert "INVALID_OR_CHANGED_FILENAME" in evaluate_cv_asset(asset, approval)["reasons"]


def test_size_limit_and_missing_scope_fail_closed():
    asset, approval = fixture()
    asset["size_bytes"] = approval["size_bytes"] = 10 * 1024 * 1024 + 1
    assert evaluate_cv_asset(asset, approval)["status"] == "BLOCKED"
    asset, approval = fixture()
    approval["scope"] = "all_jobs"
    assert "APPROVAL_REQUIRED" in evaluate_cv_asset(asset, approval)["reasons"]


def test_metadata_gate_never_returns_private_metadata():
    asset, approval = fixture()
    result = evaluate_cv_asset(asset, approval)
    assert "sha256" not in result
    assert "approved_filename" not in result
    assert "file_read_performed" in result and result["file_read_performed"] is False
