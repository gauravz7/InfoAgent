"""ArXiv AI-Agent digest engine -- all pipeline stages in one module.

Read top-to-bottom, this file is the six stages in order:

    GenAI client -> Fetch -> Select -> Synthesize -> Visuals -> News -> Dispatch

Auth is Google ADC (Vertex); models come from config. No API keys in source.
The orchestrator (daily_arxiv_agent.py) wires these together; HTML rendering
lives in convert_md_to_full_html.py.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import random
import re
import smtplib
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from typing import List

import requests
from google import genai
from google.genai import types

from . import config


# =========================================================================== #
# GenAI client (Vertex / ADC)
# =========================================================================== #
@lru_cache(maxsize=1)
def get_client() -> "genai.Client":
    return genai.Client(vertexai=True, project=config.GENAI_PROJECT,
                        location=config.GENAI_LOCATION)


# --- per-run usage/cost accounting ------------------------------------------ #
USAGE = {"text_in": 0, "text_out": 0, "text_calls": 0,
         "img_calls": 0, "img_out": 0, "search_calls": 0}


def reset_usage():
    for k in USAGE:
        USAGE[k] = 0


def _track(resp, *, image=False, search=False):
    um = getattr(resp, "usage_metadata", None)
    if um:
        if image:
            USAGE["img_out"] += getattr(um, "candidates_token_count", 0) or 0
        else:
            USAGE["text_in"] += getattr(um, "prompt_token_count", 0) or 0
            USAGE["text_out"] += getattr(um, "candidates_token_count", 0) or 0
    if image:
        USAGE["img_calls"] += 1
    else:
        USAGE["text_calls"] += 1
    if search:
        USAGE["search_calls"] += 1


def cost_report() -> dict:
    text_cost = (USAGE["text_in"] / 1e6 * config.PRICE_TEXT_INPUT_PER_M
                 + USAGE["text_out"] / 1e6 * config.PRICE_TEXT_OUTPUT_PER_M)
    img_cost = USAGE["img_calls"] * config.PRICE_PER_IMAGE
    return {**USAGE, "text_cost": text_cost, "img_cost": img_cost,
            "total_cost": text_cost + img_cost}


def _loads(text: str):
    """Parse JSON, tolerating code fences and surrounding prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise


# --- 429 / rate-limit / 5xx failover ---------------------------------------- #
_RETRYABLE_SIGNS = ("429", "resource_exhausted", "rate limit", "rate-limit",
                    "quota", "too many requests", "503", "unavailable", "500",
                    "internal error", "504", "deadline", "timeout", "overloaded",
                    "temporarily")


