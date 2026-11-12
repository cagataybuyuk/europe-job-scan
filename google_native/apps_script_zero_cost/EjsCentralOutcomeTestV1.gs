/**
 * Owner-callable synthetic TEST outcome transport. No doPost operation exposes it.
 *
 * ScriptLock serializes only cooperative writers in THIS Apps Script project.
 * It is not global Sheets CAS, excludes no manual/other-project edits, and does
 * not roll back partial cell writes. Never advertise this as a live transport.
 */
const EJS_CENTRAL_TEST_V1 = Object.freeze({
  REVIEW_PROPERTY: 'EJS_CENTRAL_OUTCOME_TEST_REVIEW_V1',
  REVIEW_PIN_PROPERTY: 'EJS_CENTRAL_OUTCOME_TEST_REVIEW_SHA256_V1',
  MAX_ROWS: 10000,
  HEADERS: Object.freeze([
    'Date Added', 'Company', 'Role', 'Country', 'City', 'Source URL',
    'Role Fit Score', 'Sponsorship', 'Role Priority', 'Status', 'Applied Date',
    'CV Persona', 'Recruiter', 'Recruiter Contacted', 'Salary Expectation',
    'Next Action', 'Next Action Date', 'Notes', 'Application Source',
    'Recruiter LinkedIn', 'Outreach Date', 'Follow-up Due', 'Follow-up Status',
    'Last Update'
  ])
});

function ejsCentralOutcomeTestRequireV1_(condition) {
  if (!condition) throw new Error('CENTRAL_TEST_SYNC_REJECTED');
}

function ejsCentralOutcomeTestExactKeysV1_(value, keys) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value) &&
    ejsGhCanonicalJsonV1_(Object.keys(value).sort()) === ejsGhCanonicalJsonV1_(keys.slice().sort()));
}

function ejsCentralOutcomeTestUtcMillisV1_(value) {
  ejsCentralOutcomeTestRequireV1_(typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value));
  const parsed = Date.parse(value);
  ejsCentralOutcomeTestRequireV1_(Number.isFinite(parsed) &&
    new Date(parsed).toISOString().slice(0, 19) === value.slice(0, 19));
  return parsed;
}

function ejsCentralOutcomeTestCapabilitiesV1_() {
  return {environment: 'TEST', synthetic_only: true,
    lock_scope: 'same_apps_script_project_cooperative_writers',
    atomic_row_compare_and_set: false, globally_atomic: false,
    rollback_supported: false, live_transport_implemented: false};
}

function ejsCentralOutcomeTestWithLockV1_(action) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) throw new Error('CENTRAL_TEST_SYNC_LOCKED');
  try {
    return action();
  } catch (err) {
    // No Sheet values, candidate data, property bytes, paths or credentials.
    throw new Error('CENTRAL_TEST_SYNC_REJECTED');
  } finally {
    lock.releaseLock();
  }
}

