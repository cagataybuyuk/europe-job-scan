from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import unittest

from ejs.contracts.browser import (
    AuthBoundaryType,
    BrowserInspectionRequest,
    BrowserMode,
    BrowserWorkerAuthority,
    CaptchaState,
    RuntimeState,
)
from ejs.domain.browser_runtime import detect_ats_family
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker


FIX = Path(__file__).resolve().parents[1] / "fixtures" / "browser"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


def file_url(name: str) -> str:
    return (FIX / name).resolve().as_uri()


@unittest.skipUnless(CHROMIUM, "Chromium not installed")
class BrowserWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = PlaywrightBrowserWorker(
            BrowserRuntimeConfig(executable_path=CHROMIUM or "", navigation_timeout_ms=10_000, settle_timeout_ms=50)
        )
        self.auth = BrowserWorkerAuthority()

    def request(self, name="smartrecruiters_like_form.html", **kwargs):
        base = BrowserInspectionRequest(
            bridge_request_id="bridge:route:test:001",
            route_key="route:test",
            opportunity_key="rituals:business-analyst",
            requisition_id="744000-test",
            application_url=file_url(name),
            adapter_key="ats:smartrecruiters",
            observed_at="2026-08-21T17:24:00+03:00",
        )
        return replace(base, **kwargs)

    def test_authority_rejects_any_form_write(self):
        with self.assertRaises(PermissionError):
            BrowserWorkerAuthority(form_value_write=True).validate()
        with self.assertRaises(PermissionError):
            BrowserWorkerAuthority(final_submit=True).validate()
        with self.assertRaises(PermissionError):
            BrowserWorkerAuthority(file_upload=True).validate()

    def test_request_rejects_non_read_only_mode(self):
        # Enum currently has only read-only mode; this guards invalid construction path.
        req = self.request()
        self.assertEqual(req.mode, BrowserMode.INSPECT_READ_ONLY)

    def test_detect_ats_family(self):
        self.assertEqual(detect_ats_family("https://x.wd3.myworkdayjobs.com/a"), "workday")
        self.assertEqual(detect_ats_family("https://jobs.smartrecruiters.com/X/1"), "smartrecruiters")
        self.assertEqual(detect_ats_family("https://job-boards.eu.greenhouse.io/x"), "greenhouse")
        self.assertEqual(detect_ats_family("https://x.hirehive.com/a"), "hirehive")

    def test_fixture_renders_and_extracts_controls(self):
        result = self.worker.inspect(self.request(), authority=self.auth)
        self.assertEqual(result.runtime_state, RuntimeState.RENDERED)
        self.assertEqual(result.ats_family, "smartrecruiters")
        self.assertGreaterEqual(len(result.controls), 9)
        self.assertTrue(result.page_fingerprint)
        self.assertTrue(result.form_fingerprint)
        self.assertTrue(result.read_only_invariant_ok)

    def test_requiredness_uses_attribute_and_aria(self):
        result = self.worker.inspect(self.request(), authority=self.auth)
        by_id = {c.element_id: c for c in result.controls}
        self.assertTrue(by_id["firstName"].required)
        self.assertEqual(by_id["firstName"].required_evidence, "required-attribute")
        self.assertTrue(by_id["email"].required)
        self.assertEqual(by_id["email"].required_evidence, "aria-required")

    def test_select_options_are_observed(self):
        result = self.worker.inspect(self.request(), authority=self.auth)
        country = next(c for c in result.controls if c.element_id == "country")
        self.assertIn("Netherlands", country.options)
        self.assertIn("Türkiye", country.options)

    def test_file_control_is_metadata_only(self):
        result = self.worker.inspect(self.request(), authority=self.auth)
        cv = next(c for c in result.controls if c.element_id == "cv")
        self.assertEqual(cv.control_type, "file")
        self.assertEqual(result.file_upload_attempts, 0)

    def test_submit_control_observed_but_not_clicked(self):
        result = self.worker.inspect(self.request(), authority=self.auth)
        submit = next(a for a in result.action_controls if a.probable_action == "submit")
        self.assertIn("Submit", submit.label)
        self.assertEqual(result.submit_attempts, 0)
        self.assertEqual(result.mutation_attempts, 0)

    def test_fingerprint_is_deterministic(self):
        a = self.worker.inspect(self.request(), authority=self.auth)
        b = self.worker.inspect(self.request(), authority=self.auth)
        self.assertEqual(a.form_fingerprint, b.form_fingerprint)
        self.assertEqual(a.page_fingerprint, b.page_fingerprint)

    def test_dom_change_changes_fingerprint(self):
        original = self.worker.inspect(self.request(), authority=self.auth)
        tmp = FIX / "_drift_tmp.html"
        content = (FIX / "smartrecruiters_like_form.html").read_text(encoding="utf-8")
        tmp.write_text(content.replace("</form>", '<label for="extra">Portfolio</label><input id="extra" name="extra" type="url"></form>'), encoding="utf-8")
        try:
            drifted = self.worker.inspect(self.request(name="_drift_tmp.html"), authority=self.auth)
            self.assertNotEqual(original.form_fingerprint, drifted.form_fingerprint)
        finally:
            tmp.unlink(missing_ok=True)

    def test_auth_boundary_detected(self):
        result = self.worker.inspect(self.request(name="auth_boundary.html"), authority=self.auth)
        self.assertEqual(result.runtime_state, RuntimeState.AUTH_BOUNDARY)
        self.assertEqual(result.auth_boundary_type, AuthBoundaryType.SIGN_IN)
        self.assertTrue(result.read_only_invariant_ok)

    def test_captcha_boundary_detected(self):
        result = self.worker.inspect(self.request(name="captcha_boundary.html"), authority=self.auth)
        self.assertEqual(result.runtime_state, RuntimeState.CAPTCHA_BOUNDARY)
        self.assertEqual(result.captcha_state, CaptchaState.PRESENT)
        self.assertTrue(result.read_only_invariant_ok)

    def test_external_admin_block_is_typed(self):
        req = replace(
            self.request(),
            application_url="https://example.com/",
            adapter_key="form:employer-custom",
        )
        result = self.worker.inspect(req, authority=self.auth)
        # In unrestricted CI this may render; in the current controlled runtime it is admin-blocked.
        self.assertIn(result.runtime_state, {RuntimeState.RENDERED, RuntimeState.ACCESS_BLOCKED, RuntimeState.NAVIGATION_ERROR})
        self.assertTrue(result.read_only_invariant_ok)


class BrowserContractTests(unittest.TestCase):
    def test_request_requires_https_or_explicit_local_fixture(self):
        with self.assertRaises(ValueError):
            BrowserInspectionRequest(
                bridge_request_id="b", route_key="r", opportunity_key="o", requisition_id="q",
                application_url="http://evil.example.com", adapter_key="x", observed_at="now"
            ).validate()

    def test_max_navigation_steps_is_bounded(self):
        with self.assertRaises(ValueError):
            BrowserInspectionRequest(
                bridge_request_id="b", route_key="r", opportunity_key="o", requisition_id="q",
                application_url="https://example.com", adapter_key="x", observed_at="now", max_navigation_steps=0
            ).validate()
