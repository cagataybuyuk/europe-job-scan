# TEST Apps Script deployment

GitHub Actions materializes `google_native/apps_script/.clasp.json` from `EJS_APPS_SCRIPT_ID_TEST` and `.clasprc.json` from encrypted secret `EJS_CLASPRC_JSON_TEST`, then runs `clasp push --force` from the Apps Script source directory.

The workflow must reject an empty Script ID and must not contain a PROD Script ID. Direct editor paste is not part of the canonical delivery flow after TEST-002C passes.
