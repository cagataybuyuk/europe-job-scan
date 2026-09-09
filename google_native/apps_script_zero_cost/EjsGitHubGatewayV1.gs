function ejsGhCanonicalJsonV1_(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) {
    return '[' + value.map(ejsGhCanonicalJsonV1_).join(',') + ']';
  }
  const type = typeof value;
  if (type === 'string' || type === 'boolean') return JSON.stringify(value);
  if (type === 'number') {
    if (!Number.isFinite(value)) throw new Error('GD004_NON_FINITE_NUMBER');
    return JSON.stringify(value);
  }
  if (type === 'object') {
    const keys = Object.keys(value).sort();
    return '{' + keys.map(function(key) {
      return JSON.stringify(key) + ':' + ejsGhCanonicalJsonV1_(value[key]);
    }).join(',') + '}';
  }
  throw new Error('GD004_UNSUPPORTED_CANONICAL_TYPE');
}

function ejsGhBytesToHexV1_(bytes) {
  return bytes.map(function(value) {
    const normalized = value < 0 ? value + 256 : value;
    return normalized.toString(16).padStart(2, '0');
  }).join('');
}

function ejsGhSha256HexV1_(value) {
  return ejsGhBytesToHexV1_(Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(value),
    Utilities.Charset.UTF_8
  ));
}

function ejsGhHmacHexV1_(value, secret) {
  return ejsGhBytesToHexV1_(Utilities.computeHmacSha256Signature(
    String(value),
    String(secret),
    Utilities.Charset.UTF_8
  ));
}

function ejsGhConstantTimeEqualV1_(left, right) {
  left = String(left || '');
  right = String(right || '');
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) {
    diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  }
  return diff === 0;
}

function ejsGhUnsignedRequestV1_(request) {
  return {
    contract_version: request.contract_version,
    request_id: request.request_id,
    execution_id: request.execution_id,
    operation: request.operation,
    created_at: request.created_at,
    expires_at: request.expires_at,
    nonce: request.nonce,
    source_sha: request.source_sha,
    environment: request.environment,
    capability_flags: request.capability_flags,
    payload: request.payload,
    payload_hash: request.payload_hash
  };
}

function ejsGhAssertCapabilitiesV1_(flags) {
  flags = flags || {};
  if (flags.browser_read !== true) throw new Error('GD004_BROWSER_READ_REQUIRED');
  if (flags.form_value_write === true) throw new Error('GD004_FORM_WRITE_FORBIDDEN');
  if (flags.approved_file_upload === true) throw new Error('GD004_FILE_UPLOAD_FORBIDDEN');
  if (flags.final_submit === true) throw new Error('GD004_FINAL_SUBMIT_FORBIDDEN');
}

function ejsGhCleanupReplayLedgerV1_(props, nowMs) {
  const all = props.getProperties();
  const prefix = 'EJS_GH_LEDGER_';
  const entries = [];
  Object.keys(all).forEach(function(key) {
    if (!key.startsWith(prefix)) return;
    try {
      const parsed = JSON.parse(all[key]);
      const expiresAtMs = Number(parsed.expires_at_ms || 0);
      if (expiresAtMs > 0 && expiresAtMs < nowMs) {
        props.deleteProperty(key);
      } else {
        entries.push({key: key, created_at_ms: Number(parsed.created_at_ms || 0)});
      }
    } catch (err) {
      props.deleteProperty(key);
    }
  });
  if (entries.length <= EJS_GH_CONFIG_V1.MAX_REPLAY_ENTRIES) return;
  entries.sort(function(a, b) { return a.created_at_ms - b.created_at_ms; });
  entries.slice(0, entries.length - EJS_GH_CONFIG_V1.MAX_REPLAY_ENTRIES).forEach(function(item) {
    props.deleteProperty(item.key);
  });
}

function ejsGhCheckReplayV1_(request, nowMs) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    ejsGhCleanupReplayLedgerV1_(props, nowMs);
    const requestKey = 'EJS_GH_LEDGER_REQ_' + ejsGhSha256HexV1_(request.request_id);
    const nonceKey = 'EJS_GH_LEDGER_NONCE_' + ejsGhSha256HexV1_(request.nonce);
    const marker = {
      request_id: request.request_id,
      nonce: request.nonce,
      payload_hash: request.payload_hash,
      signature: request.signature,
      created_at_ms: nowMs,
      expires_at_ms: Date.parse(request.expires_at)
    };

    const requestExistingRaw = props.getProperty(requestKey);
    if (requestExistingRaw) {
      const existing = JSON.parse(requestExistingRaw);
      if (existing.payload_hash !== marker.payload_hash) {
        throw new Error('GD004_REQUEST_ID_PAYLOAD_COLLISION');
      }
      if (existing.signature !== marker.signature) {
        throw new Error('GD004_REQUEST_ID_SIGNATURE_COLLISION');
      }
    }
    const nonceExistingRaw = props.getProperty(nonceKey);
    if (nonceExistingRaw) {
      const existingNonce = JSON.parse(nonceExistingRaw);
      if (existingNonce.request_id !== marker.request_id || existingNonce.payload_hash !== marker.payload_hash) {
        throw new Error('GD004_NONCE_REPLAY_COLLISION');
      }
    }

    if (!requestExistingRaw) props.setProperty(requestKey, JSON.stringify(marker));
    if (!nonceExistingRaw) props.setProperty(nonceKey, JSON.stringify(marker));
    return Boolean(requestExistingRaw || nonceExistingRaw);
  } finally {
    lock.releaseLock();
  }
}

