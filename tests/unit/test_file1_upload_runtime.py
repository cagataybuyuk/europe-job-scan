from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.file_upload import (
    ApprovedFileAsset,
    ArtifactType,
    FileUploadAuthority,
    FileUploadExecutionRequest,
    FileUploadPlan,
)
from ejs.contracts.prefill import MappingConfidence
from ejs.domain.file_upload import FileUploadExecutionState, FileUploadStatus
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.file_assets import accept_allows, sha256_file, verify_asset
from ejs.services.file_uploader import PlaywrightFileUploader

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "browser"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome") or ""


def file_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def asset(path: Path | None = None, *, approved: bool = True, mime: str = "application/pdf", sha: str | None = None, size: int | None = None, name: str | None = None, artifact_type: ArtifactType = ArtifactType.CV) -> ApprovedFileAsset:
    path = path or (FIXTURES / "test_cv.pdf")
    return ApprovedFileAsset(
        artifact_type=artifact_type,
        artifact_version="fixture-v1",
        source_ref="fixture:approved-asset",
        asset_ref="fixture://test_cv.pdf",
        local_path=str(path),
        expected_file_name=name or path.name,
        expected_mime_type=mime,
        expected_sha256=sha or sha256_file(path),
        expected_size_bytes=size if size is not None else path.stat().st_size,
        approved=approved,
    )


def plan(asset_spec: ApprovedFileAsset | None = None, *, control: str = "input:cv", field: str = "document.cv", confidence: MappingConfidence = MappingConfidence.HIGH) -> FileUploadPlan:
    return FileUploadPlan(
        upload_plan_key="upload-plan:cv",
        control_key=control,
        canonical_field=field,
        asset=asset_spec or asset(),
        provenance_ref="CV Base Library:test",
        mapping_confidence=confidence,
    )


