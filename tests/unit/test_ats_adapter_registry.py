from __future__ import annotations

import unittest

from ejs.contracts.ats_adapter import (
    AdapterCapabilityManifest,
    AdapterImplementationState,
    AtsFamily,
    DispatchState,
    HumanReviewReason,
    LAUNCH_ATS_FAMILIES,
    SessionRequirement,
)
from ejs.services.ats_adapter_registry import (
    all_launch_manifests,
    classify_application_target,
    human_review_from_dispatch,
    manifest_for_family,
)


class AtsAdapterRegistryTests(unittest.TestCase):
    def test_launch_registry_covers_all_six_priority_families(self):
        manifests = all_launch_manifests()
        self.assertEqual({m.family for m in manifests}, set(LAUNCH_ATS_FAMILIES))
        self.assertEqual(len(manifests), 6)

    def test_current_baseline_only_dispatches_adapters_with_inspection_support(self):
        cases = {
            "https://jobs.smartrecruiters.com/Company/123": (AtsFamily.SMARTRECRUITERS, DispatchState.DISPATCH),
            "https://tenant.wd3.myworkdayjobs.com/en-US/jobs/job/123": (AtsFamily.WORKDAY, DispatchState.HUMAN_REVIEW),
            "https://boards.greenhouse.io/company/jobs/123": (AtsFamily.GREENHOUSE, DispatchState.HUMAN_REVIEW),
            "https://jobs.lever.co/company/abc": (AtsFamily.LEVER, DispatchState.HUMAN_REVIEW),
            "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html": (AtsFamily.ADP, DispatchState.DISPATCH),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                decision = classify_application_target(url)
                self.assertEqual((decision.family, decision.state), expected)
                self.assertFalse(decision.mutation_authorized)
                self.assertFalse(decision.submit_authorized)

    def test_linkedin_easy_apply_requires_explicit_source_resolution(self):
        unresolved = classify_application_target("https://www.linkedin.com/jobs/view/123")
        self.assertEqual(unresolved.state, DispatchState.HUMAN_REVIEW)
        self.assertEqual(unresolved.reason_code, "LINKEDIN_ROUTE_REQUIRES_SOURCE_RESOLUTION")

        resolved = classify_application_target(
            "https://www.linkedin.com/jobs/view/123",
            linkedin_resolution_state="linkedin_easy_apply",
        )
        self.assertEqual(resolved.family, AtsFamily.LINKEDIN_EASY_APPLY)
        self.assertEqual(resolved.state, DispatchState.HUMAN_REVIEW)
        self.assertEqual(resolved.reason_code, "ADAPTER_NOT_READY")

    def test_unknown_https_host_is_unsupported(self):
        decision = classify_application_target("https://careers.example.com/jobs/1")
        self.assertEqual(decision.family, AtsFamily.UNSUPPORTED)
        self.assertEqual(decision.state, DispatchState.UNSUPPORTED)
        self.assertEqual(decision.reason_code, "UNSUPPORTED_ATS_HOST")

    def test_non_https_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute HTTPS"):
            classify_application_target("http://jobs.smartrecruiters.com/Company/123")

    def test_human_review_contract_is_structured_and_retryable_for_planned_adapter(self):
        decision = classify_application_target(
            "https://tenant.wd3.myworkdayjobs.com/en-US/jobs/job/123"
        )
        item = human_review_from_dispatch(
            execution_id="exec:1",
            opportunity_key="opp:1",
            decision=decision,
            evidence_ref="artifact:route:1",
        )
        self.assertEqual(item.reason, HumanReviewReason.ADAPTER_NOT_READY)
        self.assertEqual(item.ats_family, AtsFamily.WORKDAY)
        self.assertTrue(item.retryable)
        self.assertTrue(item.user_action_required)

    def test_dispatch_ready_decision_cannot_be_converted_to_human_review(self):
        decision = classify_application_target("https://jobs.smartrecruiters.com/Company/123")
        with self.assertRaisesRegex(ValueError, "does not require human review"):
            human_review_from_dispatch(
                execution_id="exec:1",
                opportunity_key="opp:1",
                decision=decision,
            )

    def test_planned_manifest_cannot_advertise_implemented_capability(self):
        bad = AdapterCapabilityManifest(
            family=AtsFamily.GREENHOUSE,
            adapter_key="ats:greenhouse",
            adapter_version="planned",
            implementation_state=AdapterImplementationState.PLANNED,
            inspection_supported=True,
            safe_fill_supported=False,
            document_upload_supported=False,
            multi_step_supported=False,
            conditional_submit_supported=False,
            confirmation_supported=False,
            session_requirement=SessionRequirement.NONE,
        )
        with self.assertRaisesRegex(ValueError, "planned adapter"):
            bad.validate()

    def test_registry_refuses_unsupported_manifest_lookup(self):
        with self.assertRaises(KeyError):
            manifest_for_family(AtsFamily.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
