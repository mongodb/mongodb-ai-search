/* Remote MCP vs AI Search — visual walkthrough interactions
   - 6-step clickable stepper (pills + prev/next + progress bar)
   - Step 2: clickable drawback cards → detail drawer
   - Step 4: clickable limitation → matching AI Search answer
   - Step 5: cloud tabs
   - Step 6: clickable "if you hear…" questions → recommendation
   No dependencies. */
(function () {
  "use strict";

  // ── Data ──────────────────────────────────────────────────────────────────
  var DRAWBACKS = {
    retrieval: {
      title: "It is not a retrieval or ranking layer",
      body: "MCP brokers tool calls from an agent to MongoDB. It never defines your search indexes, hybrid ranking, relevance tuning, or the top-k grounding your LLM actually sees.",
      impact: "If the product requirement is \u201cfind the most relevant documents\u201d, an MCP connection on its own gives you no answer quality \u2014 there is nothing to tune."
    },
    policy: {
      title: "The agent owns the query, not your application",
      body: "With MCP the <b>agent</b> decides when and how to call tools. Your application cannot guarantee deterministic filters \u2014 tenant isolation, ACLs, region, product status, time range \u2014 or a ranking contract through an agent\u2019s judgment.",
      impact: "For multi-tenant, regulated, or permission-scoped workloads this is a hard blocker: correctness depends on the model behaving."
    },
    rag: {
      title: "No predictable RAG contract",
      body: "There is no built-in <code>query \u2192 retrieve \u2192 top-k grounded context \u2192 LLM</code> pipeline with latency, recall, and cost characteristics under your control.",
      impact: "Every response varies with whichever tools the agent happened to pick, so you cannot put an SLO on relevance, latency, or spend."
    },
    design: {
      title: "Retrieval design is still entirely on you",
      body: "Even when MCP exposes vector-search-related tools, you still design the indexes, filters, ranking, grounding, and evaluation strategy yourself.",
      impact: "A tool contract is not a retrieval strategy \u2014 exposing $vectorSearch does not tell you how to chunk, embed, filter, fuse, or evaluate."
    },
    atlas: {
      title: "Atlas Managed MCP is Atlas-only",
      body: "The zero-hosting option covers Atlas-hosted deployments. Community Edition, Enterprise Advanced, or private deployments mean running and operating the self-managed MCP server yourself.",
      impact: "Your connectivity story changes per deployment model \u2014 exactly the lock-in most platform teams are trying to avoid."
    }
  };

  var GAP = [
    {
      id: "retrieval",
      limit: "Not a retrieval or ranking layer",
      title: "Database-native retrieval is the product",
      body: "AI Search gives you lexical, semantic, vector, and <b>hybrid</b> retrieval with relevance scores, ranking, and metadata \u2014 executed inside MongoDB. Six strategies ship out of the box, so \u201cwhich data is relevant\u201d has a real, tunable answer.",
      chips: ["vector", "fulltext", "hybrid", "graph", "parent-doc", "metadata"]
    },
    {
      id: "policy",
      limit: "The agent owns the query",
      title: "Your application owns the query policy",
      body: "Queries and aggregation pipelines are <b>application-controlled</b>. You define the query, the ranking, the filters, the grounding policy, and the response experience \u2014 tenant, region, ACL, status and time-range filters are applied <b>before</b> results ever reach the model.",
      chips: ["tenant filters", "ACLs", "pre-filters", "deterministic"]
    },
    {
      id: "rag",
      limit: "No predictable RAG contract",
      title: "A deterministic retrieval contract",
      body: "AI Search gives you exactly one predictable path: <code>user query \u2192 search / vector / hybrid retrieval \u2192 top-k grounded context \u2192 LLM response</code>. Because the pipeline is fixed, latency, recall, throughput, and cost are measurable \u2014 and improvable.",
      chips: ["top-k grounding", "measurable latency", "SLO-able"]
    },
    {
      id: "design",
      limit: "Retrieval design is on you",
      title: "Production search behaviour, designed for you",
      body: "Relevance, latency, filtering, ranking, recall, throughput and cost tuning are first-class concerns \u2014 with query understanding, an LLM planner that picks the strategy, Atlas guardrails, and grounded summarisation with citations. Operational data, metadata and embeddings stay <b>together in MongoDB</b>, so there is no separate vector store to sync.",
      chips: ["query understanding", "planner", "guardrails", "citations"]
    },
    {
      id: "atlas",
      limit: "Atlas Managed MCP is Atlas-only",
      title: "Cloud- and deployment-agnostic by design",
      body: "AI Search runs wherever MongoDB runs \u2014 Atlas, Enterprise Advanced, Community Edition, or self-managed \u2014 and the platform deploys identically on Google Cloud, AWS, and Azure. Retrieval is application-owned query logic, so your search behaviour, indexes and guardrails travel with your data.",
      chips: ["Atlas", "Enterprise Advanced", "Community", "self-managed"]
    }
  ];

  var DECISIONS = [
    { q: "How do I build semantic or hybrid search?", lead: "MongoDB AI Search", cls: "search",
      why: "It is the retrieval and ranking capability \u2014 lexical, semantic, vector and hybrid retrieval with scores you can tune." },
    { q: "How do I build a RAG chatbot?", lead: "MongoDB AI Search", cls: "search",
      why: "RAG needs a retrieval layer that returns grounded top-k context before the LLM call. MCP may optionally help the agent reach live tools alongside it." },
    { q: "How can Cursor / Claude / ChatGPT access my MongoDB?", lead: "Remote MCP Server", cls: "mcp",
      why: "MCP provides the standardized agent connection and tool interface \u2014 no custom API glue per operation." },
    { q: "How can an AI assistant inspect or manage my Atlas environment?", lead: "Remote MCP Server", cls: "mcp",
      why: "MCP exposes database and management tools, subject to the permissions of the access model you choose." },
    { q: "How do I guarantee tenant filters and result ranking?", lead: "MongoDB AI Search", cls: "search",
      why: "The application owns the query and retrieval policy, so filters and ranking are enforced deterministically \u2014 not left to an agent\u2019s judgment." },
    { q: "How can I avoid hosting MCP infrastructure for Atlas?", lead: "Atlas Managed Remote MCP", cls: "mcp",
      why: "MongoDB hosts and manages the MCP server inside Atlas \u2014 centralised auth, governance and upgrades, no infrastructure of your own." },
    { q: "How do I connect to Community Edition, Enterprise Advanced, or a private deployment?", lead: "Self-managed MCP + AI Search", cls: "both",
      why: "Atlas Managed MCP is for Atlas-hosted deployments only; self-managed MCP gives direct deployment control, while AI Search provides retrieval on any of them." }
  ];

  var NEXT_LABELS = [
    "Next: where Remote MCP stops \u2192",
    "Next: meet AI Search \u2192",
    "Next: how it closes the gap \u2192",
    "Next: any cloud, any deployment \u2192",
    "Next: decide in seconds \u2192",
    "Back to the start \u21ba"
  ];

  // ── Stepper ───────────────────────────────────────────────────────────────
  var pills = Array.prototype.slice.call(document.querySelectorAll(".wt-pill"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".wt-panel"));
  var prevBtn = document.getElementById("wt-prev");
  var nextBtn = document.getElementById("wt-next");
  var count = document.getElementById("wt-count");
  var bar = document.getElementById("wt-bar");
  var total = panels.length;
  var cur = 0;

  function go(i, scroll) {
    cur = (i + total) % total;
    pills.forEach(function (p, n) {
      p.setAttribute("aria-selected", n === cur ? "true" : "false");
      if (n <= cur) p.classList.add("seen");
    });
    panels.forEach(function (p, n) { p.classList.toggle("active", n === cur); });
    prevBtn.disabled = cur === 0;
    nextBtn.textContent = NEXT_LABELS[cur];
    count.textContent = "Step " + (cur + 1) + " of " + total;
    bar.style.width = ((cur + 1) / total * 100).toFixed(1) + "%";
    if (scroll !== false) {
      var top = document.querySelector(".wt-main").offsetTop - 70;
      window.scrollTo({ top: top, behavior: "smooth" });
    }
  }

  pills.forEach(function (p, n) { p.addEventListener("click", function () { go(n); }); });
  prevBtn.addEventListener("click", function () { go(cur - 1); });
  nextBtn.addEventListener("click", function () { go(cur + 1); });
  document.querySelectorAll("[data-goto]").forEach(function (b) {
    b.addEventListener("click", function () { go(parseInt(b.getAttribute("data-goto"), 10)); });
  });

  // ── Step 2: drawback cards → drawer ───────────────────────────────────────
  var drawer = document.getElementById("wt-drawer");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".wt-dbcard"));

  cards.forEach(function (c) {
    c.addEventListener("click", function () {
      var d = DRAWBACKS[c.getAttribute("data-db")];
      if (!d) return;
      var open = c.getAttribute("aria-expanded") === "true";
      cards.forEach(function (o) { o.setAttribute("aria-expanded", "false"); });
      if (open) { drawer.classList.remove("open"); return; }
      c.setAttribute("aria-expanded", "true");
      drawer.innerHTML =
        '<div class="wt-drawer-head"><b>\u2715 ' + d.title + '</b>' +
        '<button type="button" class="wt-drawer-x" aria-label="Close">\u2715</button></div>' +
        '<div class="wt-drawer-body">' + d.body +
        '<div class="wt-impact"><span class="lbl">What it means for your application</span>' + d.impact + '</div>' +
        '</div>';
      drawer.classList.add("open");
      drawer.querySelector(".wt-drawer-x").addEventListener("click", function () {
        drawer.classList.remove("open");
        cards.forEach(function (o) { o.setAttribute("aria-expanded", "false"); });
      });
    });
  });

  // ── Step 4: limitation → AI Search answer ─────────────────────────────────
  var mapList = document.getElementById("wt-maplist");
  var answer = document.getElementById("wt-answer");

  if (mapList && answer) {
    GAP.forEach(function (g, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "wt-maprow";
      b.setAttribute("aria-selected", i === 0 ? "true" : "false");
      b.innerHTML = '<span class="ic">\u2715</span><span class="t">' + g.limit + "</span>";
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(mapList.children, function (o) { o.setAttribute("aria-selected", "false"); });
        b.setAttribute("aria-selected", "true");
        renderAnswer(g);
      });
      mapList.appendChild(b);
    });
    renderAnswer(GAP[0]);
  }

  function renderAnswer(g) {
    answer.innerHTML =
      '<p class="from">Remote MCP: ' + g.limit + "</p>" +
      "<h3>\u2713 " + g.title + "</h3>" +
      "<p>" + g.body + "</p>" +
      '<div class="wt-chips">' +
      g.chips.map(function (c) { return '<span class="wt-chip green">' + c + "</span>"; }).join("") +
      "</div>";
  }

  // ── Step 5: cloud tabs ────────────────────────────────────────────────────
  var cloudTabs = Array.prototype.slice.call(document.querySelectorAll(".wt-cloudtabs button"));
  cloudTabs.forEach(function (t) {
    t.addEventListener("click", function () {
      cloudTabs.forEach(function (o) { o.setAttribute("aria-selected", "false"); });
      t.setAttribute("aria-selected", "true");
      document.querySelectorAll(".wt-cloudpanel").forEach(function (p) {
        p.classList.toggle("active", p.id === t.getAttribute("aria-controls"));
      });
    });
  });

  // ── Step 6: decision guide ────────────────────────────────────────────────
  var asks = document.getElementById("wt-asks");
  var reco = document.getElementById("wt-reco");

  if (asks && reco) {
    DECISIONS.forEach(function (d, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "wt-ask";
      b.setAttribute("aria-selected", i === 0 ? "true" : "false");
      b.textContent = "\u201c" + d.q + "\u201d";
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(asks.children, function (o) { o.setAttribute("aria-selected", "false"); });
        b.setAttribute("aria-selected", "true");
        renderReco(d);
      });
      asks.appendChild(b);
    });
    renderReco(DECISIONS[0]);
  }

  function renderReco(d) {
    var badge = d.cls === "search" ? "Retrieval layer" : d.cls === "mcp" ? "Agent access layer" : "Both layers";
    reco.innerHTML =
      '<span class="badge ' + d.cls + '">' + badge + "</span>" +
      '<p class="lead-with">Lead with</p>' +
      '<h3 class="' + d.cls + '">' + d.lead + "</h3>" +
      "<p>" + d.why + "</p>";
  }

  // ── Keyboard: ← / → move between steps ────────────────────────────────────
  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowRight") go(cur + 1);
    if (e.key === "ArrowLeft" && cur > 0) go(cur - 1);
  });

  go(0, false);
})();
