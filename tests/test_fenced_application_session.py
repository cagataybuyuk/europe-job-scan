"""Retained identity and one-attempt proofs, synthetic local forms only."""
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from tests.test_prepare_network_fence import local_form, request_for, FIXTURE
from ejs.services.browser_worker import BrowserRuntimeConfig
from ejs.services.prepare_network_fence import PrepareBoundaryError
from ejs.services.smartrecruiters_executor import PlaywrightSmartRecruitersExecutor
import ejs.services.fenced_application_session as session_module


@pytest.fixture(scope='module')
def config():
    with sync_playwright() as p:
        executable = p.chromium.executable_path
    if not Path(executable).exists(): pytest.skip('Managed Chromium missing')
    return BrowserRuntimeConfig(executable_path=executable, navigation_timeout_ms=3000, settle_timeout_ms=20)


def test_retains_one_page_context_and_ephemeral_state_until_prepare(config):
    with local_form() as (target, seen):
        request = request_for(config, target)
        executor = PlaywrightSmartRecruitersExecutor(config)
        with executor.open_session(target) as session:
            page, context = session._page, session._context
            page.evaluate("window.ephemeralFixtureState='synthetic-review-marker'")
            observation = session.inspect()
            documents_before = list(seen)
            result = session.prepare(request, reviewed_observation_id=observation.observation_id)
            assert result.runtime_state.value == 'pre_submit_ready'
            assert session._page is page and session._context is context
            assert not page.is_closed()
            assert page.evaluate('window.ephemeralFixtureState') == 'synthetic-review-marker'
            assert seen == documents_before
            assert session.state == 'PRE_SUBMIT_READY'
            assert session.attempts == dict(form_value_write_attempts=7, file_upload_attempts=1, submit_attempts=0)
        assert page.is_closed()
        assert session.state == 'CLOSED'


def test_token_from_another_session_is_not_a_review(config):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri()) as first:
        token = first.inspect().observation_id
    with executor.open_session(FIXTURE.as_uri()) as second:
        second.inspect()
        with pytest.raises(PrepareBoundaryError, match='SESSION_OBSERVATION_MISMATCH'):
            second.prepare(request, reviewed_observation_id=token)
        assert second.state == 'BLOCKED'
        assert second.attempts['form_value_write_attempts'] == 0


def test_new_inspection_invalidates_previous_token(config):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri()) as session:
        old = session.inspect(); new = session.inspect()
        assert old.observation_id != new.observation_id
        with pytest.raises(PrepareBoundaryError, match='SESSION_OBSERVATION_MISMATCH'):
            session.prepare(request, reviewed_observation_id=old.observation_id)
        assert session.attempts['form_value_write_attempts'] == 0


@pytest.mark.parametrize('effect', [
    "document.getElementById('lastName').id='renamed';document.querySelector('label[for=lastName]').htmlFor='renamed'",
    "document.getElementById('lastName').maxLength=1",
    "document.body.insertAdjacentHTML('beforeend','<p>Sign in to continue</p>')",
    "document.body.insertAdjacentHTML('beforeend','<div id=captcha></div>')",
])
def test_observation_drift_stops_before_any_effect(config, effect):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri()) as session:
        observed = session.inspect()
        session._page.evaluate(effect)
        with pytest.raises(PrepareBoundaryError, match='SESSION_OBSERVATION_DRIFT'):
            session.prepare(request, reviewed_observation_id=observed.observation_id)
        assert session.state == 'BLOCKED'
        assert session.attempts['form_value_write_attempts'] == 0


def test_request_target_is_bound_to_session(config):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri()) as session:
        observed = session.inspect()
        with pytest.raises(PrepareBoundaryError, match='SESSION_TARGET_MISMATCH'):
            session.prepare(replace(request, application_url='https://unapproved.example/job'),
                            reviewed_observation_id=observed.observation_id)
        assert session.attempts['form_value_write_attempts'] == 0


def test_review_observation_expiry_is_monotonic(config, monkeypatch):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri(), observation_max_age_seconds=1) as session:
        observed = session.inspect()
        instant = session_module.monotonic() + 2
        monkeypatch.setattr(session_module, 'monotonic', lambda: instant)
        with pytest.raises(PrepareBoundaryError, match='SESSION_OBSERVATION_EXPIRED'):
            session.prepare(request, reviewed_observation_id=observed.observation_id)
        assert session.attempts['form_value_write_attempts'] == 0


def test_session_expiry_closes_browser_and_is_terminal(config, monkeypatch):
    executor = PlaywrightSmartRecruitersExecutor(config)
    with executor.open_session(FIXTURE.as_uri(), session_timeout_seconds=1) as session:
        page = session._page
        session.inspect()
        instant = session_module.monotonic() + 2
        monkeypatch.setattr(session_module, 'monotonic', lambda: instant)
        with pytest.raises(PrepareBoundaryError, match='SESSION_EXPIRED'): session.inspect()
        assert session.state == 'EXPIRED'
        assert page.is_closed()
        with pytest.raises(PrepareBoundaryError, match='SESSION_EXPIRED'): session.inspect()