function ejsCentralOutcomeTestContextV1_() {
  ejsGhAssertTestEnvironmentV1_();
  const properties = PropertiesService.getScriptProperties();
  const raw = properties.getProperty(EJS_CENTRAL_TEST_V1.REVIEW_PROPERTY);
  const pin = properties.getProperty(EJS_CENTRAL_TEST_V1.REVIEW_PIN_PROPERTY);
  ejsCentralOutcomeTestRequireV1_(typeof raw === 'string' && raw.length <= 16384 &&
    typeof pin === 'string' && /^[0-9a-f]{64}$/.test(pin));
  const review = JSON.parse(raw);
  ejsCentralOutcomeTestRequireV1_(ejsCentralOutcomeTestExactKeysV1_(review,
    ['schema_version', 'enabled', 'synthetic', 'source_sha', 'binding', 'binding_sha256', 'confirmation']) &&
    ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(review)) === pin &&
    review.schema_version === 'EJS-CENTRAL-OUTCOME-TEST-1' && review.enabled === true &&
    review.synthetic === true && review.source_sha === ejsGhDeployedShaV1_());
  const b = review.binding;
  ejsCentralOutcomeTestRequireV1_(ejsCentralOutcomeTestExactKeysV1_(b, [
    'spreadsheet_id', 'sheet_id', 'row_number', 'headers', 'company', 'role', 'country',
    'tracked_url', 'opportunity_key', 'posting_url', 'requisition_id', 'candidate_profile_version',
    'submit_key', 'execution_id', 'evidence_ref', 'source_sha', 'reviewed_at', 'sheet_name',
    'time_zone', 'confirmation_kind']) &&
    ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_(b)) === review.binding_sha256 &&
    b.spreadsheet_id === EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID &&
    b.spreadsheet_id !== EJS_GH_CONFIG_V1.PROD_SPREADSHEET_ID &&
    b.source_sha === review.source_sha && b.sheet_name === 'Applications' &&
    b.time_zone === 'Europe/Istanbul' && b.confirmation_kind === 'ATS_ACKNOWLEDGEMENT' &&
    Number.isSafeInteger(b.sheet_id) && b.sheet_id >= 0 &&
    Number.isSafeInteger(b.row_number) && b.row_number >= 2 && b.row_number <= EJS_CENTRAL_TEST_V1.MAX_ROWS &&
    ejsGhCanonicalJsonV1_(b.headers) === ejsGhCanonicalJsonV1_(EJS_CENTRAL_TEST_V1.HEADERS));
  // Synthetic-only target: even the TEST copy's real candidate rows are excluded.
  ejsCentralOutcomeTestRequireV1_(b.company === 'EJS Synthetic Fixture' &&
    b.role === 'Central Outcome Synthetic Analyst' && b.country === 'TEST' &&
    b.tracked_url === 'https://fixture.invalid/central-outcome' &&
    b.posting_url === 'https://jobs.smartrecruiters.com/Fixture/123456789012345--synthetic' &&
    b.opportunity_key === 'ejs-synthetic-fixture:central-outcome-synthetic-analyst:test' &&
    b.requisition_id === 'SYNTHETIC-REF' && b.candidate_profile_version === 'synthetic:v1' &&
    b.submit_key === 'submit:ejs-synthetic-fixture-central-outcome-synthetic-analyst-test:synthetic-ref:synthetic-v1' &&
    typeof b.execution_id === 'string' && /^exec:synthetic:[A-Za-z0-9._-]{1,80}$/.test(b.execution_id) &&
    typeof b.evidence_ref === 'string' && /^evidence:sha256:[0-9a-f]{64}$/.test(b.evidence_ref));
  const now = Date.now();
  const reviewedAt = ejsCentralOutcomeTestUtcMillisV1_(b.reviewed_at);
  ejsCentralOutcomeTestRequireV1_(now >= reviewedAt && now - reviewedAt <= 300000);
  const c = review.confirmation;
  ejsCentralOutcomeTestRequireV1_(ejsCentralOutcomeTestExactKeysV1_(c,
    ['status', 'synthetic', 'confirmed_at', 'binding_sha256', 'submit_key', 'execution_id', 'requisition_id', 'evidence_ref']) &&
    c.status === 'Confirmed' && c.synthetic === true && c.binding_sha256 === review.binding_sha256 &&
    c.submit_key === b.submit_key && c.execution_id === b.execution_id && c.requisition_id === b.requisition_id &&
    c.evidence_ref === b.evidence_ref && ejsCentralOutcomeTestUtcMillisV1_(c.confirmed_at) <= now);
  const ss = SpreadsheetApp.openById(EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID);
  ejsCentralOutcomeTestRequireV1_(ss.getSpreadsheetTimeZone() === 'Europe/Istanbul');
  const sheet = ss.getSheetByName('Applications');
  ejsCentralOutcomeTestRequireV1_(sheet && sheet.getSheetId() === b.sheet_id &&
    sheet.getLastRow() >= b.row_number && sheet.getLastRow() <= EJS_CENTRAL_TEST_V1.MAX_ROWS && sheet.getMaxColumns() >= 24);
  return {binding: b, binding_sha256: review.binding_sha256, confirmation: c, sheet: sheet,
    applied_date: Utilities.formatDate(new Date(c.confirmed_at), 'Europe/Istanbul', 'yyyy-MM-dd'),
    update_date: Utilities.formatDate(new Date(now), 'Europe/Istanbul', 'yyyy-MM-dd')};
}

function ejsCentralOutcomeTestValuesV1_(values) {
  return values.map(function(value) {
    if (Object.prototype.toString.call(value) === '[object Date]') {
      ejsCentralOutcomeTestRequireV1_(Number.isFinite(value.getTime()));
      return Utilities.formatDate(value, 'Europe/Istanbul', 'yyyy-MM-dd');
    }
    ejsCentralOutcomeTestRequireV1_(value === null || ['string', 'number', 'boolean'].includes(typeof value));
    const text = value === null ? '' : String(value);
    ejsCentralOutcomeTestRequireV1_(text.length <= 10000);
    return text;
  });
}

