from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import unittest

from ejs.contracts.browser import BrowserInspectionRequest, BrowserWorkerAuthority
from ejs.contracts.prefill import (
    FieldOwnership,
    MappingConfidence,
    PrefillExecutionRequest,
    PrefillFieldPlan,
    ResolverStatus,
    SafeFieldWriterAuthority,
)
from ejs.domain.prefill import FieldWriteStatus, PrefillExecutionState
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.prefill_writer import field_plan_blocker
from ejs.services.safe_field_writer import PlaywrightSafeFieldWriter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "browser"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


def file_url(name: str) -> str:
    return (FIX / name).resolve().as_uri()


def plan(
    key: str,
    control: str,
    canonical: str,
    ctype: str,
    value: str,
    *,
    ownership: FieldOwnership = FieldOwnership.AUTO_SAFE,
    status: ResolverStatus = ResolverStatus.RESOLVED,
    confidence: MappingConfidence = MappingConfidence.HIGH,
    review: bool = False,
    required: bool = False,
) -> PrefillFieldPlan:
    return PrefillFieldPlan(
        field_plan_key=key,
        control_key=control,
        canonical_field=canonical,
        control_type=ctype,
        value=value,
        provenance_ref=f"fact:{canonical}:v1",
        ownership=ownership,
        resolver_status=status,
        mapping_confidence=confidence,
        review_required=review,
        required=required,
    )


