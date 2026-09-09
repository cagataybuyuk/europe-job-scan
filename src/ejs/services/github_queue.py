from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ejs.contracts.github_executor import canonical_json, sha256_hex
from ejs.services.apps_script_gateway import SignedAppsScriptGatewayClient
from ejs.services.github_executor import GitHubReadOnlyBrowserExecutor, fixture_url
from ejs.services.url_policy import PublicHttpsUrlPolicy

MAX_ASSET_BYTES = 2 * 1024 * 1024
FIXTURE = Path(__file__).resolve().parents[1] / 'apps/cloud_run/fixtures/smartrecruiters_smoke.html'


def validate_asset_bytes(response: Mapping[str, Any], approved: Mapping[str, Any]) -> bytes:
    if response.get('asset') != approved or approved.get('synthetic') is not True:
        raise ValueError('approved TEST asset metadata drift')
    size = approved.get('size_bytes')
    encoded = response.get('base64_data')
    if (type(size) is not int or not 5 <= size <= MAX_ASSET_BYTES
            or not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_ASSET_BYTES + 2) // 3)):
        raise ValueError('asset size bound')
    raw = base64.b64decode(encoded, validate=True)
    if (len(raw) != size or not raw.startswith(b'%PDF-')
            or approved.get('mime_type') != 'application/pdf'
            or sha256_hex(raw) != approved.get('sha256')):
        raise ValueError('asset bytes/hash mismatch')
    return raw


class GitHubQueueRunner:
    """Run one explicit TEST queue row, without employer mutations or candidate output."""

    def __init__(self, client: SignedAppsScriptGatewayClient, *, browser=None,
                 url_validator: Callable = PublicHttpsUrlPolicy.validate_resolved):
        self.client = client
        self.browser = browser or GitHubReadOnlyBrowserExecutor()
        self.url_validator = url_validator

    def run(self, *, queue_row: int, execution_id: str) -> dict[str, Any]:
        if type(queue_row) is not int or not 2 <= queue_row <= 201 or not execution_id:
            raise ValueError('explicit TEST queue row and execution ID required')
        base = {'queue_row': queue_row}

        def post(operation, payload):
            return self.client.post(operation=operation, payload=payload,
                                    request_id=f'{operation}:{execution_id}', execution_id=execution_id)

        snapshot = post('get_execution_payload', base)
        payload = snapshot.get('execution_payload')
        if (not isinstance(payload, Mapping) or payload.get('environment') != 'TEST'
                or payload.get('operation') != 'browser_read'
                or payload.get('execution_id') != execution_id
                or payload.get('source_sha') != self.client.source_sha
                or snapshot.get('queue_row') != queue_row
                or sha256_hex(canonical_json(dict(payload))) != snapshot.get('payload_hash')):
            raise ValueError('execution snapshot identity/hash mismatch')
        if snapshot['queue_state'] in ('COMPLETED', 'REVIEW_REQUIRED', 'CLAIMED'):
            return {'state': 'replay', 'queue_state': snapshot['queue_state'],
                    'execution_id': execution_id, 'browser_executed': False}
        if snapshot['queue_state'] != 'READY':
            raise ValueError('queue is not ready')
        if payload.get('fixture_key'):
            if payload['fixture_key'] != 'smartrecruiters_smoke' or payload.get('application_url'):
                raise PermissionError('unapproved fixture')
            target = fixture_url(str(FIXTURE))
        else:
            target = payload.get('application_url', '')
            decision = self.url_validator(target)
            if not decision.allowed:
                raise PermissionError(f'GD004_TARGET_{decision.code}')
        assets = payload.get('approved_assets')
        if not isinstance(assets, list) or len(assets) > 2:
            raise ValueError('approved asset list invalid')
        bound = {**base, 'expected_payload_hash': snapshot['payload_hash']}
        claim = post('claim', bound)
        if claim.get('execution_id') != execution_id:
            raise ValueError('claim identity mismatch')
        if claim.get('claim_state') == 'replay':
            return {'state': 'replay', 'queue_state': claim.get('queue_state'),
                    'execution_id': execution_id, 'browser_executed': False}
        if claim.get('claim_state') != 'claimed':
            raise ValueError('execution was not claimed')

        # Bytes are verified in memory and discarded. This phase cannot upload them.
        for approved in assets:
            asset = self.client.post(operation='get_approved_asset',
                payload={**bound, 'artifact_id': approved['artifact_id']},
                request_id=f"asset:{execution_id}:{approved['artifact_id']}", execution_id=execution_id)
            raw = validate_asset_bytes(asset, approved)
            del raw, asset
        result, _ = self.browser.inspect(application_url=target,
            observed_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            source_sha=self.client.source_sha, request_id=f'browser:{execution_id}',
            execution_id=execution_id, opportunity_key=payload['opportunity_key'],
            requisition_id=payload['requisition_id'], adapter_key=payload['adapter_key'])
        result.validate_gd004()
        result_value = {**result.unsigned_dict(), 'result_hash': result.result_hash}
        reconciled = post('reconcile_result', {**bound, 'result': result_value})
        if reconciled.get('result_hash') != result.result_hash:
            raise ValueError('reconcile result hash mismatch')
        # A second signed read verifies the durable record, not just the write response.
        after = post('get_execution_payload', base)
        expected_state = 'COMPLETED' if result.typed_status == 'rendered' else 'REVIEW_REQUIRED'
        if (after.get('result_hash') != result.result_hash or after.get('queue_state') != expected_state
                or after.get('payload_hash') != snapshot['payload_hash']):
            raise ValueError('independent queue readback failed')
        return {'state': 'completed' if expected_state == 'COMPLETED' else 'review_required',
                'execution_id': execution_id, 'browser_executed': True,
                'assets_verified': len(assets), 'typed_status': result.typed_status,
                'result_hash': result.result_hash, 'mutation_count': 0, 'upload_count': 0, 'submit_count': 0}
