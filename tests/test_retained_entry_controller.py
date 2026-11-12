"""Private-file handoff protocol, fake browser/runtime and temporary state only."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ejs.services.retained_entry_controller as module
from ejs.services.retained_entry_controller import RetainedEntryController


SHA = 'a' * 40
TOKEN = 'c' * 48


@pytest.fixture
def lane(tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'temporary-state'
    root.mkdir(mode=0o700)
    state = SimpleNamespace(root=root, control=root / 'entry-return-controller', occupied=False,
        dirty=False, wrong_head=False, clock=0.0, commands=[], observed=0, resumed=0, loaded=0,
        cancel=False, loader_error=False, mutate_scope=None, runtime_error=None,
        final={'status': 'EMPLOYER_CONFIRMED', 'applied': True, 'submit_click_attempts': 1})
    state.manifest = dict(target={'posting_id': 'synthetic-posting'}, execution_id='fixture:execution',
        opportunity_key='fixture:opportunity', candidate_profile_version='fixture:v1',
        cv={'sha256': 'b' * 64, 'approved_filename': 'fixture.pdf'}, read_urls=['https://example.test/static.js'])
    state.facts = {'private': 'SYNTHETIC PRIVATE FACT'}
    state.reviewed = deepcopy(state.manifest)
    state.reviewed['entry_return_review'] = {'observation_id': TOKEN}
    state.ledger = SimpleNamespace(has_intent=lambda: state.occupied)
    def git(command, **kwargs):
        assert command[:2] == ['git', '-C']
        return SimpleNamespace(stdout=('dirty' if state.dirty else '') if 'status' in command
                               else ('z' * 40 if state.wrong_head else SHA))
    monkeypatch.setattr(module.subprocess, 'run', git)
    monkeypatch.setattr(module, 'monotonic', lambda: state.clock)
    def observe():
        state.observed += 1
        assert state.occupied is False
        return {'observation_id': TOKEN, 'surface_fingerprint': 'd' * 64,
                'observed_at': datetime.now(timezone.utc).isoformat()}
    page = SimpleNamespace(is_closed=lambda: False)
    context = SimpleNamespace(pages=[page])
    page.context = context
    state.driver = SimpleNamespace(observe_entry_return=observe, page=page, context=context,
                                   _owned_page=page, _owned_context=context)
    state.page = page
    state.pump_calls = []
    def resume(manifest, facts, target, cv, **kwargs):
        state.resumed += 1
        assert target is state.target and cv == state.cv
        assert kwargs['authority'] is state.authority and kwargs['observation_id'] == TOKEN
        if state.runtime_error:
            state.occupied = True
            raise state.runtime_error('SYNTHETIC PRIVATE ERROR')
        return deepcopy(state.final)
    state.runtime = SimpleNamespace(resume_reviewed_return=resume)
    state.target, state.authority, state.cv = object(), object(), root / 'fixture.pdf'
    def load(mp, fp, **kwargs):
        state.loaded += 1
        assert mp == state.control / 'reviewed-manifest.json'
        assert fp == state.control / 'reviewed-facts.json'
        assert kwargs['source_sha'] == SHA
        assert kwargs['manifest_sha256'] == '1' * 64 and kwargs['facts_sha256'] == '2' * 64
        if state.loader_error: raise PermissionError('SYNTHETIC PRIVATE INPUT ERROR')
        reviewed = deepcopy(state.reviewed)
        if state.mutate_scope:
            reviewed[state.mutate_scope] = 'different'
        return reviewed, state.facts
    monkeypatch.setattr(module, 'load_reviewed_application_inputs', load)
    def pump(seconds):
        if state.cancel: raise KeyboardInterrupt()
        state.clock += seconds
        if state.commands:
            operation = state.commands.pop(0)
            operation(state)
        else:
            state.clock += 1000  # deterministic timeout, no actual waiting
    def wait_for_timeout(milliseconds):
        assert 0 < milliseconds <= 100
        state.pump_calls.append(milliseconds)
        pump(milliseconds / 1000)
    page.wait_for_timeout = wait_for_timeout
    state.run = lambda: RetainedEntryController(root, root / 'source', SHA, timeout_seconds=3).wait_for_review(
        state.runtime, state.driver, state.ledger, state.manifest, state.facts,
        state.target, state.cv, authority=state.authority)
    return state


def command(command_action, command_sequence, **changes):
    def write(state):
        session = json.loads((state.control / 'session.json').read_text())
        assert session['oauth_bridge_implemented'] is False
        assert session['native_fallback_allowed'] is False
        payload = {'session_id': session['session_id'], 'sequence': command_sequence, 'action': command_action}
        if command_action == 'resume_pinned_review':
            payload.update(manifest_sha256='1' * 64, facts_sha256='2' * 64)
        payload.update(changes)
        path = state.control / 'request.json'
        path.write_text(json.dumps(payload)); path.chmod(0o600)
    return write


def assert_clean(state):
    for name in ('active.lock', 'session.json', 'observation.json'):
        assert not (state.control / name).exists()


def test_same_owned_objects_resume_once_without_reopening_and_no_stdout(lane, capsys):
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    result = lane.run()
    assert result['status'] == 'EMPLOYER_CONFIRMED'
    assert lane.observed == lane.resumed == lane.loaded == 1
    assert capsys.readouterr().out == ''
    assert_clean(lane)


@pytest.mark.parametrize('action', ['stop', None])
def test_stop_or_deadline_is_bounded_without_observation_or_resume(lane, action):
    if action: lane.commands = [command(action, 1)]
    result = lane.run()
    assert result['status'] == ('HANDOFF_STOPPED' if action else 'HANDOFF_TIMED_OUT')
    assert lane.observed == lane.resumed == lane.loaded == 0
    assert_clean(lane)


def test_cancel_closes_private_controller_state_without_sensitive_output(lane, capsys):
    lane.cancel = True
    assert lane.run()['status'] == 'HANDOFF_CANCELLED'
    assert lane.observed == lane.resumed == 0
    assert capsys.readouterr().out == ''
    assert_clean(lane)


@pytest.mark.parametrize('occupied_when', ['initial', 'command'])
def test_occupied_scope_stops_before_observation(lane, occupied_when):
    if occupied_when == 'initial': lane.occupied = True
    else:
        def consume(state):
            state.occupied = True
            command('observe_return', 1)(state)
        lane.commands = [consume]
    assert lane.run()['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert lane.observed == lane.resumed == 0
    assert_clean(lane)


def test_occupied_ledger_is_rechecked_immediately_before_observation(lane):
    calls = []
    def occupied():
        calls.append(1)
        return len(calls) == 3
    lane.ledger.has_intent = occupied
    lane.commands = [command('observe_return', 1)]
    assert lane.run()['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert len(calls) == 3 and lane.observed == 0
    assert_clean(lane)


@pytest.mark.parametrize('changes', [{'session_id': 'foreign'}, {'sequence': True}, {'sequence': 0},
                                    {'path': '/unapproved'}, {'action': 'shell'}, {'code': 'print(1)'}])
def test_unbound_malformed_or_secret_command_is_not_executed(lane, changes):
    lane.commands = [command('observe_return', 1, **changes)]
    result = lane.run()
    assert result['status'] == 'BLOCKED'
    assert lane.observed == lane.resumed == lane.loaded == 0
    assert 'unapproved' not in json.dumps(result)
    assert_clean(lane)


def test_duplicate_observation_and_replayed_sequence_stop(lane):
    lane.commands = [command('observe_return', 1), command('observe_return', 1)]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.observed == 1 and lane.resumed == 0
    assert_clean(lane)


def test_observation_cannot_be_reissued_even_with_next_sequence(lane):
    lane.commands = [command('observe_return', 1), command('observe_return', 2)]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.observed == 1 and lane.resumed == 0


def test_resume_requires_observation_first(lane):
    lane.commands = [command('resume_pinned_review', 1)]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.observed == lane.resumed == lane.loaded == 0


@pytest.mark.parametrize('failure', ['loader', 'manifest_pin', 'dirty', 'head'])
def test_invalid_pins_or_changed_source_stop_before_resume(lane, failure):
    lane.commands = [command('observe_return', 1)]
    def review(state):
        state.loader_error = failure == 'loader'
        state.dirty = failure == 'dirty'
        state.wrong_head = failure == 'head'
        command('resume_pinned_review', 2, **({'manifest_sha256': 'invalid'} if failure == 'manifest_pin' else {}))(state)
    lane.commands.append(review)
    result = lane.run()
    assert result['status'] == 'BLOCKED' and lane.resumed == 0
    assert 'PRIVATE' not in json.dumps(result)
    assert_clean(lane)


@pytest.mark.parametrize('key', ['target', 'execution_id', 'opportunity_key', 'candidate_profile_version', 'cv', 'read_urls'])
def test_review_reload_cannot_broaden_original_scope(lane, key):
    lane.mutate_scope = key
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    assert lane.run()['status'] == 'BLOCKED' and lane.resumed == 0
    assert_clean(lane)


def test_new_manifest_must_bind_the_observed_nonce(lane):
    lane.reviewed['entry_return_review']['observation_id'] = 'wrong'
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    assert lane.run()['status'] == 'BLOCKED' and lane.resumed == 0


@pytest.mark.parametrize('exception', [KeyboardInterrupt, RuntimeError])
def test_uncertain_resume_cannot_report_zero_attempts_or_applied(lane, exception):
    lane.runtime_error = exception
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    result = lane.run()
    assert result['status'] == 'DUPLICATE_OR_UNCERTAIN'
    assert result['submit_click_attempts'] is None and result['applied'] is False
    assert lane.resumed == 1
    assert_clean(lane)


@pytest.mark.parametrize('timeout', [0, -1, 601, True, 1.0, float('nan'), float('inf')])
def test_unbounded_or_coerced_deadline_is_rejected_before_state(lane, timeout):
    with pytest.raises(PermissionError):
        RetainedEntryController(lane.root, lane.root, SHA, timeout_seconds=timeout)
    assert not lane.control.exists()


def test_private_files_owner_permissions_and_no_lock_override(lane):
    lane.control.mkdir(mode=0o700)
    lock = lane.control / 'active.lock'; lock.write_text('OTHER ACTIVE SESSION'); lock.chmod(0o600)
    assert lane.run()['status'] == 'BLOCKED'
    assert lock.read_text() == 'OTHER ACTIVE SESSION'
    assert lane.observed == lane.resumed == 0


def test_command_symlink_never_reads_or_deletes_target(lane, tmp_path):
    target = tmp_path / 'private-other'; target.write_text('SYNTHETIC PRIVATE DATA')
    def inject(state):
        (state.control / 'request.json').symlink_to(target)
    lane.commands = [inject]
    assert lane.run()['status'] == 'BLOCKED'
    assert target.read_text() == 'SYNTHETIC PRIVATE DATA'
    assert lane.observed == lane.resumed == 0


def test_controller_instance_cannot_replay_after_one_resume(lane):
    controller = RetainedEntryController(lane.root, lane.root, SHA)
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    def run():
        return controller.wait_for_review(lane.runtime, lane.driver, lane.ledger, lane.manifest,
            lane.facts, lane.target, lane.cv, authority=lane.authority)
    assert run()['status'] == 'EMPLOYER_CONFIRMED'
    assert run()['status'] == 'BLOCKED'
    assert lane.resumed == 1


@pytest.mark.parametrize('invalid', ['duplicate_key', 'permissions'])
def test_private_command_integrity_is_required(lane, invalid):
    def inject(state):
        session = json.loads((state.control / 'session.json').read_text())
        path = state.control / 'request.json'
        if invalid == 'duplicate_key':
            path.write_text('{"session_id":"' + session['session_id'] + '","sequence":1,"action":"stop","action":"observe_return"}')
        else:
            command('observe_return', 1)(state)
        path.chmod(0o644 if invalid == 'permissions' else 0o600)
    lane.commands = [inject]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.observed == lane.resumed == 0
    assert_clean(lane)


def test_real_private_loader_rejects_wrong_external_pin_before_resume(lane, monkeypatch):
    from tests.test_single_application_inputs import inputs
    from ejs.services.single_application_inputs import load_reviewed_application_inputs
    import hashlib
    reviewed, facts = inputs()
    stamp = datetime.now(timezone.utc).isoformat()
    reviewed['reviewed_at'] = stamp
    facts['facts'][0]['verified_at'] = stamp
    reviewed.update({key: deepcopy(lane.manifest[key]) for key in ('target', 'execution_id', 'opportunity_key')})
    lane.manifest = deepcopy(reviewed)
    reviewed['entry_return_review'] = dict(observation_id=TOKEN,
        surface_fingerprint=reviewed['steps'][0]['fingerprint'], reviewed_at=stamp,
        reviewer_ref=reviewed['reviewer_ref'], return_url=reviewed['steps'][0]['url'])
    monkeypatch.setattr(module, 'load_reviewed_application_inputs', load_reviewed_application_inputs)
    def review(state):
        for name, data in [('reviewed-manifest.json', reviewed), ('reviewed-facts.json', facts)]:
            path = state.control / name
            path.write_text(json.dumps(data)); path.chmod(0o600)
        correct = hashlib.sha256((state.control / 'reviewed-manifest.json').read_bytes()).hexdigest()
        command('resume_pinned_review', 2, manifest_sha256=correct, facts_sha256='0' * 64)(state)
    lane.commands = [command('observe_return', 1), review]
    result = lane.run()
    assert result['status'] == 'BLOCKED'
    assert lane.resumed == 0
    assert_clean(lane)


def run_for(state, seconds):
    return RetainedEntryController(state.root, state.root / 'source', SHA, timeout_seconds=seconds).wait_for_review(
        state.runtime, state.driver, state.ledger, state.manifest, state.facts,
        state.target, state.cv, authority=state.authority)


@pytest.mark.parametrize('slow_call,expected_observed', [(1, 0), (3, 0), (7, 1)])
def test_absolute_deadline_includes_source_preflight_and_pre_resume_checks(lane, monkeypatch, slow_call, expected_observed):
    original = module.subprocess.run
    calls = []
    def slow_source(*args, **kwargs):
        calls.append(kwargs['timeout'])
        assert 0 < kwargs['timeout'] <= 10
        result = original(*args, **kwargs)
        if len(calls) == slow_call: lane.clock += 601
        return result
    monkeypatch.setattr(module.subprocess, 'run', slow_source)
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    result = run_for(lane, 600)
    assert result['status'] == 'HANDOFF_TIMED_OUT'
    assert lane.observed == expected_observed and lane.resumed == 0
    assert_clean(lane)


def test_slow_external_pin_reload_cannot_resume_after_600_seconds(lane, monkeypatch):
    original = module.load_reviewed_application_inputs
    def slow_reload(*args, **kwargs):
        result = original(*args, **kwargs)
        lane.clock += 601
        return result
    monkeypatch.setattr(module, 'load_reviewed_application_inputs', slow_reload)
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    assert run_for(lane, 600)['status'] == 'HANDOFF_TIMED_OUT'
    assert lane.observed == lane.loaded == 1 and lane.resumed == 0
    assert_clean(lane)


def test_slow_observer_cannot_export_an_observation_after_deadline(lane, monkeypatch):
    original = lane.driver.observe_entry_return
    written = []
    write_once = RetainedEntryController._write_once
    def record(self, name, value):
        written.append(name)
        return write_once(self, name, value)
    monkeypatch.setattr(RetainedEntryController, '_write_once', record)
    def slow_observe():
        result = original()
        lane.clock += 601
        return result
    lane.driver.observe_entry_return = slow_observe
    lane.commands = [command('observe_return', 1)]
    assert run_for(lane, 600)['status'] == 'HANDOFF_TIMED_OUT'
    assert lane.observed == 1 and lane.resumed == 0
    assert written == ['active.lock', 'session.json']
    assert_clean(lane)


def test_git_checks_are_bounded_by_remaining_absolute_time(lane, monkeypatch):
    original = module.subprocess.run
    timeouts = []
    def record(*args, **kwargs):
        timeouts.append(kwargs['timeout'])
        result = original(*args, **kwargs)
        if len(timeouts) == 1: lane.clock += 2.5
        return result
    monkeypatch.setattr(module.subprocess, 'run', record)
    assert lane.run()['status'] == 'HANDOFF_TIMED_OUT'
    assert timeouts[:2] == [3.0, 0.5]


@pytest.mark.parametrize('mutation', ['closed', 'missing', 'foreign_page', 'foreign_context', 'detached'])
def test_idle_pump_rejects_closed_missing_or_foreign_owned_page(lane, mutation):
    if mutation == 'closed': lane.page.is_closed = lambda: True
    if mutation == 'missing': lane.driver.page = None
    if mutation == 'foreign_page': lane.driver.page = SimpleNamespace()
    if mutation == 'foreign_context': lane.driver.context = SimpleNamespace()
    if mutation == 'detached': lane.driver.context.pages = []
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.pump_calls == []
    assert lane.observed == lane.resumed == 0
    assert_clean(lane)


def test_idle_uses_owned_playwright_event_pump_and_detects_close_after_tick(lane):
    def close_page(state):
        state.page.is_closed = lambda: True
    lane.commands = [close_page]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.pump_calls == [100]
    assert lane.observed == lane.resumed == 0
    assert_clean(lane)


@pytest.mark.parametrize('changes', [
    {'observation_id': True}, {'observation_id': 'PRIVATE'},
    {'surface_fingerprint': 'x' * 64}, {'observed_at': '2026-11-11T00:00:00'},
    {'observed_at': '2026-11-11T00:00:00+03:00'}, {'observed_at': '2000-01-01T00:00:00+00:00'},
    {'observed_at': '2099-01-01T00:00:00+00:00'}, {'values': 'SYNTHETIC PRIVATE DATA'},
])
def test_observation_is_strict_value_free_fresh_utc_metadata(lane, monkeypatch, changes, capsys):
    original = lane.driver.observe_entry_return
    def invalid_observe():
        value = original(); value.update(changes)
        return value
    lane.driver.observe_entry_return = invalid_observe
    written = []
    write_once = RetainedEntryController._write_once
    def record(self, name, value):
        written.append(name)
        return write_once(self, name, value)
    monkeypatch.setattr(RetainedEntryController, '_write_once', record)
    lane.commands = [command('observe_return', 1)]
    result = lane.run()
    assert result['status'] == 'BLOCKED'
    assert 'observation.json' not in written
    assert lane.resumed == 0
    assert capsys.readouterr().out == ''
    assert 'PRIVATE' not in json.dumps(result)
    assert_clean(lane)


@pytest.mark.parametrize('initial,reviewed', [
    (None, {'strategy': 'native_verified_facts_first', 'decision_ref': 'USER-NATIVE-MVP-20261112'}),
    ({'strategy': 'native_verified_facts_first', 'decision_ref': 'USER-NATIVE-MVP-20261112'},
     {'strategy': 'apply_with_linkedin', 'decision_ref': 'USER-NATIVE-MVP-20261112'}),
    ({'strategy': 'native_verified_facts_first', 'decision_ref': 'USER-NATIVE-MVP-20261112'}, None),
    ({'strategy': 'native_verified_facts_first', 'verified_return_only': True},
     {'strategy': 'native_verified_facts_first', 'verified_return_only': 1}),
    ({'strategy': 'native_verified_facts_first', 'work_authorization': 'UNKNOWN'},
     {'strategy': 'native_verified_facts_first', 'work_authorization': 'YES'}),
    ({'strategy': 'native_verified_facts_first', 'sponsorship': 'UNKNOWN'},
     {'strategy': 'native_verified_facts_first', 'sponsorship': 'NOT_REQUIRED'}),
    ({'strategy': 'native_verified_facts_first', 'legal_answer_approved': False},
     {'strategy': 'native_verified_facts_first', 'legal_answer_approved': True}),
])
def test_return_review_cannot_change_optional_entry_strategy(lane, initial, reviewed):
    if initial is not None:
        lane.manifest['entry_policy'] = deepcopy(initial)
    if reviewed is not None:
        lane.reviewed['entry_policy'] = deepcopy(reviewed)
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    assert lane.run()['status'] == 'BLOCKED'
    assert lane.observed == 1 and lane.resumed == 0
    assert_clean(lane)


def test_missing_and_none_entry_policy_preserve_previous_scope(lane):
    lane.reviewed['entry_policy'] = None
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    assert lane.run()['status'] == 'EMPLOYER_CONFIRMED'
    assert lane.observed == lane.resumed == 1
    assert_clean(lane)


@pytest.mark.parametrize('status', ['EMPLOYER_CONFIRMED', 'SUBMITTED_UNCONFIRMED'])
@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_cleanup_failure_preserves_resumed_terminal_result(lane, monkeypatch, capsys, status, failure):
    lane.final = {'status': status, 'applied': status == 'EMPLOYER_CONFIRMED',
                  'submit_click_attempts': 1 if status == 'EMPLOYER_CONFIRMED' else None}
    resume = lane.runtime.resume_reviewed_return
    def effect(*args, **kwargs):
        lane.occupied = True
        return resume(*args, **kwargs)
    lane.runtime.resume_reviewed_return = effect
    cleanup = RetainedEntryController._cleanup
    def fail_cleanup(self):
        cleanup(self)
        raise failure('SYNTHETIC PRIVATE CLEANUP ERROR')
    monkeypatch.setattr(RetainedEntryController, '_cleanup', fail_cleanup)
    lane.commands = [command('observe_return', 1), command('resume_pinned_review', 2)]
    result = lane.run()
    assert result == {**lane.final, 'handoff_cleanup_incomplete': True}
    assert lane.occupied is True and lane.resumed == 1
    assert capsys.readouterr().out == ''
    assert_clean(lane)
