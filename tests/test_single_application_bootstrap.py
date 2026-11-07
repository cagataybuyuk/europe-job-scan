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
