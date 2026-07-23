"""Build a local preview of the published site (index + today's issue) using the
CURRENT render/publish code, so you can see the Subscribe button + form on
localhost without running the paid pipeline. Real issue content is reused from
output/<date>/ and upgraded to the current template (Universal snippet, Subscribe
CTAs, bottom disclaimer)."""
from __future__ import annotations

import os, re, shutil, glob

import publish
from app import config
from app.render import (MAILERLITE_UNIVERSAL, MAILERLITE_SUBSCRIBE_CTA,
                        MAILERLITE_EMBED, DISCLAIMER_TEXT)

DATE = "2026-07-23"
SITE = "/tmp/preview"
BASE = "http://localhost:8000/digest"

CTA_CSS = """<style>
.subscribe-cta{margin:22px 0;text-align:center}
.subscribe-embed{margin:64px 0 24px;padding-top:16px;border-top:1px solid #EAEAEA;scroll-margin-top:24px}
.subscribe-cta a.subscribe-link,.ml-onclick-form button{display:inline-block;background:#E5318A;
  color:#fff;text-decoration:none;border:0;border-radius:8px;padding:12px 22px;font-size:15px;
  font-weight:700;cursor:pointer;font-family:inherit}
.subscribe-cta a.subscribe-link:hover,.ml-onclick-form button:hover{opacity:.9}
.disclaimer{margin:20px 0 8px;padding:12px 16px;border:1px solid #E5318A;border-left:4px solid #E5318A;
  border-radius:6px;background:#FCEFF5;color:#111;font-size:13.5px;line-height:1.55}
.disclaimer strong{color:#E5318A}
</style>"""

DISCLAIMER_HTML = (f'<div class="disclaimer" role="note"><strong>⚠️ AI-generated:</strong> '
                   f'{DISCLAIMER_TEXT}</div>')


def build_issue() -> None:
    src = os.path.join(config.OUTPUT_ROOT, DATE, config.FINAL_HTML_NAME)
    html = open(src, encoding="utf-8").read()
    # head: Universal snippet + CTA/disclaimer CSS
    html = html.replace("</head>", f"{CTA_CSS}\n{MAILERLITE_UNIVERSAL}\n</head>", 1)
    # top CTA right after the masthead
    html = html.replace("</header>", f"</header>\n{MAILERLITE_SUBSCRIBE_CTA}", 1)
    # inline embedded form + disclaimer right after the article
    html = html.replace("</article>",
                        f"</article>\n{MAILERLITE_EMBED}\n{DISCLAIMER_HTML}", 1)
    dst_dir = os.path.join(SITE, "digest", DATE)
    os.makedirs(dst_dir, exist_ok=True)
    open(os.path.join(dst_dir, "index.html"), "w", encoding="utf-8").write(html)
    # copy images so diagrams render
    img_src = os.path.join(config.OUTPUT_ROOT, DATE, "images")
    if os.path.isdir(img_src):
        shutil.copytree(img_src, os.path.join(dst_dir, "images"), dirs_exist_ok=True)
    print(f"  issue -> {dst_dir}/index.html (embed x{html.count('xs3Yrq')})")


def main() -> None:
    if os.path.isdir(SITE):
        shutil.rmtree(SITE)
    os.makedirs(os.path.join(SITE, "digest", DATE))
    build_issue()
    publish.rebuild_index(SITE, BASE)
    print(f">> preview site ready at {SITE}")


if __name__ == "__main__":
    main()
