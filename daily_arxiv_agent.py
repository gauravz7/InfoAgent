#!/usr/bin/env python3
"""Autonomous daily ArXiv AI-Agent Research Digest pipeline.

Runs six stages end-to-end and writes everything under output/<YYYY-MM-DD>/:

    Fetch  ->  Select  ->  Synthesize  ->  Render Visuals  ->  Build MD & HTML  ->  Dispatch

Auth: Google ADC (Vertex).  Models: gemini-3.1-flash-lite (text) +
gemini-3.1-flash-lite-image (diagrams).  No API keys in source.

Usage:
    python3 daily_arxiv_agent.py                 # full live run, dry-run email
    python3 daily_arxiv_agent.py --quick         # fast smoke run
    python3 daily_arxiv_agent.py --days 30 --top 3
    python3 daily_arxiv_agent.py --no-images     # skip image generation
    python3 daily_arxiv_agent.py --send          # actually email (needs SMTP env)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import os
import re
import sys

import config
import pipeline
from convert_md_to_full_html import render_paper_html, build_full_html


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:maxlen] or "paper"


def _source_html(paper, idx: int) -> str:
    labs = ", ".join(paper.detected_labs) or "affiliations inferred from text"
    updated = f" · <strong>Updated:</strong> {_html.escape(paper.updated)}" if paper.updated and paper.updated != paper.published else ""
    return (
        f'<p class="paper-src"><strong>Authors:</strong> {_html.escape(paper.author_line)} · '
        f'<em>{_html.escape(labs)}</em></p>'
        f'<p class="paper-src"><strong>Published:</strong> {_html.escape(paper.published)}{updated} · '
        f'<strong>Category:</strong> {_html.escape(paper.primary_category)}</p>'
        f'<p class="paper-src"><strong>Citation:</strong> arXiv:'
        f'<a href="{_html.escape(paper.abs_url, quote=True)}">{_html.escape(paper.arxiv_id)}</a> · '
        f'<a href="{_html.escape(paper.pdf_url, quote=True)}">PDF</a> · '
        f'<a href="{_html.escape(paper.abs_url, quote=True)}">abstract page</a></p>'
    )


def _guard_no_banned(text: str, where: str) -> str:
    """Scrub banned words (never crash the run) and warn if any were present."""
    cleaned = pipeline.scrub_banned(text)
    for w in config.BANNED_WORDS:
        if re.search(re.escape(w), text, flags=re.IGNORECASE):
            print(f"  [guard] scrubbed banned word '{w}' from {where}")
    return cleaned


def _news_source_html(topic) -> str:
    head = (
        f'<p class="paper-src"><strong>Clustered AI-news topic</strong> · '
        f'appeared across <strong>{topic.run_span}</strong> recent run(s) · '
        f'salience {topic.salience:.1f}/10</p>'
    )
    cites = []
    for c in getattr(topic, "citations", []) or []:
        title = _html.escape(c.get("title", "") or "source")
        date = _html.escape(c.get("date", "") or "")
        src = _html.escape(c.get("source", "") or "")
        url = c.get("url", "") or ""
        label = title if not src else f"{title} — <em>{src}</em>"
        if date:
            label += f" ({date})"
        if url.startswith("http"):
            cites.append(f'<li><a href="{_html.escape(url, quote=True)}">{label}</a></li>')
        else:
            cites.append(f"<li>{label}</li>")
    if cites:
        return head + '<p class="paper-src"><strong>Citations:</strong></p><ul class="cites">' + "".join(cites) + "</ul>"
    srcs = ", ".join(dict.fromkeys(topic.sources)) or "multiple outlets"
    return head + f'<p class="paper-src"><strong>Sources:</strong> {_html.escape(srcs)}</p>'


def run(days: int, top_n: int, quick: bool, no_images: bool, send: bool,
        no_news: bool = False, verbose: bool = True) -> dict:
    today = _dt.date.today().isoformat()
    run_id = _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    out_dir = os.path.join(config.OUTPUT_ROOT, today)
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    paper_words = config.QUICK_WORDS if quick else config.PAPER_WORDS
    news_words = config.QUICK_WORDS if quick else config.NEWS_WORDS
    imgs_per_paper = 1 if quick else config.IMAGES_PER_PAPER   # keep 5 for papers
    news_imgs = 1 if quick else config.NEWS_IMAGES             # lean news visuals

    pipeline.reset_usage()
    print(f"=== ArXiv AI-Agent Daily Digest · {today} "
          f"({'QUICK' if quick else 'FULL'} run) ===")

    # 1. Fetch --------------------------------------------------------------
    print("[1/6] Fetch")
    candidates = pipeline.fetch_candidates(days=days, verbose=verbose)
    if not candidates:
        print("  no candidate papers found; aborting")
        return {}

    # 2. Select -------------------------------------------------------------
    print("[2/6] Select")
    top = pipeline.rank_and_select(candidates, top_n=top_n, verbose=verbose)

    # 3-4. Synthesize + Visuals (per paper) --------------------------------
    paper_sections = []
    for i, paper in enumerate(top, 1):
        print(f"[3/6] Synthesize paper {i}/{len(top)}")
        md = pipeline.synthesize_paper(paper, words=paper_words, verbose=verbose,
                                       verify=not quick)
        md = _guard_no_banned(md, f"paper {i} markdown")

        # write the deep-dive .md (log-style filename)
        md_name = f"paper{i}_{_slug(paper.title)}_deepdive.md"
        with open(os.path.join(out_dir, md_name), "w", encoding="utf-8") as fh:
            fh.write(md)
        if verbose:
            print(f"  [md] wrote {md_name}")

        print(f"[4/6] Render Visuals paper {i}/{len(top)}")
        diagrams = pipeline.render_visuals(
            paper.title, md, images_dir, paper_idx=i,
            n=imgs_per_paper, no_images=no_images, verbose=verbose,
        )

        body_html = render_paper_html(md, diagrams, images_prefix="images")
        # derive the paper's display headline from the first H1 in the md
        m = re.search(r"^#\s+(.*)$", md, flags=re.MULTILINE)
        headline = m.group(1).strip() if m else paper.title
        paper_sections.append({
            "title": headline,
            "source_html": _source_html(paper, i),
            "body_html": body_html,
        })

    # 4b. Recent AI news track: fetch -> accumulate -> cluster over last `days` --
    news_sections = []
    if not no_news:
        print(f"[4b] Recent AI News (fetch + cluster over last {days} days)")
        fresh = pipeline.fetch_recent_news(run_id, days=days, verbose=verbose)
        if fresh:
            pipeline.append_news_history(fresh)
        topics = pipeline.cluster_topics(days=days, top_n=top_n, verbose=verbose)
        for k, topic in enumerate(topics, 1):
            idx = len(top) + k  # continue image numbering after the papers
            print(f"  [news] synthesize topic {k}/{len(topics)}")
            md = pipeline.synthesize_news_topic(topic, words=news_words, verbose=verbose)
            md = _guard_no_banned(md, f"news topic {k} markdown")
            md_name = f"news{k}_{_slug(topic.topic)}_brief.md"
            with open(os.path.join(out_dir, md_name), "w", encoding="utf-8") as fh:
                fh.write(md)
            diagrams = pipeline.render_visuals(
                topic.title, md, images_dir, paper_idx=idx,
                n=news_imgs, no_images=no_images, plan=False, verbose=verbose,
            )
            body_html = render_paper_html(md, diagrams, images_prefix="images")
            m = re.search(r"^#\s+(.*)$", md, flags=re.MULTILINE)
            headline = m.group(1).strip() if m else topic.topic
            news_sections.append({
                "title": headline,
                "source_html": _news_source_html(topic),
                "body_html": body_html,
            })

    # 5. Build full HTML ----------------------------------------------------
    print("[5/6] Build MD & HTML")
    subtitle = (f"Top {len(paper_sections)} AI-agent papers (last {days} days)"
                + (f" + Top {len(news_sections)} AI-news topics" if news_sections else ""))
    title = "ArXiv AI-Agent Research Digest"
    full_html = build_full_html(title, subtitle, today, paper_sections, news_sections)
    full_html = _guard_no_banned(full_html, "final HTML")

    # 6. Dispatch -----------------------------------------------------------
    print("[6/6] Dispatch")
    subject = f"🤖 ArXiv AI-Agent Digest · {today} · Top {len(paper_sections)} papers"
    result = pipeline.dispatch(full_html, subject, out_dir, images_dir,
                      dry_run=not send, verbose=verbose)

    # Cost summary --------------------------------------------------------
    c = pipeline.cost_report()
    print("--- Estimated cost this run ---")
    print(f"  text: {c['text_calls']} calls, {c['text_in']:,} in + {c['text_out']:,} out tokens "
          f"-> ${c['text_cost']:.4f}")
    print(f"  images: {c['img_calls']} generated -> ${c['img_cost']:.4f}")
    print(f"  grounded-search calls: {c['search_calls']}")
    print(f"  TOTAL ≈ ${c['total_cost']:.4f} per run")

    print(f"=== Done. Output in {out_dir} ===")
    return {"out_dir": out_dir, **result, "papers": len(paper_sections), "cost": c}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Autonomous daily ArXiv AI-agent digest")
    ap.add_argument("--days", type=int, default=config.WINDOW_DAYS,
                    help=f"lookback window in days (default {config.WINDOW_DAYS})")
    ap.add_argument("--top", type=int, default=config.TOP_N,
                    help=f"number of papers to feature (default {config.TOP_N})")
    ap.add_argument("--quick", action="store_true",
                    help="fast smoke run: short synthesis + 1 image/paper")
    ap.add_argument("--no-images", action="store_true",
                    help="skip Gemini image generation (use placeholders)")
    ap.add_argument("--no-news", action="store_true",
                    help="skip the Recent AI News track")
    ap.add_argument("--send", action="store_true",
                    help="actually send email (requires SMTP_* env vars)")
    args = ap.parse_args(argv)

    try:
        run(days=args.days, top_n=args.top, quick=args.quick,
            no_images=args.no_images, send=args.send, no_news=args.no_news)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