@unittest.skipUnless(CHROMIUM, "Chromium unavailable")
class FileUploadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = BrowserRuntimeConfig(executable_path=CHROMIUM, settle_timeout_ms=0)
        self.inspector = PlaywrightBrowserWorker(self.config)
        self.repo = InMemoryTmhRepository()
        self.uploader = PlaywrightFileUploader(self.config, artifact_repository=self.repo)

    def request(self, plans, *, fixture="file_upload_form.html", expected_fp=None, execution_id="exec:file1:test:1", application_status="To Apply", readiness="High", unresolved=0):
        url = file_url(fixture)
        inspection = self.inspector.inspect(BrowserInspectionRequest(
            bridge_request_id="bridge:file1:test",
            route_key="route:file1:test",
            opportunity_key="fixture:file1",
            requisition_id="file1-001",
            application_url=url,
            adapter_key="form:employer-custom",
            observed_at="2026-08-22T10:33:00+03:00",
        ))
        return FileUploadExecutionRequest(
            execution_id=execution_id,
            bridge_request_id="bridge:file1:test",
            route_key="route:file1:test",
            opportunity_key="fixture:file1",
            requisition_id="file1-001",
            application_url=url,
            adapter_key="form:employer-custom",
            observed_at="2026-08-22T10:33:00+03:00",
            expected_form_fingerprint=expected_fp or inspection.form_fingerprint,
            application_status=application_status,
            readiness_confidence=readiness,
            unresolved_required=unresolved,
            upload_plan=tuple(plans),
        )

    def test_pdf_upload_has_exact_browser_sha_readback(self):
        result = self.uploader.execute(self.request((plan(),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.REVIEW_GATE)
        self.assertEqual(result.file_upload_attempts, 1)
        self.assertEqual(result.verified_files, 1)
        item = result.item_results[0]
        self.assertEqual(item.status, FileUploadStatus.UPLOADED_VERIFIED)
        self.assertEqual(item.content_hash, item.readback_sha256)
        self.assertEqual(item.expected_file_name, item.readback_file_name)
        self.assertEqual(item.expected_size_bytes, item.readback_size_bytes)
        self.assertEqual(item.expected_mime_type, item.readback_mime_type)
        self.assertTrue(item.artifact_appended)
        self.assertEqual(result.submit_attempts, 0)
        self.assertEqual(result.form_value_write_attempts, 0)

    def test_form_fingerprint_stable_after_file_attachment(self):
        result = self.uploader.execute(self.request((plan(),)))
        self.assertEqual(result.observed_form_fingerprint, result.final_form_fingerprint)
        self.assertEqual(result.expected_form_fingerprint, result.final_form_fingerprint)

    def test_prepopulated_exact_file_is_zero_upload_noop(self):
        result = self.uploader.execute(self.request((plan(),), fixture="file_upload_prepopulated.html"))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.REVIEW_GATE)
        self.assertEqual(result.file_upload_attempts, 0)
        self.assertEqual(result.already_attached_files, 1)
        self.assertEqual(result.item_results[0].status, FileUploadStatus.ALREADY_ATTACHED)

    def test_artifact_lineage_exact_retry_is_duplicate_suppressed(self):
        req = self.request((plan(),), execution_id="exec:file1:retry:1")
        first = self.uploader.execute(req)
        second = self.uploader.execute(req)
        self.assertTrue(first.item_results[0].artifact_appended)
        self.assertFalse(second.item_results[0].artifact_appended)
        self.assertTrue(second.item_results[0].artifact_duplicate_suppressed)
        self.assertEqual(len(self.repo.artifacts), 1)

    def test_stale_fingerprint_blocks_before_upload(self):
        result = self.uploader.execute(self.request((plan(),), expected_fp="stale-fingerprint"))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.SCHEMA_DRIFT)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_wrong_accept_blocks_before_upload(self):
        result = self.uploader.execute(self.request((plan(),), fixture="file_upload_wrong_accept.html"))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)
        self.assertIn("file type not allowed", result.error_message)

    def test_unknown_control_blocks_before_upload(self):
        result = self.uploader.execute(self.request((plan(control="input:notThere"),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_non_file_control_blocks_before_upload(self):
        result = self.uploader.execute(self.request((plan(control="input:privacy"),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_cover_letter_pdf_is_supported_as_separate_document_field(self):
        a = asset(artifact_type=ArtifactType.COVER_LETTER)
        p = FileUploadPlan(
            upload_plan_key="upload-plan:cover",
            control_key="input:coverLetter",
            canonical_field="document.cover_letter",
            asset=a,
            provenance_ref="fixture:cover-letter",
        )
        result = self.uploader.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.REVIEW_GATE)
        self.assertEqual(result.verified_files, 1)

    def test_result_does_not_expose_local_asset_path(self):
        a = asset()
        result = self.uploader.execute(self.request((plan(a),)))
        self.assertNotIn(a.local_path, repr(result))

    def test_submit_and_form_value_write_remain_zero(self):
        result = self.uploader.execute(self.request((plan(),)))
        self.assertEqual(result.submit_attempts, 0)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertTrue(result.consequential_boundary_ok)

    def test_bad_hash_blocks_before_browser_upload(self):
        bad = asset(sha="0" * 64)
        result = self.uploader.execute(self.request((plan(bad),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)
        self.assertIn("hash", result.error_message)

    def test_bad_size_blocks_before_browser_upload(self):
        bad = asset(size=(FIXTURES / "test_cv.pdf").stat().st_size + 1)
        result = self.uploader.execute(self.request((plan(bad),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_missing_file_blocks_before_browser_upload(self):
        p = Path(tempfile.gettempdir()) / "definitely-missing-file1.pdf"
        bad = ApprovedFileAsset(
            artifact_type=ArtifactType.CV, artifact_version="1", source_ref="fixture", asset_ref="fixture", local_path=str(p),
            expected_file_name=p.name, expected_mime_type="application/pdf", expected_sha256="1" * 64, expected_size_bytes=1,
        )
        result = self.uploader.execute(self.request((plan(bad),)))
        self.assertEqual(result.runtime_state, FileUploadExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_non_high_mapping_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.uploader.execute(self.request((plan(confidence=MappingConfidence.MEDIUM),)))

    def test_non_to_apply_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.uploader.execute(self.request((plan(),), application_status="Applied"))

    def test_non_high_readiness_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.uploader.execute(self.request((plan(),), readiness="Medium"))

    def test_unresolved_required_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.uploader.execute(self.request((plan(),), unresolved=1))


class FileAssetPolicyTests(unittest.TestCase):
    def test_accept_parser(self):
        self.assertTrue(accept_allows(".pdf,application/pdf", file_name="cv.pdf", mime_type="application/pdf"))
        self.assertTrue(accept_allows("application/*", file_name="cv.pdf", mime_type="application/pdf"))
        self.assertFalse(accept_allows("image/png", file_name="cv.pdf", mime_type="application/pdf"))
        self.assertTrue(accept_allows("", file_name="cv.pdf", mime_type="application/pdf"))

    def test_pdf_magic_is_verified(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fake.pdf"
            p.write_bytes(b"not-a-pdf")
            spec = ApprovedFileAsset(
                artifact_type=ArtifactType.CV, artifact_version="1", source_ref="fixture", asset_ref="fixture", local_path=str(p),
                expected_file_name=p.name, expected_mime_type="application/pdf", expected_sha256=hashlib.sha256(p.read_bytes()).hexdigest(), expected_size_bytes=p.stat().st_size,
            )
            with self.assertRaises(ValueError):
                verify_asset(spec)

    def test_unapproved_asset_is_rejected(self):
        with self.assertRaises(PermissionError):
            asset(approved=False).validate()

    def test_non_pdf_mime_is_rejected_in_file1(self):
        with self.assertRaises(PermissionError):
            asset(mime="application/msword").validate()

    def test_wrong_document_type_pairing_is_rejected(self):
        with self.assertRaises(ValueError):
            FileUploadPlan(
                upload_plan_key="x", control_key="input:cv", canonical_field="document.cover_letter",
                asset=asset(artifact_type=ArtifactType.CV), provenance_ref="fixture"
            ).validate()

    def test_authority_cannot_enable_submit_or_form_write(self):
        with self.assertRaises(PermissionError):
            FileUploadAuthority(final_submit=True).validate()
        with self.assertRaises(PermissionError):
            FileUploadAuthority(form_value_write=True).validate()
        with self.assertRaises(PermissionError):
            FileUploadAuthority(consent_action=True).validate()


if __name__ == "__main__":
    unittest.main()
