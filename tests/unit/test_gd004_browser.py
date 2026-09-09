from pathlib import Path

from ejs.contracts.github_executor import ExecutionProvider
from ejs.services.github_executor import GitHubReadOnlyBrowserExecutor, fixture_url


def test_github_hosted_synthetic_fixture_is_read_only():
    fixture = Path("src/ejs/apps/cloud_run/fixtures/smartrecruiters_smoke.html")
    executor = GitHubReadOnlyBrowserExecutor(provider=ExecutionProvider.GITHUB_HOSTED)
    result, evidence = executor.inspect(
        application_url=fixture_url(str(fixture)),
        observed_at="2026-09-09T12:00:00Z",
        source_sha="a" * 40,
        request_id="request:gd004:synthetic",
        execution_id="execution:gd004:synthetic",
        opportunity_key="opportunity:gd004:synthetic",
        requisition_id="gd004-synthetic",
        adapter_key="ats:smartrecruiters",
    )
    assert evidence["read_only_invariant_ok"] is True
    assert evidence["control_count"] == 5
    assert evidence["required_control_count"] == 5
    assert result.mutation_count == 0
    assert result.upload_count == 0
    assert result.submit_count == 0
    assert result.provider is ExecutionProvider.GITHUB_HOSTED
