"""W36-04: the runtime report must agree with the durable ledger (synthetic, offline).

QA-140 counterexample: ``mark_started`` commits the ``Submitted / Unconfirmed`` row and then
raises (for example the parent-directory fsync after ``COMMIT`` fails). The runtime used to
report ``BLOCKED`` (and tried to release) while the ledger had already consumed the key.

Contract checked here:
  * an error before ``mark_started`` is attempted -> ``BLOCKED`` and the reservation is released;
  * ``mark_started`` refused and rolled back       -> ``BLOCKED``, row still releasable;
  * ``mark_started`` committed, then an error      -> ``SUBMITTED_UNCONFIRMED`` (never ``BLOCKED``),
    never released, no click;
  * ledger state unreadable after the attempt     -> ``SUBMITTED_UNCONFIRMED`` (fail safe), no click;
  * ``confirm`` committed, then an error          -> report follows the ledger (``Confirmed``)
    only because exact employer evidence was verified first; otherwise ``SUBMITTED_UNCONFIRMED``;
  * restart / re-queue never re-dispatches the consumed key.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from ejs.persistence.submission_ledger import SubmissionLedgerError
from ejs.services.policy_application_runtime import PolicyApplicationRuntime
from tests.test_engine_queue import enable, make_queue, private_write, results
from tests.test_engine_queue import setup  # noqa: F401 - pytest fixture
# Queue outcome tests only; real standing admission is covered in test_standing_guard.py.
from tests.test_engine_queue import legacy_standing_admission  # noqa: F401 - autouse fixture
from tests.test_execution_manifest import encode
from tests.test_policy_application_runtime import ALLOW, ON, Clock, FakeBrowser, run, status
from tests.test_policy_application_runtime import env  # noqa: F401 - pytest fixture

STARTED, CONFIRMED = "Submitted / Unconfirmed", "Confirmed"


def commit_then_raise(ledger, method):
    """The real ``method`` commits durably; the parent-directory fsync after COMMIT then fails."""
    legacy = ledger._legacy
    real_method, real_sync = getattr(ledger, method), legacy._sync_parent
    armed = {"on": False, "calls": 0}

    def sync():
        real_sync()
        if armed["on"]:
            armed["on"] = False
            raise OSError("fsync failed after commit")

    def wrapped(**kwargs):
        armed["on"], armed["calls"] = True, armed["calls"] + 1
        try:
            return real_method(**kwargs)
        finally:
            armed["on"] = False

    legacy._sync_parent = sync
    setattr(ledger, method, wrapped)
    return armed


def runtime_run(env, ledger=None, **behaviour):
    base, cv, default_ledger = env
    clock = Clock()
    browser = FakeBrowser(base, clock, **behaviour)
    raw = encode(base)
    result = PolicyApplicationRuntime(browser, ledger or default_ledger, guard=ALLOW, clock=clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=cv, switch=ON)
    return result, browser


# ---- reproduction ---------------------------------------------------------------------------------

def test_mark_started_commit_then_error_reports_submitted_unconfirmed(env):
    _, _, ledger = env
    armed = commit_then_raise(ledger, "mark_started")
    result, browser = runtime_run(env)
    assert armed["calls"] == 1
    assert status(ledger) == STARTED                      # durable: key consumed
    assert result.status == "SUBMITTED_UNCONFIRMED"       # was BLOCKED before the fix
    assert result.code == "POLICY_APP_START_RECORD_ERROR"
    assert result.submit_clicks == -1 and not result.applied and result.evidence_ref == ""
    assert browser.clicks == 0 and "submit" not in browser.calls  # never dispatched after the error


def test_mark_started_commit_then_error_after_owner_handoff(env):
    _, _, ledger = env
    commit_then_raise(ledger, "mark_started")
    result, browser = runtime_run(env, challenge_at=2)
    assert "wait_for_owner" in browser.calls
    assert status(ledger) == STARTED and result.status == "SUBMITTED_UNCONFIRMED"
    assert result.code == "POLICY_APP_START_RECORD_ERROR" and browser.clicks == 0


def test_keyboard_interrupt_after_mark_started_commit_is_reraised_and_consumed(env):
    _, _, ledger = env
    real = ledger.mark_started

    def interrupted(**kwargs):
        real(**kwargs)
        raise KeyboardInterrupt

    ledger.mark_started = interrupted
    with pytest.raises(KeyboardInterrupt):
        runtime_run(env)
    assert status(ledger) == STARTED


# ---- unchanged pre-start behaviour ------------------------------------------------------------------

def test_mark_started_refused_and_rolled_back_stays_blocked_and_released(env):
    _, _, ledger = env
    real = ledger.mark_started

    def refused(**kwargs):
        raise SubmissionLedgerError("POLICY_LEDGER_REJECTED")  # nothing written

    ledger.mark_started = refused
    result, browser = runtime_run(env)
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_BLOCKED"
    assert status(ledger) == "Released" and browser.clicks == 0
    ledger.mark_started = real
    again, browser2 = runtime_run(env)
    assert again.status == "EMPLOYER_CONFIRMED" and browser2.clicks == 1


def test_failure_before_mark_started_is_blocked_and_released(env):
    _, _, ledger = env
    calls = []
    real = ledger.mark_started
    ledger.mark_started = lambda **kw: (calls.append(1), real(**kw))
    result, browser, ledger, _ = run(env, overwrite="email")
    assert result.status == "BLOCKED" and result.code == "POLICY_APP_READBACK_MISMATCH"
    assert calls == [] and status(ledger) == "Released" and browser.clicks == 0


# ---- unreadable state after the attempt ------------------------------------------------------------

def test_unreadable_ledger_after_start_attempt_fails_safe(env):
    _, _, ledger = env
    real = ledger.mark_started
    released = []

    def broken(**kwargs):
        real(**kwargs)
        ledger.status = lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable"))
        raise OSError("lost")

    real_release = ledger.release
    ledger.release = lambda **kw: (released.append(1), real_release(**kw))
    ledger.mark_started = broken
    result, browser = runtime_run(env)
    assert result.status == "SUBMITTED_UNCONFIRMED" and result.code == "POLICY_APP_START_STATE_UNKNOWN"
    assert released == [] and browser.clicks == 0
    del ledger.status
    assert status(ledger) == STARTED


@pytest.mark.parametrize("state", [None, "Released", "Awaiting User"])
def test_unexpected_ledger_state_after_start_attempt_is_not_released(env, state):
    _, _, ledger = env
    real = ledger.mark_started
    released = []

    def odd(**kwargs):
        ledger.status = lambda *a, **k: state
        raise OSError("lost")

    ledger.release = lambda **kw: released.append(1)
    ledger.hold_for_owner = lambda **kw: released.append(2)
    ledger.mark_started = odd
    result, browser = runtime_run(env)
    assert result.status == "SUBMITTED_UNCONFIRMED" and result.code == "POLICY_APP_START_STATE_UNKNOWN"
    assert released == [] and browser.clicks == 0
    del real


# ---- dispatch uncertainty and confirmation ---------------------------------------------------------

@pytest.mark.parametrize("behaviour", [dict(submit_raises=True), dict(ack=False), dict(visible=False)])
def test_dispatch_uncertainty_stays_submitted_unconfirmed(env, behaviour):
    result, browser, ledger, _ = run(env, **behaviour)
    assert result.status == "SUBMITTED_UNCONFIRMED" and result.submit_clicks == -1
    assert status(ledger) == STARTED and browser.clicks == 1 and not result.applied


def test_confirm_commit_then_error_follows_the_verified_ledger(env):
    _, _, ledger = env
    armed = commit_then_raise(ledger, "confirm")
    result, browser = runtime_run(env)
    assert armed["calls"] == 1 and status(ledger) == CONFIRMED
    assert result.status == "EMPLOYER_CONFIRMED" and result.submit_clicks == 1
    assert result.evidence_ref == "evidence:sha256:" + "e" * 64 and browser.clicks == 1


def test_confirm_error_without_commit_is_never_upgraded(env):
    _, _, ledger = env

    def failed(**kwargs):
        raise OSError("disk")

    ledger.confirm = failed
    result, browser = runtime_run(env)
    assert status(ledger) == STARTED
    assert result.status == "SUBMITTED_UNCONFIRMED" and not result.applied and browser.clicks == 1


def test_confirm_error_with_different_durable_evidence_is_never_upgraded(env):
    _, _, ledger = env
    real = ledger.confirm

    def different(**kwargs):
        kwargs["evidence"] = replace(kwargs["evidence"], evidence_ref="evidence:sha256:" + "d" * 64)
        assert real(**kwargs) is True
        raise OSError("lost")

    ledger.confirm = different
    result, browser = runtime_run(env)
    assert result.status == "SUBMITTED_UNCONFIRMED" and not result.applied and browser.clicks == 1


def test_confirm_error_with_unreadable_state_is_never_upgraded(env):
    _, _, ledger = env
    real = ledger.confirm

    def broken(**kwargs):
        real(**kwargs)
        ledger.attempt_outcome = lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable"))
        raise OSError("lost")

    ledger.confirm = broken
    result, browser = runtime_run(env)
    assert result.status == "SUBMITTED_UNCONFIRMED" and not result.applied and browser.clicks == 1


def test_unverified_observation_is_never_reported_confirmed_even_if_ledger_says_so(env):
    _, _, ledger = env
    ledger.status = lambda *a, **k: CONFIRMED  # a lying read must not create a confirmation
    result, browser = runtime_run(env, ack=False)
    assert result.status == "SUBMITTED_UNCONFIRMED" and not result.applied


# ---- duplicate prevention and restart ------------------------------------------------------------

def test_consumed_key_is_duplicate_on_a_later_run(env):
    _, _, ledger = env
    commit_then_raise(ledger, "mark_started")
    first, _ = runtime_run(env)
    assert first.status == "SUBMITTED_UNCONFIRMED"
    again, browser = runtime_run(env)
    assert again.status == "DUPLICATE" and browser.calls == [] and browser.clicks == 0


def test_queue_report_matches_ledger_and_requeue_never_dispatches(setup):
    root, cv_dir, ledger, doc = setup
    commit_then_raise(ledger, "mark_started")
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "inbox" / "a.json", encode(doc))
    reports = queue.run_once()
    assert [(r.status, r.code) for r in reports] == [("SUBMITTED_UNCONFIRMED", "POLICY_APP_START_RECORD_ERROR")]
    (document,) = results(root).values()
    assert document["status"] == "SUBMITTED_UNCONFIRMED" and document["submit_clicks"] == -1
    assert ledger.status("opp:example:sr:744000000000001", "REF001", "profile-2026-11-16") == STARTED
    assert made[0].clicks == 0

    # Restart: a fresh queue, the same manifest re-queued, and a new execution id for the same key.
    queue2, made2 = make_queue(root, cv_dir, ledger, doc)
    private_write(root / "inbox" / "b.json", encode(doc))
    other = json.loads(encode(doc))
    other["execution_id"] = "exec-20261117-example-2"
    private_write(root / "inbox" / "c.json", json.dumps(other).encode())
    reports2 = queue2.run_once()
    assert [r.status for r in reports2] == ["BLOCKED", "DUPLICATE"]
    assert reports2[0].code == "ENGINE_DUPLICATE_EXECUTION_ID"
    assert all(b.clicks == 0 for b in made2)
    assert ledger.status("opp:example:sr:744000000000001", "REF001", "profile-2026-11-16") == STARTED


def test_interrupted_after_start_commit_is_recovered_as_unconfirmed(setup):
    root, cv_dir, ledger, doc = setup
    ids = dict(opportunity_key="opp:example:sr:744000000000001", requisition_id="REF001",
               profile_version="profile-2026-11-16")
    from tests.test_execution_manifest import NOW
    assert ledger.reserve(**ids, execution_id="exec-20261117-example-1",
                          manifest_sha256=hashlib.sha256(encode(doc)).hexdigest(),
                          rollout_stage="R4", daily_cap=None, now=NOW) == "RESERVED"
    commit_then_raise(ledger, "mark_started")
    with pytest.raises(SubmissionLedgerError):
        ledger.mark_started(**ids, execution_id="exec-20261117-example-1", now=NOW)
    assert ledger.status(*ids.values()) == STARTED
    queue, made = make_queue(root, cv_dir, ledger, doc)
    private_write(root / "processing" / "a.json", encode(doc))
    assert [r.status for r in queue.run_once()] == ["SUBMITTED_UNCONFIRMED"]
    assert made == []


@pytest.mark.parametrize("changed_execution", (False, True))
def test_recovery_never_projects_a_confirmation_to_a_different_manifest(setup, changed_execution):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    private_write(root / "inbox" / "a.json", encode(doc))
    assert queue.run_once()[0].status == "EMPLOYER_CONFIRMED"

    if changed_execution:
        doc["execution_id"] = "another-synthetic-execution"
    raw = encode(doc) + b"\n"
    private_write(root / "processing" / "b.json", raw)
    assert queue.run_once()[0].status == "INTERRUPTED"
    latest = next(row for row in results(root).values()
                  if row["manifest_sha256"] == hashlib.sha256(raw).hexdigest())
    assert latest["status"] == "INTERRUPTED" and latest["evidence_ref"] is None
    assert sum(browser.clicks for browser in made) == 1


def test_pending_recovery_cannot_be_overwritten_by_same_named_inbox_item(setup, monkeypatch):
    root, cv_dir, ledger, doc = setup
    queue, made = make_queue(root, cv_dir, ledger, doc)
    enable(root)
    claimed = root / "processing" / "a.json"
    incoming = root / "inbox" / "a.json"
    raw = encode(doc)
    private_write(claimed, raw)
    private_write(incoming, raw + b"\n")

    def write_failure(*args, **kwargs):
        raise OSError("synthetic outbox failure")

    monkeypatch.setattr(queue, "_emit", write_failure)
    assert [r.code for r in queue.run_once()] == ["ENGINE_RECOVERY_FAILED", "ENGINE_PROCESSING_NAME_OCCUPIED"]
    assert claimed.read_bytes() == raw and incoming.read_bytes() == raw + b"\n" and made == []
