#!/usr/bin/env python3
"""Publish generated digest issues into a GitHub Pages /digest/ site + RSS feed.

Takes an issue produced by daily_arxiv_agent.py (output/<date>/) and writes it
into <site>/digest/:

  digest/<date>/index.html   web issue page (relative image links)
  digest/<date>/images/...   the diagrams
  digest/index.html          archive index + MailerLite signup slot
  digest/rss.xml             RSS 2.0; <content:encoded> is email-safe HTML
                             (absolute image URLs) for MailerLite's RSS-to-email

Usage:
  python3 publish.py --date 2026-07-21 --site /path/to/gauravz7.github.io \
      --base-url https://gauravz7.github.io/digest
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import os
import re
import shutil
from email.utils import format_datetime

from app import config
from app.render import (DISCLAIMER_TEXT, MAILERLITE_UNIVERSAL,
                        MAILERLITE_EMBED)

FEED_TITLE = "AI-Agent Research Digest"
FEED_DESC = ("Top AI-agent research papers + the week's AI news — image-forward, "
             "cited, and fact-checked. Twice a week.")
MAX_RSS_ITEMS = 20
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _issue_title(issue_html: str, date: str) -> str:
    """Item title = date + the first paper's measurable-impact headline."""
    h1s = re.findall(r"<h1>(.*?)</h1>", issue_html, re.S)
    lead = ""
    if len(h1s) > 1:  # h1[0] is the masthead; h1[1] is the first paper headline
        lead = re.sub(r"<[^>]+>", "", h1s[1]).strip()
    return f"AI-Agent Digest · {date}" + (f" — {lead}" if lead else "")


def _issue_desc(issue_html: str) -> str:
    m = re.search(r'<div class="meta">(.*?)</div>', issue_html, re.S)
    if m:
        return re.sub(r"<[^>]+>|&nbsp;", " ", m.group(1)).replace("·", "·").strip()
    return FEED_DESC


def _email_html(issue_html: str, date: str, base_url: str) -> str:
    """Email/RSS version: rewrite relative image src to absolute Pages URLs."""
    return re.sub(r'src="images/', f'src="{base_url}/{date}/images/', issue_html)


def publish_issue(output_root: str, date: str, site: str, base_url: str) -> dict:
    """Copy one generated issue into <site>/digest/<date>/. Returns issue meta."""
    src_dir = os.path.join(output_root, date)
    src_html = os.path.join(src_dir, config.FINAL_HTML_NAME)
    if not os.path.isfile(src_html):
        raise FileNotFoundError(f"no issue HTML at {src_html} — run the pipeline first")

    dest_dir = os.path.join(site, "digest", date)
    os.makedirs(dest_dir, exist_ok=True)
    issue_html = _read(src_html)
    _write(os.path.join(dest_dir, "index.html"), issue_html)  # relative imgs, web page

    src_images = os.path.join(src_dir, "images")
    if os.path.isdir(src_images):
        shutil.copytree(src_images, os.path.join(dest_dir, "images"), dirs_exist_ok=True)

    print(f"  [publish] issue -> digest/{date}/ ({len(os.listdir(src_images)) if os.path.isdir(src_images) else 0} images)")
    return {"date": date, "title": _issue_title(issue_html, date),
            "desc": _issue_desc(issue_html), "url": f"{base_url}/{date}/"}


def _list_issue_dates(site: str) -> list[str]:
    d = os.path.join(site, "digest")
    if not os.path.isdir(d):
        return []
    dates = [name for name in os.listdir(d)
             if _DATE_RE.match(name)
             and os.path.isfile(os.path.join(d, name, "index.html"))]
    return sorted(dates, reverse=True)


def rebuild_index(site: str, base_url: str) -> None:
    """Regenerate digest/index.html: archive list + signup slot (pink theme)."""
    t = config.THEME
    rows = []
    for date in _list_issue_dates(site):
        issue_html = _read(os.path.join(site, "digest", date, "index.html"))
        title = _html.escape(_issue_title(issue_html, date))
        rows.append(
            f'<li><a href="{base_url}/{date}/"><span class="d">{date}</span>'
            f'<span class="t">{title}</span></a></li>')
    items = "\n".join(rows) or "<li>No issues yet.</li>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{FEED_TITLE}</title>
