const EJS_GH_CONFIG_V1 = Object.freeze({
  CONTRACT_VERSION: 'EJS-GH-EXEC-0.1',
  ENVIRONMENT: 'TEST',
  TEST_SPREADSHEET_ID: '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM',
  PROD_SPREADSHEET_ID: '1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg',
  TEST_SPREADSHEET_TITLE: 'TEST_EU_Job_Tracker',
  HMAC_SECRET_PROPERTY: 'EJS_HMAC_SHARED_SECRET_TEST',
  QUEUE_SHEET: 'GD004 TEST Execution Queue',
  TEST_ASSET_FOLDER_ID: '1SWD9fGmmTkhpg-dezvBGzn5h3QxRAn6U',
  MAX_ASSET_BYTES: 2097152,
  MAX_QUEUE_ROWS: 200,
  MAX_REQUEST_BYTES: 32768,
  MAX_TTL_MS: 300000,
  MAX_CLOCK_SKEW_MS: 30000,
  MAX_REPLAY_ENTRIES: 200,
  ALLOWED_OPERATIONS: Object.freeze([
    'health',
    'claim',
    'get_execution_payload',
    'get_approved_asset',
    'reconcile_result'
  ])
});

function ejsGhDeployedShaV1_() {
  if (typeof EJS_GH_DEPLOYED_SOURCE_SHA_V1 !== 'string' ||
      !/^[0-9a-f]{40}$/.test(EJS_GH_DEPLOYED_SOURCE_SHA_V1)) {
    throw new Error('GD004_DEPLOYED_SOURCE_SHA_MISSING');
  }
  return EJS_GH_DEPLOYED_SOURCE_SHA_V1;
}

function ejsGhAssertTestEnvironmentV1_() {
  if (EJS_GH_CONFIG_V1.ENVIRONMENT !== 'TEST') {
    throw new Error('GD004_ENVIRONMENT_NOT_TEST');
  }
  if (EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID === EJS_GH_CONFIG_V1.PROD_SPREADSHEET_ID) {
    throw new Error('GD004_TEST_PROD_ID_COLLISION');
  }
  const ss = SpreadsheetApp.openById(EJS_GH_CONFIG_V1.TEST_SPREADSHEET_ID);
  if (ss.getId() === EJS_GH_CONFIG_V1.PROD_SPREADSHEET_ID) {
    throw new Error('GD004_PROD_DENY_GUARD');
  }
  if (ss.getName() !== EJS_GH_CONFIG_V1.TEST_SPREADSHEET_TITLE) {
    throw new Error('GD004_TEST_SPREADSHEET_TITLE_MISMATCH');
  }
  return {
    environment: EJS_GH_CONFIG_V1.ENVIRONMENT,
    spreadsheet_id: ss.getId(),
    spreadsheet_title: ss.getName()
  };
}

/**
 * One-time owner authorization/readiness probe.
 *
 * Run manually once in the TEST Apps Script editor after GitHub has pushed the canonical
 * source. This function performs no Sheet write and never returns the HMAC secret value.
 * Its purpose is to trigger the Google consent flow for the explicit spreadsheet/Drive-read
 * scopes and to confirm that the required Script Property has been configured.
 */
function ejsGhAuthorizationProbeV1() {
  const environment = ejsGhAssertTestEnvironmentV1_();
  const secretConfigured = Boolean(
    PropertiesService.getScriptProperties().getProperty(EJS_GH_CONFIG_V1.HMAC_SECRET_PROPERTY)
  );
  return {
    contract_version: EJS_GH_CONFIG_V1.CONTRACT_VERSION,
    environment: environment.environment,
    spreadsheet_title: environment.spreadsheet_title,
    hmac_secret_configured: secretConfigured,
    mutation_count: 0,
    upload_count: 0,
    submit_count: 0
  };
}
