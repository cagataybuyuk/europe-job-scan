#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID, e.g. ejs-browser-test-<unique-suffix>}"
: "${APPS_SCRIPT_USER_EMAIL:?Set the Google account email that owns/runs the Apps Script project}"

REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-ejs-browser-worker-test}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-ejs-browser-runtime-test}"
INVOKER_SA_NAME="${INVOKER_SA_NAME:-ejs-appscript-invoker-test}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="${INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com

gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" --display-name="EJS TEST browser runtime"
gcloud iam service-accounts describe "$INVOKER_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$INVOKER_SA_NAME" --display-name="EJS TEST Apps Script invoker"

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --no-allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --cpu 1 \
  --memory 2Gi \
  --concurrency 1 \
  --max-instances 1 \
  --min-instances 0 \
  --timeout 180

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

gcloud run services add-iam-policy-binding "$SERVICE" \
  --region "$REGION" \
  --member="serviceAccount:${INVOKER_SA}" \
  --role="roles/run.invoker"

gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
  --member="user:${APPS_SCRIPT_USER_EMAIL}" \
  --role="roles/iam.serviceAccountOpenIdTokenCreator"

cat <<EOF
EJS TEST Cloud Run bootstrap complete.
PROJECT_ID=${PROJECT_ID}
REGION=${REGION}
SERVICE=${SERVICE}
SERVICE_URL=${SERVICE_URL}
RUNTIME_SA=${RUNTIME_SA}
INVOKER_SA=${INVOKER_SA}

Do not make the service public. Put SERVICE_URL, PROJECT_ID and INVOKER_SA in Apps Script Script Properties.
EOF
