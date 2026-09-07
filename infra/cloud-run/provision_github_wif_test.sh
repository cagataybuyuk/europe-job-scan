#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to the dedicated TEST Google Cloud project ID}"

REGION="${REGION:-europe-west1}"
ARTIFACT_REPO="${ARTIFACT_REPO:-ejs-browser-test}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-ejs-browser-runtime-test}"
INVOKER_SA_NAME="${INVOKER_SA_NAME:-ejs-appscript-invoker-test}"
DEPLOY_SA_NAME="${DEPLOY_SA_NAME:-ejs-github-deploy-test}"
WIF_POOL_ID="${WIF_POOL_ID:-ejs-github-test}"
WIF_PROVIDER_ID="${WIF_PROVIDER_ID:-ejs-repo}"
GITHUB_REPO="${GITHUB_REPO:-cagataybuyuk/europe-job-scan}"
GITHUB_REPOSITORY_ID="${GITHUB_REPOSITORY_ID:-1358973851}"
GITHUB_OWNER_ID="${GITHUB_OWNER_ID:-74713279}"
GITHUB_WORKFLOW_REF="${GITHUB_WORKFLOW_REF:-cagataybuyuk/europe-job-scan/.github/workflows/deploy-test-cloud-run.yml@refs/heads/main}"

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="${INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "[1/9] Validate dedicated TEST project and billing"
gcloud projects describe "$PROJECT_ID" >/dev/null
BILLING_ENABLED="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
if [[ "$BILLING_ENABLED" != "True" && "$BILLING_ENABLED" != "true" ]]; then
  echo "ERROR: billing is not enabled for PROJECT_ID=$PROJECT_ID" >&2
  exit 2
fi

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "[2/9] Enable TEST APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  serviceusage.googleapis.com \
  --project "$PROJECT_ID" >/dev/null

echo "[3/9] Create Artifact Registry Docker repository if absent"
if ! gcloud artifacts repositories describe "$ARTIFACT_REPO" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$ARTIFACT_REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Europe Job Scan TEST browser worker images" \
    --project "$PROJECT_ID"
fi

echo "[4/9] Create dedicated TEST service accounts if absent"
for SA_NAME in "$RUNTIME_SA_NAME" "$INVOKER_SA_NAME" "$DEPLOY_SA_NAME"; do
  SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SA_NAME" \
      --project "$PROJECT_ID" \
      --display-name="Europe Job Scan TEST ${SA_NAME}"
  fi
done

echo "[5/9] Create repository-scoped Workload Identity pool/provider"
if ! gcloud iam workload-identity-pools describe "$WIF_POOL_ID" --location=global --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$WIF_POOL_ID" \
    --location=global \
    --project "$PROJECT_ID" \
    --display-name="EJS GitHub TEST"
fi

ATTRIBUTE_MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref,attribute.environment=assertion.environment"
ATTRIBUTE_CONDITION="assertion.repository_id == '${GITHUB_REPOSITORY_ID}' && assertion.repository_owner_id == '${GITHUB_OWNER_ID}' && assertion.ref == 'refs/heads/main' && assertion.environment == 'test' && assertion.workflow_ref == '${GITHUB_WORKFLOW_REF}'"

if ! gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER_ID" \
  --workload-identity-pool "$WIF_POOL_ID" --location=global --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER_ID" \
    --workload-identity-pool "$WIF_POOL_ID" \
    --location=global \
    --project "$PROJECT_ID" \
    --display-name="EJS GitHub TEST repository provider" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$ATTRIBUTE_MAPPING" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
else
  gcloud iam workload-identity-pools providers update-oidc "$WIF_PROVIDER_ID" \
    --workload-identity-pool "$WIF_POOL_ID" \
    --location=global \
    --project "$PROJECT_ID" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$ATTRIBUTE_MAPPING" \
    --attribute-condition="$ATTRIBUTE_CONDITION"
fi

WIF_POOL_NAME="$(gcloud iam workload-identity-pools describe "$WIF_POOL_ID" --location=global --project "$PROJECT_ID" --format='value(name)')"
WIF_PROVIDER_NAME="$(gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER_ID" --workload-identity-pool "$WIF_POOL_ID" --location=global --project "$PROJECT_ID" --format='value(name)')"
WIF_MEMBER="principalSet://iam.googleapis.com/${WIF_POOL_NAME}/attribute.repository_id/${GITHUB_REPOSITORY_ID}"

echo "[6/9] Allow only the scoped GitHub identity to impersonate TEST deploy SA"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --project "$PROJECT_ID" \
  --member "$WIF_MEMBER" \
  --role roles/iam.workloadIdentityUser >/dev/null

echo "[7/9] Grant deployment and manual-build submission permissions to the TEST deploy SA"
for ROLE in \
  roles/cloudbuild.builds.editor \
  roles/run.admin \
  roles/artifactregistry.reader \
  roles/storage.bucketViewer \
  roles/storage.objectUser \
  roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="$ROLE" >/dev/null
done

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role=roles/iam.serviceAccountUser >/dev/null

echo "[8/9] Grant build-time permissions to the project's actual default Cloud Build SA"
BUILD_SA="$(gcloud builds get-default-service-account --project "$PROJECT_ID")"
for ROLE in \
  roles/artifactregistry.writer \
  roles/logging.logWriter \
  roles/storage.bucketViewer \
  roles/storage.objectUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${BUILD_SA}" \
    --role="$ROLE" >/dev/null
done

echo "[9/9] Read back immutable TEST deployment values"
cat <<EOF
EJS TEST GitHub WIF provisioning complete.

PROJECT_ID=${PROJECT_ID}
PROJECT_NUMBER=${PROJECT_NUMBER}
REGION=${REGION}
ARTIFACT_REPO=${ARTIFACT_REPO}
RUNTIME_SA=${RUNTIME_SA}
INVOKER_SA=${INVOKER_SA}
DEPLOY_SA=${DEPLOY_SA}
BUILD_SA=${BUILD_SA}
WIF_PROVIDER=${WIF_PROVIDER_NAME}
GITHUB_REPO=${GITHUB_REPO}
GITHUB_REPOSITORY_ID=${GITHUB_REPOSITORY_ID}
GITHUB_OWNER_ID=${GITHUB_OWNER_ID}

Set these non-secret values in GitHub environment/repository variables:
GCP_PROJECT_ID_TEST=${PROJECT_ID}
GCP_WIF_PROVIDER_TEST=${WIF_PROVIDER_NAME}
GCP_DEPLOY_SERVICE_ACCOUNT_TEST=${DEPLOY_SA}

Security boundary:
- provider admits only repository_id ${GITHUB_REPOSITORY_ID}, owner_id ${GITHUB_OWNER_ID}, refs/heads/main, environment=test, and the exact Cloud Run deploy workflow_ref;
- storage roles apply only inside this dedicated TEST project and are required for manual Cloud Build source staging/log access;
- no service-account JSON key is created;
- this script grants no PROD application/form/submit authority.
EOF
