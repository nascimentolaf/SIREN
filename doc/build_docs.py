#!/usr/bin/env python3
"""Build the HTML documentation in doc/html/ from the Markdown in doc/.

    python3 doc/build_docs.py            # (re)build doc/html/
    python3 doc/build_docs.py --check    # validate every link, write nothing;
                                         #   exits 1 if anything is broken

Requires Python >= 3.8 and the `markdown` package >= 3.3
(`pip install markdown`). Nothing else: no CDN, no JavaScript framework,
so the pages work when opened straight from disk (file://) and offline.

How it works
------------
* Every `*.md` under doc/ (except doc/html/) becomes one page, at the same
  relative path with `.html`: doc/baselines/rr.md -> doc/html/baselines/rr.html.
* The page title is the first `# Heading` of the file.
* The left sidebar follows doc/nav.txt: one page per line, relative to doc/,
  indented by two spaces to nest it under the line above, optionally followed
  by `| Short label` to show something shorter than the page title. A page that exists
  but is not listed still gets built and appears under "Other pages" -- add
  it to nav.txt to place it.
* Links are rewritten so they keep working from doc/html/:
    - `other.md` / `other.md#section`   -> the generated `other.html`
    - a directory containing index.md    -> its index.html
    - anything else (e.g. `../scripts/parameters.sh`) -> the same file in the
      repository, re-pointed relative to doc/html/
* Every link is checked: missing pages, missing files and anchors that do not
  exist in the target page are reported. The normal build reports them as
  warnings; --check turns them into a failing exit status.
* The output is deterministic (no timestamps), so rebuilding unchanged
  sources leaves git with nothing to commit.

Writing pages
-------------
Standard Markdown plus: tables, fenced code blocks (```), footnotes,
definition lists and `{#id}` attributes on headings (python-markdown's
"extra" set). Headings get anchors automatically: `## Two time budgets`
becomes `#two-time-budgets`. Avoid raw HTML: links inside it are neither
rewritten nor checked.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as etree
from pathlib import Path, PurePosixPath

try:
    import markdown
    from markdown.extensions import Extension
    from markdown.treeprocessors import Treeprocessor
except ImportError:  # pragma: no cover - environment problem, not a bug
    sys.exit("build_docs.py needs the 'markdown' package: pip install markdown")

DOC_DIR = Path(__file__).resolve().parent
OUT_DIR = DOC_DIR / "html"
NAV_FILE = DOC_DIR / "nav.txt"
SITE_NAME = "3D-model framework documentation"
ASSETS = ("style.css", "search.js", "search_index.js")


# ---------------------------------------------------------------------------
# Pages and navigation
# ---------------------------------------------------------------------------

class Page:
    """One Markdown source and everything derived from it."""

    def __init__(self, src: PurePosixPath):
        self.src = src                                   # relative to doc/
        self.out = src.with_suffix(".html")              # relative to doc/html/
        self.text = (DOC_DIR / src).read_text(encoding="utf-8")
        self.title = first_heading(self.text) or src.stem
        self.nav_label = self.title                      # nav.txt may shorten it
        self.body = ""
        self.toc_tokens: list = []
        self.ids: set = set()
        self.links: list = []                            # (href as written, resolved, kind)

    @property
    def depth(self) -> int:
        return len(self.out.parts) - 1


def first_heading(text: str) -> str | None:
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            return strip_inline_md(line[2:].strip())
    return None


def strip_inline_md(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", s)


def discover_pages() -> dict:
    pages = {}
    for path in sorted(DOC_DIR.rglob("*.md")):
        rel = PurePosixPath(path.relative_to(DOC_DIR).as_posix())
        if rel.parts[0] == OUT_DIR.name or any(p.startswith(".") for p in rel.parts):
            continue
        pages[rel] = Page(rel)
    return pages


def read_nav(pages: dict, problems: list) -> list:
    """nav.txt -> nested list of (PurePosixPath, children)."""
    root: list = []
    stack = [(-1, root)]
    listed = set()
    if NAV_FILE.exists():
        for lineno, raw in enumerate(NAV_FILE.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            path, _, label = raw.strip().partition("|")
            rel = PurePosixPath(path.strip())
            if rel not in pages:
                problems.append(f"nav.txt:{lineno}: no such page doc/{rel}")
                continue
            while stack[-1][0] >= indent:
                stack.pop()
            node = (rel, [])
            stack[-1][1].append(node)
            stack.append((indent, node[1]))
            listed.add(rel)
            if label.strip():
                pages[rel].nav_label = label.strip()
    unlisted = [p for p in sorted(pages) if p not in listed]
    if unlisted:
        root.append((None, [(p, []) for p in unlisted]))
    return root


def flatten(nav: list) -> list:
    out = []
    for rel, children in nav:
        if rel is not None:
            out.append(rel)
        out.extend(flatten(children))
    return out


# ---------------------------------------------------------------------------
# Markdown conversion with link rewriting
# ---------------------------------------------------------------------------

EXTERNAL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")   # http:, https:, mailto:, ...


def rel_url(target: PurePosixPath, from_dir: PurePosixPath) -> str:
    return PurePosixPath(os.path.relpath(target.as_posix(), from_dir.as_posix() or ".")).as_posix()


class LinkRewriter(Treeprocessor):
    def __init__(self, md, builder):
        super().__init__(md)
        self.builder = builder

    def run(self, root):
        page = self.builder.current
        for el in root.iter():
            if el.tag == "a" and el.get("href"):
                el.set("href", self.builder.rewrite(page, el.get("href")))
            elif el.tag == "img" and el.get("src"):
                el.set("src", self.builder.rewrite(page, el.get("src")))
            if el.get("id"):
                page.ids.add(el.get("id"))
        # Wide tables scroll inside their own box instead of the whole page.
        for parent in list(root.iter()):
            for i, child in enumerate(list(parent)):
                if child.tag == "table":
                    wrap = etree.Element("div", {"class": "table-wrap"})
                    parent.remove(child)
                    wrap.append(child)
                    parent.insert(i, wrap)
        return None


class LinkExtension(Extension):
    def __init__(self, builder):
        super().__init__()
        self.builder = builder

    def extendMarkdown(self, md):
        # After inline processing (so links exist) and after toc (ids exist).
        md.treeprocessors.register(LinkRewriter(md, self.builder), "doc_links", 1)


class Builder:
    def __init__(self, pages: dict):
        self.pages = pages
        self.current: Page | None = None
        self.md = markdown.Markdown(
            extensions=["extra", "sane_lists", "toc", LinkExtension(self)],
            extension_configs={"toc": {"permalink": "#", "permalink_title": "Link to this section"}},
            output_format="html5",
        )

    def rewrite(self, page: Page, href: str) -> str:
        if EXTERNAL.match(href) or href.startswith("#"):
            if href.startswith("#"):
                page.links.append((href, (page.src, href[1:]), "anchor"))
            return href
        path, _, frag = href.partition("#")
        src_dir = page.src.parent
        target = PurePosixPath(os.path.normpath((src_dir / path).as_posix()))
        out_dir = page.out.parent
        if path.endswith(".md") or (DOC_DIR / target / "index.md").is_file():
            md_target = target if path.endswith(".md") else target / "index.md"
            page.links.append((href, (md_target, frag), "page"))
            url = rel_url(md_target.with_suffix(".html"), out_dir)
        else:
            # A file of the repository: point from doc/html/<page dir> to it.
            page.links.append((href, (target, None), "file"))
            url = rel_url(PurePosixPath("..") / target, out_dir)
        return url + ("#" + frag if frag else "")

    def convert(self, page: Page) -> None:
        self.current = page
        self.md.reset()
        page.body = self.md.convert(page.text)
        page.toc_tokens = getattr(self.md, "toc_tokens", [])
        self.current = None


def check_links(pages: dict) -> list:
    problems = []
    for page in pages.values():
        for href, (target, frag), kind in page.links:
            where = f"doc/{page.src}: link '{href}'"
            if kind == "file":
                if not (DOC_DIR / target).exists():
                    problems.append(f"{where}: no such file {target}")
                continue
            if kind == "page" and target not in pages:
                problems.append(f"{where}: no such page doc/{target}")
                continue
            if frag and frag not in pages[target].ids:
                problems.append(f"{where}: no anchor '#{frag}' in doc/{target}")
    return problems


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def nav_html(nav: list, page: Page, pages: dict) -> str:
    def items(nodes):
        parts = []
        for rel, children in nodes:
            if rel is None:
                label, link = "Other pages", None
            else:
                label = html.escape(pages[rel].nav_label)
                link = rel_url(rel.with_suffix(".html"), page.out.parent)
            cls = ' class="current"' if rel == page.src else ""
            head = f'<a href="{link}"{cls}>{label}</a>' if link else f"<span>{label}</span>"
            sub = f"<ul>{items(children)}</ul>" if children else ""
            parts.append(f"<li>{head}{sub}</li>")
        return "".join(parts)
    return f"<ul>{items(nav)}</ul>"


def toc_html(tokens: list) -> str:
    # Skip the page title (level 1); list levels 2 and 3.
    flat = []

    def walk(ts):
        for t in ts:
            if 2 <= t["level"] <= 3:
                flat.append(t)
            walk(t.get("children", []))
    walk(tokens)
    if len(flat) < 2:
        return ""
    lis = "".join(
        f'<li class="l{t["level"]}"><a href="#{t["id"]}">{html.escape(html.unescape(t["name"]))}</a></li>'
        for t in flat)
    return f'<nav class="toc"><p>On this page</p><ul>{lis}</ul></nav>'


def page_html(page: Page, nav: list, pages: dict, order: list) -> str:
    root = rel_url(PurePosixPath("."), page.out.parent)
    root = "" if root == "." else root + "/"
    i = order.index(page.src) if page.src in order else -1
    pager = []
    if i > 0:
        prev = pages[order[i - 1]]
        pager.append(f'<a class="prev" href="{rel_url(prev.out, page.out.parent)}">&larr; {html.escape(prev.title)}</a>')
    if 0 <= i < len(order) - 1:
        nxt = pages[order[i + 1]]
        pager.append(f'<a class="next" href="{rel_url(nxt.out, page.out.parent)}">{html.escape(nxt.title)} &rarr;</a>')
    home = rel_url(PurePosixPath("index.html"), page.out.parent)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page.title)} &middot; {SITE_NAME}</title>
<link rel="stylesheet" href="{root}style.css">
</head>
<body data-root="{root}">
<header class="topbar">
  <button class="menu" aria-label="Toggle navigation" onclick="document.body.classList.toggle('nav-open')">&#9776;</button>
  <a class="site" href="{home}">{SITE_NAME}</a>
  <div class="search"><input id="search" type="search" placeholder="Search the docs" autocomplete="off" aria-label="Search the documentation"><ol id="results" hidden></ol></div>
</header>
<div class="layout">
<nav class="sidebar">{nav_html(nav, page, pages)}</nav>
<main>
<article>
{page.body}
</article>
<footer>
<div class="pager">{"".join(pager)}</div>
<p class="source">Generated from <code>doc/{page.src}</code>. Edit the Markdown, then run <code>python3 doc/build_docs.py</code>.</p>
</footer>
</main>
{toc_html(page.toc_tokens)}
</div>
<script src="{root}search_index.js"></script>
<script src="{root}search.js"></script>
</body>
</html>
"""


