# GD-004 Zero-Cost Apps Script Bootstrap — superseded entrypoint

This filename is retained for backward-compatible links only. The canonical, current and executable owner runbook is:

`docs/GD004_ZERO_COST_USER_TRUST_BOOTSTRAP.md`

Use only these active bootstrap identities:

- GitHub encrypted secret: `EJS_CLASPRC_JSON_TEST`
- GitHub variable: `EJS_APPS_SCRIPT_ZERO_COST_ID_TEST`
- GitHub encrypted secret **and** Apps Script Script Property: `EJS_HMAC_SHARED_SECRET_TEST`

The current helper is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\gd004\bootstrap_zero_cost_user_trust.ps1
```

It automatically creates the standalone TEST Apps Script project when needed, deploys the exact approved `main` SHA as a versioned Web App, and then guides the account owner through the only unavoidable Google consent/property steps plus synthetic TEST queue initialization. No manual source-code paste, manual Web App deployment, Google Cloud billing project or Cloud Run provisioning is required.

GD-004 remains TEST-only and read-only toward employers: `form_value_write=false`, `approved_file_upload=false`, `final_submit=false`; CAPTCHA/MFA bypass is forbidden.