@unittest.skipUnless(CHROMIUM, "Chromium not installed")
class SafeFieldWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = BrowserRuntimeConfig(
            executable_path=CHROMIUM or "",
            navigation_timeout_ms=10_000,
            settle_timeout_ms=25,
        )
        cls.inspector = PlaywrightBrowserWorker(cls.config)
        cls.writer = PlaywrightSafeFieldWriter(cls.config)
        cls.url = file_url("safe_prefill_form.html")
        inspection = cls.inspector.inspect(
            BrowserInspectionRequest(
                bridge_request_id="bridge:be2:inspect:001",
                route_key="route:be2:fixture",
                opportunity_key="fixture:be2-business-analyst",
                requisition_id="fixture-be2-001",
                application_url=cls.url,
                adapter_key="ats:smartrecruiters",
                observed_at="2026-08-21T22:05:00+03:00",
            ),
            authority=BrowserWorkerAuthority(),
        )
        cls.fp = inspection.form_fingerprint

    def request(self, plans: tuple[PrefillFieldPlan, ...], **kwargs) -> PrefillExecutionRequest:
        base = PrefillExecutionRequest(
            execution_id="exec:be2:fixture:001",
            bridge_request_id="bridge:be2:write:001",
            route_key="route:be2:fixture",
            opportunity_key="fixture:be2-business-analyst",
            requisition_id="fixture-be2-001",
            application_url=self.url,
            adapter_key="ats:smartrecruiters",
            observed_at="2026-08-21T22:05:00+03:00",
            expected_form_fingerprint=self.fp,
            application_status="To Apply",
            readiness_confidence="High",
            unresolved_required=0,
            field_plan=plans,
        )
        return replace(base, **kwargs)

    def test_safe_identity_contact_fields_are_written_and_read_back(self):
        plans = (
            plan("p1", "input:firstName", "candidate.first_name", "text", "Test", required=True),
            plan("p2", "input:lastName", "candidate.last_name", "text", "User", required=True),
            plan("p3", "input:email", "candidate.email", "email", "test@example.com", required=True),
            plan("p4", "input:phone", "candidate.phone", "tel", "+90 555 000 0000"),
        )
        result = self.writer.execute(self.request(plans), authority=SafeFieldWriterAuthority())
        self.assertEqual(result.runtime_state, PrefillExecutionState.REVIEW_GATE)
        self.assertEqual(result.verified_fields, 4)
        self.assertEqual(result.form_value_write_attempts, 4)
        self.assertTrue(result.readback_all_match)
        self.assertTrue(result.review_gate_reached)
        self.assertEqual(result.submit_attempts, 0)
        self.assertEqual(result.file_upload_attempts, 0)
        self.assertTrue(result.consequential_boundary_ok)
        self.assertTrue(all(x.status is FieldWriteStatus.WRITTEN_VERIFIED for x in result.field_results))

    def test_exact_select_option_is_supported_for_safe_notice_period(self):
        p = plan("np", "select:noticePeriod", "employment.notice_period", "select", "4 weeks", required=True)
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.REVIEW_GATE)
        self.assertEqual(result.verified_fields, 1)
        self.assertEqual(result.field_results[0].status, FieldWriteStatus.WRITTEN_VERIFIED)

    def test_dynamic_ai_text_requires_explicit_dynamic_ai_ownership(self):
        p = plan(
            "mot",
            "textarea:motivation",
            "application.motivation_text",
            "textarea",
            "Evidence-grounded motivation.",
            ownership=FieldOwnership.DYNAMIC_AI,
        )
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.REVIEW_GATE)
        self.assertEqual(result.verified_fields, 1)

    def test_salary_is_blocked_before_any_write(self):
        safe = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        salary = plan(
            "salary", "input:salary", "compensation.salary_expectation", "text", "70000",
            ownership=FieldOwnership.REVIEW_GATED,
        )
        result = self.writer.execute(self.request((safe, salary)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertIn("forbidden canonical field", result.error_message)

    def test_privacy_checkbox_is_blocked_before_any_write(self):
        privacy = plan(
            "privacy", "input:privacy", "consent.privacy_acknowledgement", "checkbox", "true",
            ownership=FieldOwnership.USER_ONLY,
        )
        result = self.writer.execute(self.request((privacy,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertEqual(result.submit_attempts, 0)

    def test_file_upload_is_blocked_before_any_write(self):
        cv = plan(
            "cv", "input:cv", "document.cv", "file", "/tmp/cv.pdf",
            ownership=FieldOwnership.USER_ONLY,
        )
        result = self.writer.execute(self.request((cv,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.file_upload_attempts, 0)

    def test_work_authorization_is_blocked(self):
        p = plan(
            "wa", "input:firstName", "work.authorization", "text", "Yes",
            ownership=FieldOwnership.USER_FACT,
        )
        self.assertIn("forbidden canonical field", field_plan_blocker(p))

    def test_protected_demographic_is_blocked(self):
        p = plan(
            "gender", "select:gender", "demographic.gender", "select", "Prefer not to say",
            ownership=FieldOwnership.USER_ONLY,
        )
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.form_value_write_attempts, 0)


    def test_observed_character_limit_blocks_before_write(self):
        p = plan(
            "mot", "textarea:motivation", "application.motivation_text", "textarea", "X" * 201,
            ownership=FieldOwnership.DYNAMIC_AI,
        )
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertIn("maxlength 200", result.error_message)

    def test_medium_mapping_confidence_is_blocked(self):
        p = plan(
            "first", "input:firstName", "candidate.first_name", "text", "Test",
            confidence=MappingConfidence.MEDIUM,
        )
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertIn("mapping confidence", result.error_message)

    def test_review_required_is_blocked(self):
        p = plan(
            "first", "input:firstName", "candidate.first_name", "text", "Test", review=True,
        )
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)

    def test_stale_fingerprint_causes_zero_write_schema_drift(self):
        p = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        result = self.writer.execute(self.request((p,), expected_form_fingerprint="stale-fingerprint"))
        self.assertEqual(result.runtime_state, PrefillExecutionState.SCHEMA_DRIFT)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertEqual(result.submit_attempts, 0)

    def test_unknown_control_blocks_entire_plan_before_write(self):
        safe = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        unknown = plan("missing", "input:notThere", "candidate.last_name", "text", "User")
        result = self.writer.execute(self.request((safe, unknown)))
        self.assertEqual(result.runtime_state, PrefillExecutionState.PLAN_BLOCKED)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertIn("control not found", result.error_message)

    def test_exact_preexisting_values_are_idempotent_noops(self):
        url = file_url("safe_prefill_prepopulated.html")
        inspection = self.inspector.inspect(
            BrowserInspectionRequest(
                bridge_request_id="bridge:be2:inspect:pre",
                route_key="route:be2:pre",
                opportunity_key="fixture:pre",
                requisition_id="pre-001",
                application_url=url,
                adapter_key="form:employer-custom",
                observed_at="2026-08-21T22:05:00+03:00",
            )
        )
        plans = (
            plan("p1", "input:firstName", "candidate.first_name", "text", "Test"),
            plan("p2", "input:email", "candidate.email", "email", "test@example.com"),
        )
        req = self.request(
            plans,
            execution_id="exec:be2:pre:001",
            route_key="route:be2:pre",
            opportunity_key="fixture:pre",
            requisition_id="pre-001",
            application_url=url,
            adapter_key="form:employer-custom",
            expected_form_fingerprint=inspection.form_fingerprint,
        )
        result = self.writer.execute(req)
        self.assertEqual(result.runtime_state, PrefillExecutionState.REVIEW_GATE)
        self.assertEqual(result.form_value_write_attempts, 0)
        self.assertEqual(result.already_matched_fields, 2)
        self.assertTrue(all(x.status is FieldWriteStatus.ALREADY_MATCHED for x in result.field_results))

    def test_result_never_contains_raw_values(self):
        raw = "secret-looking@example.com"
        p = plan("email", "input:email", "candidate.email", "email", raw)
        result = self.writer.execute(self.request((p,)))
        self.assertNotIn(raw, repr(result))
        self.assertEqual(result.field_results[0].value_hash, result.field_results[0].readback_hash)

    def test_form_fingerprint_remains_stable_after_value_writes(self):
        p = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        result = self.writer.execute(self.request((p,)))
        self.assertEqual(result.observed_form_fingerprint, result.final_form_fingerprint)
        self.assertEqual(result.expected_form_fingerprint, result.final_form_fingerprint)

    def test_submit_button_remains_uninvoked(self):
        plans = (
            plan("p1", "input:firstName", "candidate.first_name", "text", "Test"),
            plan("p2", "input:lastName", "candidate.last_name", "text", "User"),
        )
        result = self.writer.execute(self.request(plans))
        self.assertEqual(result.submit_attempts, 0)
        self.assertTrue(result.review_gate_reached)

    def test_authority_cannot_enable_upload_or_submit(self):
        with self.assertRaises(PermissionError):
            SafeFieldWriterAuthority(file_upload=True).validate()
        with self.assertRaises(PermissionError):
            SafeFieldWriterAuthority(final_submit=True).validate()
        with self.assertRaises(PermissionError):
            SafeFieldWriterAuthority(consent_action=True).validate()

    def test_non_to_apply_request_is_rejected(self):
        p = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        with self.assertRaises(PermissionError):
            self.writer.execute(self.request((p,), application_status="Applied"))

    def test_non_high_readiness_is_rejected(self):
        p = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        with self.assertRaises(PermissionError):
            self.writer.execute(self.request((p,), readiness_confidence="Medium"))

    def test_unresolved_required_is_rejected(self):
        p = plan("first", "input:firstName", "candidate.first_name", "text", "Test")
        with self.assertRaises(PermissionError):
            self.writer.execute(self.request((p,), unresolved_required=1))


class SafeFieldPolicyTests(unittest.TestCase):
    def test_auto_safe_unknown_canonical_field_is_blocked(self):
        p = plan("x", "input:x", "candidate.random", "text", "x")
        self.assertIn("not in BE-2 allowlist", field_plan_blocker(p))

    def test_pending_user_is_blocked(self):
        p = plan(
            "x", "input:x", "candidate.first_name", "text", "x",
            status=ResolverStatus.PENDING_USER,
        )
        self.assertIn("resolver status", field_plan_blocker(p))
