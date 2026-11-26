"""W3.6-01: point-of-use runtime STOP/authorization through the real queue, runtime and ledger.

Everything is synthetic: temporary control files, a synthetic browser, a temporary SQLite ledger.
No real engine queue, STOP file, browser, employer or candidate data is touched.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.engine_queue import (
    EngineQueue, QueueError, RUNTIME_SCHEMA, _write_private, read_switch, runtime_guard,
)
from ejs.services.policy_application_runtime import PolicyApplicationRuntime
from ejs.services.policy_submission import RuntimeSwitch
from tests.test_engine_queue import enable, private_write, results
from tests.test_execution_manifest import NOW, base_document, encode
from tests.test_policy_application_runtime import PDF, Clock, FakeBrowser, two_page

MUTATIONS = ("open", "write", "upload", "advance", "submit_once")
SRC = Path(__file__).resolve().parents[1] / "src" / "ejs"
ON = RuntimeSwitch(final_submit_enabled=True, kill_switch_engaged=False)


class TriggerBrowser(FakeBrowser):
    """Synthetic browser that records every invocation and can fire an event after the n-th one."""

    def __init__(self, doc, clock, *, world, **behaviour):
        super().__init__(doc, clock, **behaviour)
        self.world, self.trace = world, []

    def _done(self, name):
        self.trace.append(name)
        self.world.fire(len(self.trace))

    def open(self, url):
        super().open(url)
        self._done("open")

    def inspect(self):
        result = super().inspect()
        self._done("inspect")
        return result

    def write(self, key, control, value):
        super().write(key, control, value)
        self._done("write")

    def readback(self, key, control):
        result = super().readback(key, control)
        self._done("readback")
        return result

    def upload(self, key, payload, filename):
        result = super().upload(key, payload, filename)
        self._done("upload")
        return result

    def advance(self):
        super().advance()
        self._done("advance")

    def wait_for_owner(self, timeout_seconds):
        result = super().wait_for_owner(timeout_seconds)
        self.trace.append("wait_for_owner")
        self.world.on_owner_wait()
        return result

    def submit_once(self):
        self.trace.append("submit_once")
        return super().submit_once()


class World:
    def __init__(self, tmp_path):
        self.root = tmp_path / "engine-queue"
        self.cv_dir = tmp_path / "cv"
        self.cv_dir.mkdir(mode=0o700)
        os.chmod(self.cv_dir, 0o700)
        private_write(self.cv_dir / "Synthetic_CV.pdf", PDF)
        self.ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "private" / "ledger.db"), now=NOW)
        doc = base_document()
        doc["cv"].update(sha256=hashlib.sha256(PDF).hexdigest(), size_bytes=len(PDF))
        doc["policy"].update(rollout_stage="R4", daily_cap=None)
        self.doc = two_page(doc)  # two pages: exercises "advance"
        self.clock, self.browsers, self.points = Clock(), [], []
        self.behaviour: dict = {}
        self.fire_at: int | None = None
        self.action = self.stop
        self.on_owner_wait = lambda: None
        self.queue = EngineQueue(self.root, ledger=self.ledger, cv_dir=self.cv_dir,
                                 browser_factory=self._factory, clock=self.clock)
        real = self.queue._guard

        def recording(point):  # wraps the REAL production guard; records the point, then delegates
            self.points.append(point)
            real(point)

        self.queue._guard = recording
        enable(self.root)

    # -- control files (real, temporary) --------------------------------------------------
    def stop(self):
        (self.root / "control" / "STOP").write_bytes(b"")

    def clear_stop(self):
        (self.root / "control" / "STOP").unlink()

    def fire(self, count):
        if self.fire_at == count:
            self.action()

    def _factory(self, _document):
        browser = TriggerBrowser(self.doc, self.clock, world=self, **self.behaviour)
        self.browsers.append(browser)
        self.fire(0)
        return browser

    # -- helpers ---------------------------------------------------------------------------
    def enqueue(self, doc=None, name="application.json"):
        _write_private(self.root / "inbox" / name, encode(doc or self.doc))

    def run(self):
        report, = self.queue.run_once()
        return report, results(self.root)

    def status(self, doc=None):
        doc = doc or self.doc
        target = doc["target"]
        return self.ledger.status(target["opportunity_key"], target["requisition_id"],
                                  doc["candidate_profile_version"])


def fresh(tmp_path, name):
    base = tmp_path / name
    base.mkdir()
    return World(base)


def baseline(tmp_path):
    world = fresh(tmp_path, "baseline")
    world.enqueue()
    report, _ = world.run()
    browser, = world.browsers
    assert report.status == "EMPLOYER_CONFIRMED" and browser.clicks == 1
    return world, browser.trace


# ---- the guard is called at every boundary in a clean run -------------------------------------

def test_clean_run_checks_every_boundary_in_order(tmp_path):
    world, trace = baseline(tmp_path)
    points = world.points
    assert points[0] == "open" and points[-2:] == ["before-start", "dispatch"]
    assert "upload" in points and "advance" in points
    mutations = [n for n in trace if n in MUTATIONS and n != "submit_once"]
    assert len(points) == len(mutations) + 2          # one check before each effect, plus start and dispatch
    for point, name in zip(points, mutations):
        assert point == name or point.startswith(name + ":")
    assert sum(p.startswith("write:") for p in points) == trace.count("write")


# ---- STOP activated at every mutation boundary ------------------------------------------------

def test_stop_at_every_browser_boundary_blocks_the_next_effect(tmp_path):
    clean, trace = baseline(tmp_path)
    expected_points = clean.points
    submit_at = trace.index("submit_once")
    denied_points = set()
    for j in range(0, submit_at + 1):
        world = fresh(tmp_path, f"j{j}")
        world.fire_at = j                       # STOP appears right after browser invocation j (0: in the factory)
        world.enqueue()
        report, outbox = world.run()
        result, = outbox.values()
        browser, = world.browsers
        label = f"j={j} after={trace[j - 1] if j else 'factory'}"
        assert result["submit_clicks"] == 0 and browser.clicks == 0, label
        assert not [n for n in browser.trace[j:] if n in MUTATIONS], label
        if j == 0:
            assert (report.status, browser.trace, world.points) == ("KILL_SWITCH", [], []), label
            assert world.status() is None, label
            continue
        assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH"), label
        assert world.status() == "Released", label
        done = len([n for n in trace[:j] if n in MUTATIONS])
        assert world.points[-1] == expected_points[done], label  # denied at exactly the next boundary
        denied_points.add(world.points[-1].split(":")[0])
    # "open" is only reachable in the window after authorization (see the next test); STOP before the
    # reservation is already refused by the authorization itself (KILL_SWITCH, j == 0 above).
    assert denied_points == {"write", "upload", "advance", "before-start"}


def test_stop_between_authorization_and_browser_open_blocks_the_open(tmp_path, monkeypatch):
    import ejs.services.policy_application_runtime as runtime_module

    world = fresh(tmp_path, "open")
    real = runtime_module.authorize_manifest

    def authorize_then_stop(*args, **kwargs):
        decision = real(*args, **kwargs)
        world.stop()                         # appears after the reservation, before any browser effect
        return decision

    monkeypatch.setattr(runtime_module, "authorize_manifest", authorize_then_stop)
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert world.points == ["open"] and browser.trace == [] and browser.clicks == 0
    assert result["submit_clicks"] == 0 and world.status() == "Released"


def test_stop_between_mark_started_and_click_blocks_dispatch_and_stays_uncertain(tmp_path, monkeypatch):
    world = fresh(tmp_path, "dispatch")
    real = world.ledger.mark_started

    def start_then_stop(**ids):
        real(**ids)
        world.stop()                        # appears after the durable start record, before the click

    monkeypatch.setattr(world.ledger, "mark_started", start_then_stop)
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert report.status == result["status"] == "SUBMITTED_UNCONFIRMED" and result["submit_clicks"] == -1
    assert world.points[-1] == "dispatch" and browser.clicks == 0 and "submit_once" not in browser.trace
    assert world.status() == "Submitted / Unconfirmed"

    # Uncertain submission is never retried or replaced: a new execution id for the same target is a duplicate.
    monkeypatch.undo()
    world.clear_stop()
    second = json.loads(json.dumps(world.doc))
    second["execution_id"] = "exec-synthetic-second"
    world.enqueue(second, name="second.json")
    report2, = world.queue.run_once()
    assert report2.status == "DUPLICATE" and world.browsers[1].trace == []
    assert world.status() == "Submitted / Unconfirmed"


def test_stop_during_owner_wait_keeps_the_owner_hold_and_blocks_further_effects(tmp_path):
    world = fresh(tmp_path, "owner")
    world.behaviour["challenge_at"] = 1
    world.on_owner_wait = world.stop        # STOP appears while the owner is solving the challenge
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert world.points == ["open", "after-owner-wait"]
    assert browser.trace[-1] == "wait_for_owner" and not [n for n in browser.trace if n in ("write", "upload", "advance")]
    assert result["submit_clicks"] == 0 and browser.clicks == 0 and world.ledger.started_today(NOW) == 0
    assert world.status() == "Awaiting User"      # held for the owner, never released or expired by the runtime


# ---- missing, malformed or inaccessible authorization fails closed ---------------------------------

def _write_json(path, payload):
    private_write(path, json.dumps(payload).encode())


def _breakage(root, kind):
    control = root / "control"
    runtime = control / "runtime.json"
    good = {"schema_version": RUNTIME_SCHEMA, "final_submit": True}
    if kind == "missing":
        runtime.unlink()
    elif kind == "empty":
        private_write(runtime, b"")
    elif kind == "malformed":
        private_write(runtime, b"{not json")
    elif kind == "duplicate-key":
        private_write(runtime, b'{"schema_version":"%s","final_submit":true,"final_submit":true}' % RUNTIME_SCHEMA.encode())
    elif kind == "wrong-schema":
        _write_json(runtime, dict(good, schema_version="EJS-ENGINE-RUNTIME-0"))
    elif kind == "flag-false":
        _write_json(runtime, dict(good, final_submit=False))
    elif kind == "flag-not-boolean":
        _write_json(runtime, dict(good, final_submit="true"))
    elif kind == "extra-key":
        _write_json(runtime, dict(good, extra=1))
    elif kind == "unsafe-file-mode":
        os.chmod(runtime, 0o644)
    elif kind == "unsafe-directory-mode":
        os.chmod(control, 0o755)
    elif kind == "symlink":
        _write_json(control / "elsewhere.json", good)
        runtime.unlink()
        runtime.symlink_to(control / "elsewhere.json")
    else:  # pragma: no cover
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["missing", "empty", "malformed", "duplicate-key", "wrong-schema", "flag-false",
                                  "flag-not-boolean", "extra-key", "unsafe-file-mode", "unsafe-directory-mode",
                                  "symlink"])
def test_unreadable_or_invalid_runtime_authorization_denies_mid_run(tmp_path, kind):
    world = fresh(tmp_path, kind)
    world.action = lambda: _breakage(world.root, kind)
    world.fire_at = 1                           # right after the browser opened
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert browser.trace == ["open", "inspect"] or browser.trace[:1] == ["open"]
    assert not [n for n in browser.trace if n in ("write", "upload", "advance", "submit_once")]
    assert result["submit_clicks"] == 0 and browser.clicks == 0 and world.status() == "Released"
    if os.path.isdir(world.root / "control"):
        os.chmod(world.root / "control", 0o700)


def test_runtime_guard_requires_enabled_final_submit_and_absent_stop(tmp_path):
    world = fresh(tmp_path, "unit")
    guard = runtime_guard(world.root)
    guard("anything")                            # enabled, no STOP: allowed
    world.stop()
    with pytest.raises(QueueError):
        guard("anything")
    world.clear_stop()
    guard("anything")                            # nothing is cached
    (world.root / "control" / "runtime.json").unlink()
    with pytest.raises(QueueError):
        guard("anything")
    assert not read_switch(world.root).final_submit_enabled


def test_runtime_requires_a_working_guard(tmp_path):
    world = fresh(tmp_path, "direct")
    browser = TriggerBrowser(world.doc, world.clock, world=world)
    with pytest.raises(TypeError):
        PolicyApplicationRuntime(browser, world.ledger, clock=world.clock)           # type: ignore[call-arg]
    with pytest.raises(TypeError, match="POLICY_APP_GUARD_REQUIRED"):
        PolicyApplicationRuntime(browser, world.ledger, guard=None, clock=world.clock)  # type: ignore[arg-type]

    def broken(point):
        raise RuntimeError("synthetic failure inside the guard")

    raw = encode(world.doc)
    result = PolicyApplicationRuntime(browser, world.ledger, guard=broken, clock=world.clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=world.cv_dir / "Synthetic_CV.pdf", switch=ON)
    assert (result.status, result.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert browser.trace == [] and world.status() == "Released"


# ---- production wiring (not only test wrappers) ---------------------------------------------------

def _calls(path, name):
    tree = ast.parse(path.read_text())
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == name]


def test_every_production_runtime_construction_passes_a_guard():
    sites = [(path, call) for path in SRC.rglob("*.py") for call in _calls(path, "PolicyApplicationRuntime")]
    assert sites, "expected the queue to construct the runtime"
    assert {path.name for path, _ in sites} == {"engine_queue.py"}
    for _, call in sites:
        assert "guard" in {keyword.arg for keyword in call.keywords}


def test_production_entry_point_uses_the_queue_and_the_queue_uses_the_real_guard(tmp_path):
    entry = SRC / "apps" / "policy_engine" / "__main__.py"
    assert _calls(entry, "EngineQueue") and not _calls(entry, "PolicyApplicationRuntime")
    world = fresh(tmp_path, "wiring")
    bare = EngineQueue(world.root, ledger=world.ledger, cv_dir=world.cv_dir,
                       browser_factory=lambda _d: None, clock=world.clock)
    bare._guard("x")                              # real, unwrapped production guard: allowed while enabled
    world.stop()
    with pytest.raises(QueueError):
        bare._guard("x")
