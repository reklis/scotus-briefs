"""Generate a zero-JS static HTML site from plain-English case explanations."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .docket import CaseDocket
from .explain import CaseExplanation, load_all_explanations

SITE_TITLE = "SCOTUS Briefs"
SITE_TAGLINE = "A Citizen's Guide to the Supreme Court"
SITE_DOMAIN = "scotusbriefs.us"
SITE_URL = f"https://{SITE_DOMAIN}"

CSS = """\
:root {
  --bg: #0f131a;
  --surface: #171d27;
  --header-bg: #11192a;
  --ink: #f3f5f8;
  --muted: #9aa6b8;
  --border: #273142;
  --accent: #6ea8fe;
  --civic-red: #c8323c;
  --civic-white: #eef2f7;
  --civic-blue: #2b6cb0;
  --pending-bg: #3b2d10;
  --pending-ink: #fcd34d;
  --argued-bg: #172554;
  --argued-ink: #93c5fd;
  --decided-bg: #143321;
  --decided-ink: #86efac;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.65;
}
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
header.site-header {
  background: var(--header-bg);
  border-bottom: 1px solid var(--border);
  position: relative;
}
.civic-bar {
  display: flex;
  height: 4px;
  width: 100%;
}
.civic-bar span:nth-child(1) { flex: 1; background: var(--civic-red); }
.civic-bar span:nth-child(2) { flex: 1; background: var(--civic-white); }
.civic-bar span:nth-child(3) { flex: 1; background: var(--civic-blue); }
.wrap {
  max-width: 840px;
  margin: 0 auto;
  padding: 0 1.5rem;
}
.nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem 1.5rem;
  padding: 1.35rem 0;
}
.brand-group { display: flex; flex-direction: column; gap: 0.1rem; }
.brand {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1.45rem;
  font-weight: 700;
  color: var(--civic-white);
  text-decoration: none;
  letter-spacing: 0.01em;
}
.brand-sub {
  font-size: 0.8rem;
  color: var(--muted);
  letter-spacing: 0.03em;
  text-transform: uppercase;
}
.nav-links { display: flex; gap: 1.5rem; font-size: 0.95rem; }
.nav-links a { color: var(--civic-white); text-decoration: none; opacity: 0.88; }
.nav-links a:hover { opacity: 1; color: var(--accent); text-decoration: underline; }
main.wrap {
  flex: 1 0 auto;
  width: 100%;
  padding: 3.5rem 1.5rem 5.5rem;
}
h1, h2, h3 {
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.25;
  margin-top: 2rem;
  margin-bottom: 0.65rem;
}
h1 { font-size: 2.05rem; margin-top: 0; }
.lead { color: var(--muted); font-size: 1.05rem; margin-top: 0.35rem; margin-bottom: 1.5rem; }
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.9rem;
  font-size: 0.9rem;
  color: var(--muted);
  margin: 0.55rem 0 1.25rem;
}
.badge {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
}
.badge-pending { background: var(--pending-bg); color: var(--pending-ink); }
.badge-argued { background: var(--argued-bg); color: var(--argued-ink); }
.badge-decided { background: var(--decided-bg); color: var(--decided-ink); }
.terms { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 1.25rem 0 2rem; }
.term-pill {
  padding: 0.3rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  text-decoration: none;
  font-size: 0.88rem;
  color: var(--civic-white);
}
.term-pill:hover { border-color: var(--accent); color: var(--accent); }
.search-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin: 1.5rem 0 1rem;
}
.search-bar input[type="search"] {
  flex: 1 1 260px;
  padding: 0.7rem 0.95rem;
  font-size: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
}
.search-bar input[type="search"]:focus {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.search-status {
  font-size: 0.88rem;
  color: var(--muted);
  margin: 0 0 1.25rem;
}
.hidden { display: none; }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.4rem 1.6rem;
  margin-bottom: 1.25rem;
  transition: border-color 0.15s ease;
}
.card:hover { border-color: var(--civic-blue); }
.card h2 { margin: 0 0 0.35rem; font-size: 1.25rem; }
.card h2 a { text-decoration: none; color: var(--ink); }
.card h2 a:hover { color: var(--accent); text-decoration: underline; }
.card p { margin: 0.5rem 0 0; }
.notice {
  background: var(--surface);
  border-left: 3px solid var(--civic-red);
  padding: 0.9rem 1.15rem;
  font-size: 0.9rem;
  color: var(--muted);
  margin: 1.75rem 0;
  border-radius: 0 6px 6px 0;
}
.prose ul { padding-left: 1.35rem; }
.prose li { margin-bottom: 0.5rem; }
details.docket-log {
  margin-top: 2.5rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  padding: 1rem 1.25rem;
}
details.docket-log summary { cursor: pointer; font-weight: 600; }
table.proceedings {
  width: 100%;
  border-collapse: collapse;
  margin-top: 0.9rem;
  font-size: 0.9rem;
}
table.proceedings th, table.proceedings td {
  text-align: left;
  vertical-align: top;
  padding: 0.55rem 0.45rem;
  border-top: 1px solid var(--border);
}
table.proceedings th:first-child, table.proceedings td:first-child {
  white-space: nowrap;
  width: 7.5rem;
  color: var(--muted);
}
footer.site-footer {
  margin-top: 2rem;
  border-top: 1px solid var(--border);
  background: var(--header-bg);
  padding: 2.25rem 0 2.75rem;
  font-size: 0.88rem;
  color: var(--muted);
}
"""


SEARCH_JS = """\
(function () {
  var input = document.getElementById("case-search");
  var status = document.getElementById("search-status");
  var cards = Array.prototype.slice.call(document.querySelectorAll("article.card[data-docket]"));
  if (!input || !cards.length) return;

  var indexByDocket = {};
  cards.forEach(function (card) {
    indexByDocket[card.dataset.docket] = card.textContent.toLowerCase();
  });

  fetch("search-index.json")
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (rows) {
      rows.forEach(function (row) {
        indexByDocket[row.docket] = (
          row.docket + " " + row.title + " " + row.term + " " +
          row.status + " " + row.summary + " " + row.text
        ).toLowerCase();
      });
      if (input.value.trim()) filter();
    })
    .catch(function () {});

  function filter() {
    var raw = input.value.trim().toLowerCase();
    var terms = raw ? raw.split(/\\s+/) : [];
    var shown = 0;
    cards.forEach(function (card) {
      var hay = indexByDocket[card.dataset.docket] || "";
      var match = terms.every(function (t) { return hay.indexOf(t) !== -1; });
      card.classList.toggle("hidden", !match);
      if (match) shown++;
    });
    if (!status) return;
    if (!raw) {
      status.textContent = "";
    } else if (shown === 0) {
      status.textContent = 'No cases match "' + input.value.trim() + '".';
    } else {
      status.textContent = "Showing " + shown + " of " + cards.length + " case(s).";
    }
  }

  input.addEventListener("input", filter);
  var params = new URLSearchParams(window.location.search);
  if (params.get("q")) {
    input.value = params.get("q");
    filter();
  }
})();
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _inline_md(text: str) -> str:
    """Render minimal safe inline Markdown (bold, italic, code, links)."""
    escaped = _esc(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https://[^)\s]+)\)",
        r'<a href="\2" rel="noopener">\1</a>',
        escaped,
    )
    return escaped


