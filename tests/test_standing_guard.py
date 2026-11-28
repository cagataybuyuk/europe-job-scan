"""Synthetic offline coverage for W36-02 S3: standing admission at the engine guard points.

Every value is a clearly synthetic placeholder. The approval tree is a
temporary directory read by the real trusted reader; only its ownership is
presented as root through a test-only ``os`` view of the reader module, and
only for the inodes of that tree. The browser is a fake with a network fence.
Nothing here opens a network connection, uploads a CV or submits anything.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import inspect
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services import engine_queue, private_source_files, standing_envelope, standing_guard
from ejs.services.engine_queue import RUNTIME_SCHEMA, EngineQueue, item_guard
from ejs.services.inventory_eligibility import MATCHING_RULE
from ejs.services.smartrecruiters_policy_browser import PolicyNetworkBoundary, SmartRecruitersPolicyBrowser
from ejs.services.standing_admission import compile_reviewed_manifest
from ejs.services.standing_envelope import BatchBinding
from ejs.services.standing_guard import ERROR, StandingRun, package_name
from tests.test_execution_manifest import NOW, URL, encode
from tests.test_policy_application_runtime import PDF, Clock, FakeBrowser
from tests.test_standing_admission import (
    COUNTRY, POLICY, PROFILE, World, handoff_document, package_document,
)
from tests.test_standing_envelope import _unsuitable_ancestor


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


URL_B = URL.replace("744000000000001", "744000000000002")
ISSUED = NOW - timedelta(hours=1)
EXPIRES = NOW + timedelta(hours=23)


def make_world(*, execution_id: str | None = None, second_target: bool = False) -> World:
    world = World()
    world.inputs["cv"] = PDF
    world.template["cv"].update(sha256=digest(PDF), size_bytes=len(PDF))
    world.snapshot["cv_sha256"] = digest(PDF)
    if second_target:
        target = world.template["target"]
        target.update(posting_id="744000000000002", requisition_id="REQ-SYNTH-0002",
                      opportunity_key="opp:example:sr:744000000000002", apply_url=URL_B)
        world.template["submit_key"] = ("submit:opp:example:sr:744000000000002:REQ-SYNTH-0002:"
                                        "profile-2026-11-16")
        world.snapshot["target"] = deepcopy(target)
        world.handoff = handoff_document(world.template)
    if execution_id:
        world.template["execution_id"] = execution_id
    return world


def package_files(world: World, generation: int) -> tuple[dict, bytes]:
    template, registry, snapshot, inventory, handoff, _ = world.raw()
    attestation = world.attestation_raw(handoff)
    package = package_document(world.template, inventory, handoff, snapshot, attestation)
    package["generation"] = generation
    manifest = compile_reviewed_manifest(
        template, registry, snapshot, expected_template_sha256=digest(template),
        expected_registry_sha256=digest(registry), expected_snapshot_sha256=digest(snapshot),
        now=NOW)
    files = dict(policy=POLICY, profile=PROFILE, template=template, registry=registry,
                 inventory=inventory, eligibility=encode(package), snapshot=snapshot,
                 handoff=handoff, country_attestation=attestation)
    return files, manifest


def approval_records(worlds: list[World], packages: list[dict], generation: int,
                     **envelope_changes) -> tuple[bytes, bytes, bytes]:
    first = packages[0]
    targets = []
    for world in worlds:  # one envelope row per distinct target
        if world.template["target"] not in targets:
            targets.append(deepcopy(world.template["target"]))
    envelope = dict(
        schema_version="EJS-STANDING-ENVELOPE-1", envelope_id="env-synthetic-1",
        generation=generation, status="active", issued_at=ISSUED.isoformat(),
        expires_at=EXPIRES.isoformat(), policy_version="SUBMIT-1.0",
        candidate_profile_version="profile-2026-11-16", ats_family="smartrecruiters",
        country=COUNTRY, targets=targets,
        rollout_stages=["R2"], daily_cap=1, daily_timezone="Europe/Istanbul",
        matching_rule_version=MATCHING_RULE,
        template_sha256s=sorted({digest(p["template"]) for p in packages}),
        policy_sha256=digest(POLICY), profile_sha256=digest(PROFILE), cv_sha256=digest(PDF),
        registry_sha256=digest(first["registry"]), inventory_sha256=digest(first["inventory"]),
    )
    envelope.update(envelope_changes)
    index = dict(schema_version="EJS-BATCH-ELIGIBILITY-INDEX-1", envelope_id="env-synthetic-1",
                 generation=generation,
                 entries={w.template["execution_id"]: digest(p["eligibility"])
                          for w, p in zip(worlds, packages)})
    envelope_raw, index_raw = encode(envelope), encode(index)
    pointer = dict(schema_version="EJS-STANDING-ENVELOPE-POINTER-1",
                   envelope_id="env-synthetic-1", generation=generation,
                   envelope_sha256=digest(envelope_raw), eligibility_index_sha256=digest(index_raw))
    return encode(pointer), envelope_raw, index_raw


class _Inode:
    """Stat result whose owner reads as root for the approval tree's inodes only."""

    def __init__(self, info):
        self._info = info

    def __getattr__(self, name):
        return 0 if name == "st_uid" else getattr(self._info, name)


