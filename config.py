"""Central configuration for the ArXiv AI-Agent Research Digest pipeline.

All tunables, theme tokens, model IDs and constants live here so the rest of the
pipeline stays declarative. No secrets are stored here -- authentication is via
Google Application Default Credentials (ADC) and optional SMTP env vars.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Google GenAI / Vertex configuration (ADC-based, no API keys in source)
# --------------------------------------------------------------------------- #
GENAI_PROJECT = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID") or os.environ.get(
    "GOOGLE_CLOUD_PROJECT", "vital-octagon-19612"
)
GENAI_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION") or os.environ.get(
    "CLOUD_ML_REGION", "global"
)

# Verified-available models on this project (see plan).
TEXT_MODEL = os.environ.get("DIGEST_TEXT_MODEL", "gemini-3.1-flash-lite")
IMAGE_MODEL = os.environ.get("DIGEST_IMAGE_MODEL", "gemini-3.1-flash-lite-image")

# Approx pricing for the per-run cost estimate (USD). Flash-Lite tier + image
# model. Override via env to match your exact billing; these are ballpark rates.
PRICE_TEXT_INPUT_PER_M = float(os.environ.get("PRICE_TEXT_INPUT_PER_M", "0.10"))   # $/1M input tokens
PRICE_TEXT_OUTPUT_PER_M = float(os.environ.get("PRICE_TEXT_OUTPUT_PER_M", "0.40"))  # $/1M output tokens
PRICE_PER_IMAGE = float(os.environ.get("PRICE_PER_IMAGE", "0.039"))                # $/generated image

# --------------------------------------------------------------------------- #
# Paper mining
# --------------------------------------------------------------------------- #
ARXIV_API = "https://export.arxiv.org/api/query"
CATEGORIES = ["cs.AI", "cs.CL", "cs.MA", "cs.SE"]

WINDOW_DAYS = 7          # rolling lookback window (override via --days)
TOP_N = 3               # number of papers to feature (override via --top)
MAX_CANDIDATES = 180     # how many recent papers to pull before ranking

# --------------------------------------------------------------------------- #
# Recent AI news track  (grounded search -> rolling history -> cluster top 3)
# --------------------------------------------------------------------------- #
NEWS_TOP_N = 3               # number of clustered news TOPICS to feature
NEWS_PER_RUN = 12            # raw headlines pulled each run before clustering
NEWS_HISTORY_DAYS = 7        # cluster news accumulated over the last N days
NEWS_HISTORY_FILE = "news_history.jsonl"  # rolling store under OUTPUT_ROOT

# Terms that flag a paper as being about AI agents (cheap pre-filter).
AGENT_KEYWORDS = [
    "agent", "agentic", "tool use", "tool-use", "multi-agent", "multiagent",
    "llm agent", "autonomous", "planning", "reasoning", "orchestration",
    "workflow", "function calling", "react", "self-refine", "self-correct",
    "memory", "environment", "reinforcement", "world model", "controller",
]

# Prestigious labs/affiliations we boost when detectable (name-heuristic +
# LLM scorer). Ordered roughly by how strongly they signal a "top lab".
TOP_LABS = [
    "Google", "DeepMind", "Google DeepMind", "Google Research", "Google Brain",
    "OpenAI", "Meta", "Meta AI", "FAIR", "Microsoft", "Microsoft Research",
    "MIT", "Stanford", "Berkeley", "UC Berkeley", "CMU", "Carnegie Mellon",
    "Princeton", "Oxford", "Cambridge", "ETH", "Tsinghua", "Allen Institute",
    "AI2", "NVIDIA", "Cohere", "Mistral", "Amazon", "AWS", "Apple", "IBM",
]

# --------------------------------------------------------------------------- #
# Visual theme  (Minimalist Plain White Background)
# --------------------------------------------------------------------------- #
# Cohere-style palette: pink accent on a black-and-white base.
THEME = {
    "bg": "#FFFFFF",          # white background
    "accent": "#E5318A",      # Cohere-style pink accent
    "accent_soft": "#F7C6DD",  # light pink for fills/borders
    "text": "#111111",        # near-black body text
    "text_soft": "#555555",   # secondary grey
    "rule": "#EAEAEA",        # hairline rules
    "code_bg": "#F6F6F6",     # near-white code background
}

IMAGES_PER_PAPER = 5  # keep rich visuals for academic articles (image-forward)
NEWS_IMAGES = 1       # news stays lean: one diagram, no planning LLM call

# Shared style prefix so every generated diagram looks like one consistent set.
IMAGE_STYLE_PREFIX = (
    "Minimalist technical infographic on a plain solid white (#FFFFFF) "
    "background. Clean flat vector style, generous white space, thin black "
    "(#111111) linework and labels, vivid pink (#E5318A) as the single accent "
    "color for emphasis. Strictly a black, white and pink palette. Editorial, "
    "professional, no photographic elements, no gradients, no drop shadows, no "
    "clutter. Crisp sans-serif labels. Concept: "
)

# --------------------------------------------------------------------------- #
# Output & guardrails
# --------------------------------------------------------------------------- #
OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
FINAL_HTML_NAME = "top_arxiv_agent_paper_email.html"

# Words that must NEVER appear in any generated artifact.
BANNED_WORDS = ["Anthropic"]

# Lean, image-forward digest: keep the whole issue skimmable (~5000 words total).
# Papers stay visual-rich (5 diagrams each); text is concise so images carry the
# understanding. News is kept short and cheap on model calls.
DIGEST_WORD_BUDGET = 5000       # informational target for the whole issue
PAPER_WORDS = 1000             # ~words per academic-paper briefing (3 -> ~3000)
NEWS_WORDS = 450               # ~words per news-topic briefing   (3 -> ~1350)
QUICK_WORDS = 250              # relaxed target for --quick smoke runs
VERIFY_STATS = True            # grounded second-pass fact-check of paper numbers

# --------------------------------------------------------------------------- #
# API resilience (429 / rate-limit / 5xx failover)
# --------------------------------------------------------------------------- #
MAX_RETRIES = 5                # attempts after the first try for retryable errors
RETRY_BASE_DELAY = 2.0         # seconds; exponential base (2,4,8,16,...)
RETRY_MAX_DELAY = 60.0         # cap per backoff wait

# --------------------------------------------------------------------------- #
# Email dispatch (all optional; absence => dry-run to disk)
# --------------------------------------------------------------------------- #
SMTP = {
    "host": os.environ.get("SMTP_HOST"),
    "port": int(os.environ.get("SMTP_PORT", "587")),
    "user": os.environ.get("SMTP_USER"),
    "password": os.environ.get("SMTP_PASS"),
    "to": os.environ.get("EMAIL_TO"),
    "sender": os.environ.get("EMAIL_FROM"),
}


def smtp_configured() -> bool:
    return all([SMTP["host"], SMTP["user"], SMTP["password"], SMTP["to"], SMTP["sender"]])
