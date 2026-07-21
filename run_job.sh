#!/usr/bin/env bash
# Cloud Run Job entrypoint: generate the digest, publish it to the Pages repo.
#
# Expects:
#   GOOGLE_CLOUD_PROJECT   set on the job (Vertex project)
#   GOOGLE_CLOUD_LOCATION  e.g. "global"
#   PAGES_TOKEN            injected from Secret Manager (GitHub PAT, write to Pages repo)
#   PAGES_REPO            e.g. "gauravz7/gauravz7.github.io"
#   DIGEST_BASE_URL       e.g. "https://gauravz7.github.io/digest"
#
# Vertex/Gemini auth is native ADC from the attached service account (no key file).
set -euo pipefail

DATE="$(date -u +%F)"
PAGES_REPO="${PAGES_REPO:-gauravz7/gauravz7.github.io}"
DIGEST_BASE_URL="${DIGEST_BASE_URL:-https://gauravz7.github.io/digest}"

echo ">> generating issue for ${DATE}"
python3 daily_arxiv_agent.py --days 7 --top 3

echo ">> cloning Pages repo"
# token used only at runtime inside the container (never echoed)
git clone --depth 1 "https://x-access-token:${PAGES_TOKEN}@github.com/${PAGES_REPO}.git" /tmp/site

echo ">> publishing"
python3 publish.py --date "${DATE}" --site /tmp/site --base-url "${DIGEST_BASE_URL}"

echo ">> committing + pushing"
cd /tmp/site
git config user.name "digest-bot"
git config user.email "digest-bot@users.noreply.github.com"
git add digest
if git diff --cached --quiet; then
  echo "no changes to publish"
else
  git commit -m "digest: ${DATE}"
  git push origin HEAD
fi
echo ">> done"
