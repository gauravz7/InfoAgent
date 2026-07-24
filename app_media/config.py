"""Central configuration for the Generative Media Weekly Digest agent.

All tunables, theme tokens, model IDs and constants live here so the rest of the
package stays declarative. No secrets are stored here -- authentication is via
Google Application Default Credentials (ADC) and optional SMTP env vars.

This is the self-contained twin of ``app/config.py``: same infrastructure and
theme, but scoped to GENERATIVE MEDIA (image/video generation, image & video
editing, speech and music generation) and writing to its own ``output_media/``
root so it never collides with the daily AI-agent digest.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Google GenAI / Vertex auth (ADC-based, no API keys in source)
# --------------------------------------------------------------------------- #
# Resolve the Vertex project: explicit env wins, else fall back to ADC's project.
# No project id is hard-coded here — set GOOGLE_CLOUD_PROJECT (the Cloud Run Job
# and local dev both provide it, or ADC resolves it). GenAI text/image models run
# in the `global` location; the deployment/infra region is separate (us-central1).
GENAI_PROJECT = (
    os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
)
if not GENAI_PROJECT:
    try:
        import google.auth

        _, GENAI_PROJECT = google.auth.default()
    except Exception:  # noqa: BLE001 - keep import side effects non-fatal
        GENAI_PROJECT = None

GENAI_LOCATION = (
    os.environ.get("GOOGLE_CLOUD_LOCATION")
    or os.environ.get("CLOUD_ML_REGION")
    or "global"
)

# Make the resolved values visible to the ADK / google-genai clients.
if GENAI_PROJECT:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GENAI_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GENAI_LOCATION)
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

# Model the interactive orchestrator (root LlmAgent) reasons with. The pipeline's
# own text/image generation keeps its proven flash-lite models below, unchanged.
ORCHESTRATOR_MODEL = os.environ.get("DIGEST_ORCHESTRATOR_MODEL", "gemini-flash-latest")

# Verified-available models on this project (pipeline generation -- do NOT change).
TEXT_MODEL = os.environ.get("DIGEST_TEXT_MODEL", "gemini-3.1-flash-lite")
IMAGE_MODEL = os.environ.get("DIGEST_IMAGE_MODEL", "gemini-3.1-flash-lite-image")

# Approx pricing for the per-run cost estimate (USD). Flash-Lite tier + image
# model. Override via env to match your exact billing; these are ballpark rates.
PRICE_TEXT_INPUT_PER_M = float(os.environ.get("PRICE_TEXT_INPUT_PER_M", "0.10"))   # $/1M input tokens
PRICE_TEXT_OUTPUT_PER_M = float(os.environ.get("PRICE_TEXT_OUTPUT_PER_M", "0.40"))  # $/1M output tokens
PRICE_PER_IMAGE = float(os.environ.get("PRICE_PER_IMAGE", "0.039"))                # $/generated image

# --------------------------------------------------------------------------- #
# Topic framing  (generative media — threaded into the pipeline prompts)
# --------------------------------------------------------------------------- #
# One place to phrase "what this digest is about" so the copied pipeline prompts
# stay media-specific. Scope: image generation, video generation, image & video
# editing, speech generation, music generation.
DIGEST_KICKER = "Generative Media Weekly Digest"

# Short noun phrase for the subject area (used mid-sentence in prompts).
TOPIC_PAPER_FOCUS = (
    "GENERATIVE MEDIA (image generation, video generation, image and video "
    "editing, speech/audio generation, and music generation — e.g. diffusion "
    "models, text-to-image, text-to-video, image/video editing, text-to-speech, "
    "voice cloning, and music/audio synthesis)"
)
TOPIC_TRENDING_FAVOR = (
    "Favor work on GENERATIVE MEDIA — image generation, video generation, image "
    "and video editing, speech/audio generation and music generation (diffusion "
    "models, text-to-image, text-to-video, editing, TTS/voice, music synthesis) — "
    "but include any genuinely major generative-media paper."
)
TOPIC_NEWS_FOCUS = (
    "GENERATIVE MEDIA (image, video, audio/speech and music generation and "
    "editing — model launches, product releases, research breakthroughs, major "
    "funding/regulation, and creative-tooling moves)"
)
TOPIC_BLOG_FOCUS = (
    "BUILDING WITH GENERATIVE MEDIA — practical work with image/video generation "
    "and editing, speech/voice synthesis, and music/audio generation (pipelines, "
    "model fine-tuning, serving, prompting, evals, guardrails, production lessons)"
)

# Upcoming events track: what counts as a "big event" for the grounded search
# (phrased mid-sentence). Scoped to generative-media + creative-AI gatherings.
EVENT_TOP_N = 3
EVENT_FOCUS = (
    "GENERATIVE MEDIA and creative-AI (major conferences, summits and product "
    "launch events for image, video, audio/speech and music generation — e.g. "
    "SIGGRAPH, CVPR, ICCV, NAB Show, Adobe MAX, IBC, Google I/O and launch events "
    "from image/video/audio generative-AI labs)"
)

# --------------------------------------------------------------------------- #
# Paper mining
# --------------------------------------------------------------------------- #
ARXIV_API = "https://export.arxiv.org/api/query"
# Vision / graphics / multimedia / sound / audio-speech / image-&-video processing.
CATEGORIES = ["cs.CV", "cs.GR", "cs.MM", "cs.SD", "eess.AS", "eess.IV"]

WINDOW_DAYS = 7          # rolling lookback window (override via tool/CLI arg)
TOP_N = 3               # number of papers to feature (override via tool/CLI arg)
MAX_CANDIDATES = 180     # how many recent papers to pull before ranking
TRENDING_PER_RUN = 12    # grounded-search "top trending papers" merged into the pool

# --------------------------------------------------------------------------- #
# Recent generative-media news track  (grounded search -> history -> cluster)
# --------------------------------------------------------------------------- #
NEWS_TOP_N = 3               # number of clustered news TOPICS to feature
NEWS_PER_RUN = 12            # raw headlines pulled each run before clustering
NEWS_HISTORY_DAYS = 7        # cluster news accumulated over the last N days
NEWS_HISTORY_FILE = "news_history.jsonl"  # rolling store under OUTPUT_ROOT

# --------------------------------------------------------------------------- #
# Engineering blogs track  (grounded search -> rolling history -> pick top N)
# Recent PRACTICAL "how we built X" generative-media posts from top eng orgs.
# Longer lookback than news: good implementation write-ups trickle out.
# --------------------------------------------------------------------------- #
BLOG_TOP_N = 4               # number of engineering blog posts to feature
BLOG_PER_RUN = 12            # raw posts pulled each run before ranking
BLOG_HISTORY_DAYS = 45       # rank posts accumulated over the last N days
BLOG_HISTORY_FILE = "blog_history.jsonl"  # rolling store under OUTPUT_ROOT
BLOG_WORDS = 220             # ~words per blog briefing (link-forward, concise)
BLOG_IMAGES = 0              # blogs stay text/link-forward: no generated diagrams

# Engineering orgs whose blogs we mine for practical generative-media work.
# Grounded search is scoped to these plus "and more".
BLOG_SOURCES = [
    "OpenAI", "Google", "Google DeepMind", "Google Research", "Meta", "Meta AI",
    "Stability AI", "Runway", "Pika", "Luma AI", "Midjourney",
    "Black Forest Labs", "Adobe", "ElevenLabs", "Suno", "Udio", "NVIDIA",
    "ByteDance", "Kuaishou", "HeyGen", "Topaz Labs", "Hugging Face",
]

# Terms that flag a paper as being about generative media (cheap pre-filter).
MEDIA_KEYWORDS = [
    "diffusion", "text-to-image", "text to image", "text-to-video",
    "text to video", "image generation", "video generation", "image editing",
    "video editing", "inpainting", "outpainting", "super-resolution",
    "super resolution", "generative", "gan", "vae", "latent diffusion",
    "video synthesis", "image synthesis", "text-to-speech", "text to speech",
    "tts", "speech synthesis", "voice synthesis", "voice cloning",
    "voice conversion", "music generation", "audio generation",
    "sound generation", "talking head", "avatar", "style transfer",
    "neural rendering", "3d generation", "novel view", "vocoder", "singing",
    "lip sync", "lip-sync", "flow matching", "consistency model", "autoregressive",
]

# Prestigious labs/affiliations we boost when detectable (name-heuristic +
# LLM scorer). Ordered roughly by how strongly they signal a "top lab".
TOP_LABS = [
    "Google", "DeepMind", "Google DeepMind", "Google Research", "Google Brain",
    "OpenAI", "Meta", "Meta AI", "FAIR", "Microsoft", "Microsoft Research",
    "Stability AI", "Runway", "Adobe", "Adobe Research", "ElevenLabs",
    "Black Forest Labs", "Luma AI", "Midjourney", "Pika", "Kuaishou", "ByteDance",
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
# Repo-root output_media/ (one level up from this app_media/ package). SEPARATE
# from the daily digest's output/ so the two digests' rolling history and prior
# issues never collide.
OUTPUT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output_media"
)
FINAL_HTML_NAME = "top_generative_media_email.html"

# Words that must NEVER appear in any generated artifact. (Empty: no ban.)
BANNED_WORDS = []

# Lean, image-forward digest: keep the whole issue skimmable (~5000 words total).
# Papers stay visual-rich (5 diagrams each); text is concise so images carry the
# understanding. News is kept short and cheap on model calls.
DIGEST_WORD_BUDGET = 5000       # informational target for the whole issue
PAPER_WORDS = 1000             # ~words per academic-paper briefing (3 -> ~3000)
NEWS_WORDS = 450               # ~words per news-topic briefing   (3 -> ~1350)
QUICK_WORDS = 250              # relaxed target for quick smoke runs
VERIFY_STATS = True            # grounded second-pass fact-check of paper numbers

# --------------------------------------------------------------------------- #
# API resilience (429 / rate-limit / 5xx failover)
# --------------------------------------------------------------------------- #
MAX_RETRIES = 5                # attempts after the first try for retryable errors
RETRY_BASE_DELAY = 2.0         # seconds; exponential base (2,4,8,16,...)
RETRY_MAX_DELAY = 60.0         # cap per backoff wait

# arXiv Atom API specifically: fixed, gentle retry policy (the API throttles hard).
ARXIV_MAX_RETRIES = 3          # retry a throttled/failed page this many times
ARXIV_RETRY_DELAY = 5.0        # seconds to wait before each arXiv retry

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


def smtp_recipients() -> list:
    """EMAIL_TO parsed into a recipient list (comma / semicolon / space separated),
    so the digest can be sent to more than one address."""
    import re
    return [a.strip() for a in re.split(r"[,;\s]+", SMTP["to"] or "") if a.strip()]
