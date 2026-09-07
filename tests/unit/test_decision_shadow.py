import unittest
from ejs.services.decision_shadow import DecisionRecord, evaluate_auto_review, run_decision_shadow


def rec(**kw):
    base = dict(key="x", company="C", role="R", role_fit=90, opportunity_score=84,
                availability="Open", mandatory_language="English", sponsorship_evidence="Recognised Sponsor / Official Registry",
                verification_status="Partially Verified", url_status="Valid", authoritative_decision="Auto Apply",
                authoritative_confidence="Medium", authoritative_reason="", role_fit_rule_version="ROLEFIT-1.0",
                opportunity_rule_version="OPPORTUNITY-1.0", auto_review_rule_version="AUTOREVIEW-1.0", provenance_status="Versioned")
    base.update(kw); return DecisionRecord(**base)

class DecisionShadowTests(unittest.TestCase):
    def test_closed_is_skip(self): self.assertEqual(evaluate_auto_review(rec(availability="Closed", authoritative_decision="Auto Skip", authoritative_confidence="High"))[0], "Auto Skip")
    def test_low_opportunity_is_skip(self): self.assertEqual(evaluate_auto_review(rec(opportunity_score=45))[0], "Auto Skip")
    def test_missing_score_fails_closed(self): self.assertEqual(evaluate_auto_review(rec(opportunity_score=None))[0], "Manual Review")
    def test_role_explicit_auto_apply_high(self):
        d,c,_=evaluate_auto_review(rec(sponsorship_evidence="Role Explicit Yes", authoritative_confidence="High")); self.assertEqual((d,c),("Auto Apply","High"))
    def test_authoritative_review_material_gap_is_preserved(self):
        self.assertEqual(evaluate_auto_review(rec(authoritative_decision="Manual Review", authoritative_confidence="High"))[0], "Manual Review")
    def test_report_zero_write(self):
        r=run_decision_shadow("r","EJS-BUNDLE-1.4",[rec()]); self.assertEqual(r.write_attempts,0); self.assertTrue(r.passed)

if __name__ == '__main__': unittest.main()