def _retryable(exc) -> bool:
    """True for transient errors worth retrying (429 rate limits, 5xx, timeouts)."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 500, 503, 504):
        return True
    s = str(exc).lower()
    return any(sig in s for sig in _RETRYABLE_SIGNS)


def _retry_after(exc):
    """Honor a server-provided Retry-After / retryDelay hint if present (seconds)."""
    m = re.search(r"retry.?(?:after|delay)\D*(\d+(?:\.\d+)?)", str(exc), re.I)
    return float(m.group(1)) if m else None


def _api(thunk, *, label="api", retries=None, image=False, search=False):
    """Run a GenAI call with 429/5xx-aware exponential backoff + jitter.

    `thunk` returns the response. Retryable errors back off and retry up to
    `retries` times; non-retryable errors (e.g. 400/permission) raise at once.
    Usage tracking happens here so every call site is accounted for exactly once.
    """
    retries = config.MAX_RETRIES if retries is None else retries
    last = None
    for attempt in range(retries + 1):
        try:
            resp = thunk()
            _track(resp, image=image, search=search)
            return resp
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= retries or not _retryable(exc):
                break
            hinted = _retry_after(exc)
            delay = hinted if hinted is not None else min(
                config.RETRY_MAX_DELAY, config.RETRY_BASE_DELAY * (2 ** attempt))
            delay += random.uniform(0, delay * 0.25)  # jitter to de-sync retries
            print(f"  [retry] {label}: {type(exc).__name__} "
                  f"(attempt {attempt + 1}/{retries}); backing off {delay:.1f}s")
            time.sleep(delay)
    raise RuntimeError(f"{label} failed after {retries} retries: {last}")


def generate_text(prompt: str, *, temperature: float = 0.7,
                  max_output_tokens: int = 8192, system: str = None,
                  retries: int = None, grounded: bool = False) -> str:
    tools = [types.Tool(google_search=types.GoogleSearch())] if grounded else None
    cfg = types.GenerateContentConfig(temperature=temperature,
                                      max_output_tokens=max_output_tokens,
                                      system_instruction=system, tools=tools)
    return _call(prompt, cfg, retries, parse=False, search=grounded)


def generate_json(prompt: str, *, temperature: float = 0.2,
                  max_output_tokens: int = 8192, retries: int = None):
    cfg = types.GenerateContentConfig(temperature=temperature,
                                      max_output_tokens=max_output_tokens,
                                      response_mime_type="application/json")
    return _call(prompt, cfg, retries, parse=True)


def _call(prompt, cfg, retries, parse, search=False):
    resp = _api(lambda: get_client().models.generate_content(
        model=config.TEXT_MODEL, contents=prompt, config=cfg),
        label="text", retries=retries, search=search)
    return _loads(resp.text or "") if parse else (resp.text or "").strip()


# =========================================================================== #
# Data models
# =========================================================================== #
@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: List[str]
    affiliations: List[str]
    abstract: str
    published: str
    updated: str
    pdf_url: str
    abs_url: str
    primary_category: str
    comment: str = ""
    score: float = 0.0
    lab_score: float = 0.0
    impact_score: float = 0.0
    relevance_score: float = 0.0
    rationale: str = ""
    detected_labs: List[str] = field(default_factory=list)

    @property
    def author_line(self) -> str:
        if len(self.authors) <= 6:
            return ", ".join(self.authors)
        return ", ".join(self.authors[:6]) + f", +{len(self.authors) - 6} more"


@dataclass
class NewsTopic:
    """A clustered news storyline, shaped to reuse the paper synth/visual path."""
    topic: str
    summary: str
    headlines: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    citations: List[dict] = field(default_factory=list)  # [{title,url,date,source}]
    run_span: int = 1
    salience: float = 0.0
    # paper-compatible fields consumed by visuals/rendering
    title: str = ""
    author_line: str = ""
    detected_labs: List[str] = field(default_factory=list)


@dataclass
class BlogPost:
    """A practical engineering-blog post, shaped to reuse the synth/render path."""
    org: str
    url: str
    summary: str = ""
    source: str = ""            # publication domain
    date: str = ""             # YYYY-MM-DD
    run_id: str = ""
    why: str = ""              # one-line "why it matters" from the ranker
    salience: float = 0.0
    # paper-compatible fields consumed by rendering
    title: str = ""
    author_line: str = ""
    detected_labs: List[str] = field(default_factory=list)


# =========================================================================== #
# Stage 1 -- Fetch (arXiv Atom API, stdlib parser)
# =========================================================================== #
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _parse_entry(entry: ET.Element) -> Paper:
    raw_id = _clean(entry.findtext(f"{_ATOM}id", ""))
    arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id.rsplit("/", 1)[-1]

    authors, affils = [], []
    for a in entry.findall(f"{_ATOM}author"):
        name = _clean(a.findtext(f"{_ATOM}name", ""))
        if name:
            authors.append(name)
        aff = a.findtext(f"{_ARXIV}affiliation")
        if aff:
            affils.append(_clean(aff))

    pdf_url, abs_url = "", raw_id
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
        elif link.get("rel") == "alternate":
            abs_url = link.get("href", abs_url)
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    pc = entry.find(f"{_ARXIV}primary_category")
    return Paper(
        arxiv_id=arxiv_id,
        title=_clean(entry.findtext(f"{_ATOM}title", "")),
        authors=authors, affiliations=affils,
        abstract=_clean(entry.findtext(f"{_ATOM}summary", "")),
        published=_clean(entry.findtext(f"{_ATOM}published", ""))[:10],
        updated=_clean(entry.findtext(f"{_ATOM}updated", ""))[:10],
        pdf_url=pdf_url, abs_url=abs_url,
        primary_category=pc.get("term", "") if pc is not None else "",
        comment=_clean(entry.findtext(f"{_ARXIV}comment", "")),
    )


def _fetch_page(search_query: str, start: int, page_size: int) -> List[Paper]:
    params = {"search_query": search_query, "start": start, "max_results": page_size,
              "sortBy": "submittedDate", "sortOrder": "descending"}
    url = f"{config.ARXIV_API}?{urllib.parse.urlencode(params)}"
    # Per the arXiv API user manual: identify the client, keep page_size <= 2000,
    # and on 429/503 back off (honoring Retry-After) before retrying.
    headers = {"User-Agent": "arxiv-agent-digest/1.0 (+https://github.com/gauravz7/InfoAgent)"}
    # Fixed, gentle policy: try once, then up to ARXIV_MAX_RETRIES more attempts,
    # waiting ARXIV_RETRY_DELAY seconds before each retry (honoring Retry-After
    # when the server sends a longer hint).
    last_exc = None
    for attempt in range(config.ARXIV_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=60, headers=headers)
            if resp.status_code in (429, 503):
                last_exc = requests.HTTPError(f"{resp.status_code} rate-limited by arXiv")
                if attempt < config.ARXIV_MAX_RETRIES:
                    ra = resp.headers.get("Retry-After", "")
                    wait = max(float(ra), config.ARXIV_RETRY_DELAY) if ra.isdigit() else config.ARXIV_RETRY_DELAY
                    time.sleep(wait)
                continue
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            return [_parse_entry(e) for e in root.findall(f"{_ATOM}entry")]
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < config.ARXIV_MAX_RETRIES:
                time.sleep(config.ARXIV_RETRY_DELAY)
    raise last_exc or requests.HTTPError("arXiv fetch failed")


def is_agent_relevant(paper: Paper) -> bool:
    hay = (paper.title + " " + paper.abstract).lower()
    return any(kw in hay for kw in config.AGENT_KEYWORDS)


def _norm_title(t: str) -> str:
    """Normalize a title for fuzzy de-duplication across sources."""
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def fetch_trending_papers(days: int = None, n: int = None, verbose: bool = True) -> List[Paper]:
    """Grounded-search the most TALKED-ABOUT recent AI research papers.

    Complements the arXiv category mining with papers trending over the last N
    days (social buzz, press, leaderboards). Uses Google Search grounding, NOT the
    arXiv Atom API, so the candidate pool stays populated even when arXiv is
    rate-limiting or down. Results are shaped into ``Paper`` objects (the grounded
    summary serves as the abstract; the later grounded verify pass enriches it).
    """
    n = n or config.TRENDING_PER_RUN
    days = days or config.WINDOW_DAYS
    tool = types.Tool(google_search=types.GoogleSearch())
    prompt = (
        f"Search the web for the {n} most TRENDING / most-discussed ARTIFICIAL "
        f"INTELLIGENCE research papers from the LAST {days} DAYS — papers getting "
        "significant attention on X/Twitter, Hacker News, Reddit r/MachineLearning, "
        "AI newsletters and press. Favor work on AI AGENTS, LLMs, reasoning, tool "
        "use and multi-agent systems, but include any genuinely major AI paper. "
        "Prefer papers that have an arXiv ID. For each give: the exact title; the "
        "arXiv id (e.g. 2506.12345) if one exists, else an empty string; up to 8 "
        "author names; the primary arXiv category if known (e.g. cs.AI); the "
        "publication DATE in YYYY-MM-DD; a 2-4 sentence factual ABSTRACT of what "
        "the paper does and its headline result; and a one-line reason it is "
        "trending.\n\n"
        'Return STRICT JSON: array of {"title":str,"arxiv_id":str,"authors":[str],'
        '"category":str,"date":str,"abstract":str,"why":str}.'
    )
    try:
        resp = _api(lambda: get_client().models.generate_content(
            model=config.TEXT_MODEL, contents=prompt,
            config=types.GenerateContentConfig(tools=[tool], temperature=0.3)),
            label="trending-search", search=True)
        items = _loads(resp.text or "")
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [trending] grounded fetch failed ({exc})")
        items = []

    papers = []
    for it in (items or []):
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        aid = re.sub(r"^arxiv:\s*", "", str(it.get("arxiv_id", "")).strip(),
                     flags=re.IGNORECASE).strip()
        # keep only a plausible arXiv id (e.g. 2506.12345); drop junk
        if aid and not re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", aid):
            aid = ""
        abs_url = f"https://arxiv.org/abs/{aid}" if aid else ""
        pdf_url = f"https://arxiv.org/pdf/{aid}" if aid else ""
        authors = [str(a).strip() for a in (it.get("authors") or []) if str(a).strip()]
        date = str(it.get("date", "")).strip()[:10]
        papers.append(Paper(
            arxiv_id=aid or f"trending:{_norm_title(it['title'])[:48]}",
            title=str(it.get("title", "")).strip(),
            authors=authors, affiliations=[],
            abstract=str(it.get("abstract", "")).strip(),
            published=date, updated=date,
            pdf_url=pdf_url, abs_url=abs_url,
            primary_category=str(it.get("category", "")).strip() or "cs.AI",
            comment=("trending: " + str(it.get("why", "")).strip())[:300],
        ))
    if verbose:
        n_id = sum(1 for p in papers if not p.arxiv_id.startswith("trending:"))
        print(f"  [trending] fetched {len(papers)} trending papers "
              f"({n_id} with arXiv IDs)")
    return papers


def fetch_candidates(days: int = None, max_candidates: int = None,
                     include_trending: bool = True, verbose: bool = True) -> List[Paper]:
    """Recent, de-duplicated candidate papers within the lookback window.

    Two complementary sources, merged and de-duplicated (by arXiv id, then title):
      1. arXiv Atom API category mining (cs.AI/CL/MA/SE), filtered to agent-relevant.
      2. (``include_trending``) grounded-search "top trending papers" of the last N
         days. The arXiv fetch is best-effort: if the API errors out (e.g. a 429
         rate-limit) the run continues on the trending source alone rather than
         aborting.
    """
    days = days or config.WINDOW_DAYS
    max_candidates = max_candidates or config.MAX_CANDIDATES
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    search_query = " OR ".join(f"cat:{c}" for c in config.CATEGORIES)

    collected: dict[str, Paper] = {}
    page_size, start = 100, 0
    while len(collected) < max_candidates and start < 2000:
        try:
            page = _fetch_page(search_query, start, page_size)
        except Exception as exc:  # noqa: BLE001 - retries exhausted; fall back to trending
            if verbose:
                print(f"  [fetch] arXiv unavailable after retries ({exc}); "
                      "continuing with trending papers only")
            break
        if not page:
            break

        all_old = True
        for p in page:
            try:
                pd = _dt.date.fromisoformat(p.published or p.updated)
            except ValueError:
                pd = _dt.date.today()
            if pd >= cutoff:
                all_old = False
                if p.arxiv_id not in collected and is_agent_relevant(p):
                    collected[p.arxiv_id] = p
        start += page_size
        if all_old:  # whole page older than cutoff -> stop paging
            break
        time.sleep(3.0)  # arXiv manual: keep >=3s between consecutive calls

    if verbose:
        print(f"  [fetch] {len(collected)} agent-relevant arXiv papers since "
              f"{cutoff.isoformat()} across {', '.join(config.CATEGORIES)}")

    # Merge in grounded "trending" papers (dedupe by arXiv id, then by title).
    if include_trending:
        seen_titles = {_norm_title(p.title) for p in collected.values()}
        added = 0
        for p in fetch_trending_papers(days=days, verbose=verbose):
            if p.arxiv_id in collected:
                continue
            nt = _norm_title(p.title)
            if nt and nt in seen_titles:
                continue
            collected[p.arxiv_id] = p
            seen_titles.add(nt)
            added += 1
        if verbose:
            print(f"  [fetch] +{added} trending papers merged "
                  f"-> {len(collected)} total candidates")

    return list(collected.values())


# =========================================================================== #
# Stage 2 -- Select (LLM-assisted ranking; arXiv rarely exposes affiliations)
# =========================================================================== #
def _heuristic_lab_hits(paper: Paper) -> List[str]:
    hay = " ".join([paper.title, paper.abstract, paper.comment] + paper.affiliations).lower()
    return sorted({lab for lab in config.TOP_LABS if lab.lower() in hay})


def _score_batch(batch: List[Paper]) -> dict:
    items = [{"i": i, "title": p.title, "authors": p.author_line,
              "abstract": p.abstract[:1200]} for i, p in enumerate(batch)]
    prompt = (
        "You are a senior AI research editor curating a daily digest of the most "
        "important new papers on AI AGENTS (LLM agents, tool use, multi-agent "
        "systems, autonomous reasoning/planning).\n\n"
        "For EACH paper, infer likely affiliation from names/phrasing/abstract and "
        "score 0-10 on three axes:\n"
        "  lab       = likelihood it is from a top-tier lab (Google, DeepMind, "
        "OpenAI, Meta/FAIR, Microsoft Research, MIT, Stanford, Berkeley, CMU, "
        "Princeton, NVIDIA, AI2, ...).\n"
        "  relevance = how squarely it is about AI AGENTS.\n"
        "  impact    = strength of concrete, MEASURABLE results/benchmarks.\n\n"
        'Return STRICT JSON: array of {"i":int,"lab":float,"relevance":float,'
        '"impact":float,"labs":[strings],"rationale":"one sentence"}.\n\n'
        f"PAPERS:\n{json.dumps(items, ensure_ascii=False)}"
    )
    scored = generate_json(prompt, max_output_tokens=4096)
    return {int(o["i"]): o for o in scored}


def rank_and_select(papers: List[Paper], top_n: int = None, verbose: bool = True) -> List[Paper]:
    top_n = top_n or config.TOP_N
    if not papers:
        return []
    for i in range(0, len(papers), 20):
        batch = papers[i:i + 20]
        try:
            scores = _score_batch(batch)
        except Exception as exc:  # noqa: BLE001 - heuristic-only fallback
            if verbose:
                print(f"  [select] LLM scoring failed ({exc}); using heuristics")
            scores = {}
        for idx, p in enumerate(batch):
            s = scores.get(idx, {})
            p.lab_score = float(s.get("lab", 0.0))
            p.relevance_score = float(s.get("relevance", 0.0))
            p.impact_score = float(s.get("impact", 0.0))
            p.rationale = s.get("rationale", "")
            p.detected_labs = sorted(set(_heuristic_lab_hits(p)) | set(s.get("labs", []) or []))
            bonus = 1.5 if _heuristic_lab_hits(p) else 0.0
            p.score = 1.6 * p.relevance_score + 1.3 * p.lab_score + 1.1 * p.impact_score + bonus

    top = sorted(papers, key=lambda p: p.score, reverse=True)[:top_n]
    if verbose:
        print(f"  [select] top {len(top)} of {len(papers)}:")
        for i, p in enumerate(top, 1):
            print(f"    {i}. {p.title}")
            print(f"       score={p.score:.1f} (rel={p.relevance_score:.0f} "
                  f"lab={p.lab_score:.0f} impact={p.impact_score:.0f}) "
                  f"labs=[{', '.join(p.detected_labs) or 'n/a'}]")
    return top


# =========================================================================== #
# Stage 3 -- Synthesize (>5000-word deep-dive; section-wise length enforcement)
# =========================================================================== #
_SYSTEM = (
    "You are a world-class technical science communicator and applied-AI "
    "researcher. You write rigorous yet accessible deep-dives that a smart "
    "practitioner can act on. You explain hard math in plain language, always "
    "pairing every theorem or formula with a 💡 intuition and a 🔍 concrete "
    "worked example. You use clean Markdown."
)

_PAPER_SPEC = """Write a focused, well-structured Markdown briefing of the paper below — aim for
approximately {words} words (hard cap {cap}). This is an image-forward digest, so
be substantive but well-organized: short paragraphs and bullets, and let the
diagrams carry visual detail.