function ejsGhVerifyRequestV1_(request, nowMs) {
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    throw new Error('GD004_REQUEST_OBJECT_REQUIRED');
  }
  if (request.contract_version !== EJS_GH_CONFIG_V1.CONTRACT_VERSION) {
    throw new Error('GD004_CONTRACT_VERSION_MISMATCH');
  }
  if (request.environment !== 'TEST') throw new Error('GD004_ENVIRONMENT_NOT_TEST');
  if (!request.request_id || !request.execution_id || !request.nonce) {
    throw new Error('GD004_IDENTITY_FIELDS_REQUIRED');
  }
  if (!EJS_GH_CONFIG_V1.ALLOWED_OPERATIONS.includes(request.operation)) {
    throw new Error('GD004_OPERATION_NOT_ALLOWED');
  }
  if (!/^[0-9a-f]{40}$/.test(String(request.source_sha || ''))) {
    throw new Error('GD004_SOURCE_SHA_INVALID');
  }
  ejsGhAssertCapabilitiesV1_(request.capability_flags);
  if (!request.payload || typeof request.payload !== 'object' || Array.isArray(request.payload)) {
    throw new Error('GD004_PAYLOAD_OBJECT_REQUIRED');
  }

  const calculatedPayloadHash = ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(request.payload));
  if (!ejsGhConstantTimeEqualV1_(calculatedPayloadHash, request.payload_hash)) {
    throw new Error('GD004_PAYLOAD_HASH_MISMATCH');
  }

  const createdAtMs = Date.parse(request.created_at);
  const expiresAtMs = Date.parse(request.expires_at);
  if (!Number.isFinite(createdAtMs) || !Number.isFinite(expiresAtMs)) {
    throw new Error('GD004_TIMESTAMP_INVALID');
  }
  const ttlMs = expiresAtMs - createdAtMs;
  if (ttlMs < 1 || ttlMs > EJS_GH_CONFIG_V1.MAX_TTL_MS) {
    throw new Error('GD004_TTL_OUT_OF_RANGE');
  }
  if (createdAtMs > nowMs + EJS_GH_CONFIG_V1.MAX_CLOCK_SKEW_MS) {
    throw new Error('GD004_CREATED_AT_IN_FUTURE');
  }
  if (nowMs > expiresAtMs) throw new Error('GD004_REQUEST_EXPIRED');

  const secret = PropertiesService.getScriptProperties().getProperty(EJS_GH_CONFIG_V1.HMAC_SECRET_PROPERTY) || '';
  if (!secret) throw new Error('GD004_HMAC_SECRET_NOT_CONFIGURED');
  const expectedSignature = ejsGhHmacHexV1_(
    ejsGhCanonicalJsonV1_(ejsGhUnsignedRequestV1_(request)),
    secret
  );
  if (!ejsGhConstantTimeEqualV1_(expectedSignature, request.signature)) {
    throw new Error('GD004_SIGNATURE_INVALID');
  }

  return {
    exact_replay: ejsGhCheckReplayV1_(request, nowMs),
    test_environment: ejsGhAssertTestEnvironmentV1_()
  };
}

function ejsGhClaimExecutionV1_(request) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    const key = 'EJS_GH_EXEC_CLAIM_' + ejsGhSha256HexV1_(request.execution_id);
    const marker = {
      execution_id: request.execution_id,
      source_sha: request.source_sha,
      payload_hash: request.payload_hash,
      status: 'claimed'
    };
    const existingRaw = props.getProperty(key);
    if (!existingRaw) {
      props.setProperty(key, JSON.stringify(marker));
      return {claim_state: 'claimed', execution_id: request.execution_id};
    }
    const existing = JSON.parse(existingRaw);
    if (existing.source_sha !== marker.source_sha || existing.payload_hash !== marker.payload_hash) {
      throw new Error('GD004_EXECUTION_CLAIM_COLLISION');
    }
    return {claim_state: 'replay', execution_id: request.execution_id};
  } finally {
    lock.releaseLock();
  }
}

