// This table stages immutable TEST requests; it never changes Applications or Applied.
const EJS_GH_QUEUE_HEADERS_V1 = Object.freeze([
  'Execution ID', 'Source SHA', 'State', 'Payload JSON', 'Payload Hash',
  'Result JSON', 'Result Hash'
]);

function ejsGhQueueSheetV1_() {
  ejsGhAssertTestEnvironmentV1_();
  const ss = SpreadsheetApp.openById(EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID);
  const sheet = ss.getSheetByName(EJS_GH_CONFIG_V1.QUEUE_SHEET);
  if (!sheet) throw new Error('GD004_QUEUE_NOT_INITIALIZED');
  const headers = sheet.getRange(1, 1, 1, EJS_GH_QUEUE_HEADERS_V1.length).getValues()[0];
  if (ejsGhCanonicalJsonV1_(headers) !== ejsGhCanonicalJsonV1_(EJS_GH_QUEUE_HEADERS_V1)) {
    throw new Error('GD004_QUEUE_HEADER_MISMATCH');
  }
  if (sheet.getLastRow() > EJS_GH_CONFIG_V1.MAX_QUEUE_ROWS + 1) {
    throw new Error('GD004_QUEUE_CAPACITY_EXCEEDED');
  }
  return sheet;
}

function ejsGhAssertPayloadV1_(payload, executionId, sourceSha) {
  const allowed = ['environment', 'execution_id', 'source_sha', 'operation',
    'opportunity_key', 'requisition_id', 'adapter_key', 'fixture_key',
    'application_url', 'approved_assets'];
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
      Object.keys(payload).some(function(key) { return !allowed.includes(key); })) {
    throw new Error('GD004_EXECUTION_PAYLOAD_SCHEMA');
  }
  if (payload.environment !== 'TEST' || payload.operation !== 'browser_read' ||
      payload.execution_id !== executionId || payload.source_sha !== sourceSha) {
    throw new Error('GD004_EXECUTION_PAYLOAD_IDENTITY');
  }
  ['opportunity_key', 'requisition_id', 'adapter_key'].forEach(function(key) {
    if (typeof payload[key] !== 'string' || !payload[key] || payload[key].length > 200) {
      throw new Error('GD004_EXECUTION_PAYLOAD_FIELD');
    }
  });
  if (payload.fixture_key) {
    if (payload.fixture_key !== 'smartrecruiters_smoke' || payload.application_url) {
      throw new Error('GD004_FIXTURE_NOT_ALLOWED');
    }
  } else if (typeof payload.application_url !== 'string' ||
             !/^https:\/\/[^\s@]+$/.test(payload.application_url)) {
    throw new Error('GD004_HTTPS_TARGET_REQUIRED');
  }
  if (!Array.isArray(payload.approved_assets) || payload.approved_assets.length > 2) {
    throw new Error('GD004_ASSET_LIST_INVALID');
  }
  const ids = [];
  payload.approved_assets.forEach(function(asset) {
    const keys = ['artifact_id', 'drive_file_id', 'filename', 'mime_type',
      'size_bytes', 'sha256', 'synthetic'];
    if (!asset || ejsGhCanonicalJsonV1_(Object.keys(asset).sort()) !==
        ejsGhCanonicalJsonV1_(keys.sort()) || asset.synthetic !== true ||
        asset.mime_type !== 'application/pdf' ||
        !Number.isSafeInteger(asset.size_bytes) || asset.size_bytes < 5 ||
        asset.size_bytes > EJS_GH_CONFIG_V1.MAX_ASSET_BYTES ||
        !/^[0-9a-f]{64}$/.test(asset.sha256 || '') ||
        !/^[A-Za-z0-9_-]{10,}$/.test(asset.drive_file_id || '') ||
        typeof asset.artifact_id !== 'string' || !asset.artifact_id ||
        typeof asset.filename !== 'string' || !/^[^/\\\r\n]{1,150}\.pdf$/.test(asset.filename) ||
        ids.includes(asset.artifact_id)) {
      throw new Error('GD004_APPROVED_ASSET_SCHEMA');
    }
    ids.push(asset.artifact_id);
  });
}