STRICT STRUCTURE (use these exact headings/emoji markers):

# <a punchy headline that leads with the paper's most MEASURABLE result — cite a concrete number>

## 1. What It Is & Why It Matters
- 1-2 short paragraphs, then 3-4 bullets (problem, core idea, headline result).
- End with a one-line 🏭 industry angle: who benefits + a concrete example.

## 2. How It Works
- 5-7 crisp bullets on architecture / method / key components. One short
  code or pseudocode snippet if it aids understanding.

## 3. Core Idea & Key Numbers
- The central mechanism in 2-4 sentences. If there is a formula, show it in clean
  Unicode (e.g. β, ∇, Σ, argmax) and add ONE line each:
  > 💡 **Intuition:** ...(plain-language meaning)...
  > 🔍 **Example:** ...(a tiny worked/numeric example)...

## 4. Analogy
- 1-2 vivid analogies (3-4 sentences) that make the mechanism click.

## 5. Results & Measurable Improvement
- REQUIRED: cite SPECIFIC numbers from the paper that prove improvement on a named
  metric. For each comparison give the metric + dataset/benchmark, the BASELINE
  value, the PROPOSED value, and the absolute AND relative delta — e.g.
  "accuracy 71.2% → 85.4% (+14.2 pts, +19.9% relative)". Provide 3-5 such quantified
  comparisons as a table or bullets, and note statistical significance/variance if
  the paper reports it.

