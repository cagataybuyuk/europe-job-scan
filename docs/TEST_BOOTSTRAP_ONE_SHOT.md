# Europe Job Scan — TEST Bootstrap One-Shot Runbook

Status: TEST-only provisioning handoff after GD-003 source/CI hardening.

Canonical source: `cagataybuyuk/europe-job-scan` / `main`.

Security invariants:

- Do not paste `.clasprc.json`, OAuth refresh tokens, cookies, passwords, service-account keys, or other secret values into ChatGPT, Drive documents, issues, commits, or logs.
- No long-lived Google service-account JSON key is used.
- Cloud Run remains private.
- This bootstrap gives no PROD application/form/upload/submit authority.
- Final submit remains OFF.

## What the user must do once

There are two user-owned trust bootstraps that cannot safely be delegated from repository code alone:

1. create/select a dedicated TEST Google Cloud project with billing enabled;
2. authorize `clasp` against the Google account that owns the standalone TEST Apps Script project.

Everything after those trust prompts is scripted or GitHub-controlled.

## A. Dedicated TEST Google Cloud project

Create a new Google Cloud project dedicated to Europe Job Scan TEST and attach billing. Do not reuse a production or unrelated project.

Record only the non-secret project ID as `PROJECT_ID`.

Recommended region remains `europe-west1`.

## B. Run the TEST GCP/WIF provisioning script in Google Cloud Shell

Cloud Shell is recommended because the active Google identity and `gcloud` are already available.

Authenticate GitHub CLI once if needed, then clone the private canonical repository:

```bash
gh auth login --web
gh repo clone cagataybuyuk/europe-job-scan
cd europe-job-scan
git checkout main
git pull --ff-only origin main
```

Set the dedicated TEST project ID and run the idempotent provisioner:

```bash
export PROJECT_ID='<YOUR_TEST_GCP_PROJECT_ID>'
bash infra/cloud-run/provision_github_wif_test.sh
```

The script validates billing, enables required APIs, creates the TEST Artifact Registry repository, creates dedicated runtime/invoker/deploy service accounts, creates repository-scoped Workload Identity Federation, grants only TEST-project deployment/build permissions, and prints the exact non-secret values required by GitHub.

Do not change the repository ID, owner ID, `main` ref, `test` environment, or exact deployment workflow-ref conditions unless a later approved GD explicitly changes that trust boundary.

## C. Put non-secret GCP values into GitHub Actions variables

From the same Cloud Shell session:

```bash
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
WIF_PROVIDER="$(gcloud iam workload-identity-pools providers describe ejs-repo \
  --workload-identity-pool ejs-github-test \
  --location global \
  --project "$PROJECT_ID" \
  --format='value(name)')"
DEPLOY_SA="ejs-github-deploy-test@${PROJECT_ID}.iam.gserviceaccount.com"

gh variable set GCP_PROJECT_ID_TEST --body "$PROJECT_ID"
gh variable set GCP_WIF_PROVIDER_TEST --body "$WIF_PROVIDER"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT_TEST --body "$DEPLOY_SA"
```

These three values are identifiers, not secrets.

## D. One-time standalone TEST Apps Script + clasp OAuth bootstrap

The Google account owner must enable the Apps Script API for their account and authorize clasp once.

From the repository root:

```bash
REPO_DIR="$(pwd)"
npm install
npx clasp login --no-localhost
```

Never print or paste `$HOME/.clasprc.json` into chat or a document.

Create the standalone TEST script in a temporary directory so repository source files are not overwritten:

```bash
mkdir -p /tmp/ejs-apps-script-bootstrap
cd /tmp/ejs-apps-script-bootstrap
"$REPO_DIR/node_modules/.bin/clasp" create --title "Europe Job Scan TEST Control Plane" --type standalone
SCRIPT_ID="$(node -e "const fs=require('fs'); const p=JSON.parse(fs.readFileSync('.clasp.json','utf8')); process.stdout.write(p.scriptId)")"
echo "TEST Script ID created: ${SCRIPT_ID}"
```

### Bind the standalone Apps Script project to the same TEST Google Cloud project

Open the new Apps Script project, go to **Project Settings → Google Cloud Platform (GCP) Project**, and set the **Project Number** for the dedicated TEST GCP project:

```bash
echo "$PROJECT_NUMBER"
```

This browser setting is a user-owned Google trust/configuration action and is intentionally not automated through repository code.

## E. Store TEST Apps Script deploy values in GitHub without exposing the OAuth secret

Return to the repository directory and set the Script ID variable plus encrypted clasp OAuth secret directly with GitHub CLI:

```bash
cd "$REPO_DIR"

gh variable set EJS_APPS_SCRIPT_ID_TEST --body "$SCRIPT_ID"
gh secret set EJS_CLASPRC_JSON_TEST < "$HOME/.clasprc.json"
```

Do not use `cat ~/.clasprc.json` in a shared/logged terminal and do not paste its contents into ChatGPT.

## F. Deploy the exact immutable `main` SHA to TEST

Resolve the exact current `main` SHA:

```bash
git fetch origin main
EXPECTED_SHA="$(git rev-parse origin/main)"
echo "$EXPECTED_SHA"
```

Trigger both TEST deployment workflows with the same immutable SHA:

```bash
gh workflow run deploy-test-apps-script.yml -f expected_sha="$EXPECTED_SHA" --ref main
gh workflow run deploy-test-cloud-run.yml -f expected_sha="$EXPECTED_SHA" --ref main
```

Then wait for the runs and inspect their status:

```bash
gh run list --limit 10
```

Do not proceed to any live employer write/upload/submit behavior from workflow success alone. TEST-002C/D/E and TEST-001B–F must be reconciled from actual deployment/runtime readback first.

## Expected post-bootstrap evidence

The engineering workflow will validate and record:

- TEST Apps Script exact Script ID and source guard;
- GitHub OIDC → WIF → deploy service-account authentication;
- no service-account JSON key;
- Artifact Registry image tied to exact commit SHA;
- private Cloud Run service with explicit runtime service account;
- `EJS_BUILD_SHA` exact runtime readback;
- authenticated `/health` and echo;
- Apps Script environment/idempotency smoke;
- synthetic Chromium and external read-only ATS egress/SSRF tests;
- TEST tracker reconciliation/replay;
- zero PROD business/form/upload/submit mutation.

## Stop conditions

Stop and return to engineering review if any of these occurs:

- billing/project is not the dedicated TEST project;
- WIF condition differs from the approved repository/owner/main/test/workflow boundary;
- a service-account JSON key is requested;
- Cloud Run becomes public;
- deployment SHA differs from the approved immutable SHA;
- TEST Apps Script is bound to a different GCP project;
- any workflow requests PROD credentials or PROD spreadsheet authority;
- any CAPTCHA/MFA bypass or employer-form submit behavior appears.
