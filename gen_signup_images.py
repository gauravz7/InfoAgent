"""One-off: generate signup-page hero image options in the digest's house theme.

Reuses config.IMAGE_STYLE_PREFIX (minimalist black/white/pink infographic) and the
same Gemini image model the pipeline uses, so the options match every issue's look.
Run: uv run python gen_signup_images.py   (writes output/signup_images/*.png)
"""
from __future__ import annotations

import os

from app import config
from app.pipeline import _generate_image

OUT = os.path.join(config.OUTPUT_ROOT, "signup_images")
os.makedirs(OUT, exist_ok=True)

# 4 distinct concepts for an "AI Research Digest" newsletter signup / campaign page.
OPTIONS = [
    ("option1_inbox_spark",
     "a friendly newsletter subscription hero illustration: an open envelope with a "
     "small AI spark / neural-node burst rising out of it, a subtle 'subscribe' pink "
     "button shape, conveying a weekly AI-research email landing in your inbox"),
    ("option2_agent_delivers",
     "a minimalist AI agent robot character cheerfully handing over a folded letter / "
     "newsletter to the viewer, clean line-art, conveying an autonomous agent that "
     "delivers a research digest to your inbox"),
    ("option3_paper_to_mail",
     "a flow illustration: a stack of research papers on the left transforming along a "
     "thin arrow into a tidy email envelope on the right, small nodes and sparkles, "
     "conveying 'top arXiv AI-agent papers, summarized to your inbox'"),
    ("option4_signup_form_mock",
     "a clean signup form mockup card floating on the canvas: an email input field and "
     "a bold pink 'Subscribe' button, with small orbiting AI icons (brain, chat bubble, "
     "network) around it, editorial and inviting"),
]


def main() -> None:
    print(f">> project={config.GENAI_PROJECT} location={config.GENAI_LOCATION} "
          f"model={config.IMAGE_MODEL}")
    ok = 0
    for name, brief in OPTIONS:
        base = os.path.join(OUT, name)
        print(f">> generating {name} ...")
        fn = _generate_image(brief, base, verbose=True)
        if fn:
            ok += 1
            print(f"   wrote {os.path.join(OUT, fn)}")
        else:
            print(f"   FAILED {name}")
    print(f">> done: {ok}/{len(OPTIONS)} images in {OUT}")


if __name__ == "__main__":
    main()
