"""Reviewed bootstrap through real private inputs, grants and temporary SQLite.

Only browser/runtime are synthetic. No canonical DB or employer is contacted.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import pwd
import sqlite3
from types import SimpleNamespace

import pytest

from ejs.apps.reviewed_application import __main__ as app
from ejs.contracts.submit import submit_identity
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence import reviewed_scope_ledger
from ejs.services.reviewed_execution_scope import EXPECTED_FUTURE_DECISION_REF
from tests.test_reviewed_execution_scope import NOW, execution_case, raw, sha
from tests.test_reviewed_scope_ledger import make_legacy
from tests.test_single_application_inputs import inputs


def write_private(path, payload):
    path.write_bytes(payload)
    path.chmod(0o600)
    return sha(payload)


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    private = tmp_path.resolve() / "inputs"
    private.mkdir(mode=0o700)
    state_root = tmp_path.resolve() / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(app, "_STATE_ROOT", state_root)
    monkeypatch.setattr(app, "_LEDGER", state_root / "first-application.sqlite3")
    monkeypatch.setattr(app, "_OUTCOME", state_root / "devoteam-ref00063-outcome.json")
    monkeypatch.setattr(app, "_now", lambda: NOW)
    monkeypatch.setattr(reviewed_scope_ledger, "_now", lambda: NOW)
    manifest, facts = inputs()
    manifest.update(target=asdict(app._TARGET), source_sha="a" * 40, reviewed_at=NOW.isoformat(),
        candidate_profile_version="fixture:v2", execution_id="fixture:execution:second",
        opportunity_key="opportunity:fixture:scope2", reviewer_ref="review:fixture:independent",
        entry_policy=dict(strategy="native_verified_facts", decision_ref="USER-NATIVE-MVP-20261112"))
    manifest["steps"][0]["url"] = app._TARGET.entry_url
    facts["candidate_profile_version"] = "fixture:v2"
    cv = private / "CV.pdf"
    payload = b"%PDF-1.4\n% SYNTHETIC BOOTSTRAP FIXTURE ONLY\n%%EOF\n"
    write_private(cv, payload)
    manifest["cv"].update(sha256=sha(payload), size_bytes=len(payload), approved_filename="CV.pdf")
    mp, fp = private / "manifest.json", private / "facts.json"
    md, fd = write_private(mp, raw(manifest)), write_private(fp, raw(facts))
    arguments = ["--manifest", str(mp), "--facts", str(fp), "--manifest-sha256", md,
                 "--facts-sha256", fd, "--source-sha", "a" * 40, "--cv", str(cv)]
    state = SimpleNamespace(private=private, state_root=state_root, manifest=manifest, facts=facts,
        mp=mp, fp=fp, cv=cv, arguments=arguments, mode="confirmed", calls=[], dirty=False,
        wrong_source=False, close_fails=False, stop_fails=False, record_fails=False, opens=0)
    def git(command, **kwargs):
        assert kwargs["timeout"] == 10
        if "status" in command:
            return SimpleNamespace(stdout="dirty" if state.dirty else "")
        return SimpleNamespace(stdout="b" * 40 if state.wrong_source else "a" * 40)
    monkeypatch.setattr(app.subprocess, "run", git)
    real_open = app.open_existing_canonical_ledger
    def open_existing():
        state.opens += 1
        return real_open()
    monkeypatch.setattr(app, "open_existing_canonical_ledger", open_existing)
    class Resource:
        def close(self):
            state.calls.append("close")
            if state.close_fails:
                raise RuntimeError("SYNTHETIC PRIVATE CLEANUP BODY")
        def new_context(self, **kw):
            assert kw == dict(service_workers="block", accept_downloads=False)
            state.calls.append("context")
            return Resource()
    @contextmanager
    def fake_playwright():
        state.calls.append("playwright")
        yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: Resource()))
        if state.stop_fails:
            raise KeyboardInterrupt("SYNTHETIC PRIVATE INTERRUPT")
    import playwright.sync_api
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", fake_playwright)
    def driver(*args):
        state.calls.append("driver")
        return object()
    monkeypatch.setattr(app, "OneClickBrowser", driver)
    class Runtime:
        def __init__(self, browser, view):
            self.view = view
        def run(self, manifest, facts, target, cv, *, authority):
            state.calls.append("runtime")
            assert authority.submit_click_allowed
            assert target == app._TARGET and not self.view.has_intent()
            if state.mode == "blocked":
                return {"status": "BLOCKED", "applied": False, "submit_click_attempts": 0}
            key = submit_identity(manifest["opportunity_key"], target.requisition_id, manifest["candidate_profile_version"])
            attempt = SubmissionAttempt(submit_key=key, execution_id=manifest["execution_id"],
                opportunity_key=manifest["opportunity_key"], requisition_id=target.requisition_id,
                candidate_profile_version=manifest["candidate_profile_version"], policy_version="SUBMIT-1.0",
                validation_key="validation:bootstrap", requested_at=(NOW + timedelta(seconds=1)).isoformat(),
                status=SubmissionAttemptStatus.VALIDATED, ats_family="smartrecruiters", form_fingerprint="form:bootstrap")
            assert self.view.reserve_intent(attempt, NOW + timedelta(seconds=1))
            self.view.mark_started(key, manifest["execution_id"], NOW + timedelta(seconds=2))
            if state.mode == "interrupt_unconfirmed":
                raise KeyboardInterrupt("SYNTHETIC PRIVATE INTERRUPT")
            if state.mode == "unconfirmed":
                return {"status": "SUBMITTED_UNCONFIRMED", "applied": False, "submit_click_attempts": None}
            ref = "evidence:sha256:" + "b" * 64
            assert self.view.confirm(key, ConfirmationEvidence(submit_key=key,
                execution_id=manifest["execution_id"], requisition_id=target.requisition_id,
                evidence_type=ConfirmationType.ATS_ACKNOWLEDGEMENT, evidence_ref=ref,
                observed_at=(NOW + timedelta(seconds=3)).isoformat(), exact_identity_match=True), NOW + timedelta(seconds=4))
            if state.mode == "interrupt_confirmed":
                raise KeyboardInterrupt("SYNTHETIC PRIVATE INTERRUPT")
            return {"status": "EMPLOYER_CONFIRMED", "applied": True, "submit_click_attempts": 1,
                    "evidence_ref": ref, "untrusted_extra": "SYNTHETIC PRIVATE BODY"}
    run = Runtime.run
    def checked_run(self, *args, **kwargs):
        try:
            return run(self, *args, **kwargs)
        except Exception as error:
            state.runtime_error = repr(error)
            raise
    Runtime.run = checked_run
    monkeypatch.setattr(app, "SingleApplicationRuntime", Runtime)
    real_outcome = app.write_confirmed_outcome
    def outcome(*args, **kwargs):
        state.calls.append("outcome")
        if state.record_fails:
            raise OSError("SYNTHETIC PRIVATE OUTCOME BODY")
        return real_outcome(*args, **kwargs)
    monkeypatch.setattr(app, "write_confirmed_outcome", outcome)
    return state


def grant(bootstrap):
    legacy = make_legacy(bootstrap.state_root / "first-application.sqlite3")
    case = execution_case(legacy.snapshot())
    case["manifest"], case["facts"] = bootstrap.manifest, bootstrap.facts
    case["grant"]["manifest_sha256"] = sha(raw(bootstrap.manifest))
    case["grant"]["facts_sha256"] = sha(raw(bootstrap.facts))
    case["grant"]["cv_sha256"] = bootstrap.manifest["cv"]["sha256"]
    gp = bootstrap.private / "grant.json"
    pin = write_private(gp, raw(case["grant"]))
    bootstrap.grant_path = gp
    bootstrap.grant_record = case["grant"]
    bootstrap.grant_args = ["--execute-reviewed-application", "--grant", str(gp), "--grant-sha256", pin,
                            "--user-decision-ref", EXPECTED_FUTURE_DECISION_REF]
    return legacy


def report(capsys):
    text = capsys.readouterr().out
    assert "SYNTHETIC PRIVATE" not in text and "@" not in text and "/inputs/" not in text
    lines = text.splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_default_real_input_compiler_cv_and_source_validation_never_open_db_or_browser(bootstrap, capsys):
    assert app.main(bootstrap.arguments) == 0
    result = report(capsys)
    assert result["status"] == "INPUTS_VALIDATED" and result["submit_authorized"] is False
    assert result["browser_action_performed"] is False
    assert bootstrap.opens == 0 and bootstrap.calls == []
    assert not tuple(bootstrap.state_root.iterdir())


def test_fixed_canonical_paths_ignore_environment_home(monkeypatch, tmp_path):
    expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Local" / "Projeler" / "EJS" / ".ejs-local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "replacement"))
    importlib.reload(app)
    assert app._STATE_ROOT == expected
    assert app._LEDGER == expected / "first-application.sqlite3"
    assert app._OUTCOME == expected / "devoteam-ref00063-outcome.json"


@pytest.mark.parametrize("bad", ["dirty", "source", "target", "native", "facts_pin", "cv_bytes"])
def test_unreviewed_default_inputs_stop_without_state_or_browser(bootstrap, capsys, bad):
    if bad == "dirty":
        bootstrap.dirty = True
    elif bad == "source":
        bootstrap.wrong_source = True
    elif bad == "target":
        bootstrap.manifest["target"]["company_id"] = "a" * 24
    elif bad == "native":
        del bootstrap.manifest["entry_policy"]
    elif bad == "facts_pin":
        bootstrap.fp.write_bytes(b"{}")
    else:
        bootstrap.cv.write_bytes(b"%PDF-SYNTHETIC CHANGED CV")
    if bad in {"target", "native"}:
        pin = write_private(bootstrap.mp, raw(bootstrap.manifest))
        bootstrap.arguments[bootstrap.arguments.index("--manifest-sha256") + 1] = pin
    assert app.main(bootstrap.arguments) == 2
    assert report(capsys)["status"] == "BLOCKED"
    assert bootstrap.opens == 0 and bootstrap.calls == []


@pytest.mark.parametrize("missing", ["all", "decision", "grant", "pin"])
def test_execute_without_independent_concrete_grant_stops_before_db(bootstrap, capsys, missing):
    extras = ["--execute-reviewed-application"]
    if missing != "all":
        extras += ["--grant", str(bootstrap.private / "absent.json"), "--grant-sha256", "a" * 64,
                   "--user-decision-ref", EXPECTED_FUTURE_DECISION_REF]
        option = {"decision": "--user-decision-ref", "grant": "--grant", "pin": "--grant-sha256"}[missing]
        pos = extras.index(option)
        del extras[pos:pos + 2]
    assert app.main(bootstrap.arguments + extras) == 2
    assert report(capsys)["status"] == "BLOCKED"
    assert bootstrap.opens == 0 and bootstrap.calls == []
    assert not tuple(bootstrap.state_root.iterdir())


@pytest.mark.parametrize("state", ["missing", "empty", "symlink"])
def test_execution_never_initializes_or_replaces_missing_empty_canonical_state(bootstrap, capsys, state):
    grant(bootstrap)
    path = bootstrap.state_root / "first-application.sqlite3"
    path.unlink()
    if state == "empty":
        path.write_bytes(b"")
        path.chmod(0o600)
    elif state == "symlink":
        path.symlink_to(bootstrap.mp)
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    assert report(capsys)["status"] == "BLOCKED"
    assert bootstrap.calls == []
    if state == "missing":
        assert not path.exists()
    elif state == "empty":
        assert path.read_bytes() == b""
    else:
        assert path.is_symlink()


def test_real_pins_grant_sqlite_owner_and_outcome_flow_with_single_synthetic_runtime(bootstrap, capsys):
    legacy = grant(bootstrap)
    before = legacy.snapshot()
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 0, (bootstrap.calls, getattr(bootstrap, "runtime_error", "none"))
    result = report(capsys)
    assert result["status"] == "EMPLOYER_CONFIRMED" and result["applied"] is True
    assert result["local_outcome_write_performed"] is True and result["central_record_pending"] is True
    assert result["applications_write_performed"] is False
    assert bootstrap.calls.count("runtime") == 1 and bootstrap.calls.count("outcome") == 1
    assert legacy.snapshot() == before
    outcome = json.loads((bootstrap.state_root / "devoteam-ref00063-outcome.json").read_text())
    assert outcome["requisition_id"] == "REF00063" and outcome["status"] == "Applied"
    calls = list(bootstrap.calls)
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    assert report(capsys)["status"] == "DUPLICATE_OR_UNCERTAIN"
    assert bootstrap.calls == calls


@pytest.mark.parametrize("mode", ["blocked", "unconfirmed", "interrupt_unconfirmed"])
def test_failure_or_interruption_consumes_scope_without_retry_or_applied(bootstrap, capsys, mode):
    legacy = grant(bootstrap)
    bootstrap.mode = mode
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    result = report(capsys)
    assert result["applied"] is False
    assert result["status"] == ("BLOCKED" if mode == "blocked" else "SUBMITTED_UNCONFIRMED")
    assert not (bootstrap.state_root / "devoteam-ref00063-outcome.json").exists()
    calls = list(bootstrap.calls)
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    assert report(capsys)["status"] == "DUPLICATE_OR_UNCERTAIN"
    assert bootstrap.calls == calls


@pytest.mark.parametrize("failure", ["close", "stop", "record", "interrupt_after_confirm"])
def test_confirmed_result_survives_cleanup_reporting_or_return_interrupt(bootstrap, capsys, failure):
    grant(bootstrap)
    if failure == "close":
        bootstrap.close_fails = True
    elif failure == "stop":
        bootstrap.stop_fails = True
    elif failure == "record":
        bootstrap.record_fails = True
    else:
        bootstrap.mode = "interrupt_confirmed"
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 0
    result = report(capsys)
    assert result["status"] == "EMPLOYER_CONFIRMED" and result["applied"] is True
    assert result["central_record_pending"] is True
    if failure in {"record", "interrupt_after_confirm"}:
        assert result["local_outcome_write_performed"] is False and result["outcome_record_pending"] is True
    assert bootstrap.calls.count("runtime") == 1


@pytest.mark.parametrize("bad", ["mode", "pin", "symlink", "hardlink", "parent", "fifo"])
def test_private_pinned_byte_reader_rejects_unsafe_paths_without_leaking(bad, tmp_path):
    private = tmp_path.resolve() / "private"
    private.mkdir(mode=0o700)
    file = private / "review.json"
    pin = write_private(file, b'{"synthetic":true}')
    if bad == "mode":
        file.chmod(0o644)
    elif bad == "pin":
        pin = "f" * 64
    elif bad == "symlink":
        alias = private / "alias.json"
        alias.symlink_to(file)
        file = alias
    elif bad == "hardlink":
        os.link(file, private / "alias.json")
    elif bad == "parent":
        private.chmod(0o755)
    else:
        file.unlink()
        os.mkfifo(file, 0o600)
    with pytest.raises(PermissionError, match="^APPLICATION_PRIVATE_INPUT_REQUIRED$"):
        app.read_private_pinned_bytes(file, pin)


def test_changed_authenticated_legacy_snapshot_stops_before_migration(bootstrap, capsys):
    grant(bootstrap)
    path = bootstrap.state_root / "first-application.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE first_application SET validation_key_sha256=?", ("d" * 64,))
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    assert report(capsys)["status"] == "BLOCKED"
    assert bootstrap.calls == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_error_after_claim_commit_cannot_report_scope_unused_or_reopen_it(bootstrap, capsys, monkeypatch):
    grant(bootstrap)
    real_claim = app.claim_reviewed_preparation
    def interrupted_claim(*args, **kwargs):
        assert real_claim(*args, **kwargs) is not None
        raise OSError("SYNTHETIC PRIVATE POST-COMMIT ERROR")
    monkeypatch.setattr(app, "claim_reviewed_preparation", interrupted_claim)
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    result = report(capsys)
    assert result["status"] == "BLOCKED" and result["preparation_scope_consumed"] is True
    assert bootstrap.calls == []
    monkeypatch.setattr(app, "claim_reviewed_preparation", real_claim)
    assert app.main(bootstrap.arguments + bootstrap.grant_args) == 2
    assert report(capsys)["status"] == "DUPLICATE_OR_UNCERTAIN"
    assert bootstrap.calls == []
