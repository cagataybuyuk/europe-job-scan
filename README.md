# Europe Job Scan

Canonical source repository for the Europe Job Scan autonomous application system.

## Current delivery status

The first reviewed Version 1 REF7089X application was confirmed on 7 November
2026 through the separately reviewed interactive CUA canary. Its canonical
single-application slot is consumed. The local command and its synthetic browser
tests provide a separate execution lane; unattended LinkedIn return handling,
profile correction and central outcome reconciliation have their own acceptance
gates. No multi-job production rollout is enabled by this result.

See [`docs/MVP_INTEGRATED_SINGLE_APPLICATION.md`](docs/MVP_INTEGRATED_SINGLE_APPLICATION.md)
for the current local lane, evidence and remaining limits. Older milestone
documents retain their original test/authority scope.
The [native-first entry and profile correction contract](docs/MVP_NATIVE_PROFILE_CORRECTION.md)
records USER-NATIVE-MVP-20261112 and the separately reviewed draft editor limits.

## Production target

The longer-term production target is a **daily multi-ATS autonomous application runtime**. Priority coverage is LinkedIn (external routing + Easy Apply where safely automatable), SmartRecruiters, Workday, Greenhouse, Lever and ADP Workforce Now. Each adapter requires its own acceptance and rollout authority. Unsupported/ambiguous/challenged applications fail closed into `human_review`; CAPTCHA/MFA bypass and invented candidate facts remain forbidden.

Canonical launch plan: [`docs/PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md`](docs/PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md).

## Planned AWLI fast-path

Evaluate Apply with LinkedIn (AWLI) as the primary entry/prefill route where reviewed and session-ready; retain native ATS fallback. LinkedIn is never the source-of-truth: verify returned prefills against the canonical candidate profile. Session/MFA/challenges fail closed or enter `human_review`. Screening, CV, SUBMIT-1, submit and confirmation remain with the underlying ATS adapter. AWLI is distinct from Easy Apply and is not yet implemented/live-proven.

Next: [MacBook Air M2 ADP native vs AWLI feasibility spike](docs/ADP_AWLI_FEASIBILITY_SPIKE.md).

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

GitHub CI runs eight jobs covering static guards, core regression, three MVP
inspection/network suites, browser prefill, upload/E2E and the GD-004 runtime.
Windows bootstrap has a separate workflow. Each workflow's explicit pytest list
must include new regression suites; a green unrelated job does not test them.

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
- Production submit behavior is governed by SUBMIT-1 and remains adapter-gated: each priority ATS must earn its own reviewed live submit canary before unattended production submit is enabled for that adapter.
