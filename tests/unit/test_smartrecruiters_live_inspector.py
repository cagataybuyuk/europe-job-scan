import importlib.util
import unittest
from unittest.mock import patch

from ejs.services.smartrecruiters_live_inspector import (
    LiveInspectionRequest,
    combine_shadow_reports,
    discovery_state,
    follow_one_read_only_apply_entry,
    select_apply_entry,
    is_known_captcha_origin,
    same_origin_url,
    scope_shadow_report,
    validate_live_url,
)


class LiveInspectionPolicyTests(unittest.TestCase):
    def test_only_https_smartrecruiters_urls_are_accepted(self):
        for url in (
            "http://jobs.smartrecruiters.com/acme/job",
            "https://example.com/job",
            "https://user:pass@jobs.smartrecruiters.com/acme/job",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_live_url(url)
        validate_live_url("https://jobs.smartrecruiters.com/Version1/job")
        validate_live_url("https://jobs.smartrecruiters.com")

    def test_timeouts_are_checked_before_browser_start(self):
        from ejs.services.smartrecruiters_live_inspector import inspect_live_page
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 500))
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 60_001))
        with self.assertRaisesRegex(ValueError, "INVALID_RENDER_WAIT_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", render_wait_ms=500))
        with self.assertRaisesRegex(ValueError, "INVALID_RENDER_WAIT_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", render_wait_ms=15_001))

    def test_same_origin_is_explicit_and_default_ports_are_normalized(self):
        parent = "https://jobs.smartrecruiters.com/oneclick-ui/acme"
        self.assertTrue(same_origin_url(parent, "https://jobs.smartrecruiters.com/frame"))
        self.assertTrue(same_origin_url(parent, "https://jobs.smartrecruiters.com:443/frame"))
        self.assertTrue(same_origin_url(parent, "about:blank"))
        self.assertFalse(same_origin_url(parent, "https://example.smartrecruiters.com/frame"))
        self.assertFalse(same_origin_url(parent, "https://example.com/frame"))

    def test_known_captcha_delivery_origins_are_classified_without_dom_access(self):
        self.assertTrue(is_known_captcha_origin("https://geo.captcha-delivery.com"))
        self.assertTrue(is_known_captcha_origin("https://edge.captcha-delivery.com"))
        self.assertTrue(is_known_captcha_origin("https://captcha-delivery.com"))
        self.assertFalse(is_known_captcha_origin("https://jobs.smartrecruiters.com"))
        self.assertFalse(is_known_captcha_origin("https://notcaptcha-delivery.example.com"))
        self.assertFalse(is_known_captcha_origin(""))

    def test_scoped_frame_reports_get_unique_observation_identities(self):
        base = {
            "controls": [{
                "scope": "document",
                "observation_key": "document/input@1",
                "tag": "input",
                "type": "text",
                "id": "firstName",
                "name": "firstName",
                "label": "First name",
                "visible": True,
                "disabled": False,
                "required": True,
                "host_required_hint": False,
                "accept": "",
                "multiple": False,
            }],
            "actions": [],
        }
        main = scope_shadow_report(base, "document")
        frame = scope_shadow_report(base, "frame:1/document")
        combined = combine_shadow_reports([main, frame])
        keys = [item["observation_key"] for item in combined["controls"]]
        self.assertEqual(keys, ["document/input@1", "frame:1/document/input@1"])
        self.assertEqual(len(keys), len(set(keys)))

    def test_empty_native_form_is_a_fail_closed_discovery_boundary(self):
        self.assertEqual(
            discovery_state({"controls": [], "actions": []}),
            ("form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED"),
        )
        self.assertEqual(
            discovery_state({"controls": [{"observation_key": "document/input@0", "visible": True}], "actions": []}),
            ("inspected", ""),
        )

    def test_hidden_share_input_is_not_an_application_form(self):
        self.assertEqual(discovery_state({"controls":[{"type":"text","visible":False}],"actions":[]}),
                         ("form_structure_not_discovered","FORM_STRUCTURE_NOT_DISCOVERED"))

    def test_visible_expiry_status_overrides_even_visible_controls(self):
        self.assertEqual(discovery_state({"controls":[{"type":"text","visible":True}],
            "actions":[{"label":"Sorry, this job has expired","visible":True}]}),
            ("job_expired","JOB_EXPIRED"))

    def test_visible_password_is_auth_boundary(self):
        self.assertEqual(discovery_state({"controls":[{"type":"password","visible":True}]}),
                         ("auth_boundary","AUTH_BOUNDARY"))

    def test_cli_records_expired_and_auth_observations_without_execution_success_claim(self):
        from ejs.services import smartrecruiters_live_inspector as inspector
        for state,code in (("job_expired","JOB_EXPIRED"),("auth_boundary","AUTH_BOUNDARY")):
            with self.subTest(state=state),patch.object(inspector,"inspect_live_page",return_value={
                "runtime_state":state,"error_code":code,"inspection_only":True,"live_execution_ready":False
            }),patch.object(inspector.sys,"argv",["inspector","--url","https://jobs.smartrecruiters.com/Acme/job"]),\
                 patch("builtins.print"):
                self.assertEqual(inspector.main(),0) # observation succeeded; no execution authority

    def test_cli_unsupported_runtime_still_fails(self):
        from ejs.services import smartrecruiters_live_inspector as inspector
        with patch.object(inspector,"inspect_live_page",return_value={"runtime_state":"unsupported"}),\
             patch.object(inspector.sys,"argv",["inspector","--url","https://jobs.smartrecruiters.com/Acme/job"]),\
             patch("builtins.print"):
            self.assertEqual(inspector.main(),2)



