from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest

from ejs.apps.github_runner.queue_main import protocol_preflight
from ejs.contracts.github_executor import ExecutionProvider, ExecutionResult
from ejs.services.apps_script_gateway import GatewayError, SignedAppsScriptGatewayClient
from ejs.services.github_queue import GitHubQueueRunner

ROOT = Path(__file__).resolve().parents[2]
SHA = 'a' * 40
SECRET = 'gd004-test-only-hmac-value'
EXECUTION_ID = 'execution:gd004:synthetic:' + SHA


class NodeTransport:
    """Real Python signer/client -> unchanged .gs gateway -> Python verifier."""
    def __init__(self):
        self.process = subprocess.Popen(['node', str(ROOT / 'tests/support/apps_script_harness.cjs')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)

    def command(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('Apps Script V8 harness terminated')
        response = json.loads(line)
        if isinstance(response, dict) and 'harness_error' in response:
            raise RuntimeError(response['harness_error'])
        return response

    def __call__(self, endpoint, body, timeout):
        return json.dumps(self.command({'request': json.loads(body)})).encode()

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()


class StubBrowser:
    def __init__(self, status='rendered', wrong_identity=False):
        self.calls = 0
        self.status = status
        self.wrong_identity = wrong_identity

    def inspect(self, **kwargs):
        self.calls += 1
        result = ExecutionResult(request_id=kwargs['request_id'],
            execution_id='wrong' if self.wrong_identity else kwargs['execution_id'],
            operation='browser_read', source_sha=kwargs['source_sha'], provider=ExecutionProvider.GITHUB_HOSTED,
            started_at=kwargs['observed_at'], completed_at=kwargs['observed_at'], typed_status=self.status,
            form_fingerprint='synthetic-form', runtime_fingerprint='synthetic-runtime').with_hash()
        return result, {}


class QueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.transport = NodeTransport()
        self.addCleanup(self.transport.close)
        self.client = SignedAppsScriptGatewayClient(endpoint='https://script.google.com/macros/s/test/exec',
            secret=SECRET, source_sha=SHA, transport=self.transport)

    def test_signed_protocol_crosses_python_and_real_apps_script(self):
        result = protocol_preflight(self.client, probe_id='integration')
        self.assertEqual(result['negative_cases'], 4)
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][2], 'READY')

    def test_complete_queue_asset_result_roundtrip_and_duplicate(self):
        browser = StubBrowser()
        runner = GitHubQueueRunner(self.client, browser=browser)
        result = runner.run(queue_row=2, execution_id=EXECUTION_ID)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['assets_verified'], 1)
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][6], result['result_hash'])
        replay = runner.run(queue_row=2, execution_id=EXECUTION_ID)
        self.assertFalse(replay['browser_executed'])
        self.assertEqual(browser.calls, 1)

    def test_changed_pdf_blocks_before_browser(self):
        self.transport.command({'action': 'alter_bytes'})
        browser = StubBrowser()
        with self.assertRaises(GatewayError):
            GitHubQueueRunner(self.client, browser=browser).run(queue_row=2, execution_id=EXECUTION_ID)
        self.assertEqual(browser.calls, 0)
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][2], 'CLAIMED')

    def test_wrong_row_or_identity_cannot_claim(self):
        for row, identity in ((3, EXECUTION_ID), (2, 'wrong')):
            with self.assertRaises(GatewayError):
                GitHubQueueRunner(self.client, browser=StubBrowser()).run(queue_row=row, execution_id=identity)
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][2], 'READY')

    def test_old_deployed_sha_is_rejected(self):
        self.client.source_sha = 'b' * 40
        with self.assertRaisesRegex(GatewayError, 'SOURCE_SHA_NOT_DEPLOYED'):
            GitHubQueueRunner(self.client).run(queue_row=2, execution_id=EXECUTION_ID)

    def test_blocked_browser_is_review_required(self):
        result = GitHubQueueRunner(self.client, browser=StubBrowser('access_blocked')).run(
            queue_row=2, execution_id=EXECUTION_ID)
        self.assertEqual(result['state'], 'review_required')
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][2], 'REVIEW_REQUIRED')

    def test_wrong_result_execution_cannot_be_reconciled(self):
        with self.assertRaisesRegex(GatewayError, 'RESULT_IDENTITY_MISMATCH'):
            GitHubQueueRunner(self.client, browser=StubBrowser(wrong_identity=True)).run(
                queue_row=2, execution_id=EXECUTION_ID)
        self.assertEqual(self.transport.command({'action': 'snapshot'})[1][2], 'CLAIMED')

    def test_gateway_failure_does_not_look_like_success(self):
        with self.assertRaises(GatewayError):
            self.client.post(operation='get_execution_payload', payload={'queue_row': 999},
                request_id='negative-row', execution_id=EXECUTION_ID)

    def test_arbitrary_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            SignedAppsScriptGatewayClient(endpoint='https://untrusted.example/exec', secret=SECRET, source_sha=SHA)


if __name__ == '__main__':
    unittest.main()
