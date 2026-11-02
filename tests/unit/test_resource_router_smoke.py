"""Exercise the actual CLI process, including exit status and output boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARGS = ['--environment', 'TEST', '--approval-ref', 'GD005:TEST:62', '--source-sha', 'a'*40]


def run(*args):
    return subprocess.run([sys.executable, '-m', 'ejs.apps.resource_router_smoke', *args],
                          cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('scenario,provider,code', [('ci_available','github_ci',0),
    ('local_only','local_macbook',0),('no_provider','human_review',2)])
def test_exhausted_quota_synthetic_flow(scenario, provider, code):
    result = run(*ARGS, '--scenario', scenario)
    assert result.returncode == code, result.stderr
    data = json.loads(result.stdout)
    assert data['mode'] == 'synthetic_advisory_only' and data['environment'] == 'TEST'
    assert data['quota_band'] == 'exhausted' and not data['provider_executed']
    assert data['test_evidence'] == ['not_run']
    assert data['decision']['provider'] == provider
    assert not data['decision']['final_submit'] and not data['decision']['permitted_actions']
    assert run(*ARGS, '--scenario', scenario).stdout == result.stdout


def test_defaults_never_assume_availability():
    result = run(*ARGS)
    assert result.returncode == 2
    assert json.loads(result.stdout)['decision']['provider'] == 'human_review'


@pytest.mark.parametrize('args', [[], ['--environment','PROD'],
    [*ARGS,'--remaining-percent','101'], [*ARGS,'--scenario','paid_api'],
    [*ARGS,'--execute'], [*ARGS,'--final-submit']])
def test_unsupported_execution_or_missing_scope(args):
    result = run(*args)
    assert result.returncode == 2 and not result.stdout


def test_invalid_source_is_not_echoed_as_evidence():
    args = [*ARGS[:-1], 'invalid-source', '--scenario', 'ci_available']
    result = run(*args)
    data = json.loads(result.stdout)
    assert result.returncode == 2 and data['source_sha'] is None
    assert data['decision']['provider'] == 'human_review'