function ejsGhQueueRecordV1_(request) {
  const rowNumber = request.payload.queue_row;
  if (!Number.isSafeInteger(rowNumber) || rowNumber < 2 ||
      rowNumber > EJS_GH_CONFIG_V1.MAX_QUEUE_ROWS + 1) {
    throw new Error('GD004_EXPLICIT_QUEUE_ROW_REQUIRED');
  }
  const sheet = ejsGhQueueSheetV1_();
  if (rowNumber > sheet.getLastRow()) throw new Error('GD004_QUEUE_ROW_MISSING');
  const values = sheet.getRange(rowNumber, 1, 1, EJS_GH_QUEUE_HEADERS_V1.length).getValues()[0];
  if (values[0] !== request.execution_id || values[1] !== request.source_sha) {
    throw new Error('GD004_QUEUE_IDENTITY_MISMATCH');
  }
  const ids = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  if (ids.filter(function(row) { return row[0] === request.execution_id; }).length !== 1) {
    throw new Error('GD004_EXECUTION_ID_NOT_UNIQUE');
  }
  let payload;
  try { payload = JSON.parse(values[3]); } catch (err) { throw new Error('GD004_QUEUE_PAYLOAD_JSON'); }
  ejsGhAssertPayloadV1_(payload, request.execution_id, request.source_sha);
  const hash = ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(payload));
  if (values[4] !== hash) throw new Error('GD004_QUEUE_PAYLOAD_DRIFT');
  if (request.operation !== 'get_execution_payload' &&
      request.payload.expected_payload_hash !== hash) {
    throw new Error('GD004_STALE_QUEUE_SNAPSHOT');
  }
  if (!['READY', 'CLAIMED', 'COMPLETED', 'REVIEW_REQUIRED'].includes(values[2])) {
    throw new Error('GD004_QUEUE_STATE_INVALID');
  }
  return {sheet: sheet, row: rowNumber, values: values, payload: payload, payload_hash: hash};
}

function ejsGhWriteQueueV1_(record, values) {
  // Refresh the exact row before writing and independently read it back afterwards.
  const range = record.sheet.getRange(record.row, 1, 1, EJS_GH_QUEUE_HEADERS_V1.length);
  if (ejsGhCanonicalJsonV1_(range.getValues()[0]) !== ejsGhCanonicalJsonV1_(record.values)) {
    throw new Error('GD004_CONCURRENT_QUEUE_CHANGE');
  }
  range.setValues([values]);
  SpreadsheetApp.flush();
  if (ejsGhCanonicalJsonV1_(range.getValues()[0]) !== ejsGhCanonicalJsonV1_(values)) {
    throw new Error('GD004_QUEUE_READBACK_MISMATCH');
  }
}

function ejsGhGetExecutionPayloadV1_(request) {
  const record = ejsGhQueueRecordV1_(request);
  return {queue_row: record.row, queue_state: record.values[2],
    execution_payload: record.payload, payload_hash: record.payload_hash,
    result_hash: record.values[6] || ''};
}

function ejsGhQueueClaimV1_(request) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const record = ejsGhQueueRecordV1_(request);
    if (record.values[2] !== 'READY') {
      return {claim_state: 'replay', queue_state: record.values[2], execution_id: request.execution_id};
    }
    if (record.values[5] || record.values[6]) throw new Error('GD004_READY_ROW_HAS_RESULT');
    const values = record.values.slice();
    values[2] = 'CLAIMED';
    ejsGhWriteQueueV1_(record, values);
    return {claim_state: 'claimed', queue_state: 'CLAIMED', execution_id: request.execution_id};
  } finally { lock.releaseLock(); }
}

function ejsGhReadApprovedAssetV1_(request) {
  const record = ejsGhQueueRecordV1_(request);
  if (record.values[2] !== 'CLAIMED') throw new Error('GD004_ASSET_REQUIRES_CLAIM');
  const asset = record.payload.approved_assets.find(function(item) {
    return item.artifact_id === request.payload.artifact_id;
  });
  if (!asset) throw new Error('GD004_ASSET_NOT_APPROVED_FOR_EXECUTION');
  const file = DriveApp.getFileById(asset.drive_file_id);
  const parents = file.getParents();
  let inTestFolder = false;
  while (parents.hasNext()) {
    if (parents.next().getId() === EJS_GH_CONFIG_V1.TEST_ASSET_FOLDER_ID) inTestFolder = true;
  }
  if (!inTestFolder || file.isTrashed()) throw new Error('GD004_ASSET_OUTSIDE_TEST_FOLDER');
  if (file.getSize() !== asset.size_bytes || file.getName() !== asset.filename ||
      file.getMimeType() !== asset.mime_type) throw new Error('GD004_ASSET_METADATA_DRIFT');
  const blob = file.getBlob();
  const bytes = blob.getBytes();
  if (blob.getContentType() !== asset.mime_type || bytes.length !== asset.size_bytes ||
      bytes.slice(0, 5).map(function(b) { return String.fromCharCode(b); }).join('') !== '%PDF-') {
    throw new Error('GD004_ASSET_BYTES_INVALID');
  }
  const hash = ejsGhBytesToHexV1_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes));
  if (hash !== asset.sha256) throw new Error('GD004_ASSET_HASH_DRIFT');
  // Recheck the immutable approval after Drive IO; neither file bytes nor names are logged.
  const after = ejsGhQueueRecordV1_(request);
  if (after.values[2] !== 'CLAIMED') throw new Error('GD004_ASSET_CLAIM_CHANGED');
  return {asset: asset, base64_data: Utilities.base64Encode(bytes)};
}

