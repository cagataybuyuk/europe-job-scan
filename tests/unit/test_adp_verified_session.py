from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from ejs.services.adp_verified_session_bootstrap import (
    AdpVerifiedSessionBootstrapRequest,
    _capture_session_storage,
    _form_surface_signature,
    _open_reviewed_adp_target,
    _visible,
    run_bootstrap,
    validate_request as validate_bootstrap_request,
)
from ejs.services import adp_verified_session_bootstrap as bootstrap
from ejs.services import adp_verified_session_inspector as inspector
from ejs.services.browser_worker import BrowserRuntimeConfig
from ejs.services.adp_verified_session_inspector import (
    AdpVerifiedSessionInspectorRequest,
    _load_session_storage,
    _session_storage_init_script,
    validate_request as validate_inspector_request,
    validate_storage_state,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64
CLEAR_COOKIE_SURFACE = {"observation_succeeded": True, "banner_visible": False, "preference_center_visible": False}


def surface(*controls):
    return {
        "visible_control_count": len(controls),
        "visible_controls": [
            {"tag": "input", "type": "text", "id": "question", "name": "", "disabled": False, **c}
            for c in controls
        ],
        "verification_completed": False,
        "storage_state_exported": False,
        "raw_values_exposed": False,
    }


class AdpVerifiedSessionTests(unittest.TestCase):
    def test_initial_navigation_waits_only_for_commit(self):
        page = MagicMock()
        page.url = URL
        result = _open_reviewed_adp_target(page, URL)
        page.goto.assert_called_once_with(URL, wait_until="commit", timeout=45_000)
        self.assertEqual(result["navigation_attempts"], 1)
        self.assertTrue(result["navigation_commit_observed"])
        self.assertFalse(result["navigation_timeout_tolerated"])

    def test_initial_navigation_tolerates_timeout_only_on_reviewed_adp_origin(self):
        page = MagicMock()
        page.url = URL
        page.goto.side_effect = TimeoutError("slow ADP load")
        result = _open_reviewed_adp_target(page, URL)
        self.assertEqual(result["navigation_attempts"], 1)
        self.assertFalse(result["navigation_commit_observed"])
        self.assertTrue(result["navigation_timeout_tolerated"])

    def test_initial_navigation_retries_timeout_before_reviewed_origin(self):
        page = MagicMock()
        page.url = "about:blank"
        calls = [0]

        def navigate(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise TimeoutError("no commit")
            page.url = URL
            return None

        page.goto.side_effect = navigate
        result = _open_reviewed_adp_target(page, URL)
        self.assertEqual(result["navigation_attempts"], 2)
        self.assertTrue(result["navigation_commit_observed"])
        self.assertEqual(page.goto.call_count, 2)

    def test_session_storage_capture_and_load_keep_diagnostics_value_free(self):
        page = MagicMock()
        page.evaluate.return_value = {"adp-session": "opaque-value"}
        captured, capture_evidence = _capture_session_storage(page)
        self.assertEqual(captured, {"adp-session": "opaque-value"})
        self.assertEqual(capture_evidence["session_storage_entry_count"], 1)
        self.assertFalse(capture_evidence["session_storage_values_exposed"])
        self.assertNotIn("opaque-value", repr(capture_evidence))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(json.dumps(captured), encoding="utf-8")
            loaded, load_evidence = _load_session_storage(str(path))
        self.assertEqual(loaded, captured)
        self.assertEqual(load_evidence["session_storage_entry_count"], 1)
        self.assertFalse(load_evidence["raw_session_storage_exposed"])
        self.assertNotIn("opaque-value", repr(load_evidence))
        script = _session_storage_init_script(URL, loaded)
        self.assertIn("workforcenow.adp.com", script)
        self.assertIn("sessionStorage.setItem", script)

    def test_windows_cli_passes_playwright_managed_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(inspector, "run_inspector") as run, patch("builtins.print"):
            run.return_value = {"inspector_status": "blocked"}
            with patch("sys.argv", ["inspector", "--url", URL,
                    "--expected-navigation-surface-fingerprint", FP, "--entry-ordinal", "0",
                    "--storage-state-json", "state.json", "--output", str(Path(tmp) / "report.json"),
                    "--playwright-managed"]):
                self.assertEqual(inspector.main(), 2)
            self.assertTrue(run.call_args.kwargs["config"].use_playwright_managed)

    def test_post_apply_auth_or_captcha_blocks_reuse(self):
        for boundary in ("auth_observed", "captcha_observed"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state.json"
                state.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
                page, context, browser, playwright = (MagicMock() for _ in range(4))
                context.new_page.return_value = page
                context.pages = [page]
                browser.new_context.return_value = context
                playwright.chromium.launch.return_value = browser
                with patch("playwright.sync_api.sync_playwright") as sync, \
                        patch.object(inspector, "_cookie_visibility", return_value=CLEAR_COOKIE_SURFACE), \
                        patch.object(inspector, "_snapshot", side_effect=[{}, {boundary: True}]), \
                        patch.object(inspector, "navigation_surface_fingerprint", return_value=FP), \
                        patch.object(inspector, "_approved_entry", return_value={}), \
                        patch.object(inspector, "_resolve_document_locator") as entry:
                    sync.return_value.__enter__.return_value = playwright
                    result = inspector.run_inspector(
                        AdpVerifiedSessionInspectorRequest(URL, FP, 0, str(state)),
                        config=BrowserRuntimeConfig(use_playwright_managed=True),
                    )
                self.assertEqual(result["error_code"], "ADP_VERIFIED_SESSION_POST_APPLY_BOUNDARY_OBSERVED")
                self.assertEqual(result["inspector_status"], "blocked")
                self.assertNotIn("session_reused", result)
                entry.return_value.click.assert_called_once()
                self.assertEqual(result["form_value_write_attempts"], 0)
                self.assertEqual(result["credential_entry_attempts"], 0)
                self.assertEqual(result["submit_attempts"], 0)

    def run_cookie_timeline(self, visibility_at, *, late_after_render=False):
        clock = [0]
        rendered = [False]
        page, context, browser, playwright = (MagicMock() for _ in range(4))
        page.url = URL
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms)
        context.new_page.return_value = page
        context.pages = [page]
        browser.new_context.return_value = context
        playwright.chromium.launch.return_value = browser

        def observe(_):
            if late_after_render and rendered[0]:
                return {**CLEAR_COOKIE_SURFACE, "banner_visible": True}
            return visibility_at(clock[0])

        def snapshot(*args, **kwargs):
            rendered[0] = True
            return {}

        clicks = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
            with patch("playwright.sync_api.sync_playwright") as sync, \
                    patch.object(inspector, "_cookie_visibility", side_effect=observe), \
                    patch.object(inspector, "_snapshot", side_effect=snapshot) as snapshots, \
                    patch.object(inspector, "navigation_surface_fingerprint", return_value=FP), \
                    patch.object(inspector, "_approved_entry", return_value={}), \
                    patch.object(inspector, "_resolve_document_locator") as entry, \
                    patch.object(inspector, "_surface_descriptor", return_value={
                        "verification_code_surface_present": False,
                        "guest_identity_surface_present": False,
                        "visible_application_control_count": 1,
                    }):
                sync.return_value.__enter__.return_value = playwright
                entry.return_value.click.side_effect = lambda **kwargs: clicks.append(clock[0])
                result = inspector.run_inspector(
                    AdpVerifiedSessionInspectorRequest(URL, FP, 0, str(state), render_wait_ms=2_000),
                    config=BrowserRuntimeConfig(use_playwright_managed=True),
                )
        self.assertEqual(result["form_value_write_attempts"], 0)
        self.assertEqual(result["credential_entry_attempts"], 0)
        self.assertEqual(result["file_upload_attempts"], 0)
        self.assertEqual(result["submit_attempts"], 0)
        self.assertEqual(result["cookie_gate"]["cookie_click_attempts"], 0)
        return result, clicks, snapshots.call_count

    def test_transient_cookie_banner_settles_before_apply(self):
        result, clicks, snapshots = self.run_cookie_timeline(
            lambda ms: {**CLEAR_COOKIE_SURFACE, "banner_visible": ms < 500},
        )
        self.assertEqual(result["inspector_status"], "inspected")
        self.assertEqual(clicks, [2_000])
        self.assertEqual(snapshots, 2)
        self.assertTrue(result["cookie_gate"]["initial"]["banner_visible"])
        self.assertFalse(result["cookie_gate"]["final"]["banner_visible"])
        self.assertGreaterEqual(result["cookie_gate"]["clear_stable_ms"], 1_000)

    def test_persistent_banner_or_preference_center_blocks_without_apply(self):
        for container in ("banner_visible", "preference_center_visible"):
            with self.subTest(container=container):
                result, clicks, snapshots = self.run_cookie_timeline(
                    lambda ms: {**CLEAR_COOKIE_SURFACE, container: True},
                )
                self.assertEqual(result["error_code"], "ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED")
                self.assertEqual(clicks, [])
                self.assertEqual(snapshots, 0)
                self.assertEqual(result["navigation_click_attempts"], 0)

    def test_initially_absent_delayed_banner_blocks_without_apply(self):
        result, clicks, _ = self.run_cookie_timeline(
            lambda ms: {**CLEAR_COOKIE_SURFACE, "banner_visible": ms >= 1_000},
        )
        self.assertEqual(result["inspector_status"], "blocked")
        self.assertEqual(clicks, [])
        self.assertFalse(result["cookie_gate"]["initial"]["banner_visible"])
        self.assertTrue(result["cookie_gate"]["final"]["banner_visible"])

    def test_banner_appearing_during_snapshot_blocks_apply(self):
        result, clicks, snapshots = self.run_cookie_timeline(lambda ms: CLEAR_COOKIE_SURFACE, late_after_render=True)
        self.assertEqual(result["inspector_status"], "blocked")
        self.assertEqual(clicks, [])
        self.assertEqual(snapshots, 1)
        self.assertTrue(result["cookie_gate"]["pre_apply"]["banner_visible"])
        self.assertFalse(result["cookie_gate"]["surface_clear"])

    def test_unreadable_cookie_surface_is_not_treated_as_clear(self):
        unknown = {"observation_succeeded": False, "banner_visible": None, "preference_center_visible": None}
        result, clicks, _ = self.run_cookie_timeline(lambda ms: unknown)
        self.assertEqual(result["inspector_status"], "blocked")
        self.assertEqual(clicks, [])
        self.assertGreater(result["cookie_gate"]["observation_error_count"], 0)

    def test_clear_surface_requires_a_full_stable_second(self):
        result, clicks, _ = self.run_cookie_timeline(
            lambda ms: {**CLEAR_COOKIE_SURFACE, "banner_visible": ms < 1_750},
        )
        self.assertEqual(result["inspector_status"], "blocked")
        self.assertFalse(result["cookie_gate"]["final"]["banner_visible"])
        self.assertEqual(clicks, [])

    def test_cookie_visibility_errors_are_sanitized(self):
        page = MagicMock()
        page.locator.side_effect = RuntimeError("sensitive-error-value")
        result = inspector._cookie_visibility(page)
        self.assertFalse(result["observation_succeeded"])
        self.assertIsNone(result["banner_visible"])
        self.assertNotIn("sensitive-error-value", repr(result))

    def test_inspector_rejects_unbounded_cookie_observation_window(self):
        for ms in (0, 999, 15_001):
            with self.subTest(ms=ms), self.assertRaisesRegex(ValueError, "INVALID_INSPECTOR_RENDER_WAIT"):
                validate_inspector_request(AdpVerifiedSessionInspectorRequest(URL, FP, 0, "state.json", render_wait_ms=ms))

    def run_timeline(self, stages, reports, *, expected_error=None):
        """Drive the actual bootstrap loop with a deterministic browser clock."""
        clock = [0]
        page = MagicMock()
        page.url = URL
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0] + ms / 1000)
        context = MagicMock()
        context.new_page.return_value = page
        context.pages = [page]
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        exports = []

        def export(path):
            exports.append(clock[0])
            Path(path).write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

        context.storage_state.side_effect = export

        def at(items):
            value = items[min(int(clock[0]), len(items) - 1)]
            if isinstance(value, Exception):
                raise value
            return dict(value)

        with tempfile.TemporaryDirectory() as tmp, \
                patch("playwright.sync_api.sync_playwright") as sync, \
                patch.object(bootstrap.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(bootstrap, "bootstrap_stage", side_effect=lambda _: at(stages)), \
                patch.object(bootstrap, "_sanitized_post_verification_report", side_effect=lambda *_: at(reports)), \
                patch("builtins.print"):
            sync.return_value.__enter__.return_value = playwright
            request = AdpVerifiedSessionBootstrapRequest(URL, str(Path(tmp) / "state.json"), str(Path(tmp) / "report.json"), 60)
            if expected_error:
                with self.assertRaisesRegex(TimeoutError, expected_error):
                    run_bootstrap(request)
                self.assertFalse(Path(request.storage_state_out).exists())
                self.assertFalse(Path(request.report_out).exists())
                result = None
            else:
                result = run_bootstrap(request)
                self.assertEqual(json.loads(Path(request.report_out).read_text()), result)
        context.close.assert_called_once()
        browser.close.assert_called_once()
        return exports, result

    def test_blank_transition_waits_for_stable_form_before_export(self):
        otp = {"verification_code_visible": True, "identity_surface_visible": False}
        cleared = {"verification_code_visible": False, "identity_surface_visible": False}
        exports, result = self.run_timeline(
            [otp, cleared], [surface()] * 21 + [surface({})],
        )
        self.assertEqual(exports, [26])
        self.assertTrue(result["post_verification_surface_stable"])
        self.assertEqual(result["visible_form_control_count"], 1)
        self.assertFalse(result["session_reuse_proven"])

    def test_permanently_blank_transition_never_exports(self):
        exports, _ = self.run_timeline(
            [{"verification_code_visible": True, "identity_surface_visible": False},
             {"verification_code_visible": False, "identity_surface_visible": False}],
            [surface()], expected_error="FORM_NOT_READY",
        )
        self.assertEqual(exports, [])

    def test_form_without_observed_verification_never_exports(self):
        exports, _ = self.run_timeline(
            [{"verification_code_visible": False, "identity_surface_visible": False}],
            [surface({})], expected_error="VERIFICATION_TIMEOUT",
        )
        self.assertEqual(exports, [])

    def test_identity_return_and_dom_failure_reset_stability(self):
        clear = {"verification_code_visible": False, "identity_surface_visible": False}
        stages = [{"verification_code_visible": True, "identity_surface_visible": False}] + [clear] * 8
        stages += [{"verification_code_visible": False, "identity_surface_visible": True}, clear]
        reports = [surface({})] * 14 + [RuntimeError("navigation"), surface({})]
        exports, _ = self.run_timeline(stages, reports)
        self.assertEqual(exports, [20])

    def test_changing_form_resets_stability(self):
        exports, _ = self.run_timeline(
            [{"verification_code_visible": True, "identity_surface_visible": False},
             {"verification_code_visible": False, "identity_surface_visible": False}],
            [surface({})] * 8 + [surface({"id": "other-question"})],
        )
        self.assertEqual(exports, [13])

    def test_navigation_buttons_cookie_search_and_password_are_not_form_evidence(self):
        for control in (
            {"tag": "button"}, {"tag": "sdf-button"}, {"type": "hidden"},
            {"type": "search"}, {"id": "onetrust-accept-btn-handler"},
            {"id": "ot-group-id-C0001"}, {"id": "guestEmail"},
            {"id": "oneTimePassWord"}, {"disabled": True}, {"type": "password"},
        ):
            with self.subTest(control=control):
                self.assertEqual(_form_surface_signature(surface(control)), ())
        self.assertEqual(_form_surface_signature(surface({}, {"type": "password"})), ())

    def test_duplicate_otp_control_and_dom_errors_do_not_mean_absent(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 2
        page.locator.return_value.nth.side_effect = [MagicMock(is_visible=lambda: False), MagicMock(is_visible=lambda: True)]
        self.assertTrue(_visible(page, "#oneTimePassWord"))
        page.locator.side_effect = RuntimeError("detached document")
        with self.assertRaises(RuntimeError):
            _visible(page, "#oneTimePassWord")

    def test_bootstrap_request_accepts_reviewed_adp_url(self):
        validate_bootstrap_request(AdpVerifiedSessionBootstrapRequest(
            application_url=URL,
            storage_state_out="state.json",
            report_out="report.json",
            timeout_seconds=900,
        ))

    def test_bootstrap_rejects_short_timeout(self):
        with self.assertRaisesRegex(ValueError, "INVALID_ADP_SESSION_BOOTSTRAP_TIMEOUT"):
            validate_bootstrap_request(AdpVerifiedSessionBootstrapRequest(
                application_url=URL,
                storage_state_out="state.json",
                report_out="report.json",
                timeout_seconds=30,
            ))

    def test_inspector_request_requires_valid_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"):
            validate_inspector_request(AdpVerifiedSessionInspectorRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint="bad",
                entry_ordinal=0,
                storage_state_json_path="state.json",
            ))

    def test_storage_state_validation_exposes_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({
                "cookies": [{"name": "secret-cookie", "value": "secret-value"}],
                "origins": [{"origin": "https://example.test", "localStorage": [{"name": "token", "value": "secret"}]}],
            }), encoding="utf-8")
            evidence = validate_storage_state(str(path))
        self.assertEqual(evidence["cookie_count"], 1)
        self.assertEqual(evidence["origin_count"], 1)
        self.assertFalse(evidence["raw_storage_state_exposed"])
        self.assertNotIn("secret-cookie", repr(evidence))
        self.assertNotIn("secret-value", repr(evidence))

    def test_storage_state_rejects_invalid_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"cookies": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHAPE_INVALID"):
                validate_storage_state(str(path))


if __name__ == "__main__":
    unittest.main()
