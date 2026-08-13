#!/usr/bin/env python3
# =============================================================================
# build_docs_site.py — Generate the GitHub Pages documentation site.
#
# Renders every repo markdown doc to a styled HTML page under pages/
# (mirroring the repo layout) and generates the root index.html portal.
# Static output — no Jekyll required (.nojekyll is written at the root).
#
# Usage:  venv/bin/python tools/build_docs_site.py
# =============================================================================
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = REPO_ROOT / "pages"

# ── Doc inventory, organized by portal section ────────────────────────────────
# (source path relative to repo root, section)
DOCS: list[tuple[str, str]] = [
    # Getting started
    ("README.md", "Getting Started"),
    ("docs/Instructions.md", "Getting Started"),
    ("docs/QUICK_REFERENCE.md", "Getting Started"),
    # Architecture & internals
    ("docs/ARCHITECTURE_DIAGRAMS.md", "Architecture & Internals"),
    ("docs/CODEBASE_ANALYSIS.md", "Architecture & Internals"),
    ("docs/DOCUMENTATION_INDEX.md", "Architecture & Internals"),
    ("docs/known-issues.md", "Architecture & Internals"),
    # Agents
    ("agents/employee-support-copilot/README.md", "Agents"),
    # Deploy — Google Cloud
    ("deployment/google/README.md", "Deploy — Google Cloud"),
    ("deployment/google/agent_runtime/README.md", "Deploy — Google Cloud"),
    ("deployment/google/cloud_run/README.md", "Deploy — Google Cloud"),
    ("deployment/google/agents/README.md", "Deploy — Google Cloud"),
    # Deploy — AWS
    ("deployment/aws/README.md", "Deploy — AWS"),
    ("deployment/aws/ecs/README.md", "Deploy — AWS"),
    ("deployment/aws/agentcore/README.md", "Deploy — AWS"),
    ("deployment/aws/s3-ui/README.md", "Deploy — AWS"),
    ("deployment/aws/amplify/README.md", "Deploy — AWS"),
    # Deploy — Azure
    ("deployment/azure/DEPLOYMENT.md", "Deploy — Azure"),
    # Components & testing
    ("searchaas/ui_react/README.md", "Components & Testing"),
    ("searchaas/tests/README.md", "Components & Testing"),
    ("searchaas/tests/LOAD_TEST_REPORT.md", "Components & Testing"),
]

SECTION_ORDER = [
    "Getting Started",
    "Architecture & Internals",
    "Agents",
    "Deploy — Google Cloud",
    "Deploy — AWS",
    "Deploy — Azure",
    "Components & Testing",
]

MD_EXTS = ["fenced_code", "tables", "toc", "sane_lists", "codehilite"]
MD_EXT_CFG = {"codehilite": {"guess_lang": False, "css_class": "highlight"}}


def out_path_for(src: str) -> str:
    """pages/ output path mirroring the repo path, .md → .html."""
    return re.sub(r"\.md$", ".html", src, flags=re.I)


def extract_title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", md_text, flags=re.M)
    if m:
        return re.sub(r"[*_`]", "", m.group(1)).strip()
    return fallback


def extract_blurb(md_text: str) -> str:
    """First non-heading, non-fence paragraph, markdown stripped, truncated."""
    in_fence = False
    for para in re.split(r"\n\s*\n", md_text):
        p = para.strip()
        if p.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not p or p.startswith(("#", "|", "<", "-", "*", ">")):
            continue
        p = re.sub(r"!?\[([^]]*)\]\([^)]*\)", r"\1", p)  # links/images → text
        p = re.sub(r"[*_`#]", "", p)
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) > 30:
            return p[:200].rstrip() + ("…" if len(p) > 200 else "")
    return ""


def github_blob_base() -> str:
    """https://github.com/<org>/<repo>/blob/main derived from the git remote."""
    import subprocess
    url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return ""
    return f"https://github.com/{m.group(1)}/{m.group(2)}/blob/main"


