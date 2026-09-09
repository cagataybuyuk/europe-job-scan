# GD-004 Zero-Cost Apps Script Bootstrap

Purpose: one-time TEST trust bootstrap for the zero-cost GitHub browser execution path. This path does **not** require Google Cloud billing or Cloud Run.

## Fixed safety boundary

- Environment: TEST only.
- TEST Sheet: `17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM`.
- PROD Sheet is explicitly denied by source guards.
- `browser_read=true`.
- `form_value_write=false`.
- `approved_file_upload=false`.
- `final_submit=false`.
- CAPTCHA/MFA bypass is not implemented.
- HMAC secret must never be committed, pasted into Drive/Sheets, or sent in chat.

## Why the Web App can be anonymous

`appsscript.json` declares `webapp.access=ANYONE_ANONYMOUS` and `executeAs=USER_DEPLOYING` so a GitHub-hosted runner can reach the endpoint without Google account cookies. The endpoint is not trusted by network location: every accepted request must pass the `EJS-GH-EXEC-0.1` HMAC-SHA256 signature, TTL, nonce/replay, operation allowlist, TEST target, immutable source SHA, and capability guards. Invalid requests fail closed.

## User-owned one-time bootstrap (Windows PowerShell)

### 1. Create the standalone TEST script

Open `https://script.google.com/create` with the Google account that owns the TEST tracker.

Rename the project to:

`Europe Job Scan Zero Cost Control Plane - TEST`

In **Project Settings**, copy the **Script ID**. The Script ID is not a secret.

If Apps Script API access is disabled for the account, enable it from Apps Script user settings; no billing project is required for this workflow.

### 2. Authenticate clasp locally once

From a local clone of `cagataybuyuk/europe-job-scan` on `main`:

```powershell
npm install
npx clasp login
```

Complete the Google OAuth screen in the browser. Do not send `.clasprc.json` to ChatGPT.

Store the credential directly as a GitHub encrypted secret (requires GitHub CLI already authenticated):

```powershell
Get-Content "$HOME\.clasprc.json" -Raw | gh secret set EJS_CLASPRC_JSON_TEST
```

Store the non-secret Script ID as a repository variable:

```powershell
gh variable set EJS_APPS_SCRIPT_ZERO_COST_ID_TEST --body "<SCRIPT_ID>"
```

### 3. Generate the HMAC secret locally

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToHexString($bytes).ToLowerInvariant()
$secret | gh secret set EJS_GH_HMAC_SECRET_TEST
```

Do **not** paste `$secret` into chat.

In Apps Script **Project Settings → Script properties**, add:

- Property: `EJS_GH_HMAC_SECRET`
- Value: the local `$secret` value

The same secret therefore exists only in GitHub encrypted secrets and Apps Script Script Properties.

### 4. Deploy canonical source from GitHub

After this bootstrap hardening is merged, use the current approved `main` SHA:

```powershell
gh workflow run deploy-test-apps-script-zero-cost.yml -f expected_sha=<APPROVED_MAIN_SHA>
```

Wait for the workflow to pass. It pushes only `google_native/apps_script_zero_cost` to the TEST Script ID.

### 5. Create the Web App deployment once

In Apps Script choose **Deploy → New deployment → Web app**. The manifest is already pinned to:

- Execute as: deploying user.
- Access: anyone, including anonymous.

Create the deployment and copy the `/exec` Web App URL. The URL is not a secret; send only this URL and the Script ID back to the project workflow if needed. Never send the HMAC secret or `.clasprc.json`.

## After bootstrap

The engineering workflow resumes automatically:

1. TEST-004B signed health request / invalid signature / expiry / replay tests.
2. TEST-004C claim + reconcile idempotency and TEST/PROD fence readback.
3. GitHub-hosted browser execution binds to the signed Apps Script control plane.
4. Live form write/upload/submit remain OFF until separate approved gates.

## Rotation / rollback

If a secret may have been exposed, rotate both copies: GitHub `EJS_GH_HMAC_SECRET_TEST` and Apps Script `EJS_GH_HMAC_SECRET`.

To stop the endpoint, delete the Apps Script Web App deployment or remove/rotate the HMAC property. Cloud Run is not required and remains an inactive fallback.
