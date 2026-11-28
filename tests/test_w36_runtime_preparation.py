"""Synthetic W3.6 handoff through the real GD-006 queue, runtime and ledger."""
from datetime import timedelta
import json
from unittest.mock import Mock

import pytest

from ejs.persistence.policy_submission_ledger import enable_policy_submissions
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.current_fact_approval import SCHEMA
from ejs.services.current_manifest_compiler import ERROR, compile_current_manifest
from ejs.services.engine_queue import EngineQueue, _write_private
from ejs.services.execution_manifest import load_execution_manifest
from tests.test_engine_queue import enable, legacy_standing_admission, private_write, results  # noqa: F401
from tests.test_execution_manifest import NOW, encode
from tests.test_policy_application_runtime import PDF, Clock, FakeBrowser, two_page
from tests.test_reviewed_manifest_compiler import digest, inputs


def published_state(record):
    """Fixture for an authenticated publisher response; the writer has no role here."""
    raw = encode(record)
    return raw, digest(raw), record["generation"], record["profile_version"]


class GuardedFakeBrowser(FakeBrowser):
    def __init__(self, document, clock, check, **behavior):
        super().__init__(document, clock, **behavior)
        self.check = check

    def write(self, key, control, value):
        self.check(f"write:{key}")
        super().write(key, control, value)

    def upload(self, key, payload, filename):
        self.check("upload")
        return super().upload(key, payload, filename)

    def advance(self):
        self.check("advance")
        super().advance()

    def submit_once(self):
        # Simulates a last check at dispatch after the durable start record.
        self.check("dispatch")
        return super().submit_once()


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    class Handoff:
        def __init__(self):
            tmp = tmp_path.resolve()
            self.root = tmp / "queue"
            self.cv = tmp / "cv" / "Synthetic_CV.pdf"
            self.cv.parent.mkdir(mode=0o700)
            private_write(self.cv, PDF)
            self.clock = Clock()
            self.ledger = enable_policy_submissions(
                SqliteSubmissionLedger(tmp / "private" / "ledger.db"), now=NOW)
            self.browsers = []
            self.browser_behavior = {}
            self.read_current = Mock()
            self.queue = EngineQueue(self.root, ledger=self.ledger, cv_dir=self.cv.parent,
                                     browser_factory=self.browser_factory, clock=self.clock)
            self.mark_started = self.ledger.mark_started
            enable(self.root)  # Temporary, synthetic queue control only.
            self.approve()

        def approve(self, generation=11, execution_id=None):
            document, template, registry, snapshot = inputs()
            self.document, template = two_page(document), two_page(template)
            if execution_id is not None:
                self.document["execution_id"] = template["execution_id"] = execution_id
            for item in (self.document, template):
                item["cv"].update(sha256=digest(PDF), size_bytes=len(PDF))
            snapshot.update(cv_sha256=digest(PDF), form_fingerprint=template["form"]["fingerprint"])
            steps = {field["key"]: field.get("step", 0) for field in template["form"]["fields"]}
            for fact in snapshot["facts"]:
                fact["step"] = steps[fact["key"]]
            self.sources = tuple(map(encode, (template, registry, snapshot)))
            template_pin, registry_pin, snapshot_pin = map(digest, self.sources)
            self.approval = dict(schema_version=SCHEMA, generation=generation, status="active",
                                 profile_version=template["candidate_profile_version"],
                                 policy_version=template["policy"]["version"],
                                 issued_at=(NOW - timedelta(minutes=5)).isoformat(),
                                 expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                                 template_sha256=template_pin, registry_sha256=registry_pin,
                                 snapshot_sha256=snapshot_pin)
            self.read_current.return_value = published_state(self.approval)

        def compile(self, state):
            if state is None:
                raise PermissionError(ERROR)
            raw, pin, generation, profile = state
            return compile_current_manifest(raw, *self.sources, expected_approval_sha256=pin,
                                            expected_generation=generation,
                                            expected_profile_version=profile, now=self.clock())

        def prepare(self):
            return self.compile(self.read_current("prepare"))

        def enqueue(self, raw):
            _write_private(self.root / "inbox" / "application.json", raw)

        def browser_factory(self, _document):
            # Test-only seam: simulate admission inside the factory of the
            # unchanged queue. It owns one claimed fixture item per run.
            raw = (self.root / "processing" / "application.json").read_bytes()
            admitted = self.read_current("admission")
            if self.compile(admitted) != raw:
                raise PermissionError(ERROR)

            def check(point):
                current = self.read_current(point)
                if self.compile(current) != raw or current != admitted:
                    raise PermissionError(ERROR)

            def guarded_mark_started(**ids):
                check("before-start")
                self.mark_started(**ids)

            # The production runtime has no current-approval injection point.
            monkeypatch.setattr(self.ledger, "mark_started", guarded_mark_started)
            browser = GuardedFakeBrowser(self.document, self.clock, check, **self.browser_behavior)
            self.browsers.append(browser)
            return browser

        def status(self):
            target = self.document["target"]
            return self.ledger.status(target["opportunity_key"], target["requisition_id"],
                                      self.document["candidate_profile_version"])

    return Handoff()


def test_current_approval_reaches_confirmed_ledger_and_outbox(handoff):
    raw = handoff.prepare()
    handoff.enqueue(raw)
    report, = handoff.queue.run_once()
    result, = results(handoff.root).values()
    browser, = handoff.browsers
    assert report.status == result["status"] == "EMPLOYER_CONFIRMED"
    assert handoff.status() == "Confirmed"
    assert result["execution_id"] == handoff.document["execution_id"]
    assert result["manifest_sha256"] == digest(raw)
    assert result["submit_clicks"] == browser.clicks == handoff.ledger.started_today(NOW) == 1
    assert result["evidence_ref"] == "evidence:sha256:" + "e" * 64
    assert (handoff.root / "archive" / "application.json").read_bytes() == raw
    assert browser.values["email"] == "synthetic@example.invalid"
    assert "synthetic@example.invalid" not in json.dumps(result)
    points = [call.args[0] for call in handoff.read_current.call_args_list]
    assert points == ["prepare", "admission", *browser.calls[1:-1], "before-start", "dispatch"]