<meta name="description" content="{_html.escape(FEED_DESC)}">
<link rel="alternate" type="application/rss+xml" title="{FEED_TITLE}" href="{base_url}/rss.xml">
<style>
  body{{margin:0;background:{t['bg']};color:{t['text']};
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;line-height:1.6}}
  .wrap{{max-width:44rem;margin:0 auto;padding:3.5rem 1.4rem 5rem}}
  .kicker{{color:{t['accent']};font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px}}
  h1{{font-size:2.1rem;margin:.3em 0 .2em;letter-spacing:-.02em}}
  .lede{{color:{t['text_soft']};font-size:1.1rem;margin:0 0 1.6rem;max-width:32rem}}
  .sub{{background:#FCEFF5;border:1px solid {t['rule']};border-left:4px solid {t['accent']};
    border-radius:10px;padding:1rem 1.2rem;margin:0 0 2rem}}
  .sub h3{{margin:.1em 0 .5em;font-size:1rem}}
  .rss{{font-size:.85rem}} .rss a{{color:{t['accent']};text-decoration:none}}
  ul.issues{{list-style:none;padding:0;margin:0}}
  ul.issues li{{border-top:1px solid {t['rule']}}}
  ul.issues a{{display:flex;gap:1rem;align-items:baseline;padding:.9rem .2rem;
    text-decoration:none;color:{t['text']}}}
  ul.issues a:hover .t{{color:{t['accent']}}}
  .d{{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:{t['text_soft']};min-width:6.2rem}}
  .t{{font-weight:600}}
  footer{{margin-top:3rem;padding-top:1.2rem;border-top:3px solid {t['accent']};
    color:{t['text_soft']};font-size:.85rem}}
  footer a{{color:{t['accent']};text-decoration:none}}
  .ml-onclick-form button{{background:{t['accent']};color:#fff;border:0;border-radius:8px;
    padding:.7rem 1.4rem;font-size:1rem;font-weight:700;cursor:pointer}}
  .ml-onclick-form button:hover{{opacity:.9}}
</style>
{MAILERLITE_UNIVERSAL}
</head>
<body><div class="wrap">
  <div class="kicker">Newsletter</div>
  <h1>{FEED_TITLE}</h1>
  <p class="lede">{_html.escape(FEED_DESC)}</p>

  <div class="sub">
    <h3>Subscribe — a fresh AI-agent research digest, delivered daily</h3>
    <p style="margin:.2em 0 .9rem;color:{t['text_soft']};font-size:.9rem">
      Top arXiv AI-agent papers + the week's AI news, summarized to your inbox.</p>
    {MAILERLITE_EMBED}
    <p class="rss" style="margin-top:.9rem">Or subscribe by feed: <a href="{base_url}/rss.xml">RSS</a></p>
  </div>

  <p class="kicker" style="font-size:11px">Archive</p>
  <ul class="issues">
    {items}
  </ul>

  <footer>
    <p style="margin:0 0 .8rem"><strong>⚠️ AI-generated content.</strong>
      {_html.escape(DISCLAIMER_TEXT)}</p>
    <a href="https://gauravz7.github.io/">← gauravz7</a> ·
    <a href="https://github.com/gauravz7/InfoAgent">source</a> ·
    <a href="{base_url}/rss.xml">RSS</a>
  </footer>