function ejsGhQueueReconcileV1_(request) {
  const result = request.payload.result;
  const fields = ['request_id', 'execution_id', 'operation', 'source_sha', 'provider',
    'started_at', 'completed_at', 'typed_status', 'form_fingerprint', 'runtime_fingerprint',
    'mutation_count', 'upload_count', 'submit_count', 'evidence_summary', 'result_hash'];
  if (!result || ejsGhCanonicalJsonV1_(Object.keys(result).sort()) !==
      ejsGhCanonicalJsonV1_(fields.sort())) throw new Error('GD004_RESULT_SCHEMA');
  ['mutation_count', 'upload_count', 'submit_count'].forEach(function(key) {
    if (result[key] !== 0) throw new Error('GD004_FORBIDDEN_SIDE_EFFECT_REPORTED');
  });
  if (result.execution_id !== request.execution_id || result.source_sha !== request.source_sha ||
      result.request_id !== 'browser:' + request.execution_id || result.operation !== 'browser_read' ||
      !['github_hosted', 'self_hosted'].includes(result.provider)) {
    throw new Error('GD004_RESULT_IDENTITY_MISMATCH');
  }
  fields.filter(function(key) { return !key.endsWith('_count'); }).forEach(function(key) {
    if (typeof result[key] !== 'string' || result[key].length > 1000) throw new Error('GD004_RESULT_FIELD');
  });
  const unsigned = Object.assign({}, result);
  delete unsigned.result_hash;
  if (ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(unsigned)) !== result.result_hash) {
    throw new Error('GD004_RESULT_HASH_MISMATCH');
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const record = ejsGhQueueRecordV1_(request);
    if (record.values[2] === 'READY') throw new Error('GD004_EXECUTION_NOT_CLAIMED');
    if (record.values[6]) {
      if (record.values[6] !== result.result_hash ||
          record.values[5] !== ejsGhCanonicalJsonV1_(result)) throw new Error('GD004_RESULT_COLLISION');
      return {reconcile_state: 'replay', queue_state: record.values[2], result_hash: result.result_hash};
    }
    if (record.values[2] !== 'CLAIMED') throw new Error('GD004_RESULT_STATE_INVALID');
    const values = record.values.slice();
    values[2] = result.typed_status === 'rendered' ? 'COMPLETED' : 'REVIEW_REQUIRED';
    values[5] = ejsGhCanonicalJsonV1_(result);
    values[6] = result.result_hash;
    ejsGhWriteQueueV1_(record, values);
    return {reconcile_state: 'recorded', queue_state: values[2], result_hash: result.result_hash};
  } finally { lock.releaseLock(); }
}

// Owner/editor-only initializer, not an operation exposed by the Web App.
// Seeds one synthetic browser job per deployed SHA. Existing data is never reset.
function ejsGhInitializeSyntheticTestV1() {
  ejsGhAssertTestEnvironmentV1_();
  const sha = ejsGhDeployedShaV1_();
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const ss = SpreadsheetApp.openById(EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID);
    let sheet = ss.getSheetByName(EJS_GH_CONFIG_V1.QUEUE_SHEET);
    if (!sheet) sheet = ss.insertSheet(EJS_GH_CONFIG_V1.QUEUE_SHEET);
    if (sheet.getLastRow() === 0) {
      sheet.getRange(1, 1, 1, EJS_GH_QUEUE_HEADERS_V1.length).setValues([EJS_GH_QUEUE_HEADERS_V1.slice()]);
      sheet.setFrozenRows(1);
    }
    sheet = ejsGhQueueSheetV1_();
    const id = 'execution:gd004:synthetic:' + sha;
    const payload = {environment: 'TEST', execution_id: id, source_sha: sha,
      operation: 'browser_read', opportunity_key: 'opportunity:gd004:synthetic',
      requisition_id: 'gd004-synthetic', adapter_key: 'ats:smartrecruiters',
      fixture_key: 'smartrecruiters_smoke', application_url: '',
      approved_assets: [EJS_GH_SYNTHETIC_ASSET_V1]};
    ejsGhAssertPayloadV1_(payload, id, sha);
    const json = ejsGhCanonicalJsonV1_(payload);
    const hash = ejsGhSha256HexV1_(json);
    for (let row = 2; row <= sheet.getLastRow(); row += 1) {
      const values = sheet.getRange(row, 1, 1, 7).getValues()[0];
      if (values[0] === id) {
        if (values[3] !== json || values[4] !== hash) throw new Error('GD004_FIXTURE_ID_COLLISION');
        console.log(JSON.stringify({queue_row: row, execution_id: id, state: values[2], replay: true}));
        return;
      }
    }
    const row = sheet.getLastRow() + 1;
    if (row > EJS_GH_CONFIG_V1.MAX_QUEUE_ROWS + 1) throw new Error('GD004_QUEUE_CAPACITY_EXCEEDED');
    sheet.getRange(row, 1, 1, 7).setValues([[id, sha, 'READY', json, hash, '', '']]);
    SpreadsheetApp.flush();
    const request = {execution_id: id, source_sha: sha,
      operation: 'get_execution_payload', payload: {queue_row: row}};
    ejsGhQueueRecordV1_(request);
    console.log(JSON.stringify({queue_row: row, execution_id: id, state: 'READY', replay: false}));
  } finally { lock.releaseLock(); }
}