def test_missing_approval_cannot_prepare_queue_item(handoff):
    handoff.read_current.return_value = None
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        handoff.enqueue(handoff.prepare())
    assert handoff.queue.run_once() == []
    assert handoff.browsers == [] and handoff.status() is None


@pytest.mark.parametrize("failure", ["missing", "unavailable", "expired", "revoked", "rollback",
                                     "profile-mismatch", "altered-manifest"])
def test_point_of_use_denials_never_open_browser(handoff, failure):
    raw = handoff.prepare()
    record = handoff.approval
    if failure == "missing":
        handoff.read_current.return_value = None
    elif failure == "unavailable":
        handoff.read_current.side_effect = TimeoutError
    elif failure == "expired":
        handoff.clock.now = NOW + timedelta(minutes=10)
    elif failure == "revoked":
        handoff.read_current.return_value = published_state(dict(record, status="revoked", generation=12))
    elif failure == "rollback":
        _, pin, generation, profile = published_state(dict(record, generation=12))
        handoff.read_current.return_value = encode(record), pin, generation, profile
    elif failure == "profile-mismatch":
        handoff.read_current.return_value = published_state(dict(record, profile_version="synthetic-other"))
    elif failure == "altered-manifest":
        altered = json.loads(raw)
        next(f for f in altered["form"]["fields"] if f["key"] == "motivation")["answer"]["value"] = (
            "Altered synthetic explanation.")
        raw = encode(altered)
        assert load_execution_manifest(raw, now=NOW, expected_sha256=digest(raw))
    handoff.enqueue(raw)
    report, = handoff.queue.run_once()
    result, = results(handoff.root).values()
    assert (report.status, report.code) == ("BLOCKED", "ENGINE_BROWSER_UNAVAILABLE")
    assert result["status"] == "BLOCKED" and result["submit_clicks"] == 0
    assert handoff.browsers == [] and handoff.status() is None


@pytest.mark.parametrize(("point", "forbidden"), [
    ("write:first_name", "write:first_name"), ("upload", "upload"),
    ("advance", "advance"), ("before-start", "submit"), ("dispatch", "submit"),
])
def test_revocation_during_run_blocks_next_effect(handoff, point, forbidden):
    handoff.enqueue(handoff.prepare())
    revoked = published_state(dict(handoff.approval, status="revoked", generation=12))

    def read_current(action):
        if action == point:
            handoff.read_current.return_value = revoked
        return handoff.read_current.return_value

    handoff.read_current.side_effect = read_current
    report, = handoff.queue.run_once()
    browser, = handoff.browsers
    result, = results(handoff.root).values()
    assert forbidden not in browser.calls and browser.clicks == 0
    if point == "dispatch":
        assert report.status == "SUBMITTED_UNCONFIRMED" and result["submit_clicks"] == -1
        assert handoff.status() == "Submitted / Unconfirmed"
    else:
        assert report.status == "BLOCKED" and result["submit_clicks"] == 0
        assert handoff.status() == "Released"


def test_revocation_after_owner_handoff_preserves_owner_hold(handoff):
    handoff.browser_behavior["challenge_at"] = 1
    handoff.enqueue(handoff.prepare())
    active = handoff.read_current.return_value
    revoked = published_state(dict(handoff.approval, status="revoked", generation=12))
    handoff.read_current.side_effect = lambda point: active if point == "admission" else revoked
    report, = handoff.queue.run_once()
    browser, = handoff.browsers
    assert report.status == "BLOCKED" and handoff.status() == "Awaiting User"
    assert browser.calls == ["open", "wait_for_owner"]
    assert browser.clicks == handoff.ledger.started_today(NOW) == 0


def test_invalid_cv_releases_reservation_without_browser_effects(handoff):
    handoff.enqueue(handoff.prepare())
    private_write(handoff.cv, PDF[:-1] + b"1")  # Same size and PDF prefix, different bytes.
    report, = handoff.queue.run_once()
    browser, = handoff.browsers
    result, = results(handoff.root).values()
    assert (report.status, report.code) == ("BLOCKED", "POLICY_APP_CV")
    assert result["submit_clicks"] == 0 and handoff.status() == "Released"
    assert browser.calls == [] and handoff.ledger.started_today(NOW) == 0


@pytest.mark.parametrize("acknowledged", [True, False])
def test_duplicate_submission_is_blocked_across_approved_execution_ids(handoff, acknowledged):
    handoff.browser_behavior["ack"] = acknowledged
    handoff.enqueue(handoff.prepare())
    first, = handoff.queue.run_once()
    assert first.status == ("EMPLOYER_CONFIRMED" if acknowledged else "SUBMITTED_UNCONFIRMED")

    handoff.approve(generation=12, execution_id="exec-synthetic-second")
    handoff.enqueue(handoff.prepare())
    second, = handoff.queue.run_once()
    assert second.status == "DUPLICATE"
    initial_browser, duplicate_browser = handoff.browsers
    assert initial_browser.clicks == handoff.ledger.started_today(NOW) == 1
    assert duplicate_browser.calls == []
    assert handoff.status() == ("Confirmed" if acknowledged else "Submitted / Unconfirmed")
    assert sorted(row["status"] for row in results(handoff.root).values()) == ["DUPLICATE", first.status]
