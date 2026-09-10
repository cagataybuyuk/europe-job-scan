from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import secrets

from ejs.apps.github_runner.main import _assert_gd004_runtime_fence
from ejs.contracts.github_executor import sign_execution_request
from ejs.services.apps_script_gateway import GatewayError, SignedAppsScriptGatewayClient
from ejs.services.github_queue import GitHubQueueRunner


def protocol_preflight(client, *, probe_id):
    execution_id = f'protocol:{probe_id}'
    def signed(suffix, **extra):
        return sign_execution_request(secret=client.secret, source_sha=client.source_sha,
            operation='health', request_id=f'health:{probe_id}:{suffix}', execution_id=execution_id,
            nonce=secrets.token_hex(16), **extra)
    request = signed('replay')
    first = client.send(request)
    second = client.send(request)
    if first.get('status') != 'ok' or second.get('exact_replay') is not True:
        raise ValueError('live health/replay preflight failed')
    negatives = [
        (replace(signed('signature'), signature='0' * 64), 'GD004_SIGNATURE_INVALID'),
        (signed('expired', created_at=datetime.now(timezone.utc) - timedelta(minutes=10)),
         'GD004_REQUEST_EXPIRED'),
        (replace(signed('prod'), environment='PROD'), 'GD004_ENVIRONMENT_NOT_TEST'),
        (signed('replay', payload={'changed': True}), 'GD004_REQUEST_ID_PAYLOAD_COLLISION'),
    ]
    for request, expected in negatives:
        try:
            client.send(request)
        except GatewayError as error:
            if str(error) != expected:
                raise ValueError('unexpected control-plane rejection') from None
        else:
            raise ValueError('control-plane accepted a negative preflight')
    return {'health': 'passed', 'replay': 'passed', 'negative_cases': len(negatives)}


def main():
    parser = argparse.ArgumentParser(description='GD-004 signed TEST queue integration')
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--queue-row', type=int, required=True)
    parser.add_argument('--execution-id', required=True)
    args = parser.parse_args()
    _assert_gd004_runtime_fence()
    client = SignedAppsScriptGatewayClient(
        endpoint=os.environ['EJS_APPS_SCRIPT_ZERO_COST_URL_TEST'],
        secret=os.environ['EJS_HMAC_SHARED_SECRET_TEST'], source_sha=args.source_sha)
    protocol = protocol_preflight(client, probe_id=os.environ.get('GITHUB_RUN_ID') or secrets.token_hex(8))
    result = GitHubQueueRunner(client).run(queue_row=args.queue_row, execution_id=args.execution_id)
    print(json.dumps({'protocol_preflight': protocol, 'queue_result': result}, sort_keys=True))
    return 0 if (result['state'] == 'completed' or
                 result['state'] == 'replay' and result.get('queue_state') == 'COMPLETED') else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # No raw response, application payload, URL, token, filename, or document bytes.
        print(json.dumps({'error_type': type(error).__name__, 'state': 'blocked'}))
        raise SystemExit(2) from None
