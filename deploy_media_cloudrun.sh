#!/usr/bin/env bash
# One-time setup: deploy the WEEKLY GENERATIVE MEDIA digest as a SECOND Cloud Run
# Job triggered WEEKLY (Sunday 06:00 Asia/Singapore) by Cloud Scheduler. Run this
# from the repo root with gcloud authenticated. Reuses the same container image
# as the daily digest, overriding the entrypoint to run_job_media.sh.
#
# Reuses the existing 'digest-bot' service account and 'pages-token' secret created
# by deploy_cloudrun.sh. If you have not run that yet, create the token secret ONCE
# (do NOT commit the token):
#   gh auth token | gcloud secrets create pages-token --data-file=- --project "$PROJECT"
# (the PAT needs write access to the Pages repo)
#
set -euo pipefail

# Project id is not hard-coded: pass PROJECT=... or rely on your active gcloud config.
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "ERROR: no GCP project. Run 'gcloud config set project <id>' or PROJECT=<id> $0" >&2
  exit 1
fi
REGION="${REGION:-us-central1}"
JOB="${JOB:-media-digest-job}"
SCHED="${SCHED:-media-digest-schedule}"
SA_NAME="${SA_NAME:-digest-bot}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
PAGES_REPO="${PAGES_REPO:-gauravz7/gauravz7.github.io}"
DIGEST_BASE_URL="${DIGEST_BASE_URL:-https://gauravz7.github.io/media-digest}"
# Weekly on Sunday at 06:00 Asia/Singapore (day-of-week 0 = Sunday).
SCHEDULE="${SCHEDULE:-0 6 * * 0}"
TIME_ZONE="${TIME_ZONE:-Asia/Singapore}"

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

# Optional SMTP email dispatch. To enable, export these before running deploy and
# create the password secret once:
#   export SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=you@gmail.com \
#          EMAIL_FROM=you@gmail.com EMAIL_TO="a@x.com,b@y.com"   # EMAIL_TO may be a list
#   printf '%s' '<smtp-app-password>' | gcloud secrets create smtp-pass --data-file=- --project "$PROJECT"
#   gcloud secrets add-iam-policy-binding smtp-pass \
#     --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor -q
# Env vars use a custom '##' delimiter so a comma-separated EMAIL_TO is preserved.
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT##GOOGLE_CLOUD_LOCATION=global##PAGES_REPO=$PAGES_REPO##DIGEST_BASE_URL=$DIGEST_BASE_URL"
SECRETS="PAGES_TOKEN=pages-token:latest"
if [[ -n "${SMTP_HOST:-}" ]]; then
  ENV_VARS="$ENV_VARS##SMTP_HOST=$SMTP_HOST##SMTP_PORT=${SMTP_PORT:-587}##SMTP_USER=$SMTP_USER##EMAIL_FROM=$EMAIL_FROM##EMAIL_TO=$EMAIL_TO"
  SECRETS="$SECRETS,SMTP_PASS=smtp-pass:latest"
  echo ">> SMTP email ENABLED (to: $EMAIL_TO)"
else
  echo ">> SMTP email disabled (export SMTP_HOST/... to enable); job will dry-run dispatch"
fi

echo ">> build + deploy the Cloud Run Job (Cloud Build builds the image)"
# Same image as the daily digest, but override the entrypoint so this job runs the
# weekly generative-media pipeline (run_job_media.sh) instead of run_job.sh.
gcloud run jobs deploy "$JOB" \
  --source . --region "$REGION" \
  --service-account "$SA" \
  --command bash --args run_job_media.sh \
  --set-env-vars "^##^$ENV_VARS" \
  --set-secrets "$SECRETS" \
  --max-retries 1 --task-timeout 1800 --memory 1Gi --cpu 1

echo ">> allow the SA to invoke the job, then schedule weekly '${SCHEDULE}' ${TIME_ZONE}"
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker -q
gcloud scheduler jobs create http "$SCHED" --location "$REGION" \
  --schedule "$SCHEDULE" --time-zone "$TIME_ZONE" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" \
  --http-method POST \
  --oauth-service-account-email "$SA" 2>/dev/null \
  || gcloud scheduler jobs update http "$SCHED" --location "$REGION" \
       --schedule "$SCHEDULE" --time-zone "$TIME_ZONE" \
       --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" \
       --http-method POST --oauth-service-account-email "$SA"

echo ">> done. Test now with:  gcloud run jobs execute $JOB --region $REGION --wait"