class ApprovalTreeView:
    """Test-only ``os`` stand-in for the reader module; never used by production code.

    Presents only the registered approval inodes (and their ancestors) as root
    owned, and can inject EIO on reads of approval files. Every other entry,
    including the local package and the CV, keeps its real owner.
    """

    def __init__(self):
        self.inodes: set[tuple[int, int]] = set()
        self.eio = False

    def register(self, path: Path) -> None:
        for item in (path, *path.parents):
            info = os.lstat(item)
            self.inodes.add((info.st_dev, info.st_ino))

    def _view(self, info):
        return _Inode(info) if (info.st_dev, info.st_ino) in self.inodes else info

    def __getattr__(self, name):
        return getattr(os, name)

    def stat(self, *args, **kwargs):
        return self._view(os.stat(*args, **kwargs))

    def fstat(self, fd):
        return self._view(os.fstat(fd))

    def read(self, fd, size):
        info = os.fstat(fd)
        if self.eio and (info.st_dev, info.st_ino) in self.inodes:
            raise OSError(errno.EIO, "synthetic EIO")
        return os.read(fd, size)


class NetBrowser(FakeBrowser):
    """Fake page whose network fence asks the bound guard before forwarding a POST."""

    def __init__(self, doc, clock, log):
        super().__init__(doc, clock, url=doc["target"]["apply_url"])
        self.log, self.fence = log, None

    def bind_guard(self, check):
        self.fence = check

    def _effect(self, name, *, network=False):
        if network:
            self.fence("network:" + name)  # raises on denial: nothing is forwarded
        self.log.append("effect:" + name)

    def open(self, url):
        assert url == self.doc["target"]["apply_url"]
        self.calls.append("open")
        self._effect("open")

    def write(self, key, control, value):
        self._effect("write:" + key)
        super().write(key, control, value)

    def upload(self, key, payload, filename):
        self._effect("upload", network=True)
        return super().upload(key, payload, filename)

    def advance(self):
        self._effect("advance")
        super().advance()

    def submit_once(self):
        self._effect("submit", network=True)
        return super().submit_once()


def private_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.write_bytes(data)
    os.chmod(path, mode)