def test_preparation_can_only_be_attempted_once(config):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    with executor.open_session(FIXTURE.as_uri()) as session:
        observed = session.inspect()
        assert session.prepare(request, reviewed_observation_id=observed.observation_id).runtime_state.value == 'pre_submit_ready'
        before = session.attempts
        with pytest.raises(PrepareBoundaryError):
            session.prepare(request, reviewed_observation_id=observed.observation_id)
        assert session.attempts == before
        with pytest.raises(PrepareBoundaryError): session.inspect()


def test_failed_mutation_retains_counters_and_cannot_retry(config):
    with local_form("firstName.addEventListener('input',()=>fetch('/autosave',{method:'POST'}).catch(()=>{}))") as (target, seen):
        executor = PlaywrightSmartRecruitersExecutor(config)
        request = request_for(config, target)
        with executor.open_session(target) as session:
            observed = session.inspect()
            result = session.prepare(request, reviewed_observation_id=observed.observation_id)
            assert result.error_code == 'PREPARE_NETWORK_BLOCKED'
            assert result.form_value_write_attempts == session.attempts['form_value_write_attempts'] == 1
            assert session.state == 'BLOCKED'
            with pytest.raises(PrepareBoundaryError): session.inspect()
            with pytest.raises(PrepareBoundaryError): session.prepare(request, reviewed_observation_id=observed.observation_id)
            assert all(method == 'GET' and path == '/application' for method, path in seen)
            assert session.attempts['form_value_write_attempts'] == 1


def test_initial_side_effect_stops_open_and_cleans_resources(config):
    with local_form("fetch('/unapproved',{method:'POST'}).catch(()=>{})") as (target, seen):
        session = PlaywrightSmartRecruitersExecutor(config).open_session(target)
        with pytest.raises(PrepareBoundaryError, match='PREPARE_NETWORK_BLOCKED'):
            with session: pass
        assert session.state == 'BLOCKED'
        assert session._context is None and session._browser is None
        assert seen == [('GET', '/application')]


@pytest.mark.parametrize('script, expected', [
    ("document.body.insertAdjacentHTML('beforeend','<iframe srcdoc=\"<input>\"></iframe>')", 'FRAME_ADAPTER_REQUIRED'),
    ("let host=document.createElement('div');document.body.append(host);host.attachShadow({mode:'open'}).innerHTML='<input>'", 'SHADOW_DOM_ADAPTER_REQUIRED'),
    ("document.body.insertAdjacentHTML('beforeend','<button type=button>Next</button>')", 'MULTISTEP_ADAPTER_REQUIRED'),
])
def test_unsupported_scopes_still_cannot_write(config, script, expected):
    with local_form(script) as (target, seen):
        request = request_for(config, target)
        with PlaywrightSmartRecruitersExecutor(config).open_session(target) as session:
            observed = session.inspect()
            assert observed.error_code == expected
            result = session.prepare(request, reviewed_observation_id=observed.observation_id)
            assert result.error_code == expected
            assert result.form_value_write_attempts == result.file_upload_attempts == 0


def test_close_and_exception_paths_cannot_reopen(config):
    session = PlaywrightSmartRecruitersExecutor(config).open_session(FIXTURE.as_uri())
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with session:
            page = session._page
            raise RuntimeError('synthetic failure')
    assert page.is_closed()
    with pytest.raises(PrepareBoundaryError, match='SESSION_CANNOT_REOPEN'): session.__enter__()
    assert session.attempts == dict(form_value_write_attempts=0, file_upload_attempts=0, submit_attempts=0)


@pytest.mark.parametrize('kwargs', [{'session_timeout_seconds': 0}, {'session_timeout_seconds': 901},
                                   {'session_timeout_seconds': True}, {'observation_max_age_seconds': 301}])
def test_no_unbounded_session_lifetime(config, kwargs):
    with pytest.raises(ValueError, match='INVALID_SESSION_TIMEOUT'):
        PlaywrightSmartRecruitersExecutor(config).open_session(FIXTURE.as_uri(), **kwargs)


@pytest.mark.parametrize('mode', ['retained', 'compatibility'])
def test_late_form_validity_autosave_cannot_certify_pre_submit(config, mode):
    script = """const form=document.getElementById('applicationForm');
      const original=form.checkValidity.bind(form);
      form.checkValidity=()=>{fetch('/late-autosave',{method:'POST'}).catch(()=>{});return original();};"""
    with local_form(script) as (target, seen):
        request = request_for(config, target)
        executor = PlaywrightSmartRecruitersExecutor(config)
        if mode == 'retained':
            with executor.open_session(target) as session:
                observed = session.inspect()
                result = session.prepare(request, reviewed_observation_id=observed.observation_id)
                assert session.state == 'BLOCKED'
        else:
            result = executor.execute(request)
        assert result.runtime_state.value == 'boundary'
        assert result.error_code == 'PREPARE_NETWORK_BLOCKED'
        assert result.review_gate_reached is False
        assert result.form_value_write_attempts == 7
        assert result.file_upload_attempts == 1
        assert result.submit_attempts == 0
        assert all(method == 'GET' and path == '/application' for method, path in seen)