GITHUB_BASE = github_blob_base()


def rewrite_links(md_text: str, src: str) -> str:
    """Relative .md links → .html (site-internal); other relative file links →
    the GitHub blob view (scripts, source files, configs aren't in the site)."""
    import posixpath
    src_dir = posixpath.dirname(src)

    def repl(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if re.match(r"^[a-z]+://|^mailto:|^#", url):
            return m.group(0)
        path, _, anchor = url.partition("#")
        suffix = "#" + anchor if anchor else ""
        if path.endswith(".md"):
            path = path[:-3] + ".html"
            return f"[{label}]({path}{suffix})"
        if GITHUB_BASE and path:
            repo_rel = posixpath.normpath(posixpath.join(src_dir, path))
            if (REPO_ROOT / repo_rel).exists():
                return f"[{label}]({GITHUB_BASE}/{repo_rel})"
        return m.group(0)
    return re.sub(r"\[([^\]]*)\]\(([^)]+)\)", repl, md_text)


def render_page(src: str, md_text: str, title: str) -> str:
    # Extract mermaid fences BEFORE rendering (codehilite would strip the
    # language marker); substitute back as <pre class="mermaid"> afterwards.
    mermaid_blocks: list[str] = []
    def _stash(m: re.Match) -> str:
        mermaid_blocks.append(m.group(1))
        return f"\n\n@@MERMAID{len(mermaid_blocks) - 1}@@\n\n"
    md_text = re.sub(r"```mermaid\s*\n(.*?)```", _stash, md_text, flags=re.S)

    body = markdown.markdown(md_text, extensions=MD_EXTS, extension_configs=MD_EXT_CFG)

    def _restore(m: re.Match) -> str:
        block = mermaid_blocks[int(m.group(1))]
        # Escape for HTML; the browser decodes entities when mermaid.js reads
        # textContent, so the diagram source survives intact.
        return '<pre class="mermaid">' + html.escape(block) + "</pre>"
    body = re.sub(r"(?:<p>)?@@MERMAID(\d+)@@(?:</p>)?", _restore, body)
    depth = out_path_for(src).count("/") + 1  # pages/<path> depth below root
    rel = "../" * depth
    return PAGE_TMPL.format(rel=rel, title=html.escape(title), body=body)


PAGE_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · SearchaaS Docs</title>
<link rel="stylesheet" href="{rel}pages/assets/style.css">
<link rel="stylesheet" href="{rel}pages/assets/pygments.css">
</head>
<body>
<header class="topbar">
  <a class="brand" href="{rel}index.html">SearchaaS Docs</a>
  <a class="back" href="{rel}index.html">← All docs</a>
</header>
<main class="content">
{body}
</main>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>if (window.mermaid) mermaid.initialize({{startOnLoad: true, theme: "neutral"}});</script>
</body>
</html>
"""


def build() -> None:
    entries = []
    for src, section in DOCS:
        path = REPO_ROOT / src
        if not path.exists():
            print(f"[skip] missing: {src}")
            continue
        text = path.read_text(encoding="utf-8")
        title = extract_title(text, Path(src).stem)
        blurb = extract_blurb(text)
        out = PAGES_DIR / out_path_for(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_page(src, rewrite_links(text, src), title), encoding="utf-8")
        entries.append({"src": src, "section": section, "title": title,
                        "blurb": blurb, "href": "pages/" + out_path_for(src)})
        print(f"[page] {src} → {out.relative_to(REPO_ROOT)}")

    (REPO_ROOT / ".nojekyll").touch()
    (REPO_ROOT / "index.html").write_text(render_index(entries), encoding="utf-8")
    print(f"[done] index.html + {len(entries)} pages")

    # Validate internal links across all generated HTML.
    broken = []
    for f in [REPO_ROOT / "index.html", *PAGES_DIR.rglob("*.html")]:
        for href in re.findall(r'href="([^"]+)"', f.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+://|^mailto:|^#", href):
                continue
            target = (f.parent / href.split("#")[0]).resolve()
            if href and not target.exists():
                broken.append(f"{f.relative_to(REPO_ROOT)} → {href}")
    if broken:
        print(f"[warn] {len(broken)} broken internal links:")
        for b in dict.fromkeys(broken):
            print("   ", b)
    else:
        print("[ok] all internal links resolve")


def render_index(entries: list[dict]) -> str:
    sections_html = []
    for section in SECTION_ORDER:
        cards = [e for e in entries if e["section"] == section]
        if not cards:
            continue
        card_html = "\n".join(
            f'''      <a class="card" href="{e["href"]}" data-title="{html.escape(e["title"].lower())}">
        <h3>{html.escape(e["title"])}</h3>
        <p class="path">{e["src"]}</p>
        <p class="blurb">{html.escape(e["blurb"])}</p>
      </a>''' for e in cards)
        sections_html.append(f'  <section>\n    <h2>{section}</h2>\n    <div class="grid">\n{card_html}\n    </div>\n  </section>')
    return INDEX_TMPL.format(count=len(entries))


INDEX_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SearchaaS — One retrieval platform. Every cloud. Every agent.</title>
<meta name="description" content="SearchaaS — MongoDB Atlas retrieval platform: architecture, agents, and deployment guides for Google Cloud, AWS, and Azure.">
<link rel="stylesheet" href="pages/assets/style.css">
<link rel="stylesheet" href="pages/assets/landing.css">
<script src="pages/assets/landing.js" defer></script>
</head>
<body>

<header class="lnav">
  <a class="logo" href="index.html"><span class="leaf">◈</span> SearchaaS</a>
  <nav>
    <a href="#architecture">Architecture</a>
    <a href="#agents">Agents</a>
    <a href="#clouds">Clouds</a>
    <a href="#docs">Docs</a>
    <a href="pages/demo.html">Playground</a>
  </nav>
  <a class="cta" href="pages/README.html">Get Started</a>
</header>

<!-- ── Hero + chatbot demo ──────────────────────────────────────────────── -->
<section class="lhero">
  <div class="wrap">
    <div>
      <p class="kicker">MongoDB Atlas Retrieval Platform</p>
      <h1>One retrieval platform. <em>Every cloud. Every agent.</em></h1>
      <p class="sub">SearchaaS turns MongoDB Atlas into an AI-planned search service —
         vector, full-text, hybrid, graph, parent-doc and metadata retrieval with query
         understanding and grounded summarization, exposed over REST and MCP on
         Google Cloud, AWS, and Azure.</p>
      <div class="actions">
        <a class="btn btn-primary" href="pages/demo.html">▶ Try the connection playground</a>
        <a class="btn btn-ghost" href="#architecture">Explore the architecture</a>
        <a class="btn btn-ghost" href="#docs">Read the docs ({count})</a>
      </div>
      <div class="stats">
        <div class="stat"><b>6</b><span>retrieval strategies</span></div>
        <div class="stat"><b>2</b><span>API surfaces · REST + MCP</span></div>
        <div class="stat"><b>3</b><span>clouds, one architecture</span></div>
        <div class="stat"><b>1</b><span>MongoDB Atlas backend</span></div>
      </div>
    </div>

    <!-- Live-style mock of the Employee Support Copilot agent -->
    <div class="chatdemo" aria-label="Chatbot demo">
      <div class="cd-head"><span class="dot"></span> Employee Support Copilot
        <span class="sub">powered by SearchaaS</span></div>
      <div class="cd-body" id="cd-body"></div>
      <div class="cd-suggests">
        <button type="button" data-ask="pto">How many PTO days do I get?</button>
        <button type="button" data-ask="vpn">VPN keeps disconnecting</button>
        <button type="button" data-ask="payroll">Update direct deposit</button>
      </div>
      <div class="cd-note">Demo: the agent classifies the domain, calls SearchaaS
        <code>/retrieve</code> with per-collection Atlas overrides, and renders a grounded answer.
        <a href="pages/demo.html" style="color:var(--mdb-forest);font-weight:700">Full interactive connection playground →</a></div>
    </div>
  </div>
</section>

<!-- ── Clickable architecture ───────────────────────────────────────────── -->
<section class="lsec" id="architecture">
  <p class="kicker">How SearchaaS works</p>
  <h2>The retrieval pipeline, end to end</h2>
  <p class="lead">Every box is clickable and jumps to the doc that covers it. A query flows
     left to right: an agent calls REST or MCP, the factory pipeline understands, plans and
     retrieves, and MongoDB Atlas does the heavy lifting.</p>
  <div class="archwrap">
  <svg viewBox="0 0 1160 520" role="img" aria-label="SearchaaS architecture diagram">
    <defs>
      <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="#9aa4ad"/></marker>
      <marker id="arrp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="#b39ddb"/></marker>
      <marker id="arrg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="#00684a"/></marker>
    </defs>

    <text class="achip-label" x="10" y="20">AGENTS &amp; APPS</text>
    <text class="achip-label" x="275" y="20">API SURFACES</text>
    <text class="achip-label" x="530" y="20">SEARCHAAS CORE — FACTORY PIPELINE</text>
    <text class="achip-label" x="930" y="20">DATA &amp; MODELS</text>

    <!-- edges -->
    <path class="aedge" d="M215 76 H245 V114 H275"/>
    <path class="aedge" d="M215 160 H245 V130 H275"/>
    <path class="aedge" d="M215 248 H245 V222 H275"/>
    <path class="aedge" d="M470 122 H510 V89 H550"/>
    <path class="aedge" d="M470 222 H510 V272 H550"/>
    <path class="aedge" d="M700 116 V138"/>
    <path class="aedge" d="M700 192 V214"/>
    <path class="aedge" d="M700 330 V352"/>
    <path class="aedge" d="M850 240 H897 V150 H930"/>
    <path class="aedge dash" d="M930 326 H905 V379 H850"/>
    <path class="aedge resp" d="M700 406 V462 H112 V284"/>

    <!-- Column 1: agents & apps -->
    <a class="anode" href="pages/agents/employee-support-copilot/README.html">
      <rect x="10" y="40" width="205" height="72" rx="10"/>
      <text class="t" x="24" y="66">Employee Support Copilot</text>
      <text class="s" x="24" y="84">Next.js chat + BFF /api/chat</text>
      <text class="s" x="24" y="99">domain router · citations UI</text>
    </a>
    <a class="anode" href="pages/searchaas/ui_react/README.html">
      <rect x="10" y="132" width="205" height="56" rx="10"/>
      <text class="t" x="24" y="158">Retrieval Tester UI</text>
      <text class="s" x="24" y="176">React + Vite · pipeline viewer</text>
    </a>
    <a class="anode" href="pages/docs/QUICK_REFERENCE.html">
      <rect x="10" y="212" width="205" height="72" rx="10"/>
      <text class="t" x="24" y="238">Any MCP Agent</text>
      <text class="s" x="24" y="256">Agent Engine · AgentCore ·</text>
      <text class="s" x="24" y="271">AI Foundry · Claude</text>
    </a>

    <!-- Column 2: API surfaces -->
    <a class="anode" href="pages/README.html">
      <rect x="275" y="90" width="195" height="64" rx="10"/>
      <text class="t" x="289" y="116">FastAPI REST</text>
      <text class="s" x="289" y="134">POST /retrieve</text>
      <text class="s" x="289" y="148">/retrieve/&#123;strategy&#125;</text>
    </a>
    <a class="anode" href="pages/docs/QUICK_REFERENCE.html">
      <rect x="275" y="190" width="195" height="64" rx="10"/>
      <text class="t" x="289" y="216">FastMCP Tools</text>
      <text class="s" x="289" y="234">auto_search · vector_search</text>
      <text class="s" x="289" y="248">hybrid_search · graph_search …</text>
    </a>

    <!-- Column 3: core pipeline -->
    <rect x="530" y="40" width="340" height="380" rx="14" fill="#fafcfb" stroke="#b6e9cd" stroke-dasharray="6 4"/>
    <a class="anode core" href="pages/docs/Instructions.html">
      <rect x="550" y="62" width="300" height="54" rx="10"/>
      <text class="t" x="564" y="88">1 · Query Understanding</text>
      <text class="s" x="564" y="105">rewrite · entities · intent · typed facts</text>
    </a>
    <a class="anode core" href="pages/docs/Instructions.html">
      <rect x="550" y="138" width="300" height="54" rx="10"/>
      <text class="t" x="564" y="164">2 · Retrieval Planner</text>
      <text class="s" x="564" y="181">LLM strategy pick · Atlas guardrails</text>
    </a>
    <a class="anode core" href="pages/docs/ARCHITECTURE_DIAGRAMS.html">
      <rect x="550" y="214" width="300" height="116" rx="10"/>
      <text class="t" x="564" y="240">3 · Retriever Factory</text>
    </a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="564" y="252" width="88" height="24" rx="12"/><text x="608" y="268" text-anchor="middle">vector</text></a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="660" y="252" width="88" height="24" rx="12"/><text x="704" y="268" text-anchor="middle">fulltext</text></a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="756" y="252" width="88" height="24" rx="12"/><text x="800" y="268" text-anchor="middle">hybrid</text></a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="564" y="286" width="88" height="24" rx="12"/><text x="608" y="302" text-anchor="middle">graph</text></a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="660" y="286" width="88" height="24" rx="12"/><text x="704" y="302" text-anchor="middle">parent-doc</text></a>
    <a class="chip" href="pages/docs/ARCHITECTURE_DIAGRAMS.html"><rect x="756" y="286" width="88" height="24" rx="12"/><text x="800" y="302" text-anchor="middle">metadata</text></a>
    <a class="anode core" href="pages/README.html">
      <rect x="550" y="352" width="300" height="54" rx="10"/>
      <text class="t" x="564" y="378">4 · Grounded Summary</text>
      <text class="s" x="564" y="395">answer · citations · timings</text>
    </a>

    <!-- Column 4: data & models -->
    <a class="anode data" href="pages/README.html">
      <rect x="930" y="62" width="220" height="190" rx="10"/>
      <text class="t" x="944" y="88">MongoDB Atlas</text>
      <text class="s" x="944" y="110">collections &amp; indexes</text>
      <text class="s" x="944" y="126">$vectorSearch (ANN)</text>
      <text class="s" x="944" y="142">$search · $rankFusion</text>
      <text class="s" x="944" y="158">$graphLookup · $match</text>
      <text class="s" x="944" y="174">AutoEmbeddings (autoEmbed)</text>
      <text class="s" x="944" y="190">policy store (guardrails)</text>
    </a>
    <a class="anode llm" href="pages/docs/Instructions.html">
      <rect x="930" y="292" width="220" height="68" rx="10"/>
      <text class="t" x="944" y="318">LLM Providers</text>
      <text class="s" x="944" y="336">Gemini · OpenAI · Azure OpenAI</text>
      <text class="s" x="944" y="351">Anthropic · Bedrock</text>
    </a>

    <!-- edge labels (painted last so nodes never cover them) -->
    <text class="aelabel" x="893" y="130" text-anchor="middle">aggregation</text>
    <text class="aelabel" x="893" y="142" text-anchor="middle">pipeline</text>
    <text class="aelabel" x="877" y="395" text-anchor="middle">LLM calls</text>
    <text class="aelabel" x="400" y="454" text-anchor="middle">grounded response — answer · citations · timings · real Atlas pipeline</text>
  </svg>
  </div>
  <div class="archlegend">
    <span><i></i> request flow</span>
    <span><i class="dash"></i> LLM calls (understand · plan · summarize)</span>
    <span><i class="resp"></i> grounded response back to the agent</span>
  </div>
</section>

<!-- ── How agents interact ──────────────────────────────────────────────── -->
<div class="lsec-alt">
<section class="lsec" id="agents">
  <p class="kicker">Agents × SearchaaS</p>
  <h2>How an agent answers a question</h2>
  <p class="lead">The Employee Support Copilot is the reference agent: five steps from
     user question to grounded, cited answer — each step links to its doc.</p>
  <div class="steps">
    <a class="step" href="pages/agents/employee-support-copilot/README.html">
      <span class="n">1</span><h3>Ask in chat</h3>
      <p>User asks in the Next.js UI; the BFF receives <code>POST /api/chat</code>.</p>
    </a>
    <a class="step" href="pages/agents/employee-support-copilot/README.html">
      <span class="n">2</span><h3>Classify domain</h3>
      <p>Keyword classifier routes to <code>IT_helpdesk</code> or <code>employee_support</code>.</p>
    </a>
    <a class="step" href="pages/README.html">
      <span class="n">3</span><h3>Call SearchaaS</h3>
      <p><code>POST /retrieve</code> with per-request <code>atlas</code> overrides — collection, indexes, weights.</p>
    </a>
    <a class="step" href="pages/docs/ARCHITECTURE_DIAGRAMS.html">
      <span class="n">4</span><h3>Understand → plan → retrieve</h3>
      <p>Query understanding + LLM planner pick a strategy; Atlas executes the pipeline.</p>
    </a>
    <a class="step" href="pages/agents/employee-support-copilot/README.html">
      <span class="n">5</span><h3>Ground &amp; cite</h3>
      <p>Answer rendered with domain badge, strategy chip, timings and citations.</p>
    </a>
  </div>
</section>
</div>

<!-- ── One architecture, three clouds ───────────────────────────────────── -->
<section class="lsec" id="clouds">
  <p class="kicker">Multi-cloud by design</p>
  <h2>One generic architecture — Google Cloud, AWS, Azure</h2>
  <p class="lead">The request path is identical on every provider; only the managed service
     names change. Pick a cloud to see the mapping and its deployment guides.</p>

  <div class="cloudtabs" role="tablist" aria-label="Cloud providers">
    <button type="button" role="tab" aria-selected="true"  aria-controls="cp-gcp"   id="tab-gcp">Google Cloud</button>
    <button type="button" role="tab" aria-selected="false" aria-controls="cp-aws"   id="tab-aws">AWS</button>
    <button type="button" role="tab" aria-selected="false" aria-controls="cp-azure" id="tab-azure">Azure</button>
  </div>

  <div class="cloudpanel active" id="cp-gcp" role="tabpanel" aria-labelledby="tab-gcp">
    <div class="clouddiag">
      <div class="clayer"><span class="lbl">Experience</span><span class="svc">Cloud Run</span>
        <span class="desc">Employee Support Copilot — Next.js chat + BFF</span></div>
      <div class="clayer"><span class="lbl">Agent runtime</span><span class="svc">Vertex AI Agent Engine</span>
        <span class="desc">Managed serverless agent host (Reasoning Engine)</span></div>
      <div class="clayer"><span class="lbl">SearchaaS runtime</span><span class="svc">Cloud Run</span>
        <span class="desc">FastAPI REST + FastMCP containers · Secret Manager</span></div>
      <div class="clayer const"><span class="lbl">Data — unchanged</span><span class="svc">MongoDB Atlas</span>
        <span class="desc">Same collections, indexes &amp; guardrails on every cloud</span></div>
    </div>
    <div class="cloudlinks">
      <a href="pages/deployment/google/README.html">Overview</a>
      <a href="pages/deployment/google/cloud_run/README.html">Cloud Run</a>
      <a href="pages/deployment/google/agent_runtime/README.html">Vertex AI Agent Engine</a>
      <a href="pages/deployment/google/agents/README.html">Copilot on Cloud Run</a>
    </div>
  </div>

  <div class="cloudpanel" id="cp-aws" role="tabpanel" aria-labelledby="tab-aws">
    <div class="clouddiag">
      <div class="clayer"><span class="lbl">Experience</span><span class="svc">Amplify + S3</span>
        <span class="desc">Copilot on Amplify Hosting · tester UI as static site</span></div>
      <div class="clayer"><span class="lbl">Agent runtime</span><span class="svc">Bedrock AgentCore</span>
        <span class="desc">AgentCore Runtime hosts the FastMCP surface</span></div>
      <div class="clayer"><span class="lbl">SearchaaS runtime</span><span class="svc">ECS Express</span>
        <span class="desc">FastAPI REST + FastMCP services · Secrets Manager</span></div>
      <div class="clayer const"><span class="lbl">Data — unchanged</span><span class="svc">MongoDB Atlas</span>
        <span class="desc">Same collections, indexes &amp; guardrails on every cloud</span></div>
    </div>
    <div class="cloudlinks">
      <a href="pages/deployment/aws/README.html">Overview</a>
      <a href="pages/deployment/aws/ecs/README.html">ECS Express</a>
      <a href="pages/deployment/aws/agentcore/README.html">Bedrock AgentCore</a>
      <a href="pages/deployment/aws/amplify/README.html">Copilot on Amplify</a>
      <a href="pages/deployment/aws/s3-ui/README.html">UI on S3</a>
    </div>
  </div>

  <div class="cloudpanel" id="cp-azure" role="tabpanel" aria-labelledby="tab-azure">
    <div class="clouddiag">
      <div class="clayer"><span class="lbl">Experience</span><span class="svc">Container Apps</span>
        <span class="desc">Employee Support Copilot — Next.js chat + BFF</span></div>
      <div class="clayer"><span class="lbl">Agent runtime</span><span class="svc">AI Foundry</span>
        <span class="desc">Foundry agent wired to the MCP endpoint</span></div>
      <div class="clayer"><span class="lbl">SearchaaS runtime</span><span class="svc">Container Apps</span>
        <span class="desc">FastAPI REST + FastMCP containers · Key Vault</span></div>
      <div class="clayer const"><span class="lbl">Data — unchanged</span><span class="svc">MongoDB Atlas</span>
        <span class="desc">Same collections, indexes &amp; guardrails on every cloud</span></div>
    </div>
    <div class="cloudlinks">
      <a href="pages/deployment/azure/DEPLOYMENT.html">Container Apps + AI Foundry</a>
    </div>
  </div>
</section>

<!-- ── Documentation portal ─────────────────────────────────────────────── -->
<div class="lsec-alt">
<section class="lsec" id="docs">
  <div class="docshead">
    <div>
      <p class="kicker">Documentation</p>
      <h2 style="margin:0">All docs ({count})</h2>
    </div>
    <input id="filter" type="search" placeholder="Filter docs…" autocomplete="off">
  </div>
  <main class="portal" style="max-width:none;padding:1rem 0 0">
{sections}
  </main>
</section>
</div>

<footer class="lfooter">
  <div class="wrap">
    <div><b>◈ SearchaaS</b><br>MongoDB Atlas retrieval platform — REST + MCP, on any cloud.</div>
    <div>Generated from the repository's markdown docs by tools/build_docs_site.py</div>
  </div>
</footer>

</body>
</html>
"""


if __name__ == "__main__":
    build()