def plain_text(body: str) -> str:
    body = re.sub(r"<pre.*?</pre>", " ", body, flags=re.S)       # code is noise for search
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


def search_index(pages: dict) -> str:
    entries = []
    for page in sorted(pages.values(), key=lambda p: str(p.src)):
        heads = []

        def walk(ts):
            for t in ts:
                if t["level"] >= 2:
                    heads.append([html.unescape(t["name"]), t["id"]])
                walk(t.get("children", []))
        walk(page.toc_tokens)
        entries.append({"u": page.out.as_posix(), "t": page.title, "h": heads,
                        "x": plain_text(page.body)[:6000]})
    return "window.DOC_INDEX = " + json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + ";\n"


STYLE = """\
:root { --bg:#ffffff; --fg:#1d2228; --muted:#5b6672; --line:#dde2e8; --soft:#f4f6f9;
        --accent:#1b5fae; --code:#f2f4f7; --side:#fafbfc; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14181d; --fg:#dde3ea; --muted:#94a0ad; --line:#2c333c; --soft:#1b2027;
          --accent:#6aa8f0; --code:#1e242c; --side:#171b21; } }
* { box-sizing:border-box; }
html { scroll-padding-top:4rem; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.topbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:1rem;
          height:3.2rem; padding:0 1rem; background:var(--bg); border-bottom:1px solid var(--line); }
.topbar .site { font-weight:600; color:var(--fg); white-space:nowrap; }
.menu { display:none; background:none; border:0; font-size:1.3rem; color:var(--fg); cursor:pointer; }
.search { position:relative; margin-left:auto; width:min(26rem,50vw); }
.search input { width:100%; padding:.4rem .7rem; border:1px solid var(--line); border-radius:6px;
                background:var(--soft); color:var(--fg); font:inherit; font-size:.95rem; }
#results { position:absolute; right:0; left:0; top:2.4rem; margin:0; padding:.3rem 0; list-style:none;
           background:var(--bg); border:1px solid var(--line); border-radius:6px;
           box-shadow:0 6px 24px rgba(0,0,0,.15); max-height:70vh; overflow:auto; }
#results li a { display:block; padding:.4rem .8rem; color:var(--fg); }
#results li a:hover, #results li a.sel { background:var(--soft); text-decoration:none; }
#results small { display:block; color:var(--muted); font-size:.8rem; }
.layout { display:grid; grid-template-columns:16rem minmax(0,1fr) 14rem; max-width:96rem; margin:0 auto; }
.sidebar { position:sticky; top:3.2rem; height:calc(100vh - 3.2rem); overflow:auto; padding:1rem .5rem 2rem 1rem;
           background:var(--side); border-right:1px solid var(--line); font-size:.92rem; }
.sidebar ul { list-style:none; margin:0; padding:0; }
.sidebar ul ul { padding-left:.9rem; border-left:1px solid var(--line); margin:.1rem 0 .3rem .3rem; }
.sidebar li { margin:.1rem 0; }
.sidebar a, .sidebar span { display:block; padding:.15rem .4rem; border-radius:4px; color:var(--fg); }
.sidebar span { color:var(--muted); font-weight:600; margin-top:.6rem; }
.sidebar a.current { background:var(--soft); color:var(--accent); font-weight:600; }
main { min-width:0; padding:1.5rem 2.5rem 3rem; }
article { max-width:52rem; }
article h1 { font-size:2rem; margin-top:.3rem; line-height:1.25; }
article h2 { margin-top:2.4rem; padding-bottom:.25rem; border-bottom:1px solid var(--line); }
article h3 { margin-top:1.8rem; }
.headerlink { margin-left:.35rem; opacity:0; font-size:.8em; color:var(--muted); }
h1:hover .headerlink, h2:hover .headerlink, h3:hover .headerlink, h4:hover .headerlink { opacity:1; }
code { font:.88em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--code);
       padding:.1em .3em; border-radius:4px; }
pre { background:var(--code); padding:.8rem 1rem; border-radius:6px; overflow:auto; line-height:1.45; }
pre code { background:none; padding:0; font-size:.86em; }
.table-wrap { overflow-x:auto; margin:1rem 0; }
table { border-collapse:collapse; font-size:.93rem; }
th, td { border:1px solid var(--line); padding:.35rem .6rem; text-align:left; vertical-align:top; }
th { background:var(--soft); }
blockquote { margin:1rem 0; padding:.6rem 1rem; border-left:4px solid var(--accent); background:var(--soft); }
blockquote p:first-child { margin-top:0; } blockquote p:last-child { margin-bottom:0; }
hr { border:0; border-top:1px solid var(--line); margin:2rem 0; }
.toc { position:sticky; top:3.2rem; height:calc(100vh - 3.2rem); overflow:auto; padding:1.6rem 1rem; font-size:.85rem; }
.toc p { margin:0 0 .4rem; font-weight:600; color:var(--muted); text-transform:uppercase; font-size:.75rem; letter-spacing:.04em; }
.toc ul { list-style:none; margin:0; padding:0; }
.toc li { margin:.2rem 0; } .toc li.l3 { padding-left:.8rem; }
.toc a { color:var(--muted); } .toc a:hover { color:var(--accent); }
footer { max-width:52rem; margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line); }
.pager { display:flex; justify-content:space-between; gap:1rem; }
.pager .next { margin-left:auto; text-align:right; }
.source { color:var(--muted); font-size:.82rem; }
@media (max-width:72rem) { .layout { grid-template-columns:16rem minmax(0,1fr); } .toc { display:none; } }
@media (max-width:48rem) {
  .layout { grid-template-columns:minmax(0,1fr); }
  .menu { display:block; }
  .sidebar { display:none; position:fixed; top:3.2rem; left:0; right:0; z-index:9; height:auto; max-height:80vh; }
  body.nav-open .sidebar { display:block; }
  main { padding:1rem; } .topbar .site { display:none; } .search { width:100%; } }
@media print { .topbar, .sidebar, .toc, .pager { display:none; } .layout { display:block; } }
"""

