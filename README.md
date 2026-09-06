# Europe Job Scan

Canonical source repository for the Europe Job Scan autonomous application system.

## Source ownership

- **Code:** this private GitHub repository (`main` after an approved PR is merged).
- **Governance:** Google Drive `00_BASLA_BURADAN`, `01_Proje_Plani`, `02_Guncel_Durum`, and approved GD/TEST/DEPLOY records.
- **Operational state:** Google Sheets (`EU Job Tracker` PROD, `TEST_EU_Job_Tracker` TEST).
- **Assets:** Google Drive.

Direct Apps Script editor changes are not canonical. TEST deployment is intended to be performed by GitHub Actions + `clasp`; Cloud Run deployment is intended to use GitHub OIDC / Google Workload Identity Federation.

## Migrated baseline

- Service version: `0.12.0a1`
- Execution contract: `EJS-GN-1.0`
- Approved engineering gate: `GD-003`
- Regression baseline: `221/221` tests, deterministic partitions
- External form write/upload/final-submit authority during GD-003: **OFF**

See `MIGRATION_BASELINE.json` for the immutable source-package hash and CI partitions. The legacy MW-1 connector-capture fixture is committed only in a sanitized form; candidate-specific values were replaced with synthetic placeholders during migration.

## Test

```bash
python -m pip install -e '.[test]'
python -m playwright install chromium
python -m pytest -q
```

GitHub CI deliberately runs the regression suite in three partitions because the full browser suite can exceed a single short-lived runner/tool wall-clock while each deterministic partition is stable.

## Apps Script

Canonical staged source is currently under `google_native/apps_script/`.

- `appsscript.json` is committed.
- `.clasprc.json` is **never** committed.
- `.clasp.json` is materialized at workflow runtime from the repository variable `EJS_APPS_SCRIPT_ID_TEST`.
- OAuth material is expected only in encrypted GitHub Actions secret `EJS_CLASPRC_JSON_TEST` after the one-time user authorization bootstrap.

## Cloud Run

Cloud Run source/runtime configuration is under `google_native/cloud_run/` plus the Python package under `src/ejs/`.

TEST deployment must retain the GD-002 boundaries: request-based runtime, min instances 0, max instances 1, concurrency 1, 1 vCPU, 2 GiB, and no form-write/file-upload/final-submit authority.

## Safety

- No CAPTCHA/MFA bypass.
- No invented work-right, sponsorship, salary, legal, experience or demographic facts.
- A submit click alone can never mark an application Applied.
- Production submit behavior is governed by the separately approved SUBMIT-1 runtime gates and is not enabled by this migration.
