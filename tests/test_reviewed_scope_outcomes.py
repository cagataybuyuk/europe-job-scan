"""A new scoped confirmation can report without altering the first outcome."""
from copy import copy
from datetime import timedelta
import json

import pytest

from ejs.contracts.mw5 import opportunity_key
from ejs.contracts.submit import submit_identity
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence import reviewed_scope_ledger as scope_module
from ejs.persistence.reviewed_scope_ledger import claim_reviewed_preparation, register_reviewed_execution_scope
from ejs.services.application_outcome import write_confirmed_outcome
from ejs.services.central_application_outcome import (
    CentralApplicationBinding, central_binding_sha256, sync_confirmed_application,
)
from tests.test_central_application_outcome import HEADERS, SyntheticLockedTransport
from tests.test_reviewed_execution_scope import NOW, execution_case, issue_scope
from tests.test_reviewed_scope_ledger import make_legacy


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    legacy = make_legacy(tmp_path / "private" / "first.sqlite3")
    original = legacy.snapshot()
    case = execution_case(original)
    opp = opportunity_key("Devoteam", "Digital Impulse | Business Analyst", "Portugal")
    case["manifest"]["opportunity_key"] = opp
    scope = issue_scope(case)
    now = NOW + timedelta(seconds=4)
    monkeypatch.setattr(scope_module, "_now", lambda: now)
    assert register_reviewed_execution_scope(legacy, scope, now=NOW)
    view = claim_reviewed_preparation(legacy, scope, now=NOW)
    key = submit_identity(opp, "REF00063", "fixture:v2")
    attempt = SubmissionAttempt(key, "fixture:execution:second", opp, "REF00063", "fixture:v2",
        "SUBMIT-1.0", "validation:scoped", (NOW + timedelta(seconds=1)).isoformat(),
        SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:scoped")
    return legacy, original, view, case, key, attempt, now


def confirmed(scoped):
    legacy, original, view, case, key, attempt, now = scoped
    assert view.reserve_intent(attempt, NOW + timedelta(seconds=1))
    view.mark_started(key, "fixture:execution:second", NOW + timedelta(seconds=2))
    evidence = "evidence:sha256:" + "b" * 64
    assert view.confirm(key, ConfirmationEvidence(key, ConfirmationType.ATS_ACKNOWLEDGEMENT,
        evidence, (NOW + timedelta(seconds=3)).isoformat(), True,
        "fixture:execution:second", "REF00063"), now)
    assert legacy.snapshot() == original
    return evidence


def write(view, case, key, evidence):
    return write_confirmed_outcome(view.path.parent / "next-outcome.json", ledger=view,
        submit_key=key, execution_id="fixture:execution:second", requisition_id="REF00063",
        posting_url=case["manifest"]["target"]["posting_url"], evidence_ref=evidence)


def test_preparation_does_not_create_an_applied_outcome(scoped):
    _, _, view, case, key, _, _ = scoped
    with pytest.raises(PermissionError, match="APPLICATION_OUTCOME_REJECTED"):
        write(view, case, key, "evidence:sha256:" + "b" * 64)
    assert not (view.path.parent / "next-outcome.json").exists()


def test_confirmed_scoped_local_record_is_idempotent_and_preserves_legacy(scoped):
    legacy, original, view, case, key, _, _ = scoped
    evidence = confirmed(scoped)
    assert write(view, case, key, evidence)["local_outcome_write_performed"] is True
    assert write(view, case, key, evidence)["local_outcome_write_performed"] is False
    record = json.loads((view.path.parent / "next-outcome.json").read_text())
    assert record["requisition_id"] == "REF00063" and record["status"] == "Applied"
    assert legacy.snapshot() == original


def test_scoped_confirmation_syncs_once_through_the_exact_test_transport(scoped):
    legacy, original, view, case, key, attempt, now = scoped
    evidence = confirmed(scoped)
    binding = CentralApplicationBinding("synthetic_tracker_id_123456789", 77, 8, HEADERS,
        "Devoteam", "Digital Impulse | Business Analyst", "Portugal",
        case["manifest"]["target"]["posting_url"], attempt.opportunity_key,
        case["manifest"]["target"]["posting_url"], "REF00063", "fixture:v2", key,
        "fixture:execution:second", evidence, "a" * 40, now.isoformat())
    transport = SyntheticLockedTransport(binding)
    args = dict(ledger=view, binding=binding, binding_sha256=central_binding_sha256(binding),
        reviewed_source_sha="a" * 40, transport=transport, now=now)
    assert sync_confirmed_application(**args)["status"] == "CENTRAL_WRITE_DISABLED"
    assert transport.writes == 0
    assert sync_confirmed_application(**args, write_enabled=True)["status"] == "CENTRAL_RECORDED"
    assert sync_confirmed_application(**args, write_enabled=True)["status"] == "ALREADY_RECORDED"
    assert transport.writes == 1 and legacy.snapshot() == original


def test_copied_owner_cannot_publish_a_confirmed_scoped_result(scoped):
    _, _, view, case, key, _, _ = scoped
    evidence = confirmed(scoped)
    with pytest.raises(PermissionError, match="APPLICATION_OUTCOME_REJECTED"):
        write_confirmed_outcome(view.path.parent / "next-outcome.json", ledger=copy(view),
            submit_key=key, execution_id="fixture:execution:second", requisition_id="REF00063",
            posting_url=case["manifest"]["target"]["posting_url"], evidence_ref=evidence)