def render_markdown(md: str) -> str:
    """Convert simple Markdown (headings, paragraphs, bullet lists) to clean HTML."""
    out: list[str] = []
    in_list = False
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline_md(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw_line in md.splitlines():
        line = raw_line.strip()
        if not line:
            flush_para()
            close_list()
            continue
        if line.startswith("### "):
            flush_para()
            close_list()
            out.append(f"<h3>{_inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            flush_para()
            close_list()
            out.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            flush_para()
            close_list()
            out.append(f"<h2>{_inline_md(line[2:])}</h2>")
        elif line.startswith(("- ", "* ")):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(line[2:])}</li>")
        else:
            close_list()
            para.append(line)

    flush_para()
    close_list()
    return "\n".join(out)


def _ga_snippet(ga_id: str | None) -> str:
    if not ga_id:
        return ""
    safe_id = _esc(ga_id.strip())
    return (
        f'\n  <script async src="https://www.googletagmanager.com/gtag/js?id={safe_id}"></script>'
        f"\n  <script>"
        f"window.dataLayer=window.dataLayer||[];"
        f"function gtag(){{dataLayer.push(arguments);}}"
        f"gtag('js',new Date());gtag('config','{safe_id}');"
        f"</script>"
    )


def _layout(
    *,
    title: str,
    body: str,
    depth: int = 0,
    scripts: list[str] | None = None,
    ga_id: str | None = None,
) -> str:
    prefix = "../" * depth
    script_tags = "".join(f'\n  <script src="{prefix}{s}" defer></script>' for s in (scripts or []))
    ga_tag = _ga_snippet(ga_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)} — {SITE_TITLE}</title>
  <link rel="stylesheet" href="{prefix}style.css">
  <link rel="alternate" type="application/atom+xml" title="{SITE_TITLE}" href="{prefix}feed.xml">{ga_tag}{script_tags}
