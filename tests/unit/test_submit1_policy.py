import unittest

from ejs.contracts.submit import (
    ApprovedSubmitPolicy,
    RolloutStage,
    SubmitAuthority,
    submit_identity,
    validation_identity,
)
from ejs.domain.submission import (
    ConfirmationEvidence,
    ConfirmationType,
    ControlResolution,
    PreSubmitValidationRequest,
    ResolutionMode,
    SubmissionAttemptStatus,
    ValidationDecision,
)
from ejs.domain.template_history import DriftState, MappingConfidence
from ejs.persistence.submit_memory import InMemorySubmitRepository
from ejs.services.submit_policy import (
    applied_transition_allowed,
    create_submission_attempt,
    mark_attempt_confirmed,
    validate_pre_submit,
)


class Submit1PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ApprovedSubmitPolicy()
        self.authority_on = SubmitAuthority(
            browser_external_form_write=True,
            final_submit_runtime_flag=True,
            kill_switch_engaged=False,
        )

    def c(self, key, field, required=True, mode=ResolutionMode.VERIFIED_FACT, **kwargs):
        return ControlResolution(
            control_key=key,
            label=key,
            required=required,
            canonical_field=field,
            mapping_confidence=MappingConfidence.HIGH,
            resolution_mode=mode,
            provenance_ref=kwargs.pop("provenance_ref", "evidence:verified"),
            **kwargs,
        )

    def request(self, controls=(), **kwargs):
        base = dict(
            execution_id="exec:rituals:744:1",
            opportunity_key="rituals:business-analyst",
            requisition_id="7440001",
            candidate_profile_version="candidate-profile-v1",
            apply_url="https://jobs.smartrecruiters.com/Rituals1/7440001",
            ats_family="SmartRecruiters",
            form_fingerprint="abc123",
            drift_state=DriftState.NO_BASELINE,
            runtime_inspection_current=True,
            tracker_fingerprint_current=True,
            controls=tuple(controls),
            rollout_stage=RolloutStage.R2_SINGLE_CANARY,
            submissions_today=0,
        )
        base.update(kwargs)
        return PreSubmitValidationRequest(**base)

    def test_approved_policy_baseline_passes(self):
        self.policy.validate()

    def test_submit_identity_is_deterministic_and_profile_versioned(self):
        a = submit_identity("Rituals BA", "744", "profile-v1")
        b = submit_identity("Rituals BA", "744", "profile-v1")
        c = submit_identity("Rituals BA", "744", "profile-v2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_validation_identity_is_form_and_policy_versioned(self):
        self.assertNotEqual(
            validation_identity("exec:1", "fp-a"),
            validation_identity("exec:1", "fp-b"),
        )

    def test_runtime_authority_is_separate_from_policy_approval(self):
        off = SubmitAuthority()
        self.assertFalse(off.submit_click_allowed)
        self.assertTrue(self.authority_on.submit_click_allowed)

    def test_captcha_bypass_is_forbidden(self):
        with self.assertRaises(PermissionError):
            SubmitAuthority(captcha_bypass=True).validate_policy_boundary()

    def test_happy_path_is_execution_eligible(self):
        controls = (
            self.c("first", "candidate.first_name"),
            self.c("email", "candidate.email"),
            self.c("cv", "document.cv", mode=ResolutionMode.ARTIFACT, artifact_ref="artifact:cv:v1"),
        )
        r = validate_pre_submit(self.request(controls), policy=self.policy, authority=self.authority_on)
        self.assertEqual(r.decision, ValidationDecision.ELIGIBLE)
        self.assertTrue(r.policy_eligible)
        self.assertTrue(r.execution_allowed)
        self.assertEqual(r.required_controls, 3)
        self.assertEqual(r.resolved_required_controls, 3)

    def test_r0_shadow_never_allows_click_even_when_policy_clean(self):
        controls = (self.c("email", "candidate.email"),)
        r = validate_pre_submit(
            self.request(controls, rollout_stage=RolloutStage.R0_SHADOW),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertEqual(r.decision, ValidationDecision.RUNTIME_DISABLED)
        self.assertTrue(r.policy_eligible)
        self.assertFalse(r.execution_allowed)

    def test_kill_switch_blocks_runtime_click_without_corrupting_policy_eligibility(self):
        controls = (self.c("email", "candidate.email"),)
        authority = SubmitAuthority(
            browser_external_form_write=True,
            final_submit_runtime_flag=True,
            kill_switch_engaged=True,
        )
        r = validate_pre_submit(self.request(controls), policy=self.policy, authority=authority)
        self.assertEqual(r.decision, ValidationDecision.RUNTIME_DISABLED)
        self.assertTrue(r.policy_eligible)

    def test_unknown_required_control_blocks(self):
        x = ControlResolution(
            control_key="mystery", label="Mystery Declaration", required=True,
            mapping_confidence=MappingConfidence.LOW,
        )
        r = validate_pre_submit(self.request((x,)), policy=self.policy, authority=self.authority_on)
        self.assertEqual(r.decision, ValidationDecision.BLOCKED)
        self.assertTrue(any("unknown required control" in b for b in r.blockers))

    def test_required_mapping_must_be_high_confidence(self):
        x = ControlResolution(
            control_key="email", label="Email", required=True, canonical_field="candidate.email",
            mapping_confidence=MappingConfidence.MEDIUM,
            resolution_mode=ResolutionMode.VERIFIED_FACT,
            provenance_ref="fact:email",
        )
        r = validate_pre_submit(self.request((x,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("not High-confidence" in b for b in r.blockers))

    def test_work_authorization_requires_explicit_verified_fact(self):
        bad = self.c("work", "work.authorization", explicit_user_fact=False)
        r = validate_pre_submit(self.request((bad,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("explicit verified fact" in b for b in r.blockers))
        good = self.c("work", "work.authorization", explicit_user_fact=True)
        r2 = validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(r2.policy_eligible)

    def test_sponsorship_requires_explicit_verified_fact(self):
        good = self.c("sponsor", "work.requires_sponsorship", explicit_user_fact=True)
        r = validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(r.policy_eligible)

    def test_salary_requires_preapproved_policy_reference(self):
        bad = self.c("salary", "compensation.salary_expectation")
        r = validate_pre_submit(self.request((bad,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("pre-approved" in b for b in r.blockers))
        good = self.c(
            "salary", "compensation.salary_expectation",
            mode=ResolutionMode.APPROVED_POLICY,
            policy_ref="salary-policy:nl:ba:v1",
            provenance_ref="",
        )
        r2 = validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(r2.policy_eligible)

    def test_required_privacy_ack_must_be_policy_covered(self):
        good = self.c(
            "privacy", "consent.privacy_acknowledgement",
            mode=ResolutionMode.APPROVED_POLICY,
            policy_ref="policy:A4",
            provenance_ref="",
        )
        self.assertTrue(validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on).policy_eligible)

    def test_optional_marketing_defaults_no_or_blank(self):
        good = self.c(
            "marketing", "consent.marketing_opt_in", required=False,
            mode=ResolutionMode.DEFAULT_NO, policy_ref="policy:A5", provenance_ref="",
        )
        self.assertTrue(validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on).policy_eligible)
        bad = self.c(
            "marketing", "consent.marketing_opt_in", required=False,
            mode=ResolutionMode.APPROVED_POLICY, policy_ref="policy:yes", provenance_ref="",
        )
        r = validate_pre_submit(self.request((bad,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("No or blank" in b for b in r.blockers))

    def test_optional_demographic_must_be_blank(self):
        good = self.c(
            "gender", "demographic.gender", required=False,
            mode=ResolutionMode.BLANK_OPTIONAL, policy_ref="policy:A6", provenance_ref="",
        )
        self.assertTrue(validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on).policy_eligible)

    def test_mandatory_demographic_requires_neutral_choice_available(self):
        bad = self.c(
            "gender", "demographic.gender", mode=ResolutionMode.NEUTRAL_CHOICE,
            policy_ref="policy:A6", provenance_ref="", option_available=False,
        )
        r = validate_pre_submit(self.request((bad,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("neutral choice" in b for b in r.blockers))
        good = self.c(
            "gender", "demographic.gender", mode=ResolutionMode.NEUTRAL_CHOICE,
            policy_ref="policy:A6", provenance_ref="", option_available=True,
        )
        self.assertTrue(validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on).policy_eligible)

    def test_generated_motivation_must_be_grounded_and_provenanced(self):
        bad = self.c(
            "motivation", "application.motivation_text",
            mode=ResolutionMode.GROUNDED_GENERATED, grounded=False,
        )
        r = validate_pre_submit(self.request((bad,)), policy=self.policy, authority=self.authority_on)
        self.assertTrue(any("not grounded" in b for b in r.blockers))
        good = self.c(
            "motivation", "application.motivation_text",
            mode=ResolutionMode.GROUNDED_GENERATED, grounded=True,
            provenance_ref="answer-snapshot:abc",
        )
        self.assertTrue(validate_pre_submit(self.request((good,)), policy=self.policy, authority=self.authority_on).policy_eligible)

    def test_form_drift_blocks(self):
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), drift_state=DriftState.DRIFT),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(any("drift" in b for b in r.blockers))

    def test_stale_runtime_inspection_blocks(self):
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), runtime_inspection_current=False),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(any("inspection is stale" in b for b in r.blockers))

    def test_stale_tracker_fingerprint_blocks(self):
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), tracker_fingerprint_current=False),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(any("tracker/opportunity fingerprint" in b for b in r.blockers))

    def test_technical_challenge_blocks(self):
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), technical_challenge="CAPTCHA"),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(any("CAPTCHA" in b for b in r.blockers))

    def test_duplicate_submit_key_is_suppressed(self):
        key = submit_identity("rituals:business-analyst", "7440001", "candidate-profile-v1")
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), existing_submit_keys=frozenset({key})),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertEqual(r.decision, ValidationDecision.DUPLICATE_SUPPRESSED)
        self.assertFalse(r.execution_allowed)

    def test_r2_daily_cap_is_one(self):
        r = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), submissions_today=1),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(any("daily submission cap reached (1)" in b for b in r.blockers))

    def test_r3_daily_cap_is_five(self):
        r = validate_pre_submit(
            self.request(
                (self.c("email", "candidate.email"),),
                rollout_stage=RolloutStage.R3_BOUNDED_ATS,
                submissions_today=4,
            ),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertTrue(r.execution_allowed)
        r2 = validate_pre_submit(
            self.request(
                (self.c("email", "candidate.email"),),
                rollout_stage=RolloutStage.R3_BOUNDED_ATS,
                submissions_today=5,
            ),
            policy=self.policy,
            authority=self.authority_on,
        )
        self.assertFalse(r2.execution_allowed)

    def test_attempt_creation_requires_execution_eligible_validation(self):
        req = self.request((self.c("email", "candidate.email"),))
        val = validate_pre_submit(req, policy=self.policy, authority=self.authority_on)
        attempt = create_submission_attempt(req, val, requested_at="2026-08-21T17:00:00+03:00")
        self.assertEqual(attempt.status, SubmissionAttemptStatus.VALIDATED)
        off_val = validate_pre_submit(
            self.request((self.c("email", "candidate.email"),), rollout_stage=RolloutStage.R0_SHADOW),
            policy=self.policy,
            authority=self.authority_on,
        )
        with self.assertRaises(PermissionError):
            create_submission_attempt(req, off_val, requested_at="2026-08-21T17:00:00+03:00")

    def test_applied_transition_requires_confirmation_evidence(self):
        req = self.request((self.c("email", "candidate.email"),))
        val = validate_pre_submit(req, policy=self.policy, authority=self.authority_on)
        attempt = create_submission_attempt(req, val, requested_at="2026-08-21T17:00:00+03:00")
        attempt = attempt.__class__(**{**attempt.__dict__, "status": SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED})
        allowed, _ = applied_transition_allowed(attempt, None)
        self.assertFalse(allowed)
        evidence = ConfirmationEvidence(
            submit_key=attempt.submit_key,
            evidence_type=ConfirmationType.CONFIRMATION_PAGE,
            evidence_ref="browser:confirmation:123",
            observed_at="2026-08-21T17:01:00+03:00",
            exact_identity_match=True,
            execution_id=attempt.execution_id,
            requisition_id=attempt.requisition_id,
        )
        allowed2, _ = applied_transition_allowed(attempt, evidence)
        self.assertTrue(allowed2)
        confirmed = mark_attempt_confirmed(attempt, evidence)
        self.assertEqual(confirmed.status, SubmissionAttemptStatus.CONFIRMED)

    def test_confirmation_mismatch_blocks_applied(self):
        req = self.request((self.c("email", "candidate.email"),))
        val = validate_pre_submit(req, policy=self.policy, authority=self.authority_on)
        attempt = create_submission_attempt(req, val, requested_at="2026-08-21")
        attempt = attempt.__class__(**{**attempt.__dict__, "status": SubmissionAttemptStatus.SUBMIT_CLICKED})
        evidence = ConfirmationEvidence(
            submit_key="submit:other",
            evidence_type=ConfirmationType.ATS_ACKNOWLEDGEMENT,
            evidence_ref="ats:ack",
            observed_at="2026-08-21",
            exact_identity_match=True,
        )
        self.assertFalse(applied_transition_allowed(attempt, evidence)[0])

    def test_submit_repository_is_immutable_and_dedupes_exact_retry(self):
        repo = InMemorySubmitRepository()
        self.assertTrue(repo.register_policy(self.policy))
        self.assertFalse(repo.register_policy(self.policy))
        req = self.request((self.c("email", "candidate.email"),))
        val = validate_pre_submit(req, policy=self.policy, authority=self.authority_on)
        self.assertTrue(repo.append_validation(val))
        self.assertFalse(repo.append_validation(val))
        attempt = create_submission_attempt(req, val, requested_at="2026-08-21")
        self.assertTrue(repo.reserve_attempt(attempt))
        self.assertFalse(repo.reserve_attempt(attempt))
        self.assertTrue(repo.has_submit_key(attempt.submit_key))

    def test_validation_collision_with_changed_payload_fails(self):
        repo = InMemorySubmitRepository()
        req = self.request((self.c("email", "candidate.email"),))
        val = validate_pre_submit(req, policy=self.policy, authority=self.authority_on)
        repo.append_validation(val)
        changed = val.__class__(**{**val.__dict__, "warnings": ("changed",)})
        with self.assertRaises(ValueError):
            repo.append_validation(changed)


if __name__ == "__main__":
    unittest.main()
