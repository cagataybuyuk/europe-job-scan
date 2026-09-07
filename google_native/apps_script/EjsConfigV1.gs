const EJS_CONFIG_V1 = Object.freeze({
  CONTRACT_VERSION: 'EJS-GN-1.0',
  ENVIRONMENT: 'TEST',
  TEST_SPREADSHEET_ID: '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM',
  PROD_SPREADSHEET_ID: '1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg',
  TEST_SPREADSHEET_TITLE: 'TEST_EU_Job_Tracker',
  TIME_ZONE: 'Europe/Istanbul',
  REQUIRED_SHEETS: [
    'Applications', 'Candidate Profile', 'Form Fill Queue', 'Application Execution Log',
    'Submission Artifact Registry', 'Auto Submit Policy Registry', 'Pre-Submit Validation Audit',
    'Browser Runtime Audit', 'File Upload Runtime Audit', 'SmartRecruiters Execution Audit'
  ],
  REQUIRED_HEADERS: Object.freeze({
    'Applications': ['Company', 'Role', 'Status', 'Source URL', 'URL Status'],
    'Form Fill Queue': ['Company', 'Role', 'Application URL', 'Application Status', 'Final Submit'],
    'Application Execution Log': ['Execution Event Key', 'Execution ID', 'Event Type', 'Form Fingerprint', 'Result'],
    'Submission Artifact Registry': ['Artifact Key', 'Execution ID', 'Artifact Type', 'Content Hash', 'Immutable'],
    'Pre-Submit Validation Audit': ['Validation Key', 'Execution ID', 'Form Fingerprint', 'Decision'],
    'Browser Runtime Audit': ['Run ID', 'Application URL', 'Runtime State', 'Form Fingerprint', 'Read-only Invariant']
  })
});

function getEjsRuntimeConfigV1_() {
  const props = PropertiesService.getScriptProperties();
  return Object.freeze({
    environment: EJS_CONFIG_V1.ENVIRONMENT,
    targetSpreadsheetId: EJS_CONFIG_V1.TEST_SPREADSHEET_ID,
    cloudProjectId: props.getProperty('EJS_GCP_PROJECT_ID') || '',
    cloudRunUrl: props.getProperty('EJS_CLOUD_RUN_URL') || '',
    invokerServiceAccount: props.getProperty('EJS_INVOKER_SA_EMAIL') || ''
  });
}
