# GD-004 Zero-Cost Apps Script Bootstrap

One-time owner authorization for the TEST GitHub browser path. No Google Cloud
billing, Cloud Run, paid proxy, or manual source-code paste is required.

## Ready engineering / pending live acceptance

Main contains the signed gateway, TEST queue, synthetic PDF resolver, Python runner
and deployment/integration workflows. CI executes the actual `.gs` code in V8 with
Google service doubles, crosses the Python/JavaScript HMAC boundary and runs a real
synthetic Chromium roundtrip. This does not replace TEST-004B/C on the owner's Web App.

The new table is `GD004 TEST Execution Queue`, only in TEST spreadsheet
`17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM`. The initializer stages one synthetic
job per deployed SHA. It never resets existing rows or changes Applications, Form
Fill Queue, personal facts, application history or PROD. Real opportunities require
explicit staging and their own release gates; this is not automatic job selection.

## Canonical settings

| Setting | Location | Purpose |
| --- | --- | --- |
| `EJS_CLASPRC_JSON_TEST` | GitHub encrypted secret | One-time owner clasp OAuth credential |
| `EJS_APPS_SCRIPT_ZERO_COST_ID_TEST` | GitHub variable | Standalone TEST Script ID |
| `EJS_HMAC_SHARED_SECRET_TEST` | GitHub secret AND Apps Script Script Property | Same name and same locally generated value |
| `EJS_APPS_SCRIPT_ZERO_COST_URL_TEST` | GitHub variable | Published Web App `/exec` URL |

The deployment workflow generates `EjsDeploymentV1.gs` from the exact approved main
SHA. Other SHAs fail closed. `clasp push` updates source, not a published Web App
version: after a later source deployment, update that Web App version and stage the
new SHA's synthetic job before integration.

## One-time setup (Windows PowerShell)

Prerequisites: Git, Node.js and authenticated GitHub CLI (`gh auth status`). Use the
Google account that can edit the TEST spreadsheet and read the synthetic test PDF.

1. Open <https://script.google.com/create>. Name the standalone project **Europe Job
   Scan Zero Cost Control Plane - TEST**. Copy the Script ID from Project Settings.
   Enable Apps Script API in user settings if disabled.
2. In an up-to-date local clone of `cagataybuyuk/europe-job-scan` on `main`, run:

   ```powershell
   .\scripts\bootstrap_zero_cost.ps1 -ScriptId "<SCRIPT_ID>"
   ```

   It checks main, runs `npm install` and `clasp login`, stores OAuth directly in
   GitHub, generates HMAC, copies it to your local clipboard and dispatches TEST
   source deployment. Complete Google's OAuth screen yourself. No secret is printed.
3. In Apps Script **Project Settings > Script properties**, create
   `EJS_HMAC_SHARED_SECRET_TEST`, paste the clipboard value and save. Clear the
   clipboard. Re-running the helper rotates GitHub's secret: update this copy too.
4. After **Deploy TEST Apps Script Zero Cost** succeeds, refresh the editor and run
   **`ejsGhInitializeSyntheticTestV1`** once. Authorize Sheets and read-only Drive
   access. Record its printed `queue_row` and `execution_id`.
5. Choose **Deploy > New deployment > Web app**, execute as yourself, with access
   for anyone (including anonymous). Every accepted request still passes HMAC,
   expiry, replay, deployed-SHA, TEST-target and capability checks. Store its URL:

   ```powershell
   gh variable set EJS_APPS_SCRIPT_ZERO_COST_URL_TEST --body "<WEB_APP_EXEC_URL>"
   ```

Only Script ID, `/exec` URL, queue row and execution ID may be shared with the project
workflow. Never share `.clasprc.json`, OAuth tokens or the HMAC value in chat/Drive.

## Real TEST-004B/C

After owner setup, engineering dispatches:

```powershell
gh workflow run gd004-test-control-plane.yml -f expected_sha=<APPROVED_MAIN_SHA> -f queue_row=<ROW> -f execution_id=<EXECUTION_ID>
```

The workflow checks signed health, replay, invalid signature, expiry, PROD denial
and request collision. It then reads the exact row, claims it, verifies its approved
synthetic PDF in memory, inspects the packaged Chromium fixture, reconciles the result
and independently reads the durable queue state back. No employer receives a PDF.
No candidate documents, filenames, tokens or request payloads are printed or uploaded
as Actions artifacts; this phase uses only the synthetic regression PDF.

Acceptance: queue state `COMPLETED`, matching result hashes and zero employer
mutation/upload/submit counters. Repeating a completed execution performs no browser
action or second write. A blocked browser yields `REVIEW_REQUIRED`, never Applied.
Do not mark live TEST-004B/C passed from local mocks or CI results alone.

## Recovery and boundaries

- An interrupted `CLAIMED` row is quarantined. Inspect evidence and reconcile the
  original result, or explicitly stage a new execution after review. Retries never
  clear a claim or acquire it again automatically.
- Changed payload/PDF, duplicate execution IDs, wrong SHA/folder/TEST identity and
  invalid result hashes fail closed.
- Limits: 200 staged rows, two approved synthetic PDFs per row, 2 MiB per PDF,
  5-minute request TTL, bounded replay storage and one queue workflow at a time.
- Scopes are Sheets plus `drive.readonly`; no Cloud Platform scope.
- The Web App exposes no initializer or administrative operation.
- Stop by disabling the TEST workflow and removing the Web App deployment. Rotate
  both secret copies together when needed.
- Live field writes, CV uploads, final submit, Applied transitions and production
  queue selection remain separate release work.

Google reference: <https://developers.google.com/apps-script/guides/web>.
