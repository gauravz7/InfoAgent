# ArXiv AI-Agent Research Digest

An autonomous daily pipeline that mines fresh **AI-agent research** and assembles a
single, image-forward HTML digest published to the web. Each issue has three bands:

1. **Research** — the top arXiv AI-agent papers (last 7 days), each a ~1,000-word
   briefing with 5 generated diagrams, a results table, and grounded fact-checking.
2. **News Coverage** — the week's top AI-news topics, clustered over a rolling
   history, each substantiated with hard before→after numbers and real citations.
3. **Engineering Blogs** — recent practical "how we built it" AI-agent posts from
   top engineering orgs, LLM-ranked and link-forward.

It runs itself on GCP every day and publishes to **https://gauravz7.github.io/digest/**
(with an RSS feed and a homepage card), optionally emailing the issue over SMTP.

> ⚠️ **All digest content — every line of text and every image — is AI-generated
> and may contain mistakes.** Each published issue carries this disclaimer.

- **Auth:** Google ADC (Vertex) — no API keys or project ids in source.
- **Models:** `gemini-3.1-flash-lite` (text + grounded Google Search) and
  `gemini-3.1-flash-lite-image` (diagrams).
- **Cost:** ~**$0.73/run** (mostly image generation), printed at the end of every run.

## Architecture

```
Cloud Scheduler (daily 06:17 UTC)
  └─▶ Cloud Run Job "digest-job"  (runs as service account → native Vertex ADC)
        └─▶ run_job.sh
              ├─ python -m app.runner   # generate output/<date>/ (+ optional email)
              └─ python publish.py       # push issue + RSS + homepage card to Pages repo
```

The GitHub PAT for the push lives in Secret Manager (`pages-token`); the digest is
published to the separate Pages repo `gauravz7/gauravz7.github.io`.

## Code layout

| File | Responsibility |
|------|----------------|
| `app/config.py` | All tunables: categories, top-lab list, theme, model IDs, word/image budgets, news & blog windows, retry/backoff, pricing, SMTP |
| `app/pipeline.py` | The engine — every stage + the shared GenAI client with 429/5xx failover; arXiv fetch, ranking, synthesis, grounded verify, images, news, blogs, SMTP dispatch |
| `app/render.py` | Markdown→HTML: LaTeX→Unicode math, styled 💡/🔍/🏭 callouts, interleaved diagrams, the three section bands, and the AI-generated disclaimer (also a standalone CLI) |
| `app/runner.py` | Headless orchestrator/CLI: runs the stages, writes `output/<YYYY-MM-DD>/`, prints per-run cost |
| `publish.py` | Publishes an issue to the Pages repo: `/digest/<date>/`, rebuilds the archive index + RSS, and refreshes the homepage AI-Digest card |
| `Dockerfile`, `run_job.sh`, `deploy_cloudrun.sh` | Container + Cloud Run Job entrypoint + one-shot GCP deploy |

## Pipeline stages

```
Fetch ─▶ Select ─▶ Synthesize ─▶ Verify ─▶ Visuals ─▶ News ─▶ Blogs ─▶ Build HTML ─▶ Dispatch
```

Every model call routes through a shared `_api()` wrapper with **429 / rate-limit /
5xx failover** (exponential backoff + jitter, honoring `Retry-After`). The arXiv
Atom API has its own gentle retry policy and is non-fatal — on failure the pipeline
falls back to grounded-search "trending" papers. Image generation falls back to an
on-theme placeholder only after retries are exhausted.

## Run it locally

```bash
uv sync --no-dev                                   # install runtime deps
gcloud auth application-default login              # ADC for Vertex
export GOOGLE_CLOUD_PROJECT=<your-project> GOOGLE_CLOUD_LOCATION=global

uv run python -m app.runner --days 7 --top 3       # full run → output/<date>/
uv run python -m app.runner --quick --no-images    # fast smoke test
uv run python -m app.runner --send                 # also email (needs SMTP_* env)
```

Then optionally publish to a local checkout of the Pages repo:

```bash
uv run python publish.py --date $(date -u +%F) --site /path/to/gauravz7.github.io \
  --base-url https://gauravz7.github.io/digest
```

## Deploy the daily job (GCP)

One-time, from the repo root with gcloud authenticated (`gcloud config set project <id>`):

```bash
# store the GitHub PAT (write access to the Pages repo) once:
gh auth token | gcloud secrets create pages-token --data-file=-

bash deploy_cloudrun.sh          # SA + IAM, build image, deploy job, create daily scheduler
gcloud run jobs execute digest-job --region us-central1 --wait   # test now
```

`deploy_cloudrun.sh` is idempotent — re-run it to redeploy after code changes.

## Email

**Path A — SMTP (this repo):** export `SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS
EMAIL_FROM EMAIL_TO` and run with `--send`. `EMAIL_TO` may be a comma/semicolon list.
To enable it on the daily job, create an `smtp-pass` secret and export the SMTP vars
before running `deploy_cloudrun.sh` (see comments in that script). Without SMTP env,
`--send` is a harmless dry-run that writes `digest.eml`.

**Path B — newsletter (recommended for subscribers):** point an email service
(MailerLite, Buttondown, …) at the published feed `…/digest/rss.xml` to auto-send
each new issue to your subscriber list, and paste a signup form into the placeholder
in `publish.py`.

## Output layout

```
output/
├── news_history.jsonl / blog_history.jsonl   # rolling stores (dated per run)
└── 2026-07-23/
    ├── paper1_<slug>_deepdive.md
    ├── news1_<slug>_brief.md
    ├── images/                               # generated diagrams (interleaved)
    ├── top_arxiv_agent_paper_email.html      # final HTML digest
    └── digest.eml                            # email artifact (inline images)
```

## Requirements

- Python 3.11+
- `google-genai`, `requests`, `pillow` (no other third-party deps; arXiv parsed
  with stdlib, MD→HTML is custom)
- **Google ADC** for Vertex; the Cloud Run Job uses its attached service account.