SEARCH_JS = """\
// Client-side search over window.DOC_INDEX (search_index.js). Plain script
// tags rather than fetch(), so it also works on pages opened from disk.
(function () {
  var input = document.getElementById("search"), list = document.getElementById("results");
  var root = document.body.getAttribute("data-root") || "", sel = -1;
  if (!input || !window.DOC_INDEX) return;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function count(hay, needle) { var n = 0, i = hay.indexOf(needle);
    while (i !== -1 && n < 50) { n++; i = hay.indexOf(needle, i + needle.length); } return n; }
  function search(q) {
    var terms = q.toLowerCase().split(/\\s+/).filter(Boolean), out = [];
    if (!terms.length) return out;
    window.DOC_INDEX.forEach(function (p) {
      var title = p.t.toLowerCase(), text = p.x.toLowerCase(), score = 0, best = null, ok = true;
      terms.forEach(function (t) {
        var s = (title.indexOf(t) !== -1 ? 20 : 0) + Math.min(count(text, t), 10);
        p.h.forEach(function (h) { if (h[0].toLowerCase().indexOf(t) !== -1) { s += 8; if (!best) best = h; } });
        if (!s) ok = false; score += s; });
      if (ok) out.push({p: p, s: score, h: best});
    });
    return out.sort(function (a, b) { return b.s - a.s; }).slice(0, 12);
  }
  function render() {
    var hits = search(input.value); sel = -1;
    if (!input.value.trim()) { list.hidden = true; return; }
    list.innerHTML = hits.length ? hits.map(function (r) {
      var url = root + r.p.u + (r.h ? "#" + r.h[1] : "");
      return '<li><a href="' + url + '">' + esc(r.p.t) + (r.h ? "<small>" + esc(r.h[0]) + "</small>" : "") + "</a></li>";
    }).join("") : '<li><a>No match</a></li>';
    list.hidden = false;
  }
  function move(d) { var a = list.querySelectorAll("a[href]"); if (!a.length) return;
    if (sel >= 0) a[sel].classList.remove("sel"); sel = (sel + d + a.length) % a.length; a[sel].classList.add("sel"); }
  input.addEventListener("input", render);
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { move(1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { move(-1); e.preventDefault(); }
    else if (e.key === "Enter") { var a = list.querySelectorAll("a[href]"); if (a.length) location.href = a[Math.max(sel, 0)].href; }
    else if (e.key === "Escape") { list.hidden = true; input.blur(); } });
  document.addEventListener("click", function (e) { if (!e.target.closest(".search")) list.hidden = true; });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input) { input.focus(); e.preventDefault(); } });
})();
"""


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="validate links and nav.txt without writing; exit 1 on any problem")
    args = ap.parse_args()

    pages = discover_pages()
    if not pages:
        print("no Markdown files under doc/", file=sys.stderr)
        return 1
    problems: list = []
    nav = read_nav(pages, problems)
    order = flatten(nav)

    builder = Builder(pages)
    for page in pages.values():
        builder.convert(page)
    problems += check_links(pages)
    unlisted = [p for p in pages if p not in set(flatten([n for n in nav if n[0] is not None]))]

    if args.check:
        for p in problems:
            print("ERROR", p)
        for p in unlisted:
            print(f"note  doc/{p} is not in nav.txt (shown under 'Other pages')")
        print(f"{len(pages)} pages, {sum(len(p.links) for p in pages.values())} links, "
              f"{len(problems)} problem(s)")
        return 1 if problems else 0

    written = set()
    for page in pages.values():
        dest = OUT_DIR / page.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page_html(page, nav, pages, order), encoding="utf-8")
        written.add(dest)
    for name, content in (("style.css", STYLE), ("search.js", SEARCH_JS),
                          ("search_index.js", search_index(pages))):
        (OUT_DIR / name).write_text(content, encoding="utf-8")
        written.add(OUT_DIR / name)

    # Pages whose Markdown was deleted or renamed: remove their stale HTML.
    removed = 0
    for stale in OUT_DIR.rglob("*.html"):
        if stale not in written:
            stale.unlink()
            removed += 1
    for d in sorted((p for p in OUT_DIR.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()

    for p in problems:
        print("WARNING", p)
    for p in unlisted:
        print(f"note    doc/{p} is not in nav.txt (shown under 'Other pages')")
    print(f"Built {len(pages)} page(s) into {OUT_DIR.relative_to(DOC_DIR.parent)}/"
          + (f", removed {removed} stale page(s)" if removed else "")
          + (f"; {len(problems)} link problem(s), run with --check for details" if problems else ""))
    print(f"Open {OUT_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