class BoundedApplyEntryPolicyTests(unittest.TestCase):
    POSTING = "https://jobs.smartrecruiters.com/Acme/123-job"
    TRUSTED = "https://jobs.smartrecruiters.com/oneclick-ui/Acme/123"

    def cta(self, **changes):
        item = dict(tag="a", label="I'm interested", href=self.TRUSTED,
                    type="", visible=True, disabled=False, in_form=False)
        item.update(changes)
        return item

    def test_unique_visible_trusted_oneclick_link_is_accepted(self):
        result = select_apply_entry([self.cta()], self.POSTING)
        self.assertEqual(result["status"], "APPROVED_CTA")
        self.assertEqual(result["kind"], "link")
        self.assertEqual(result["href"], self.TRUSTED)

    def test_untrusted_or_ambiguous_links_cannot_trigger_navigation(self):
        urls = (
            "https://evil.example/apply",
            "https://jobs.smartrecruiters.com.evil.example/oneclick-ui/Acme/123",
            "http://jobs.smartrecruiters.com/oneclick-ui/Acme/123",
            "https://user:pass@jobs.smartrecruiters.com/oneclick-ui/Acme/123",
            "https://jobs.smartrecruiters.com/Acme/123-job",
            "javascript:alert(1)",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(select_apply_entry([self.cta(href=url)], self.POSTING)["status"],
                                 "NO_APPROVED_CTA")
        self.assertEqual(select_apply_entry([self.cta(), self.cta()], self.POSTING)["status"],
                         "AMBIGUOUS_APPLY_CTA")
        self.assertEqual(select_apply_entry([self.cta()], "http://jobs.smartrecruiters.com/Acme")["status"],
                         "UNTRUSTED_POSTING_URL")

    def test_hidden_disabled_submit_and_non_entry_controls_are_not_clicked(self):
        candidates = [
            self.cta(visible=False), self.cta(disabled=True), self.cta(in_form=True),
            self.cta(tag="button", href="", type="submit"),
            self.cta(tag="button", href="", type="reset"),
            self.cta(label="Submit application"),
            self.cta(label="Apply with LinkedIn"),
            self.cta(label="I'm interested", tag="div", href=""),
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertEqual(select_apply_entry([candidate], self.POSTING)["status"],
                                 "NO_APPROVED_CTA")

    def test_unique_explicit_non_submit_button_is_allowed_to_open_only(self):
        value = select_apply_entry(
            [self.cta(tag="button", href="", type="button", label="I’m interested")],
            self.POSTING
        )
        self.assertEqual(value["status"], "APPROVED_CTA")
        self.assertEqual(value["kind"], "button")


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class BoundedApplyEntryBrowserTests(unittest.TestCase):
    POSTING = "https://jobs.smartrecruiters.com/Acme/123-job"
    APPLY = "https://jobs.smartrecruiters.com/oneclick-ui/Acme/123"

    def test_verified_oneclick_get_navigation_reveals_form_without_mutating(self):
        from playwright.sync_api import sync_playwright
        from ejs.services.smartrecruiters_shadow import inspect_shadow_form
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                context.route(self.POSTING, lambda route: route.fulfill(
                    status=200, content_type="text/html",
                    body="<a href='" + self.APPLY + "'>I'm interested</a>"
                ))
                context.route(self.APPLY, lambda route: route.fulfill(
                    status=200, content_type="text/html",
                    body="<input aria-label='First name' required><input type='file' aria-label='CV'>"
                ))
                page = context.new_page()
                page.goto(self.POSTING)
                result = follow_one_read_only_apply_entry(
                    page, context, self.POSTING, timeout_ms=5000
                )
                self.assertEqual(result["status"], "FOLLOWED")
                self.assertTrue(result["url_changed"])
                self.assertEqual(result["attempt_count"], 1)
                observed = inspect_shadow_form(page)
                self.assertTrue(any(c["label"] == "First name" for c in observed["controls"]))
                self.assertEqual(observed["form_value_write_attempts"], 0)
                self.assertEqual(observed["file_upload_attempts"], 0)
                self.assertEqual(observed["submit_attempts"], 0)
                self.assertEqual(page.locator('input[type=file]').count(), 1)
                self.assertEqual(page.locator('input[type=file]').evaluate_all(
                    "(els) => els.map(el => el.files.length)"), [0])
            finally:
                browser.close()

    def test_same_url_button_modal_is_observed_without_field_interaction(self):
        from playwright.sync_api import sync_playwright
        from ejs.services.smartrecruiters_shadow import inspect_shadow_form
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                context.route(self.POSTING, lambda route: route.fulfill(
                    status=200, content_type="text/html",
                    body="<button type='button' id='apply'>I'm interested</button>"
                         "<script>document.querySelector('#apply').addEventListener('click',"
                         "() => {const x=document.createElement('input');"
                         "x.setAttribute('aria-label','First name');"
                         "document.body.appendChild(x);})</script>"
                ))
                page = context.new_page()
                page.goto(self.POSTING)
                result = follow_one_read_only_apply_entry(
                    page, context, self.POSTING, timeout_ms=5000
                )
                self.assertEqual(result["status"], "FOLLOWED")
                self.assertFalse(result["url_changed"])
                self.assertEqual(page.locator('input').count(), 1)
                observed = inspect_shadow_form(page)
                self.assertEqual(observed["form_value_write_attempts"], 0)
            finally:
                browser.close()

    def test_form_inside_submit_button_never_runs(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_content("<form><button type='submit'>Apply</button></form>"
                                 "<script>window.submitted=0;"
                                 "document.querySelector('form').onsubmit=e=>{"
                                 "e.preventDefault();window.submitted++}</script>")
                result = follow_one_read_only_apply_entry(
                    page, context, self.POSTING, timeout_ms=5000
                )
                self.assertEqual(result["status"], "NO_APPROVED_CTA")
                self.assertEqual(result["attempt_count"], 0)
                self.assertEqual(page.evaluate("window.submitted"), 0)
            finally:
                browser.close()

    def test_button_post_is_blocked_and_not_misreported_as_success(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                context.route(self.POSTING, lambda route: route.fulfill(
                    status=200, content_type="text/html",
                    body="<button type='button' id='apply'>I'm interested</button>"
                         "<script>document.querySelector('#apply').addEventListener('click',"
                         "() => {fetch('/oneclick-ui/collect',{method:'POST',body:'x'}).catch(()=>{})})"
                         "</script>"
                ))
                page = context.new_page()
                page.goto(self.POSTING)
                result = follow_one_read_only_apply_entry(
                    page, context, self.POSTING, timeout_ms=5000
                )
                self.assertEqual(result["status"], "NETWORK_FENCE_BLOCKED")
                self.assertIn("NON_GET_REQUEST", result["blocked_request_types"])
                self.assertEqual(result["attempt_count"], 1)
            finally:
                browser.close()

    def test_untrusted_external_navigation_does_not_go_through(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                context.route(self.POSTING, lambda route: route.fulfill(
                    status=200, content_type="text/html",
                    body="<button type='button' id='apply'>I'm interested</button>"
                         "<script>document.querySelector('#apply').addEventListener('click',"
                         "() => {location.href='https://evil.example/collect'})</script>"
                ))
                page = context.new_page()
                page.goto(self.POSTING)
                result = follow_one_read_only_apply_entry(
                    page, context, self.POSTING, timeout_ms=5000
                )
                self.assertEqual(result["status"], "NETWORK_FENCE_BLOCKED")
                self.assertIn("UNTRUSTED_NAVIGATION", result["blocked_request_types"])
                self.assertEqual(page.url, self.POSTING)
            finally:
                browser.close()



if __name__ == "__main__":
    unittest.main()