</head>
<body>
  <header class="site-header">
    <div class="civic-bar" aria-hidden="true"><span></span><span></span><span></span></div>
    <div class="wrap nav">
      <div class="brand-group">
        <a class="brand" href="{prefix}index.html">{SITE_TITLE}</a>
        <span class="brand-sub">{SITE_TAGLINE}</span>
      </div>
      <nav class="nav-links">
        <a href="{prefix}index.html">Cases</a>
        <a href="{prefix}about.html">About</a>
        <a href="{prefix}feed.xml">Feed</a>
      </nav>
    </div>
  </header>
  <main class="wrap">
{body}
  </main>
  <footer class="site-footer">
    <div class="wrap">
      {SITE_TITLE} — {SITE_TAGLINE}. AI-generated plain-English summaries linked to official
      <a href="https://www.supremecourt.gov" rel="noopener">supremecourt.gov</a> filings. Not legal advice.
    </div>
  </footer>
</body>
</html>
"""


def _status_badge(status: str) -> str:
    s = status.lower() if status.lower() in {"pending", "argued", "decided"} else "pending"
    return f'<span class="badge badge-{s}">{_esc(s)}</span>'


def _case_card(exp: CaseExplanation, *, depth: int = 0) -> str:
    prefix = "../" * depth
    return (
        f'<article class="card" data-docket="{_esc(exp.docket)}">'
        f'<h2><a href="{prefix}cases/{_esc(exp.docket)}.html">{_esc(exp.title)}</a></h2>'
        f'<div class="meta">'
        f'{_status_badge(exp.status)}'
        f'<span>No. {_esc(exp.docket)}</span>'
        f'<span>OT {exp.term}</span>'
        f'</div>'
        f'<p>{_esc(exp.summary)}</p>'
        f'</article>'
    )


def _proceedings_html(docket: CaseDocket | None) -> str:
    if docket is None or not docket.proceedings:
        return ""
    rows: list[str] = []
    for proc in reversed(docket.proceedings):
        links_html = ""
        if proc.links:
            anchors = " · ".join(
                f'<a href="{_esc(lk.url)}" rel="noopener">{_esc(lk.label)}</a>'
                for lk in proc.links
            )
            links_html = f"<div>{anchors}</div>"
        rows.append(
            f"<tr><td>{_esc(proc.date)}</td><td>{_esc(proc.text)}{links_html}</td></tr>"
        )
    return (
        f'<details class="docket-log">'
        f'<summary>Official Docket Proceedings ({len(docket.proceedings)} entries)</summary>'
        f'<table class="proceedings">'
        f'<thead><tr><th>Date</th><th>Proceeding &amp; Filings</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        f'</table>'
        f'</details>'
    )


def _build_atom_feed(explanations: list[CaseExplanation]) -> str:
    updated = (
        explanations[0].generated_at
        if explanations
        else datetime.now(UTC).isoformat(timespec="seconds")
    )
    entries: list[str] = []
    for exp in explanations[:30]:
        url = f"{SITE_URL}/cases/{exp.docket}.html"
        entries.append(
            f"  <entry>\n"
            f"    <id>{_esc(url)}</id>\n"
            f"    <title>{_esc(exp.title)} (No. {_esc(exp.docket)})</title>\n"
            f"    <link href=\"{_esc(url)}\"/>\n"
            f"    <updated>{_esc(exp.generated_at)}</updated>\n"
            f"    <summary>{_esc(exp.summary)}</summary>\n"
            f"  </entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{SITE_TITLE} — {SITE_TAGLINE}</title>\n"
        f'  <link href="{SITE_URL}/feed.xml" rel="self"/>\n'
        f'  <link href="{SITE_URL}/"/>\n'
        f"  <id>{SITE_URL}/</id>\n"
        f"  <updated>{_esc(updated)}</updated>\n"
        + "\n".join(entries)
        + "\n</feed>\n"
    )


def build_site(
    *,
    root: Path = Path("."),
    out_dir: Path | None = None,
    cname: str = SITE_DOMAIN,
    ga_id: str | None = None,
) -> int:
    """Build the complete static HTML site into dist/. Returns count of case pages built."""
    resolved_ga = ga_id if ga_id is not None else os.environ.get("GA_MEASUREMENT_ID", "").strip()
    dest = out_dir or (root / "dist")
    (dest / "cases").mkdir(parents=True, exist_ok=True)
    (dest / "terms").mkdir(parents=True, exist_ok=True)

    explanations = load_all_explanations(root)
    dockets_by_id: dict[str, CaseDocket] = {}
    docket_dir = root / "data" / "docket"
    if docket_dir.exists():
        for p in docket_dir.glob("*.json"):
            d = CaseDocket.from_dict(json.loads(p.read_text(encoding="utf-8")))
            dockets_by_id[d.docket] = d

    (dest / "style.css").write_text(CSS, encoding="utf-8")
    (dest / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    if cname:
        (dest / "CNAME").write_text(f"{cname}\n", encoding="utf-8")

    # Search index (full plain-text guide content for instant client-side search)
    search_rows = [
        {
            "docket": e.docket,
            "title": e.title,
            "term": e.term,
            "status": e.status,
            "summary": e.summary,
            "text": re.sub(r"[#*`_]+", " ", e.markdown),
        }
        for e in explanations
    ]
    (dest / "search-index.json").write_text(
        json.dumps(search_rows, ensure_ascii=False),
        encoding="utf-8",
    )

    # Group by term
    terms: dict[int, list[CaseExplanation]] = {}
    for exp in explanations:
        terms.setdefault(exp.term, []).append(exp)
    sorted_terms = sorted(terms.keys(), reverse=True)

    term_pills = "".join(
        f'<a class="term-pill" href="terms/{t}.html">OT {t} ({len(terms[t])})</a>'
        for t in sorted_terms
    )

    # Home page
    if explanations:
        cards = "\n".join(_case_card(e, depth=0) for e in explanations)
    else:
        cards = (
            '<div class="card"><p>No cases have been explained yet. '
            "Run <code>scotus run</code> to monitor the docket and generate plain-English guides.</p></div>"
        )

    search_box = (
        '<div class="search-bar">'
        '<input id="case-search" type="search" '
        'placeholder="Search cases by name, docket, topic, or legal term…" '
        'aria-label="Search cases" autocomplete="off">'
        "</div>\n"
        '<p id="search-status" class="search-status" aria-live="polite"></p>\n'
    )

    index_body = (
        f"<h1>{SITE_TAGLINE}</h1>\n"
        f'<p class="lead">Plain-English explanations of Supreme Court cases, '
        f"built directly from official docket filings, oral arguments, and opinions.</p>\n"
        + search_box
        + (f'<div class="terms">{term_pills}</div>\n' if term_pills else "")
        + cards
    )
    (dest / "index.html").write_text(
        _layout(title="Cases", body=index_body, depth=0, scripts=["search.js"], ga_id=resolved_ga),
        encoding="utf-8",
    )

    # Term pages
    for t in sorted_terms:
        t_cards = "\n".join(_case_card(e, depth=1) for e in terms[t])
        t_body = (
            f"<h1>October Term {t}</h1>\n"
            f'<p class="lead">{len(terms[t])} explained case(s).</p>\n'
            f"{t_cards}"
        )
        (dest / "terms" / f"{t}.html").write_text(
            _layout(title=f"OT {t}", body=t_body, depth=1, ga_id=resolved_ga),
            encoding="utf-8",
        )

    # Individual case pages
    for exp in explanations:
        docket_obj = dockets_by_id.get(exp.docket)
        official_link = (
            f'<a href="{_esc(exp.docket_url)}" rel="noopener">Official Supreme Court Docket</a>'
            if exp.docket_url
            else ""
        )
        case_body = (
            f"<h1>{_esc(exp.title)}</h1>\n"
            f'<div class="meta">'
            f'{_status_badge(exp.status)}'
            f'<span>Docket No. {_esc(exp.docket)}</span>'
            f'<span>OT {exp.term}</span>'
            f"{official_link}"
            f"</div>\n"
            f'<div class="notice">'
            f"AI-generated plain-English guide (model: <code>{_esc(exp.model)}</code>, "
            f"updated {_esc(exp.generated_at[:10])}). Not legal advice — verify against the official filings below."
            f"</div>\n"
            f'<article class="prose">\n{render_markdown(exp.markdown)}\n</article>\n'
            f"{_proceedings_html(docket_obj)}"
        )
        (dest / "cases" / f"{exp.docket}.html").write_text(
            _layout(title=f"{exp.title} (No. {exp.docket})", body=case_body, depth=1, ga_id=resolved_ga),
            encoding="utf-8",
        )

    # About page
    about_body = """\
