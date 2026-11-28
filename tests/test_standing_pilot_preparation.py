"""A private review proposal assembled only from synthetic reviewed bytes."""

from dataclasses import replace
from datetime import timedelta, timezone
import io
import json
from zipfile import ZipFile

import pytest

from ejs.services.standing_pilot_preparation import (
    ERROR, PilotCandidateReview, PilotReview, prepare_pilot_draft,
)
from ejs.services.standing_guard import _PACKAGE_FILES
from ejs.services.standing_pilot_bundle import ERROR as BUNDLE_ERROR, build_pilot_review_bundle
from tests.test_execution_manifest import NOW, encode
from tests.test_standing_admission import POLICY, PROFILE, attestation_document, digest
from tests.test_standing_guard import EXPIRES, ISSUED, make_world
from tests.test_standing_guard import rig  # noqa: F401 - pytest fixture
from tests.test_standing_manifest_preparation import pin, review_for


def pilot_review(*, first_is_eligible=True, first_country="NL", second_country="DE"):
    first = make_world()
    second = make_world(second_target=True, execution_id="exec-20261117-example-2")
    first_row = first.handoff["candidates"][0]
    first_row["company"] = "Example Display Company GmbH"
    if not first_is_eligible:
        first_row["mandatory_qualification_lines"].append("Unknown synthetic qualification")
    second_row = second.handoff["candidates"][0]
    second_row.update(handoff_rank=2, company=first_row["company"])
    first.handoff["candidates"].append(second_row)
    second.handoff = first.handoff
    candidates = tuple(PilotCandidateReview(
        review_for(world), pin(encode(dict(attestation_document(world.template, encode(world.handoff)),
                                           country=country))))
        for world, country in ((first, first_country), (second, second_country)))
    # Supply reverse input order so the proposal has to use the pinned F05 rank.
    return PilotReview("env-synthetic-1", 3, ISSUED, EXPIRES, pin(POLICY), pin(PROFILE),
                       pin(encode(first.inventory)), tuple(reversed(candidates)))


def test_pilot_selects_country_of_first_eligible_vacancy_and_pins_only_selected_bytes():
    review = pilot_review(first_is_eligible=False)
    draft = prepare_pilot_draft(review, now=NOW)
    (entry,) = draft.packages
    approval = draft.approval_draft
    assert entry.ranked_target.rank == 2
    assert json.loads(approval.envelope)["country"] == "DE"
    assert json.loads(approval.eligibility_index)["entries"] == {entry.execution_id: digest(entry.inputs.eligibility)}
    assert json.loads(approval.pointer)["envelope_sha256"] == digest(approval.envelope)
    assert prepare_pilot_draft(review, now=NOW) == draft


def test_pilot_selects_both_attested_same_country_targets_in_f05_order():
    draft = prepare_pilot_draft(pilot_review(second_country="NL"), now=NOW)
    assert [item.ranked_target.rank for item in draft.packages] == [1, 2]
    assert json.loads(draft.approval_draft.envelope)["targets"] == [item.ranked_target.target for item in draft.packages]


def test_pilot_returns_no_draft_when_all_reviewed_targets_are_unmet():
    review = pilot_review(first_is_eligible=False)
    assert prepare_pilot_draft(replace(review, candidates=(review.candidates[-1],)), now=NOW) is None


@pytest.mark.parametrize("problem", ("different-handoff", "wrong-attestation-pin", "duplicate-id", "inventory-pin"))
def test_pilot_refuses_the_entire_draft_for_invalid_reviews(problem):
    review = pilot_review()
    second, first = review.candidates
    if problem == "different-handoff":
        changed = pin(first.manifest.handoff.raw + b"\n")
        first = replace(first, manifest=replace(first.manifest, handoff=changed))
    elif problem == "wrong-attestation-pin":
        first = replace(first, country_attestation=replace(first.country_attestation, sha256="0" * 64))
    elif problem == "duplicate-id":
        first = second
    else:
        review = replace(review, inventory=replace(review.inventory, sha256="0" * 64))
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        prepare_pilot_draft(replace(review, candidates=(second, first)), now=NOW)


def stage_in_test_queue(rig, draft):
    sources = draft.approval_draft
    for name, raw in (("envelope.json", sources.envelope),
                      ("eligibility-index.json", sources.eligibility_index), ("current.json", sources.pointer)):
        path = rig.approval / name
        path.write_bytes(raw)
        path.chmod(0o644)
        rig.view.register(path)
    for rank, item in enumerate(draft.packages, 1):
        inputs = vars(item.inputs) | vars(item.evidence)
        rig.write_package(item.execution_id, {key: inputs[key] for key, _, _ in _PACKAGE_FILES})
        rig.enqueue(item.inputs.manifest, f"{rank:02d}.json")


