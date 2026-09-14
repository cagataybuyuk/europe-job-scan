from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import hashlib
import shutil
import tempfile
import unittest

from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.file_upload import ApprovedFileAsset, ArtifactType, FileUploadPlan
from ejs.contracts.prefill import FieldOwnership, MappingConfidence as PrefillConfidence, PrefillFieldPlan, ResolverStatus
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionAuthority, SmartRecruitersExecutionRequest
from ejs.contracts.submit import RolloutStage, submit_identity
from ejs.domain.smartrecruiters_execution import SmartRecruitersExecutionState
from ejs.domain.submission import ControlResolution, ResolutionMode, ValidationDecision
from ejs.domain.template_history import MappingConfidence
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.smartrecruiters_executor import PlaywrightSmartRecruitersExecutor

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "browser"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


def url(name="smartrecruiters_e2e_form.html") -> str:
    return (FIX / name).resolve().as_uri()


def fplan(key, control, canonical, ctype, value, *, ownership=FieldOwnership.AUTO_SAFE, required=True):
    return PrefillFieldPlan(
        field_plan_key=key,
        control_key=control,
        canonical_field=canonical,
        control_type=ctype,
        value=value,
        provenance_ref=f"fact:{canonical}:v1",
        ownership=ownership,
        resolver_status=ResolverStatus.RESOLVED,
        mapping_confidence=PrefillConfidence.HIGH,
        review_required=False,
        required=required,
    )


def cv_plan() -> FileUploadPlan:
    p = FIX / "test_cv.pdf"
    asset = ApprovedFileAsset(
        artifact_type=ArtifactType.CV,
        artifact_version="test-v1",
        source_ref="fixture:test-cv",
        asset_ref="fixture:test-cv",
        local_path=str(p),
        expected_file_name=p.name,
        expected_mime_type="application/pdf",
        expected_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        expected_size_bytes=p.stat().st_size,
    )
    return FileUploadPlan(
        upload_plan_key="upload:cv",
        control_key="input:cv",
        canonical_field="document.cv",
        asset=asset,
        provenance_ref="fixture:test-cv",
        mapping_confidence=PrefillConfidence.HIGH,
        required=True,
    )


def resolutions() -> tuple[ControlResolution, ...]:
    def fact(control, label, canonical):
        return ControlResolution(
            control_key=control, label=label, required=True, canonical_field=canonical,
            mapping_confidence=MappingConfidence.HIGH,
            resolution_mode=ResolutionMode.VERIFIED_FACT,
            provenance_ref=f"fact:{canonical}:v1",
        )
    return (
        fact("input:firstName", "First name", "candidate.first_name"),
        fact("input:lastName", "Last name", "candidate.last_name"),
        fact("input:email", "Email", "candidate.email"),
        fact("input:phone", "Phone", "candidate.phone"),
        fact("select:noticePeriod", "Notice period", "employment.notice_period"),
        ControlResolution(
            control_key="textarea:motivation", label="Motivation", required=True,
            canonical_field="application.motivation_text", mapping_confidence=MappingConfidence.HIGH,
            resolution_mode=ResolutionMode.GROUNDED_GENERATED, provenance_ref="answer:motivation:v1", grounded=True,
        ),
        ControlResolution(
            control_key="input:cv", label="CV", required=True,
            canonical_field="document.cv", mapping_confidence=MappingConfidence.HIGH,
            resolution_mode=ResolutionMode.ARTIFACT, artifact_ref="artifact:cv:test-v1",
        ),
    )