<h1>About SCOTUS Briefs</h1>
<p><strong>SCOTUS Briefs</strong> is an independent civic project that monitors the U.S. Supreme Court
docket and turns primary legal filings — petitions, briefs, orders, oral argument transcripts, and
opinions — into plain-English explanations for everyday citizens.</p>
<h2>How it works</h2>
<ul>
  <li><strong>Monitor:</strong> Tracks official docket pages and filings from
  <a href="https://www.supremecourt.gov" rel="noopener">supremecourt.gov</a>.</li>
  <li><strong>Explain:</strong> Passes the docket timeline and filing text to a language model
  prompted to write clearly, neutrally, and without unexplained legal jargon.</li>
  <li><strong>Publish:</strong> Generates this fast, zero-JavaScript static site with direct links
  to the Court's official documents so you can always read the primary source.</li>
</ul>
<h2>Disclaimer</h2>
<p>These guides are AI-generated for civic education. They may contain mistakes or omissions, are
<strong>not legal advice</strong>, and do not replace the Court's official orders, transcripts, or opinions.</p>
"""
    (dest / "about.html").write_text(
        _layout(title="About", body=about_body, depth=0, ga_id=resolved_ga),
        encoding="utf-8",
    )

    # Atom feed
    (dest / "feed.xml").write_text(_build_atom_feed(explanations), encoding="utf-8")

    return len(explanations)