</div>
</body></html>"""
    _write(os.path.join(site, "digest", "index.html"), page)
    print(f"  [publish] index -> digest/index.html ({len(_list_issue_dates(site))} issues)")


DIGEST_CARD_START = "<!-- DIGEST-CARD:START -->"
DIGEST_CARD_END = "<!-- DIGEST-CARD:END -->"


def _digest_card_html(date: str, base_url: str) -> str:
    """A dedicated homepage tile for the live AI Digest: the current issue as the
    primary link, an explicit 'older digests' archive link, and the generation
    date. Wrapped in markers so it can be refreshed in place on every run."""
    return (
        f'{DIGEST_CARD_START}\n'
        '        <article class="card">\n'
        '          <span class="tag">Daily · AI-generated · auto-published</span>\n'
        f'          <h2><a class="stretch" href="{base_url}/{date}/">AI Digest — {date}</a></h2>\n'
        '          <p>Today&#x27;s issue — the top AI-agent research papers, the '
        'week&#x27;s AI news, and practical engineering blogs — freshly generated and '
        'published. A new digest lands here every day, with older issues archived. '
        '<em>All text and images are AI-generated and may contain mistakes.</em></p>\n'
        f'          <span class="sub">current: <a href="{base_url}/{date}/">{date}</a> '
        f'· <a href="{base_url}/">older digests</a></span>\n'
        '          <span class="meta-row"><span class="go">Read the current digest '
        '<span class="arw">→</span></span></span>\n'
        '        </article>\n'
        f'        {DIGEST_CARD_END}'
    )


def update_homepage(site: str, base_url: str, date: str) -> None:
    """Add/refresh a dedicated AI-Digest card on the site homepage (index.html),
    pointing at the current issue and stamped with the generation date. Idempotent:
    the card is marker-wrapped; on the first run it is inserted as the first tile in
    the projects grid, and rewritten in place on subsequent runs."""
    path = os.path.join(site, "index.html")
    if not os.path.isfile(path):
        print("  [publish] no homepage index.html; skipping tile update")
        return
    doc = _read(path)
    card = _digest_card_html(date, base_url)
    if DIGEST_CARD_START in doc and DIGEST_CARD_END in doc:
        doc = re.sub(
            re.escape(DIGEST_CARD_START) + r".*?" + re.escape(DIGEST_CARD_END),
            lambda _m: card, doc, count=1, flags=re.S)
    else:
        # First run: insert the new card as the first tile right after the grid opens.
        m = re.search(r'<div class="grid">\s*\n', doc)
        if not m:
            print("  [publish] projects grid not found on homepage; skipping tile update")
            return
        doc = doc[:m.end()] + "\n        " + card + "\n\n" + doc[m.end():]
    _write(path, doc)
    print(f"  [publish] homepage AI-Digest card -> current {date} + archive link")


def rebuild_rss(site: str, base_url: str, now: _dt.datetime = None) -> None:
    """Regenerate digest/rss.xml with content:encoded (absolute-image) HTML."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    items_xml = []
    for date in _list_issue_dates(site)[:MAX_RSS_ITEMS]:
        issue_html = _read(os.path.join(site, "digest", date, "index.html"))
        title = _issue_title(issue_html, date)
        desc = _issue_desc(issue_html)
        url = f"{base_url}/{date}/"
        try:
            pub = _dt.datetime.fromisoformat(date).replace(
                hour=6, tzinfo=_dt.timezone.utc)
        except ValueError:
            pub = now
        content = _email_html(issue_html, date, base_url)
        items_xml.append(f"""    <item>
      <title>{_html.escape(title)}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{url}</guid>
      <pubDate>{format_datetime(pub)}</pubDate>
      <description>{_html.escape(desc)}</description>
      <content:encoded><![CDATA[{content}]]></content:encoded>
    </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{FEED_TITLE}</title>
    <link>{base_url}/</link>
    <atom:link href="{base_url}/rss.xml" rel="self" type="application/rss+xml"/>
    <description>{_html.escape(FEED_DESC)}</description>
    <language>en-us</language>
    <lastBuildDate>{format_datetime(now)}</lastBuildDate>
{chr(10).join(items_xml)}
  </channel>
</rss>"""
    _write(os.path.join(site, "digest", "rss.xml"), rss)
    print(f"  [publish] rss   -> digest/rss.xml ({len(items_xml)} items)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Publish a digest issue into a Pages /digest/ site")
    ap.add_argument("--date", default=_dt.date.today().isoformat(),
                    help="issue date (YYYY-MM-DD); defaults to today")
    ap.add_argument("--site", required=True, help="path to the gauravz7.github.io checkout")
    ap.add_argument("--base-url", default="https://gauravz7.github.io/digest",
                    help="public base URL of the digest section")
    ap.add_argument("--output-root", default=config.OUTPUT_ROOT,
                    help="where daily_arxiv_agent.py wrote the issue")
    args = ap.parse_args(argv)

    base = args.base_url.rstrip("/")
    publish_issue(args.output_root, args.date, args.site, base)
    rebuild_index(args.site, base)
    rebuild_rss(args.site, base)
    update_homepage(args.site, base, args.date)
    print(f"Published {args.date} into {os.path.join(args.site, 'digest')}")


if __name__ == "__main__":
    main()