@unittest.skipUnless(CHROMIUM, "Chromium not installed")
class SmartRecruitersE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = BrowserRuntimeConfig(executable_path=CHROMIUM or "", navigation_timeout_ms=10_000, settle_timeout_ms=25)
        cls.inspector = PlaywrightBrowserWorker(cls.config)
        inspect = cls.inspector.inspect(BrowserInspectionRequest(
            bridge_request_id="bridge:sr:e2e:inspect",
            route_key="route:sr:e2e",
            opportunity_key="fixture:sr:e2e",
            requisition_id="sr-e2e-001",
            application_url=url(),
            adapter_key="ats:smartrecruiters",
            observed_at="2026-08-22T11:05:00+03:00",
        ))
        cls.fp = inspect.form_fingerprint

    def setUp(self):
        self.repo = InMemoryTmhRepository()
        self.executor = PlaywrightSmartRecruitersExecutor(self.config, artifact_repository=self.repo)

    def request(self, **kwargs):
        base = SmartRecruitersExecutionRequest(
            execution_id="exec:sr:e2e:001",
            bridge_request_id="bridge:sr:e2e:001",
            route_key="route:sr:e2e",
            opportunity_key="fixture:sr:e2e",
            requisition_id="sr-e2e-001",
            application_url=url(),
            observed_at="2026-08-22T11:05:00+03:00",
            expected_form_fingerprint=self.fp,
            candidate_profile_version="candidate-profile-v1",
            application_status="To Apply",
            readiness_confidence="High",
            unresolved_required=0,
            field_plan=(
                fplan("first", "input:firstName", "candidate.first_name", "text", "Test"),
                fplan("last", "input:lastName", "candidate.last_name", "text", "User"),
                fplan("email", "input:email", "candidate.email", "email", "test@example.com"),
                fplan("phone", "input:phone", "candidate.phone", "tel", "+90 555 000 0000"),
                fplan("linkedin", "input:linkedin", "candidate.linkedin_url", "url", "https://linkedin.com/in/test", required=False),
                fplan("notice", "select:noticePeriod", "employment.notice_period", "select", "4 weeks"),
                fplan("mot", "textarea:motivation", "application.motivation_text", "textarea", "Evidence-grounded motivation.", ownership=FieldOwnership.DYNAMIC_AI),
            ),
            upload_plan=(cv_plan(),),
            control_resolutions=resolutions(),
            rollout_stage=RolloutStage.R1_PREFILL,
        )
        return replace(base, **kwargs)

    def test_single_session_reaches_pre_submit_ready_without_submit(self):
        r = self.executor.execute(self.request())
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PRE_SUBMIT_READY)
        self.assertTrue(r.review_gate_reached)
        self.assertTrue(r.browser_form_valid)
        self.assertTrue(r.submit_control_observed)
        self.assertEqual(r.form_value_write_attempts, 7)
        self.assertEqual(r.file_upload_attempts, 1)
        self.assertEqual(r.submit_attempts, 0)
        self.assertTrue(r.consequential_boundary_ok)
        self.assertEqual(r.observed_form_fingerprint, r.final_form_fingerprint)
        self.assertIsNotNone(r.validation)
        self.assertEqual(r.validation.decision, ValidationDecision.RUNTIME_DISABLED)
        self.assertTrue(r.validation.policy_eligible)
        self.assertFalse(r.validation.execution_allowed)
        self.assertEqual(r.required_controls_observed, 7)
        self.assertEqual(r.resolved_required_controls, 7)

    def test_unsupported_form_structures_stop_before_any_write_or_upload(self):
        cases = (
            ('<div id="host"></div><script>document.getElementById("host").attachShadow({mode:"open"}).innerHTML = \'<input id="firstName">\';</script>', "SHADOW_DOM_ADAPTER_REQUIRED"),
            ('<input id="cv" type="file"><input id="cv" type="file">', "AMBIGUOUS_CONTROL_LOCATOR"),
            ('<input id="firstName"><button>Next</button>', "MULTISTEP_ADAPTER_REQUIRED"),
            ('<h1>Loading application</h1>', "FORM_CONTROLS_NOT_READY"),
        )
        for html, expected_error in cases:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "unsupported.html"
                target.write_text(html, encoding="utf-8")
                r = self.executor.execute(self.request(application_url=target.as_uri()))
                self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.BOUNDARY)
                self.assertEqual(r.error_code, expected_error)
                self.assertEqual(r.form_value_write_attempts, 0)
                self.assertEqual(r.file_upload_attempts, 0)
                self.assertEqual(r.submit_attempts, 0)
                self.assertFalse(r.review_gate_reached)

    def test_all_field_readbacks_are_verified(self):
        r = self.executor.execute(self.request())
        self.assertTrue(all(x.value_hash == x.readback_hash for x in r.field_results))
        self.assertTrue(all(x.status.value in {"written_verified", "already_matched"} for x in r.field_results))

    def test_cv_browser_hash_readback_is_exact(self):
        r = self.executor.execute(self.request())
        self.assertEqual(len(r.file_results), 1)
        f = r.file_results[0]
        self.assertEqual(f.content_hash, f.readback_sha256)
        self.assertEqual(f.expected_file_name, f.readback_file_name)
        self.assertEqual(f.expected_size_bytes, f.readback_size_bytes)
        self.assertEqual(f.expected_mime_type, f.readback_mime_type)

    def test_artifact_lineage_is_idempotent_across_exact_retry(self):
        req = self.request()
        a = self.executor.execute(req)
        b = self.executor.execute(req)
        self.assertTrue(a.file_results[0].artifact_appended)
        self.assertFalse(b.file_results[0].artifact_appended)
        self.assertTrue(b.file_results[0].artifact_duplicate_suppressed)
        self.assertEqual(len(self.repo.artifacts), 1)

    def test_stale_fingerprint_stops_before_write_or_upload(self):
        r = self.executor.execute(self.request(expected_form_fingerprint="stale"))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.SCHEMA_DRIFT)
        self.assertEqual(r.form_value_write_attempts, 0)
        self.assertEqual(r.file_upload_attempts, 0)

    def test_missing_required_resolution_stops_before_write(self):
        res = tuple(x for x in resolutions() if x.control_key != "input:email")
        r = self.executor.execute(self.request(control_resolutions=res))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PLAN_BLOCKED)
        self.assertEqual(r.form_value_write_attempts, 0)
        self.assertEqual(r.file_upload_attempts, 0)
        self.assertIn("input:email", r.error_message)

    def test_forbidden_salary_in_field_plan_blocks_before_browser_mutation(self):
        bad = self.request().field_plan + (
            fplan("salary", "input:firstName", "compensation.salary_expectation", "text", "70000", ownership=FieldOwnership.REVIEW_GATED),
        )
        r = self.executor.execute(self.request(field_plan=bad))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PLAN_BLOCKED)
        self.assertEqual(r.form_value_write_attempts, 0)
        self.assertEqual(r.file_upload_attempts, 0)

    def test_duplicate_submit_identity_blocks_validator_but_never_clicks(self):
        key = submit_identity("fixture:sr:e2e", "sr-e2e-001", "candidate-profile-v1")
        r = self.executor.execute(self.request(existing_submit_keys=frozenset({key})))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.VALIDATOR_BLOCKED)
        self.assertEqual(r.validation.decision, ValidationDecision.DUPLICATE_SUPPRESSED)
        self.assertEqual(r.submit_attempts, 0)

    def test_stale_tracker_fingerprint_blocks_validator(self):
        r = self.executor.execute(self.request(tracker_fingerprint_current=False))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.VALIDATOR_BLOCKED)
        self.assertIn("tracker/opportunity fingerprint is stale", r.validation.blockers)

    def test_rollout_r2_does_not_enable_click_inside_prepare_executor(self):
        r = self.executor.execute(self.request(rollout_stage=RolloutStage.R2_SINGLE_CANARY))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PRE_SUBMIT_READY)
        self.assertTrue(r.validation.policy_eligible)
        self.assertFalse(r.validation.execution_allowed)
        self.assertEqual(r.submit_attempts, 0)

    def test_authority_rejects_final_submit(self):
        with self.assertRaises(PermissionError):
            self.executor.execute(self.request(), authority=SmartRecruitersExecutionAuthority(final_submit=True))

    def test_authority_rejects_consent_action(self):
        with self.assertRaises(PermissionError):
            self.executor.execute(self.request(), authority=SmartRecruitersExecutionAuthority(consent_action=True))

    def test_result_does_not_contain_raw_field_values_or_local_asset_path(self):
        req = self.request()
        r = self.executor.execute(req)
        rendered = repr(r)
        self.assertNotIn("test@example.com", rendered)
        self.assertNotIn("Evidence-grounded motivation.", rendered)
        self.assertNotIn(req.upload_plan[0].asset.local_path, rendered)

    def test_asset_manifest_failure_stops_before_browser_mutation(self):
        p = cv_plan()
        bad_asset = replace(p.asset, expected_sha256="0" * 64)
        bad_plan = replace(p, asset=bad_asset)
        r = self.executor.execute(self.request(upload_plan=(bad_plan,)))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PLAN_BLOCKED)
        self.assertEqual(r.form_value_write_attempts, 0)
        self.assertEqual(r.file_upload_attempts, 0)

    def test_character_limit_failure_stops_before_any_write(self):
        plans = tuple(replace(x, value="X" * 301) if x.field_plan_key == "mot" else x for x in self.request().field_plan)
        r = self.executor.execute(self.request(field_plan=plans))
        self.assertEqual(r.runtime_state, SmartRecruitersExecutionState.PLAN_BLOCKED)
        self.assertEqual(r.form_value_write_attempts, 0)
        self.assertEqual(r.file_upload_attempts, 0)

    def test_submit_control_is_metadata_only(self):
        r = self.executor.execute(self.request())
        self.assertTrue(r.submit_control_observed)
        self.assertEqual(r.submit_attempts, 0)


if __name__ == "__main__":
    unittest.main()