## 6. Key Takeaways
- 3 crisp bullets: for industry, for researchers, for practitioners.

## 7. Where This Could Be Applied
- A concrete closing takeaway: 3-4 real deployment settings where this technique
  would pay off (name the domain, the workflow it slots into, and the expected
  benefit). End with a one-sentence bottom line naming the single most promising
  application.

RULES: Be specific and grounded; prefer concrete numbers over vague claims. If a
figure is not stated in the abstract, give a realistic value and clearly mark it as
*illustrative*. Clean Markdown only, no outer code fence.
"""

_NEWS_SPEC = """Write a SHORT Markdown briefing of the recent AI-NEWS topic below (a clustered
storyline from several headlines) — about {words} words TOTAL (hard cap {cap}).
Tight, factual and skimmable.

STRICT STRUCTURE (exact headings/emoji markers):

# <a punchy headline leading with the most concrete / measurable fact of the story>

## 1. What Happened & Why It Matters
- One short paragraph, then 2-3 bullets. End with a one-line 🏭 industry angle.

## 2. Key Details & Timeline (with numbers)
- 3-5 bullets. EVERY quantitative claim MUST carry its specific figure(s).

## 3. By the Numbers
- A short bullet list or table of the concrete facts. For ANY change, give the
  BEFORE and AFTER values plus the delta, and attach dates. Examples of the
  required specificity:
  - Price cut → "$499 → $399 (−$100, −20%), effective 2026-07-15"
  - Revenue → "$2.1B → $1.7B (−19% YoY, Q2 2026)"
  - Layoffs → "headcount 12,000 → 9,500 (−2,500, −21%)"
  - FUNDING RAISED → state the EXACT amount and terms: "raised $2.0B at a $12B
    post-money valuation (Series C, led by <investor>)".

## 4. Impact & What to Watch
- 3-4 bullets: implications for the field, plus 2 things to watch next.

RULES: SUBSTANTIATE EVERYTHING WITH FACTS — never a vague quantitative statement.
If prices were slashed, give old → new. If revenue declined, give prior → current
and the % change. If funding was raised, state EXACTLY how much (and valuation/
round/lead investor if available). Use web search to find the precise numbers; if
a figure genuinely cannot be found, write "figure not disclosed" rather than being
vague. Do NOT exceed the word cap. Clean Markdown only, no outer code fence.
"""

_BLOG_SPEC = """Write a SHORT Markdown briefing of the ENGINEERING BLOG post below — a recent
practical "how we built it" write-up on AI agents from a top engineering org.
About {words} words TOTAL (hard cap {cap}). Tight, concrete and skimmable, aimed
at an engineer who wants to know whether to read the full post.

STRICT STRUCTURE (exact headings):

# <a punchy headline naming the concrete thing they built or the technique>

## 1. What They Built & Why
- One short paragraph: the problem, and what they shipped to solve it. Name the
  org and the concrete system/pattern.

## 2. How It Works (the practical bits)
- 3-5 bullets on the actual implementation: architecture, tools, models, prompt
  or orchestration patterns, evals, guardrails. Prefer specifics over generalities.

## 3. Takeaways You Can Apply
- 2-3 bullets an engineer could reuse in their own agent system.

