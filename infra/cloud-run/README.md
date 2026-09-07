# TEST Cloud Run deployment

Target: `ejs-browser-worker-test` in `europe-west1`.

Authentication: GitHub OIDC -> Google Workload Identity Federation -> dedicated deploy service account. Long-lived service-account keys are prohibited.

Runtime bounds inherited from GD-002: min=0, max=1, concurrency=1, 1 vCPU, 2 GiB, request timeout 180s. External browser capability remains read-only until later approved gates.

## One-time provisioning boundary

A dedicated Google Cloud TEST project with billing enabled must exist before the GitHub deployment can run. Do not use the PROD operational project or grant production application/form/submit authority.

From an authenticated Google Cloud Shell session:

```bash
export PROJECT_ID='<dedicated-test-project-id>'
bash infra/cloud-run/provision_github_wif_test.sh
```

The provisioning script is idempotent and creates/configures only TEST engineering resources:

- Artifact Registry Docker repository `ejs-browser-test` in `europe-west1`;
- runtime SA `ejs-browser-runtime-test`;
- Apps Script invoker SA `ejs-appscript-invoker-test`;
- GitHub deploy SA `ejs-github-deploy-test`;
- Workload Identity pool/provider restricted to repository ID `1358973851`, owner ID `74713279`, `refs/heads/main`, GitHub environment `test`, and the exact Cloud Run deploy workflow;
- least-purpose TEST deploy/build IAM needed by the current pipeline.

The script prints three non-secret GitHub variable values required by `.github/workflows/deploy-test-cloud-run.yml`:

- `GCP_PROJECT_ID_TEST`
- `GCP_WIF_PROVIDER_TEST`
- `GCP_DEPLOY_SERVICE_ACCOUNT_TEST`

These values are identifiers, not credentials. No service-account JSON key is created.

## Cost guard

Before first Cloud Run deployment, configure the approved low-value TEST billing budget/alert for this dedicated project. Budget configuration is intentionally not hard-coded because the billing account currency and notification recipients are account-owned values.

## Deployment readback

The GitHub deployment workflow pins an exact commit SHA, authenticates through WIF, builds the exact image into Artifact Registry, deploys the private service with the dedicated runtime identity, grants `roles/run.invoker` only to the TEST Apps Script invoker SA, and fails unless readback confirms:

- exact runtime service account;
- exact image tagged by approved commit SHA;
- `EJS_BUILD_SHA` equals the approved commit SHA;
- Apps Script invoker binding exists;
- `allUsers` / `allAuthenticatedUsers` are absent.
