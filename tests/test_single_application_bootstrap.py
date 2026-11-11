"""Bootstrap regressions: no browser by default; never obscure a real result."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ejs.apps.single_application import __main__ as app


def test_canonical_state_does_not_follow_environment_derived_home(monkeypatch, tmp_path):
    import importlib
    import os
    from pathlib import Path
    import pwd
    expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / 'Local' / 'Projeler' / 'EJS' / '.ejs-local'
    # Model the Path.home() result after a caller redirects its environment;
    # do not alter the host's HOME or touch either state directory.
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'alternate'))
    importlib.reload(app)
    assert app._STATE_ROOT == expected
    assert app._LEDGER == expected / 'first-application.sqlite3'


@pytest.fixture
def bootstrap(monkeypatch):
    calls = []
    manifest = {'target': {'company': 'Version1', 'company_id': '593fe2a3e4b0ac9d00fb37f8',
        'posting_id': '744000159638089', 'publication_id': 'aaf6587c-8336-4454-adb1-e09661d9bdfd',
        'requisition_id': 'REF7089X',
        'posting_url': 'https://jobs.smartrecruiters.com/Version1/744000159638089--senior-business-analyst'},
        'cv': {'approved_filename': 'CV.pdf'}, 'read_urls': [],
        'opportunity_key': 'fixture:opportunity', 'execution_id': 'fixture:execution',
        'candidate_profile_version': 'fixture:v1'}
    state = SimpleNamespace(manifest=manifest, calls=calls, occupied=False, cleanup_fails=False,
        outcome_fails=False, outcome_wrote=True, dirty=False, wrong_head=False, playwright_stop_fails=False,
        result={'status': 'EMPLOYER_CONFIRMED', 'applied': True, 'evidence_ref': 'evidence:sha256:' + 'a' * 64})
    def git(command, **kwargs):
        text = ('dirty' if state.dirty else '') if 'status' in command else ('b' * 40 if state.wrong_head else 'a' * 40)
        return SimpleNamespace(stdout=text)
    monkeypatch.setattr(app.subprocess, 'run', git)
    monkeypatch.setattr(app, 'load_reviewed_application_inputs', lambda *a, **kw: (state.manifest, {}))
    monkeypatch.setattr(app, 'read_approved_cv', lambda *a: calls.append('cv'))
    def ledger(path):
        assert path == app._STATE_ROOT / 'first-application.sqlite3'
        calls.append('ledger')
        return SimpleNamespace(has_intent=lambda: state.occupied)
    monkeypatch.setattr(app, 'SqliteSubmissionLedger', ledger)
    class Resource:
        def close(self):
            calls.append('close')
            if state.cleanup_fails:
                raise RuntimeError('SYNTHETIC PRIVATE LIBRARY ERROR')
        def new_context(self, **kwargs):
            assert kwargs == {'service_workers': 'block', 'accept_downloads': False}
            calls.append('context')
            return Resource()
    @contextmanager
    def fake_playwright():
        calls.append('playwright')
        yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: Resource()))
        if state.playwright_stop_fails:
            raise RuntimeError('SYNTHETIC PRIVATE STOP ERROR')
    import playwright.sync_api
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', fake_playwright)
    monkeypatch.setattr(app, 'OneClickBrowser', lambda *args: calls.append('driver'))
    monkeypatch.setattr(app, 'SingleApplicationRuntime', lambda *args: SimpleNamespace(run=lambda *a, **kw: deepcopy(state.result)))
    def outcome(*args, **kwargs):
        calls.append('outcome')
        if state.outcome_fails:
            raise OSError('SYNTHETIC PRIVATE STORAGE ERROR')
        return {'local_outcome_write_performed': state.outcome_wrote}
    monkeypatch.setattr(app, 'write_confirmed_outcome', outcome)
    state.arguments = ['--manifest', '/synthetic/manifest.json', '--facts', '/synthetic/facts.json',
        '--manifest-sha256', 'a' * 64, '--facts-sha256', 'b' * 64,
        '--source-sha', 'a' * 40, '--cv', '/synthetic/CV.pdf']
    return state


def report(capsys):
    import json
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_default_validation_has_no_browser_or_ledger(bootstrap, capsys):
    assert app.main(bootstrap.arguments) == 0
    assert report(capsys)['status'] == 'INPUTS_VALIDATED'
    assert bootstrap.calls == ['cv']


@pytest.mark.parametrize('mutation', ['dirty', 'wrong_head', 'wrong_company_id'])
def test_unreviewed_source_or_upload_destination_stops_before_browser(bootstrap, capsys, mutation):
    if mutation == 'wrong_company_id':
        bootstrap.manifest['target']['company_id'] = 'a' * 24
    else:
        setattr(bootstrap, mutation, True)
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 2
    assert report(capsys)['status'] == 'BLOCKED'
    assert 'ledger' not in bootstrap.calls and 'playwright' not in bootstrap.calls


def test_occupied_canonical_ledger_never_opens_browser(bootstrap, capsys):
    bootstrap.occupied = True
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 2
    assert report(capsys)['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert 'playwright' not in bootstrap.calls


@pytest.mark.parametrize('outcome_failure,cleanup_failure', [(True, False), (False, True), (True, True)])
def test_local_failure_does_not_erase_authenticated_employer_result(bootstrap, capsys, outcome_failure, cleanup_failure):
    bootstrap.outcome_fails, bootstrap.cleanup_fails = outcome_failure, cleanup_failure
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 0
    result = report(capsys)
    assert result['status'] == 'EMPLOYER_CONFIRMED' and result['applied'] is True
    if outcome_failure:
        assert result['outcome_record_pending'] is True and result['local_outcome_write_performed'] is False


def test_idempotent_local_outcome_does_not_claim_new_write(bootstrap, capsys):
    bootstrap.outcome_wrote = False
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 0
    assert report(capsys)['local_outcome_write_performed'] is False


def test_playwright_stop_failure_has_one_preserved_terminal_result(bootstrap, capsys):
    bootstrap.playwright_stop_fails = True
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 0
    result = report(capsys)
    assert result['status'] == 'EMPLOYER_CONFIRMED' and result['applied'] is True
    assert result['cleanup_incomplete'] is True


def test_unconfirmed_dispatch_never_creates_applied_outcome(bootstrap, capsys):
    bootstrap.result = {'status': 'SUBMITTED_UNCONFIRMED', 'applied': False}
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application']) == 2
    assert report(capsys)['applied'] is False
    assert 'outcome' not in bootstrap.calls


def test_retained_option_requires_explicit_execution_option(bootstrap, capsys):
    assert app.main(bootstrap.arguments + ['--retain-reviewed-entry']) == 2
    assert report(capsys)['status'] == 'BLOCKED'
    assert bootstrap.calls == []


@pytest.mark.parametrize('timeout', ['0', '601'])
def test_invalid_retained_timeout_stops_before_browser(bootstrap, capsys, timeout):
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application', '--retain-reviewed-entry',
        '--handoff-timeout-seconds', timeout]) == 2
    assert report(capsys)['status'] == 'BLOCKED'
    assert bootstrap.calls == []


@pytest.mark.parametrize('retained', [False, True])
def test_handoff_controller_retains_same_runtime_until_bounded_result(bootstrap, capsys, monkeypatch, retained):
    bootstrap.result = {'status': 'HUMAN_HANDOFF_REQUIRED', 'applied': False}
    class Controller:
        def __init__(self, state_root, source_repo, source_sha, *, timeout_seconds):
            assert state_root == app._STATE_ROOT and source_sha == 'a' * 40 and timeout_seconds == 300
            assert 'close' not in bootstrap.calls
            bootstrap.calls.append('controller')
        def wait_for_review(self, runtime, driver, ledger, manifest, facts, target, cv_path, *, authority):
            assert manifest is bootstrap.manifest
            assert callable(runtime.run) and callable(ledger.has_intent)
            assert 'close' not in bootstrap.calls
            bootstrap.calls.append('retained-wait')
            return {'status': 'HANDOFF_TIMED_OUT', 'applied': False}
    monkeypatch.setattr(app, 'RetainedEntryController', Controller)
    flags = ['--execute-reviewed-first-application'] + (['--retain-reviewed-entry'] if retained else [])
    assert app.main(bootstrap.arguments + flags) == 2
    assert report(capsys)['status'] == ('HANDOFF_TIMED_OUT' if retained else 'HUMAN_HANDOFF_REQUIRED')
    assert bootstrap.calls.count('close') == 2
    assert ('retained-wait' in bootstrap.calls) is retained
    assert 'outcome' not in bootstrap.calls


def test_occupied_scope_prevents_retained_controller_as_well_as_browser(bootstrap, capsys, monkeypatch):
    bootstrap.occupied = True
    def forbidden(*args, **kwargs): pytest.fail('No retained controller may open consumed scope')
    monkeypatch.setattr(app, 'RetainedEntryController', forbidden)
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application', '--retain-reviewed-entry']) == 2
    assert report(capsys)['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert 'playwright' not in bootstrap.calls


def test_cancel_during_retained_wait_emits_only_after_cleanup(bootstrap, capsys, monkeypatch):
    bootstrap.result = {'status': 'HUMAN_HANDOFF_REQUIRED', 'applied': False}
    class Controller:
        def __init__(self, *args, **kwargs): pass
        def wait_for_review(self, *args, **kwargs):
            assert 'close' not in bootstrap.calls
            raise KeyboardInterrupt()
    monkeypatch.setattr(app, 'RetainedEntryController', Controller)
    assert app.main(bootstrap.arguments + ['--execute-reviewed-first-application', '--retain-reviewed-entry']) == 2
    assert report(capsys)['status'] == 'CANCELLED'
    assert bootstrap.calls.count('close') == 2
    assert 'outcome' not in bootstrap.calls


@pytest.mark.parametrize('status', ['EMPLOYER_CONFIRMED', 'SUBMITTED_UNCONFIRMED'])
@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_actual_controller_cleanup_failure_keeps_one_truthful_cli_report(
        bootstrap, monkeypatch, tmp_path, capsys, status, failure):
    from datetime import datetime, timezone
    import ejs.services.retained_entry_controller as controller_module
    from ejs.services.retained_entry_controller import RetainedEntryController
    private = tmp_path.resolve() / 'temporary-canonical-state'
    private.mkdir(mode=0o700)
    monkeypatch.setattr(app, '_STATE_ROOT', private)
    monkeypatch.setattr(app, '_LEDGER', private / 'first-application.sqlite3')
    monkeypatch.setattr(app, '_OUTCOME', private / 'first-application-outcome.json')
    token = 'c' * 48
    final = {'status': status, 'applied': status == 'EMPLOYER_CONFIRMED',
             'submit_click_attempts': 1 if status == 'EMPLOYER_CONFIRMED' else None}
    if status == 'EMPLOYER_CONFIRMED':
        final['evidence_ref'] = 'evidence:sha256:' + 'a' * 64
    def driver(context, *args):
        page = SimpleNamespace(context=context, is_closed=lambda: False)
        context.pages = [page]
        return SimpleNamespace(page=page, context=context, _owned_page=page, _owned_context=context,
            observe_entry_return=lambda: {'observation_id': token, 'surface_fingerprint': 'd' * 64,
                                         'observed_at': datetime.now(timezone.utc).isoformat()})
    class Runtime:
        def __init__(self, driver, ledger): pass
        def run(self, *args, **kwargs):
            return {'status': 'HUMAN_HANDOFF_REQUIRED', 'applied': False}
        def resume_reviewed_return(self, *args, **kwargs):
            bootstrap.calls.append('resume')
            bootstrap.occupied = True
            return deepcopy(final)
    def command(self):
        if not self._observed:
            return {'action': 'observe_return'}
        return {'action': 'resume_pinned_review', 'manifest_sha256': '1' * 64, 'facts_sha256': '2' * 64}
    def reviewed(*args, **kwargs):
        value = deepcopy(bootstrap.manifest)
        value['entry_return_review'] = {'observation_id': token}
        return value, {}
    cleanup = RetainedEntryController._cleanup
    def fail_cleanup(self):
        cleanup(self)
        raise failure('SYNTHETIC PRIVATE CLEANUP ERROR')
    monkeypatch.setattr(app, 'OneClickBrowser', driver)
    monkeypatch.setattr(app, 'SingleApplicationRuntime', Runtime)
    monkeypatch.setattr(controller_module, 'load_reviewed_application_inputs', reviewed)
    monkeypatch.setattr(RetainedEntryController, '_command', command)
    monkeypatch.setattr(RetainedEntryController, '_cleanup', fail_cleanup)
    result_code = app.main(bootstrap.arguments + ['--execute-reviewed-first-application', '--retain-reviewed-entry'])
    assert result_code == (0 if status == 'EMPLOYER_CONFIRMED' else 2)
    result = report(capsys)
    assert result['status'] == status and result['applied'] is final['applied']
    assert result['submit_click_attempts'] == final['submit_click_attempts']
    assert result['handoff_cleanup_incomplete'] is True
    assert bootstrap.occupied is True and bootstrap.calls.count('resume') == 1
    assert bootstrap.calls.count('close') == 2
    assert ('outcome' in bootstrap.calls) is (status == 'EMPLOYER_CONFIRMED')