RULES: Ground everything in the post; use web search to confirm the specifics. Do
NOT invent numbers the post does not report. Stay under the word cap. Clean
Markdown only, no outer code fence.
"""


def scrub_banned(text: str) -> str:
    """Remove banned words from visible prose, but PRESERVE URLs so citation links
    keep working. Handles 'Anthropic', "Anthropic's", and 'anthropic.com' cleanly.
    """
    # Mask link URLs (href values + bare links) so scrubbing never breaks them.
    protected = []

    def _mask(m):
        protected.append(m.group(0))
        return f"\x00U{len(protected) - 1}\x00"

    text = re.sub(r'href="[^"]*"', _mask, text)
    text = re.sub(r"https?://\S+", _mask, text)

    for w in config.BANNED_WORDS:
        text = re.sub(rf"\b{re.escape(w)}(?:\.[a-z]{{2,}})?(?:'s)?\b", "", text,
                      flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text)          # collapse runs of spaces
    text = re.sub(r"(,\s*){2,}", ", ", text)         # ", , " -> ", "
    text = re.sub(r"([(\[]\s*),\s*", r"\1", text)     # "( , " -> "( "
    text = re.sub(r",\s*([)\]<])", r"\1", text)       # " , )" / " , <" -> ")"/"<"

    for i, p in enumerate(protected):               # restore URLs verbatim
        text = text.replace(f"\x00U{i}\x00", p)
    return text


def _word_count(md: str) -> int:
    return len(re.findall(r"\b\w+\b", md))


def _paper_brief(p: Paper) -> str:
    return (
        f"TITLE: {p.title}\nAUTHORS: {p.author_line}\n"
        f"LIKELY LABS: {', '.join(p.detected_labs) or 'unknown'}\n"
        f"ARXIV ID: {p.arxiv_id}\nPRIMARY CATEGORY: {p.primary_category}\n"
        f"PUBLISHED: {p.published}\nCOMMENT: {p.comment or 'n/a'}\n"
        f"ABSTRACT:\n{p.abstract}\n"
    )


def _topic_brief(t: NewsTopic) -> str:
    return (
        f"TOPIC: {t.topic}\nSOURCES: {t.author_line}\n"
        f"APPEARED ACROSS {t.run_span} PIPELINE RUN(S); SALIENCE {t.salience:.1f}/10\n"
        f"SYNTHESIZED SUMMARY:\n{t.summary}\n\n"
        f"CONTRIBUTING HEADLINES:\n- " + "\n- ".join(t.headlines) + "\n"
    )


def _blog_brief(b: BlogPost) -> str:
    return (
        f"POST TITLE: {b.title}\nORG: {b.org}\nSOURCE: {b.source}\n"
        f"PUBLISHED: {b.date or 'n/a'}\nURL: {b.url}\n"
        f"WHY IT MATTERS: {b.why or 'n/a'}\n"
        f"SUMMARY:\n{b.summary}\n"
    )


def _hard_trim(md: str, cap: int) -> str:
    """Safety net: keep the doc under `cap` words, cutting at a paragraph break."""
    if _word_count(md) <= cap:
        return md
    out, total = [], 0
    for para in md.split("\n\n"):
        w = _word_count(para)
        if total + w > cap and out:
            break
        out.append(para)
        total += w
    return "\n\n".join(out)


def _synthesize(spec: str, brief: str, kind: str, label: str,
                words: int, verbose: bool, floor: int = None,
                grounded: bool = False) -> str:
    """A capped generation call -> an on-budget briefing.

    If `floor` is set and the first draft comes back short, do ONE top-up pass to
    reach roughly `words` (keeps papers near their target without a heavy loop).
    `grounded=True` lets the model web-search for exact figures (used for news).
    """
    cap = int(words * 1.6)
    prompt = f"{spec.format(words=words, cap=cap)}\n\n{kind}:\n{brief}"
    md = generate_text(prompt, system=_SYSTEM, temperature=0.6,
                       max_output_tokens=4096, grounded=grounded)
    md = scrub_banned(md)
    if floor and _word_count(md) < floor:
        topup = (
            f"The briefing below is shorter than desired. EXPAND it to about {words} "
            "words, keeping the EXACT same section headings and structure. Add more "
            "specific numbers in '5. Results & Measurable Improvement' (baseline → "
            "proposed with absolute and relative deltas), richer 'How It Works' "
            "detail, and a fuller 'Where This Could Be Applied'. Return the COMPLETE "
            f"updated Markdown, no outer fence.\n\nCURRENT:\n{md}"
        )
        md = scrub_banned(generate_text(topup, system=_SYSTEM, temperature=0.6,
                                        max_output_tokens=4096))
    md = _hard_trim(md, cap)
    if verbose:
        print(f"  [synthesize] {label[:56]} -> {_word_count(md)} words (target ~{words})")
    return md


def verify_paper_stats(paper: Paper, md: str, verbose: bool = True) -> str:
    """Grounded second pass: fact-check the numbers in the Results section against
    the actual paper (web search + abstract), correcting or flagging as needed."""
    m = re.search(r"(##\s*5\..*?)(?=\n##\s|\Z)", md, re.S)
    if not m:
        return md
    section = m.group(1).strip()
    tool = types.Tool(google_search=types.GoogleSearch())
    prompt = (
        "You are a meticulous fact-checker. Verify EVERY quantitative claim in the "
        "RESULTS section below against the ACTUAL paper. Use web search to find the "
        f"paper (arXiv:{paper.arxiv_id}, \"{paper.title}\") and its reported metrics, "
        "and also use the abstract provided. For each number: if the paper supports "
        "it, keep it; if the paper reports a DIFFERENT value, replace it with the "
        "correct one; if it cannot be verified from the paper or abstract, keep a "
        "reasonable figure but append ' *(illustrative)*' right after it. Do not "
        "invent precision the source lacks. Preserve the EXACT heading and the "
        "table/bullet format. Return ONLY the corrected section markdown, no fence.\n\n"
        f"ABSTRACT:\n{paper.abstract}\n\nRESULTS SECTION TO VERIFY:\n{section}"
    )
    try:
        resp = _api(lambda: get_client().models.generate_content(
            model=config.TEXT_MODEL, contents=prompt,
            config=types.GenerateContentConfig(tools=[tool], temperature=0.1)),
            label="verify", search=True)
        out = (resp.text or "").strip()
        if out.startswith("```"):
            out = re.sub(r"^```[a-zA-Z]*\n?", "", out)
            out = re.sub(r"\n?```$", "", out).strip()
        if out.startswith("## 5") and len(out) > 80:
            md = md[:m.start()] + out + md[m.end():]
            if verbose:
                n_illus = out.count("*(illustrative)*")
                print(f"  [verify] {paper.title[:48]}: results fact-checked "
                      f"(grounded; {n_illus} marked illustrative)")
    except Exception as exc:  # noqa: BLE001 - keep unverified section on failure
        if verbose:
            print(f"  [verify] skipped ({exc})")
    return scrub_banned(md)


def synthesize_paper(paper: Paper, words: int = None, verbose: bool = True,
                     verify: bool = None) -> str:
    words = words or config.PAPER_WORDS
    md = _synthesize(_PAPER_SPEC, _paper_brief(paper), "PAPER", paper.title,
                     words, verbose, floor=int(words * 0.9))
    if config.VERIFY_STATS if verify is None else verify:
        md = verify_paper_stats(paper, md, verbose=verbose)
    return md


def synthesize_news_topic(topic: NewsTopic, words: int = None, verbose: bool = True) -> str:
    return _synthesize(_NEWS_SPEC, _topic_brief(topic), "TOPIC", topic.topic,
                       words or config.NEWS_WORDS, verbose, grounded=True)


def synthesize_blog(blog: BlogPost, words: int = None, verbose: bool = True) -> str:
    return _synthesize(_BLOG_SPEC, _blog_brief(blog), "BLOG POST", blog.title,
                       words or config.BLOG_WORDS, verbose, grounded=True)


# =========================================================================== #
# Stage 4 -- Visuals (Gemini image model; shared plain-white/terracotta style)
# =========================================================================== #
DIAGRAM_SLOTS = [
    ("summary", "a clean conceptual overview / hero diagram of the central idea"),
    ("architecture", "a system architecture / pipeline flow diagram of the method"),
    ("method", "a diagram illustrating the core mechanism, math or algorithm"),
    ("analogy", "a diagram visualizing the main analogy or mental model"),
    ("results", "an infographic summarizing the measurable results and impact"),
]
_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _placeholder(path: str, label: str) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1024, 640), config.THEME["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, 1000, 616], outline=config.THEME["accent"], width=3)
    d.rectangle([60, 80, 320, 96], fill=config.THEME["accent"])
    d.rectangle([60, 130, 520, 142], fill=config.THEME["text_soft"])
    d.text((60, 260), (label[:60] + "…") if len(label) > 60 else label, fill=config.THEME["text"])
    d.text((60, 570), "Visual Theme: Minimalist Plain White Background (#FFFFFF)",
           fill=config.THEME["accent"])
    img.save(path)


def _plan_diagrams(title: str, report_md: str, n: int, verbose: bool) -> List[dict]:
    slots = DIAGRAM_SLOTS[:max(1, min(n, len(DIAGRAM_SLOTS)))]
    prompt = (
        "You are an information designer. From the report below, write one concrete "
        "diagram brief per requested slot: a single vivid sentence describing exactly "
        "what to draw (boxes, arrows, axes, labels) to explain that aspect. "
        "Minimalist and technical.\n\n"
        f"SLOTS: {[s[0] + ' = ' + s[1] for s in slots]}\n\n"
        'Return STRICT JSON: array of {"slot":str,"brief":str}.\n\n'
        f"PAPER TITLE: {title}\nREPORT (excerpt):\n{report_md[:4000]}"
    )
    try:
        by_slot = {b.get("slot"): b.get("brief", "") for b in generate_json(prompt, temperature=0.4, max_output_tokens=2048)}
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [visuals] brief planning failed ({exc}); slot defaults")
        by_slot = {}
    return [{"slot": s, "brief": by_slot.get(s) or f"{d} for '{title}'"} for s, d in slots]


def _generate_image(brief: str, base_no_ext: str, verbose: bool) -> str | None:
    try:
        resp = _api(lambda: get_client().models.generate_content(
            model=config.IMAGE_MODEL, contents=config.IMAGE_STYLE_PREFIX + brief,
            config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])),
            label="image", image=True)
        for cand in (resp.candidates or []):
            for part in (cand.content.parts or []):
                data = getattr(part, "inline_data", None)
                if data and data.data:
                    ext = _MIME_EXT.get((data.mime_type or "").lower(), "png")
                    path = f"{base_no_ext}.{ext}"
                    with open(path, "wb") as fh:
                        fh.write(data.data)
                    return os.path.basename(path)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [visuals] generation error ({exc}); placeholder")
    return None


def _default_briefs(title: str, n: int) -> List[dict]:
    slots = DIAGRAM_SLOTS[:max(1, min(n, len(DIAGRAM_SLOTS)))]
    return [{"slot": s, "brief": f"{d} for '{title}'"} for s, d in slots]


def render_visuals(title: str, report_md: str, images_dir: str, paper_idx: int,
                   n: int = None, no_images: bool = False, plan: bool = True,
                   verbose: bool = True) -> List[dict]:
    """Generate diagrams for one report; return [{slot, brief, filename, generated}].

    plan=True asks the text model for tailored diagram briefs (one extra LLM call);
    plan=False uses default slot briefs (no call) -- used for the lean news track.
    """
    n = n or config.IMAGES_PER_PAPER
    os.makedirs(images_dir, exist_ok=True)
    briefs = _plan_diagrams(title, report_md, n, verbose) if plan else _default_briefs(title, n)
    results = []
    for j, b in enumerate(briefs, 1):
        base = os.path.join(images_dir, f"paper{paper_idx}_{j:02d}_{b['slot']}")
        fname = None if no_images else _generate_image(b["brief"], base, verbose)
        if not fname:
            fname = f"paper{paper_idx}_{j:02d}_{b['slot']}.png"
            _placeholder(os.path.join(images_dir, fname), b["brief"])
            generated = False
        else:
            generated = True
        results.append({**b, "filename": fname, "generated": generated})
        if verbose:
            print(f"  [visuals] paper{paper_idx} {b['slot']:<12} -> {fname} "
                  f"({'img' if generated else 'placeholder'})")
    return results


# =========================================================================== #
# Recent AI News -- grounded fetch -> rolling history -> cluster over last N days
# =========================================================================== #
def _history_path() -> str:
    return os.path.join(config.OUTPUT_ROOT, config.NEWS_HISTORY_FILE)


def fetch_recent_news(run_id: str, days: int = None, n: int = None, verbose: bool = True) -> List[dict]:
    n = n or config.NEWS_PER_RUN
    days = days or config.NEWS_HISTORY_DAYS
    tool = types.Tool(google_search=types.GoogleSearch())
    prompt = (
        f"Search the web for the {n} most important and widely-covered ARTIFICIAL "
        f"INTELLIGENCE news stories published in the LAST {days} DAYS ONLY (model "
        "launches, research breakthroughs, major funding/regulation, enterprise AI "
        "moves, agent/tooling releases). Exclude anything older than "
        f"{days} days. For each give: a specific headline; a 1-2 sentence factual "
        "summary that INCLUDES the key hard numbers (exact funding amount and "
        "valuation, price before→after, revenue/headcount before→after, %); the "
        "primary organization; the source publication domain; the DIRECT ARTICLE "
        "URL (a real https link to the specific story, not a homepage); and the "
        "publication DATE in YYYY-MM-DD format.\n\n"
        'Return STRICT JSON: array of {"title":str,"summary":str,"org":str,'
        '"source":str,"url":str,"date":str}.'
    )
    grounding_urls = []
    try:
        resp = _api(lambda: get_client().models.generate_content(
            model=config.TEXT_MODEL, contents=prompt,
            config=types.GenerateContentConfig(tools=[tool], temperature=0.3)),
            label="news-search", search=True)
        items = _loads(resp.text or "")
        grounding_urls = _grounding_urls(resp)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [news] grounded fetch failed ({exc})")
        items = []
    clean = [{"run_id": run_id, "title": str(it.get("title", "")).strip(),
              "summary": str(it.get("summary", "")).strip(),
              "org": str(it.get("org", "")).strip(),
              "source": str(it.get("source", "")).strip(),
              "url": str(it.get("url", "")).strip(),
              "date": str(it.get("date", "")).strip()[:10]}
             for it in (items or []) if isinstance(it, dict) and it.get("title")]
    # Backfill any missing URL from the real grounding sources, in order.
    gi = iter(grounding_urls)
    for c in clean:
        if not c["url"].startswith("http"):
            c["url"] = next(gi, "")
    if verbose:
        n_url = sum(1 for c in clean if c["url"].startswith("http"))
        print(f"  [news] fetched {len(clean)} headlines this run ({n_url} with links)")
    return clean


def _grounding_urls(resp) -> List[str]:
    """Pull real source URLs from a grounded response's metadata (best-effort)."""
    urls = []
    try:
        for cand in (resp.candidates or []):
            gm = getattr(cand, "grounding_metadata", None)
            for chunk in (getattr(gm, "grounding_chunks", None) or []):
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if uri:
                    urls.append(uri)
    except Exception:  # noqa: BLE001
        pass
    return urls


