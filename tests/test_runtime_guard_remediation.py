"""W3.6-01 remediation: the four Codex QA counterexamples (all synthetic, offline).

1. A CV POST must not be forwarded after STOP appears.
2. The application POST must not be forwarded after STOP appears.
3. A STOP lookup that fails with EIO (or any non-ENOENT error) is not absence.
4. A manifest that expires while the owner is solving a challenge (or before any later mutation or
   POST) must not be used.

Real temporary ``control/`` files, a synthetic browser and a synthetic transport are used. The
real ``SmartRecruitersPolicyBrowser`` route handler is exercised with fake Playwright route objects.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import errno
import hashlib
import os

import pytest

from ejs.services import engine_queue
from ejs.services.engine_queue import QueueError, prepare_root, read_switch, runtime_guard
from ejs.services.policy_application_runtime import PolicyApplicationRuntime
from ejs.services.smartrecruiters_policy_browser import PolicyNetworkBoundary, SmartRecruitersPolicyBrowser
from tests.test_engine_queue import enable
from tests.test_execution_manifest import encode
from tests.test_policy_application_runtime import PDF
from tests.test_runtime_guard import MUTATIONS, ON, TriggerBrowser, World, fresh
from tests.test_smartrecruiters_policy_browser import (
    APPLICATION, COMPANY, POSTING, PUBLICATION, UPLOAD, FakeContext, FakeRoute,
)


# ---- synthetic transport: forwards a POST only when the runtime-bound guard allows it ------------

class NetBrowser(TriggerBrowser):
    """Mirrors the production boundary: unbound or denying guard means the POST is never forwarded."""

    def __init__(self, doc, clock, *, world, **behaviour):
        super().__init__(doc, clock, world=world, **behaviour)
        self.guard, self.forwarded = None, []

    def bind_guard(self, guard):
        self.guard = guard

    def _forward(self, kind):
        self.world.pre_forward(kind)               # the world changes here: after the runtime's own check
        if self.guard is None:
            raise PermissionError("unbound")
        self.guard("network:" + kind)              # raises to deny
        self.forwarded.append(kind)

    def upload(self, key, payload, filename):
        self._forward("upload")
        return super().upload(key, payload, filename)

    def submit_once(self):
        self._forward("application")
        return super().submit_once()


class NetWorld(World):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.pre_forward = lambda kind: None

    def _factory(self, _document):
        browser = NetBrowser(self.doc, self.clock, world=self, **self.behaviour)
        self.browsers.append(browser)
        self.fire(0)
        return browser


def net(tmp_path, name):
    base = tmp_path / name
    base.mkdir()
    return NetWorld(base)


def expires_at(world):
    return datetime.fromisoformat(world.doc["expires_at"].replace("Z", "+00:00"))


def jump_past_expiry(world):
    world.clock.now = expires_at(world) + timedelta(seconds=1)


def test_clean_run_binds_the_guard_and_forwards_both_posts(tmp_path):
    world = net(tmp_path, "clean")
    world.enqueue()
    report, _ = world.run()
    browser, = world.browsers
    assert report.status == "EMPLOYER_CONFIRMED"
    assert browser.guard is not None and browser.forwarded == ["upload", "application"]


# ---- 1. CV POST after STOP ---------------------------------------------------------------------------

def test_stop_before_the_cv_post_is_forwarded_blocks_it(tmp_path):
    world = net(tmp_path, "cv")
    world.pre_forward = lambda kind: world.stop() if kind == "upload" else None
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert report.status == "BLOCKED" and browser.forwarded == []
    assert result["submit_clicks"] == 0 and browser.clicks == 0 and world.status() == "Released"


# ---- 2. application POST after STOP ------------------------------------------------------------------

def test_stop_before_the_application_post_is_forwarded_blocks_it_and_stays_uncertain(tmp_path):
    world = net(tmp_path, "application")
    world.pre_forward = lambda kind: world.stop() if kind == "application" else None
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    # The runtime's own dispatch check passed: STOP appeared after it, so only the network fence can stop it.
    assert world.points[-2:] == ["dispatch", "network:application"]
    assert browser.forwarded == ["upload"] and browser.clicks == 0
    assert report.status == result["status"] == "SUBMITTED_UNCONFIRMED" and result["submit_clicks"] == -1
    assert world.status() == "Submitted / Unconfirmed"
    world.clear_stop()
    second = dict(world.doc, execution_id="exec-synthetic-second")
    world.enqueue(second, name="second.json")
    report2, = world.queue.run_once()
    assert report2.status == "DUPLICATE" and world.browsers[1].trace == []


# ---- 1 + 2 on the real route handler and boundary (fake Playwright route objects) -----------------

def _sr(tmp_path, *, guard=None):
    root = tmp_path / "engine-queue"
    prepare_root(root)
    enable(root)
    boundary = PolicyNetworkBoundary(COMPANY, POSTING, "Synthetic_CV.pdf")
    boundary.set_publication(PUBLICATION)
    browser = SmartRecruitersPolicyBrowser(FakeContext(), boundary)       # production transport ("fetch")
    browser.bind_guard(guard or runtime_guard(root))
    return root, boundary, browser


class CountingRoute(FakeRoute):
    fetched = 0

    def fetch(self, **kwargs):
        type(self).fetched += 1
        return super().fetch(**kwargs)


def _post(browser, url):
    CountingRoute.fetched = 0
    route = CountingRoute("POST", url, 201)
    browser._route(route)
    return route


def test_route_forwards_the_cv_and_application_posts_while_authorized(tmp_path):
    _, boundary, browser = _sr(tmp_path)
    boundary.begin_upload()
    assert _post(browser, UPLOAD).result == ("fulfilled", 201) and CountingRoute.fetched == 1
    boundary.end_upload()
    boundary.begin_submit()
    assert _post(browser, APPLICATION).result == ("fulfilled", 201) and CountingRoute.fetched == 1


def test_route_aborts_the_cv_post_after_stop(tmp_path):
    root, boundary, browser = _sr(tmp_path)
    boundary.begin_upload()
    (root / "control" / "STOP").write_bytes(b"")
    route = _post(browser, UPLOAD)
    assert route.result == ("aborted", "blockedbyclient") and CountingRoute.fetched == 0
    assert boundary.uploads == 0 and boundary.blocked_writes >= 1


def test_route_aborts_the_application_post_after_stop(tmp_path):
    root, boundary, browser = _sr(tmp_path)
    boundary.begin_submit()
    (root / "control" / "STOP").write_bytes(b"")
    route = _post(browser, APPLICATION)
    assert route.result == ("aborted", "blockedbyclient") and CountingRoute.fetched == 0
    assert boundary.applications_allowed == 0 and boundary.blocked_writes >= 1


@pytest.mark.parametrize("kind", ["upload", "application"])
def test_route_looks_again_immediately_before_the_request_leaves(tmp_path, kind):
    """STOP created between the boundary decision and the forward is caught by the second check."""
    root, boundary, browser = _sr(tmp_path)
    real, calls = runtime_guard(root), []

    def guard(point):
        calls.append(point)
        real(point)
        if len(calls) == 1:                    # the decision passed; STOP arrives right after it
            (root / "control" / "STOP").write_bytes(b"")

    browser.boundary._guard = guard
    boundary.begin_upload() if kind == "upload" else boundary.begin_submit()
    route = _post(browser, UPLOAD if kind == "upload" else APPLICATION)
    assert len(calls) == 2 and calls[0] == "network:" + kind and calls[1] == "network:forward"
    assert route.result == ("aborted", "blockedbyclient") and CountingRoute.fetched == 0


def test_posts_are_denied_while_no_guard_is_bound(tmp_path):
    boundary = PolicyNetworkBoundary(COMPANY, POSTING, "Synthetic_CV.pdf")
    boundary.set_publication(PUBLICATION)
    browser = SmartRecruitersPolicyBrowser(FakeContext(), boundary)
    boundary.begin_upload()
    assert _post(browser, UPLOAD).result == ("aborted", "blockedbyclient") and CountingRoute.fetched == 0
    boundary.end_upload()
    boundary.begin_submit()
    assert _post(browser, APPLICATION).result == ("aborted", "blockedbyclient") and CountingRoute.fetched == 0
    with pytest.raises(PermissionError):
        browser.bind_guard("not callable")
    browser.bind_guard(lambda point: None)
    with pytest.raises(PermissionError):
        browser.bind_guard(lambda point: None)                 # bound once, never replaced


def test_runtime_binds_its_guard_to_the_production_browser_before_the_browser_opens(tmp_path):
    world = fresh(tmp_path, "bind")
    boundary = PolicyNetworkBoundary(COMPANY, POSTING, "Synthetic_CV.pdf")
    browser = SmartRecruitersPolicyBrowser(FakeContext(), boundary)       # has no page.goto: open() fails
    raw = encode(world.doc)
    result = PolicyApplicationRuntime(browser, world.ledger, guard=lambda point: None, clock=world.clock).run(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), cv_path=world.cv_dir / "Synthetic_CV.pdf", switch=ON)
    assert result.status == "BLOCKED" and boundary._guard is not None


# ---- 3. STOP lookup errors are not absence -------------------------------------------------------------

def _break_stop_lookup(monkeypatch, code):
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if os.fspath(path).endswith(os.sep + "control" + os.sep + "STOP"):
            raise OSError(code, os.strerror(code))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(engine_queue.os, "lstat", lstat)


@pytest.mark.parametrize("code", [errno.EIO, errno.EACCES, errno.EPERM, errno.ETIMEDOUT, errno.EBUSY])
def test_unexpected_stop_lookup_error_denies_authorization(tmp_path, monkeypatch, code):
    root = tmp_path / "engine-queue"
    prepare_root(root)
    enable(root)
    assert read_switch(root).final_submit_enabled
    _break_stop_lookup(monkeypatch, code)
    switch = read_switch(root)
    assert not switch.final_submit_enabled and switch.kill_switch_engaged
    with pytest.raises(QueueError):
        runtime_guard(root)("any")


def test_only_a_genuine_enoent_means_stop_is_absent(tmp_path):
    root = tmp_path / "engine-queue"
    prepare_root(root)
    enable(root)
    assert not os.path.lexists(root / "control" / "STOP") and read_switch(root).final_submit_enabled
    runtime_guard(root)("any")


@pytest.mark.parametrize("code", [errno.EIO, errno.EACCES])
def test_stop_lookup_error_during_a_run_blocks_the_next_effect(tmp_path, monkeypatch, code):
    world = net(tmp_path, f"eio{code}")
    world.fire_at = 1                                              # right after the browser opened
    world.action = lambda: _break_stop_lookup(monkeypatch, code)
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert not [n for n in browser.trace[1:] if n in MUTATIONS] and browser.forwarded == []
    assert result["submit_clicks"] == 0 and world.status() == "Released"


def test_stop_lookup_error_at_the_network_forward_blocks_the_post(tmp_path, monkeypatch):
    world = net(tmp_path, "eio-forward")
    world.pre_forward = lambda kind: _break_stop_lookup(monkeypatch, errno.EIO) if kind == "application" else None
    world.enqueue()
    report, _ = world.run()
    browser, = world.browsers
    assert report.status == "SUBMITTED_UNCONFIRMED" and browser.forwarded == ["upload"] and browser.clicks == 0


# ---- 4. manifest expiry ------------------------------------------------------------------------------

def test_manifest_expiring_during_the_owner_wait_blocks_everything_after_it(tmp_path):
    world = net(tmp_path, "owner")
    world.behaviour["challenge_at"] = 1
    world.on_owner_wait = lambda: jump_past_expiry(world)      # the owner took longer than the manifest lives
    world.enqueue()
    report, outbox = world.run()
    result, = outbox.values()
    browser, = world.browsers
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH")
    assert browser.trace[-1] == "wait_for_owner"            # nothing ran after the wait
    assert not [n for n in browser.trace if n in ("write", "upload", "advance", "submit_once")]
    assert browser.forwarded == [] and browser.clicks == 0 and result["submit_clicks"] == 0
    assert world.status() == "Awaiting User"                   # owner hold kept; never released by the runtime


def test_manifest_expiring_at_any_boundary_blocks_the_next_effect(tmp_path):
    clean = net(tmp_path, "clean")
    clean.enqueue()
    clean.run()
    trace = clean.browsers[0].trace
    submit_at = trace.index("submit_once")
    for j in range(1, submit_at + 1):
        world = net(tmp_path, f"x{j}")
        world.fire_at = j
        world.action = lambda world=world: jump_past_expiry(world)
        world.enqueue()
        report, outbox = world.run()
        result, = outbox.values()
        browser, = world.browsers
        label = f"j={j} after={trace[j - 1]}"
        assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_RUNTIME_AUTH"), label
        assert not [n for n in browser.trace[j:] if n in MUTATIONS], label
        assert browser.clicks == 0 and result["submit_clicks"] == 0 and world.status() == "Released", label


def test_manifest_expiring_between_mark_started_and_dispatch_is_not_submitted(tmp_path, monkeypatch):
    world = net(tmp_path, "start")
    real = world.ledger.mark_started

    def start_then_expire(**ids):
        real(**ids)
        jump_past_expiry(world)

    monkeypatch.setattr(world.ledger, "mark_started", start_then_expire)
    world.enqueue()
    report, outbox = world.run()
    browser, = world.browsers
    assert report.status == "SUBMITTED_UNCONFIRMED" and "dispatch" not in world.points
    assert browser.clicks == 0 and browser.forwarded == ["upload"] and world.status() == "Submitted / Unconfirmed"


@pytest.mark.parametrize("kind", ["upload", "application"])
def test_manifest_expiring_at_the_network_forward_blocks_the_post(tmp_path, kind):
    world = net(tmp_path, "forward-" + kind)
    world.pre_forward = lambda k: jump_past_expiry(world) if k == kind else None
    world.enqueue()
    report, outbox = world.run()
    browser, = world.browsers
    assert report.status == ("BLOCKED" if kind == "upload" else "SUBMITTED_UNCONFIRMED")
    assert kind not in browser.forwarded and browser.clicks == 0
    assert world.status() == ("Released" if kind == "upload" else "Submitted / Unconfirmed")


def test_a_clock_that_runs_backwards_cannot_extend_the_manifest(tmp_path):
    world = fresh(tmp_path, "backwards")
    expiry = expires_at(world)
    readings = iter([expiry - timedelta(hours=1), expiry + timedelta(seconds=1), expiry - timedelta(hours=5)])
    runtime = PolicyApplicationRuntime(TriggerBrowser(world.doc, world.clock, world=world), world.ledger,
                                       guard=lambda point: None, clock=lambda: next(readings))
    runtime._expires_at = expiry
    runtime._check("one")                                      # before expiry: allowed
    with pytest.raises(Exception):
        runtime._check("two")                                  # past expiry: denied
    with pytest.raises(Exception):
        runtime._check("three")                                # clock went backwards: still denied