class Rig:
    """Engine root, ledger, CV folder, package folders and a synthetic approval tree."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp, self.monkeypatch = tmp_path.resolve(), monkeypatch
        self.approval = self.tmp / "synthetic-approval"
        self.approval.mkdir()
        self.approval.chmod(0o755)
        if _unsuitable_ancestor(self.approval):
            pytest.skip("temporary root has shared/writable ancestors; run with --basetemp "
                        "under a non-shared directory")
        self.view = ApprovalTreeView()
        monkeypatch.setattr(private_source_files, "os", self.view)
        monkeypatch.setattr(standing_envelope, "_APPROVAL_ROOT", self.approval)
        self.root = self.tmp / "engine-queue"
        engine_queue.prepare_root(self.root)
        self.cv_dir = self.tmp / "cv"
        self.cv_dir.mkdir(mode=0o700)
        os.chmod(self.cv_dir, 0o700)
        private_write(self.cv_dir / "Synthetic_CV.pdf", PDF)
        self.ledger = enable_policy_submissions(
            SqliteSubmissionLedger(self.tmp / "private" / "ledger.db"), now=NOW)
        private_write(self.root / "control" / "runtime.json",
                      json.dumps({"schema_version": RUNTIME_SCHEMA, "final_submit": True}).encode())
        self.clock = Clock()
        self.log: list[str] = []
        self.points: list[str] = []
        self.trigger: tuple[int, object] | None = None
        self.browsers: list[NetBrowser] = []
        self.manifests: dict[str, bytes] = {}
        self.worlds: list[World] = []
        self.packages: list[dict] = []

        real_guard = engine_queue.runtime_guard

        def hooked(root):
            check = real_guard(root)

            def counted(point):
                self.points.append(point)
                self.log.append("guard:" + point)
                if self.trigger is not None and self.trigger[0] == len(self.points):
                    self.trigger[1]()  # the change lands just before this guard point reads
                check(point)
            return counted
        monkeypatch.setattr(engine_queue, "runtime_guard", hooked)

        def factory(document):
            self.log.append("factory")
            browser = NetBrowser(document, self.clock, self.log)
            self.browsers.append(browser)
            return browser
        self.queue = EngineQueue(self.root, ledger=self.ledger, cv_dir=self.cv_dir,
                                 browser_factory=factory, clock=self.clock)

    # ---- publishing -----------------------------------------------------------------

    def add(self, world: World, generation: int = 3) -> bytes:
        files, manifest = package_files(world, generation)
        self.worlds.append(world)
        self.packages.append(files)
        self.write_package(world.template["execution_id"], files)
        self.manifests[world.template["execution_id"]] = manifest
        return manifest

    def write_package(self, execution_id: str, files: dict) -> None:
        folder = self.root / "packages" / package_name(execution_id)
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        for key, name, _ in standing_guard._PACKAGE_FILES:
            private_write(folder / name, files[key])

    def publish(self, generation: int = 3, **envelope_changes) -> None:
        """Owner publication; rewrites the files in place (same root-presented inodes)."""
        records = approval_records(self.worlds, self.packages, generation, **envelope_changes)
        for name, raw in zip(("current.json", "envelope.json", "eligibility-index.json"), records):
            path = self.approval / name
            if not path.exists():
                path.touch()
            os.chmod(path, 0o644)
            with open(path, "r+b") as stream:
                stream.truncate(0)
                stream.write(raw)
            self.view.register(path)

    def regenerate(self, generation: int) -> None:
        """Owner publishes a new generation with re-issued packages for every execution."""
        self.packages = []
        for world in self.worlds:
            files, manifest = package_files(world, generation)
            self.packages.append(files)
            self.write_package(world.template["execution_id"], files)
            assert manifest == self.manifests[world.template["execution_id"]]
        self.publish(generation)

    # ---- running --------------------------------------------------------------------

    def enqueue(self, manifest: bytes, name: str = "a.json") -> None:
        private_write(self.root / "inbox" / name, manifest)

    def run(self):
        reports = self.queue.run_once()
        outbox = {p.name: json.loads(p.read_text()) for p in (self.root / "outbox").iterdir()
                  if not p.name.startswith(".")}
        return reports, outbox

    def status(self, world: World) -> str | None:
        target = world.template["target"]
        return self.ledger.status(target["opportunity_key"], target["requisition_id"],
                                  world.template["candidate_profile_version"])

    def stop(self) -> None:
        private_write(self.root / "control" / "STOP", b"")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def ready(rig: Rig) -> tuple[World, bytes]:
    world = make_world()
    manifest = rig.add(world)
    rig.publish()
    rig.enqueue(manifest)
    return world, manifest


def effects(log: list[str]) -> list[str]:
    return [entry for entry in log if entry.startswith(("effect:", "guard:", "factory"))]


# --- positive control ------------------------------------------------------------------

def test_admitted_batch_runs_through_every_guard_point_and_submits_once(rig):
    world, manifest = ready(rig)
    (report,), outbox = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED", report
    (result,) = outbox.values()
    assert result["submit_clicks"] == 1 and rig.browsers[0].clicks == 1
    assert rig.status(world) == "Confirmed"
    # Admission happened before the browser existed; every effect is preceded by a guard.
    assert rig.log[0] == "factory"
    for position, entry in enumerate(rig.log):
        if entry.startswith("effect:"):
            assert rig.log[position - 1].startswith("guard:")
    assert {"open", "upload", "before-start", "dispatch", "network:upload",
            "network:submit"} <= set(rig.points)


def positive_points(tmp_path, monkeypatch) -> tuple[list[str], list[str]]:
    (tmp_path / "probe").mkdir()
    probe = Rig(tmp_path / "probe", monkeypatch)
    ready(probe)
    (report,), _ = probe.run()
    assert report.status == "EMPLOYER_CONFIRMED"
    return list(probe.points), list(probe.log)


# --- admission before the browser factory; no un-admitted fallback ---------------------

@pytest.mark.parametrize("damage", ("no-envelope", "no-package", "revoked", "other-generation-package"))
def test_unadmitted_manifest_never_creates_a_browser(rig, damage):
    world = make_world()
    manifest = rig.add(world)
    if damage != "no-envelope":
        rig.publish(**(dict(status="revoked") if damage == "revoked" else {}))
    if damage == "no-package":
        folder = rig.root / "packages" / package_name(world.template["execution_id"])
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
    if damage == "other-generation-package":
        files, _ = package_files(world, 4)
        rig.write_package(world.template["execution_id"], files)
    rig.enqueue(manifest)
    (report,), outbox = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_STANDING_ADMISSION_DENIED")
    assert rig.browsers == [] and rig.points == [] and rig.status(world) is None
    (result,) = outbox.values()
    assert result["submit_clicks"] == 0 and result["code"] == "ENGINE_STANDING_ADMISSION_DENIED"


# --- revocation, expiry, generation change, STOP and EIO at every guard point ----------

def _mutations(rig: Rig):
    def revoke():
        rig.publish(status="revoked")

    def remove_pointer():
        (rig.approval / "current.json").unlink()

    def expire():
        rig.clock.now = EXPIRES

    def new_generation():
        rig.regenerate(4)

    def stop():
        rig.stop()

    def eio():
        rig.view.eio = True
    return dict(revoke=revoke, remove_pointer=remove_pointer, expire=expire,
                new_generation=new_generation, stop=stop, eio=eio)


@pytest.mark.parametrize("change", ("revoke", "remove_pointer", "expire", "new_generation",
                                    "stop", "eio"))
def test_change_at_every_guard_point_denies_the_next_effect(tmp_path, monkeypatch, change):
    points, positive_log = positive_points(tmp_path, monkeypatch)
    monkeypatch.undo()
    assert len(points) >= 6
    for index in range(1, len(points) + 1):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        rig = Rig(case, monkeypatch)
        world, _ = ready(rig)
        rig.trigger = (index, _mutations(rig)[change])
        (report,), outbox = rig.run()
        (result,) = outbox.values()
        browser = rig.browsers[0]
        label = (change, index, points[index - 1])
        assert browser.clicks == 0 and "submit" not in browser.calls, label
        # Exactly the positive run up to and including the denied guard point; no effect after it.
        cut = [i for i, entry in enumerate(positive_log) if entry.startswith("guard:")][index - 1]
        assert effects(rig.log) == effects(positive_log[:cut + 1]), label
        if points[index - 1] in ("dispatch", "network:submit"):
            # mark_started is durable: the outcome stays uncertain and is never retried.
            assert report.status == result["status"] == "SUBMITTED_UNCONFIRMED", label
            assert rig.status(world) == "Submitted / Unconfirmed", label
        else:
            assert report.status == result["status"] == "BLOCKED", label
            assert result["submit_clicks"] == 0 and rig.status(world) == "Released", label
        monkeypatch.undo()


def test_new_generation_is_admitted_afresh_but_never_continues_a_pinned_run(rig):
    world, manifest = ready(rig)
    run = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    run.check("probe")  # positive control: same generation still passes
    pinned = run.binding
    rig.regenerate(4)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        run.check("probe")
    fresh = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)  # re-admission is required
    assert fresh.binding.generation == 4 and pinned.generation == 3
    assert fresh.binding != pinned
    fresh.check("probe")


def test_rollback_to_the_pinned_generation_bytes_is_still_the_pinned_binding_only(rig):
    """Re-publishing generation 3 after 4 only matches a run pinned to identical bytes."""
    world, manifest = ready(rig)
    run = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    rig.regenerate(4)
    fresh = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    rig.regenerate(3)
    run.check("probe")  # identical bytes: the same binding (a root rollback is out of scope)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        fresh.check("probe")


# --- reads through the production SmartRecruiters route ---------------------------------

ASSET = "https://jobs.smartrecruiters.com/oneclick-ui/static/app.js"
READS = (("GET", URL, True, "document"), ("GET", ASSET, False, "script"), ("HEAD", ASSET, False, "fetch"))
FORWARDED, ABORTED = ["fetch", "fulfill"], ["abort:blockedbyclient"]


class _Route:
    """Fake Playwright route; ``fetch`` stands in for the transport, so nothing leaves."""

    def __init__(self, method, url, navigation, resource):
        self.request = SimpleNamespace(method=method, url=url, resource_type=resource,
                                       is_navigation_request=lambda: navigation)
        self.events: list[str] = []

    def fetch(self, **options):
        assert options.get("max_redirects") == 0
        self.events.append("fetch")
        return SimpleNamespace(status=200)

    def fulfill(self, response):
        self.events.append("fulfill")

    def abort(self, reason):
        self.events.append("abort:" + reason)

    def fallback(self):
        self.events.append("fallback")


class _Context:
    def new_page(self):
        return SimpleNamespace(set_default_timeout=lambda _ms: None)

    def route(self, _pattern, handler):
        self.handler = handler

    def route_web_socket(self, _pattern, _handler):
        pass

    def on(self, _event, _handler):
        pass


def production_route() -> tuple[_Context, SmartRecruitersPolicyBrowser]:
    """The production browser and boundary with the production ``fetch`` transport."""
    context = _Context()
    boundary = PolicyNetworkBoundary("ExampleCo", "744000000000001", "Synthetic_CV.pdf")
    return context, SmartRecruitersPolicyBrowser(context, boundary)


def read_all(context: _Context) -> list[list[str]]:
    routes = [_Route(*read) for read in READS]
    for route in routes:
        context.handler(route)  # the handler the browser registered with context.route
    return [route.events for route in routes]


def test_production_route_forwards_no_read_while_no_guard_is_bound():
    context, browser = production_route()
    assert read_all(context) == [ABORTED] * len(READS)
    assert browser.boundary.blocked_writes == len(READS)
    browser.bind_guard(lambda point: None)  # positive control: a bound, allowing guard
    assert read_all(context) == [FORWARDED] * len(READS)


@pytest.mark.parametrize("change", ("revoke", "remove_pointer", "expire", "new_generation",
                                    "stop", "eio"))
def test_production_route_denies_later_reads_after_a_change(rig, change):
    world, manifest = ready(rig)
    context, browser = production_route()
    guard = item_guard(engine_queue.runtime_guard(rig.root),
                       StandingRun(rig.root, rig.cv_dir, manifest, rig.clock))
    browser.bind_guard(guard)
    # Positive control: every read asks the real guard (fresh trusted read) and is forwarded.
    assert read_all(context) == [FORWARDED] * len(READS)
    assert rig.points == ["network:read"] * len(READS)
    _mutations(rig)[change]()
    # Reads after an earlier successful guard: each asks again and none is forwarded.
    assert read_all(context) == [ABORTED] * len(READS)
    assert rig.points == ["network:read"] * (2 * len(READS))


class RouteBrowser(NetBrowser):
    """Fake page whose background reads go through the production route and boundary.

    The runtime binds its guard once; it reaches the production boundary through
    the production ``bind_guard``. Reads happen after the runtime's own guard
    point passed: once after ``open`` and before every write.
    """

    def __init__(self, doc, clock, log, change=None):
        super().__init__(doc, clock, log)
        self.context, self.sr = production_route()
        self.change, self.reads = change, []

    def bind_guard(self, check):
        super().bind_guard(check)
        self.sr.bind_guard(check)

    def open(self, url):
        super().open(url)
        self.reads.append(read_all(self.context))

    def write(self, key, control, value):
        if self.change is not None:  # lands after the runtime's write guard point passed
            change, self.change = self.change, None
            change()
        self.reads.append(read_all(self.context))
        super().write(key, control, value)


def use_route_browser(rig: Rig, change=None) -> None:
    def factory(document):
        rig.log.append("factory")
        browser = RouteBrowser(document, rig.clock, rig.log, change)
        rig.browsers.append(browser)
        return browser
    rig.queue.browser_factory = factory


def test_engine_run_reads_through_the_production_route_ask_the_bound_guard(rig):
    world, manifest = ready(rig)
    use_route_browser(rig)
    (report,), _ = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED", report
    browser = rig.browsers[0]
    assert len(browser.reads) >= 2 and all(r == [FORWARDED] * len(READS) for r in browser.reads)
    assert rig.points.count("network:read") == len(READS) * len(browser.reads)


@pytest.mark.parametrize("change", ("revoke", "remove_pointer", "expire", "new_generation",
                                    "stop", "eio"))
def test_engine_run_denies_reads_after_a_change_between_guard_points(rig, change):
    world, manifest = ready(rig)
    use_route_browser(rig, change=lambda: _mutations(rig)[change]())
    (report,), outbox = rig.run()
    browser = rig.browsers[0]
    first, *later = browser.reads
    assert first == [FORWARDED] * len(READS)  # the read after open, before the change
    assert later and all(r == [ABORTED] * len(READS) for r in later)
    assert report.status == "BLOCKED" and browser.clicks == 0 and "submit" not in browser.calls
    (result,) = outbox.values()
    assert result["submit_clicks"] == 0 and rig.status(world) == "Released"


# --- clock -----------------------------------------------------------------------------

class Script:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def test_clock_going_backwards_never_revives_an_expired_envelope(rig):
    world, manifest = ready(rig)
    later = NOW + timedelta(seconds=30)
    control = StandingRun(rig.root, rig.cv_dir, manifest, Script(NOW, later, later))
    control.check("probe")
    control.check("probe")  # positive control: a steady clock keeps passing
    run = StandingRun(rig.root, rig.cv_dir, manifest, Script(NOW, later, EXPIRES, later, NOW))
    run.check("probe")
    for _ in range(3):  # expired, then the clock steps back twice
        with pytest.raises(PermissionError, match=f"^{ERROR}$"):
            run.check("probe")


@pytest.mark.parametrize("bad", (datetime(2026, 11, 17, 5, 30), "2026-11-17T05:30:00Z", None))
def test_unusable_clock_denies(rig, bad):
    world, manifest = ready(rig)
    run = StandingRun(rig.root, rig.cv_dir, manifest, Script(NOW, NOW, bad))
    run.check("probe")
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        run.check("probe")
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        StandingRun(rig.root, rig.cv_dir, manifest, Script(bad))


def test_non_utc_aware_clock_is_normalized(rig):
    world, manifest = ready(rig)
    istanbul = timezone(timedelta(hours=3))
    run = StandingRun(rig.root, rig.cv_dir, manifest,
                      Script(NOW.astimezone(istanbul), NOW.astimezone(istanbul)))
    run.check("probe")


# --- EIO on a trusted source read ------------------------------------------------------

def test_eio_on_a_trusted_source_read_denies_admission_and_later_checks(rig):
    world, manifest = ready(rig)
    run = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)  # positive control
    run.check("probe")
    rig.view.eio = True
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        run.check("probe")
    assert error.value.__cause__ is error.value.__context__ is None
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    rig.view.eio = False
    run.check("probe")


# --- shared daily cap across generations -----------------------------------------------

def test_daily_cap_is_shared_across_generations_and_duplicates_stay_blocked(rig):
    first = make_world()
    second = make_world(execution_id="exec-20261117-example-2", second_target=True)
    retry = make_world(execution_id="exec-20261117-example-1-retry")  # same target as first
    again = make_world(execution_id="exec-20261117-example-1-again")  # and once more
    manifest_a = rig.add(first)
    rig.publish(3)
    rig.enqueue(manifest_a, "a.json")
    (report,), _ = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED"

    manifest_b, manifest_r, manifest_g = rig.add(second, 3), rig.add(retry, 3), rig.add(again, 3)
    rig.regenerate(4)  # a new owner generation covering all three executions
    # The new generation admits B, so only the shared cap can stop it.
    assert StandingRun(rig.root, rig.cv_dir, manifest_b, rig.clock).binding.generation == 4
    rig.enqueue(manifest_b, "b.json")
    (report,), _ = rig.run()
    assert report.status == "CAP_REACHED" and rig.browsers[-1].calls == []
    assert rig.status(second) is None

    # A repeated duplicate of the confirmed target, never reopened by a generation.
    for name, raw in (("r.json", manifest_r), ("g.json", manifest_g)):
        rig.enqueue(raw, name)
        (report,), _ = rig.run()
        assert report.status == "DUPLICATE" and rig.browsers[-1].calls == []
    rig.enqueue(manifest_r, "r-again.json")  # the very same manifest again
    (report,), _ = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_DUPLICATE_EXECUTION_ID")
    assert rig.status(first) == "Confirmed"


def test_positive_control_second_target_submits_on_a_fresh_ledger(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    second = make_world(execution_id="exec-20261117-example-2", second_target=True)
    manifest = rig.add(second, 4)
    rig.publish(4)
    rig.enqueue(manifest)
    (report,), _ = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED"


def _reserve(ledger, index: int, now: datetime) -> str:
    return ledger.reserve(opportunity_key=f"opp:synthetic:{index}", requisition_id=f"REQ-SYN-{index}",
                          profile_version="profile-2026-11-16", execution_id=f"exec-synthetic-{index}",
                          manifest_sha256="a" * 64, rollout_stage="R2", daily_cap=1, now=now)


def _start(ledger, index: int, now: datetime) -> None:
    ledger.mark_started(opportunity_key=f"opp:synthetic:{index}", requisition_id=f"REQ-SYN-{index}",
                        profile_version="profile-2026-11-16", execution_id=f"exec-synthetic-{index}",
                        now=now)


def _parallel(ledger, indexes, now) -> list[str]:
    barrier = threading.Barrier(len(indexes))
    outcomes: list[str] = []
    lock = threading.Lock()

    def work(index):
        barrier.wait()
        try:
            outcome = _reserve(ledger, index, now)
        except Exception as error:  # noqa: BLE001
            outcome = type(error).__name__
        with lock:
            outcomes.append(outcome)
    threads = [threading.Thread(target=work, args=(i,)) for i in indexes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def test_parallel_cap_reservation_admits_at_most_one_per_istanbul_day(tmp_path):
    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "p" / "ledger.db"), now=NOW)
    outcomes = _parallel(ledger, range(1, 9), NOW)
    assert outcomes.count("RESERVED") == 1 and set(outcomes) <= {"RESERVED", "BUSY", "CAP_REACHED"}
    winner = next(i for i in range(1, 9) if ledger.status(f"opp:synthetic:{i}", f"REQ-SYN-{i}",
                                                           "profile-2026-11-16") == "Reserved")
    _start(ledger, winner, NOW + timedelta(seconds=1))
    later = NOW + timedelta(minutes=1)
    assert set(_parallel(ledger, range(11, 19), later)) == {"CAP_REACHED"}
    assert ledger.started_today(later) == 1
    # Positive control: the next Istanbul calendar day has its own single slot.
    tomorrow = datetime(2026, 11, 17, 21, 5, tzinfo=timezone.utc)  # 00:05 Istanbul, 18 Nov
    assert _parallel(ledger, range(21, 29), tomorrow).count("RESERVED") == 1


def test_clock_stepping_back_across_midnight_does_not_reopen_the_cap(tmp_path):
    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "p" / "ledger.db"), now=NOW)
    tomorrow = datetime(2026, 11, 17, 21, 5, tzinfo=timezone.utc)  # 00:05 Istanbul, 18 Nov
    assert _reserve(ledger, 1, tomorrow) == "RESERVED"
    _start(ledger, 1, tomorrow + timedelta(seconds=1))
    back = datetime(2026, 11, 17, 20, 55, tzinfo=timezone.utc)  # 23:55 Istanbul, 17 Nov
    assert _reserve(ledger, 2, back) == "CAP_REACHED"
    # Positive control: the following Istanbul day opens a new slot.
    assert _reserve(ledger, 3, tomorrow + timedelta(days=1)) == "RESERVED"


def test_mark_started_rechecks_the_floored_cap_day(tmp_path):
    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "p" / "ledger.db"), now=NOW)
    back = datetime(2026, 11, 17, 20, 50, tzinfo=timezone.utc)  # 23:50 Istanbul, 17 Nov
    assert _reserve(ledger, 1, back) == "RESERVED"
    _start(ledger, 1, back + timedelta(seconds=1))
    tomorrow = datetime(2026, 11, 17, 21, 5, tzinfo=timezone.utc)
    assert _reserve(ledger, 2, tomorrow) == "RESERVED"  # positive control: new day
    _start(ledger, 2, tomorrow + timedelta(seconds=1))
    assert _reserve(ledger, 3, tomorrow + timedelta(days=1)) == "RESERVED"
    with pytest.raises(Exception):
        _start(ledger, 3, back + timedelta(seconds=30))  # stepped back: floored to 18 Nov, full


def test_start_under_a_stepped_back_clock_is_refused_and_cap_holds(tmp_path):
    def reserve2(index, now):
        return ledger.reserve(opportunity_key=f"opp:synthetic:{index}", requisition_id=f"REQ-SYN-{index}",
                              profile_version="profile-2026-11-16", execution_id=f"exec-synthetic-{index}",
                              manifest_sha256="a" * 64, rollout_stage="R2", daily_cap=2, now=now)

    def release(index, now):
        ledger.release(opportunity_key=f"opp:synthetic:{index}", requisition_id=f"REQ-SYN-{index}",
                       profile_version="profile-2026-11-16", execution_id=f"exec-synthetic-{index}", now=now)

    ledger = enable_policy_submissions(SqliteSubmissionLedger(tmp_path / "p" / "ledger.db"), now=NOW)
    tomorrow = datetime(2026, 11, 17, 21, 5, tzinfo=timezone.utc)  # 00:05 Istanbul, 18 Nov
    back = datetime(2026, 11, 17, 20, 55, tzinfo=timezone.utc)  # 23:55 Istanbul, 17 Nov
    assert reserve2(1, tomorrow) == "RESERVED"
    _start(ledger, 1, tomorrow + timedelta(seconds=1))
    assert reserve2(2, back) == "RESERVED"  # one of two slots on the floored day is free
    with pytest.raises(Exception):
        _start(ledger, 2, back + timedelta(seconds=1))  # would be filed under 17 Nov
    release(2, back + timedelta(seconds=2))
    assert ledger.started_today(tomorrow) == 1 and ledger.started_today(back) == 0
    # Positive control: once the clock is back in order the second slot starts, then the cap holds.
    assert reserve2(4, tomorrow + timedelta(minutes=1)) == "RESERVED"
    _start(ledger, 4, tomorrow + timedelta(minutes=1, seconds=1))
    assert ledger.started_today(tomorrow) == 2
    assert reserve2(5, back + timedelta(minutes=1)) == "CAP_REACHED"
    assert reserve2(6, tomorrow + timedelta(minutes=2)) == "CAP_REACHED"


def test_unreadable_cap_count_rejects(rig):
    world, manifest = ready(rig)
    (rig.tmp / "private" / "ledger.db").write_bytes(b"not a database")
    (report,), _ = rig.run()
    assert report.status == "LEDGER_ERROR" and rig.browsers[0].calls == []


# --- no uid, path or environment override ------------------------------------------------

def test_production_code_has_no_override_hooks():
    for module in (standing_guard, standing_envelope):
        source = inspect.getsource(module)
        for word in ("environ", "getenv", "geteuid", "getuid", "TRUSTED_UID", "APPROVAL_ROOT="):
            assert word not in source, (module.__name__, word)
    queue_source = inspect.getsource(engine_queue)
    assert "environ" not in queue_source and "getenv" not in queue_source
    assert list(inspect.signature(EngineQueue.__init__).parameters) == [
        "self", "root", "ledger", "cv_dir", "browser_factory", "clock"]
    assert list(inspect.signature(StandingRun.__init__).parameters) == [
        "self", "root", "cv_dir", "raw", "clock"]
    assert standing_envelope._APPROVAL_ROOT == Path("/Library/Application Support/EJS/approval")


def test_environment_cannot_redirect_the_trusted_root(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    world, manifest = ready(rig)
    StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)  # positive control
    monkeypatch.setattr(standing_envelope, "_APPROVAL_ROOT",
                        Path("/Library/Application Support/EJS/approval"))
    if Path("/Library/Application Support/EJS/approval").exists():
        pytest.skip("a real approval root exists on this host; not touched by tests")
    for name in ("EJS_APPROVAL_ROOT", "EJS_STANDING_ROOT", "EJS_TRUSTED_UID", "HOME", "TMPDIR"):
        monkeypatch.setenv(name, str(rig.approval))
    (report,), _ = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_STANDING_ADMISSION_DENIED")
    assert rig.browsers == []


def test_caller_owned_approval_tree_is_not_trusted(rig):
    world, manifest = ready(rig)
    StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)  # positive control: root-presented
    rig.view.inodes.clear()  # the same files, owned by the agent's own uid
    (report,), _ = rig.run()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_STANDING_ADMISSION_DENIED")
    assert rig.browsers == []


@pytest.mark.parametrize("damage", ("symlinked-package", "shared-package-mode", "package-file-mode",
                                    "hardlinked-package-file"))
def test_package_path_and_owner_policy_cannot_be_bypassed(rig, damage):
    world, manifest = ready(rig)
    StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)  # positive control
    folder = rig.root / "packages" / package_name(world.template["execution_id"])
    if damage == "symlinked-package":
        elsewhere = rig.tmp / "elsewhere"
        folder.rename(elsewhere)
        folder.symlink_to(elsewhere)
    elif damage == "shared-package-mode":
        os.chmod(folder, 0o755)
    elif damage == "package-file-mode":
        os.chmod(folder / "eligibility.json", 0o644)
    else:
        os.link(folder / "eligibility.json", rig.tmp / "extra-link")
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    (report,), _ = rig.run()
    assert report.code == "ENGINE_STANDING_ADMISSION_DENIED" and rig.browsers == []


def test_binding_is_pinned_from_trusted_sources_only(rig):
    world, manifest = ready(rig)
    run = StandingRun(rig.root, rig.cv_dir, manifest, rig.clock)
    assert run.binding == BatchBinding(
        "env-synthetic-1", 3, digest((rig.approval / "envelope.json").read_bytes()),
        digest((rig.approval / "eligibility-index.json").read_bytes()))
    assert "synthetic" not in repr(run.binding)
