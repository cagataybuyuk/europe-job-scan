# Europe Job Scan — Apps Script TEST Control Plane v1

GD-002 source staging. This package is TEST-only and hard-binds the target spreadsheet to `TEST_EU_Job_Tracker` while carrying the PROD spreadsheet ID as a deny reference.

## Script Properties after Cloud Run deployment

- `EJS_GCP_PROJECT_ID`
- `EJS_CLOUD_RUN_URL`
- `EJS_INVOKER_SA_EMAIL`

No password, private key, cookie, access token or ID token is stored in Script Properties.

## Planned runtime tests

1. `runEjsEnvironmentSchemaPreflightV1()` — TEST-001A-equivalent runtime assertion.
2. `runEjsIdempotencyLockSmokeV1()` — TEST-001B.
3. `runEjsCloudRunHealthEchoV1()` — TEST-001C after Cloud Run provisioning.

The Apps Script manifest explicitly requests `spreadsheets`, `script.external_request`, and `cloud-platform` scopes so `ScriptApp.getOAuthToken()` can call IAM Credentials `generateIdToken` without a downloaded service-account key.