function ejsCentralOutcomeTestReadContextV1_(context) {
  const b = context.binding;
  const sheet = context.sheet;
  const headers = ejsCentralOutcomeTestValuesV1_(sheet.getRange(1, 1, 1, 24).getValues()[0]);
  ejsCentralOutcomeTestRequireV1_(ejsGhCanonicalJsonV1_(headers) === ejsGhCanonicalJsonV1_(EJS_CENTRAL_TEST_V1.HEADERS));
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 24).getValues();
  const matches = [];
  rows.forEach(function(values, index) {
    const cells = ejsCentralOutcomeTestValuesV1_(values);
    if (cells[1] === b.company && cells[2] === b.role && cells[3] === b.country) {
      matches.push({row_number: index + 2, cells: cells});
    }
  });
  ejsCentralOutcomeTestRequireV1_(matches.length === 1 && matches[0].row_number === b.row_number &&
    matches[0].cells[5] === b.tracked_url);
  const validation = sheet.getRange(b.row_number, 10).getDataValidation();
  ejsCentralOutcomeTestRequireV1_(validation &&
    validation.getCriteriaType() === SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST &&
    validation.getAllowInvalid() === false);
  const allowed = validation.getCriteriaValues()[0];
  ejsCentralOutcomeTestRequireV1_(Array.isArray(allowed) && allowed.length > 0 && allowed.length <= 100 &&
    allowed.every(function(value) { return typeof value === 'string' && value.length <= 200; }) && allowed.includes('Applied'));
  [10, 11, 24].forEach(function(column) {
    ejsCentralOutcomeTestRequireV1_(sheet.getRange(b.row_number, column).getFormulas()[0][0] === '');
  });
  return {spreadsheet_id: b.spreadsheet_id, sheet_id: b.sheet_id, sheet_name: 'Applications',
    headers: headers, identity_matches: matches, status_allowed_values: allowed.slice()};
}

function ejsCentralOutcomeTestFingerprintV1_(read) {
  return ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_([read.spreadsheet_id, read.sheet_id, read.sheet_name,
    read.headers, read.status_allowed_values, read.identity_matches.map(function(row) { return [row.row_number, row.cells]; })]));
}

function ejsCentralOutcomeTestReadV1_(binding) {
  return ejsCentralOutcomeTestWithLockV1_(function() {
    const context = ejsCentralOutcomeTestContextV1_();
    ejsCentralOutcomeTestRequireV1_(ejsGhCanonicalJsonV1_(binding) === ejsGhCanonicalJsonV1_(context.binding));
    return ejsCentralOutcomeTestReadContextV1_(context);
  });
}

function ejsCentralOutcomeTestCasV1_(request) {
  return ejsCentralOutcomeTestWithLockV1_(function() {
    const context = ejsCentralOutcomeTestContextV1_();
    const b = context.binding;
    const operation = ejsGhSha256HexV1_(ejsGhCanonicalJsonV1_([
      'EJS-CENTRAL-APPLICATION-1', context.binding_sha256, b.evidence_ref, context.applied_date]));
    const patches = [{column: 'J', row: b.row_number, value: 'Applied'},
      {column: 'K', row: b.row_number, value: context.applied_date},
      {column: 'X', row: b.row_number, value: context.update_date}];
    ejsCentralOutcomeTestRequireV1_(ejsCentralOutcomeTestExactKeysV1_(request, [
      'spreadsheet_id', 'sheet_id', 'sheet_name', 'row_number', 'identity_key',
      'operation_key', 'expected_fingerprint', 'patches']) &&
      request.spreadsheet_id === b.spreadsheet_id && request.sheet_id === b.sheet_id &&
      request.sheet_name === 'Applications' && request.row_number === b.row_number &&
      ejsGhCanonicalJsonV1_(request.identity_key) === ejsGhCanonicalJsonV1_([b.company, b.role, b.country]) &&
      request.operation_key === operation && /^[0-9a-f]{64}$/.test(request.expected_fingerprint || '') &&
      ejsGhCanonicalJsonV1_(request.patches) === ejsGhCanonicalJsonV1_(patches));
    const before = ejsCentralOutcomeTestReadContextV1_(context);
    const current = before.identity_matches[0].cells;
    if (request.expected_fingerprint !== ejsCentralOutcomeTestFingerprintV1_(before)) {
      return {operation_key: operation, comparison_succeeded: false, wrote: false};
    }
    ejsCentralOutcomeTestRequireV1_(current[9] === 'To Apply' && current[10] === '');
    // Preserve formatting, notes, validations and all 21 unowned values/formulas.
    // J is deliberately last: a partial failure cannot claim Applied prematurely.
    const appliedDate = new Date(context.applied_date + 'T12:00:00Z');
    const updateDate = new Date(context.update_date + 'T12:00:00Z');
    context.sheet.getRange(b.row_number, 11).setValue(appliedDate);
    context.sheet.getRange(b.row_number, 24).setValue(updateDate);
    context.sheet.getRange(b.row_number, 10).setValue('Applied');
    SpreadsheetApp.flush();
    const after = ejsCentralOutcomeTestReadContextV1_(context);
    const expected = current.slice();
    expected[9] = 'Applied'; expected[10] = context.applied_date; expected[23] = context.update_date;
    ejsCentralOutcomeTestRequireV1_(ejsGhCanonicalJsonV1_(after.identity_matches[0].cells) === ejsGhCanonicalJsonV1_(expected) &&
      ejsGhCanonicalJsonV1_(after.status_allowed_values) === ejsGhCanonicalJsonV1_(before.status_allowed_values));
    return {operation_key: operation, comparison_succeeded: true, wrote: true};
  });
}
