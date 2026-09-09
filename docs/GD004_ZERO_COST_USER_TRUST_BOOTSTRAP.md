# GD-004 zero-cost user trust bootstrap

This runbook is the one-time account-owner step required before TEST-004B/C can run against a real Apps Script Web App. It does **not** require a Google Cloud billing project.

## What is already automated

After GD-004 Wave-2 merges, GitHub owns the canonical source and workflows for:

- Apps Script source push with `clasp`;
- immutable Apps Script version + Web App deployment;
- exact source-SHA deployment discovery;
- HMAC-signed TEST health/replay checks;
- invalid-signature and expired-request negative checks;
- execution claim replay/idempotency;
- result reconcile replay/idempotency;
- zero mutation/upload/submit guards.

The Web App manifest is TEST-only and uses `ANYONE_ANONYMOUS` + `USER_DEPLOYING`; the endpoint itself is not trusted without the EJS-GH-EXEC-0.1 HMAC, TTL, nonce/replay and capability checks.

## User-owned actions that cannot be performed by ChatGPT/GitHub without your Google authorization

1. Enable the Google Apps Script API in your personal Apps Script user settings.
2. Complete one interactive Google OAuth authorization for `clasp`.
3. Put the generated TEST HMAC value into the Apps Script **Script Properties** under key `EJS_HMAC_SHARED_SECRET_TEST`.
4. Run `ejsGhAuthorizationProbeV1` once in the Apps Script editor to grant the script's spreadsheet scope. The probe is read-only.

No code is copied into the Apps Script editor.

## Windows one-shot helper

From a local clone of `cagataybuyuk/europe-job-scan` on `main`, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\gd004\bootstrap_zero_cost_user_trust.ps1
```

Prerequisites checked by the script:

- `git`
- Node.js / `npm` / `npx`
- GitHub CLI `gh`, authenticated to the repository

The helper then:

1. opens the Apps Script API user-setting page;
2. runs the one-time `clasp login --no-localhost` flow when needed;
3. creates `Europe Job Scan Control Plane - TEST` automatically if the TEST Script ID is not already configured;
4. writes the Script ID to GitHub repository variable `EJS_APPS_SCRIPT_ZERO_COST_ID_TEST`;
5. sends `~/.clasprc.json` directly to encrypted GitHub secret `EJS_CLASPRC_JSON_TEST`;
6. generates a random 256-bit HMAC value and sends it directly to encrypted GitHub secret `EJS_GH_HMAC_SECRET_TEST`;
7. copies the same HMAC value to the local clipboard (or a temporary local file) so the account owner can add the one Script Property without exposing it to chat, Drive, Sheet or repository;
8. dispatches and watches the exact-main-SHA zero-cost Apps Script deployment workflow;
9. asks the owner to run the read-only authorization probe once;
10. dispatches and watches the TEST-004B/C signed runtime smoke workflow.

The temporary plaintext HMAC file is deleted after the owner confirms the Script Property was saved.

## Security boundary

Never send `.clasprc.json`, the HMAC value, cookies, candidate answers or document bytes to ChatGPT or place them in repository/Drive/Sheet/log output.

GD-004 remains:

- `browser_read=true`
- `form_value_write=false`
- `approved_file_upload=false`
- `final_submit=false`

CAPTCHA/MFA bypass remains forbidden. PROD spreadsheet ID remains deny-only.

## Expected successful end state

- Apps Script TEST project exists without Google Cloud billing.
- Canonical Apps Script code came only from GitHub `main`.
- A versioned Web App deployment is bound to the exact approved main SHA.
- `ejsGhAuthorizationProbeV1` returns TEST and `hmac_secret_configured=true` with zero side effects.
- TEST-004B: valid signature/replay pass; invalid signature and expired requests reject.
- TEST-004C: first claim/reconcile records; exact replay is idempotent; mutation/upload/submit counts remain zero.

Only after these gates pass should the project move to the next separately approved live-write phase.