def append_news_history(items: List[dict]) -> None:
    os.makedirs(config.OUTPUT_ROOT, exist_ok=True)
    with open(_history_path(), "a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def _load_recent_history(days: int):
    """Return (items, n_runs) for entries whose run_id date is within `days`."""
    path = _history_path()
    if not os.path.isfile(path):
        return [], 0
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:  # run_id is an ISO timestamp; keep only the last `days` days
                rid_date = _dt.date.fromisoformat((r.get("run_id") or "")[:10])
            except ValueError:
                continue
            if rid_date >= cutoff:
                rows.append(r)
    n_runs = len({r.get("run_id") for r in rows if r.get("run_id")})
    return rows, n_runs


def cluster_topics(days: int = None, top_n: int = None, verbose: bool = True) -> List[NewsTopic]:
    """Cluster news accumulated over the last `days` days into top topics."""
    top_n = top_n or config.NEWS_TOP_N
    days = days or config.NEWS_HISTORY_DAYS
    items, n_runs = _load_recent_history(days)
    if not items:
        return []
    # index items so the model references them by id; we rebuild real citations.
    compact = [{"i": idx, "title": it.get("title", ""), "summary": it.get("summary", ""),
                "org": it.get("org", ""), "source": it.get("source", ""),
                "date": it.get("date", "")} for idx, it in enumerate(items)]
    prompt = (
        "You are an AI news editor. Below are AI-news items (each with an id 'i') "
        f"from the last {days} days (collected across {n_runs} pipeline run(s)). "
        "CLUSTER them into coherent TOPICS (merge duplicates and follow-ups). "
        f"Return the TOP {top_n} topics ranked by importance AND persistence (a "
        "theme recurring across multiple distinct runs ranks higher). For each "
        "topic, list the ids of the contributing items in 'article_ids'.\n\n"
        'Return STRICT JSON: array of {"topic":str,"summary":str,"salience":float,'
        '"article_ids":[int]}.\n\n'
        f"ITEMS:\n{json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        clusters = generate_json(prompt, temperature=0.3, max_output_tokens=4096)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [news] clustering failed ({exc})")
        return []

    topics = []
    for c in (clusters or [])[:top_n]:
        ids = [int(x) for x in c.get("article_ids", [])
               if str(x).lstrip("-").isdigit() and 0 <= int(x) < len(items)]
        cited = [items[i] for i in ids]
        # prefer items that carry a real link; build de-duplicated citations
        cited.sort(key=lambda it: 0 if str(it.get("url", "")).startswith("http") else 1)
        seen, citations, sources = set(), [], []
        for it in cited:
            key = it.get("url") or it.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            citations.append({"title": it.get("title", ""), "url": it.get("url", ""),
                              "date": it.get("date", ""), "source": it.get("source", "")})
            if it.get("source"):
                sources.append(it["source"])
        t = NewsTopic(
            topic=str(c.get("topic", "")).strip(),
            summary=str(c.get("summary", "")).strip(),
            headlines=[it.get("title", "") for it in cited],
            sources=list(dict.fromkeys(sources)),
            citations=citations[:6],
            run_span=len({it.get("run_id") for it in cited if it.get("run_id")}) or 1,
            salience=float(c.get("salience", 0.0) or 0.0),
        )
        t.title = t.topic
        t.author_line = ", ".join(t.sources) or "Multiple outlets"
        t.detected_labs = list(t.sources)
        topics.append(t)

    if verbose:
        print(f"  [news] clustered {len(items)} items from {n_runs} run(s) in last "
              f"{days}d -> top {len(topics)} topics:")
        for i, t in enumerate(topics, 1):
            print(f"    {i}. {t.topic}  (runs={t.run_span}, salience={t.salience:.1f})")
    return topics


# =========================================================================== #
# Engineering blogs -- grounded fetch -> rolling history -> rank top N over N days
# =========================================================================== #
def _blog_history_path() -> str:
    return os.path.join(config.OUTPUT_ROOT, config.BLOG_HISTORY_FILE)


def fetch_engineering_blogs(run_id: str, days: int = None, n: int = None,
                            verbose: bool = True) -> List[dict]:
    """Grounded search for recent PRACTICAL AI-agent implementation blog posts."""
    n = n or config.BLOG_PER_RUN
    days = days or config.BLOG_HISTORY_DAYS
    orgs = ", ".join(config.BLOG_SOURCES)
    tool = types.Tool(google_search=types.GoogleSearch())
    prompt = (
        f"Search the web for the {n} best RECENT engineering-blog posts about "
        "BUILDING AI AGENTS / agentic systems — practical 'how we built it' "
        "write-ups with real implementation detail (architecture, orchestration, "
        "tool use, memory, evals, prompts, guardrails, production lessons). Favor "
        f"posts published in the LAST {days} DAYS; skip anything clearly older. "
        f"Prioritize the engineering blogs of: {orgs} — and other reputable "
        "engineering orgs. Exclude marketing pages, model release notes with no "
        "implementation detail, and pure opinion pieces. For each give: the exact "
        "post title; the publishing ORG; a 1-2 sentence factual summary of what "
        "they built and the key technique; the source domain; the DIRECT ARTICLE "
        "URL (a real https link to the specific post, not a homepage); and the "
        "publication DATE in YYYY-MM-DD format.\n\n"
        'Return STRICT JSON: array of {"title":str,"org":str,"summary":str,'
        '"source":str,"url":str,"date":str}.'
    )
    grounding_urls = []
    try:
        resp = _api(lambda: get_client().models.generate_content(
            model=config.TEXT_MODEL, contents=prompt,
            config=types.GenerateContentConfig(tools=[tool], temperature=0.3)),
            label="blog-search", search=True)
        items = _loads(resp.text or "")
        grounding_urls = _grounding_urls(resp)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [blogs] grounded fetch failed ({exc})")
        items = []
    clean = [{"run_id": run_id, "title": str(it.get("title", "")).strip(),
              "org": str(it.get("org", "")).strip(),
              "summary": str(it.get("summary", "")).strip(),
              "source": str(it.get("source", "")).strip(),
              "url": str(it.get("url", "")).strip(),
              "date": str(it.get("date", "")).strip()[:10]}
             for it in (items or []) if isinstance(it, dict) and it.get("title")]
    # Backfill any missing URL from the real grounding sources, in order.
    gi = iter(grounding_urls)
    for c in clean:
        if not c["url"].startswith("http"):
            c["url"] = next(gi, "")
    if verbose:
        n_url = sum(1 for c in clean if c["url"].startswith("http"))
        print(f"  [blogs] fetched {len(clean)} posts this run ({n_url} with links)")
    return clean


def append_blog_history(items: List[dict]) -> None:
    os.makedirs(config.OUTPUT_ROOT, exist_ok=True)
    with open(_blog_history_path(), "a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def _load_recent_blog_history(days: int):
    """Return (items, n_runs) for blog entries whose run_id date is within `days`."""
    path = _blog_history_path()
    if not os.path.isfile(path):
        return [], 0
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:  # run_id is an ISO timestamp; keep only the last `days` days
                rid_date = _dt.date.fromisoformat((r.get("run_id") or "")[:10])
            except ValueError:
                continue
            if rid_date >= cutoff:
                rows.append(r)
    n_runs = len({r.get("run_id") for r in rows if r.get("run_id")})
    return rows, n_runs


def select_blogs(days: int = None, top_n: int = None, verbose: bool = True) -> List[BlogPost]:
    """De-duplicate blog posts over the last `days` days and rank the top N."""
    top_n = top_n or config.BLOG_TOP_N
    days = days or config.BLOG_HISTORY_DAYS
    items, n_runs = _load_recent_blog_history(days)
    if not items:
        return []
    # De-duplicate by URL (fallback title), keeping the first-seen entry.
    seen, unique = set(), []
    for it in items:
        key = (it.get("url") or "").strip().lower() or (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(it)
    compact = [{"i": idx, "title": it.get("title", ""), "org": it.get("org", ""),
                "summary": it.get("summary", ""), "source": it.get("source", ""),
                "date": it.get("date", "")} for idx, it in enumerate(unique)]
    prompt = (
        "You are an engineering editor curating a digest of PRACTICAL AI-agent "
        "implementation blog posts. Below are candidate posts (each with an id 'i') "
        f"collected over the last {days} days ({n_runs} run(s)). Pick the TOP "
        f"{top_n} that are the most useful to an engineer BUILDING agents — favor "
        "concrete implementation detail, credible orgs, and recency; drop marketing "
        "and duplicates. For each pick, write a one-line 'why' (why an agent builder "
        "should read it) and a salience score 0-10.\n\n"
        'Return STRICT JSON: array of {"i":int,"why":str,"salience":float}.\n\n'
        f"POSTS:\n{json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        picks = generate_json(prompt, temperature=0.3, max_output_tokens=2048)
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [blogs] ranking failed ({exc})")
        return []

    blogs = []
    for p in (picks or [])[:top_n]:
        try:
            idx = int(p.get("i"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(unique)):
            continue
        it = unique[idx]
        b = BlogPost(
            org=str(it.get("org", "")).strip(),
            url=str(it.get("url", "")).strip(),
            summary=str(it.get("summary", "")).strip(),
            source=str(it.get("source", "")).strip(),
            date=str(it.get("date", "")).strip()[:10],
            run_id=str(it.get("run_id", "")).strip(),
            why=str(p.get("why", "")).strip(),
            salience=float(p.get("salience", 0.0) or 0.0),
        )
        b.title = str(it.get("title", "")).strip()
        b.author_line = b.org or b.source or "Engineering blog"
        b.detected_labs = [b.org] if b.org else []
        blogs.append(b)

    if verbose:
        print(f"  [blogs] ranked {len(unique)} unique posts from {n_runs} run(s) in "
              f"last {days}d -> top {len(blogs)}:")
        for i, b in enumerate(blogs, 1):
            print(f"    {i}. {b.title[:60]}  ({b.org}, salience={b.salience:.1f})")
    return blogs


# =========================================================================== #
# Stage 6 -- Dispatch (SMTP when configured, else dry-run .html + .eml)
# =========================================================================== #
def _inline_images(html_doc: str, images_dir: str):
    attachments, seen = [], {}

    def repl(m):
        fname = os.path.basename(m.group(1))
        path = os.path.join(images_dir, fname)
        if not os.path.isfile(path):
            return m.group(0)
        if fname not in seen:
            seen[fname] = f"img{len(seen)}"
            with open(path, "rb") as fh:
                img = MIMEImage(fh.read())
            img.add_header("Content-ID", f"<{seen[fname]}>")
            img.add_header("Content-Disposition", "inline", filename=fname)
            attachments.append(img)
        return f'src="cid:{seen[fname]}"'

    html_doc = re.sub(r'src="([^"]+\.(?:png|jpg|jpeg|webp))"', repl, html_doc)
    return html_doc, attachments


def dispatch(html_doc: str, subject: str, out_dir: str, images_dir: str,
             dry_run: bool = False, verbose: bool = True) -> dict:
    html_path = os.path.join(out_dir, config.FINAL_HTML_NAME)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    if verbose:
        print(f"  [dispatch] wrote {html_path}")

    email_html, attachments = _inline_images(html_doc, images_dir)
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = config.SMTP["sender"] or "arxiv-digest@localhost"
    msg["To"] = config.SMTP["to"] or "you@example.com"
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("This digest is best viewed as HTML.", "plain"))
    alt.attach(MIMEText(email_html, "html"))
    msg.attach(alt)
    for img in attachments:
        msg.attach(img)

    eml_path = os.path.join(out_dir, "digest.eml")
    with open(eml_path, "w", encoding="utf-8") as fh:
        fh.write(msg.as_string())
    if verbose:
        print(f"  [dispatch] wrote {eml_path} ({len(attachments)} inline images)")

    if dry_run or not config.smtp_configured():
        if verbose and not dry_run:
            print("  [dispatch] SMTP not configured -> DRY RUN. To send live set: "
                  "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO")
        return {"sent": False, "html_path": html_path, "eml_path": eml_path}

    with smtplib.SMTP(config.SMTP["host"], config.SMTP["port"]) as server:
        server.starttls()
        server.login(config.SMTP["user"], config.SMTP["password"])
        server.sendmail(config.SMTP["sender"], [config.SMTP["to"]], msg.as_string())
    if verbose:
        print(f"  [dispatch] emailed digest to {config.SMTP['to']}")
    return {"sent": True, "html_path": html_path, "eml_path": eml_path}
