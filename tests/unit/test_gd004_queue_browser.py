from ejs.services.github_queue import GitHubQueueRunner
from ejs.services.apps_script_gateway import SignedAppsScriptGatewayClient
from tests.unit.test_gd004_queue import NodeTransport, SHA, SECRET, EXECUTION_ID


def test_python_real_apps_script_and_real_chromium_roundtrip():
    transport = NodeTransport()
    try:
        client = SignedAppsScriptGatewayClient(endpoint='https://script.google.com/macros/s/test/exec',
            secret=SECRET, source_sha=SHA, transport=transport)
        runner = GitHubQueueRunner(client)
        result = runner.run(queue_row=2, execution_id=EXECUTION_ID)
        assert result['state'] == 'completed'
        assert result['typed_status'] == 'rendered'
        assert result['assets_verified'] == 1
        assert result['mutation_count'] == result['upload_count'] == result['submit_count'] == 0
        assert runner.run(queue_row=2, execution_id=EXECUTION_ID)['browser_executed'] is False
    finally:
        transport.close()