def test_prepared_batch_through_real_queue_and_test_trusted_reader(rig):
    draft = prepare_pilot_draft(pilot_review(second_country="NL"), now=NOW)
    stage_in_test_queue(rig, draft)
    reports, outbox = rig.run()
    assert [item.status for item in reports] == ["EMPLOYER_CONFIRMED", "CAP_REACHED"]
    confirmation = next(row for row in outbox.values() if row["status"] == "EMPLOYER_CONFIRMED")
    assert confirmation["execution_id"] == draft.packages[0].execution_id
    assert confirmation["manifest_sha256"] == digest(draft.packages[0].inputs.manifest)
    assert confirmation["evidence_ref"].startswith("evidence:sha256:")
    assert sum(browser.clicks for browser in rig.browsers) == 1  # fake browser only


def test_prepared_batch_loses_admission_without_pinned_index(rig):
    draft = prepare_pilot_draft(pilot_review(), now=NOW)
    stage_in_test_queue(rig, draft)
    (rig.approval / "eligibility-index.json").unlink()
    (report,), outbox = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_STANDING_ADMISSION_DENIED")
    assert (next(iter(outbox.values()))["submit_clicks"], rig.browsers) == (0, [])


def test_prepared_pilot_recovers_exact_confirmation_after_outbox_failure(rig, monkeypatch):
    draft = prepare_pilot_draft(pilot_review(), now=NOW)
    stage_in_test_queue(rig, draft)

    def fail_outbox(*args, **kwargs):
        raise OSError("synthetic outbox failure after ledger commit")

    with monkeypatch.context() as patch:
        patch.setattr(rig.queue, "_emit", fail_outbox)
        (report,), outbox = rig.run()
    assert report.status == "STUCK" and outbox == {}
    (report,), outbox = rig.run()
    assert (report.status, report.code) == ("EMPLOYER_CONFIRMED", "ENGINE_INTERRUPTED")
    (confirmation,) = outbox.values()
    raw = draft.packages[0].inputs.manifest
    assert confirmation["manifest_sha256"] == digest(raw)
    assert confirmation["evidence_ref"] == "evidence:sha256:" + "e" * 64
    assert sum(browser.clicks for browser in rig.browsers) == 1


def test_review_zip_is_reproducible_and_contains_only_exact_private_draft_files():
    draft = prepare_pilot_draft(pilot_review(second_country="NL"), now=NOW)
    raw = build_pilot_review_bundle(draft, now=NOW)
    assert build_pilot_review_bundle(draft, now=NOW) == raw
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = {entry.filename: entry for entry in archive.infolist()}
        assert len(entries) == 24
        assert all(entry.external_attr >> 16 & 0o777 == 0o600 for entry in entries.values())
        assert not any(name.startswith(("inbox/", "control/")) or name.endswith(".pdf") for name in entries)
        assert archive.read("approval-draft/current.json") == draft.approval_draft.pointer
        assert archive.read("manifest-drafts/01.json") == draft.packages[0].inputs.manifest
        sums = archive.read("SHA256SUMS").decode("ascii").splitlines()
        assert {line.split("  ", 1)[1] for line in sums} == entries.keys() - {"SHA256SUMS"}
        assert all(digest(archive.read(name)) == expected
                   for expected, name in (line.split("  ", 1) for line in sums))


def test_review_zip_refuses_an_incomplete_or_modified_proposal():
    draft = prepare_pilot_draft(pilot_review(second_country="NL"), now=NOW)
    first, second = draft.packages
    changed = replace(first, inputs=replace(first.inputs, manifest=first.inputs.manifest + b"\n"))
    for incomplete in ((first,), (first, first), (changed, second)):
        with pytest.raises(PermissionError, match=f"^{BUNDLE_ERROR}$"):
            build_pilot_review_bundle(replace(draft, packages=incomplete), now=NOW)


@pytest.mark.parametrize("bad_times", (
    dict(issued_at=ISSUED.replace(tzinfo=None)),
    dict(issued_at=ISSUED.astimezone(timezone(timedelta(hours=3)))),
    dict(expires_at=EXPIRES.replace(tzinfo=None)),
    dict(expires_at=EXPIRES.astimezone(timezone(timedelta(hours=3)))),
    dict(issued_at=NOW - timedelta(minutes=45, seconds=1)),  # before preparation
    dict(issued_at=NOW - timedelta(minutes=40, seconds=1)),  # before attestation
))
def test_pilot_rejects_non_utc_or_premature_envelope_chronology(bad_times):
    review = replace(pilot_review(), **bad_times)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        prepare_pilot_draft(review, now=NOW)


@pytest.mark.parametrize("bad_now", (
    NOW.replace(tzinfo=None),
    NOW.astimezone(timezone(timedelta(hours=3))),
))
def test_pilot_rejects_non_utc_clock(bad_now):
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        prepare_pilot_draft(pilot_review(), now=bad_now)
