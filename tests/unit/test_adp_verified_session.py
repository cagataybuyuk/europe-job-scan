from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ejs.services.adp_verified_session_bootstrap import (
    AdpVerifiedSessionBootstrapRequest,
    validate_request as validate_bootstrap_request,
)
from ejs.services.adp_verified_session_inspector import (
    AdpVerifiedSessionInspectorRequest,
    validate_request as validate_inspector_request,
    validate_storage_state,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


class AdpVerifiedSessionTests(unittest.TestCase):
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
