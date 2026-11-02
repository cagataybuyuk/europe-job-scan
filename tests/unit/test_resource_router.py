from dataclasses import FrozenInstanceError, replace
import pytest
from ejs.services.resource_router import Provider, Request, quota_band, route

CAPS = frozenset({'python_tests', 'fixture_browser', 'design', 'review', 'documentation'})
CI = Provider('github_ci', True, CAPS, True)
LOCAL = Provider('local_macbook', True, CAPS)
WORK = Provider('work', True, CAPS, True)
CODEX = Provider('codex', True, CAPS, True)
BASE = Request('synthetic-62', 'TEST', 'GD-005:TEST:62', 'low',
               frozenset({'python_tests'}), 'normal', 'offline', 'a' * 40, ('not_run',))

@pytest.mark.parametrize('percent,expected', [(0,'exhausted'),(1,'low'),(19,'low'),
    (20,'economical'),(49,'economical'),(50,'normal'),(100,'normal')])
def test_manual_thresholds(percent, expected):
    assert quota_band(percent) == expected

@pytest.mark.parametrize('value', [-1,101,True,20.5,'50',None])
def test_bad_quota(value):
    with pytest.raises(ValueError): quota_band(value)

@pytest.mark.parametrize('band', ['normal','economical','low','exhausted'])
def test_deterministic_ci_and_local_fallback(band):
    r = replace(BASE, quota_band=band)
    d = route(r, (WORK, LOCAL, CI))
    assert (d.provider,d.fallback_provider,d.stop_reason) == ('github_ci','local_macbook',None)
    assert route(r, (replace(CI,available=False), LOCAL)).provider == 'local_macbook'
    assert route(r, ()).provider == 'human_review'
    assert route(r, (replace(LOCAL,available=False),)).provider == 'human_review'

@pytest.mark.parametrize('band,expected', [('normal','work'),('economical','github_ci'),
                                         ('low','github_ci'),('exhausted','github_ci')])
def test_model_quota_conservation(band, expected):
    r = replace(BASE, risk_level='high', required_capabilities={'design'}, quota_band=band)
    assert route(r, (CI, WORK, CODEX)).provider == expected
    if band == 'normal':
        assert route(r, (CODEX,)).provider == 'codex'

@pytest.mark.parametrize('change', [dict(task_id=''),dict(environment='PROD'),dict(environment=''),
    dict(approval_ref=''),dict(risk_level='unknown'),dict(quota_band='unknown'),
    dict(network_requirement='unknown'),dict(required_capabilities=set()),
    dict(required_capabilities={'interactive_auth'}),dict(required_capabilities={'live_browser'}),
    dict(required_capabilities={'final_submit'}),dict(required_capabilities={'form_write'}),
    dict(required_capabilities={'upload'}),dict(required_capabilities={'applied_mutation'}),
    dict(source_sha='main'),dict(test_evidence=('raw data with spaces',))])
def test_fail_closed(change):
    d = route(replace(BASE, **change), (CI,LOCAL,WORK))
    assert d.provider == 'human_review' and d.stop_reason and d.fallback_provider is None
    assert not d.final_submit and d.permitted_actions == ()

@pytest.mark.parametrize('providers', [(CI,CI),(Provider('paid_api',True,CAPS),),
    (Provider('github_ci',True,{'interactive_auth'}),),
    (Provider('github_ci','true',CAPS),)])
def test_unverified_providers(providers):
    assert route(BASE, providers).provider == 'human_review'

def test_capability_and_network_matching():
    assert route(replace(BASE,network_requirement='required'),(LOCAL,)).provider == 'human_review'
    assert route(BASE,(replace(CI,capabilities={'documentation'}),)).provider == 'human_review'

def test_checkpoint_is_immutable_idempotent_and_complete():
    r = replace(BASE, required_capabilities=['python_tests'], test_evidence=['not_run'])
    d = route(r,(CI,LOCAL))
    assert d == route(r,(LOCAL,CI)) == route(r,(CI,LOCAL))
    assert d.checkpoint_ref != route(replace(r,test_evidence=('passed_synthetic',)),(CI,LOCAL)).checkpoint_ref
    assert d.checkpoint_ref != route(replace(r,source_sha='b'*40),(CI,LOCAL)).checkpoint_ref
    assert d.checkpoint_ref != route(r,(CI,)).checkpoint_ref
    with pytest.raises(FrozenInstanceError): d.provider = 'paid_api'
    assert isinstance(r.required_capabilities,frozenset) and isinstance(r.test_evidence,tuple)
    assert not d.final_submit and not d.permitted_actions

@pytest.mark.parametrize('change', [dict(task_id=None), dict(approval_ref=1),
    dict(risk_level=[]), dict(quota_band={}), dict(network_requirement=False),
    dict(source_sha=None), dict(required_capabilities=None),
    dict(required_capabilities='python_tests'),dict(required_capabilities=[1]),
    dict(test_evidence=None),dict(test_evidence='passed'),dict(test_evidence=[{}])])
def test_malformed_request_requires_human_review(change):
    d = route(replace(BASE, **change), (CI,))
    assert d.provider == 'human_review' and d.stop_reason == 'invalid_input_schema'
    assert not d.permitted_actions and not d.final_submit

@pytest.mark.parametrize('providers', [None, 'github_ci', [None], [dict(name='github_ci')],
    [replace(CI, name=None)], [replace(CI, capabilities=None)],
    [replace(CI, capabilities=[1])], [replace(CI, capabilities='python_tests')]])
def test_malformed_provider_requires_human_review(providers):
    assert route(BASE, providers).stop_reason == 'invalid_input_schema'

@pytest.mark.parametrize('invalid_request', [None, {}, 'TEST'])
def test_malformed_request_object(invalid_request):
    assert route(invalid_request, (CI,)).provider == 'human_review'
