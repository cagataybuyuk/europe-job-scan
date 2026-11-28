"""W36-02 S4 / E2E-01: synthetic offline end-to-end runs of the standing-envelope engine.

The real S3-wired ``EngineQueue`` runs with the real trusted reader pointed at a
temporary approval tree (presented as root only through the S3 test view of the
reader module), a real SQLite ledger, the real admission and a fake browser with a
network fence. Every value is a clearly synthetic placeholder: no candidate fact,
owner country, target list or employer evidence is used. Nothing here opens a
network connection, uploads a CV or submits anything; an "EMPLOYER_CONFIRMED" in
the test root is the fake browser's synthetic acknowledgement and is never an
employer-confirmed application.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ejs.apps.policy_engine import __main__ as policy_engine
from ejs.services import standing_envelope
from ejs.services.standing_guard import StandingRun
from tests.test_execution_manifest import encode
from tests.test_standing_admission import LINES
from tests.test_standing_guard import (
    EXPIRES, NetBrowser, Rig, _mutations, make_world, package_files, positive_points, ready,
)

PRODUCTION_ROOT = Path("/Library/Application Support/EJS/approval")
DENIED = ("BLOCKED", "ENGINE_STANDING_ADMISSION_DENIED")


def stage(rig: Rig, world, *, package=None, registry=None) -> bytes:
    """``Rig.add`` with an optional edit of the package's eligibility or registry record.

    The edited record is what the owner would pin: ``publish`` pins these exact bytes.
    """
    files, manifest = package_files(world, 3)
    for key, edit in (("eligibility", package), ("registry", registry)):
        if edit is not None:
            document = json.loads(files[key])
            edit(document)
            files[key] = encode(document)
    rig.worlds.append(world)
    rig.packages.append(files)
    rig.write_package(world.template["execution_id"], files)
    rig.manifests[world.template["execution_id"]] = manifest
    return manifest


def results(outbox: dict) -> list[dict]:
    return [outbox[name] for name in sorted(outbox)]


def assert_never_confirmed(rig: Rig, outbox: dict) -> None:
    """No result and no ledger row claims an employer confirmation."""
    assert all(r["status"] != "EMPLOYER_CONFIRMED" and r["evidence_ref"] is None
               for r in outbox.values())
    assert all(rig.status(world) != "Confirmed" for world in rig.worlds)


def use_browser(rig: Rig, **behaviour) -> None:
    """The S3 fake page with FakeBrowser behaviour switches (ack, visible, submit_raises)."""
    def factory(document):
        rig.log.append("factory")
        browser = NetBrowser(document, rig.clock, rig.log)
        browser.b.update(behaviour)
        rig.browsers.append(browser)
        return browser
    rig.queue.browser_factory = factory


def two_targets(rig: Rig) -> tuple:
    first, second = make_world(), make_world(execution_id="exec-20261117-example-2",
                                             second_target=True)
    manifest_a, manifest_b = rig.add(first), rig.add(second)
    rig.publish()
    rig.enqueue(manifest_a, "a.json")
    rig.enqueue(manifest_b, "b.json")
    return first, second, manifest_b


# --- positive path: accepted in the test root only ----------------------------------------

def test_synthetic_batch_is_accepted_under_the_test_root(rig):
    world, manifest = ready(rig)
    (report,), outbox = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED", report
    (result,) = outbox.values()
    assert result["submit_clicks"] == 1 and rig.browsers[0].clicks == 1
    # The synthetic acknowledgement lives only in this temporary ledger and outbox.
    assert rig.status(world) == "Confirmed"
    assert rig.ledger._legacy.path.resolve().is_relative_to(rig.tmp)
    assert standing_envelope._APPROVAL_ROOT == rig.approval != PRODUCTION_ROOT


@pytest.mark.parametrize("requirement", ("caller-owned", "production-root"))
def test_same_synthetic_bytes_are_rejected_under_the_production_trusted_source(
        tmp_path, monkeypatch, requirement):
    (tmp_path / "test-root").mkdir()
    accepted = Rig(tmp_path / "test-root", monkeypatch)
    _, manifest_a = ready(accepted)
    (report,), _ = accepted.run()
    assert report.status == "EMPLOYER_CONFIRMED"  # positive control in the test root
    records_a = {p.name: p.read_bytes() for p in accepted.approval.iterdir()}
    monkeypatch.undo()

    (tmp_path / "production").mkdir()
    rig = Rig(tmp_path / "production", monkeypatch)
    world, manifest = ready(rig)
    assert manifest == manifest_a
    assert {p.name: p.read_bytes() for p in rig.approval.iterdir()} == records_a
    if requirement == "caller-owned":
        rig.view.inodes.clear()  # the same files under the agent's own uid, as written
    else:
        if PRODUCTION_ROOT.exists():
            pytest.skip("a real approval root exists on this host; not touched by tests")
        monkeypatch.setattr(standing_envelope, "_APPROVAL_ROOT", PRODUCTION_ROOT)
    (report,), outbox = rig.run()
    assert (report.status, report.code) == DENIED
    assert rig.browsers == [] and rig.points == [] and rig.status(world) is None
    (result,) = outbox.values()
    assert result["submit_clicks"] == 0
    assert_never_confirmed(rig, outbox)


# --- duplicate submit_key ------------------------------------------------------------------

def test_duplicate_submit_key_in_one_batch_is_blocked_before_any_effect(rig):
    first = make_world()
    duplicate = make_world(execution_id="exec-20261117-example-1-dup")  # same target, same key
    assert duplicate.template["submit_key"] == first.template["submit_key"]
    manifest_a, manifest_d = rig.add(first), rig.add(duplicate)
    rig.publish()
    rig.enqueue(manifest_a, "a.json")
    rig.enqueue(manifest_d, "b.json")
    reports, _ = rig.run()
    assert [r.status for r in reports] == ["EMPLOYER_CONFIRMED", "DUPLICATE"]
    assert rig.browsers[1].calls == [] and sum(b.clicks for b in rig.browsers) == 1
    assert rig.status(first) == "Confirmed"


# --- STOP at start and mid-run -------------------------------------------------------------

def test_stop_at_start_opens_no_form_and_reserves_nothing(rig):
    world = make_world()
    later = make_world(execution_id="exec-20261117-example-1-later")
    manifest, manifest_later = rig.add(world), rig.add(later)
    rig.publish()
    rig.enqueue(manifest)
    rig.stop()
    (report,), outbox = rig.run()
    assert report.status == "KILL_SWITCH"
    assert all(b.calls == [] for b in rig.browsers) and rig.points == []
    assert not [entry for entry in rig.log if entry.startswith("effect:")]
    assert rig.status(world) is None and outbox[next(iter(outbox))]["submit_clicks"] == 0
    # Positive control: STOP was the only cause; once the owner removes it the next run submits.
    (rig.root / "control" / "STOP").unlink()
    rig.enqueue(manifest_later, "b.json")
    (report,), _ = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED"


def test_stop_at_start_cli_opens_no_browser_and_leaves_the_inbox(rig, monkeypatch, capsys):
    world, manifest = ready(rig)
    rig.stop()
    monkeypatch.setattr(policy_engine, "QUEUE_ROOT", rig.root)
    assert policy_engine.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "DISABLED"}
    assert [p.name for p in (rig.root / "inbox").iterdir()] == ["a.json"]
    assert list((rig.root / "outbox").iterdir()) == [] and rig.status(world) is None


def test_stop_mid_run_denies_the_item_in_flight_and_every_later_item(rig):
    first, second, _ = two_targets(rig)
    rig.trigger = (1, rig.stop)  # lands just before the first guard point reads
    reports, outbox = rig.run()
    assert rig.points[0] == "open"
    assert [(r.status, r.code) for r in reports] == [("BLOCKED", "POLICY_APP_RUNTIME_AUTH"),
                                                     ("KILL_SWITCH", "")]
    assert all(b.clicks == 0 and "open" not in b.calls for b in rig.browsers)
    assert rig.status(first) == "Released" and rig.status(second) is None
    assert_never_confirmed(rig, outbox)


# --- revoke, expiry and a new generation mid-run ---------------------------------------------

@pytest.mark.parametrize("change", ("revoke", "remove_pointer", "expire"))
def test_revocation_or_expiry_mid_run_stops_the_batch(rig, change):
    first, second, _ = two_targets(rig)
    rig.trigger = (1, _mutations(rig)[change])
    reports, outbox = rig.run()
    assert [r.status for r in reports] == ["BLOCKED", "BLOCKED"]
    assert reports[1].code == DENIED[1]
    assert len(rig.browsers) == 1 and rig.browsers[0].clicks == 0  # item 2: no browser at all
    assert rig.status(first) == "Released" and rig.status(second) is None
    assert_never_confirmed(rig, outbox)


def test_new_generation_mid_run_stops_the_pinned_item_and_admits_the_next_afresh(rig):
    first, second, manifest_b = two_targets(rig)
    rig.trigger = (1, lambda: rig.regenerate(4))
    reports, _ = rig.run()
    assert [r.status for r in reports] == ["BLOCKED", "EMPLOYER_CONFIRMED"]
    assert rig.browsers[0].clicks == 0 and rig.browsers[1].clicks == 1
    assert rig.status(first) == "Released" and rig.status(second) == "Confirmed"
    assert StandingRun(rig.root, rig.cv_dir, manifest_b, rig.clock).binding.generation == 4


# --- daily cap across generations, with an uncertain start -------------------------------------

def test_uncertain_start_counts_against_the_cap_across_a_new_generation(tmp_path, monkeypatch):
    points, _ = positive_points(tmp_path, monkeypatch)
    monkeypatch.undo()
    (tmp_path / "case").mkdir()
    rig = Rig(tmp_path / "case", monkeypatch)
    first, second, manifest_b = two_targets(rig)
    rig.trigger = (points.index("network:submit") + 1, lambda: rig.regenerate(4))
    reports, outbox = rig.run()
    assert [r.status for r in reports] == ["SUBMITTED_UNCONFIRMED", "CAP_REACHED"]
    assert rig.browsers[0].clicks == 0 and rig.browsers[1].calls == []
    assert rig.status(first) == "Submitted / Unconfirmed" and rig.status(second) is None
    # The new generation admits item 2, so only the shared cap stopped it.
    assert StandingRun(rig.root, rig.cv_dir, manifest_b, rig.clock).binding.generation == 4
    assert_never_confirmed(rig, outbox)


# --- unknown, unmet or contradictory mandatory facts fail closed ---------------------------------

EXTRA = "Synthetic requirement C: unlisted synthetic skill"
UNMET = "Synthetic requirement Z: synthetic licence mandatory"


def _add_line(world, line):
    world.handoff["candidates"][0]["mandatory_qualification_lines"].append(line)


def _row(text, result, key):
    return lambda p: p["requirements"].append(dict(text=text, result=result, inventory_key=key))


def _fact(field, change):
    def edit(registry):
        (fact,) = [f for f in registry["facts"] if f["canonical_field"] == field]
        if change is None:
            registry["facts"].remove(fact)
        else:
            fact.update(change)
    return edit


CASES = {
    "requirement-unknown": dict(line=EXTRA, package=_row(EXTRA, "unknown", None)),
    "requirement-unmet": dict(line=UNMET, package=_row(UNMET, "unmet", "synthetic.licence.z")),
    "requirement-forged-met": dict(line=EXTRA, package=_row(EXTRA, "met", "synthetic.language.a")),
    "inventory-contradiction-held": dict(inventory=dict(held=True)),
    "inventory-contradiction-not-held": dict(inventory=dict(held=False)),
    "answer-fact-missing": dict(registry=_fact("work.authorization", None)),
    "answer-fact-unverified": dict(registry=_fact("work.authorization", dict(verified=False))),
    "answer-fact-contradicts-review": dict(registry=_fact("work.authorization", dict(value="Yes"))),
}


@pytest.mark.parametrize("case", ("control", *CASES))
def test_unknown_unmet_or_contradictory_mandatory_fact_never_creates_a_browser(rig, case):
    spec = CASES.get(case, {})
    world = make_world()
    if "line" in spec:
        _add_line(world, spec["line"])
    if "inventory" in spec:  # a second entry claims requirement A
        world.inventory["entries"][2].update(spec["inventory"], requirement_texts=[LINES[0]])
    manifest = stage(rig, world, package=spec.get("package"), registry=spec.get("registry"))
    rig.publish()  # the owner pins exactly these package bytes
    rig.enqueue(manifest)
    (report,), outbox = rig.run()
    if case == "control":
        assert report.status == "EMPLOYER_CONFIRMED"
        return
    assert (report.status, report.code) == DENIED, case
    assert rig.browsers == [] and rig.status(world) is None
    assert outbox[next(iter(outbox))]["submit_clicks"] == 0
    assert_never_confirmed(rig, outbox)


def test_manifest_answer_edited_after_compilation_is_denied(rig):
    world = make_world()
    manifest = rig.add(world)
    rig.publish()
    document = json.loads(manifest)
    (field,) = [f for f in document["form"]["fields"] if f["key"] == "sponsorship"]
    field["answer"]["value"] = "No" if field["answer"]["value"] == "Yes" else "Yes"
    tampered = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    assert tampered != manifest
    rig.enqueue(tampered)
    (report,), outbox = rig.run()
    assert (report.status, report.code) == DENIED
    assert rig.browsers == [] and rig.status(world) is None
    assert_never_confirmed(rig, outbox)


# --- an uncertain submission stays SUBMITTED_UNCONFIRMED and is never retried ------------------

@pytest.mark.parametrize("behaviour", (dict(ack=False), dict(visible=False),
                                       dict(submit_raises=True)))
def test_uncertain_submission_is_never_confirmed_or_retried(rig, behaviour):
    world = make_world()
    retry = make_world(execution_id="exec-20261117-example-1-retry")  # same target
    manifest, manifest_retry = rig.add(world), rig.add(retry)
    rig.publish()
    rig.enqueue(manifest)
    use_browser(rig, **behaviour)
    (report,), outbox = rig.run()
    assert report.status == "SUBMITTED_UNCONFIRMED", report
    (result,) = outbox.values()
    assert result["submit_clicks"] == -1 and rig.browsers[0].clicks == 1
    assert rig.status(world) == "Submitted / Unconfirmed"

    assert rig.run()[0] == []  # nothing is re-run on its own
    rig.enqueue(manifest, "again.json")  # the same manifest again
    (report,), _ = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_DUPLICATE_EXECUTION_ID")
    rig.regenerate(4)  # a new owner generation never reopens the key
    rig.enqueue(manifest_retry, "retry.json")
    (report,), outbox = rig.run()
    assert report.status == "DUPLICATE" and rig.browsers[-1].calls == []
    assert sum(b.clicks for b in rig.browsers) == 1
    assert rig.status(world) == "Submitted / Unconfirmed"
    assert_never_confirmed(rig, outbox)


def test_interrupted_started_item_is_reported_unconfirmed_and_never_rerun(rig):
    world = make_world()
    manifest = rig.add(world)
    rig.publish()
    target = world.template["target"]
    ids = dict(opportunity_key=target["opportunity_key"], requisition_id=target["requisition_id"],
               profile_version=world.template["candidate_profile_version"],
               execution_id=world.template["execution_id"])
    assert rig.ledger.reserve(**ids, manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                              rollout_stage="R2", daily_cap=1, now=rig.clock()) == "RESERVED"
    rig.ledger.mark_started(**ids, now=rig.clock())
    (rig.root / "processing" / "a.json").write_bytes(manifest)  # left behind by a crash
    (rig.root / "processing" / "a.json").chmod(0o600)
    (report,), outbox = rig.run()
    assert (report.status, report.code) == ("SUBMITTED_UNCONFIRMED", "ENGINE_INTERRUPTED")
    assert rig.browsers == [] and rig.points == []
    assert list((rig.root / "processing").iterdir()) == []
    assert rig.status(world) == "Submitted / Unconfirmed"
    assert_never_confirmed(rig, outbox)



@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)
