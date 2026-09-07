# TEST Apps Script deployment

GitHub Actions materializes `google_native/apps_script/.clasp.json` from `EJS_APPS_SCRIPT_ID_TEST` and `.clasprc.json` from encrypted secret `EJS_CLASPRC_JSON_TEST`, then runs `clasp push --force` from the Apps Script source directory.

The workflow must reject an empty Script ID and must not contain a PROD Script ID. Direct editor paste is not part of the canonical delivery flow after TEST-002C passes.

## One-time user-owned bootstrap

The OAuth authorization itself is intentionally user-owned. Never paste the generated auth JSON into ChatGPT, Drive documentation, an issue, a commit, or an Actions log.

1. Create a **standalone TEST Apps Script project** under the Google account that will run the TEST control plane.
2. In Apps Script Project Settings, link that script to the same dedicated TEST Google Cloud project used by the Cloud Run worker. Keep the production project separate.
3. Ensure the Apps Script API is enabled for the authorizing Google account.
4. On a trusted local/Cloud Shell terminal with Node available, run `npx clasp login` and complete the Google OAuth consent flow.
5. Record the TEST Apps Script Script ID as the non-secret GitHub environment/repository variable `EJS_APPS_SCRIPT_ID_TEST`.
6. Store the complete generated clasp auth JSON only as the encrypted GitHub environment/repository secret `EJS_CLASPRC_JSON_TEST`.
7. Do not commit `.clasp.json` containing an environment-specific Script ID or `.clasprc.json`; the workflow materializes both ephemerally and removes auth material after use.

## TEST-002C acceptance

Dispatch `Deploy TEST Apps Script` from `main` with the exact approved commit SHA. PASS requires:

- workflow checks out that exact SHA;
- TEST Script ID variable is present;
- OAuth material is obtained only from the encrypted secret;
- `clasp push --force` succeeds against the TEST project;
- repository/history/log secret scan remains clean;
- TEST source retains the hard-coded TEST tracker binding and PROD tracker deny reference;
- no production Apps Script project is touched.

After TEST-002C, direct editor paste is treated as drift rather than a normal deployment method.