@pytest.mark.parametrize('effect,expected', [
    ("document.body.insertAdjacentHTML('beforeend','<p>Sign in to continue</p>')", 'AUTH_BOUNDARY'),
    ("document.body.insertAdjacentHTML('beforeend','<div id=captcha></div>')", 'CAPTCHA_BOUNDARY'),
])
def test_late_validity_auth_challenge_stops_after_readback(config, effect, expected):
    script = f"""const form=document.getElementById('applicationForm');
      const original=form.checkValidity.bind(form);
      form.checkValidity=()=>{{{effect};return original();}};"""
    with local_form(script) as (target, seen):
        request = request_for(config, target)
        with PlaywrightSmartRecruitersExecutor(config).open_session(target) as session:
            observed = session.inspect()
            result = session.prepare(request, reviewed_observation_id=observed.observation_id)
            assert result.error_code == expected
            assert result.runtime_state.value == 'boundary'
            assert result.form_value_write_attempts == 7
            assert result.file_upload_attempts == 1
            assert result.submit_attempts == 0
            assert session.state == 'BLOCKED'


def test_retained_session_final_validation_rejects_expired_proposed_success(config, monkeypatch):
    executor = PlaywrightSmartRecruitersExecutor(config)
    request = request_for(config, FIXTURE.as_uri())
    original = executor._prepare_in_session
    def delay_after_executor_result(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.runtime_state.value == 'pre_submit_ready'
        instant = session_module.monotonic() + 901
        monkeypatch.setattr(session_module, 'monotonic', lambda: instant)
        return result
    monkeypatch.setattr(executor, '_prepare_in_session', delay_after_executor_result)
    with executor.open_session(FIXTURE.as_uri()) as session:
        observed = session.inspect()
        result = session.prepare(request, reviewed_observation_id=observed.observation_id)
        assert result.runtime_state.value == 'boundary'
        assert result.error_code == 'SESSION_EXPIRED'
        assert session.state == 'EXPIRED'
        assert result.form_value_write_attempts == 7
        assert result.file_upload_attempts == 1
        assert len(result.field_results) == 7
        assert len(result.file_results) == 1


def test_session_expiry_during_preparation_preserves_attempts(config, monkeypatch):
    from ejs.services.safe_field_writer import PlaywrightSafeFieldWriter
    original = PlaywrightSafeFieldWriter._readback
    calls = 0
    def expire_after_first_write(*args):
        nonlocal calls
        value = original(*args)
        calls += 1
        if calls == 2:
            instant = session_module.monotonic() + 901
            monkeypatch.setattr(session_module, 'monotonic', lambda: instant)
        return value
    monkeypatch.setattr(PlaywrightSafeFieldWriter, '_readback', staticmethod(expire_after_first_write))
    request = request_for(config, FIXTURE.as_uri())
    with PlaywrightSmartRecruitersExecutor(config).open_session(FIXTURE.as_uri()) as session:
        observed = session.inspect()
        result = session.prepare(request, reviewed_observation_id=observed.observation_id)
        assert result.error_code == 'SESSION_EXPIRED'
        assert session.state == 'EXPIRED'
        assert result.form_value_write_attempts == 1
        assert result.file_upload_attempts == 0
        assert session.attempts['form_value_write_attempts'] == 1


def test_keyboard_interrupt_during_open_closes_created_context(config, monkeypatch):
    from ejs.services.prepare_network_fence import PrepareNetworkFence
    contexts = []
    def cancel_after_context_creation(self, context):
        contexts.append(context)
        raise KeyboardInterrupt()
    monkeypatch.setattr(PrepareNetworkFence, 'install', cancel_after_context_creation)
    session = PlaywrightSmartRecruitersExecutor(config).open_session(FIXTURE.as_uri())
    with pytest.raises(KeyboardInterrupt):
        with session: pytest.fail('Cancelled session cannot enter its body')
    assert len(contexts) == 1
    assert session._context is session._browser is session._playwright_manager is None
    assert session.state == 'BLOCKED'
    assert session.attempts['form_value_write_attempts'] == 0


def test_formless_document_controls_can_complete_bounded_preparation(config):
    script = "const form=document.getElementById('applicationForm');form.replaceWith(...form.childNodes)"
    with local_form(script) as (target, seen):
        request = request_for(config, target)
        executor = PlaywrightSmartRecruitersExecutor(config)
        with executor.open_session(target) as session:
            observed = session.inspect()
            result = session.prepare(request, reviewed_observation_id=observed.observation_id)
            assert result.runtime_state.value == 'pre_submit_ready'
            assert session.attempts == dict(form_value_write_attempts=7, file_upload_attempts=1, submit_attempts=0)
            assert all(method == 'GET' and path == '/application' for method, path in seen)
