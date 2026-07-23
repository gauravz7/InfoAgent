# DESIGN_SPEC.md — ArXiv AI-Agent Digest, agentified

## Overview
Convert the existing linear 6-stage digest pipeline (fetch → select → synthesize →
visuals → build → dispatch) into a **Google ADK** agent project, built and deployed
with `agents-cli`. Two entry shapes share one tool/stage layer:

1. **Interactive orchestrator** (`root_agent`) — a chat-driven `LlmAgent` whose
   tools are the pipeline stages. You can say *"digest the last 3 days, top 3, skip
   news, don't send"* and it runs the right stages and reports back.
2. **Ambient / scheduled runner** — a headless entrypoint (`python -m app.runner`)
   that runs the full digest end-to-end on a schedule with no user in the loop.

The digest has THREE section bands: ① Research (top arXiv AI-agent papers),
② News Coverage (grounded, clustered AI news), ③ Engineering (recent practical
"how we built it" AI-agent blog posts from top eng orgs — link-forward, no diagrams).

Deployment (chosen "simple" path): a **daily GitHub Actions cron** runs
`python -m app.runner`, then `publish.py` pushes the issue + RSS to the GitHub
Pages repo `gauravz7/gauravz7.github.io` under `/digest/`. No managed deployment
target (`deployment_target = "none"`); the interactive `app/agent.py` is kept for
playground/manual use.

## Architecture (ADK-native)
```
app/
  __init__.py        # exports root_agent + app; runs auth setup on import
  config.py          # folds current config.py (theme, models, budgets, SMTP, labs)
  agent.py           # root_agent (LlmAgent orchestrator) + App(name="app")
  tools.py           # each pipeline stage exposed as an ADK FunctionTool
  pipeline.py        # engine (fetch/select/synth/visuals/news/dispatch) — ported, ADK-clean
  render.py          # convert_md_to_full_html.py (Markdown → email HTML), ported
  runner.py          # ambient headless run(): full digest, for schedule/Job
DESIGN_SPEC.md
pyproject.toml       # [tool.agents-cli] + deps (google-genai, requests, pillow, ...)
deployment/          # Agent Runtime deploy config (scaffolded)
```

### Stage → tool mapping (interactive)
| Tool | Wraps | Returns |
|------|-------|---------|
| `fetch_papers(days, max_candidates)` | `fetch_candidates` | count + candidate titles/ids |
| `select_top_papers(top_n)` | `rank_and_select` | ranked shortlist w/ scores |
| `synthesize_paper(arxiv_id, words, verify)` | `synthesize_paper` (+grounded verify) | markdown brief |
| `render_visuals(...)` | `render_visuals` | diagram file list |
| `fetch_and_cluster_news(days, top_n)` | news fetch + cluster | top topics |
| `fetch_and_curate_blogs(days, top_n, words)` | blog fetch + rank + synth | curated eng-blog posts |
| `build_and_dispatch(send)` | build HTML (3 bands) + `dispatch` | paths / send status |

State (papers, briefs, diagrams) is carried in ADK session state between tool calls
so a conversation can build a digest incrementally.

## Tools Required / External APIs
- **arXiv Atom API** (no auth) — paper mining.
- **Google GenAI on Vertex (ADC)** — `gemini-3.1-flash-lite` (text + Google Search
  grounding) and `gemini-3.1-flash-lite-image` (diagrams). **Models unchanged.**
  Auth via ADC; Vertex project from env (`GOOGLE_CLOUD_PROJECT`), GenAI location
  `global`. Deployment/infra region: `us-central1`.
- **SMTP** (optional env) — live email; absent ⇒ dry-run `.html`/`.eml` to disk.

## Constraints & Safety Rules
- Word ban: `config.BANNED_WORDS` (empty by default — the "Anthropic" ban was lifted
  2026-07-22 so the Engineering Blogs track can name it as a source). The
  `scrub_banned` guard remains wired and applies any words still in the list.
- **Do not change the models** — reuse the project's proven flash-lite text/image
  models. The orchestrator `LlmAgent` model is chosen from the live model list.
- Grounded fact-check pass on paper Results (numbers verified via search) is kept.
- No secrets in source; SMTP + project via env / Secret Manager.
- `send=False` by default everywhere; live email only on explicit `--send` / tool arg.
- Preserve the visual theme (white bg, `#111111` text, `#E5318A` pink accent).

## Example Use Cases
- *Interactive:* "Find the top 3 AI-agent papers from the last week and email me the
  digest." → agent runs fetch→select→synthesize→visuals→news→build→dispatch(send=True).
- *Interactive (partial):* "Just show me the shortlist for the last 3 days, no email."
  → fetch + select only, returns ranked titles.
- *Ambient:* the daily GitHub Actions cron fires the run → full 3-band issue built
  and published to GitHub Pages (+ RSS).

## Success Criteria
- `agents-cli run "..."` produces a coherent multi-step run that writes an issue under
  `output/<date>/` and reports paths + cost.
- Ambient `runner.run(...)` builds the full 3-band issue (papers + news + blogs).
- Eval: ≥1 core case (tool trajectory: fetch→select→synthesize→build) passes an
  LLM-as-judge check that the digest was produced and respects the requested scope.

## Reference Samples
- `ambient-expense-agent` — schedule/trigger + FastAPI app pattern for the ambient run.
- `deep-search` — grounded, tool-driven research agent + citation handling patterns.

## Out of scope (this pass)
- CI/CD + Terraform (add later via `scaffold enhance`).
- Frontend UI. Publishing to Gemini Enterprise. Observability wiring.
