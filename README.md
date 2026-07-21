# ArXiv AI-Agent Research Digest — Autonomous Pipeline

An autonomous agent that mines fresh **AI-agent research** from arXiv, picks the
**top 3 papers** (favouring top labs), writes a concise, **image-forward** briefing
of each, then tracks the **top 3 recent AI-news topics** and assembles everything
into a single HTML digest (web page + email). Designed to run on a schedule and
publish a newsletter.

- **Auth:** Google ADC (Vertex) — no API keys in source.
- **Models:** `gemini-3.1-flash-lite` (text + grounded search) and
  `gemini-3.1-flash-lite-image` (diagrams).
- **Theme:** Cohere-style **pink `#E5318A` + black + white**.
- **Cost:** ~**$0.72/run** (measured; ~97% is image generation). Printed at the
  end of every run.

## Pipeline stages

```
Fetch ─▶ Select ─▶ Synthesize ─▶ Verify ─▶ Visuals ─▶ News ─▶ Build HTML ─▶ Dispatch
```

Four files:

| File | Responsibility |
|------|----------------|
| `config.py` | All tunables: categories, top-lab list, theme, model IDs, word budgets, image counts, news window, retry/backoff, pricing, banned-word guard, SMTP |
| `pipeline.py` | The engine — every stage + the shared GenAI client with 429/5xx failover |
| `convert_md_to_full_html.py` | Markdown→HTML: LaTeX→Unicode math, styled 💡/🔍/🏭 callouts, images interleaved, **Research vs News Coverage** subsections (also a standalone CLI) |
| `daily_arxiv_agent.py` | Orchestrator + CLI; runs the stages, writes `output/<YYYY-MM-DD>/`, prints per-run cost |

| Stage | What it does |
|-------|--------------|
| Fetch | Recent papers (`cs.AI, cs.CL, cs.MA, cs.SE`) from the arXiv Atom API, **last 7 days**, agent-relevance filtered |
| Select | LLM-assisted ranking on {top-lab likelihood, agent-relevance, measurable-impact} → top N |
| Synthesize | One capped call per paper (~`PAPER_WORDS`≈1000), image-forward; includes a **Results & Measurable Improvement** table (baseline→proposed, absolute+relative deltas) and a **Where This Could Be Applied** takeaway |
| Verify | Grounded second pass (`VERIFY_STATS`): fact-checks each paper's numbers via web search; corrects wrong values, marks unverifiable ones *(illustrative)* |
| Visuals | **5 diagrams/paper**, **1/news topic**; shared pink/black/white style; on-theme placeholder fallback |
| News | Grounded fetch (last 7 days) → rolling history → clusters over the last 7 days → top 3 topics. **Grounded synthesis that substantiates every claim with hard before→after numbers** (funding amounts, price/revenue/headcount changes). Real **citations** (title · source · date · link) |
| Build HTML | Assembles the digest with **① Research** and **② News Coverage** subsections + TOC |
| Dispatch | Writes the HTML + `.eml`; emails with inline images when SMTP is set, else dry-run to disk |

## Resilience

Every model call routes through a shared `_api()` wrapper with **429 / rate-limit /
5xx failover**: exponential backoff + jitter (up to `MAX_RETRIES`), honoring a
server `Retry-After`/`retryDelay` hint when present. Non-retryable errors (e.g.
400/permission) fail fast. Text, JSON, image, grounded-search, and verify calls
are all covered; image generation falls back to an on-theme placeholder only after
retries are exhausted.

## Requirements

- Python 3.10+
- `google-genai`, `requests`, `pillow` (no other third-party deps; arXiv parsed
  with stdlib, MD→HTML is custom)
- **Google ADC**: `gcloud auth application-default login` (locally) or a
  service-account key (in CI)

## Usage

```bash
python3 daily_arxiv_agent.py                 # full run, dry-run email
python3 daily_arxiv_agent.py --quick         # fast smoke run (short text, 1 img/report, no verify)
python3 daily_arxiv_agent.py --days 30 --top 3
python3 daily_arxiv_agent.py --no-images     # placeholders instead of generated diagrams
python3 daily_arxiv_agent.py --no-news       # papers only
python3 daily_arxiv_agent.py --send          # actually email (needs SMTP_* env vars)
```

### Live email
Set these env vars, then add `--send`:
```
SMTP_HOST  SMTP_PORT  SMTP_USER  SMTP_PASS  EMAIL_FROM  EMAIL_TO
```

## Configuration (`config.py`)

- **Content:** `CATEGORIES`, `WINDOW_DAYS`, `TOP_N`, `PAPER_WORDS`, `NEWS_WORDS`, `DIGEST_WORD_BUDGET`
- **Visuals:** `IMAGES_PER_PAPER=5`, `NEWS_IMAGES=1`, `THEME` (pink/black/white), `IMAGE_STYLE_PREFIX`
- **News:** `NEWS_TOP_N`, `NEWS_PER_RUN`, `NEWS_HISTORY_DAYS=7`
- **Quality:** `VERIFY_STATS`, `BANNED_WORDS`
- **Resilience:** `MAX_RETRIES`, `RETRY_BASE_DELAY`, `RETRY_MAX_DELAY`
- **Cost estimate:** `PRICE_TEXT_INPUT_PER_M`, `PRICE_TEXT_OUTPUT_PER_M`, `PRICE_PER_IMAGE`

## Output layout

```
output/
├── news_history.jsonl                      # rolling news store (dated per run)
└── 2026-07-21/
    ├── paper1_<slug>_deepdive.md
    ├── news1_<slug>_brief.md
    ├── images/                             # generated diagrams (interleaved)
    ├── top_arxiv_agent_paper_email.html    # final HTML digest
    └── digest.eml                          # email artifact (inline images)
```

## Scheduling

Twice a week (Mon & Thu), e.g. cron / GitHub Actions (UTC):

```
0 6 * * 1,4  cd /path/InfoAgent && python3 daily_arxiv_agent.py --send >> output/cron.log 2>&1
```

At ~$0.72/run, Mon/Thu ≈ **$6/month**. News clusters over the last 7 days, so
persistent stories rise to the top as history accumulates.

## Cost per run

Text is ~$0.02; the rest is images (18 × ~$0.039 ≈ $0.70). Lower it by reducing
`IMAGES_PER_PAPER`. Sending is separate (an ESP like MailerLite is free to 1,000
subscribers / 12k emails per month).
