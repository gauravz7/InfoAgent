#!/usr/bin/env bash
# One-time setup: deploy the digest as a Cloud Run Job triggered DAILY by
# Cloud Scheduler. Run this from the repo root with gcloud authenticated.
#
# Before running, create the GitHub-token secret ONCE (do NOT commit the token):
#   gh auth token | gcloud secrets create pages-token --data-file=- --project "$PROJECT"
# (the PAT needs write access to the Pages repo)
#
set -euo pipefail

PROJECT="${PROJECT:-vital-octagon-19612}"
REGION="${REGION:-us-central1}"
JOB="${JOB:-digest-job}"
SCHED="${SCHED:-digest-schedule}"
SA_NAME="${SA_NAME:-digest-bot}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
PAGES_REPO="${PAGES_REPO:-gauravz7/gauravz7.github.io}"
DIGEST_BASE_URL="${DIGEST_BASE_URL:-https://gauravz7.github.io/digest}"
# Daily at 06:17 UTC (off the top-of-hour to dodge scheduler backlog).
SCHEDULE="${SCHEDULE:-17 6 * * *}"

gcloud config set project "$PROJECT"

echo ">> enabling APIs"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com

echo ">> service account + roles"
gcloud iam service-accounts create "$SA_NAME" --display-name "Digest bot" 2>/dev/null || true
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:$SA" --role roles/aiplatform.user --condition=None -q
gcloud secrets add-iam-policy-binding pages-token \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor -q

echo ">> build + deploy the Cloud Run Job (Cloud Build builds the image)"
gcloud run jobs deploy "$JOB" \
  --source . --region "$REGION" \
  --service-account "$SA" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,PAGES_REPO=$PAGES_REPO,DIGEST_BASE_URL=$DIGEST_BASE_URL" \
  --set-secrets "PAGES_TOKEN=pages-token:latest" \
  --max-retries 1 --task-timeout 1800 --memory 1Gi --cpu 1

echo ">> allow the SA to invoke the job, then schedule daily ${SCHEDULE} UTC"
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker -q
gcloud scheduler jobs create http "$SCHED" --location "$REGION" \
  --schedule "$SCHEDULE" --time-zone "UTC" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" \
  --http-method POST \
  --oauth-service-account-email "$SA" 2>/dev/null \
  || gcloud scheduler jobs update http "$SCHED" --location "$REGION" \
       --schedule "$SCHEDULE" --time-zone "UTC" \
       --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" \
       --http-method POST --oauth-service-account-email "$SA"

echo ">> done. Test now with:  gcloud run jobs execute $JOB --region $REGION --wait"
