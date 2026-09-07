from __future__ import annotations

import unittest

from ejs.contracts.tmh import (
    CANONICAL_SCHEMA_VERSION,
    TmhAuthority,
    artifact_key,
    execution_event_key,
    execution_identity,
    mapping_identity,
    template_identity,
)
from ejs.domain.template_history import (
    CanonicalFieldDefinition,
    DriftState,
    ExecutionEvent,
    FieldMappingRecord,
    MappingConfidence,
    ObservedControl,
    PromotionState,
    RuntimeFormObservation,
    SubmissionArtifact,
    TemplateVersionRecord,
    mapping_promotion_state,
)
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.tmh_registry import (
    dynamic_mapping_allowed,
    promotion_candidate,
    register_mapping,
    register_template,
    runtime_drift_gate,
)


class Tmh1HistoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryTmhRepository()
        self.auth = TmhAuthority()

    def test_authority_forbids_business_or_submit_writes(self):
        with self.assertRaises(PermissionError):
            TmhAuthority(applications_write=True).validate()
        with self.assertRaises(PermissionError):
            TmhAuthority(final_submit=True).validate()
        with self.assertRaises(PermissionError):
            TmhAuthority(external_form_write=True).validate()

    def test_template_identity_decouples_from_discovery_source(self):
        self.assertEqual(template_identity("SmartRecruiters", "Rituals"), "tmpl:smartrecruiters:rituals")
        self.assertEqual(template_identity("SmartRecruiters", "Rituals"), template_identity("SmartRecruiters", "Rituals"))

    def test_mapping_identity_is_semantic_and_deterministic(self):
        a = mapping_identity(label="First Name", control_type="text", context="smartrecruiters", canonical_field="candidate.first_name")
        b = mapping_identity(label="  first   name ", control_type="text", context="smartrecruiters", canonical_field="candidate.first_name")
        self.assertEqual(a, b)

    def test_execution_identity_requires_attempt_and_requisition(self):
        self.assertTrue(execution_identity("rituals:ba", "7440001", 1).startswith("exec:"))
        with self.assertRaises(ValueError):
            execution_identity("rituals:ba", "", 1)
        with self.assertRaises(ValueError):
            execution_identity("rituals:ba", "7440001", 0)

    def test_template_version_is_append_only(self):
        rec = TemplateVersionRecord(
            template_id="tmpl:smartrecruiters:global", adapter_family="SmartRecruiters", employer_scope="global",
            template_type="ATS Base", version="1.0", status="Active"
        )
        self.assertTrue(register_template(self.repo, rec, self.auth))
        self.assertFalse(register_template(self.repo, rec, self.auth))
        with self.assertRaises(ValueError):
            register_template(self.repo, TemplateVersionRecord(**{**rec.__dict__, "notes": "changed"}), self.auth)

    def test_mapping_version_is_append_only(self):
        rec = FieldMappingRecord(
            mapping_id="map:first", normalized_label_value="first name", control_type="text", context="generic",
            employer_scope="global", canonical_field="candidate.first_name", mapping_version="1.0",
            confidence=MappingConfidence.HIGH, status="Active", provenance="reviewed seed", evidence_count=3,
            first_observed="2026-08-21", last_observed="2026-08-21", promotion_state=PromotionState.PROMOTED,
        )
        self.assertTrue(register_mapping(self.repo, rec, self.auth))
        self.assertFalse(register_mapping(self.repo, rec, self.auth))
        with self.assertRaises(ValueError):
            register_mapping(self.repo, FieldMappingRecord(**{**rec.__dict__, "canonical_field": "candidate.last_name"}), self.auth)

    def test_mapping_promotion_requires_repeated_evidence_or_review(self):
        self.assertEqual(mapping_promotion_state(evidence_count=1, explicitly_reviewed=False), PromotionState.RUNTIME_ONLY)
        self.assertEqual(mapping_promotion_state(evidence_count=2, explicitly_reviewed=False), PromotionState.CANDIDATE)
        self.assertEqual(mapping_promotion_state(evidence_count=3, explicitly_reviewed=False), PromotionState.PROMOTED)
        self.assertEqual(mapping_promotion_state(evidence_count=1, explicitly_reviewed=True), PromotionState.REVIEWED)

    def test_medium_confidence_does_not_auto_promote_from_repetition(self):
        rec = FieldMappingRecord(
            mapping_id="map:x", normalized_label_value="experience", control_type="text", context="generic",
            employer_scope="global", canonical_field="experience.years", mapping_version="1.0",
            confidence=MappingConfidence.MEDIUM, status="Active", provenance="runtime", evidence_count=4,
            first_observed="2026-08-21", last_observed="2026-08-21", promotion_state=PromotionState.RUNTIME_ONLY,
        )
        self.assertEqual(promotion_candidate(rec).promotion_state, PromotionState.CANDIDATE)

    def test_live_form_fingerprint_is_deterministic(self):
        obs = RuntimeFormObservation(
            opportunity_key="rituals:ba", requisition_id="744", apply_url="https://example.test", ats_family="SmartRecruiters",
            observed_at="2026-08-21", controls=(ObservedControl("First Name", "text", True), ObservedControl("CV", "file", True))
        )
        obs2 = RuntimeFormObservation(**{**obs.__dict__})
        self.assertEqual(obs.fingerprint, obs2.fingerprint)

    def test_runtime_drift_blocks_template_execution(self):
        obs = RuntimeFormObservation(
            opportunity_key="rituals:ba", requisition_id="744", apply_url="https://example.test", ats_family="SmartRecruiters",
            observed_at="2026-08-21", controls=(ObservedControl("First Name", "text", True),)
        )
        tmpl = TemplateVersionRecord(
            template_id="tmpl:smartrecruiters:global", adapter_family="SmartRecruiters", employer_scope="global",
            template_type="ATS Base", version="1.0", status="Active", baseline_fingerprint="different"
        )
        state, allowed, reason = runtime_drift_gate(template=tmpl, observation=obs)
        self.assertEqual(state, DriftState.DRIFT)
        self.assertFalse(allowed)
        self.assertIn("re-inspection", reason)

    def test_no_template_baseline_does_not_block_runtime_inspection(self):
        obs = RuntimeFormObservation(
            opportunity_key="x", requisition_id="1", apply_url="https://example.test", ats_family="Generic",
            observed_at="2026-08-21", controls=(ObservedControl("Email", "email", True),)
        )
        state, allowed, _ = runtime_drift_gate(template=None, observation=obs)
        self.assertEqual(state, DriftState.NO_BASELINE)
        self.assertTrue(allowed)

    def test_dynamic_required_mapping_requires_high_confidence(self):
        self.assertTrue(dynamic_mapping_allowed(MappingConfidence.HIGH, required=True))
        self.assertFalse(dynamic_mapping_allowed(MappingConfidence.MEDIUM, required=True))
        self.assertTrue(dynamic_mapping_allowed(MappingConfidence.MEDIUM, required=False))

    def test_execution_log_is_append_only_and_ordered(self):
        eid = execution_identity("rituals:ba", "744", 1)
        e1 = ExecutionEvent(eid, 1, "2026-08-21T17:00", "rituals:ba", "744", 1, "Inspection Started")
        e2 = ExecutionEvent(eid, 2, "2026-08-21T17:01", "rituals:ba", "744", 1, "Inspection Completed")
        self.assertTrue(self.repo.append_execution_event(e1))
        self.assertTrue(self.repo.append_execution_event(e2))
        self.assertFalse(self.repo.append_execution_event(e2))
        self.assertEqual([x.sequence for x in self.repo.events_for_execution(eid)], [1, 2])
        self.assertEqual(execution_event_key(eid, 2, "Inspection Completed"), next(k for k in self.repo.execution_events if k.endswith(":2:inspection-completed")))

    def test_execution_event_key_collision_with_changed_payload_fails(self):
        eid = execution_identity("rituals:ba", "744", 1)
        e = ExecutionEvent(eid, 1, "2026-08-21T17:00", "rituals:ba", "744", 1, "Inspection")
        self.repo.append_execution_event(e)
        with self.assertRaises(ValueError):
            self.repo.append_execution_event(ExecutionEvent(**{**e.__dict__, "result": "changed"}))

    def test_artifact_registry_is_immutable(self):
        eid = execution_identity("rituals:ba", "744", 1)
        art = SubmissionArtifact(
            execution_id=eid, artifact_type="CV", artifact_version="v4", content_hash="abc123",
            source_ref="CV Base Library", asset_ref="drive:file", captured_at="2026-08-21"
        )
        self.assertTrue(self.repo.append_artifact(art))
        self.assertFalse(self.repo.append_artifact(art))
        with self.assertRaises(ValueError):
            self.repo.append_artifact(SubmissionArtifact(**{**art.__dict__, "asset_ref": "drive:other"}))
        self.assertTrue(artifact_key(eid, "CV", "abc123").startswith("artifact:"))

    def test_canonical_schema_cannot_be_silently_overwritten(self):
        field = CanonicalFieldDefinition(
            field_key="candidate.first_name", category="Identity", data_type="string", ownership_mode="Verified Fact",
            source_of_truth="Candidate Profile", auto_fill_allowed=True, auto_submit_eligible=True,
            review_requirement="None", reuse_scope="Global", null_behavior="Block if required",
            schema_version=CANONICAL_SCHEMA_VERSION,
        )
        self.assertTrue(self.repo.put_canonical_field(field))
        self.assertFalse(self.repo.put_canonical_field(field))
        with self.assertRaises(ValueError):
            self.repo.put_canonical_field(CanonicalFieldDefinition(**{**field.__dict__, "auto_submit_eligible": False}))

    def test_low_confidence_promoted_mapping_is_rejected(self):
        rec = FieldMappingRecord(
            mapping_id="map:bad", normalized_label_value="legal", control_type="select", context="generic",
            employer_scope="global", canonical_field="legal.unknown", mapping_version="1.0",
            confidence=MappingConfidence.LOW, status="Active", provenance="runtime", evidence_count=5,
            first_observed="2026-08-21", last_observed="2026-08-21", promotion_state=PromotionState.PROMOTED,
        )
        with self.assertRaises(ValueError):
            register_mapping(self.repo, rec, self.auth)


if __name__ == "__main__":
    unittest.main()
