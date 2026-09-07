from __future__ import annotations

from dataclasses import asdict
from enum import Enum
import json
from pathlib import Path
import shutil

from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.file_upload import ApprovedFileAsset, ArtifactType, FileUploadExecutionRequest, FileUploadPlan
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.file_assets import sha256_file
from ejs.services.file_uploader import PlaywrightFileUploader

RUN_ID = "FILE1-20260822-1033"
OBSERVED_AT = "2026-08-22T10:33:00+03:00"
CV_PATH = Path("/mnt/data/Cagatay_Buyuk_Base_Transformation_BA.pdf")
FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "browser" / "file_upload_form.html"
REPORT = Path(__file__).resolve().parent / "file1_upload_runtime_report_2026-08-22.json"


def jsonable(v):
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return v


def main() -> int:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or ""
    if not chromium:
        raise SystemExit("Chromium unavailable")
    if not CV_PATH.is_file():
        raise SystemExit(f"Approved CV asset unavailable: {CV_PATH}")

    config = BrowserRuntimeConfig(executable_path=chromium, settle_timeout_ms=0)
    url = FIXTURE.resolve().as_uri()
    inspection = PlaywrightBrowserWorker(config).inspect(
        BrowserInspectionRequest(
            bridge_request_id="bridge:file1:approved-cv:001",
            route_key="route:file1:local-approved-cv",
            opportunity_key="fixture:file1:approved-cv",
            requisition_id="file1-approved-cv-001",
            application_url=url,
            adapter_key="form:employer-custom",
            observed_at=OBSERVED_AT,
        )
    )

    digest = sha256_file(CV_PATH)
    asset = ApprovedFileAsset(
        artifact_type=ArtifactType.CV,
        artifact_version="1.0",
        source_ref="CV Base Library:Base - Transformation & BA",
        asset_ref="drive:1WhIZ49dkdsL9vYVuZE6cv6gz70P5gufa",
        local_path=str(CV_PATH),
        expected_file_name="Cagatay_Buyuk_Base_Transformation_BA.pdf",
        expected_mime_type="application/pdf",
        expected_sha256=digest,
        expected_size_bytes=CV_PATH.stat().st_size,
    )
    plan = FileUploadPlan(
        upload_plan_key="file1:cv:transformation-ba",
        control_key="input:cv",
        canonical_field="document.cv",
        asset=asset,
        provenance_ref="CV Base Library!Base - Transformation & BA / Ready PDF",
    )
    request = FileUploadExecutionRequest(
        execution_id="exec:file1:local-approved-cv:1",
        bridge_request_id="bridge:file1:approved-cv:001",
        route_key="route:file1:local-approved-cv",
        opportunity_key="fixture:file1:approved-cv",
        requisition_id="file1-approved-cv-001",
        application_url=url,
        adapter_key="form:employer-custom",
        observed_at=OBSERVED_AT,
        expected_form_fingerprint=inspection.form_fingerprint,
        application_status="To Apply",
        readiness_confidence="High",
        unresolved_required=0,
        upload_plan=(plan,),
    )
    repo = InMemoryTmhRepository()
    uploader = PlaywrightFileUploader(config, artifact_repository=repo)
    first = uploader.execute(request)
    retry = uploader.execute(request)

    report = {
        "run_id": RUN_ID,
        "observed_at": OBSERVED_AT,
        "service_version": "0.10.0a1",
        "contract": "FILE-UPLOAD-0.1",
        "asset": {
            "base_cv": "Base - Transformation & BA",
            "drive_file_id": "1WhIZ49dkdsL9vYVuZE6cv6gz70P5gufa",
            "file_name": CV_PATH.name,
            "mime_type": "application/pdf",
            "size_bytes": CV_PATH.stat().st_size,
            "sha256": digest,
            "cv_library_version": "1.0",
            "upload_asset_status": "Ready PDF",
        },
        "inspection": {
            "runtime_state": inspection.runtime_state.value,
            "form_fingerprint": inspection.form_fingerprint,
            "file_controls": [c.control_key for c in inspection.controls if c.control_type == "file"],
        },
        "first": jsonable(asdict(first)),
        "retry": jsonable(asdict(retry)),
        "artifact_repository_count": len(repo.artifacts),
        "production_employer_uploads": 0,
        "production_submission_artifact_rows": 0,
        "applications_mutations": 0,
        "submit_clicks": 0,
        "passed": (
            first.review_gate_reached
            and first.exact_readback_all_match
            and first.file_upload_attempts == 1
            and first.submit_attempts == 0
            and first.form_value_write_attempts == 0
            and retry.item_results
            and retry.item_results[0].artifact_duplicate_suppressed
            and len(repo.artifacts) == 1
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
