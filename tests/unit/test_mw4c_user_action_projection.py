from __future__ import annotations

import unittest

from ejs.domain.user_action import UserActionRow
from ejs.services.user_action_projection import normalize_dateish, project_user_actions, compare_user_action_projection


class Mw4cUserActionProjectionTests(unittest.TestCase):
    def test_form_fill_submit_projection(self):
        rows = project_user_actions(
            form_fill_rows=[dict(company="Planon", role="BA", application_status="To Apply", fill_pack_status="Ready for User Submit", remaining_user_actions="Upload; submit", user_required_factual=0, user_only_legal=0, technical_captcha=0, assigned_cv="Base", application_url="url", last_mapped="2026-08-08", queue_date="2026-07-29", notes="n")],
            application_rows=[], follow_up_rows=[], interview_rows=[])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].priority, "P1")
        self.assertEqual(rows[0].technical_upload, 1)

    def test_form_fill_input_projection_detects_review(self):
        rows = project_user_actions(
            form_fill_rows=[dict(company="K", role="BA", application_status="To Apply", fill_pack_status="Ready - User Input Required", remaining_user_actions="approve salary; upload", user_required_factual=1, user_only_legal=0, technical_captcha=0)],
            application_rows=[], follow_up_rows=[], interview_rows=[])
        self.assertEqual(rows[0].review_approval, 1)
        self.assertEqual(rows[0].ordinary_facts, 1)

    def test_manual_review_fallback_workday(self):
        rows = project_user_actions(
            form_fill_rows=[],
            application_rows=[dict(company="D", role="AI BA", status="To Review", next_action="Review", last_update="2026-07-30", user_required_application_fields="", cv_persona="Base", source_url="url", auto_review_reason="reason")],
            follow_up_rows=[], interview_rows=[])
        self.assertEqual(rows[0].due_date, "2026-08-03")
        self.assertEqual(rows[0].priority, "P3")

    def test_manual_review_user_field_classification(self):
        rows = project_user_actions(
            form_fill_rows=[],
            application_rows=[dict(company="A", role="BA", status="To Review", next_action="Review", user_required_application_fields="confirm employment; privacy consent")],
            follow_up_rows=[], interview_rows=[])
        self.assertEqual(rows[0].ordinary_facts, 1)
        self.assertEqual(rows[0].legal_consent, 1)

    def test_follow_up_due(self):
        rows = project_user_actions(
            form_fill_rows=[], application_rows=[],
            follow_up_rows=[dict(company="H", role="R", pipeline_status="Applied", queue_state="Due", recommended_action="Monitor", follow_up_due="2026-08-03")],
            interview_rows=[])
        self.assertEqual(rows[0].action_category, "Follow-up")
        self.assertEqual(rows[0].review_approval, 1)

    def test_follow_up_data_quality(self):
        rows = project_user_actions(
            form_fill_rows=[], application_rows=[],
            follow_up_rows=[dict(company="C", role="R", pipeline_status="Applied", queue_state="Data Quality - Missing Applied Date", recommended_action="Confirm date")],
            interview_rows=[])
        self.assertEqual(rows[0].action_category, "Data Quality")
        self.assertEqual(rows[0].ordinary_facts, 1)

    def test_interview_projection(self):
        rows = project_user_actions(
            form_fill_rows=[], application_rows=[], follow_up_rows=[],
            interview_rows=[dict(prep_id="IP-1", company="X", role="R", pipeline_stage="Interview 1", readiness_tier="Needs Prep", priority="P1", required_user_action="Practice", interview_date="2026-08-20")])
        self.assertEqual(rows[0].action_category, "Interview Prep")

    def test_ready_interview_excluded(self):
        rows = project_user_actions(
            form_fill_rows=[], application_rows=[], follow_up_rows=[],
            interview_rows=[dict(prep_id="IP-1", company="X", role="R", pipeline_stage="Interview 1", readiness_tier="Ready", priority="P1")])
        self.assertEqual(rows, [])

    def test_serial_date_normalization(self):
        self.assertEqual(normalize_dateish("46237"), "2026-08-03")

    def test_exact_compare(self):
        row = UserActionRow("P1","Submit","C","R","To Apply","Ready for User Submit","Submit",None,0,0,0,1,None,None,"Yes — user actions only","Submit Now",None,None)
        report = compare_user_action_projection([row], [row])
        self.assertTrue(report["exact_row_parity"])
        self.assertEqual(report["critical_divergences"], 0)


if __name__ == "__main__":
    unittest.main()
