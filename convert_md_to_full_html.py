"""Stage 5a -- Build HTML (MD -> dense, styled HTML).

A self-contained Markdown-to-HTML converter tuned for this pipeline. It renders
the full MD-level detail (headings, lists, tables, code, blockquotes, bold/
italic/inline-code), transforms LaTeX-ish math into clean Unicode/HTML, styles
the 💡 / 🔍 / 🏭 callout blocks, and interleaves each paper's generated diagrams
between the report sections.

Usable as a library (render_paper_html / build_full_html) or standalone CLI:

    python3 convert_md_to_full_html.py report.md --images-dir output/2026-07-21/images \
        --paper-idx 1 -o preview.html
"""

from __future__ import annotations

import html
import os
import re
from typing import List

import config

# --------------------------------------------------------------------------- #
# LaTeX / math -> Unicode
# --------------------------------------------------------------------------- #
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ",
    "Delta": "Δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "Theta": "Θ", "iota": "ι", "kappa": "κ", "lambda": "λ",
    "Lambda": "Λ", "mu": "μ", "nu": "ν", "xi": "ξ", "Xi": "Ξ", "pi": "π",
    "Pi": "Π", "rho": "ρ", "sigma": "σ", "Sigma": "Σ", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "Phi": "Φ", "chi": "χ",
    "psi": "ψ", "Psi": "Ψ", "omega": "ω", "Omega": "Ω",
}
_SYMBOLS = {
    "vdash": "⊢", "dashv": "⊣", "longrightarrow": "⟶", "rightarrow": "→",
    "Rightarrow": "⇒", "leftarrow": "←", "leftrightarrow": "↔",
    "subseteq": "⊆", "subset": "⊂", "supseteq": "⊇", "supset": "⊃",
    "in": "∈", "notin": "∉", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "leq": "≤", "geq": "≥",
    "neq": "≠", "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "sqrt": "√", "circ": "∘", "star": "⋆", "oplus": "⊕",
    "otimes": "⊗", "to": "→", "mapsto": "↦", "ll": "≪", "gg": "≫",
    "Vdash": "⊩", "models": "⊨", "top": "⊤", "bot": "⊥", "ldots": "…",
    "cdots": "⋯", "langle": "⟨", "rangle": "⟩", "ast": "∗",
}


def _sub_sup(s: str) -> str:
    # e^{...} / x^2  ->  <sup>...</sup> ;  a_{...} / a_i -> <sub>...</sub>
    s = re.sub(r"\^\{([^}]*)\}", lambda m: f"<sup>{m.group(1)}</sup>", s)
    s = re.sub(r"\^(\\?\w)", lambda m: f"<sup>{m.group(1)}</sup>", s)
    s = re.sub(r"_\{([^}]*)\}", lambda m: f"<sub>{m.group(1)}</sub>", s)
    s = re.sub(r"_(\\?\w)", lambda m: f"<sub>{m.group(1)}</sub>", s)
    return s


def _replace_commands(s: str) -> str:
    def repl(m):
        name = m.group(1)
        if name in _GREEK:
            return _GREEK[name]
        if name in _SYMBOLS:
            return _SYMBOLS[name]
        if name in ("text", "mathrm", "mathbf", "mathcal", "mathbb", "operatorname", "mathit"):
            return ""  # drop the wrapper; brace stripper handles the argument
        if name == "longrightarrow":
            return "⟶"
        return m.group(0)
    # apply repeatedly for nested/adjacent commands
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\\([A-Za-z]+)", repl, s)
    # \longrightarrow^* etc already handled; clean leftover \\
    return s


def _render_math(expr: str) -> str:
    s = expr
    s = _replace_commands(s)
    s = _sub_sup(s)
    # strip remaining TeX braces and stray backslashes
    s = s.replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    s = s.replace("\\{", "{").replace("\\}", "}")
    s = re.sub(r"[{}]", "", s)
    s = s.replace("\\", "")
    s = re.sub(r"\s+", " ", s).strip()
    return f'<span class="math">{s}</span>'


