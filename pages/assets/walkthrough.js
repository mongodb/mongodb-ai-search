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
    relevance: {
      title: "It doesn\u2019t know which results are the good ones",
      body: "Remote MCP can run a query and hand back rows. What it can\u2019t do is judge which of those rows actually answers the question \u2014 there\u2019s no ranking, and no way to make tomorrow\u2019s answers better than today\u2019s.",
      impact: "Ask \u201cwhat\u2019s our refund policy?\u201d and you get back whatever the query happened to return first. When users say the answers are wrong, there\u2019s no dial to turn."
    },
    control: {
      title: "The AI decides how to search \u2014 not your application",
      body: "The agent picks which tool to call and what to pass it. Your app can\u2019t force every single search to stay inside the right customer, the right region, or the right permission level \u2014 it can only hope the model remembers.",
      impact: "One customer\u2019s question could surface another customer\u2019s data \u2014 not because of a bug, but because the model left out a filter."
    },
    repeatable: {
      title: "The same question can give different answers",
      body: "Because the agent chooses its own path each time, two identical questions can take two different routes and come back with two different results.",
      impact: "You can\u2019t promise a response time, predict what a question costs, or write a test that keeps passing \u2014 quality drifts with the model."
    },
    lockin: {
      title: "The easy, managed version only works on Atlas",
      body: "Atlas Managed MCP \u2014 the no-setup option MongoDB hosts for you \u2014 only covers databases running in Atlas. On Community Edition, Enterprise Advanced, or your own servers, you install and operate the MCP server yourself.",
      impact: "How you connect changes depending on where your database happens to live."
    }
  };

  var GAP = [
    {
      id: "relevance",
      limit: "Doesn\u2019t know which results are good",
      title: "It ranks the results \u2014 and you can tune the ranking",
      body: "AI Search scores every result and puts the best ones first. It can match on <b>keywords</b>, on <b>meaning</b>, or on <b>both at once</b>. And when answers aren\u2019t good enough, you have real dials to turn \u2014 which method to use, which fields matter, how much each one counts.",
      chips: ["keywords", "meaning", "both combined", "scores you can tune"]
    },
    {
      id: "control",
      limit: "The AI decides how to search",
      title: "Your application sets the rules \u2014 every time",
      body: "Filters like <b>which customer</b>, <b>which region</b>, and <b>who\u2019s allowed to see this</b> are applied by your own code before anything reaches the AI. They\u2019re guarantees, not suggestions the model can skip.",
      chips: ["always-on filters", "one customer never sees another\u2019s data"]
    },
    {
      id: "repeatable",
      limit: "Same question, different answers",
      title: "Same question, same path, every time",
      body: "Every request follows one fixed route: <code>question \u2192 search \u2192 best matches \u2192 answer</code>. Because the path never changes, you can measure it, test it, budget it, and improve it.",
      chips: ["predictable speed", "testable", "repeatable"]
    },
    {
      id: "lockin",
      limit: "Managed version is Atlas-only",
      title: "Works anywhere your data already lives",
      body: "Atlas, Enterprise Advanced, Community Edition, or your own servers \u2014 and it deploys the same way on Google Cloud, AWS and Azure. Your search behaviour travels <b>with your data</b> instead of being tied to one host.",
      chips: ["Atlas", "Enterprise Advanced", "Community", "any cloud"]
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
