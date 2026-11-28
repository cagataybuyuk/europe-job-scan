"""Synthetic offline coverage for W36-02 S2 admission and the W36-03 inventory check.

Every value is a clearly synthetic placeholder. No candidate fact, owner
country, target list or real inventory content is used. Acceptance here is a
pure offline verdict and authorizes nothing.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os

import pytest

from ejs.services import inventory_eligibility, standing_admission
from ejs.services.inventory_eligibility import (
    MATCHING_RULE, EligibilityResult, canonical_text, evaluate_inventory_eligibility, recompute,
)
from ejs.services.standing_admission import (
    ERROR, AdmissionDecision, AdmissionEvidence, admit_standing_batch,
)
from ejs.services.standing_envelope import BatchApprovalSources, BatchBinding, BatchInputs
from tests.test_execution_manifest import NOW, OBSERVED, PREPARED, encode
from tests.test_reviewed_manifest_compiler import inputs as compiler_inputs


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


CV = b"%PDF-1.4 synthetic cv for S2"
POLICY = b'{"synthetic":"policy"}'
PROFILE = b'{"synthetic":"profile"}'
COUNTRY = "NL"  # a synthetic fixture value; the owner country is not configured
EVIDENCE_REF = "evidence:cv:base-erp:experience"
LINES = ["Synthetic requirement A: fluent synthetic language",
         "Synthetic requirement B: synthetic certificate required"]


def compiler_documents():
    """Template, registry and snapshot from the existing synthetic compiler fixture."""
    _, template, registry, snap = compiler_inputs()
    template["policy"] = dict(version="SUBMIT-1.0", rollout_stage="R2", daily_cap=1)
    template["cv"]["sha256"] = snap["cv_sha256"] = digest(CV)
    return template, registry, snap


def inventory_document():
    entry = dict(held=True, source_ref="candidate_verified:synthetic-inventory",
                 verified=True, verified_at=PREPARED.isoformat())
    return dict(
        schema_version="EJS-QUALIFICATION-INVENTORY-1",
        candidate_profile_version="profile-2026-11-16", matching_rule_version=MATCHING_RULE,
        entries=[
            dict(entry, key="synthetic.language.a", requirement_texts=[LINES[0]]),
            dict(entry, key="synthetic.certificate.b", requirement_texts=[LINES[1]]),
            dict(entry, key="synthetic.licence.z", held=False,
                 requirement_texts=["Synthetic requirement Z: synthetic licence mandatory"]),
        ],
        evidence_texts=[dict(ref=EVIDENCE_REF, text_sha256=digest(b"Synthetic grounded answer."),
                             entry_key="synthetic.language.a")],
    )


def handoff_document(template):
    target = template["target"]
    candidate = dict(
        handoff_rank=1, status="candidate_for_review", status_reasons=[], ats="smartrecruiters",
        region="global", tenant="ExampleCo", posting_id=target["posting_id"],
        canonical_url=target["apply_url"], requisition_id=target["requisition_id"],
        company=target["company"], role="Synthetic Analyst", location="Synthetic City",
        detail_sha256="d" * 64, observed_at=OBSERVED.strftime("%Y-%m-%dT%H:%M:%SZ"),
        evidence_origin="live_employer_response", mandatory_qualification_lines=list(LINES),
        candidate_qualification_match="not_performed_requires_verified_candidate_facts",
    )
    return dict(schema="EJS-F06-HANDOFF-1", mode="function06_handoff_non_executable",
                executable=False, submit_authorized=False,
                evidence_origin="live_employer_response", real_evidence=True,
                function06_eligible=True, candidates=[candidate])


ATTESTED = OBSERVED + timedelta(minutes=10)


def attestation_document(template, handoff_raw):
    """Synthetic owner-pinned vacancy-country attestation; its review is an owner procedure."""
    return dict(schema_version="EJS-VACANCY-COUNTRY-ATTESTATION-1",
                execution_id=template["execution_id"], target=deepcopy(template["target"]),
                country=COUNTRY, source_ref=f"sha256:{digest(handoff_raw)}",
                attested_at=ATTESTED.isoformat())


def package_document(template, inventory_raw, handoff_raw, snapshot_raw, attestation_raw=None):
    if attestation_raw is None:
        attestation_raw = encode(attestation_document(template, handoff_raw))
    return dict(
        schema_version="EJS-BATCH-ELIGIBILITY-1", envelope_id="env-synthetic-1", generation=3,
        execution_id=template["execution_id"],
        candidate_profile_version=template["candidate_profile_version"], country=COUNTRY,
        target=deepcopy(template["target"]), matching_rule_version=MATCHING_RULE,
        inventory_sha256=digest(inventory_raw), handoff_sha256=digest(handoff_raw),
        snapshot_sha256=digest(snapshot_raw), country_attestation_sha256=digest(attestation_raw),
        requirements=[dict(text=LINES[0], result="met", inventory_key="synthetic.language.a"),
                      dict(text=LINES[1], result="met", inventory_key="synthetic.certificate.b")],
    )


class World:
    """One fully pinned synthetic batch. Edit documents, then call build()."""

    def __init__(self):
        self.template, self.registry, self.snapshot = compiler_documents()
        self.inventory = inventory_document()
        self.handoff = handoff_document(self.template)
        self.package = None  # derived unless a test overrides it
        self.attestation = None  # derived unless a test overrides it (dict or bytes)
        self.envelope = {}
        self.index_entries = None
        self.manifest = None  # compiled unless a test overrides it
        self.inputs = dict(cv=CV, policy=POLICY, profile=PROFILE, country=COUNTRY,
                           matching_rule_version=MATCHING_RULE)

    def raw(self):
        template, registry, snapshot = (encode(self.template), encode(self.registry),
                                        encode(self.snapshot))
        inventory, handoff = encode(self.inventory), encode(self.handoff)
        package = self.package
        if package is None:
            package = package_document(self.template, inventory, handoff, snapshot,
                                       self.attestation_raw(handoff))
        package = package if isinstance(package, bytes) else encode(package)
        return template, registry, snapshot, inventory, handoff, package

    def attestation_raw(self, handoff):
        attestation = self.attestation
        if attestation is None:
            attestation = attestation_document(self.template, handoff)
        return attestation if isinstance(attestation, bytes) else encode(attestation)

    def build(self):
        template, registry, snapshot, inventory, handoff, package = self.raw()
        if self.manifest is None:
            manifest = standing_admission.compile_reviewed_manifest(
                template, registry, snapshot, expected_template_sha256=digest(template),
                expected_registry_sha256=digest(registry),
                expected_snapshot_sha256=digest(snapshot), now=NOW)
        else:
            manifest = self.manifest
        envelope = dict(
            schema_version="EJS-STANDING-ENVELOPE-1", envelope_id="env-synthetic-1",
            generation=3, status="active", issued_at=(NOW - timedelta(minutes=5)).isoformat(),
            expires_at=(NOW + timedelta(hours=23)).isoformat(), policy_version="SUBMIT-1.0",
            candidate_profile_version="profile-2026-11-16", ats_family="smartrecruiters",
            country=COUNTRY, targets=[deepcopy(self.template["target"])], rollout_stages=["R2"],
            daily_cap=1, daily_timezone="Europe/Istanbul", matching_rule_version=MATCHING_RULE,
            template_sha256s=[digest(template)], policy_sha256=digest(POLICY),
            profile_sha256=digest(PROFILE), cv_sha256=digest(CV),
            registry_sha256=digest(registry), inventory_sha256=digest(inventory),
        )
        envelope.update(self.envelope)
        entries = self.index_entries or {self.template["execution_id"]: digest(package)}
        index = dict(schema_version="EJS-BATCH-ELIGIBILITY-INDEX-1",
                     envelope_id="env-synthetic-1", generation=3, entries=entries)
        envelope_raw, index_raw = encode(envelope), encode(index)
        pointer = dict(schema_version="EJS-STANDING-ENVELOPE-POINTER-1",
                       envelope_id="env-synthetic-1", generation=3,
                       envelope_sha256=digest(envelope_raw),
                       eligibility_index_sha256=digest(index_raw))
        sources = BatchApprovalSources(encode(pointer), envelope_raw, index_raw)
        values = dict(manifest=manifest, template=template, registry=registry,
                      inventory=inventory, eligibility=package)
        values.update(self.inputs)
        return sources, BatchInputs(**values), AdmissionEvidence(
            snapshot, handoff, self.attestation_raw(handoff))


def admit(world, now=NOW):
    sources, inputs, evidence = world.build()
    return admit_standing_batch(sources, inputs, evidence, now=now)


def reject(world=None, *, built=None, now=NOW):
    sources, inputs, evidence = built or world.build()
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        admit_standing_batch(sources, inputs, evidence, now=now)
    assert error.value.__cause__ is error.value.__context__ is None
    return error.value


# --- positive control ------------------------------------------------------------------

def test_accepts_exact_synthetic_batch_and_returns_offline_decision_only():
    world = World()
    sources, inputs, evidence = world.build()
    decision = admit_standing_batch(sources, inputs, evidence, now=NOW)
    assert decision == AdmissionDecision(
        BatchBinding("env-synthetic-1", 3, digest(sources.envelope),
                     digest(sources.eligibility_index)),
        world.template["execution_id"], digest(inputs.manifest))
    assert "synthetic" not in repr(decision) and "synthetic" not in repr(evidence)


def test_admission_is_pure_and_does_not_touch_the_filesystem(monkeypatch):
    built = World().build()

    def forbidden(*args, **kwargs):
        pytest.fail("filesystem access during pure admission")
    for name in ("open", "stat", "lstat", "listdir", "scandir"):
        monkeypatch.setattr(os, name, forbidden)
    assert admit_standing_batch(*built, now=NOW)


def test_matches_employer_display_name_to_exact_ats_tenant_vacancy():
    world = World()
    name = "Example Research & Consulting GmbH"
    world.handoff["candidates"][0]["company"] = name
    handoff = encode(world.handoff)
    bound = inventory_eligibility.bind_f05_vacancy(
        handoff, world.template["target"], expected_handoff_sha256=digest(handoff), now=NOW)
    assert bound.employer_display_name == name
    assert bound.mandatory_lines == tuple(LINES)
    args = _draft_inputs(world)
    world.package = inventory_eligibility.draft_eligibility_package(**args)
    assert admit(world).execution_id == world.template["execution_id"]


@pytest.mark.parametrize("changes", (
    {"tenant": "WrongTenant"}, {"posting_id": "744000000000009"},
    {"requisition_id": "wrong-requisition"}, {"region": "eu"}, {"company": ""},
))
def test_f05_binding_requires_exact_vacancy_identity(changes):
    world = World()
    world.handoff["candidates"][0].update(changes)
    raw = encode(world.handoff)
    with pytest.raises(PermissionError, match=inventory_eligibility.ERROR):
        inventory_eligibility.bind_f05_vacancy(
            raw, world.template["target"], expected_handoff_sha256=digest(raw), now=NOW)
    reject(world)


def test_f05_binding_rejects_stale_pin_and_company_slug_conflict():
    world = World()
    original = encode(world.handoff)
    world.handoff["candidates"][0]["company"] = "Different Display Name"
    raw = encode(world.handoff)
    target = world.template["target"]
    for supplied, pin in ((target, digest(original)), (dict(target, company="OtherTenant"), digest(raw))):
        with pytest.raises(PermissionError, match=inventory_eligibility.ERROR):
            inventory_eligibility.bind_f05_vacancy(raw, supplied, expected_handoff_sha256=pin, now=NOW)


def _draft_inputs(world):
    _, _, snapshot, inventory, handoff, _ = world.raw()
    _, inputs, evidence = world.build()
    return dict(inventory_raw=inventory, handoff_raw=handoff,
                attestation_raw=evidence.country_attestation, manifest_raw=inputs.manifest,
                snapshot_raw=snapshot, expected_inventory_sha256=digest(inventory),
                envelope_id="env-synthetic-1", generation=3, now=NOW)


def test_eligibility_draft_is_deterministic_and_round_trips_through_standing_admission():
    world = World()
    args = _draft_inputs(world)
    draft = inventory_eligibility.draft_eligibility_package
    world.package = raw = draft(**args)
    assert raw == draft(**args)
    assert json.loads(raw)["requirements"] == json.loads(World().raw()[-1])["requirements"]
    assert admit_standing_batch(*world.build(), now=NOW).execution_id == world.template["execution_id"]


@pytest.mark.parametrize("problem", ("unknown", "unmet", "synthetic", "stale", "wrong-attestation",
                                     "unresolved-text", "inventory-pin", "oversize-snapshot"))
def test_eligibility_draft_rejects_unreviewable_inputs(problem):
    world = World()
    if problem == "unknown":
        world.inventory["entries"][1]["requirement_texts"] = ["Unrelated synthetic text"]
    elif problem == "unmet":
        world.inventory["entries"][1]["held"] = False
    elif problem == "synthetic":
        world.handoff["evidence_origin"] = "offline_replay_synthetic"
    elif problem == "stale":
        world.handoff["candidates"][0]["observed_at"] = (NOW - timedelta(days=3)).isoformat()
    elif problem == "unresolved-text":
        world.inventory["evidence_texts"] = []
    args = _draft_inputs(world)
    if problem == "wrong-attestation":
        args["attestation_raw"] = encode(dict(json.loads(args["attestation_raw"]), source_ref="sha256:" + "0" * 64))
    elif problem == "inventory-pin":
        args["expected_inventory_sha256"] = "0" * 64
    elif problem == "oversize-snapshot":
        args["snapshot_raw"] = b"x" * (inventory_eligibility.MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(PermissionError, match=f"^{inventory_eligibility.ERROR}$"):
        inventory_eligibility.draft_eligibility_package(**args)


def test_does_not_use_the_24_hour_current_fact_approval_verifier():
    source = open(standing_admission.__file__, encoding="utf-8").read()
    source += open(inventory_eligibility.__file__, encoding="utf-8").read()
    assert "resolve_current_fact_inputs" not in source and "compile_current_manifest" not in source
    assert "environ" not in source and "getenv" not in source


def test_errors_do_not_echo_values():
    world = World()
    world.inputs["country"] = "SECRET-COUNTRY"
    assert "SECRET" not in repr(reject(world).args)


# --- recompilation equality ------------------------------------------------------------

def test_rejects_manifest_bytes_that_differ_from_the_recompilation():
    world = World()
    sources, inputs, evidence = world.build()
    # Same JSON value, different bytes: the S1 binding alone would accept this.
    pretty = json.dumps(json.loads(inputs.manifest), indent=1).encode("utf-8")
    reject(built=(sources, replace(inputs, manifest=pretty), evidence))


def test_rejects_manifest_edited_and_consistently_refingerprinted():
    world = World()
    template, registry, snapshot, *_ = world.raw()
    edited = deepcopy(world.template)
    edited["form"]["fields"][-1]["answer"]["value"] = "Synthetic rewritten answer."
    for field in edited["form"]["fields"]:
        if field["answer"]["mode"] == "Verified Fact":
            field["answer"].update(value=None, source_ref=None)
    world.manifest = standing_admission.compile_reviewed_manifest(
        encode(edited), registry, snapshot, expected_template_sha256=digest(encode(edited)),
        expected_registry_sha256=digest(registry), expected_snapshot_sha256=digest(snapshot),
        now=NOW)
    reject(world)


def test_rejects_snapshot_not_pinned_by_the_package():
    world = World()
    sources, inputs, evidence = world.build()
    reject(built=(sources, inputs, replace(evidence, snapshot=evidence.snapshot + b" ")))


@pytest.mark.parametrize("evidence", (None, (b"", b"", b""), "text"))
def test_rejects_missing_or_malformed_evidence(evidence):
    sources, inputs, real = World().build()
    if isinstance(evidence, tuple):
        evidence = AdmissionEvidence(*evidence)
    reject(built=(sources, inputs, evidence))


# --- scope (S1 composition) ------------------------------------------------------------

@pytest.mark.parametrize("change", (
    dict(daily_cap=2), dict(rollout_stages=["R3"]), dict(ats_family="greenhouse"),
    dict(country="DE"), dict(country=None), dict(targets=[]),
    dict(matching_rule_version="match-synthetic-1"),
))
def test_rejects_scope_outside_envelope(change):
    world = World()
    world.envelope = change
    reject(world)


def test_rejects_cap_exceeded_by_manifest():
    world = World()
    world.template["policy"]["daily_cap"] = 2
    world.template["policy"]["rollout_stage"] = "R3"
    world.envelope = dict(rollout_stages=["R2", "R3"])
    reject(world)


@pytest.mark.parametrize("country", (None, "", "DE", "nl"))
def test_rejects_other_or_missing_country(country):
    world = World()
    world.inputs["country"] = country
    reject(world)


def test_rejects_package_country_differing_from_envelope():
    reject(edit_package(World(), lambda p: p.update(country="DE")))


def test_rejects_other_target():
    world = World()
    other = deepcopy(world.template["target"])
    other.update(posting_id="744000000000002", opportunity_key="opp:other",
                 apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000002")
    world.envelope = dict(targets=[other])
    reject(world)


@pytest.mark.parametrize("name", ("cv", "template", "registry", "inventory", "eligibility",
                                  "policy", "profile"))
def test_rejects_changed_pinned_input(name):
    sources, inputs, evidence = World().build()
    reject(built=(sources, replace(inputs, **{name: getattr(inputs, name) + b" "}), evidence))


def test_rejects_inventory_change_repinned_in_envelope_but_not_in_package():
    # The indexed eligibility package still names the previously pinned inventory.
    world = World()
    template, registry, snapshot, inventory, handoff, package = world.raw()
    world.package = json.loads(package)
    world.inventory["entries"][0]["requirement_texts"].append("Synthetic extra text")
    reject(world)


def test_rejects_package_not_in_pinned_index():
    world = World()
    world.index_entries = {"exec-other-synthetic": "0" * 64}
    reject(world)


def test_rejects_package_indexed_under_another_execution():
    world = World()
    *_, package = world.raw()
    world.index_entries = {"exec-other-synthetic": digest(package)}
    reject(world)


# --- W36-03: recomputed met --------------------------------------------------------------

def edit_package(world, edit):
    template, registry, snapshot, inventory, handoff, _ = world.raw()
    package = package_document(world.template, inventory, handoff, snapshot)
    edit(package)
    world.package = package
    return world


@pytest.mark.parametrize("edit", (
    lambda p: p["requirements"].pop(),                                   # missing row
    lambda p: p["requirements"].append(dict(p["requirements"][0])),      # extra/duplicate row
    lambda p: p["requirements"].reverse(),                               # reordered rows
    lambda p: p["requirements"][0].update(text=LINES[0] + "."),          # altered text
    lambda p: p["requirements"][0].update(text=LINES[0].lower()),        # case variant
    lambda p: p["requirements"].clear(),
    lambda p: p.update(requirements=None),
    lambda p: p["requirements"][0].update(extra=True),
    lambda p: p["requirements"][0].pop("inventory_key"),
), ids=["missing", "extra", "reordered", "altered", "case", "empty", "none", "extra-key",
        "missing-key"])
def test_required_rows_must_match_exactly(edit):
    reject(edit_package(World(), edit))


@pytest.mark.parametrize("result,key", (
    ("unmet", "synthetic.language.a"), ("unknown", None), ("met", "synthetic.certificate.b"),
    ("met", None), ("MET", "synthetic.language.a"), (True, "synthetic.language.a"),
))
def test_rejects_claims_that_differ_from_recomputation(result, key):
    def edit(package):
        package["requirements"][0].update(result=result, inventory_key=key)
    reject(edit_package(World(), edit))


def test_rejects_forged_met_for_a_requirement_absent_from_inventory():
    world = World()
    line = "Synthetic requirement C: unlisted synthetic skill"
    world.handoff["candidates"][0]["mandatory_qualification_lines"].append(line)
    edit_package(world, lambda p: p["requirements"].append(
        dict(text=line, result="met", inventory_key="synthetic.language.a")))
    reject(world)


def test_rejects_honest_unknown_row():
    world = World()
    line = "Synthetic requirement C: unlisted synthetic skill"
    world.handoff["candidates"][0]["mandatory_qualification_lines"].append(line)
    edit_package(world, lambda p: p["requirements"].append(
        dict(text=line, result="unknown", inventory_key=None)))
    reject(world)


def test_rejects_honest_unmet_row():
    world = World()
    line = "Synthetic requirement Z: synthetic licence mandatory"
    world.handoff["candidates"][0]["mandatory_qualification_lines"].append(line)
    edit_package(world, lambda p: p["requirements"].append(
        dict(text=line, result="unmet", inventory_key="synthetic.licence.z")))
    reject(world)


def test_rejects_semantic_near_match_without_inference():
    # Paraphrase, case and punctuation variants never match; only NFC and
    # whitespace are canonicalized.
    world = World()
    world.inventory["entries"][0]["requirement_texts"] = [
        "synthetic requirement a: fluent synthetic language"]
    reject(world)


def test_whitespace_and_nfc_are_the_only_canonicalization():
    assert canonical_text("  Á  b\tc ") == canonical_text("Á b c") == "Á b c"
    assert canonical_text("Synthetic") != canonical_text("synthetic")
    world = World()
    world.inventory["entries"][0]["requirement_texts"] = [" " + LINES[0]]
    reject(world)  # an inventory text must itself be trimmed and printable...
    world.inventory["entries"][0]["requirement_texts"] = [LINES[0].replace(": ", ":  ")]
    assert admit(world)  # ...but inner whitespace runs are canonicalized


@pytest.mark.parametrize("held", (True, False))
def test_rejects_contradictory_or_ambiguous_inventory(held):
    world = World()
    world.inventory["entries"][2].update(held=held, requirement_texts=[LINES[0]])
    reject(world)


def test_rejects_ambiguous_canonical_duplicate_inside_one_entry():
    world = World()
    world.inventory["entries"][0]["requirement_texts"].append(LINES[0].replace(": ", ":  "))
    reject(world)


@pytest.mark.parametrize("change", (
    dict(verified=False), dict(verified=1), dict(source_ref="cv:synthetic"),
    dict(source_ref=""), dict(held="true"), dict(requirement_texts=[]),
    dict(verified_at=(NOW - timedelta(days=31)).isoformat()),
    dict(verified_at=(NOW + timedelta(seconds=1)).isoformat()),
    dict(verified_at="2026-11-17T05:00:00"), dict(key="*synthetic"),
))
def test_rejects_unverified_stale_or_future_inventory_evidence(change):
    world = World()
    world.inventory["entries"][1].update(change)
    reject(world)


@pytest.mark.parametrize("change", (
    dict(candidate_profile_version="profile-other"), dict(matching_rule_version="other"),
    dict(schema_version="EJS-VERIFIED-FACTS-1"), dict(entries=[]), dict(extra=True),
))
def test_rejects_inventory_scope_changes(change):
    world = World()
    world.inventory.update(change)
    reject(world)


def test_recompute_is_exact_and_ignores_file_claims():
    world = World()
    template, registry, snapshot, inventory, *_ = world.raw()
    by_text, _ = inventory_eligibility._inventory(inventory, "profile-2026-11-16", NOW)
    assert recompute(LINES[0], by_text) == ("met", "synthetic.language.a")
    assert recompute("Synthetic requirement Z: synthetic licence mandatory", by_text) == (
        "unmet", "synthetic.licence.z")
    assert recompute("Synthetic requirement A", by_text) == ("unknown", None)


# --- W36-03: employer evidence -------------------------------------------------------------

@pytest.mark.parametrize("edit", (
    lambda h: h.update(evidence_origin="offline_replay_synthetic"),
    lambda h: h.update(evidence_origin="derived_offline_analysis_of_live_evidence"),
    lambda h: h.update(real_evidence=False),
    lambda h: h.update(function06_eligible=False),
    lambda h: h.update(executable=True),
    lambda h: h.update(schema="EJS-F05-DETAIL-VERIFICATION-1"),
    lambda h: h["candidates"][0].update(evidence_origin="offline_replay_synthetic"),
    lambda h: h["candidates"][0].update(status="incomplete_evidence"),
    lambda h: h["candidates"][0].update(status_reasons=["synthetic:reason"]),
    lambda h: h["candidates"][0].update(detail_sha256=None),
    lambda h: h["candidates"][0].update(mandatory_qualification_lines=[]),
    lambda h: h["candidates"][0].update(mandatory_qualification_lines=[LINES[0], LINES[0]]),
    lambda h: h["candidates"].append(deepcopy(h["candidates"][0])),
    lambda h: h["candidates"][0].update(posting_id="744000000000009"),
    lambda h: h["candidates"][0].update(canonical_url=h["candidates"][0]["canonical_url"] + "x"),
    lambda h: h.update(candidates=[]),
), ids=["replay", "derived", "not-real", "not-f06", "executable", "schema", "row-replay",
        "status", "reasons", "detail", "no-lines", "dup-lines", "two-matches", "other-posting",
        "other-url", "no-candidates"])
def test_rejects_non_live_or_non_matching_employer_evidence(edit):
    world = World()
    edit(world.handoff)
    reject(world)


@pytest.mark.parametrize("observed", (
    OBSERVED - timedelta(hours=48, seconds=1),   # stale
    NOW + timedelta(seconds=1),                  # future
))
def test_rejects_stale_or_future_employer_evidence(observed):
    world = World()
    world.handoff["candidates"][0]["observed_at"] = observed.strftime("%Y-%m-%dT%H:%M:%SZ")
    reject(world, now=NOW)


def test_rejects_relabeled_synthetic_handoff_under_the_original_pin():
    world = World()
    world.handoff.update(evidence_origin="offline_replay_synthetic", real_evidence=False,
                         function06_eligible=False)
    *_, handoff_replay, _ = world.raw()
    template, registry, snapshot, inventory, _, _ = world.raw()
    pinned_package = package_document(world.template, inventory, handoff_replay, snapshot)
    # The same synthetic content relabeled as live no longer matches the pin.
    world.handoff.update(evidence_origin="live_employer_response", real_evidence=True,
                         function06_eligible=True)
    world.package = pinned_package
    reject(world)


def test_relabeled_handoff_needs_a_new_index_pin_from_the_trusted_root():
    world = World()
    sources, inputs, evidence = world.build()
    relabeled = evidence.handoff.replace(b'"live_employer_response"', b'"live_employer_response" ')
    reject(built=(sources, inputs, replace(evidence, handoff=relabeled)))


# --- vacancy-country attestation ------------------------------------------------------------

def v1_package(world):
    """The pre-fix package shape: a country assertion and no attestation pin."""
    template, registry, snapshot, inventory, handoff, package = world.raw()
    doc = json.loads(package)
    doc.pop("country_attestation_sha256")
    return doc


@pytest.mark.parametrize("location", ("Berlin, Germany", None))
def test_qa_country_probe_now_rejects(location):
    # QA AIW-110 country-probe.txt: the matched employer location is changed or
    # removed, every pin is rebuilt consistently and envelope, package and input
    # all say NL. With only the package's country assertion this was admitted.
    world = World()
    candidate = world.handoff["candidates"][0]
    if location is None:
        candidate.pop("location")
    else:
        candidate["location"] = location
    world.package = v1_package(world)
    reject(world)


def test_rejects_package_without_attestation_even_with_evidence_supplied():
    world = World()
    world.package = v1_package(world)
    sources, inputs, evidence = world.build()
    assert evidence.country_attestation  # supplied, but nothing pins it
    reject(built=(sources, inputs, evidence))


@pytest.mark.parametrize("country", ("DE", "nl", "", None, "XX", ["NL"], ["NL", "DE"]))
def test_rejects_attestation_country_disagreeing_with_envelope(country):
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff), country=country)
    reject(world)


def test_rejects_de_attestation_even_when_envelope_package_and_input_agree_on_de():
    # Only the envelope country can be admitted. Moving every assertion to DE
    # still leaves an NL envelope.
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff), country="DE")
    world.package = edit_package(World(), lambda p: p.update(country="DE")).package
    world.inputs["country"] = "DE"
    reject(world)


@pytest.mark.parametrize("change", (
    dict(requisition_id="REF999"), dict(posting_id="744000000000002"),
    dict(company="OtherCo"), dict(opportunity_key="opp:other"),
    dict(apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000002"),
    dict(ats_family="greenhouse"), dict(extra=True),
))
def test_rejects_attestation_for_another_target(change):
    world = World()
    *_, handoff, _ = world.raw()
    doc = attestation_document(world.template, handoff)
    doc["target"].update(change)
    world.attestation = doc
    reject(world)


def test_rejects_attestation_for_another_execution():
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff),
                             execution_id="exec-other-synthetic")
    reject(world)


@pytest.mark.parametrize("source_ref", (
    f"sha256:{'0' * 64}", "0" * 64, "evidence:f05-handoff", "Berlin, Germany", None, "",
))
def test_rejects_attestation_not_referencing_the_pinned_handoff(source_ref):
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff), source_ref=source_ref)
    reject(world)


def test_rejects_attestation_referencing_an_older_handoff():
    world = World()
    *_, old_handoff, _ = world.raw()
    stale = attestation_document(world.template, old_handoff)
    world.handoff["candidates"][0]["role"] = "Synthetic Analyst II"
    world.attestation = stale
    reject(world)


@pytest.mark.parametrize("attested", (
    NOW + timedelta(seconds=1),                     # future
    OBSERVED - timedelta(seconds=1),                # before the evidence it reviews
))
def test_rejects_future_or_premature_attestation(attested):
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff),
                             attested_at=attested.isoformat())
    reject(world)


def test_rejects_stale_attestation_of_stale_evidence():
    # attested_at >= observed_at and the 48-hour handoff bound together imply the
    # attestation bound; an attestation older than 48 hours needs older evidence.
    world = World()
    old = NOW - timedelta(hours=48, seconds=2)
    world.handoff["candidates"][0]["observed_at"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff),
                             attested_at=(old + timedelta(seconds=1)).isoformat())
    reject(world)


@pytest.mark.parametrize("change", (
    dict(schema_version="EJS-VACANCY-COUNTRY-ATTESTATION-2"), dict(extra=True),
    dict(attested_at="2026-11-17T04:55:00"), dict(attested_at=None),
))
def test_rejects_malformed_attestation(change):
    world = World()
    *_, handoff, _ = world.raw()
    world.attestation = dict(attestation_document(world.template, handoff), **change)
    reject(world)


@pytest.mark.parametrize("missing", ("country", "source_ref", "target", "attested_at"))
def test_rejects_attestation_missing_a_key(missing):
    world = World()
    *_, handoff, _ = world.raw()
    doc = attestation_document(world.template, handoff)
    doc.pop(missing)
    world.attestation = doc
    reject(world)


@pytest.mark.parametrize("raw", (b"", b" ", b"[]", b'{"country":"NL","country":"DE"}',
                                 b"\xff"))
def test_rejects_absent_duplicate_key_or_malformed_attestation_bytes(raw):
    world = World()
    world.attestation = raw
    reject(world)


def test_rejects_attestation_bytes_not_pinned_by_the_package():
    world = World()
    sources, inputs, evidence = world.build()
    swapped = evidence.country_attestation.replace(b'"NL"', b'"NL" ')
    reject(built=(sources, inputs, replace(evidence, country_attestation=swapped)))


def test_free_text_location_is_never_parsed():
    # The attestation decides; the handoff's free-text location is neither a
    # source nor a veto. Checking it is part of the owner's attestation review.
    for location in ("Berlin, Germany", "Amsterdam, Netherlands"):
        world = World()
        world.handoff["candidates"][0]["location"] = location
        assert admit(world)
    source = open(inventory_eligibility.__file__, encoding="utf-8").read()
    assert '"location"' not in source and "canon_country" not in source


# --- W36-03: Evidence Text references -------------------------------------------------------

@pytest.mark.parametrize("change", (
    lambda inv: inv.update(evidence_texts=[]),
    lambda inv: inv["evidence_texts"][0].update(ref="evidence:cv:other"),
    lambda inv: inv["evidence_texts"][0].update(text_sha256=digest(b"other synthetic text")),
    lambda inv: inv["evidence_texts"][0].update(entry_key="synthetic.licence.z"),   # not held
    lambda inv: inv["evidence_texts"][0].update(entry_key="synthetic.missing"),
    lambda inv: inv["evidence_texts"].append(dict(inv["evidence_texts"][0])),       # duplicate
    lambda inv: inv["evidence_texts"][0].update(ref="cv:base-erp:experience"),
), ids=["absent", "other-ref", "hash", "unheld", "unknown-entry", "duplicate", "bad-ref"])
def test_rejects_unresolved_evidence_text_reference(change):
    world = World()
    change(world.inventory)
    reject(world)


# --- W36-03: package binding ----------------------------------------------------------------

@pytest.mark.parametrize("change", (
    dict(envelope_id="env-synthetic-2"), dict(generation=4), dict(generation=True),
    dict(execution_id="exec-other-synthetic"), dict(candidate_profile_version="profile-x"),
    dict(matching_rule_version="match-synthetic-1"), dict(inventory_sha256="0" * 64),
    dict(handoff_sha256="0" * 64), dict(snapshot_sha256="*"), dict(schema_version="x"),
    dict(extra=True),
))
def test_rejects_package_binding_changes(change):
    reject(edit_package(World(), lambda p: p.update(change)))


def test_rejects_package_target_differing_from_manifest():
    reject(edit_package(World(), lambda p: p["target"].update(requisition_id="REF999")))


@pytest.mark.parametrize("raw", (b"", b"[]", b'{"a":1,"a":2}', b'{"generation":NaN}',
                                 b'{"generation":1.0}', b"\xff"))
def test_rejects_malformed_package(raw):
    world = World()
    world.package = raw or b" "
    reject(world)


# --- the validator alone ------------------------------------------------------------------

def direct(world, **override):
    template, registry, snapshot, inventory, handoff, package = world.raw()
    sources, inputs, evidence = world.build()
    kwargs = dict(expected_package_sha256=digest(package),
                  expected_inventory_sha256=digest(inventory), envelope_id="env-synthetic-1",
                  generation=3, country=COUNTRY, matching_rule_version=MATCHING_RULE, now=NOW)
    kwargs.update(override)
    return evaluate_inventory_eligibility(package, inventory, handoff,
                                          evidence.country_attestation, inputs.manifest, **kwargs)


def test_validator_returns_only_recompilation_pins():
    world = World()
    result = direct(world)
    assert result == EligibilityResult(world.template["execution_id"],
                                       digest(encode(world.snapshot)))
    assert "synthetic" not in repr(result)


@pytest.mark.parametrize("override", (
    dict(country=None), dict(country=""), dict(country="XX"), dict(country="DE"),
    dict(expected_package_sha256=None), dict(expected_inventory_sha256="0" * 64),
    dict(envelope_id="env-other"), dict(generation=2), dict(matching_rule_version=None),
    dict(now=NOW.replace(tzinfo=None)),
))
def test_validator_rejects_unconfigured_or_mismatched_caller_values(override):
    with pytest.raises(PermissionError, match="^ELIGIBILITY_REVIEW_REQUIRED$") as error:
        direct(World(), **override)
    assert error.value.__cause__ is error.value.__context__ is None


# --- R1 & R2 remediation regressions --------------------------------------------------------

def test_admission_independently_rejects_envelope_issued_before_authorized_timestamps():
    # Before attestation (ATTESTED = OBSERVED + 10m = 04:50, PREPARED = 04:45).
    world = World()
    world.envelope["issued_at"] = (ATTESTED - timedelta(seconds=1)).isoformat()
    reject(world)

    # Before snapshot issuance.
    world = World()
    world.snapshot["issued_at"] = (NOW - timedelta(minutes=10)).isoformat()
    world.envelope["issued_at"] = (NOW - timedelta(minutes=10, seconds=1)).isoformat()
    reject(world)

    # Before manifest preparation.
    world = World()
    world.envelope["issued_at"] = (PREPARED - timedelta(seconds=1)).isoformat()
    reject(world)

    # Before F05 evidence observation.
    world = World()
    world.envelope["issued_at"] = (OBSERVED - timedelta(seconds=1)).isoformat()
    reject(world)


@pytest.mark.parametrize("duplicate", (
    dict(company="exampleco"),
    dict(apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000001-senior-analyst"),
    dict(apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000001/"),
))
def test_admission_independently_rejects_duplicate_canonical_vacancy_targets(duplicate):
    world = World()
    base = deepcopy(world.template["target"])
    extra = deepcopy(base)
    extra.update(duplicate)
    world.envelope["targets"] = [base, extra]
    reject(world)