def _transform_inline_math(text: str) -> str:
    # display $$...$$ and inline $...$  (already HTML-escaped text comes in)
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: _render_math(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", lambda m: _render_math(m.group(1)), text)
    # bare \( ... \) and \[ ... \]
    text = re.sub(r"\\\((.+?)\\\)", lambda m: _render_math(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: _render_math(m.group(1)), text, flags=re.DOTALL)
    return text


# --------------------------------------------------------------------------- #
# Inline markdown (bold/italic/code/links)
# --------------------------------------------------------------------------- #
def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    # inline code first (protect its contents)
    codes: List[str] = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = _transform_inline_math(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', text)
    # restore code
    for i, c in enumerate(codes):
        text = text.replace(f"\x00CODE{i}\x00", f"<code>{html.escape(c, quote=False)}</code>")
    return text


# --------------------------------------------------------------------------- #
# Callout detection
# --------------------------------------------------------------------------- #
def _callout_class(line: str) -> str | None:
    low = line.lower()
    if "💡" in line or "intuition" in low:
        return "callout intuition"
    if "🔍" in line or "concrete" in low or "example" in low:
        return "callout example"
    if "🏭" in line or "industr" in low or "use-case" in low or "enterprise" in low:
        return "callout industry"
    return "callout"


# --------------------------------------------------------------------------- #
# Block-level parser
# --------------------------------------------------------------------------- #
def _md_to_blocks(md: str) -> List[str]:
    """Convert markdown body to a list of HTML block strings (section-aware)."""
    lines = md.split("\n")
    out: List[str] = []
    i = 0
    n = len(lines)

    def flush_para(buf):
        if buf:
            out.append(f"<p>{_inline(' '.join(buf))}</p>")
            buf.clear()

    para: List[str] = []
    while i < n:
        line = lines[i]

        # fenced code
        if line.strip().startswith("```"):
            flush_para(para)
            lang = line.strip()[3:].strip()
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(code_lines), quote=False)
            out.append(f'<pre class="code" data-lang="{html.escape(lang)}"><code>{code}</code></pre>')
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para(para)
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # horizontal rule
        if re.match(r"^\s*([-*_])\1\1+\s*$", line):
            flush_para(para)
            out.append("<hr>")
            i += 1
            continue

        # table (pipe rows with a separator line)
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            flush_para(para)
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "".join(f"<th>{_inline(h)}</th>" for h in header)
            body = ""
            for r in rows:
                body += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>")
            continue

        # blockquote (grouped, callout-aware)
        if line.strip().startswith(">"):
            flush_para(para)
            q_lines = []
            first = line.strip().lstrip(">").strip()
            cls = _callout_class(first)
            while i < n and lines[i].strip().startswith(">"):
                q_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            inner = _inline(" ".join(l for l in q_lines if l))
            out.append(f'<blockquote class="{cls}">{inner}</blockquote>')
            continue

        # lists (unordered / ordered)
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            flush_para(para)
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i])
                items.append(f"<li>{_inline(item)}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # blank line -> paragraph break
        if not line.strip():
            flush_para(para)
            i += 1
            continue

        para.append(line.strip())
        i += 1

    flush_para(para)
    return out


# --------------------------------------------------------------------------- #
# Image interleaving
# --------------------------------------------------------------------------- #
# Map diagram slot -> the section heading (by number) after which it appears.
_SLOT_AFTER_HEADING = {
    "summary": 1,        # after "## 1. Executive Summary..."
    "architecture": 2,   # after "## 2. Implementation Details..."
    "method": 3,         # after "## 3. Core Method, Mathematics..."
    "analogy": 4,        # after "## 4. Analogies..."
    "results": 5,        # after "## 5. Results..."
}


def _figure_html(img_rel: str, caption: str, idx: int) -> str:
    cap = html.escape(caption)
    return (
        f'<figure class="diagram">'
        f'<img src="{img_rel}" alt="{cap}" loading="lazy">'
        f'<figcaption>Figure {idx}. {cap}</figcaption>'
        f"</figure>"
    )


def render_paper_html(md: str, diagrams: List[dict], images_prefix: str) -> str:
    """Render one paper's markdown to HTML with diagrams interleaved by section."""
    blocks = _md_to_blocks(md)

    # index level-2 section numbers -> position of that heading block
    slot_by_secnum = {}
    for slot, secnum in _SLOT_AFTER_HEADING.items():
        slot_by_secnum.setdefault(secnum, []).append(slot)
    diagram_by_slot = {d["slot"]: d for d in diagrams}

    used = set()
    out_blocks: List[str] = []
    fig_counter = 0
    for blk in blocks:
        out_blocks.append(blk)
        m = re.match(r"<h2>\s*(\d+)\.", blk)
        if m:
            secnum = int(m.group(1))
            for slot in slot_by_secnum.get(secnum, []):
                d = diagram_by_slot.get(slot)
                if d and d["filename"] not in used:
                    fig_counter += 1
                    rel = os.path.join(images_prefix, d["filename"]) if images_prefix else d["filename"]
                    out_blocks.append(_figure_html(rel, d.get("brief", slot), fig_counter))
                    used.add(d["filename"])

    # append any diagrams whose section wasn't found (robustness)
    for d in diagrams:
        if d["filename"] not in used:
            fig_counter += 1
            rel = os.path.join(images_prefix, d["filename"]) if images_prefix else d["filename"]
            out_blocks.append(_figure_html(rel, d.get("brief", d["slot"]), fig_counter))

    return "\n".join(out_blocks)


# --------------------------------------------------------------------------- #
# Full-document assembly
# --------------------------------------------------------------------------- #
def _css() -> str:
    t = config.THEME
    return f"""
    :root {{
      --bg:{t['bg']}; --accent:{t['accent']}; --accent-soft:{t['accent_soft']};
      --text:{t['text']}; --text-soft:{t['text_soft']}; --rule:{t['rule']};
      --code-bg:{t['code_bg']};
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
      line-height:1.7; font-size:17px; }}
    .wrap {{ max-width:860px; margin:0 auto; padding:56px 28px 96px; background:var(--bg); }}
    .masthead {{ border-bottom:3px solid var(--accent); padding-bottom:22px; margin-bottom:14px; }}
    .masthead .kicker {{ color:var(--accent); font-weight:700; letter-spacing:.14em;
      text-transform:uppercase; font-size:12px; }}
    .masthead h1 {{ font-size:32px; line-height:1.2; margin:.35em 0 .15em; }}
    .masthead .meta {{ color:var(--text-soft); font-size:14px; }}
    .toc {{ background:#fff; border:1px solid var(--rule); border-left:4px solid var(--accent);
      padding:16px 22px; margin:28px 0; border-radius:6px; }}
    .toc h3 {{ margin:.2em 0 .5em; font-size:14px; text-transform:uppercase;
      letter-spacing:.08em; color:var(--text-soft); }}
    .toc ol {{ margin:0 0 6px; padding-left:20px; }} .toc a {{ color:var(--text); }}
    .toc-label {{ font-size:12px; font-weight:700; letter-spacing:.06em;
      text-transform:uppercase; color:var(--accent); margin:10px 0 2px; }}
    .section-divider {{ margin:56px 0 8px; padding:18px 0; border-top:3px solid var(--accent);
      border-bottom:1px solid var(--rule); text-align:center; }}
    .section-divider .kicker {{ color:var(--accent); font-weight:700; letter-spacing:.1em;
      text-transform:uppercase; font-size:13px; }}
    article {{ margin-top:12px; }}
    .paper {{ padding:34px 0 8px; border-top:1px solid var(--rule); margin-top:40px; }}
    .paper:first-of-type {{ border-top:none; margin-top:8px; }}
    .paper-tag {{ display:inline-block; background:var(--accent); color:#fff; font-size:12px;
      font-weight:700; padding:4px 12px; border-radius:20px; letter-spacing:.06em; }}
    .paper-src {{ color:var(--text-soft); font-size:14px; margin:10px 0 2px; }}
    .paper-src a {{ color:var(--accent); text-decoration:none; }}
    ul.cites {{ margin:.2em 0 .8em 1.1em; padding:0; font-size:13.5px; color:var(--text-soft); }}
    ul.cites li {{ margin:.2em 0; }}
    ul.cites a {{ color:var(--accent); text-decoration:none; }}
    h1,h2,h3,h4 {{ line-height:1.28; }}
    h1 {{ font-size:27px; margin:.5em 0 .3em; }}
    h2 {{ font-size:22px; margin:1.5em 0 .4em; padding-bottom:.2em; border-bottom:1px solid var(--rule); }}
    h3 {{ font-size:18px; color:var(--accent); margin:1.3em 0 .3em; }}
    h4 {{ font-size:16px; margin:1.1em 0 .3em; }}
    p {{ margin:.7em 0; }}
    ul,ol {{ margin:.6em 0 .6em 1.2em; }} li {{ margin:.28em 0; }}
    strong {{ color:#000000; }}
    code {{ background:var(--code-bg); border:1px solid var(--rule); border-radius:4px;
      padding:.08em .35em; font-family:'SF Mono',ui-monospace,Menlo,Consolas,monospace;
      font-size:.9em; color:#C21E73; }}
    pre.code {{ background:var(--code-bg); border:1px solid var(--rule); border-left:4px solid var(--accent);
      border-radius:6px; padding:16px 18px; overflow:auto; }}
    pre.code code {{ background:none; border:none; padding:0; color:var(--text); font-size:13.5px; line-height:1.55; }}
    blockquote.callout {{ margin:1em 0; padding:14px 18px; border-radius:8px;
      background:#FCEFF5; border-left:4px solid var(--accent); color:var(--text); }}
    blockquote.intuition {{ background:#FCEFF5; border-left-color:var(--accent); }}
    blockquote.example {{ background:#F4F4F4; border-left-color:#111111; }}
    blockquote.industry {{ background:#FAFAFA; border-left-color:#888888; }}
    table {{ border-collapse:collapse; width:100%; margin:1.1em 0; font-size:15px; }}
    th,td {{ border:1px solid var(--rule); padding:9px 12px; text-align:left; }}
    th {{ background:#FCE7F0; color:var(--text); }}
    figure.diagram {{ margin:1.6em 0; text-align:center; }}
    figure.diagram img {{ max-width:100%; height:auto; border:1px solid var(--rule);
      border-radius:8px; background:#fff; }}
    figure.diagram figcaption {{ color:var(--text-soft); font-size:13.5px; margin-top:8px; font-style:italic; }}
    .math {{ font-family:'Cambria Math','Latin Modern Math',Georgia,serif; font-size:1.03em; white-space:nowrap; }}
    hr {{ border:none; border-top:1px solid var(--rule); margin:1.6em 0; }}
    .footer {{ margin-top:56px; padding-top:20px; border-top:3px solid var(--accent);
      color:var(--text-soft); font-size:13px; }}
    """


def _render_group(sections: List[dict], id_prefix: str, tag_word: str) -> str:
    articles = []
    total = len(sections)
    for i, p in enumerate(sections, 1):
        articles.append(
            f'<section class="paper" id="{id_prefix}-{i}">'
            f'<span class="paper-tag">{tag_word} {i} OF {total}</span>'
            f'{p["source_html"]}'
            f'{p["body_html"]}'
            f"</section>"
        )
    return "\n".join(articles)


def build_full_html(title: str, subtitle: str, date_str: str,
                    paper_sections: List[dict], news_sections: List[dict] = None) -> str:
    """Assemble the full digest: Top papers + (optional) Top AI-news topics."""
    news_sections = news_sections or []
    toc = "".join(
        f'<li><a href="#paper-{i}">{html.escape(p["title"])}</a></li>'
        for i, p in enumerate(paper_sections, 1)
    )
    news_toc = "".join(
        f'<li><a href="#news-{i}">{html.escape(p["title"])}</a></li>'
        for i, p in enumerate(news_sections, 1)
    )

    def _band(kicker: str) -> str:
        return f'<div class="section-divider"><span class="kicker">{kicker}</span></div>'

    # Subsection 1 — Research (papers)
    body = (
        _band(f"① Research — Top {len(paper_sections)} arXiv papers (last 7 days)")
        + _render_group(paper_sections, "paper", "PAPER")
    )
    # Subsection 2 — News Coverage
    news_block = ""
    if news_sections:
        news_block = (
            _band(f"② News Coverage — Top {len(news_sections)} AI stories (last 7 days)")
            + _render_group(news_sections, "news", "AI NEWS")
        )

    toc_html = (
        f'<nav class="toc"><h3>In this issue</h3>'
        f'<div class="toc-label">① Research — {len(paper_sections)} papers</div><ol>{toc}</ol>'
        + (f'<div class="toc-label">② News Coverage — {len(news_sections)} topics</div>'
           f'<ol>{news_toc}</ol>' if news_sections else "")
        + "</nav>"
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_css()}</style></head>
<body><div class="wrap">
  <header class="masthead">
    <div class="kicker">ArXiv AI-Agent Research Digest</div>
    <h1>{html.escape(title)}</h1>
    <div class="meta">{html.escape(subtitle)} &nbsp;·&nbsp; {html.escape(date_str)}</div>
  </header>
  {toc_html}
  <article>{body}{news_block}</article>
  <div class="footer">
    Generated autonomously by the ArXiv AI-Agent Daily Pipeline · Sources: arXiv
    ({', '.join(config.CATEGORIES)}) + grounded AI-news search · Visual Theme:
    Minimalist Plain White Background (#FFFFFF).
  </div>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse
    import glob

    ap = argparse.ArgumentParser(description="Render a deep-dive Markdown file to styled HTML")
    ap.add_argument("md_file")
    ap.add_argument("--images-dir", default="")
    ap.add_argument("--paper-idx", type=int, default=1)
    ap.add_argument("-o", "--out", default="preview.html")
    args = ap.parse_args()

    with open(args.md_file, encoding="utf-8") as fh:
        md = fh.read()

    diagrams = []
    if args.images_dir and os.path.isdir(args.images_dir):
        paths = []
        for ext in ("png", "jpg", "jpeg", "webp"):
            paths += glob.glob(os.path.join(args.images_dir, f"paper{args.paper_idx}_*.{ext}"))
        for path in sorted(paths):
            fname = os.path.basename(path)
            slot = re.sub(r"\.(png|jpg|jpeg|webp)$", "", fname).rsplit("_", 1)[-1]
            diagrams.append({"slot": slot, "brief": slot, "filename": fname})

    prefix = os.path.relpath(args.images_dir, os.path.dirname(os.path.abspath(args.out))) if args.images_dir else ""
    body_html = render_paper_html(md, diagrams, prefix)
    doc = build_full_html("Digest Preview", "single-paper preview", "",
                          [{"title": "Preview", "source_html": "", "body_html": body_html}])
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"wrote {args.out}")