function ejsGhReconcileResultV1_(request) {
  const result = request.payload && request.payload.result;
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    throw new Error('GD004_RESULT_OBJECT_REQUIRED');
  }
  ['mutation_count', 'upload_count', 'submit_count'].forEach(function(field) {
    if (Number(result[field] || 0) !== 0) throw new Error('GD004_FORBIDDEN_SIDE_EFFECT_REPORTED');
  });
  if (String(result.source_sha || '') !== request.source_sha) {
    throw new Error('GD004_RESULT_SOURCE_SHA_MISMATCH');
  }

  const resultUnsigned = Object.assign({}, result);
  delete resultUnsigned.result_hash;
  const expectedResultHash = ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(resultUnsigned));
  if (!ejsGhConstantTimeEqualV1_(expectedResultHash, result.result_hash || '')) {
    throw new Error('GD004_RESULT_HASH_MISMATCH');
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    const claimKey = 'EJS_GH_EXEC_CLAIM_' + ejsGhSha256HexV1_(request.execution_id);
    if (!props.getProperty(claimKey)) throw new Error('GD004_EXECUTION_NOT_CLAIMED');
    const resultKey = 'EJS_GH_EXEC_RESULT_' + ejsGhSha256HexV1_(request.execution_id);
    const existing = props.getProperty(resultKey);
    if (!existing) {
      props.setProperty(resultKey, result.result_hash);
      return {reconcile_state: 'recorded', result_hash: result.result_hash};
    }
    if (existing !== result.result_hash) throw new Error('GD004_RESULT_COLLISION');
    return {reconcile_state: 'replay', result_hash: result.result_hash};
  } finally {
    lock.releaseLock();
  }
}

function ejsGhHandleOperationV1_(request, verification) {
  if (request.operation === 'health') {
    return {
      status: 'ok',
      contract_version: EJS_GH_CONFIG_V1.CONTRACT_VERSION,
      environment: 'TEST',
      provider_contract: ['github_hosted', 'self_hosted', 'cloud_run_fallback'],
      capabilities: {
        browser_read: true,
        form_value_write: false,
        approved_file_upload: false,
        final_submit: false
      },
      exact_replay: verification.exact_replay
    };
  }
  if (request.operation === 'claim') return ejsGhClaimExecutionV1_(request);
  if (request.operation === 'reconcile_result') return ejsGhReconcileResultV1_(request);
  if (request.operation === 'get_execution_payload') {
    throw new Error('GD004_EXECUTION_PAYLOAD_NOT_IMPLEMENTED_WAVE1');
  }
  if (request.operation === 'get_approved_asset') {
    throw new Error('GD004_APPROVED_ASSET_NOT_IMPLEMENTED_WAVE1');
  }
  throw new Error('GD004_OPERATION_NOT_ALLOWED');
}

function ejsGhSignResponseV1_(request, responsePayload) {
  const secret = PropertiesService.getScriptProperties().getProperty(EJS_GH_CONFIG_V1.HMAC_SECRET_PROPERTY) || '';
  if (!secret) throw new Error('GD004_HMAC_SECRET_NOT_CONFIGURED');
  const responseHash = ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(responsePayload));
  const unsigned = {
    contract_version: EJS_GH_CONFIG_V1.CONTRACT_VERSION,
    request_id: request.request_id,
    execution_id: request.execution_id,
    operation: request.operation,
    response: responsePayload,
    response_hash: responseHash
  };
  return Object.assign({}, unsigned, {
    response_signature: ejsGhHmacHexV1_(ejsGhCanonicalJsonV1_(unsigned), secret)
  });
}

function ejsGhJsonV1_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  let request = null;
  try {
    request = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    const verification = ejsGhVerifyRequestV1_(request, Date.now());
    const responsePayload = ejsGhHandleOperationV1_(request, verification);
    return ejsGhJsonV1_(ejsGhSignResponseV1_(request, {ok: true, data: responsePayload}));
  } catch (err) {
    const safeCode = String((err && err.message) || 'GD004_UNKNOWN_ERROR').replace(/[^A-Z0-9_:-]/g, '_').slice(0, 120);
    if (request && request.request_id && request.execution_id && request.operation) {
      try {
        return ejsGhJsonV1_(ejsGhSignResponseV1_(request, {ok: false, error_code: safeCode}));
      } catch (signErr) {
        // Fall through to unsigned fail-closed response when the HMAC secret is not configured.
      }
    }
    return ejsGhJsonV1_({ok: false, error_code: safeCode});
  }
}
