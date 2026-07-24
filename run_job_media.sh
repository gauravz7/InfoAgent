#!/usr/bin/env bash
# Cloud Run Job entrypoint: generate the WEEKLY GENERATIVE MEDIA digest and publish
# it to the Pages repo (separate /media-digest/ section from the daily digest).
#
# Expects:
#   GOOGLE_CLOUD_PROJECT   set on the job (Vertex project)
#   GOOGLE_CLOUD_LOCATION  e.g. "global"
#   PAGES_TOKEN            injected from Secret Manager (GitHub PAT, write to Pages repo)
#   PAGES_REPO             e.g. "gauravz7/gauravz7.github.io"
#   DIGEST_BASE_URL        e.g. "https://gauravz7.github.io/media-digest"
#
# Vertex/Gemini auth is native ADC from the attached service account (no key file).
set -euo pipefail

# Stamp issues with the Singapore local date (the digest's audience/schedule tz).
# The 06:00 SGT run happens at 22:00 UTC the prior day, so `date -u` would label
# it a day behind — use Asia/Singapore instead.
DATE="$(TZ=Asia/Singapore date +%F)"
PAGES_REPO="${PAGES_REPO:-gauravz7/gauravz7.github.io}"
DIGEST_BASE_URL="${DIGEST_BASE_URL:-https://gauravz7.github.io/media-digest}"

echo ">> generating media issue for ${DATE}"
# --send emails the issue when SMTP_* env is present; without it, dispatch is a
# harmless dry-run (writes digest.eml, sends nothing).
python -m app_media.runner --days 7 --top 3 --send

echo ">> cloning Pages repo"
# Auth via GIT_ASKPASS so the token is NEVER placed in the remote URL (git would
# otherwise echo it verbatim into error output). Strip any stray whitespace/newline
# from the secret value defensively.
export PAGES_TOKEN="$(printf '%s' "${PAGES_TOKEN}" | tr -d '[:space:]')"
ASKPASS="$(mktemp)"
cat > "$ASKPASS" <<'EOS'
#!/usr/bin/env bash
case "$1" in
  Username*) printf '%s' "x-access-token" ;;
  Password*) printf '%s' "${PAGES_TOKEN}" ;;
esac
EOS
chmod +x "$ASKPASS"
export GIT_ASKPASS="$ASKPASS"
git clone --depth 1 "https://github.com/${PAGES_REPO}.git" /tmp/site

echo ">> publishing"
python publish_media.py --date "${DATE}" --site /tmp/site --base-url "${DIGEST_BASE_URL}"

echo ">> committing + pushing"
cd /tmp/site
git config user.name "digest-bot"
git config user.email "digest-bot@users.noreply.github.com"
# media-digest/ = the issues + index + RSS; index.html = the homepage media card
git add media-digest index.html
if git diff --cached --quiet; then
  echo "no changes to publish"
else
  git commit -m "media-digest: ${DATE}"
  git push origin HEAD
fi
echo ">> done"
