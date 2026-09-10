# GD-004 zero-cost user trust bootstrap

This is the one-time account-owner step required before live TEST-004B/C can run against the real TEST Apps Script Web App. It requires **no Google Cloud billing project** and **no manual source-code paste**.

## Already automated

GitHub `main` owns the canonical source and workflows for:

- zero-cost Apps Script source push with `clasp`;
- exact-main-SHA generated deployment marker (`EjsDeploymentV1.gs`);
- immutable Apps Script version + Web App deployment;
- exact-SHA deployment URL discovery;
- EJS-GH-EXEC-0.1 HMAC, expiry, nonce/replay and capability verification;
- explicit TEST queue row claim + independent readback;
- bounded approved synthetic PDF transport from Drive read-only scope;
- real Chromium synthetic inspection;
- result identity/hash validation and durable reconcile;
- zero form-write/upload/submit guards.

The Web App is `ANYONE_ANONYMOUS` + `USER_DEPLOYING`, but every accepted request is fail-closed behind the signed protocol. PROD spreadsheet ID remains deny-only.

## Canonical secret/property names

| Name | Location | Purpose |
| --- | --- | --- |
| `EJS_CLASPRC_JSON_TEST` | GitHub encrypted secret | one-time owner clasp OAuth credential |
| `EJS_APPS_SCRIPT_ZERO_COST_ID_TEST` | GitHub variable | standalone TEST Apps Script ID |
| `EJS_HMAC_SHARED_SECRET_TEST` | GitHub encrypted secret **and** Apps Script Script Property | same locally generated 256-bit HMAC value |

Never send `.clasprc.json`, OAuth tokens or the HMAC value to ChatGPT, Drive, Sheets, issue comments, Actions artifacts or logs.

## Verified synthetic TEST asset

The TEST-only PDF is not a candidate CV. It contains only `GD-004 SYNTHETIC TEST - NO CANDIDATE DATA` and is stored in the project TEST-document folder. The runtime validates exact folder, file ID, filename, MIME, byte size and SHA-256 before making it available in memory. It is never submitted to an employer during GD-004.

## Windows one-shot helper

From an up-to-date local clone of `cagataybuyuk/europe-job-scan` on `main`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\gd004\bootstrap_zero_cost_user_trust.ps1
```

Prerequisites checked by the helper:

- Git
- Node.js / npm / npx
- GitHub CLI `gh`, authenticated to the repository

The helper performs or guides exactly these actions:

1. Opens the free Apps Script API user-setting page.
2. Runs one interactive Google OAuth flow for `clasp` if needed.
3. Automatically creates `Europe Job Scan Control Plane - TEST` when no TEST Script ID exists.
4. Writes the Script ID directly to GitHub variable `EJS_APPS_SCRIPT_ZERO_COST_ID_TEST`.
5. Sends `~/.clasprc.json` directly to encrypted GitHub secret `EJS_CLASPRC_JSON_TEST`.
6. Generates a random 256-bit HMAC and sends it directly to encrypted GitHub secret `EJS_HMAC_SHARED_SECRET_TEST`.
7. Places the same HMAC value only on the local clipboard (or a temporary local file) so the owner can add Apps Script Script Property `EJS_HMAC_SHARED_SECRET_TEST`; the local plaintext is then cleared/deleted.
8. Dispatches and watches exact-main-SHA zero-cost Apps Script deployment. GitHub creates the versioned Web App automatically; there is no manual Deploy > New deployment step.
9. Opens the Apps Script editor. The owner runs `ejsGhAuthorizationProbeV1` once to approve the explicit Sheets + Drive-readonly scopes. It must report `hmac_secret_configured=true` and zero side effects.
10. The owner runs `ejsGhInitializeSyntheticTestV1` once. It creates/uses only `GD004 TEST Execution Queue` in `TEST_EU_Job_Tracker`, stages one immutable synthetic row for the deployed SHA and prints non-secret `queue_row` + `execution_id`.
11. The helper validates those non-secret values and dispatches `gd004-test-control-plane.yml` for the exact SHA/row/execution.
12. GitHub resolves the exact-SHA Web App deployment, runs signed TEST-004B protocol negatives/replay, claims the explicit TEST row, verifies the synthetic PDF in memory, runs real Chromium, reconciles the result and independently reads the durable state back.

## Expected live TEST-004B/C acceptance

Success requires:

- exact canonical main SHA deployed;
- valid signed health succeeds;
- exact replay is idempotent;
- invalid signature, expired request, PROD target and request collision reject;
- one explicit TEST queue claim winner;
- immutable payload/hash readback;
- synthetic PDF folder/name/MIME/size/SHA-256 verification;
- Chromium result bound to the same execution/source SHA;
- queue state `COMPLETED` with matching result hash;
- retry performs no second browser action or duplicate write;
- `mutation_count=0`, `upload_count=0`, `submit_count=0`.

A blocked browser becomes `REVIEW_REQUIRED`, never `Applied`. A claimed-but-interrupted execution stays quarantined and is not silently re-claimed.

## GD-004 authority boundary

- `browser_read=true`
- `form_value_write=false`
- `approved_file_upload=false`
- `final_submit=false`
- CAPTCHA/MFA bypass forbidden
- candidate/legal/work-right/salary/protected facts are not created or inferred
- Cloud Run remains inactive fallback only

Live safe-fill, real CV upload and final submit require later, separate release gates. Do not mark TEST-004B/C live PASS from mock/CI evidence alone; the owner's actual Apps Script Web App run is the acceptance event.
