"""Synthetic reviews only. No inspector, network or production files."""

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from ejs.services.execution_manifest import form_fingerprint
from ejs.services.standing_manifest_preparation import (
    ERROR, OBSERVATION_SCHEMA, ReviewedBytes, StandingManifestReview, prepare_standing_manifest,
)
from tests.test_execution_manifest import NOW, OBSERVED, encode
from tests.test_standing_admission import World, digest


def pin(raw):
    return ReviewedBytes(raw, digest(raw))


def review_for(world):
    template, registry, snapshot, _, handoff, _ = world.raw()
    form = world.template["form"]
    observation = dict(schema_version=OBSERVATION_SCHEMA, target=world.template["target"],
                       source_detail_sha256=world.handoff["candidates"][0]["detail_sha256"],
                       observed_at=form["observed_at"], final_page_observed=True,
                       fields=[{key: value for key, value in field.items()
                                if key not in ("answer", "canonical_field")} for field in form["fields"]],
                       cv_upload=form["cv_upload"])
    return StandingManifestReview(pin(handoff), pin(encode(observation)), pin(template),
                                  pin(registry), pin(snapshot), world.inputs["cv"])


@pytest.mark.parametrize("later_page", (False, True))
def test_review_prepares_exact_manifest_for_a_complete_form(later_page):
    world = World()
    world.template["cv"]["size_bytes"] = len(world.inputs["cv"])
    world.handoff["candidates"][0]["company"] = "Example Display Company GmbH"
    if later_page:
        form = world.template["form"]
        form["fields"][-1]["step"] = 1
        form["fingerprint"] = form_fingerprint(form["fields"], form["cv_upload"])
        world.snapshot["form_fingerprint"] = form["fingerprint"]
    result = prepare_standing_manifest(review_for(world), now=NOW)
    assert result.raw == world.build()[1].manifest
    assert result.vacancy.employer_display_name == "Example Display Company GmbH"
    assert result.manifest.step_count == (2 if later_page else 1)


@pytest.mark.parametrize("edit", (
    lambda o: o["target"].update(company="WrongTenant"),
    lambda o: o["target"].update(opportunity_key="opp:other"),
    lambda o: o.update(source_detail_sha256="0" * 64),
    lambda o: o.update(observed_at=(OBSERVED - timedelta(seconds=1)).isoformat()),
    lambda o: o.update(final_page_observed=False),
    lambda o: o["fields"].pop(),
    lambda o: o["fields"][0].update(label="Changed label"),
    lambda o: o["fields"][0].update(answer="Synthetic answer"),
    lambda o: o["cv_upload"].update(required=False),
    lambda o: o.update(cv_upload=None),
), ids=("tenant", "opportunity", "detail", "time", "partial", "missing-control", "changed-label",
        "has-answer", "optional-upload", "missing-upload"))
def test_review_denies_form_drift_even_when_observation_hash_is_updated(edit):
    world = World()
    world.template["cv"]["size_bytes"] = len(world.inputs["cv"])
    review = review_for(world)
    observation = json.loads(review.observation.raw)
    edit(observation)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        prepare_standing_manifest(replace(review, observation=pin(encode(observation))), now=NOW)


def test_review_denies_wrong_observation_bytes_and_wrong_cv():
    world = World()
    world.template["cv"]["size_bytes"] = len(world.inputs["cv"])
    review = review_for(world)
    changed = replace(review.observation, raw=review.observation.raw + b"\n")
    for bad in (replace(review, observation=changed), replace(review, cv=b"%PDF-other synthetic")):
        with pytest.raises(PermissionError, match=f"^{ERROR}$"):
            prepare_standing_manifest(bad, now=NOW)
