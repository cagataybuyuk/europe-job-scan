from __future__ import annotations

import unittest

from ejs.services.linkedin_live_resolver import (
    classify_source_snapshot,
    extract_external_apply_target,
    validate_linkedin_job_url,
)


class LinkedInLiveResolverTest(unittest.TestCase):
    def test_validate_linkedin_job_url_accepts_job_view(self):
        validate_linkedin_job_url(
            "https://ie.linkedin.com/jobs/view/ai-business-analyst-at-avanci-4467233887"
        )

    def test_validate_linkedin_job_url_rejects_non_linkedin(self):
        with self.assertRaises(ValueError):
            validate_linkedin_job_url("https://example.com/jobs/view/123")

    def test_extracts_direct_external_https_target(self):
        href = "https://jobs.example-ats.com/company/role/123"
        self.assertEqual(extract_external_apply_target(href), href)

    def test_extracts_external_apply_query_target_without_navigation(self):
        href = (
            "https://www.linkedin.com/jobs/view/externalApply/4467233887"
            "?url=https%3A%2F%2Fjobs.example-ats.com%2Fcompany%2Frole%2F123"
        )
        self.assertEqual(
            extract_external_apply_target(href),
            "https://jobs.example-ats.com/company/role/123",
        )

    def test_rejects_linkedin_or_non_https_target(self):
        self.assertIsNone(
            extract_external_apply_target(
                "https://www.linkedin.com/jobs/view/externalApply/1?url=http%3A%2F%2Fexample.com%2Fapply"
            )
        )
        self.assertIsNone(extract_external_apply_target("https://www.linkedin.com/jobs/view/1"))

    def test_external_target_routes_to_ats_candidate_without_execution_authority(self):
        result = classify_source_snapshot({
            "apply_anchors": [
                {
                    "kind": "apply",
                    "href": "https://jobs.example-ats.com/apply/123",
                }
            ],
            "apply_buttons": [],
            "body_signals": [],
        })
        self.assertEqual(result["resolution_state"], "external_apply_resolved")
        self.assertTrue(result["ats_inspection_candidate"])
        self.assertFalse(result["human_action_required"])

    def test_easy_apply_is_fail_closed_boundary(self):
        result = classify_source_snapshot({
            "apply_anchors": [],
            "apply_buttons": [{"kind": "easy_apply"}],
            "body_signals": ["easy_apply"],
        })
        self.assertEqual(result["resolution_state"], "linkedin_easy_apply")
        self.assertEqual(result["error_code"], "LINKEDIN_EASY_APPLY_BOUNDARY")
        self.assertFalse(result["ats_inspection_candidate"])
        self.assertTrue(result["human_action_required"])

    def test_login_and_challenge_boundaries_are_fail_closed(self):
        login = classify_source_snapshot({
            "apply_anchors": [], "apply_buttons": [], "body_signals": ["login_boundary"]
        })
        challenge = classify_source_snapshot({
            "apply_anchors": [], "apply_buttons": [], "body_signals": ["challenge_boundary"]
        })
        self.assertEqual(login["resolution_state"], "login_boundary")
        self.assertEqual(challenge["resolution_state"], "challenge_boundary")
        self.assertFalse(login["ats_inspection_candidate"])
        self.assertFalse(challenge["ats_inspection_candidate"])

    def test_unresolved_external_apply_wrapper_is_not_followed(self):
        result = classify_source_snapshot({
            "apply_anchors": [
                {
                    "kind": "external_apply_redirect",
                    "href": "https://www.linkedin.com/jobs/view/externalApply/4467233887",
                }
            ],
            "apply_buttons": [],
            "body_signals": [],
        })
        self.assertEqual(result["resolution_state"], "external_apply_unresolved")
        self.assertEqual(
            result["error_code"], "LINKEDIN_EXTERNAL_APPLY_TARGET_NOT_EXPOSED"
        )
        self.assertFalse(result["ats_inspection_candidate"])


if __name__ == "__main__":
    unittest.main()
